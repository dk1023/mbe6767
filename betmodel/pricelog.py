"""Price log: every price a run sees, graded later against the closing line.

Each run stores every book's pre-game price with the edge the model gave it at that moment (blended, and
against the sharp books alone). Once a game is final, each price gets its closing-line value (CLV): what it
was worth at the sharp closing no-vig odds, as expected profit per 1 staked. Grouping hundreds of
DraftKings prices by the edge seen at the time shows which edges really beat the close. That is how
to set the minimum edge, markets and timing from data, instead of from a handful of bets.

Closing lines (free): NFL from nflverse (the morning after), soccer from football-data.co.uk (about twice a
week; Champions / Europa League have no free closing odds, so those prices stay ungraded).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import db, odds as O
from .odds_api import BETTABLE  # the sportsbooks you can bet at
EDGE_BUCKETS = [(-9, 0, "below 0"), (0, .01, "0 to 1%"), (.01, .02, "1 to 2%"), (.02, .03, "2 to 3%"),
                (.03, .05, "3 to 5%"), (.05, 9, "5% and up")]
HOUR_BUCKETS = [(-1, 3, "under 3 h"), (3, 12, "3 to 12 h"), (12, 48, "12 to 48 h"), (48, 1e6, "2+ days")]
MARKETS = {"SPREAD": "NFL spread", "TOTAL": "NFL total", "ML": "NFL moneyline", "1X2": "Soccer result",
           "OU25": "Soccer over/under 2.5"}


def frame() -> pd.DataFrame:
    with db.connect() as con:
        return pd.read_sql_query("SELECT rowid AS rid, * FROM odds", con)


def _match(index: dict, home, away, game_date):
    """The finished game for a logged price (same teams, within a day of the logged date)."""
    gd = pd.Timestamp(game_date)
    for r in index.get((home, away), []):
        if abs(pd.Timestamp(r.date).normalize() - gd) <= pd.Timedelta(days=1):
            return r
    return None


def grade(nm=None, nfl_games=None, soccer_df=None) -> int:
    """Fill in closing-line value for logged prices whose games are final. nm: a fitted NFLModel (its margin
    and total distributions turn a closing line and its prices into a cover chance at any line)."""
    with db.connect() as con:
        d = pd.read_sql_query("SELECT rowid AS rid, sport, game_date, home, away, market, selection, line, odds "
                              "FROM odds WHERE clv IS NULL", con)
    if d.empty:
        return 0
    out = []
    if nm is not None and nfl_games is not None and (d["sport"] == "nfl").any():
        idx = {}
        for r in nfl_games[nfl_games["result"].notna()].itertuples():
            idx.setdefault((r.home_team, r.away_team), []).append(r)
        cache = {}
        for x in d[d["sport"] == "nfl"].itertuples():
            r = _match(idx, x.home, x.away, x.game_date)
            res = _nfl_close(nm, r, x, cache) if r is not None else None
            if res is not None:
                out.append((*res, x.rid))
    if soccer_df is not None and len(soccer_df) and (d["sport"] == "soccer").any():
        idx = {}
        for r in soccer_df[soccer_df["hg"].notna()].itertuples():
            idx.setdefault((r.home, r.away), []).append(r)
        for x in d[d["sport"] == "soccer"].itertuples():
            r = _match(idx, x.home, x.away, x.game_date)
            res = _soccer_close(r, x) if r is not None else None
            if res is not None:
                out.append((*res, x.rid))
    if out:
        t = db.now()
        with db.connect() as con:
            con.executemany("UPDATE odds SET clv=?, close_line=?, close_odds=?, graded_at=? WHERE rowid=?",
                            [(c, ln, co, t, rid) for c, ln, co, rid in out])
    return len(out)


def _nfl_close(nm, r, x, cache):
    """(clv, closing line, closing price) for one logged NFL price, from nflverse's closing numbers."""
    p = lambda v: O.parse_odds(v) if pd.notna(v) else 1.909
    home = x.selection == x.home
    if x.market == "SPREAD":
        if pd.isna(r.spread_line) or x.line is None or pd.isna(x.line):
            return None
        k = ("m", r.Index)
        if k not in cache:
            cache[k] = nm.margin_dist(nm.mu_from_market(-r.spread_line,
                                                        O.devig([p(r.home_spread_odds), p(r.away_spread_odds)], "mult")[0]))
        w, _, lo = nm.line_probs(cache[k], float(x.line), 1 if home else -1)
        return float(w * (x.odds - 1) - lo), float(-r.spread_line if home else r.spread_line), None
    if x.market == "TOTAL":
        if pd.isna(r.total_line) or x.line is None or pd.isna(x.line):
            return None
        k = ("t", r.Index)
        if k not in cache:
            cache[k] = nm.total_dist(nm.tau_from_market(r.total_line, O.devig([p(r.over_odds), p(r.under_odds)], "mult")[0]))
        td = cache[k]
        w, _, lo = nm.line_probs(td, -float(x.line)) if x.selection == "OVER" else nm.line_probs(td, float(x.line), -1)
        return float(w * (x.odds - 1) - lo), float(r.total_line), None
    if x.market == "ML":
        if pd.isna(r.home_moneyline) or pd.isna(r.away_moneyline):
            return None
        c = [O.parse_odds(r.home_moneyline), O.parse_odds(r.away_moneyline)]
        i = 0 if home else 1
        return float(x.odds * O.devig(c, "shin")[i] - 1), None, float(c[i])
    return None


