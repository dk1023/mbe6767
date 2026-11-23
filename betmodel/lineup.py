"""Player ratings and lineup strength.

Team ratings (soccer.py) describe a club's *usual* side. This module measures how far a given
starting XI is from that usual side, using three player-level signals built only from matches played
before the game being priced:

  box   - the player's own xG + xA per 90, shrunk toward his position's average when he has few
          minutes (attacking output)
  o, d  - on-pitch impact (ridge "plus-minus" on xG): how much xG his team creates (o) and prevents (d)
          per 90 while he is on the field, controlling for teammates and opponents
  reg   - his share of the team's minutes over its last 10 matches (are the regulars playing?)

A lineup's value is the sum over its XI; the deviation from the team's typical XI (average of its
last 10 lineups, valued with the same ratings) is what adjusts the expected goals. How much each
deviation is worth is fitted on past matches (see backtest in fit_effects).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.sparse.linalg import lsqr

POS = ["GK", "DEF", "MID", "FWD"]
EFFECTS_PATH = Path(__file__).resolve().parent / "lineup_effects.json"
FEATS = ["box", "o", "d", "reg", "reg_def", "reg_att"]


@dataclass
class PlayerRatings:
    as_of: pd.Timestamp
    box: dict = field(default_factory=dict)     # player_id -> xG+xA per 90 (shrunk)
    o: dict = field(default_factory=dict)       # on-pitch xG created per 90 (vs average)
    d: dict = field(default_factory=dict)       # on-pitch xG prevented per 90 (vs average)
    pos: dict = field(default_factory=dict)
    names: dict = field(default_factory=dict)
    team_of: dict = field(default_factory=dict)
    pos_box: dict = field(default_factory=dict)


def _stints(apps: pd.DataFrame, shots: pd.DataFrame, home_id: int, away_id: int):
    """Split a match into periods with no substitutions: (minutes, home XI ids, away XI ids, xg_h, xg_a)."""
    on = {}
    for r in apps.itertuples():
        start = 0.0 if r.started else float(r.sub_in if pd.notna(r.sub_in) else 90)
        end = float(r.sub_out) if pd.notna(r.sub_out) else 90.0
        if end > start:
            on[r.player_id] = (start, end, r.team_id)
    cuts = sorted({0.0, 90.0} | {t for v in on.values() for t in v[:2] if 0 < t < 90})
    out = []
    sh = shots[["team_id", "minute", "xg", "own_goal"]].to_numpy() if len(shots) else np.zeros((0, 4))
    for a, b in zip(cuts[:-1], cuts[1:]):
        if b - a < 1:
            continue
        hp = [p for p, (s, e, t) in on.items() if t == home_id and s <= a and e >= b]
        ap = [p for p, (s, e, t) in on.items() if t == away_id and s <= a and e >= b]
        last = b >= 90
        sel = (sh[:, 1] >= a) & ((sh[:, 1] < b) | last) & (sh[:, 3] == 0) if len(sh) else np.zeros(0, bool)
        xh = float(sh[sel & (sh[:, 0] == home_id), 2].sum()) if len(sh) else 0.0
        xa = float(sh[sel & (sh[:, 0] == away_id), 2].sum()) if len(sh) else 0.0
        out.append((b - a, hp, ap, xh, xa))
    return out


def rate(m: pd.DataFrame, a: pd.DataFrame, s: pd.DataFrame, as_of, xi: float = 0.0025, window_days: int = 1100,
         k_box: float = 6.0, ridge: float = 400.0, stint_cache: dict | None = None) -> PlayerRatings:
    as_of = pd.Timestamp(as_of)
    mm = m[(m["utc"] < as_of) & (m["utc"] >= as_of - pd.Timedelta(days=window_days))]
    w_match = np.exp(-xi * (as_of - mm["utc"]).dt.days.to_numpy(float))
    wmap = dict(zip(mm["id"], w_match))
    aa = a[a["match_id"].isin(wmap)].copy()
    aa["w"] = aa["match_id"].map(wmap)
    pr = PlayerRatings(as_of)
    if aa.empty:
        return pr
    # ---- box score attack, shrunk to position mean
    aa["n90"] = aa["minutes"] / 90.0
    aa["xga"] = aa["xg"] + aa["xa"]
    g = aa.groupby("player_id").apply(lambda x: pd.Series({
        "xga": float((x.w * x.xga).sum()), "n90": float((x.w * x.n90).sum()),
        "pos": x.sort_values("match_id")["pos"].mode().iat[0] if len(x) else "MID",
        "name": x["name"].iat[-1]}), include_groups=False)
    pos_rate = (g.groupby("pos")["xga"].sum() / g.groupby("pos")["n90"].sum().clip(lower=1e-9)).to_dict()
    pr.pos_box = pos_rate
    for pid, r in g.iterrows():
        prior = pos_rate.get(r.pos, 0.15)
        pr.box[pid] = (r.xga + k_box * prior) / (r.n90 + k_box)
        pr.pos[pid] = r.pos
        pr.names[pid] = r["name"]
    last_team = aa.sort_values("match_id").groupby("player_id")["team_id"].last()
    pr.team_of = last_team.to_dict()

    # ---- on-pitch xG plus-minus (sparse ridge)
    pid_ix = {p: i for i, p in enumerate(g.index)}
    n = len(pid_ix)
    rows, cols, vals, y, wt = [], [], [], [], []
    r_i = 0
    a_by = dict(tuple(aa.groupby("match_id")))
    s_by = dict(tuple(s[s["match_id"].isin(wmap)].groupby("match_id")))
    for mt in mm.itertuples():
        key = mt.id
        if stint_cache is not None and key in stint_cache:
            st = stint_cache[key]
        else:
            st = _stints(a_by.get(mt.id, aa.iloc[:0]), s_by.get(mt.id, s.iloc[:0]), mt.home_id, mt.away_id)
            if stint_cache is not None:
                stint_cache[key] = st
        wm = wmap[mt.id]
        for dur, hp, ap, xh, xa in st:
            if len(hp) < 9 or len(ap) < 9:
                continue
            for att, dfn, xg_ in ((hp, ap, xh), (ap, hp, xa)):
                for p in att:
                    rows.append(r_i); cols.append(pid_ix[p]); vals.append(1.0 / len(att))
                for p in dfn:
                    rows.append(r_i); cols.append(n + pid_ix[p]); vals.append(-1.0 / len(dfn))
                y.append(xg_ / dur * 90.0)
                wt.append(wm * dur / 90.0)
                r_i += 1
    if r_i:
        X = sparse.csr_matrix((vals, (rows, cols)), shape=(r_i, 2 * n))
        y = np.array(y)
        wt = np.array(wt)
        sw = np.sqrt(wt)
        yc = y - np.average(y, weights=wt)
        Xw = sparse.diags(sw) @ X
        # ridge via augmented least squares: lambda = ridge / (11^2) scale since entries are 1/11
        sol = lsqr(Xw, sw * yc, damp=np.sqrt(ridge) / 11.0, atol=1e-8, btol=1e-8, iter_lim=3000)[0]
        for p, i in pid_ix.items():
            pr.o[p] = float(sol[i])
            pr.d[p] = float(sol[n + i])
    return pr


def team_history(a: pd.DataFrame, m: pd.DataFrame) -> pd.DataFrame:
    """One row per (match, team) with its starting XI, in date order."""
    st = a[a["started"] == 1].groupby(["match_id", "team_id"])["player_id"].apply(list).reset_index()
    st = st.merge(m[["id", "utc"]], left_on="match_id", right_on="id").drop(columns="id")
    return st.sort_values("utc").reset_index(drop=True)


def xi_value(pr: PlayerRatings, xi: list) -> dict:
    out = {"box": 0.0, "o": 0.0, "d": 0.0}
    for p in xi:
        pos = pr.pos.get(p)
        if pos != "GK":
            out["box"] += pr.box.get(p, pr.pos_box.get(pos or "MID", 0.12) * 0.7)  # unknown -> below-average
        out["o"] += pr.o.get(p, -0.02)
        out["d"] += pr.d.get(p, -0.02)
    return out


def minutes_share(a: pd.DataFrame, team_id: int, before_ids: list) -> dict:
    """player -> share of the team's minutes in the given previous matches."""
    x = a[(a["team_id"] == team_id) & a["match_id"].isin(before_ids)]
    tot = 90.0 * len(before_ids)
    return (x.groupby("player_id")["minutes"].sum() / tot).to_dict() if tot else {}


