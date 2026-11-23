"""Global Dixon-Coles soccer model.

One set of attack/defence ratings for ~1,000 European clubs, fitted jointly across 23 domestic
leagues and the three UEFA competitions so that cross-league matches (UCL/UEL) are priced on a
common scale. Improvements over textbook Dixon-Coles:
  * exponential time decay (recent matches count more)
  * target blends actual goals with xG (or a shots-on-target proxy where xG is missing),
    which strips out finishing luck - the biggest source of noise in goal-based ratings
  * ridge shrinkage so thinly-observed teams are pulled toward average
  * competition-specific home advantage, neutral venues for finals
  * Dixon-Coles low-score correction (rho) for realistic 0-0 / 1-1 / draw pricing
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import minimize, minimize_scalar
from scipy.stats import poisson

MAX_GOALS = 11
UEFA = ("UCL", "UEL", "UECL")


@dataclass
class SoccerParams:
    xi: float = 0.0019          # time decay per day (half-life ~ 1 year)
    goal_weight: float = 0.5    # share of actual goals in the target (rest = xG / SoT proxy)
    ridge: float = 2.0          # L2 penalty on team ratings
    window_days: int = 1100     # ignore matches older than this
    market_weight: float = 0.0  # share of the target taken from past closing-price implied xG
    xi_uefa: float = 0.0015     # slower decay for UEFA matches (they anchor league strengths)
    uefa_boost: float = 1.0     # extra weight on UEFA matches
    two_stage: bool = True      # league strengths from long memory, team form from short memory
    separate_uefa: bool = True  # price UCL/UEL with a single-stage long-memory model (better there)


_GRID = None


def _implied_grid():
    """Lookup table: (lambda_home, lambda_away) -> P(H), P(A), P(Over 2.5) under DC (rho=-0.04)."""
    global _GRID
    if _GRID is None:
        from scipy.spatial import cKDTree
        ls = np.arange(0.15, 4.5, 0.025)
        LH, LA = [x.ravel() for x in np.meshgrid(ls, ls, indexing="ij")]
        g = np.arange(MAX_GOALS)
        ph, pa = poisson.pmf(g[None, :], LH[:, None]), poisson.pmf(g[None, :], LA[:, None])
        M = ph[:, :, None] * pa[:, None, :]
        r = -0.04
        M[:, 0, 0] *= 1 - LH * LA * r
        M[:, 0, 1] *= 1 + LH * r
        M[:, 1, 0] *= 1 + LA * r
        M[:, 1, 1] *= 1 - r
        M /= M.sum(axis=(1, 2), keepdims=True)
        i, j = np.indices((MAX_GOALS, MAX_GOALS))
        H = (M * (i > j)).sum(axis=(1, 2))
        A = (M * (i < j)).sum(axis=(1, 2))
        Ov = (M * ((i + j) > 2.5)).sum(axis=(1, 2))
        X = np.c_[H, A, Ov]
        _GRID = (cKDTree(X), LH, LA)
    return _GRID


def implied_rates(d: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Back out expected goals for each match from its closing 1X2 and over/under 2.5 prices."""
    from . import odds as O
    q = d[["cH", "cD", "cA"]].to_numpy(float)
    t = d[["cO25", "cU25"]].to_numpy(float)
    ok = np.all(np.isfinite(q), 1) & np.all(np.isfinite(t), 1)
    lh = np.full(len(d), np.nan)
    la = np.full(len(d), np.nan)
    if not ok.any():
        return lh, la
    P = 1 / q[ok]
    P /= P.sum(1, keepdims=True)           # proportional de-vig is fine at sharp margins
    T = 1 / t[ok]
    T = T[:, 0] / T.sum(1)
    tree, LH, LA = _implied_grid()
    _, idx = tree.query(np.c_[P[:, 0], P[:, 2], T])
    lh[ok], la[ok] = LH[idx], LA[idx]
    return lh, la


