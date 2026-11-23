"""NFL model: offence/defence power ratings + key-number-aware score distribution.

Ratings are a weighted ridge regression over team-game rows. Each row's target blends three
views of how many points a team "deserved":
  * actual points scored (noisy)
  * an EPA-based points proxy (strips out fumble luck, red-zone variance, garbage time)
  * the market-implied team total from that game's closing spread/total (what books rate on)
with exponential time decay and an extra discount per season back, plus covariates for home
field, rest and a non-regular starting QB. The predicted margin/total are turned into full
probability distributions with empirical key-number weights (3, 7, 10, 6, 14 ...), so spreads,
moneylines, pushes, alt lines and totals are priced the way a US book prices them.
"""
from __future__ import annotations

import io
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "nfl"
GAMES_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"
STATS_URL = "https://github.com/nflverse/nflverse-data/releases/download/stats_team/stats_team_week_{y}.csv"


def _get(url: str, dest: Path, max_age_h: float) -> bytes:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and (time.time() - dest.stat().st_mtime) < max_age_h * 3600:
        return dest.read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (betmodel)"})
    try:
        raw = urllib.request.urlopen(req, timeout=90).read()
    except Exception as e:
        if dest.exists():
            print(f"  ! {url} failed ({e}); using cached copy")
            return dest.read_bytes()
        raise
    dest.write_bytes(raw)
    return raw


def load_games(first_season: int = 2012, max_age_h: float = 3) -> pd.DataFrame:
    g = pd.read_csv(io.BytesIO(_get(GAMES_URL, DATA_DIR / "games.csv", max_age_h)), low_memory=False)
    g = g[g["season"] >= first_season].copy()
    g["date"] = pd.to_datetime(g["gameday"])
    g["neutral"] = g["location"].eq("Neutral")
    cur = int(g["season"].max())
    frames = []
    for y in range(max(first_season, 2016), cur + 1):
        try:
            raw = _get(STATS_URL.format(y=y), DATA_DIR / f"stats_team_week_{y}.csv", max_age_h if y == cur else 24 * 365)
            frames.append(pd.read_csv(io.BytesIO(raw), usecols=["game_id", "team", "passing_epa", "rushing_epa"]))
        except Exception as e:
            print(f"  ! no team stats for {y}: {e}")
    if frames:
        s = pd.concat(frames)
        s["epa"] = s["passing_epa"].fillna(0) + s["rushing_epa"].fillna(0)
        e = s.set_index(["game_id", "team"])["epa"]
        g["home_epa"] = [e.get((gid, t), np.nan) for gid, t in zip(g["game_id"], g["home_team"])]
        g["away_epa"] = [e.get((gid, t), np.nan) for gid, t in zip(g["game_id"], g["away_team"])]
    else:
        g["home_epa"] = g["away_epa"] = np.nan
    g = _qb_flags(g)
    try:
        from .nfl_injuries import attach
        g = attach(g, first_season)
    except Exception as e:  # injuries are an enhancement; never block the model
        print(f"  ! injury data unavailable ({e}); continuing without it")
    return g.sort_values(["date", "game_id"]).reset_index(drop=True)


def _qb_flags(g: pd.DataFrame) -> pd.DataFrame:
    """1 if the listed starter is not the team's regular QB (< 4 of its previous 8 starts)."""
    rows = pd.concat([
        pd.DataFrame({"i": g.index, "date": g["date"], "team": g["home_team"], "qb": g["home_qb_id"], "side": "home"}),
        pd.DataFrame({"i": g.index, "date": g["date"], "team": g["away_team"], "qb": g["away_qb_id"], "side": "away"}),
    ]).sort_values(["team", "date"])
    flag = {}
    for team, r in rows.groupby("team"):
        hist: list = []
        for i, side, qb in zip(r["i"], r["side"], r["qb"]):
            prev = hist[-8:]
            flag[(i, side)] = 0 if (pd.isna(qb) or len(prev) < 4 or prev.count(qb) >= 4) else 1
            if pd.notna(qb):
                hist.append(qb)
    g["home_backup"] = [flag.get((i, "home"), 0) for i in g.index]
    g["away_backup"] = [flag.get((i, "away"), 0) for i in g.index]
    return g


WIND_REF = 7.0  # mph; typical open-air game. Wind effects are measured relative to this.