def features(pr: PlayerRatings, a: pd.DataFrame, hist: pd.DataFrame, team_id: int, xi: list,
             when: pd.Timestamp, n_prev: int = 10) -> dict | None:
    """Deviation of this XI from the team's typical XI (last n_prev lineups before `when`)."""
    h = hist[(hist["team_id"] == team_id) & (hist["utc"] < when)].tail(n_prev)
    # need a current picture of the squad: clubs we only see in Europe (a few games a season) are skipped
    h = h[h["utc"] >= pd.Timestamp(when) - pd.Timedelta(days=120)]
    if len(h) < 4:
        return None
    share = minutes_share(a, team_id, h["match_id"].tolist())
    vals = [xi_value(pr, x) for x in h["player_id"]]
    typ = {k: float(np.mean([v[k] for v in vals])) for k in ("box", "o", "d")}
    cur = xi_value(pr, xi)
    # regulars: minutes share of this XI vs the team's 11 most-used players (0 = full-strength side),
    # split into the back line (GK + 4 defenders) and the front six
    pos = lambda p: pr.pos.get(p, "MID")
    srt = sorted(share.items(), key=lambda kv: -kv[1])
    back = [v for p, v in srt if pos(p) in ("GK", "DEF")][:5]
    front = [v for p, v in srt if pos(p) in ("MID", "FWD")][:6]
    reg_def = sum(share.get(p, 0) for p in xi if pos(p) in ("GK", "DEF")) - sum(back)
    reg_att = sum(share.get(p, 0) for p in xi if pos(p) in ("MID", "FWD")) - sum(front)
    reg = sum(share.get(p, 0) for p in xi) - sum(v for _, v in srt[:11])
    return {"box": cur["box"] - typ["box"], "o": cur["o"] - typ["o"], "d": cur["d"] - typ["d"],
            "reg": reg, "reg_def": reg_def, "reg_att": reg_att}