@dataclass
class SoccerModel:
    params: SoccerParams = field(default_factory=SoccerParams)
    teams: list = field(default_factory=list)
    comps: list = field(default_factory=list)
    mu: float = 0.0
    att: np.ndarray | None = None
    dfn: np.ndarray | None = None
    home: dict = field(default_factory=dict)
    rho: float = -0.05
    team_comp: dict = field(default_factory=dict)
    fitted_until: pd.Timestamp | None = None

    # ------------------------------------------------------------------ fitting
    @staticmethod
    def _targets(d: pd.DataFrame, p: "SoccerParams") -> tuple[np.ndarray, np.ndarray]:
        hg, ag = d["hg"].to_numpy(float), d["ag"].to_numpy(float)
        # shot-based xG proxy: goals ~ a*shots_on_target + b*shots_off_target (fitted, no intercept)
        sh = np.concatenate([d["hst"], d["ast"]]).astype(float)
        so = np.concatenate([d["hs"] - d["hst"], d["as_"] - d["ast"]]).astype(float)
        gg = np.concatenate([hg, ag])
        ok = np.isfinite(sh) & np.isfinite(so)
        coef = np.linalg.lstsq(np.c_[sh[ok], so[ok]], gg[ok], rcond=None)[0] if ok.sum() > 100 else [0.3, 0.03]
        hx, ax = d["hxg"].to_numpy(float), d["axg"].to_numpy(float)
        hx = np.where(np.isnan(hx), coef[0] * d["hst"] + coef[1] * (d["hs"] - d["hst"]), hx)
        ax = np.where(np.isnan(ax), coef[0] * d["ast"] + coef[1] * (d["as_"] - d["ast"]), ax)
        hx = np.where(np.isnan(hx), hg, hx)
        ax = np.where(np.isnan(ax), ag, ax)
        w = p.goal_weight
        yh, ya = w * hg + (1 - w) * hx, w * ag + (1 - w) * ax
        # market-implied expected goals from past closing prices: the information sportsbooks
        # actually rate teams on (injuries, lineups, sharp money are all baked in)
        if p.market_weight > 0:
            mh, ma = implied_rates(d)
            okm = np.isfinite(mh)
            yh = np.where(okm, p.market_weight * mh + (1 - p.market_weight) * yh, yh)
            ya = np.where(okm, p.market_weight * ma + (1 - p.market_weight) * ya, ya)
        return yh, ya

    def fit(self, df: pd.DataFrame, as_of: pd.Timestamp | None = None) -> "SoccerModel":
        p = self.params
        as_of = as_of or (df["date"].max() + pd.Timedelta(days=1))
        d = df[(df["date"] < as_of) & df["hg"].notna() & df["ag"].notna()]
        d = d[d["date"] >= as_of - pd.Timedelta(days=p.window_days)].copy()
        self.fitted_until = as_of
        self.teams = sorted(set(d["home"]) | set(d["away"]))
        # one home-advantage term per domestic league, one shared by all UEFA competitions
        hcomp = d["comp"].where(~d["comp"].isin(UEFA), "UEFA")
        self.comps = sorted(hcomp.unique())
        ti = {t: i for i, t in enumerate(self.teams)}
        ci = {c: i for i, c in enumerate(self.comps)}
        h = d["home"].map(ti).to_numpy()
        a = d["away"].map(ti).to_numpy()
        c = hcomp.map(ci).to_numpy()
        nh = (~d["neutral"].astype(bool)).to_numpy(float)
        yh, ya = self._targets(d, p)
        age = (as_of - d["date"]).dt.days.to_numpy(float)
        # UEFA games are the only bridge between leagues; league strength moves slowly, so they
        # keep a longer memory than domestic games
        is_uefa = d["comp"].isin(UEFA).to_numpy()
        w_slow = np.where(is_uefa, p.uefa_boost * np.exp(-p.xi_uefa * age), np.exp(-p.xi * age))
        w_fast = np.exp(-p.xi * age)
        n, nc = len(self.teams), len(self.comps)

        # hierarchical prior: team = league strength + shrunk team deviation. Without this, a
        # side that dominates a weak league looks elite, because the ridge pulls it toward the
        # global mean instead of its own league's level.
        dom = d[~d["comp"].isin(UEFA)].sort_values("date")
        self.team_comp = pd.concat([dom[["home", "comp"]].rename(columns={"home": "t"}),
                                    dom[["away", "comp"]].rename(columns={"away": "t"})]
                                   ).groupby("t")["comp"].last().to_dict()
        self.leagues = sorted(set(self.team_comp.values())) + ["UEFA-only"]
        li = {l: i for i, l in enumerate(self.leagues)}
        lg = np.array([li[self.team_comp.get(t, "UEFA-only")] for t in self.teams])
        nl = len(self.leagues)

        def unpack(x):
            o = 0
            mu = x[o]; o += 1
            ra = x[o:o + n]; o += n
            rd = x[o:o + n]; o += n
            la_ = x[o:o + nl]; o += nl
            ld = x[o:o + nl]; o += nl
            return mu, ra, rd, la_, ld, x[o:]

        def solve(w, lam, lat0, ldf0, x0):
            def f(x):
                mu, ra, rd, lat, ldf, hm = unpack(x)
                att, dfn = lat[lg] + ra, ldf[lg] + rd
                eh = mu + hm[c] * nh + att[h] - dfn[a]
                ea = mu + att[a] - dfn[h]
                lh, la = np.exp(eh), np.exp(ea)
                nll = -np.sum(w * (yh * eh - lh + ya * ea - la))
                dl, dd = lat - lat0, ldf - ldf0
                nll += p.ridge * (ra @ ra + rd @ rd) + lam * (dl @ dl + dd @ dd)
                gh = -w * (yh - lh)
                ga = -w * (ya - la)
                g_att = np.bincount(h, gh, n) + np.bincount(a, ga, n)
                g_def = -np.bincount(a, gh, n) - np.bincount(h, ga, n)
                g = np.concatenate([[gh.sum() + ga.sum()],
                                    g_att + 2 * p.ridge * ra, g_def + 2 * p.ridge * rd,
                                    np.bincount(lg, g_att, nl) + 2 * lam * dl,
                                    np.bincount(lg, g_def, nl) + 2 * lam * dd,
                                    np.bincount(c, gh * nh, nc)])
                return nll, g
            return minimize(f, x0, jac=True, method="L-BFGS-B", options={"maxiter": 5000}).x

        x0 = np.zeros(1 + 2 * n + 2 * nl + nc)
        x0[0] = np.log(max(np.average((yh + ya) / 2, weights=w_fast), 0.1))
        x0[-nc:] = 0.25
        z = np.zeros(nl)
        # stage 1: long UEFA memory -> league strengths
        x = solve(w_slow, 0.01, z, z, x0)
        _, _, _, lat1, ldf1, _ = unpack(x)
        # stage 2: current team form with league strengths held at stage-1 values
        if p.two_stage and (p.xi_uefa != p.xi or p.uefa_boost != 1.0):
            x = solve(w_fast, 1e3, lat1.copy(), ldf1.copy(), x)
        else:
            w_fast = w_slow
        w = w_fast
        self.mu, ra, rd, lat, ldf, hm = unpack(x)
        self.att, self.dfn = lat[lg] + ra, ldf[lg] + rd
        self.league_strength = dict(zip(self.leagues, np.exp(self.mu + lat) - np.exp(self.mu - ldf)))
        self.home = {cc: hm[i] for cc, i in ci.items()}

        # Dixon-Coles rho on actual (integer) scores given fitted rates
        lh = np.exp(self.mu + hm[c] * nh + self.att[h] - self.dfn[a])
        la = np.exp(self.mu + self.att[a] - self.dfn[h])
        hg, ag = d["hg"].to_numpy(int), d["ag"].to_numpy(int)

        def nll_rho(r):
            t = np.ones_like(lh)
            t = np.where((hg == 0) & (ag == 0), 1 - lh * la * r, t)
            t = np.where((hg == 0) & (ag == 1), 1 + lh * r, t)
            t = np.where((hg == 1) & (ag == 0), 1 + la * r, t)
            t = np.where((hg == 1) & (ag == 1), 1 - r, t)
            return -np.sum(w * np.log(np.clip(t, 1e-9, None)))

        self.rho = minimize_scalar(nll_rho, bounds=(-0.25, 0.25), method="bounded").x
        self.uefa_model = None
        if p.separate_uefa and p.two_stage:
            from dataclasses import replace
            self.uefa_model = SoccerModel(replace(p, two_stage=False, separate_uefa=False)).fit(df, as_of)
        return self

    # ------------------------------------------------------------------ predicting
    def rates(self, home: str, away: str, comp: str | None = None, neutral: bool = False) -> tuple[float, float]:
        ti = {t: i for i, t in enumerate(self.teams)}
        hi, ai = ti[home], ti[away]
        comp = comp or self.team_comp.get(home, "EPL")
        comp = "UEFA" if comp in UEFA else comp
        hadv = 0.0 if neutral else self.home.get(comp, np.mean(list(self.home.values())))
        lh = np.exp(self.mu + hadv + self.att[hi] - self.dfn[ai])
        la = np.exp(self.mu + self.att[ai] - self.dfn[hi])
        return float(lh), float(la)

    def score_matrix(self, lh: float, la: float) -> np.ndarray:
        g = np.arange(MAX_GOALS)
        m = np.outer(poisson.pmf(g, lh), poisson.pmf(g, la))
        r = self.rho
        m[0, 0] *= 1 - lh * la * r
        m[0, 1] *= 1 + lh * r
        m[1, 0] *= 1 + la * r
        m[1, 1] *= 1 - r
        m = np.clip(m, 0, None)
        return m / m.sum()

    def predict(self, home, away, comp=None, neutral=False, mult=(1.0, 1.0)) -> "Markets":
        """mult: (home xG multiplier, away xG multiplier) from injuries / lineups."""
        if comp in UEFA and getattr(self, "uefa_model", None) is not None:
            return self.uefa_model.predict(home, away, comp, neutral, mult)
        lh, la = self.rates(home, away, comp, neutral)
        lh, la = lh * mult[0], la * mult[1]
        return Markets(self.score_matrix(lh, la), lh, la)

    def ratings(self, comp: str | None = None) -> pd.DataFrame:
        r = pd.DataFrame({"team": self.teams, "attack": self.att, "defence": self.dfn})
        r["league"] = r["team"].map(self.team_comp).fillna("UEFA-only")
        # expected goal difference per match vs an average team on neutral ground
        r["rating"] = np.exp(self.mu + r["attack"]) - np.exp(self.mu - r["defence"])
        if comp:
            r = r[r["league"] == comp]
        return r.sort_values("rating", ascending=False).reset_index(drop=True)


