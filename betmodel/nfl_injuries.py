"""NFL injury features from the official weekly injury reports (nflverse) + snap counts.

For every team-week: each player listed Out / Doubtful / Questionable is weighted by how much he
was actually playing (average snap share over the team's previous 4 games, counting games he
missed as 0) and summed by position group. A starting WR who is Out scores ~0.9 in "WR"; a
depth player scores ~0.1. The model then *fits* how many points each group costs.
Quarterbacks are excluded - they are handled by the per-QB values in nfl.py.
"""
from __future__ import annotations

import io

import numpy as np
import pandas as pd

from .nfl import DATA_DIR, _get

INJ_URL = "https://github.com/nflverse/nflverse-data/releases/download/injuries/injuries_{y}.csv"
SNAP_URL = "https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_{y}.csv"

OFF_GROUPS = {"OL": {"T", "G", "C", "OL", "OT", "OG"}, "WR": {"WR"}, "TE": {"TE"}, "RB": {"RB", "FB"}}
DEF_GROUPS = {"DL": {"DT", "DE", "NT", "DL"}, "LB": {"LB", "OLB", "ILB", "MLB"}, "CB": {"CB"},
              "S": {"S", "SS", "FS", "DB"}}
GROUPS = list(OFF_GROUPS) + list(DEF_GROUPS)
STATUS_W = {"Out": 1.0, "Doubtful": 0.8, "Questionable": 0.25,
            "DNP": 0.5, "Limited": 0.1}  # practice-based guesses before the final Friday report


def _group(pos: str) -> str | None:
    for g, s in {**OFF_GROUPS, **DEF_GROUPS}.items():
        if pos in s:
            return g
    return None


def injury_index(seasons: list[int], max_age_h: float = 3) -> pd.DataFrame:
    """One row per (season, week, team) with columns inj_OL ... inj_S, plus the players behind them."""
    cur = max(seasons)
    out, detail = [], []
    for y in seasons:
        age = max_age_h if y == cur else 24 * 365
        try:
            inj = pd.read_csv(io.BytesIO(_get(INJ_URL.format(y=y), DATA_DIR / f"injuries_{y}.csv", age)))
            snp = pd.read_csv(io.BytesIO(_get(SNAP_URL.format(y=y), DATA_DIR / f"snap_counts_{y}.csv", age)))
        except Exception as e:
            print(f"  ! no injury/snap data for {y}: {e}")
            continue
        # final game statuses arrive Friday; until then infer from practice participation, but only
        # for team-weeks that have no final report yet (so historical training rows are unchanged)
        has_final = inj.groupby(["team", "week"])["report_status"].transform(lambda s: s.notna().any())
        prac = inj["practice_status"].fillna("")
        guess = np.where(prac.str.contains("Did Not Participate"), "DNP",
                         np.where(prac.str.contains("Limited"), "Limited", ""))
        inj["report_status"] = np.where(~has_final & inj["report_status"].isna(), guess, inj["report_status"])
        inj = inj[inj["report_status"].isin(STATUS_W)].copy()
        inj["grp"] = inj["position"].map(_group)
        inj = inj.dropna(subset=["grp"])
        snp["share"] = np.maximum(snp["offense_pct"].fillna(0), snp["defense_pct"].fillna(0))
        snp = snp[snp["game_type"] == "REG"] if "game_type" in snp else snp
        # team schedule of weeks played, to count missed games as zero share
        tw = snp.groupby("team")["week"].apply(lambda s: sorted(s.unique())).to_dict()
        share = snp.set_index(["team", "player", "week"])["share"]
        share = share[~share.index.duplicated()]
        for r in inj.itertuples():
            weeks = [w for w in tw.get(r.team, []) if w < r.week][-4:]
            if not weeks:
                continue
            vals = [share.get((r.team, r.full_name, w), 0.0) for w in weeks]
            s = float(np.mean(vals))
            if s <= 0.05:
                continue
            v = STATUS_W[r.report_status] * s
            out.append((y, r.week, r.team, r.grp, v))
            detail.append((y, r.week, r.team, r.full_name, r.position, r.report_status, round(s, 2)))
    if not out:
        return pd.DataFrame(columns=["season", "week", "team"] + [f"inj_{g}" for g in GROUPS])
    df = pd.DataFrame(out, columns=["season", "week", "team", "grp", "v"])
    piv = df.pivot_table(index=["season", "week", "team"], columns="grp", values="v", aggfunc="sum", fill_value=0)
    piv = piv.reindex(columns=GROUPS, fill_value=0).add_prefix("inj_").reset_index()
    global DETAIL
    DETAIL = pd.DataFrame(detail, columns=["season", "week", "team", "player", "pos", "status", "snap_share"])
    return piv