# ------------------------------------------------------------------------------- live use
CACHE = Path(__file__).resolve().parent.parent / "data" / "lineup_ratings.pkl"


@dataclass
class LiveState:
    pr: PlayerRatings
    apps: pd.DataFrame
    hist: pd.DataFrame
    eff: dict


def current(update: bool = True) -> LiveState | None:
    """Player ratings as of today (cached per day) plus the history needed to value a lineup.
    update=True first adds any newly finished matches from FotMob (current season only)."""
    import pickle
    from . import players as P
    eff = load_effects()
    if not eff:
        return None
    if update:
        try:
            P.collect(seasons=P.SEASONS[-1:], workers=3)
        except Exception as e:
            print(f"  ! player data update failed: {e}")
    m, a, s = P.load()
    if m.empty:
        return None
    today = pd.Timestamp.today().normalize()
    pr = None
    if CACHE.exists():
        try:
            c = pickle.loads(CACHE.read_bytes())
            if c["as_of"] == today and c["n"] == len(m):
                pr = c["pr"]
        except Exception:
            pr = None
    if pr is None:
        pr = rate(m, a, s, today + pd.Timedelta(days=1))
        CACHE.write_bytes(pickle.dumps({"as_of": today, "n": len(m), "pr": pr}))
    return LiveState(pr, a, team_history(a, m), eff)


def live_adjust(st: LiveState, lu: dict, when=None) -> tuple[float, float, dict] | None:
    """xG multipliers from a FotMob predicted/confirmed lineup. None if it can't be valued."""
    when = pd.Timestamp(when) if when is not None else pd.Timestamp.now()
    info = {}
    feats = {}
    for side in ("home", "away"):
        t = lu.get(side) or {}
        tid = t.get("id")
        xi = [int(p["id"]) for p in t.get("starters") or [] if p.get("id") is not None]
        if tid is None or len(xi) < 11:
            return None
        tid = int(tid)
        f = features(st.pr, st.apps, st.hist, tid, xi, when)
        feats[side] = f
        h = st.hist[(st.hist["team_id"] == tid) & (st.hist["utc"] < when)].tail(10)
        share = minutes_share(st.apps, tid, h["match_id"].tolist())
        missing = sorted([p for p, v in share.items() if v >= 0.5 and p not in xi], key=lambda p: -share[p])
        info[side] = {"feat": f, "missing": [(st.pr.names.get(p, str(p)), share[p]) for p in missing][:6],
                      "unrated": [p.get("name") for p in t.get("starters") if int(p["id"]) not in st.pr.box][:4]}
    if feats["home"] is None and feats["away"] is None:
        return None
    mh, ma = multipliers(st.eff, feats["home"], feats["away"])
    info["type"] = lu.get("type")
    return mh, ma, info


# ------------------------------------------------------------------------------- effects (fitted)
def match_features(start="2024-08-01", progress=lambda f, m: None) -> pd.DataFrame:
    """Lineup deviations for every stored match from `start`, using player ratings refit at the start of
    each month (only earlier matches). Columns: id, comp, utc, day, home, away, fh_*, fa_*."""
    from . import players as P
    m, a, s = P.load()
    hist = team_history(a, m)
    xi_of = {(r.match_id, r.team_id): r.player_id for r in hist.itertuples()}
    cache: dict = {}
    rows = []
    months = pd.period_range(pd.Timestamp(start).to_period("M"), pd.Timestamp.today().to_period("M"), freq="M")
    for i, mo in enumerate(months):
        progress(i / len(months), f"Lineups: rating players as of {mo.start_time:%b %Y}")
        mm = m[(m["utc"] >= mo.start_time) & (m["utc"] < mo.end_time)]
        if mm.empty:
            continue
        pr = rate(m, a, s, mo.start_time, stint_cache=cache)
        for r in mm.itertuples():
            fh = features(pr, a, hist, r.home_id, xi_of.get((r.id, r.home_id), []), r.utc)
            fa = features(pr, a, hist, r.away_id, xi_of.get((r.id, r.away_id), []), r.utc)
            if fh is None or fa is None:
                continue
            rows.append({"id": r.id, "comp": r.comp, "utc": r.utc, "day": r.utc.normalize(), "home": r.home,
                         "away": r.away, **{f"fh_{k}": v for k, v in fh.items()},
                         **{f"fa_{k}": v for k, v in fa.items()}})
    return pd.DataFrame(rows)


