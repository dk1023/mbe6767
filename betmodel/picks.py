"""Pick tracking: log recommendations, grade them from final scores, measure closing-line value.

CLV (closing-line value) is the number to watch: results swing for hundreds of bets, but a
bettor who consistently gets better prices than the sharp close is a winning bettor long-term.
  1X2 / O/U / moneyline: CLV = your odds x fair closing probability - 1     (percent)
  NFL spread / total    : CLV = points better than the closing line          (points)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import db, odds as O


LEAN_SOURCES = ("lean", "leastbad")   # tracked separately; never mixed into the main pick record
LEAN_UNITS = 0.5


def log(cands: list[dict]) -> list[dict]:
    new = []
    for c in cands:
        if db.add_pick(c):
            new.append(c)
    return new


from .odds_api import BETTABLE  # noqa: E402  (the sportsbooks you can bet at)


def _ex(w, push):
    """Win chance with pushes left out (what a price's break-even is measured against)."""
    if w is None:
        return None
    return float(w) / max(1.0 - float(push or 0.0), 1e-9)


def options(d: dict) -> list[dict]:
    """Every priced bet on a game with its win chance (pushes left out) three ways:
    model = the model's own numbers, mkt = the sharp market (no-vig), fair = the blend used for value.
    ev is the expected profit per 1 staked using the blended (fair) chance."""
    out = []

    def add(book, market, sel, line, odds, fair_raw, push, model, mkt):
        if odds is None or not np.isfinite(odds) or odds <= 1 or fair_raw is None:
            return
        lose = 1 - fair_raw - (push or 0)
        out.append({"book": book, "market": market, "selection": sel, "line": line, "odds": float(odds),
                    "fair": _ex(fair_raw, push), "model": model, "mkt": mkt, "ev": fair_raw * (odds - 1) - lose})

    if d["sport"] == "nfl":
        wm, wk, wf = d.get("win_model"), d.get("win_market"), d.get("win_fair")
        for b, e in (d.get("books") or {}).items():
            s = e.get("spread")
            if s:
                for side, team in (("home", d["home"]), ("away", d["away"])):
                    add(b, "SPREAD", team, s[f"{side}_pt"], s[f"{side}_price"], s[f"{side}_cover"], s.get("push", 0),
                        _ex(s.get(f"{side}_cover_model"), s.get(f"{side}_push_model")),
                        _ex(s.get(f"{side}_cover_mkt"), s.get(f"{side}_push_mkt")))
            t = e.get("total")
            if t:
                for side in ("over", "under"):
                    add(b, "TOTAL", side.upper(), t["pt"], t[f"{side}_price"], t[side], t.get("push", 0),
                        _ex(t.get(f"{side}_model"), t.get("push_model")), _ex(t.get(f"{side}_mkt"), t.get("push_mkt")))
            m = e.get("ml")
            if m and wf is not None:
                add(b, "ML", d["home"], None, m["home_price"], wf, 0, wm, wk)
                add(b, "ML", d["away"], None, m["away_price"], 1 - wf, 0,
                    None if wm is None else 1 - wm, None if wk is None else 1 - wk)
    else:
        pf, pm, pk = d.get("p_fair"), d.get("p_model"), d.get("sharp_1x2")
        for b, pr3 in (d.get("books1x2") or {}).items():
            for i, s in enumerate("HDA"):
                if pf:
                    add(b, "1X2", s, None, pr3[i], pf[i], 0, pm[i] if pm else None, pk[i] if pk else None)
        po, pom, pok = d.get("po_fair"), d.get("po_model"), d.get("sharp_ou")
        for b, pr2 in (d.get("booksou") or {}).items():
            if po is not None:
                add(b, "OU25", "OVER", 2.5, pr2[0], po, 0, pom, pok[0] if pok else None)
                add(b, "OU25", "UNDER", 2.5, pr2[1], 1 - po, 0, None if pom is None else 1 - pom,
                    pok[1] if pok else None)
    return out


def bet_views(d: dict, bet: dict) -> dict | None:
    """The options() entry matching a bet (same market / side / line, same book if listed)."""
    same = [o for o in options(d) if o["market"] == bet["market"] and str(o["selection"]) == str(bet["selection"])
            and (bet.get("line") is None or o["line"] is None or abs(float(o["line"]) - float(bet["line"])) < 1e-6)]
    exact = [o for o in same if o["book"] == bet.get("book")]
    pool = exact or same
    return max(pool, key=lambda o: o["odds"]) if pool else None


def likely_bet(d: dict) -> dict | None:
    """The bet the MODEL ITSELF thinks is most likely to win (its own numbers; ties -> better price), at the
    best DraftKings / FanDuel price (other books only if neither has the game). Used as the 'least-bad' bet
    when no price has value. edge is still measured with the blended chance, the honest expected value."""
    pool = [o for o in options(d) if o["book"] in BETTABLE]  # only prices you can actually bet
    if not pool:
        return None
    o = max(pool, key=lambda o: (round(o["model"] if o["model"] is not None else o["fair"], 4), o["odds"]))
    return {"market": o["market"], "selection": o["selection"], "line": o["line"], "odds": o["odds"],
            "book": o["book"], "model_prob": float(o["model"] if o["model"] is not None else o["fair"]),
            "edge": float(o["ev"])}


def grade_simple(market: str, side: str, line, hs: int, as_: int) -> str:
    """won / lost / push from a final score. side: H / A / D / OVER / UNDER (the phone page grades the same way)."""
    if market == "1X2":
        res = "H" if hs > as_ else "A" if as_ > hs else "D"
        return "won" if side == res else "lost"
    if market in ("TOTAL", "OU25"):
        v = (hs + as_ - float(line)) if side == "OVER" else (float(line) - (hs + as_))
    elif market == "SPREAD":
        v = (hs - as_ if side == "H" else as_ - hs) + float(line)
    elif market == "ML":
        v = 0 if hs == as_ else (1 if (hs > as_) == (side == "H") else -1)
    else:
        return "push"
    return "won" if v > 0 else "lost" if v < 0 else "push"


def game_bet(d: dict) -> tuple[str, dict] | None:
    """The one bet shown and tracked for a game: ('auto', recommended) / ('lean', best value under the
    minimum) / ('leastbad', most likely outcome when no price has value)."""
    b = d.get("best")
    if d.get("pick") and b:
        return "auto", b
    if b and b.get("odds") and np.isfinite(b["odds"]) and b["edge"] > 0:
        return "lean", b
    lb = likely_bet(d)
    return ("leastbad", lb) if lb else None


def main_picks(d: pd.DataFrame) -> pd.DataFrame:
    """Recommended bets and bets you logged yourself (leans excluded)."""
    return d[~d["source"].isin(LEAN_SOURCES)] if len(d) else d


def lean_picks(d: pd.DataFrame) -> pd.DataFrame:
    return d[d["source"].isin(LEAN_SOURCES)] if len(d) else d


def _started(d: dict) -> bool:
    if d.get("final") or d.get("started") or (d.get("espn") or {}).get("state") in ("in", "post"):
        return True
    ko = d.get("kickoff")
    if ko is not None and pd.notna(ko):
        t = pd.Timestamp(ko)
        t = t.tz_convert(None) if t.tzinfo is not None else t
        return t <= pd.Timestamp.now(tz="UTC").tz_localize(None)
    return False


def log_leans(games: list[dict], cfg: dict) -> int:
    """Track the model's best bet on every game that has no recommended bet, at a notional 0.5 unit:
    'lean' when the edge is positive but under the minimum, 'leastbad' when every price is -EV.
    One row per game: each run before kickoff replaces it with the latest best bet and price, so the
    record reflects the last price the model saw before the game. A game that becomes a recommended
    bet drops its lean."""
    unit = cfg.get("unit", 10)
    n = 0
    with db.connect() as con:
        for d in games:
            if _started(d):
                continue
            gd = pd.Timestamp(d["date"]).strftime("%Y-%m-%d")
            con.execute(f"DELETE FROM picks WHERE status='open' AND sport=? AND game_date=? AND home=? AND away=? "
                        f"AND source IN ({','.join('?' * len(LEAN_SOURCES))})",
                        (d["sport"], gd, d["home"], d["away"], *LEAN_SOURCES))
            gb = game_bet(d)
            if gb is None or gb[0] == "auto":
                continue
            src, b = gb
            vals = {"sport": d["sport"], "comp": d["comp"], "game_date": gd, "home": d["home"], "away": d["away"],
                    "market": b["market"], "selection": b["selection"], "line": b.get("line"), "odds": float(b["odds"]),
                    "book": b.get("book"), "model_prob": float(b["model_prob"]),
                    "fair_odds": 1 / max(float(b["model_prob"]), 1e-9), "edge": float(b["edge"]),
                    "stake": round(LEAN_UNITS * unit, 2), "source": src}
            con.execute(f"INSERT OR REPLACE INTO picks (created_at, {', '.join(db.PICK_COLS)}) "
                        f"VALUES (?, {', '.join('?' * len(db.PICK_COLS))})", [db.now(), *[vals[c] for c in db.PICK_COLS]])
            n += 1
    return n


def backfill_leans(games: list[dict], ran_at, cfg: dict) -> int:
    """Add the best bet for games in a saved run that had NOT started when the model ran but have no
    tracked bet yet (e.g. games that finished before lean tracking existed). Uses the prices the model saw
    before kickoff, so nothing is picked with hindsight."""
    import datetime as _dt
    unit = cfg.get("unit", 10)
    ran_utc = pd.Timestamp(ran_at)
    if ran_utc.tzinfo is None:
        ran_utc = ran_utc.tz_localize(_dt.datetime.now().astimezone().tzinfo)
    ran_utc = ran_utc.tz_convert("UTC").tz_localize(None)
    existing = db.picks()
    have = set(zip(existing["sport"], existing["game_date"], existing["home"], existing["away"])) if len(existing) else set()
    n = 0
    with db.connect() as con:
        for d in games:
            ko = d.get("kickoff")
            if ko is None or pd.isna(ko):
                continue
            ko = pd.Timestamp(ko)
            ko = ko.tz_convert("UTC").tz_localize(None) if ko.tzinfo is not None else ko
            if ko <= ran_utc:
                continue  # already under way when the model priced it: no honest pre-game bet
            gd = pd.Timestamp(d["date"]).strftime("%Y-%m-%d")
            gb = game_bet(d)
            if (d["sport"], gd, d["home"], d["away"]) in have or gb is None or gb[0] == "auto":
                continue
            src, b = gb
            vals = {"sport": d["sport"], "comp": d["comp"], "game_date": gd, "home": d["home"], "away": d["away"],
                    "market": b["market"], "selection": b["selection"], "line": b.get("line"), "odds": float(b["odds"]),
                    "book": b.get("book"), "model_prob": float(b["model_prob"]),
                    "fair_odds": 1 / max(float(b["model_prob"]), 1e-9), "edge": float(b["edge"]),
                    "stake": round(LEAN_UNITS * unit, 2), "source": src}
            con.execute(f"INSERT OR IGNORE INTO picks (created_at, {', '.join(db.PICK_COLS)}) "
                        f"VALUES (?, {', '.join('?' * len(db.PICK_COLS))})", [db.now(), *[vals[c] for c in db.PICK_COLS]])
            n += 1
    return n


def summary(d: pd.DataFrame) -> dict:
    """Record / profit / ROI / CLV for a set of picks."""
    s = d[d["status"].isin(["won", "lost", "push"])] if len(d) else d
    out = {"n": len(d), "open": int((d["status"] == "open").sum()) if len(d) else 0, "settled": len(s)}
    if len(s):
        c = s.loc[s["market"].isin(["1X2", "OU25", "ML"]), "clv"].dropna()
        pts = s.loc[s["market"].isin(["SPREAD", "TOTAL"]), "clv"].dropna()
        out.update(record=f"{(s.status == 'won').sum()}-{(s.status == 'lost').sum()}-{(s.status == 'push').sum()}",
                   pnl=float(s["pnl"].sum()), staked=float(s["stake"].sum()),
                   roi=float(s["pnl"].sum() / s["stake"].sum()) if s["stake"].sum() else None,
                   clv=" / ".join(([f"{c.mean():+.1%}"] if len(c) else []) + ([f"{pts.mean():+.1f} pts"] if len(pts) else []))
                   or "-")
    return out


def _pnl(status, stake, odds):
    return {"won": stake * (odds - 1), "lost": -stake, "push": 0.0, "void": 0.0}[status]


def _espn_soccer_result(p, espn_games):
    """Final score from ESPN (available minutes after full time, before football-data updates)."""
    from .data_soccer import norm_name
    if not espn_games:
        return None
    for e in espn_games:
        if e["comp"] == "NFL" or e.get("state") != "post":
            continue
        if abs(pd.Timestamp(e["date_utc"]).normalize() - pd.Timestamp(p.game_date)) > pd.Timedelta(days=1):
            continue
        hn, an = norm_name(e["home_name"]), norm_name(e["away_name"])
        if (norm_name(p.home) in hn or hn in norm_name(p.home)) and (norm_name(p.away) in an or an in norm_name(p.away)):
            try:
                return int(e["home_score"]), int(e["away_score"])
            except (TypeError, ValueError):
                return None
    return None


def _backfill_clv(soccer_df, nfl_games):
    """Picks graded early from ESPN finals get their closing-line value once the free closing-odds
    sources (football-data / nflverse) have the close - on the next full run."""
    d = db.picks()
    todo = d[d["status"].isin(["won", "lost", "push"]) & d["clv"].isna()]
    for p in todo.itertuples():
        res = None
        if p.sport == "soccer" and soccer_df is not None:
            res = _settle_soccer(p, soccer_df)
        elif p.sport == "nfl" and nfl_games is not None:
            res = _settle_nfl(p, nfl_games)
        if res and res[4] is not None:
            db.update_pick(p.id, close_odds=res[2], close_line=res[3], clv=res[4])


def _final_from_index(p, espn_index):
    """(home_score, away_score) when ESPN shows the game as final."""
    if not espn_index:
        return None
    e = espn_index.get((p.sport, p.home, p.away))
    if not e or e.get("state") != "post":
        return None
    try:
        return int(e["home_score"]), int(e["away_score"])
    except (TypeError, ValueError):
        return None


def _grade_from_score(p, hs, as_):
    """Grade any pick from a final score alone (no closing-line value yet)."""
    if p.sport == "soccer":
        if p.market == "1X2":
            out = "H" if hs > as_ else "D" if hs == as_ else "A"
            return ("won" if p.selection == out else "lost", f"{hs}-{as_}", None, None, None)
        if p.market == "OU25":
            return ("won" if (p.selection == "OVER") == (hs + as_ > 2.5) else "lost", f"{hs}-{as_}", None, None, None)
        return None
    margin, total = hs - as_, hs + as_
    score = f"{p.away} {as_} - {p.home} {hs}"
    home_side = p.selection == p.home
    if p.market == "SPREAD":
        s = (margin if home_side else -margin) + p.line
    elif p.market == "TOTAL":
        s = (total - p.line) if p.selection == "OVER" else (p.line - total)
    elif p.market == "ML":
        s = 0 if margin == 0 else (margin if home_side else -margin)
    else:
        return None
    return ("won" if s > 0 else "lost" if s < 0 else "push", score, None, None, None)


def settle(soccer_df: pd.DataFrame | None = None, nfl_games: pd.DataFrame | None = None,
           espn_games: list | None = None, espn_index: dict | None = None) -> list[str]:
    """Grade open picks. Uses football-data / nflverse when given (with closing-line value), otherwise
    ESPN finals - so the free score update can grade without any paid data."""
    if soccer_df is not None or nfl_games is not None:
        _backfill_clv(soccer_df, nfl_games)
    open_ = db.picks("open")
    msgs = []
    today = pd.Timestamp.today().normalize()
    for p in open_.itertuples():
        gd = pd.Timestamp(p.game_date)
        if gd > today:
            continue
        res = None
        if p.sport == "soccer" and soccer_df is not None:
            res = _settle_soccer(p, soccer_df)
        elif p.sport == "nfl" and nfl_games is not None:
            res = _settle_nfl(p, nfl_games)
        if res is None:
            sc = _final_from_index(p, espn_index)
            if sc is None and p.sport == "soccer":
                sc = _espn_soccer_result(p, espn_games)
            if sc:
                res = _grade_from_score(p, *sc)
        if res is None:
            if today - gd > pd.Timedelta(days=10) and (soccer_df is not None or nfl_games is not None):
                db.update_pick(p.id, status="void", pnl=0.0, settled_at=db.now(), score="no result found")
                msgs.append(f"void   #{p.id} {p.home} v {p.away} (no result found)")
            continue
        status, score, close_odds, close_line, clv = res
        pnl = _pnl(status, p.stake, p.odds)
        db.update_pick(p.id, status=status, score=score, close_odds=close_odds, close_line=close_line, clv=clv,
                       pnl=round(pnl, 2), settled_at=db.now())
        msgs.append(f"{status:<6} #{p.id} {p.home} v {p.away} {p.market} {p.selection} @ {p.odds:.2f}  "
                    f"{score}  P/L {pnl:+.2f}" + (f"  CLV {clv:+.1%}" if clv is not None and p.market in ("1X2", "OU25", "ML")
                                                   else f"  CLV {clv:+.1f} pts" if clv is not None else ""))
    return msgs


def _settle_soccer(p, df):
    gd = pd.Timestamp(p.game_date)
    r = df[(df["home"] == p.home) & (df["away"] == p.away) & (abs(df["date"] - gd) <= pd.Timedelta(days=2))
           & df["hg"].notna()]
    if r.empty:
        return None
    r = r.iloc[-1]
    hg, ag = int(r["hg"]), int(r["ag"])
    score = f"{hg}-{ag}"
    clv = close = None
    if p.market == "1X2":
        out = "H" if hg > ag else "D" if hg == ag else "A"
        status = "won" if p.selection == out else "lost"
        c = [r.get("cH"), r.get("cD"), r.get("cA")]
        if np.all(np.isfinite(np.asarray(c, float))):
            fair = O.devig(c, "shin")["HDA".index(p.selection)]
            close, clv = float(c["HDA".index(p.selection)]), float(p.odds * fair - 1)
    elif p.market == "OU25":
        over = hg + ag > 2.5
        status = "won" if (p.selection == "OVER") == over else "lost"
        c = [r.get("cO25"), r.get("cU25")]
        if np.all(np.isfinite(np.asarray(c, float))):
            i = 0 if p.selection == "OVER" else 1
            close, clv = float(c[i]), float(p.odds * O.devig(c, "shin")[i] - 1)
    else:
        return None
    return status, score, close, None, clv


def _settle_nfl(p, g):
    gd = pd.Timestamp(p.game_date)
    r = g[(g["home_team"] == p.home) & (g["away_team"] == p.away) & (abs(g["date"] - gd) <= pd.Timedelta(days=3))]
    if r.empty or pd.isna(r.iloc[-1]["result"]):
        return None
    r = r.iloc[-1]
    margin, total = float(r["result"]), float(r["total"])
    score = f"{p.away} {int(r['away_score'])} - {p.home} {int(r['home_score'])}"
    home_side = p.selection == p.home
    close_line = clv = close_odds = None
    if p.market == "SPREAD":
        s = (margin if home_side else -margin) + p.line
        status = "won" if s > 0 else "lost" if s < 0 else "push"
        if pd.notna(r["spread_line"]):
            close_line = -r["spread_line"] if home_side else r["spread_line"]
            clv = float(p.line - close_line)
    elif p.market == "TOTAL":
        s = (total - p.line) if p.selection == "OVER" else (p.line - total)
        status = "won" if s > 0 else "lost" if s < 0 else "push"
        if pd.notna(r["total_line"]):
            close_line = float(r["total_line"])
            clv = float((close_line - p.line) if p.selection == "OVER" else (p.line - close_line))
    elif p.market == "ML":
        status = "push" if margin == 0 else "won" if (margin > 0) == home_side else "lost"
        if pd.notna(r["home_moneyline"]) and pd.notna(r["away_moneyline"]):
            c = [O.parse_odds(r["home_moneyline"]), O.parse_odds(r["away_moneyline"])]
            i = 0 if home_side else 1
            close_odds, clv = c[i], float(p.odds * O.devig(c, "shin")[i] - 1)
    else:
        return None
    return status, score, close_odds, close_line, clv


def report(last: int = 15) -> str:
    allp = db.picks()
    d = main_picks(allp)
    lines_lean = []
    for src, label in (("lean", "Leans (positive edge below the minimum)"), ("leastbad", "Least-bad (all prices -EV)")):
        sm = summary(allp[allp["source"] == src]) if len(allp) else {"n": 0}
        if sm["n"]:
            lines_lean.append(f"{label}: {sm['n']} logged, {sm['open']} open" +
                              (f", {sm['record']} W-L-P, P/L {sm['pnl']:+.2f}, ROI {sm['roi']:+.1%}, CLV {sm['clv']}"
                               if sm.get("record") else ""))
    if d.empty:
        return "\n".join(["No recommended picks logged yet."] + lines_lean)
    s = d[d["status"].isin(["won", "lost", "push"])]
    L = [f"Picks logged: {len(d)}  |  open {int((d.status == 'open').sum())}  |  settled {len(s)}"]
    if len(s):
        def summ(g):
            staked = g["stake"].sum()
            pct_mk = g["market"].isin(["1X2", "OU25", "ML"])
            return pd.Series({
                "bets": len(g), "W-L-P": f"{(g.status == 'won').sum()}-{(g.status == 'lost').sum()}-{(g.status == 'push').sum()}",
                "staked": round(staked, 2), "profit": round(g["pnl"].sum(), 2),
                "ROI": f"{g['pnl'].sum() / staked:+.1%}" if staked else "n/a",
                "avg CLV %": f"{g.loc[pct_mk, 'clv'].mean():+.1%}" if g.loc[pct_mk, "clv"].notna().any() else "",
                "avg CLV pts": f"{g.loc[~pct_mk, 'clv'].mean():+.2f}" if g.loc[~pct_mk, "clv"].notna().any() else "",
                "beat close": f"{(g['clv'].dropna() > 0).mean():.0%}" if g["clv"].notna().any() else ""})
        s = s.assign(prices=np.where(s["book"].fillna("").str.contains("draftkings|fanduel"), "DK/FD", s["book"].fillna("")))
        parts = {k: summ(g) for k, g in s.groupby(["sport", "market", "prices"])}
        parts[("ALL", "", "")] = summ(s)
        tbl = pd.DataFrame(parts).T
        tbl.index.names = ["sport", "market", "prices"]
        L += ["", tbl.to_string()]
        L.append("\nCLV > 0 over time is the sign of a real edge; win/loss on a few dozen bets is mostly luck.")
    recent = d.sort_values("id").tail(last)
    cols = ["id", "game_date", "sport", "home", "away", "market", "selection", "line", "odds", "book", "edge",
            "stake", "status", "score", "pnl", "clv"]
    rec = recent[cols].copy()
    rec["edge"] = rec["edge"].map(lambda x: f"{x:+.1%}" if pd.notna(x) else "")
    L += ["", f"Last {len(rec)} picks:", rec.to_string(index=False)]
    if lines_lean:
        L += ["", "Tracked separately (not in the record above):"] + ["  " + x for x in lines_lean]
    return "\n".join(L)