DETAIL: pd.DataFrame | None = None  # player-level rows behind the latest injury index
LIVE: dict = {}  # team -> [{player,pos,status,snap_share,group}] for the upcoming week (ESPN)


def _norm(n: str) -> str:
    import re
    n = re.sub(r"[^a-z ]", "", str(n).lower().replace(".", ""))
    return " ".join(w for w in n.split() if w not in {"jr", "sr", "ii", "iii", "iv"})


def apply_live(g: pd.DataFrame, live: dict, season: int, week: int) -> pd.DataFrame:
    """Overwrite this week's injury indices with ESPN's current statuses (final Friday designations,
    updated through game day). Players are weighted by recent snap share, exactly like history."""
    global LIVE
    LIVE = {}
    try:
        snp = pd.read_csv(io.BytesIO(_get(SNAP_URL.format(y=season), DATA_DIR / f"snap_counts_{season}.csv", 3)))
    except Exception:
        return g
    snp["share"] = np.maximum(snp["offense_pct"].fillna(0), snp["defense_pct"].fillna(0))
    snp["key"] = snp["player"].map(_norm)
    cols = [f"inj_{x}" for x in GROUPS]
    for team, players in live.items():
        weeks = sorted(snp.loc[(snp["team"] == team) & (snp["week"] < week), "week"].unique())[-4:]
        ts = snp[(snp["team"] == team) & snp["week"].isin(weeks)]
        sums = dict.fromkeys(GROUPS, 0.0)
        rows = []
        for p in players:
            w = STATUS_W.get(p["status"])
            grp = _group(p.get("pos") or "")
            if not w or not grp or not weeks:
                continue
            s = ts.loc[ts["key"] == _norm(p["player"]), ["week", "share"]]
            share = float(s.groupby("week")["share"].max().reindex(weeks, fill_value=0).mean()) if len(s) else 0.0
            if share > 0.05:
                sums[grp] += w * share
            rows.append({**p, "snap_share": round(share, 2), "group": grp})
        LIVE[team] = sorted(rows, key=lambda r: -r["snap_share"])
        for side in ("home", "away"):
            sel = (g["season"] == season) & (g["week"] == week) & (g[f"{side}_team"] == team)
            for gname, c in zip(GROUPS, cols):
                g.loc[sel, f"{side}_{c}"] = sums[gname]
    return g


def attach(g: pd.DataFrame, first_season: int) -> pd.DataFrame:
    """Add home_inj_* / away_inj_* columns to the games frame."""
    seasons = list(range(max(first_season, 2016), int(g["season"].max()) + 1))
    idx = injury_index(seasons)
    cols = [f"inj_{x}" for x in GROUPS]
    for side in ("home", "away"):
        m = idx.rename(columns={"team": f"{side}_team", **{c: f"{side}_{c}" for c in cols}})
        g = g.merge(m, on=["season", "week", f"{side}_team"], how="left")
        for c in cols:
            g[f"{side}_{c}"] = g[f"{side}_{c}"].fillna(0.0)
    return g