def _soccer_close(r, x):
    """(clv, None, closing price) for one logged soccer price, from the sharpest free closing odds."""
    if x.market == "1X2" and x.selection in ("H", "D", "A"):
        c, i = [r.cH, r.cD, r.cA], "HDA".index(x.selection)
    elif x.market == "OU25" and x.selection in ("OVER", "UNDER"):
        c, i = [r.cO25, r.cU25], 0 if x.selection == "OVER" else 1
    else:
        return None
    if not all(pd.notna(v) and v > 1 for v in c):
        return None
    return float(x.odds * O.devig(c, "shin")[i] - 1), None, float(c[i])


# ------------------------------------------------------------------------------------------ Kalshi closing prices
def _kalshi_chances(x, km, ko):
    """(win, push, lose) for one bet at Kalshi's last pre-kickoff prices, or None if Kalshi can't say.
    Spreads and totals use the ladder of 'wins by over s' / 'over s' markets either side of the bet's line,
    so whole-number lines get their push chance too."""
    import re
    from . import freeodds as F
    sel, line = str(x.selection), (None if x.line is None or pd.isna(x.line) else float(x.line))

    def ladder_p(series, event, strike, team=None):
        ms = F.event_markets(f"{series}-{event}")
        t = next((m["ticker"] for m in ms if m.get("floor_strike") is not None and abs(float(m["floor_strike"]) - strike) < 1e-6
                  and (team is None or re.fullmatch(rf"{team}\d+", str(m["ticker"]).rsplit("-", 1)[-1]))), None)
        return F.close_mid(series, t, ko)

    if x.sport == "nfl":
        tk = km.home_k if sel == x.home else km.away_k if sel == x.away else None
        ok_ = km.away_k if tk == km.home_k else km.home_k

        def win_p(team):  # chance `team` wins outright, from the game-winner markets
            a = F.close_mid(km.s_game, f"{km.s_game}-{km.code}-{team}", ko)
            b = F.close_mid(km.s_game, f"{km.s_game}-{km.code}-{ok_ if team == tk else tk}", ko)
            return None if a is None or b is None else a / (a + b)

        def over(team, s):  # chance `team` wins by more than s (s = k + 0.5)
            if abs(s - 0.5) < 1e-9:
                p = ladder_p(km.s_spread, km.code, s, team)
                return p if p is not None else win_p(team)
            return ladder_p(km.s_spread, km.code, s, team)
        if x.market == "ML" or (x.market == "SPREAD" and line is not None and abs(line) < 1e-9):
            if tk is None:
                return None
            p = win_p(tk)
            return None if p is None else (p, 0.0, 1 - p)
        if x.market == "SPREAD" and tk is not None and line is not None:
            k = abs(line)
            team = tk if line < 0 else ok_  # favourite: our team must win by > k; underdog: the other team mustn't
            if k % 1:
                p = over(team, k)
                if p is None:
                    return None
                return (p, 0.0, 1 - p) if line < 0 else (1 - p, 0.0, p)
            hi, lo = over(team, k + .5), over(team, k - .5)
            if hi is None or lo is None:
                return None
            push = max(lo - hi, 0.0)
            return (hi, push, 1 - lo) if line < 0 else (1 - lo, push, hi)
        if x.market == "TOTAL" and line is not None and sel in ("OVER", "UNDER"):
            if line % 1:
                p = ladder_p(km.s_total, km.code, line)
                if p is None:
                    return None
                return (p, 0.0, 1 - p) if sel == "OVER" else (1 - p, 0.0, p)
            hi, lo = ladder_p(km.s_total, km.code, line + .5), ladder_p(km.s_total, km.code, line - .5)
            if hi is None or lo is None:
                return None
            push = max(lo - hi, 0.0)
            return (hi, push, 1 - lo) if sel == "OVER" else (1 - lo, push, hi)
        return None
    if x.market == "1X2" and sel in ("H", "D", "A"):
        ps = [F.close_mid(km.s_game, f"{km.s_game}-{km.code}-{k}", ko) for k in (km.home_k, "TIE", km.away_k)]
        if any(p is None for p in ps):
            return None
        p = ps["HDA".index(sel)] / sum(ps)
        return (p, 0.0, 1 - p)
    if x.market == "OU25" and km.s_total and sel in ("OVER", "UNDER"):
        p = ladder_p(km.s_total, km.code, 2.5)
        if p is None:
            return None
        return (p, 0.0, 1 - p) if sel == "OVER" else (1 - p, 0.0, p)
    return None


