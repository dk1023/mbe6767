"""The Pick tracker: only the bets that cleared your edge (recommended, plus any you log yourself).

Shown the same way in the PC app and on the phone page: the record, profit and ROI, whether the prices beat
Kalshi's closing price (the real test of an edge), the bets still open, and every settled one. The model's
leans and least-bad bets aren't here (they aren't expected to profit); they're on the Results page.
"""
from __future__ import annotations

import pandas as pd

from . import db, odds as O
from .results import COMP_NAMES

BOOK_NAMES = {"draftkings": "DraftKings", "fanduel": "FanDuel", "kalshi": "Kalshi", "pinnacle": "Pinnacle",
              "manual": "logged by you"}


def _amer(x) -> str:
    return O.to_american(x) if x is not None and pd.notna(x) and x > 1 else "-"


def _match(x: dict) -> str:
    return f"{x['away']} @ {x['home']}" if x["sport"] == "nfl" else f"{x['home']} v {x['away']}"


def _money(x: float) -> str:
    return f"{'+' if x >= 0 else '-'}${abs(x):,.2f}"


def _clv_src(x: dict):
    """(closing-line value, 'Kalshi' | 'close') for one bet: vs Kalshi's last pre-kickoff price when graded, else
    the free closing line (prices only: a spread's CLV in points isn't comparable). (None, '') when not known yet."""
    k = x.get("kclv")
    if k is not None and pd.notna(k):
        return float(k), "Kalshi"
    c = x.get("clv")
    if c is not None and pd.notna(c) and x["market"] in ("1X2", "OU25", "ML"):
        return float(c), "close"
    return None, ""


def _clv(x: dict):
    return _clv_src(x)[0]


def stats(d: pd.DataFrame) -> dict:
    s = d[d["status"].isin(["won", "lost", "push"])] if len(d) else d
    o = d[d["status"] == "open"] if len(d) else d
    clv = [c for c in (_clv(x) for x in s.to_dict("records")) if c is not None] if len(s) else []
    staked = float(s["stake"].sum()) if len(s) else 0.0
    pnl = float(s["pnl"].fillna(0).sum()) if len(s) else 0.0
    return {"w": int((s["status"] == "won").sum()) if len(s) else 0,
            "l": int((s["status"] == "lost").sum()) if len(s) else 0,
            "p": int((s["status"] == "push").sum()) if len(s) else 0, "n": len(s),
            "pnl": pnl, "staked": staked, "roi": pnl / staked if staked else None,
            "clv": sum(clv) / len(clv) if clv else None, "n_clv": len(clv), "beat": sum(1 for c in clv if c > 0),
            "open": len(o), "open_stake": float(o["stake"].sum()) if len(o) else 0.0}


def _game_index(games) -> dict:
    out = {}
    for d in games or []:
        out[(d["sport"], d["home"], d["away"])] = d
    return out


def _state(x: dict, gi: dict) -> str:
    d = gi.get((x["sport"], x["home"], x["away"]))
    e = (d or {}).get("espn") or {}
    if e.get("state") == "in":
        return "LIVE " + (f"{e.get('away_score')}-{e.get('home_score')}" if x["sport"] == "nfl"
                          else f"{e.get('home_score')}-{e.get('away_score')}")
    if e.get("state") == "post":
        return "Final, grading at the next update"
    return pd.Timestamp(x["game_date"]).strftime("%a %d %b")