class Markets:
    """All standard soccer markets derived from one correct-score matrix."""

    def __init__(self, m: np.ndarray, lh: float, la: float):
        self.m, self.lh, self.la = m, lh, la
        i, j = np.indices(m.shape)
        self.diff = i - j
        self.tot = i + j

    def reweight(self, x12_target) -> "Markets":
        """Rescale the score grid so its 1X2 matches a target (e.g. model+market blend), keeping
        every other market consistent with that 1X2."""
        cur = self.x12()
        t = np.asarray(x12_target, float)
        f = np.where(self.diff > 0, t[0] / cur[0], np.where(self.diff == 0, t[1] / cur[1], t[2] / cur[2]))
        m = self.m * f
        return Markets(m / m.sum(), self.lh, self.la)

    def x12(self) -> np.ndarray:
        m = self.m
        return np.array([np.tril(m, -1).sum(), np.trace(m), np.triu(m, 1).sum()])

    def btts(self) -> float:
        return float(self.m[1:, 1:].sum())

    def _asian(self, values: np.ndarray, line: float) -> tuple[float, float, float]:
        """P(win), P(push), P(lose) for 'values + line > 0' including quarter lines (split stakes)."""
        frac = abs(line * 4) % 2
        lines = [line - 0.25, line + 0.25] if frac == 1 else [line]
        win = push = lose = 0.0
        for ln in lines:
            s = values + ln
            win += self.m[s > 1e-9].sum() / len(lines)
            push += self.m[np.abs(s) <= 1e-9].sum() / len(lines)
            lose += self.m[s < -1e-9].sum() / len(lines)
        return win, push, lose

    def total(self, line: float) -> tuple[float, float, float]:
        """over: (win, push, lose)."""
        return self._asian(self.tot.astype(float), -line)

    def handicap(self, line: float) -> tuple[float, float, float]:
        """home with handicap `line` (e.g. -0.5, -1.25): (win, push, lose)."""
        return self._asian(self.diff.astype(float), line)

    def team_total(self, side: str, line: float) -> float:
        g = np.arange(self.m.shape[0])
        marg = self.m.sum(axis=1) if side == "home" else self.m.sum(axis=0)
        return float(marg[g > line].sum())

    def top_scores(self, k: int = 8):
        idx = np.dstack(np.unravel_index(np.argsort(-self.m, axis=None), self.m.shape))[0][:k]
        return [(int(a), int(b), float(self.m[a, b])) for a, b in idx]

    @staticmethod
    def fair_asian(win, push, lose) -> float:
        """Decimal odds where EV = 0 given win/push/lose probabilities."""
        return 1 + lose / win if win > 0 else np.inf

    def fair_ah_line(self) -> float:
        """Asian handicap line (home) closest to a 50/50 price - what a book would hang."""
        best, bl = 9, 0.0
        for ln in np.arange(-4, 4.01, 0.25):
            w, p, l = self.handicap(ln)
            if abs(w - l) < best:
                best, bl = abs(w - l), ln
        return float(bl)

    def fair_total_line(self) -> float:
        best, bl = 9, 2.5
        for ln in np.arange(0.5, 6.01, 0.25):
            w, p, l = self.total(ln)
            if abs(w - l) < best:
                best, bl = abs(w - l), ln
        return float(bl)