def wind_x(roof, wind) -> float:
    """Wind above/below a normal day, open-air games only (0 for domes, closed roofs, or unknown wind)."""
    if not (isinstance(roof, str) and roof in ("outdoors", "open")) or wind is None or pd.isna(wind):
        return 0.0
    return float(min(max(wind, 0.0), 30.0)) - WIND_REF


def is_dome(roof) -> float:
    return 1.0 if isinstance(roof, str) and roof in ("dome", "closed") else 0.0


@dataclass
class NFLParams:
    xi: float = 0.004            # decay per day
    season_decay: float = 0.6    # extra weight multiplier per season back (offseason churn)
    w_result: float = 0.3        # target weights (normalised): actual points
    w_epa: float = 0.3           # EPA-based points proxy
    w_market: float = 0.4        # closing-line implied team total
    ridge: float = 3.0
    ridge_qb: float = 1.0
    ridge_inj: float = 1.0
    use_injuries: bool = True
    use_weather: bool = True
    window_seasons: int = 4


@dataclass
class NFLModel:
    params: NFLParams = field(default_factory=NFLParams)
    teams: list = field(default_factory=list)
    off: np.ndarray | None = None
    dfn: np.ndarray | None = None
    mu: float = 22.0
    hfa: float = 1.5
    beta: dict = field(default_factory=dict)
    sd_margin: float = 13.0
    sd_total: float = 13.0
    key_margin: dict = field(default_factory=dict)
    key_total: dict = field(default_factory=dict)

    def fit(self, g: pd.DataFrame, as_of: pd.Timestamp | None = None) -> "NFLModel":
        p = self.params
        as_of = as_of or g["date"].max() + pd.Timedelta(days=1)
        d = g[(g["date"] < as_of) & g["result"].notna()]
        cur_season = int(g.loc[g["date"] >= as_of, "season"].min()) if (g["date"] >= as_of).any() else int(d["season"].max())
        d = d[d["season"] > cur_season - p.window_seasons].copy()
        self.teams = sorted(set(d["home_team"]) | set(d["away_team"]))
        ti = {t: i for i, t in enumerate(self.teams)}
        n = len(self.teams)

        # two rows per game: home offence vs away defence, away offence vs home defence
        hs, as_ = d["home_score"].to_numpy(float), d["away_score"].to_numpy(float)
        avg = np.nanmean(np.r_[hs, as_])
        epa_scale = 1.0
        he = avg + epa_scale * d["home_epa"].to_numpy(float)
        ae = avg + epa_scale * d["away_epa"].to_numpy(float)
        tl, sl = d["total_line"].to_numpy(float), d["spread_line"].to_numpy(float)
        hm, am = (tl + sl) / 2, (tl - sl) / 2
        y = np.r_[hs, as_]
        comps = [(p.w_result, y), (p.w_epa, np.r_[he, ae]), (p.w_market, np.r_[hm, am])]
        num = np.zeros_like(y)
        den = np.zeros_like(y)
        for wt, v in comps:
            ok = np.isfinite(v)
            num[ok] += wt * v[ok]
            den[ok] += wt
        target = num / den

        m = len(d)
        # quarterbacks get their own (shrunk) offensive value that travels with them, so a
        # starter's injury, a benching or a trade moves the number the way the market does
        qbs = sorted(set(d["home_qb_id"].dropna()) | set(d["away_qb_id"].dropna()))
        qi = {q: i for i, q in enumerate(qbs)}
        nq = len(qbs)
        k = 2 * n + nq  # first covariate column
        from .nfl_injuries import OFF_GROUPS, DEF_GROUPS
        inj_cols = [f"inj_{x}" for x in list(OFF_GROUPS) + list(DEF_GROUPS)]
        use_inj = p.use_injuries and all(f"home_{c}" in d for c in inj_cols)
        ni = len(inj_cols) if use_inj else 0
        nw = 2 if p.use_weather and "roof" in d else 0
        X = np.zeros((2 * m, k + 4 + ni + nw))
        hi, ai = d["home_team"].map(ti).to_numpy(), d["away_team"].map(ti).to_numpy()
        r = np.arange(m)
        X[r, hi] = 1; X[r, n + ai] = -1
        X[m + r, ai] = 1; X[m + r, n + hi] = -1
        hq = d["home_qb_id"].map(qi).to_numpy()
        aq = d["away_qb_id"].map(qi).to_numpy()
        okh, oka = ~pd.isna(hq), ~pd.isna(aq)
        X[r[okh], 2 * n + hq[okh].astype(int)] = 1
        X[m + r[oka], 2 * n + aq[oka].astype(int)] = 1
        nh = (~d["neutral"]).to_numpy(float)
        X[r, k] = nh / 2; X[m + r, k] = -nh / 2                           # home field (split)
        rest = (d["home_rest"] - d["away_rest"]).clip(-7, 7).fillna(0).to_numpy(float)
        X[r, k + 1] = rest / 2; X[m + r, k + 1] = -rest / 2                # rest edge
        X[r, k + 2] = d["home_backup"]; X[m + r, k + 2] = d["away_backup"]  # unfamiliar starter
        X[:, k + 3] = 1                                                    # league mean
        # injuries: a row's scoring is hit by its own offensive absences and helped by the
        # opponent's defensive absences
        for j, c in enumerate(inj_cols[:ni]):
            own_off = c.split("_")[1] in OFF_GROUPS
            hsrc, asrc = ("home", "away") if own_off else ("away", "home")
            X[r, k + 4 + j] = d[f"{hsrc}_{c}"].to_numpy(float)
            X[m + r, k + 4 + j] = d[f"{asrc}_{c}"].to_numpy(float)
        # weather: wind (open-air games) and domes change both teams' scoring
        kw = k + 4 + ni
        if nw:
            wx = np.array([wind_x(rf, wd) for rf, wd in zip(d["roof"], d["wind"])])
            dm = np.array([is_dome(rf) for rf in d["roof"]])
            X[:, kw] = np.r_[wx, wx]
            X[:, kw + 1] = np.r_[dm, dm]
        age = (as_of - d["date"]).dt.days.to_numpy(float)
        sb = cur_season - d["season"].to_numpy()
        w = np.exp(-p.xi * age) * p.season_decay ** sb
        W = np.r_[w, w]
        pen = np.r_[np.full(2 * n, p.ridge), np.full(nq, p.ridge_qb), 1e-6, 1e-6, 1e-6, 1e-8, np.full(ni, p.ridge_inj),
                    np.full(nw, 1.0)]
        A = X.T @ (X * W[:, None]) + np.diag(pen)
        b = X.T @ (W * target)
        beta = np.linalg.solve(A, b)
        self.off, self.dfn = beta[:n], beta[n:2 * n]
        self.qb = dict(zip(qbs, beta[2 * n:k]))
        self.hfa, self.beta = beta[k], {"rest": beta[k + 1], "backup_qb": beta[k + 2]}
        self.mu = beta[k + 3]
        self.beta_inj = dict(zip(inj_cols[:ni], beta[k + 4:k + 4 + ni]))
        self.beta["wind"], self.beta["dome"] = (float(beta[kw]), float(beta[kw + 1])) if nw else (0.0, 0.0)
        # each team's most recent starter, and QB names for lookups
        last = pd.concat([d[["date", "home_team", "home_qb_id", "home_qb_name"]].set_axis(["date", "t", "id", "nm"], axis=1),
                          d[["date", "away_team", "away_qb_id", "away_qb_name"]].set_axis(["date", "t", "id", "nm"], axis=1)])
        last = last.dropna(subset=["id"]).sort_values("date")
        self.team_qb = last.groupby("t")["id"].last().to_dict()
        self.qb_names = last.groupby("id")["nm"].last().to_dict()
        self._fit_distributions(g[(g["date"] < as_of) & g["result"].notna()])
        return self

    def _fit_distributions(self, h: pd.DataFrame):
        """Spread of outcomes around the closing number, and key-number weights."""
        h = h.dropna(subset=["spread_line", "total_line"])
        res = h["result"] - h["spread_line"]
        self.sd_margin = float(np.sqrt(np.mean(res ** 2)))
        tres = h["total"] - h["total_line"]
        self.sd_total = float(np.sqrt(np.mean(tres ** 2)))
        self.key_margin = self._key_weights(h["result"].to_numpy(), h["spread_line"].to_numpy(), self.sd_margin, 60)
        self.key_total = self._key_weights(h["total"].to_numpy(), h["total_line"].to_numpy(), self.sd_total, 110, lo=0)
        # spread -> moneyline the way books map it: historical win rate by closing spread
        # (logistic, ties excluded). A normal curve overstates big underdogs.
        from scipy.optimize import minimize_scalar
        hh = h[h["result"] != 0]
        s, y = hh["spread_line"].to_numpy(float), (hh["result"] > 0).to_numpy(float)

        def nll(k):
            p = np.clip(1 / (1 + np.exp(-k * s)), 1e-9, 1 - 1e-9)
            return -np.sum(y * np.log(p) + (1 - y) * np.log(1 - p))
        self.ml_k = float(minimize_scalar(nll, bounds=(0.05, 0.3), method="bounded").x)

    def win_prob(self, mu: float) -> float:
        """P(home wins | expected home margin mu), ties excluded."""
        return float(1 / (1 + np.exp(-self.ml_k * mu)))

    @staticmethod
    def _key_weights(outcome, line, sd, hi, lo=None):
        lo = -hi if lo is None else lo
        ks = np.arange(lo, hi + 1)
        emp = pd.Series(outcome).value_counts().reindex(ks, fill_value=0).to_numpy(float)
        exp_ = norm.pdf((ks[None, :] - line[:, None]) / sd).sum(0) / sd
        emp = emp + 0.5
        exp_ = exp_ * emp.sum() / exp_.sum() + 0.5
        wts = emp / exp_
        wts = np.where(np.abs(ks - np.median(line)) > 2.5 * sd, 1.0, wts)  # thin tails -> neutral
        return dict(zip(ks.tolist(), wts.tolist()))

    # ---------------------------------------------------------------- prediction
    def expected(self, home: str, away: str, neutral=False, rest_diff=0.0, home_backup=0, away_backup=0,
                 adj_home=0.0, adj_away=0.0, home_qb=None, away_qb=None, inj_home=None, inj_away=None,
                 roof=None, wind=None):
        """home_qb / away_qb: gsis id of the starter (default: team's most recent starter).
        inj_home / inj_away: {'inj_WR': 0.9, ...} injury indices for this game.
        roof / wind: stadium roof ('outdoors', 'dome', ...) and kickoff wind in mph."""
        ih, ia = self.injury_points(inj_home, inj_away)
        wx = self.weather_points(roof, wind)
        adj_home, adj_away = adj_home + ih + wx, adj_away + ia + wx
        ti = {t: i for i, t in enumerate(self.teams)}
        h, a = ti[home], ti[away]
        hf = 0 if neutral else self.hfa
        rd = float(np.clip(0 if pd.isna(rest_diff) else rest_diff, -7, 7)) * self.beta["rest"]
        hq = self.qb.get(home_qb if isinstance(home_qb, str) else self.team_qb.get(home), 0.0)
        aq = self.qb.get(away_qb if isinstance(away_qb, str) else self.team_qb.get(away), 0.0)
        hp = self.mu + self.off[h] - self.dfn[a] + hq + hf / 2 + rd / 2 + self.beta["backup_qb"] * home_backup + adj_home
        ap = self.mu + self.off[a] - self.dfn[h] + aq - hf / 2 - rd / 2 + self.beta["backup_qb"] * away_backup + adj_away
        return hp - ap, hp + ap  # (home margin, total)

    def weather_points(self, roof=None, wind=None) -> float:
        """Points added to EACH team's score by the conditions (negative in high wind)."""
        return self.beta.get("wind", 0.0) * wind_x(roof, wind) + self.beta.get("dome", 0.0) * is_dome(roof)

    def injury_points(self, inj_home=None, inj_away=None) -> tuple[float, float]:
        """Points added to (home score, away score) from both teams' injury indices."""
        from .nfl_injuries import OFF_GROUPS
        bi = getattr(self, "beta_inj", {}) or {}
        inj_home, inj_away = inj_home or {}, inj_away or {}
        hp = ap = 0.0
        for c, b in bi.items():
            if c.split("_")[1] in OFF_GROUPS:   # own offence missing players
                hp += b * inj_home.get(c, 0.0)
                ap += b * inj_away.get(c, 0.0)
            else:                                # opponent's defence missing players
                hp += b * inj_away.get(c, 0.0)
                ap += b * inj_home.get(c, 0.0)
        return hp, ap

    @staticmethod
    def game_injuries(row, side: str) -> dict:
        from .nfl_injuries import GROUPS
        return {f"inj_{g}": float(getattr(row, f"{side}_inj_{g}", 0.0) or 0.0) for g in GROUPS}

    def find_qb(self, name: str) -> str:
        import difflib
        inv = {v.lower(): k for k, v in self.qb_names.items() if isinstance(v, str)}
        c = difflib.get_close_matches(name.lower(), list(inv), n=1, cutoff=0.6)
        if not c:
            raise SystemExit(f"Unknown QB '{name}'")
        return inv[c[0]]

    @staticmethod
    def _centered(keys: dict, sd: float, line: float) -> dict:
        """Discrete outcome distribution with key-number weights, located so that `line` is a
        balanced number (P(above) == P(below), pushes aside) - that is what a sportsbook line is.
        Without re-centring, the heavy mass on 3/7 drags the distribution and misprices spreads."""
        ks = np.array(list(keys), float)
        wt = np.array(list(keys.values()))

        def pmf(loc):
            pr = norm.pdf((ks - loc) / sd) * wt
            return pr / pr.sum()

        # continuity-corrected median: each integer's mass spread over [k-0.5, k+0.5]. At an integer
        # line this balances the two sides around the push; at a half line it is the plain median;
        # in between it moves smoothly, so 3.2 and 3.8 are priced differently.
        def cdf_cont(loc):
            p = pmf(loc)
            return float((p * np.clip(line - (ks - 0.5), 0, 1)).sum())

        lo, hi = line - 4.0, line + 4.0
        for _ in range(40):
            mid = (lo + hi) / 2
            if cdf_cont(mid) > 0.5:
                lo = mid
            else:
                hi = mid
        return dict(zip(ks.astype(int).tolist(), pmf((lo + hi) / 2).tolist()))

    def _invert(self, dist_fn, line_arg, centre, p_target):
        """Find the centre whose distribution gives win/(win+lose) == p_target at a fixed line."""
        lo, hi = centre - 10, centre + 10
        for _ in range(40):
            mid = (lo + hi) / 2
            w, _, l = self.line_probs(dist_fn(mid), line_arg)
            if (w / (w + l) if w + l > 0 else 0.5) < p_target:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2

    def mu_from_market(self, home_line: float, p_home_cover: float) -> float:
        """Expected home margin the market is pricing, from a home spread (e.g. -3 at -119) and the
        de-vigged probability of that side. Juice matters: -3 at -119 is really ~-3.4."""
        return self._invert(self.margin_dist, home_line, -home_line, p_home_cover)

    def tau_from_market(self, total_line: float, p_over: float) -> float:
        return self._invert(self.total_dist, -total_line, total_line, p_over)

    def margin_dist(self, mu: float) -> dict:
        return self._centered(self.key_margin, self.sd_margin, mu)

    def total_dist(self, tau: float) -> dict:
        return self._centered(self.key_total, self.sd_total, tau)

    @staticmethod
    def line_probs(dist: dict, line: float, side_sign: int = 1) -> tuple[float, float, float]:
        """For 'outcome*side_sign + line > 0': (win, push, lose). Home spread -3 -> margin - 3 > 0."""
        w = p = l = 0.0
        for k, pr in dist.items():
            s = side_sign * k + line
            if s > 1e-9:
                w += pr
            elif s < -1e-9:
                l += pr
            else:
                p += pr
        return w, p, l

    def ratings(self) -> pd.DataFrame:
        r = pd.DataFrame({"team": self.teams, "off": self.off, "def": self.dfn})
        r["QB"] = [self.qb_names.get(self.team_qb.get(t), "?") for t in self.teams]
        r["qb_value"] = [self.qb.get(self.team_qb.get(t), 0.0) for t in self.teams]
        # points better than an average team per game on a neutral field, with current starter
        r["net"] = r["off"] + r["def"] + r["qb_value"]
        return r.sort_values("net", ascending=False).reset_index(drop=True)


def market_to_rating_spread(spread_home: float) -> float:
    """nflverse spread_line is the expected home margin; sportsbook home spread = -spread_line."""
    return -spread_home