def grade_kalshi(max_games: int = 60) -> int:
    """Closing-line value against Kalshi's last pre-kickoff prices for logged DraftKings prices AND your picks
    (recommended, leans, least-bad), once a game has kicked off. Free; covers the Champions / Europa League too.
    Returns how many got a value; ones Kalshi can't price are marked done so they aren't retried."""
    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    qs = ", ".join("?" * len(BETTABLE))
    with db.connect() as con:
        km = pd.read_sql_query("SELECT * FROM kalshi_games", con)
        if km.empty:
            return 0
        cols = "sport, game_date, home, away, market, selection, line, odds, book"
        rows = pd.concat([
            pd.read_sql_query(f"SELECT rowid AS rid, 'odds' AS tbl, {cols} FROM odds WHERE kgraded_at IS NULL "
                              f"AND book IN ({qs})", con, params=list(BETTABLE)),
            pd.read_sql_query(f"SELECT id AS rid, 'picks' AS tbl, {cols} FROM picks WHERE kgraded_at IS NULL "
                              f"AND book IN ({qs})", con, params=list(BETTABLE))], ignore_index=True)
    if rows.empty:
        return 0
    km["ko"] = pd.to_datetime(km["kickoff"], errors="coerce")
    idx = {(r.sport, r.game_date, r.home, r.away): r for r in km.itertuples()}
    out, games = [], 0
    for key, grp in rows.groupby(["sport", "game_date", "home", "away"]):
        m = idx.get(key)
        if m is None or pd.isna(m.ko) or m.ko > now - pd.Timedelta(minutes=10):
            continue  # not started yet (or no Kalshi markets for it)
        games += 1
        if games > max_games:
            break
        seen = {}
        for x in grp.itertuples():
            k = (x.market, str(x.selection), None if x.line is None or pd.isna(x.line) else round(float(x.line), 2))
            if k not in seen:
                try:
                    seen[k] = _kalshi_chances(x, m, m.ko)
                except Exception:
                    seen[k] = None
            ch = seen[k]
            if ch is None:
                out.append((x.tbl, x.rid, None, None))
                continue
            w, _, lo = ch
            out.append((x.tbl, x.rid, float(w * (x.odds - 1) - lo), float(w / (w + lo)) if w + lo > 0 else None))
    if out:
        t = db.now()
        with db.connect() as con:
            for tbl, idcol in (("odds", "rowid"), ("picks", "id")):
                vals = [(kc, kp, t, rid) for tb, rid, kc, kp in out if tb == tbl]
                if vals:
                    con.executemany(f"UPDATE {tbl} SET kclv=?, kclose=?, kgraded_at=? WHERE {idcol}=?", vals)
    return sum(1 for o in out if o[2] is not None)


# ------------------------------------------------------------------------------------------ report
def graded(d: pd.DataFrame | None = None) -> pd.DataFrame:
    """Graded DraftKings prices, one row per distinct price (a price seen by several runs counts once,
    at the first time it was seen)."""
    d = (frame() if d is None else d).copy()
    # CLV against Kalshi's closing price where there is one (sharper, and it covers UEFA games), else the
    # free closing line (nflverse / football-data)
    d["cv"] = d["kclv"].where(d["kclv"].notna(), d["clv"]) if "kclv" in d else d["clv"]
    g = d[d["cv"].notna() & d["book"].isin(BETTABLE)].copy()
    if g.empty:
        return g
    g = g.sort_values("snap_at").drop_duplicates(
        ["sport", "game_date", "home", "away", "market", "selection", "line", "odds", "book"])
    return g