def view(unit: float = 10.0, games=None, min_edge: dict | None = None):
    """The tracker as htmltools tags (works in the Shiny app and on the static phone page)."""
    from htmltools import HTML, tags
    from . import engine, picks as PK
    allp = db.picks()
    d = PK.main_picks(allp) if len(allp) else allp
    st = stats(d)
    me = min_edge or {}
    edge_txt = " / ".join(f"{k.upper() if k == 'nfl' else k} {v:.0%}" for k, v in me.items()) if me else "your minimum"

    def kpi(k, v, sub, tone=None):
        return tags.div(tags.div(k, class_="k"), tags.div(v, class_="v"), tags.div(sub, class_="s"),
                        class_=f"rkpi {tone}" if tone else "rkpi")

    rec = f"{st['w']}-{st['l']}" + (f"-{st['p']}" if st["p"] else "") if st["n"] else "-"
    tiles = tags.div(
        kpi("Record", rec, f"{st['n']} settled" + (f" · {st['w'] / (st['w'] + st['l']):.0%} won" if st['w'] + st['l'] else "")),
        kpi("Profit", _money(st["pnl"]) if st["n"] else "-",
            f"{st['pnl'] / unit:+.1f} units of ${unit:g}" if st["n"] else "nothing settled yet",
            tone=("pos" if st["pnl"] >= 0 else "neg") if st["n"] else None),
        kpi("ROI", f"{st['roi']:+.1%}" if st["roi"] is not None else "-",
            f"on ${st['staked']:,.2f} staked" if st["n"] else ""),
        kpi("Beat the closing price", f"{st['beat']} of {st['n_clv']}" if st["n_clv"] else "-",
            (f"average CLV {st['clv']:+.1%} against the closing price" if st["n_clv"] else
             "shown after kickoff: did your price beat Kalshi's last one?"),
            tone=("pos" if st["clv"] >= 0 else "neg") if st["n_clv"] else None),
        kpi("Open now", str(st["open"]), f"${st['open_stake']:,.2f} riding" if st["open"] else "none right now"),
        class_="rkpis")
    intro = tags.div(
        "Only bets that cleared your edge are tracked here: a DraftKings price that beats Kalshi's sharp no-vig price "
        f"by at least {edge_txt}, with the model agreeing, no longshots and a tight Kalshi market. Every run checks "
        "every price (every 2 hours, and every 30 minutes before kickoffs), so new ones appear here and under Bets to "
        "take as soon as they're found. Expect only a few a week: DraftKings' margin is built into its prices.",
        class_="note")

    gi = _game_index(games)
    recs = d.sort_values("id", ascending=False).to_dict("records") if len(d) else []
    open_rows, done_rows = [], []
    def cell(label, *content):  # data-label: on a phone each row becomes a card with labelled lines
        return tags.td(tags.span(*content, class_="cv"), **{"data-label": label})

    for x in recs:
        edge = f"{x['edge']:+.1%}" if pd.notna(x.get("edge")) else "-"
        bet = cell("Bet", tags.b(engine.describe_bet(x)),
                   tags.small(f"{_amer(x['odds'])} at {BOOK_NAMES.get(x['book'], x['book'] or '?')} · edge {edge}"))
        stake = cell("Stake", f"${x['stake']:,.2f}", tags.small(f"{x['stake'] / unit:g}u"))
        game = cell("Game", _match(x), tags.small(COMP_NAMES.get(x["comp"], x["comp"] or "")))
        if x["status"] == "open":
            fair = _amer(1 / x["model_prob"]) if pd.notna(x.get("model_prob")) and x["model_prob"] else "-"
            open_rows.append(tags.tr(cell("When", _state(x, gi)), game, bet, stake,
                                     cell("Take it at", f"{_amer(x['odds'])} or better", tags.small(f"fair price {fair}")),
                                     class_="rrow"))
        else:
            c, src = _clv_src(x)
            res = {"won": "HIT", "lost": "MISS", "push": "PUSH", "void": "VOID"}.get(x["status"], x["status"])
            done_rows.append(tags.tr(
                cell("Date", pd.Timestamp(x["game_date"]).strftime("%a %d %b")), game, bet, stake,
                cell("Result", tags.span(res, class_=f"res {x['status']}"),
                     tags.small(_money(x["pnl"]) if pd.notna(x.get("pnl")) else "")),
                cell("CLV", f"{c:+.1%}" if c is not None else "-",
                     tags.small((("beat " if c > 0 else "worse than ")
                                 + ("Kalshi's close" if src == "Kalshi" else "the close")) if c is not None else "")),
                cell("Final", x.get("score") or "-"),
                class_=f"rrow {x['status']}"))

    def table(head, rows):
        return tags.div(tags.table(tags.thead(tags.tr(*[tags.th(h) for h in head])), tags.tbody(*rows),
                                   class_="bk rtable trk"), class_="sect")

    open_sec = tags.div(
        tags.div(tags.h4("Open bets"), tags.div(tags.span(f"{st['open']} open", class_="big"),
                                                 tags.span(f"${st['open_stake']:,.2f} riding") if st["open"] else None,
                                                 class_="srec"), class_="shead"),
        table(["When", "Game", "Bet", "Stake", "Take it at"], open_rows) if open_rows else
        tags.div("No open bets right now. The next one will show up here and under Bets to take.", class_="note"),
        class_="rsec")
    done_sec = tags.div(
        tags.div(tags.h4("Settled"), tags.div(
            tags.span(rec, class_="big"),
            tags.span(_money(st["pnl"]), class_="pos" if st["pnl"] >= 0 else "neg") if st["n"] else None,
            tags.span(f"ROI {st['roi']:+.1%}") if st["roi"] is not None else None, class_="srec"), class_="shead"),
        table(["Date", "Game", "Bet", "Stake", "Result", "CLV", "Final"], done_rows)
        if done_rows else tags.div("Nothing settled yet.", class_="note"),
        tags.div(HTML("<b>CLV</b> (closing-line value) is what your price was worth against the last price before "
                      "kickoff: Kalshi's, or the books' closing line when Kalshi had none. Wins and losses on a few "
                      "dozen bets are mostly luck; prices that keep beating the close are the sign the edge is real."),
                 class_="note"),
        class_="rsec")
    foot = tags.div("The model's leans and least-bad picks on every other game, and the price log, are on the Results page.",
                    class_="note", style="margin-top:14px")
    return tags.div(intro, tiles, open_sec, done_sec, foot)