def join_model(F: pd.DataFrame, bt: pd.DataFrame) -> pd.DataFrame:
    """Attach team-model predictions (backtest rows with lh, la, rho, hg, ag) by day + team names."""
    from .data_soccer import resolve_team
    teams = sorted(set(bt["home"]) | set(bt["away"]))
    names = {}
    for nm in set(F["home"]) | set(F["away"]):
        try:
            names[nm] = resolve_team(nm, teams)
        except SystemExit:
            names[nm] = None
    F = F.assign(h=F["home"].map(names), a=F["away"].map(names)).drop(columns=["home", "away", "comp"])
    b = bt.assign(day=pd.to_datetime(bt["date"]).dt.normalize())
    return F.merge(b, left_on=["day", "h", "a"], right_on=["day", "home", "away"], how="inner")


def fit_coef(J: pd.DataFrame, ks: list, lam: float = 20.0) -> dict:
    """Poisson fit of log xG adjustments on top of the team model: own lineup + opponent lineup terms."""
    from scipy.optimize import minimize
    Fh, Fa = J[[f"fh_{k}" for k in ks]].to_numpy(float), J[[f"fa_{k}" for k in ks]].to_numpy(float)
    yh, ya = J["hg"].to_numpy(float), J["ag"].to_numpy(float)
    l0h, l0a = np.log(J["lh"].to_numpy(float)), np.log(J["la"].to_numpy(float))
    n = len(ks)

    def nll(b):
        eh = l0h + Fh @ b[:n] + Fa @ b[n:]
        ea = l0a + Fa @ b[:n] + Fh @ b[n:]
        return -(yh * eh - np.exp(eh) + ya * ea - np.exp(ea)).sum() + lam * (b @ b)
    b = minimize(nll, np.zeros(2 * n), method="BFGS").x
    return {**{f"own_{k}": float(b[i]) for i, k in enumerate(ks)}, **{f"opp_{k}": float(b[n + i]) for i, k in enumerate(ks)}}


def adjust_rows(J: pd.DataFrame, coef: dict, cap: float = 0.25) -> tuple[np.ndarray, np.ndarray]:
    eff = {"coef": coef, "cap": cap}
    mh, ma = zip(*[multipliers(eff, {k: r[f"fh_{k}"] for k in FEATS if f"fh_{k}" in r},
                               {k: r[f"fa_{k}"] for k in FEATS if f"fa_{k}" in r}) for _, r in J.iterrows()])
    return J["lh"].to_numpy() * np.array(mh), J["la"].to_numpy() * np.array(ma)


def walk_forward_rates(J: pd.DataFrame, ks: list, first_eval="2025-01") -> pd.DataFrame:
    """Lineup-adjusted (lh, la) for each month from first_eval, with coefficients fitted on earlier months only."""
    J = J.copy()
    J["month"] = J["day"].dt.to_period("M")
    out = []
    for mo in sorted(J["month"].unique()):
        if mo < pd.Period(first_eval, "M"):
            continue
        tr = J[J["month"] < mo]
        te = J[J["month"] == mo]
        if len(tr) < 200:
            continue
        lh, la = adjust_rows(te, fit_coef(tr, ks))
        out.append(te.assign(lh_adj=lh, la_adj=la))
    return pd.concat(out) if out else J.iloc[:0]


def load_effects() -> dict | None:
    return json.loads(EFFECTS_PATH.read_text()) if EFFECTS_PATH.exists() else None


def multipliers(eff: dict, fh: dict | None, fa: dict | None) -> tuple[float, float]:
    """xG multipliers (home, away) from both teams' lineup deviations."""
    if not eff or (fh is None and fa is None):
        return 1.0, 1.0
    zero = {k: 0.0 for k in FEATS}
    fh, fa = fh or zero, fa or zero
    co = eff["coef"]
    own = lambda f: sum(co.get("own_" + k, 0.0) * f.get(k, 0.0) for k in FEATS)
    opp = lambda f: sum(co.get("opp_" + k, 0.0) * f.get(k, 0.0) for k in FEATS)
    cap = eff.get("cap", 0.25)
    mh = float(np.exp(np.clip(own(fh) + opp(fa), -cap, cap)))
    ma = float(np.exp(np.clip(own(fa) + opp(fh), -cap, cap)))
    return mh, ma