def _bucket(v, buckets, missing="no sharp price"):
    if v is None or pd.isna(v):
        return missing
    for lo, hi, lab in buckets:
        if lo <= v < hi:
            return lab
    return buckets[-1][2]


def table(g: pd.DataFrame, by: str) -> pd.DataFrame:
    """Prices, average CLV and share that beat the close, grouped by: edge_mkt, edge, hrs, market, book."""
    if g.empty:
        return pd.DataFrame()
    if by in ("edge_mkt", "edge"):
        key, order = g[by].map(lambda v: _bucket(v, EDGE_BUCKETS)), [b[2] for b in EDGE_BUCKETS] + ["no sharp price"]
    elif by == "hrs":
        key, order = g["hrs"].map(lambda v: _bucket(v, HOUR_BUCKETS, "unknown")), [b[2] for b in HOUR_BUCKETS] + ["unknown"]
    elif by == "market":
        key, order = g["market"].map(lambda m: MARKETS.get(m, m)), list(MARKETS.values())
    else:
        key, order = g[by], sorted(g[by].dropna().unique())
    t = g.groupby(key).agg(prices=("cv", "size"), avg_clv=("cv", "mean"), beat=("cv", lambda s: float((s > 0).mean())))
    return t.reindex([o for o in order if o in t.index])


def text_report() -> str:
    d = frame()
    if d.empty:
        return "Price log is empty: each Run model adds every book's prices."
    g = graded(d)
    lines = [f"{len(d):,} prices logged from {d['snap_at'].nunique()} run(s); "
             f"{len(g):,} distinct DraftKings prices graded against the closing line."]
    for by, title in (("edge_mkt", "By edge against the sharp books (price only)"), ("market", "By market"),
                      ("hrs", "By time before kickoff")):
        t = table(g, by)
        if len(t):
            lines += ["", title, t.to_string(formatters={"avg_clv": "{:+.2%}".format, "beat": "{:.0%}".format})]
    return "\n".join(lines)


def view():
    """The Pick tracker card: what the log holds, and once games close, which edges beat the closing line."""
    from htmltools import tags
    d = frame()
    head = tags.h5("Price log: which edges beat the closing line", style="margin-top:26px")
    intro = tags.div("Every run saves every book's price with the edge the model saw. Once a game kicks off, each "
                     "DraftKings price is checked against Kalshi's last price before kickoff (the sharp close; "
                     "otherwise the free closing line once the game is final). Average CLV above 0 for an edge size "
                     "means prices like that beat the market, which is what to set the minimum edge from.",
                     class_="note")
    if d.empty:
        return tags.div(head, intro, tags.div("Nothing logged yet: the next Run model starts it.", class_="empty"))
    g = graded(d)
    stat = tags.div(f"{len(d):,} prices logged from {d['snap_at'].nunique()} run(s) · "
                    f"{len(g):,} DraftKings prices graded so far", class_="pagehint", style="margin-top:8px")
    if g.empty:
        return tags.div(head, intro, stat, tags.div(
            "Nothing graded yet. Prices are graded against Kalshi's closing price on the first free update or run "
            "after kickoff (all competitions, including the Champions and Europa League); games Kalshi doesn't "
            "list fall back to nflverse (NFL, the morning after) or football-data.co.uk (domestic soccer, about "
            "twice a week).", class_="note"))

    def tbl(by, first):
        t = table(g, by)
        if t.empty:
            return None
        rows = [tags.tr(tags.td(k), tags.td(f"{int(r.prices):,}"),
                        tags.td(tags.span(f"{r.avg_clv:+.2%}", class_="ev-pos" if r.avg_clv > 0 else "ev-neg")),
                        tags.td(f"{r.beat:.0%}")) for k, r in t.iterrows()]
        return tags.div(tags.table(tags.thead(tags.tr(*[tags.th(x) for x in (first, "Prices", "Avg CLV", "Beat the close")])),
                                   tags.tbody(*rows), class_="bk"), class_="sect")
    return tags.div(head, intro, stat,
                    tags.div(tags.div(tags.h6("By edge against the sharp books"), tbl("edge_mkt", "Edge at the time"),
                                      class_="hcard"),
                             tags.div(tags.h6("By market"), tbl("market", "Market"), class_="hcard"),
                             tags.div(tags.h6("By time before kickoff"), tbl("hrs", "Logged"), class_="hcard"),
                             class_="hcharts"),
                    tags.div("A few hundred graded prices per row before trusting a difference; small rows are noise.",
                             class_="note"))
