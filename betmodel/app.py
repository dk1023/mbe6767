"""OddSea (package: betmodel) Shiny app - a sports-app style board. Nothing runs until you press "Run model".

Start it with app.bat (or: python -m betmodel app).
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json

import numpy as np
import pandas as pd
from shiny import App, reactive, render, ui

from . import cloud, db, engine, history as H, odds as O, slate, theme as T

LOCAL_TZ = _dt.datetime.now().astimezone().tzinfo
BOOKS = {"draftkings": "DraftKings", "fanduel": "FanDuel", "pinnacle": "Pinnacle (sharp)", "kalshi": "Kalshi (sharp)",
         "betfair_ex_eu": "Betfair Exchange", "Betfair Exchange": "Betfair Exchange", "consensus": "Consensus",
         "PaddyPower": "Paddy Power", "Bet365": "Bet365", "Avg": "Market average"}
BOOK_ORDER = ["draftkings", "kalshi", "fanduel", "pinnacle", "betfair_ex_eu", "Betfair Exchange", "PaddyPower", "Bet365",
              "consensus", "Avg"]
COMP_NAMES = {"NFL": "NFL", "EPL": "Premier League", "LALIGA": "La Liga", "SERIEA": "Serie A",
              "UCL": "Champions League", "UEL": "Europa League"}

CSS = T.CSS  # the stylesheet lives in theme.py (shared with the phone page)


# ---------------------------------------------------------------------------------- helpers
def amer(dec):
    return O.to_american(dec) if dec is not None and np.isfinite(dec) and dec > 1 else "-"


def pct(x, d=0):
    return "-" if x is None or not np.isfinite(x) else f"{100 * x:.{d}f}%"


def ev_span(ev):
    if ev is None or not np.isfinite(ev):
        return ui.span("-")
    return ui.span(f"{ev:+.1%}", class_="ev-pos" if ev > 0 else "ev-neg")


def local(ts):
    if ts is None or (not isinstance(ts, pd.Timestamp) and pd.isna(ts)):
        return None
    t = pd.Timestamp(ts)
    if pd.isna(t):
        return None
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return t.tz_convert(LOCAL_TZ)


def when(d):
    """Kickoff in the viewer's local time, e.g. 'Sun 22 Nov, 1:00 PM'."""
    t = local(d.get("kickoff"))
    if t is None:
        return pd.Timestamp(d["date"]).strftime("%a %d %b")
    return t.strftime("%a %d %b, %I:%M %p").replace(", 0", ", ")


def day_key(d):
    t = local(d.get("kickoff"))
    return (t.date() if t is not None else pd.Timestamp(d["date"]).date())


def team_meta(d, side):
    e = d.get("espn") or {}
    if d["sport"] == "nfl":
        name = e.get(f"{side}_short") or d[side]
        abbr = d[side]
    else:
        name = e.get(f"{side}_display") or d[side]
        abbr = d[side]
    return {"name": name, "abbr": abbr, "logo": e.get(f"{side}_logo"), "rec": e.get(f"{side}_record") or "",
            "score": e.get(f"{side}_score")}


def status(d):
    e = d.get("espn") or {}
    st = e.get("state")
    if st == "in":
        return ui.span(f"LIVE  {e.get('status') or ''}", class_="live gstat")
    if st == "post" or d.get("final"):
        return ui.span("Final", class_="gstat")
    return ui.span(when(d), class_="gstat")


def logo(url, fallback="", lazy=True):
    if not url:  # no logo from ESPN: a neutral badge keeps the names lined up
        return ui.span(str(fallback)[:3].upper(), class_="nologo") if fallback else ui.span()
    return ui.tags.img(src=url, loading="lazy" if lazy else "eager", alt="")


def side_name(d, home_margin):
    """'PHI by 3.5' from a home-margin number."""
    if abs(home_margin) < 0.05:
        return "Pick'em"
    return f"{d['home'] if home_margin > 0 else d['away']} by {abs(home_margin):.1f}"


def ordered_books(books: dict):
    return sorted(books, key=lambda b: BOOK_ORDER.index(b) if b in BOOK_ORDER else 99)


def click(js_input, value):
    return f"Shiny.setInputValue('{js_input}', {json.dumps(value)}, {{priority: 'event'}})"


def status_bar(r):
    """The chips under the header: when the model ran, when scores last updated, games, bets, credits."""
    if r is None:
        return ui.div(ui.span("Nothing has run yet. Press ", ui.tags.b("Run model"), " - nothing runs until you do.",
                              class_="schip"), class_="statusline")
    ran = pd.Timestamp(r.ran_at)
    old = pd.Timestamp.now() - ran > pd.Timedelta(hours=12)
    chips = [ui.span(ui.span(class_="dot old" if old else "dot"), "Model run ",
                     ui.tags.b(ran.strftime("%a %d %b, %I:%M %p").replace(", 0", ", ")), class_="schip",
                     title="Prices and injuries are as of this run" + (" (over 12 hours ago)" if old else ""))]
    sc = getattr(r, "scores_at", None)
    if sc is not None:
        chips.append(ui.span("Scores ", ui.tags.b(pd.Timestamp(sc).strftime("%a %I:%M %p").replace(" 0", " ")),
                             " · free", class_="schip"))
    chips.append(ui.span(ui.tags.b(str(len(r.games))), " games", class_="schip"))
    n = len(r.recommended)
    chips.append(ui.span(ui.tags.b(str(n)), " bet recommended" if n == 1 else " bets recommended",
                         class_="schip hl" if n else "schip"))
    if r.credits_left is not None:
        chips.append(ui.span(ui.tags.b(str(r.credits_left)), " Odds API credits left", class_="schip"))
    return ui.div(*chips, class_="statusline")


# ---------------------------------------------------------------------------------- game list
def game_card(i, d, active, onclick=None):
    """onclick: override for the static snapshot page (default talks to the live Shiny app)."""
    h, a = team_meta(d, "home"), team_meta(d, "away")
    st = (d.get("espn") or {}).get("state")
    show_score = st in ("in", "post")
    top = [ui.span(ui.span(COMP_NAMES.get(d["comp"], d["comp"]), class_="compchip"), " ", status(d))]
    if d.get("pick"):
        top.append(ui.span(f"VALUE {d['best']['edge']:+.1%}", class_="vbadge"))
    loser = None  # dim the beaten side once it's final
    if st == "post":
        try:
            hs_, as_ = int(h["score"]), int(a["score"])
            loser = "home" if hs_ < as_ else "away" if as_ < hs_ else None
        except (TypeError, ValueError):
            pass

    def row(t, side):
        return ui.div(logo(t["logo"], t["abbr"]), ui.span(t["name"], class_="nm"), ui.span(t["rec"], class_="rec"),
                      ui.span(t["score"] if show_score and t["score"] is not None else "", class_="sc",
                              **{"data-side": side}), class_="trow lose" if side == loser else "trow")
    pred = ui.span(ui.tags.b("Prediction: "), pred_text(d), ui.span(class_="pmark"))
    sl = sim_lines(d)
    sim_txt = f"Sim {sl['spread']}" if sl else None
    if d["sport"] == "nfl":
        mk = d.get("mu_market")
        books = (f"DK {fav_line(d, sl['dk']['home_pt'])}" if sl and sl.get("dk") else
                 f"Books {side_name(d, mk)}" if mk is not None else "Books -")
    else:
        ps = d.get("sharp_1x2")
        books = f"Books {pct(ps[0])} / {pct(ps[1])} / {pct(ps[2])}" if ps else "Books -"
    line = ui.div(pred, ui.span(" · ".join(x for x in (sim_txt, books) if x)), class_="gline")
    (first, fs), (second, ss) = ((a, "away"), (h, "home")) if d["sport"] == "nfl" else ((h, "home"), (a, "away"))
    return ui.div(ui.div(*top, class_="gtop"), row(first, fs), row(second, ss), line, best_line(d),
                  class_="gcard active" if active else "gcard",
                  onclick=onclick or (f"bmActive({i}); " + click("pick_game", str(i)) + OPEN), id=f"c{i}",
                  **{"data-comp": d["comp"]})


# phones: the game page opens full-screen over the list (desktop shows it beside the list)
OPEN = "; document.body.classList.add('show-detail'); var gd = document.getElementById('game_detail'); if (gd) gd.scrollTop = 0;"
BACK = ui.div("‹ All games", class_="backlink mobile-only", onclick="document.body.classList.remove('show-detail')")


# ---------------------------------------------------------------------------------- detail pages
def detail_header(d):
    h, a = team_meta(d, "home"), team_meta(d, "away")
    e = d.get("espn") or {}
    show_score = e.get("state") in ("in", "post")
    left, right = (a, h) if d["sport"] == "nfl" else (h, a)
    mid = ui.div(ui.div(f"{left['score']} - {right['score']}" if show_score else ("@" if d["sport"] == "nfl" else "v"),
                        class_="score", **{"data-left": "away" if d["sport"] == "nfl" else "home"}),
                 ui.div(status(d), class_="when"),
                 ui.div(COMP_NAMES.get(d["comp"], d["comp"]), class_="when"), class_="hmid")

    def tm(t):
        return ui.div(logo(t["logo"], t["abbr"], lazy=False), ui.div(t["name"], class_="big"),
                      ui.div(t["rec"], class_="rec"), class_="hteam")
    return ui.div(tm(left), mid, tm(right), class_="dhead")


def prob_bar(parts):
    """parts: [(label, prob, color)]. Narrow segments show just the % (the label wouldn't fit on a phone)."""
    return ui.div(*[ui.div(f"{lab} {pct(p)}" if p >= 0.2 else pct(p) if p >= 0.09 else "",
                           style=f"width:{100 * p:.1f}%;background:{c}", title=f"{lab} {pct(p, 1)}")
                    for lab, p, c in parts], class_="bar")


def fav_line(d, home_line, names=False):
    """A spread from the favourite's side, e.g. 'CIN -6' (home line -6 -> home team; +6 -> away team -6)."""
    if home_line is None:
        return "-"
    if abs(home_line) < 1e-9:
        return "Pick'em"
    side = "home" if home_line < 0 else "away"
    nm = team_meta(d, side)["name"] if names else d[side]
    return f"{nm} {-abs(home_line):g}"


def sim_lines(d):
    """The simulation's own spread and total: {'line' (home), 'spread' text, 'total', 'dk' (DraftKings' spread)
    and 'dk_cover' ((home win, push, home lose) in the simulations at DraftKings' line)}."""
    from . import simulate as SIM
    s = (game_prediction(d) or {}).get("sim")
    if not s:
        return None
    L = SIM.fair_spread(s)
    out = {"line": L, "spread": fav_line(d, L, names=d["sport"] == "soccer"), "total": SIM.fair_total(s), "dk": None,
           "avg_margin": s["avg"][0] - s["avg"][1]}
    sp = ((d.get("books") or {}).get("draftkings") or {}).get("spread") if d["sport"] == "nfl" else None
    if sp and sp.get("home_pt") is not None:
        out["dk"], out["dk_cover"] = sp, SIM.cover(s, sp["home_pt"])
    return out


def spread_lines(d):
    """The simulation's spread and total, and how DraftKings' spread did in the simulations."""
    sl = sim_lines(d)
    if not sl:
        return None
    goals = " goals" if d["sport"] == "soccer" else ""
    rows = [ui.div(ui.tags.b("Simulated spread "), sl["spread"] + (" (goal handicap)" if goals else ""),
                   ui.span(" · "), ui.tags.b("simulated total "), f"{sl['total']:g}{goals}",
                   ui.tags.small("  (the lines where each side wins about half the simulations)", class_="muted"),
                   class_="why")]
    if sl.get("dk"):
        w, p, lo = sl["dk_cover"]
        hp = sl["dk"]["home_pt"]
        rows.append(ui.div(ui.tags.b("At DraftKings' line "), f"({fav_line(d, hp)}): ",
                           f"{d['home']} {hp:+g} covers {w:.0%} · {d['away']} {-hp:+g} covers {lo:.0%}"
                           + (f" · push {p:.0%}" if p >= 0.005 else "") + " of the simulations", class_="why"))
    return ui.div(*rows)


def sim_section(d):
    """The simulated games behind the prediction: how often each side won, and the most common final scores."""
    s = (game_prediction(d) or {}).get("sim")
    if not s:
        return None
    n, H, A = s["n"], d["home"], d["away"]
    if d["sport"] == "nfl":
        parts = [(A, s["away_wins"], "var(--c-away)"), (H, s["home_wins"], "var(--c-home)")]
        counts = f"{A} won {s['away_wins']:,} · {H} won {s['home_wins']:,}" + (f" · {s['ties']:,} ties" if s["ties"] else "")
        lab = lambda h, a: f"Tie {h}-{a}" if h == a else f"{H if h > a else A} {max(h, a)}-{min(h, a)}"
    else:
        parts = [(H, s["home_wins"], "var(--c-home)"), ("Draw", s["ties"], "var(--c-draw)"), (A, s["away_wins"], "var(--c-away)")]
        counts = f"{H} won {s['home_wins']:,} · {s['ties']:,} draws · {A} won {s['away_wins']:,}"
        lab = lambda h, a: f"{h}-{a}"
    chips = [ui.span(lab(h, a), ui.tags.small(f" {c:,}", class_="muted")) for h, a, c in s["top"][:6]]
    return ui.div(ui.h6(f"Simulated {n:,} times"),
                  prob_bar([(x, c / n, col) for x, c, col in parts]),
                  ui.div(counts, class_="barlabel"),
                  spread_lines(d),
                  ui.div(f"Most common exact scores (times out of {n:,})", class_="barlabel"),
                  ui.div(*chips, class_="scores"),
                  ui.div(("The prediction is the outcome that came up most often. Its score is the most common one near "
                          f"each team's average of {s['avg'][1]:.0f}-{s['avg'][0]:.0f} ({A}-{H}): exact NFL scores are "
                          "spread thin, and 3-point games like 20-17 top the list for almost any matchup. "
                          if d["sport"] == "nfl" else
                          "The prediction is the outcome that came up most often, with its most common score. ") +
                         ("The games are centred on the model blended with the market (spreads 10% model / 90% "
                          "market, totals 15% / 85%), the same numbers the prices use; that mix matched the books' "
                          "closing line on 2,388 past games, where the model alone missed by 0.2 points more. Win "
                          "chances follow the model's margin distribution; for favourites that runs a point or a few "
                          "below the moneyline win probability above."
                          if d["sport"] == "nfl" else
                          "The results are matched to the model blended with the market (95% market when a sharp "
                          "price exists), the same numbers the prices use; that mix called 54.4% of 2,614 past "
                          "matches right, the model alone 53.3%."),
                         class_="note"),
                  class_="sect")


KIND_LABEL = {"rec": "RECOMMENDED", "lean": "LEAN", "neg": "LEAST-BAD"}


def game_prediction(d, blend: bool = True):
    """The call after simulating the game 100,000 times: the outcome that came up most often, and its most common
    final score (soccer: home win / draw / away win and scoreline). blend=True (the headline): the model blended
    with the market, the mix the prices use, which matched the books' closing line on past games and beat the
    model alone; blend=False: the model alone. Same numbers and seed as the Results page."""
    from . import results as R
    names = {d["home"]: team_meta(d, "home")["name"], d["away"]: team_meta(d, "away")["name"]}
    key = pd.Timestamp(d["date"]).strftime("%Y-%m-%d")  # same seed as the Results page uses for this game
    if d["sport"] == "nfl":
        mu, tau, p = d.get("mu_model"), d.get("tau_model"), d.get("win_model")
        if blend:
            mu, tau, p = R.blend_nfl(mu, tau, p, d.get("mu_market"), d.get("tau_market"), d.get("win_market"))
        return R.predict_nfl(d["home"], d["away"], mu, tau, p, names, key=key)
    p3, target = d.get("p_model"), None
    if blend:
        p3, target = R.blend_soccer(p3, d.get("sharp_1x2"))
    return R.predict_soccer(d["home"], d["away"], p3, d.get("xg"), names, key=key, target=target)


def pred_text(d, with_prob=True):
    p = game_prediction(d)
    if p is None:
        return "-"
    return p["text"] + (f" ({p['prob']:.0%})" if with_prob and p.get("prob") is not None else "")


def pred_value(d):
    """The headline's prediction: the call in big type, its chance in a small pill, the model alone underneath."""
    p = game_prediction(d)
    if p is None:
        return ui.div("-", class_="v")
    m = game_prediction(d, blend=False)
    has_mkt = d.get("mu_market") is not None if d["sport"] == "nfl" else bool(d.get("sharp_1x2"))
    if not has_mkt:
        alone = ui.div("model alone (no sharp market price yet)", class_="vsub")
    else:
        alone = ui.div("model + market · " + (f"model alone: {m['text']}" if m and m["text"] != p["text"]
                                              else "model alone: same"), class_="vsub")
    return ui.div(ui.div(p["text"], ui.span(f"{p['prob']:.0%}", class_="vp") if p.get("prob") is not None else None,
                         class_="v"), alone)


def why_text(d, b, kind) -> str:
    """One plain sentence on why this is the bet: the model alone vs the books vs what the price needs."""
    from . import picks as PK
    v = PK.bet_views(d, b)
    if v is None:
        return ""
    bet = engine.describe_bet({**b, "home": d["home"], "away": d["away"]})
    price, need = amer(b["odds"]), 1 / b["odds"]
    m_ = v["model"] if v["model"] is not None else v["fair"]
    k_ = v["mkt"]
    pts = ""
    if d["sport"] == "nfl" and b["market"] == "SPREAD" and d.get("mu_market") is not None:
        pts = f" (model {side_name(d, d['mu_model'])}, books {side_name(d, d['mu_market'])})"
    elif d["sport"] == "nfl" and b["market"] == "TOTAL" and d.get("tau_market") is not None:
        pts = f" (model total {d['tau_model']:.1f}, books {d['tau_market']:.1f})"
    elif d["sport"] == "soccer" and b["market"] == "OU25":
        pts = f" (model expects {d['xg'][0] + d['xg'][1]:.1f} goals)"
    books_txt = f", books {k_:.0%}" if k_ is not None else ""
    if kind == "neg":
        return (f"The model's most likely outcome: {bet} {m_:.0%} by the model alone{books_txt}{pts}. "
                f"{price} needs {need:.0%}; blended with the sharp market it's {v['fair']:.0%}, so there's no value "
                f"(expected {v['ev']:+.1%} per bet).")
    if k_ is not None and abs(m_ - k_) < 0.015:
        sharp = [o for o in PK.options(d) if o["book"] in ("pinnacle", "betfair_ex_eu") and o["market"] == b["market"]
                 and str(o["selection"]) == str(b["selection"])]
        sh = ""
        if sharp:
            o = max(sharp, key=lambda o: o["odds"])
            sh = f"; the sharp price is {engine.describe_bet({**o, 'home': d['home'], 'away': d['away']})} {amer(o['odds'])}"
        return (f"The model agrees with the books ({m_:.0%} vs {k_:.0%}), so the edge is the price: "
                f"{BOOKS.get(b['book'], b['book'])} has {bet} {price}{sh}. Blended chance {v['fair']:.0%} vs "
                f"{need:.0%} needed: edge {b['edge']:+.1%}.")
    return (f"{bet}: model alone {m_:.0%}{books_txt}{pts}. {price} needs {need:.0%}; blended chance "
            f"{v['fair']:.0%}: edge {b['edge']:+.1%}.")


def best_bet(d, cfg=None):
    """The model's single bet on a game, whether or not it clears the minimum edge.
    kind: rec (clears the minimum, tracked), lean (best value, positive edge below the minimum),
    neg = least-bad (no price has value: the outcome the model thinks is MOST LIKELY)."""
    from . import picks as PK
    if d.get("started"):  # already under way when the model ran: a 'bet' now would be picked with hindsight
        return None
    gb = PK.game_bet(d)
    if gb is None:
        return None
    src, b = gb
    cfg = cfg or slate.user_config()
    kind = {"auto": "rec", "lean": "lean", "leastbad": "neg"}[src]
    units = slate.units_for(b["model_prob"], b["odds"], cfg) if kind != "neg" else 0.0
    if kind == "lean":
        units = 0.5
    return {"kind": kind, "label": KIND_LABEL[kind], "bet": engine.describe_bet({**b, "home": d["home"], "away": d["away"]}),
            "price": amer(b["odds"]), "book": BOOKS.get(b["book"], b["book"]), "edge": b["edge"],
            "prob": b["model_prob"], "fair": amer(1 / b["model_prob"]) if b["model_prob"] > 0 else "-", "units": units,
            "min_edge": cfg["min_edge"]["nfl" if d["sport"] == "nfl" else "soccer"],
            "why": why_text(d, b, kind), "block": b.get("block") or ""}


def block_note(bb) -> str:
    """Why a lean that clears the minimum edge still isn't recommended ('' if it's simply below the minimum)."""
    blk = (bb or {}).get("block") or ""
    return "" if not blk or blk.startswith("below") else f"Not recommended: {blk}."


def best_line(d):
    """One line for the game card: the model's best bet and how strong it is."""
    if d.get("started"):
        return ui.div("No bet: the game had started when the model ran", class_="bline muted")
    bb = best_bet(d)
    if bb is None:
        return ui.div("Best bet: no bookable prices yet", class_="bline")
    extra = f"{bb['prob']:.0%} likely" if bb["kind"] == "neg" else f"edge {bb['edge']:+.1%}"
    return ui.div(ui.span(bb["label"], class_=f"kchip {bb['kind']}"),
                  ui.span(f"{bb['bet']} {bb['price']}", class_="bb"),
                  ui.span(f"{bb['book']} · {extra}", class_="bm"), result_chip(d), class_="bline")


def bet_side(d, b) -> str:
    """H / A / D / OVER / UNDER for grading (NFL bets name the team)."""
    s = str(b["selection"])
    if b["market"] in ("SPREAD", "ML"):
        return "H" if s == d["home"] else "A"
    return s


def grade_now(d) -> str | None:
    """won / lost / push for the game's best bet once ESPN shows it final; 'live' while in progress."""
    from . import picks as PK
    e = d.get("espn") or {}
    if e.get("state") not in ("in", "post") or d.get("started"):
        return None
    if e.get("state") == "in":
        return "live"
    gb = PK.game_bet(d)
    try:
        hs, as_ = int(e["home_score"]), int(e["away_score"])
    except (TypeError, ValueError, KeyError):
        return None
    if gb is None:
        return None
    b = gb[1]
    return PK.grade_simple(b["market"], bet_side(d, b), b.get("line"), hs, as_)


def result_chip(d):
    g = grade_now(d)
    if g in ("won", "lost", "push"):
        return ui.span({"won": "HIT", "lost": "MISS", "push": "PUSH"}[g], class_=f"res {g} bres")
    return ui.span(class_="bres")


def _kick_utc(d):
    ko = d.get("kickoff")
    if ko is None or pd.isna(ko):
        return None
    t = pd.Timestamp(ko)
    return t.tz_convert("UTC").tz_localize(None) if t.tzinfo is not None else t


def score_text(d):
    e = d.get("espn") or {}
    try:
        hs, as_ = int(e["home_score"]), int(e["away_score"])
    except (TypeError, ValueError, KeyError):
        return ""
    return f"{d['away']} {as_} - {d['home']} {hs}" if d["sport"] == "nfl" else f"{hs}-{as_}"


def now_cell(d):
    """Live score / final result for the every-game table (the phone page keeps it up to date itself)."""
    st = (d.get("espn") or {}).get("state")
    if st == "in":
        return ui.tags.td(ui.span("LIVE", class_="live"), ui.tags.small(score_text(d)), class_="nowcell")
    if st == "post":
        g = grade_now(d)
        chip = ui.span({"won": "HIT", "lost": "MISS", "push": "PUSH"}[g], class_=f"res {g}") if g in ("won", "lost", "push") \
            else ui.span("Final")
        return ui.tags.td(chip, ui.tags.small(score_text(d)), class_="nowcell")
    return ui.tags.td("", class_="nowcell")


def all_bets_table(games, onclick_for, unit, ran_at=None):
    """Every game with the model's prediction and best bet: upcoming ones, plus the last day's games that were
    priced before kickoff (with their live score or HIT / MISS). onclick_for(i, d) -> JS for the row."""
    import datetime as _dt
    rows = []
    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    ran = None
    if ran_at is not None:
        ran = pd.Timestamp(ran_at)
        ran = (ran.tz_localize(_dt.datetime.now().astimezone().tzinfo) if ran.tzinfo is None else ran)
        ran = ran.tz_convert("UTC").tz_localize(None)

    def keep(d):
        st = (d.get("espn") or {}).get("state")
        if not d.get("final") and st not in ("in", "post"):
            return True
        k = _kick_utc(d)
        return k is not None and ran is not None and k > ran and now - k < pd.Timedelta(hours=30)
    items = [(i, d) for i, d in enumerate(games) if keep(d)]
    items.sort(key=lambda x: (_kick_utc(x[1]) if _kick_utc(x[1]) is not None else pd.Timestamp(x[1]["date"])))
    for i, d in items:
        bb = best_bet(d)
        match = f"{d['away']} @ {d['home']}" if d["sport"] == "nfl" else f"{d['home']} v {d['away']}"
        if bb is None:
            cells = [ui.tags.td(ui.span("NO PRICES", class_="kchip neg")), ui.tags.td("-"), ui.tags.td(""),
                     ui.tags.td(""), ui.tags.td("")]
        else:
            stake = f"{bb['units']:g}u (${bb['units'] * unit:,.0f})" if bb["units"] else "-"
            cells = [ui.tags.td(ui.span(bb["label"], class_=f"kchip {bb['kind']}")),
                     ui.tags.td(ui.tags.b(f"{bb['bet']} {bb['price']}"), ui.tags.small(bb["book"]),
                                ui.tags.small(block_note(bb), class_="whys") if block_note(bb) else None,
                                ui.tags.small("Why: " + bb["why"], class_="whys") if bb.get("why") else None),
                     ui.tags.td(f"{bb['prob']:.1%}", ui.tags.small(f"fair {bb['fair']}")),
                     ui.tags.td(ev_span(bb["edge"])), ui.tags.td(stake)]
        rows.append(ui.tags.tr(ui.tags.td(when(d), ui.tags.small(COMP_NAMES.get(d["comp"], d["comp"]))),
                               ui.tags.td(match), ui.tags.td(ui.tags.b(pred_text(d, with_prob=False)),
                                                             ui.tags.small(pred_text(d).split("(")[-1].rstrip(")")
                                                                           if "(" in pred_text(d) else ""),
                                                             ui.tags.small(f"sim spread {sim_lines(d)['spread']}")
                                                             if sim_lines(d) else None),
                               *cells, now_cell(d), onclick=onclick_for(i, d), **{"data-gi": str(i)}))
    if not rows:
        return None
    return ui.div(ui.h6("Every game: the model's prediction and best bet"), ui.tags.table(
        ui.tags.thead(ui.tags.tr(*[ui.tags.th(x) for x in ["Kickoff", "Game", "Prediction", "", "Best bet",
                                                           "Chance", "Edge", "Stake", "Now"]])),
        ui.tags.tbody(*rows), class_="bk allbets"),
        ui.div("RECOMMENDED = DraftKings beats the sharp price (Kalshi's, no vig) by your minimum edge on its own "
               "(the model can veto a bet but not create one; no longshots past +300 in the NFL; stale sharp prices "
               "skipped). Logged and tracked. LEAN = the best-value side that didn't pass those checks: small stake at "
               "most. LEAST-BAD = no price has value, so this is "
               "the outcome the model thinks is most likely to happen (often a favourite at a short price). Edges this "
               "small are mostly noise. Leans and least-bad bets are tracked at 0.5u in "
               "their own section of the Pick tracker, apart from the RECOMMENDED record.",
               class_="note"), class_="sect panel")


def reco_box(d, unit):
    b = d.get("best")
    if not d.get("pick") or not b:
        bb = best_bet(d)
        if bb is None:
            return ui.div("This game had already started when the model ran, so there's no bet on it."
                          if d.get("started") else "No bookable prices for this game yet.", class_="note")
        why = ((block_note(bb) or f"Below your {bb['min_edge']:.0%} minimum edge, so it isn't logged as a pick.")
               + f" If you play it, keep it to the minimum 0.5u (${0.5 * unit:,.2f})." if bb["kind"] == "lean" else
               "No price on this game has value, so this is the outcome the model thinks is most likely to happen. "
               "Likely isn't the same as profitable: the books charge for it.")
        return ui.div(ui.div(ui.span(bb["label"], class_=f"kchip {bb['kind']}"), " Model's best bet on this game",
                             class_="barlabel"),
                      ui.div(f"{bb['bet']}  {bb['price']}", class_="b"),
                      ui.div(f"at {bb['book']}  -  model gives it {bb['prob']:.1%} (fair price {bb['fair']})  -  "
                             f"edge {bb['edge']:+.1%}"),
                      ui.div(ui.tags.b("Why: "), bb["why"], class_="why") if bb.get("why") else None,
                      ui.div(why, class_="note"), class_=f"reco lean-box {bb['kind']}")
    units = slate.units_for(b["model_prob"], b["odds"], slate.user_config())
    wtxt = why_text(d, b, "rec")
    return ui.div(ui.div("Recommended bet", class_="barlabel"),
                  ui.div(f"{engine.describe_bet({**b, 'home': d['home'], 'away': d['away']})}  {amer(b['odds'])}", class_="b"),
                  ui.div(f"at {BOOKS.get(b['book'], b['book'])}  -  {units:g}u (${units * unit:,.2f})  -  edge {b['edge']:+.1%}"),
                  ui.div(ui.tags.b("Why: "), wtxt, class_="why") if wtxt else None,
                  ui.div(f"Fair price {amer(1 / b['model_prob'])}; only bet at {amer(b['odds'])} or better.", class_="note"),
                  class_="reco")


def _final_scores(d):
    e = d.get("espn") or {}
    if e.get("state") not in ("post", "in"):
        return None
    try:
        return int(e["home_score"]), int(e["away_score"]), e.get("state")
    except (TypeError, ValueError):
        return None


def verdict_nfl(d):
    fs = _final_scores(d)
    if not fs:
        return None
    hs, as_, st = fs
    act = hs - as_
    mk = d.get("mu_market")
    what = "Final" if st == "post" else "Live"
    txt = f"{what}: {side_name(d, act).replace('.0', '') if act else 'tied'}. Model said {side_name(d, d['mu_model'])}"
    if mk is not None:
        me, be = abs(act - d["mu_model"]), abs(act - mk)
        txt += f", books {side_name(d, mk)}."
        if st == "post":
            txt += (" Model was closer." if me < be - 0.25 else " Books were closer." if be < me - 0.25 else " Both equally close.")
            if abs(d["mu_model"] - mk) >= 1:
                lean_home = d["mu_model"] > mk
                won = (act - mk > 0) == lean_home
                txt += f" Model lean ({d['home'] if lean_home else d['away']} vs the line) {'won' if won else 'lost'}."
    return ui.div(txt, class_="verdict")


def verdict_soccer(d, hn, an):
    fs = _final_scores(d)
    if not fs:
        return None
    hs, as_, st = fs
    out = 0 if hs > as_ else 1 if hs == as_ else 2
    lab = [f"{hn} win", "draw", f"{an} win"][out]
    pm, ps = d["p_model"], d.get("sharp_1x2")
    txt = f"{'Final' if st == 'post' else 'Live'} {hs}-{as_} ({lab})."
    if st == "post":
        txt += f" Model gave this {pct(pm[out])}" + (f", books {pct(ps[out])}." if ps else ".")
        txt += f" Model expected {d['xg'][0]:.1f}-{d['xg'][1]:.1f} on xG."
    return ui.div(txt, class_="verdict")


def weather_line(d):
    w = d.get("weather")
    if not w:
        return None
    roof = w.get("roof") or ""
    if roof in ("dome", "closed"):
        txt = "Indoors (roof closed): no weather effect."
    elif roof == "retractable/unknown":
        txt = "Retractable roof: treated as closed, no wind effect."
    elif w.get("wind") is None:
        txt = "Outdoors: no forecast yet, average conditions assumed."
    else:
        t = f", {w['temp']:.0f}°F" if w.get("temp") is not None else ""
        txt = (f"Weather at kickoff: wind {w['wind']:.0f} mph{t}. "
               f"Effect on the model's total: {w['pts']:+.1f} pts.")
    return ui.div(txt, class_="sub")


def lineup_line(d, hn, an):
    """Soccer: what the player-rating lineup model did to each side's expected goals."""
    info = d.get("lineup_adj")
    if not info:
        return None
    mh, ma = d.get("mult", (1.0, 1.0))
    kind = "confirmed" if info.get("type") == "confirmed" else "predicted"
    fx = lambda m: f"{(m - 1):+.0%}" if abs(m - 1) >= 0.005 else "no change"
    parts = [f"Lineup model ({kind} XIs vs each team's usual side): {hn} xG {fx(mh)}, {an} xG {fx(ma)}."]
    for side, nm in (("home", hn), ("away", an)):
        miss = info.get(side, {}).get("missing") or []
        if miss:
            parts.append(f"{nm} without regulars: " + ", ".join(n for n, _ in miss[:4]) + ".")
    return ui.div(" ".join(parts), class_="sub")


def nfl_detail(d, unit):
    H, A = d["home"], d["away"]
    mu, mk, fair = d["mu_model"], d.get("mu_market"), d["mu_fair"]
    diff = (mu - mk) if mk is not None else None
    sub = ""
    if diff is not None:
        sub = (f"The model rates {H if diff > 0 else A} {abs(diff):.1f} points better than the books do."
               if abs(diff) >= 0.5 else "The model agrees with the books here.")
    sl = sim_lines(d)
    head = ui.div(
        ui.div(ui.div(ui.div("Prediction", class_="k"), pred_value(d)),
               (ui.div(ui.div("Simulated spread", class_="k"),
                       ui.div(sl["spread"], ui.span(f"model {side_name(d, mu)}", class_="vp"), class_="v"))
                if sl else
                ui.div(ui.div("Model projection", class_="k"), ui.div(side_name(d, mu), class_="v"))),
               ui.div(ui.div("Sharp market (no vig)", class_="k"), ui.div(side_name(d, mk) if mk is not None else "-", class_="v")),
               ui.div(ui.div("Projected total", class_="k"),
                      ui.div(f"{sl['total']:g}" if sl else f"{d['tau_model']:.1f}",
                             ui.span(f"model {d['tau_model']:.1f}" + (f" · books {d['tau_market']:.1f}"
                                                                      if d.get("tau_market") else ""), class_="vp"),
                             class_="v")),
               class_="row2"),
        ui.div(sub, class_="sub"), weather_line(d), verdict_nfl(d), class_="headline")

    wm, wk, wf = d["win_model"], d.get("win_market"), d.get("win_fair")
    bars = ui.div(ui.h6("Win probability"),
                  ui.div("Model", class_="barlabel"),
                  prob_bar([(A, 1 - wm, "var(--c-away)"), (H, wm, "var(--c-home)")]),
                  *([ui.div("Books (no-vig)", class_="barlabel"),
                     prob_bar([(A, 1 - wk, "var(--c-away)"), (H, wk, "var(--c-home)")])]
                    if wk is not None else []),
                  *([ui.div("Model + market (used for the prediction and the prices)", class_="barlabel"),
                     prob_bar([(A, 1 - wf, "var(--c-away)"), (H, wf, "var(--c-home)")])]
                    if wk is not None and wf is not None else []), class_="sect")

    rows_s, rows_t, rows_m = [], [], []
    for b in ordered_books(d["books"]):
        e = d["books"][b]
        nm = BOOKS.get(b, b)
        if "spread" in e:
            s = e["spread"]
            rows_s.append(ui.tags.tr(
                ui.tags.td(nm),
                ui.tags.td(f"{A} {s['away_pt']:+g} ({amer(s['away_price'])})", ui.tags.small(f"covers {pct(s['away_cover'], 1)}")),
                ui.tags.td(ev_span(s["away_ev"])),
                ui.tags.td(f"{H} {s['home_pt']:+g} ({amer(s['home_price'])})", ui.tags.small(f"covers {pct(s['home_cover'], 1)}"
                                                                                           + (f", push {pct(s['push'], 1)}" if s['push'] > 0.001 else ""))),
                ui.tags.td(ev_span(s["home_ev"]))))
        if "total" in e:
            t = e["total"]
            rows_t.append(ui.tags.tr(
                ui.tags.td(nm),
                ui.tags.td(f"Over {t['pt']:g} ({amer(t['over_price'])})", ui.tags.small(f"hits {pct(t['over'], 1)}")),
                ui.tags.td(ev_span(t["over_ev"])),
                ui.tags.td(f"Under {t['pt']:g} ({amer(t['under_price'])})", ui.tags.small(f"hits {pct(t['under'], 1)}")),
                ui.tags.td(ev_span(t["under_ev"]))))
        if "ml" in e:
            ml = e["ml"]
            rows_m.append(ui.tags.tr(
                ui.tags.td(nm),
                ui.tags.td(f"{A} {amer(ml['away_price'])}", ui.tags.small(f"implied {pct(1 / ml['away_price'], 1)}")),
                ui.tags.td(ev_span(ml["away_ev"])),
                ui.tags.td(f"{H} {amer(ml['home_price'])}", ui.tags.small(f"implied {pct(1 / ml['home_price'], 1)}")),
                ui.tags.td(ev_span(ml["home_ev"]))))

    def table(title, rows, note=None):
        if not rows:
            return None
        return ui.div(ui.h6(title), ui.tags.table(
            ui.tags.thead(ui.tags.tr(ui.tags.th("Book"), ui.tags.th(A if title != "Total" else "Over"), ui.tags.th("EV"),
                                     ui.tags.th(H if title != "Total" else "Under"), ui.tags.th("EV"))),
            ui.tags.tbody(*rows), class_="bk"), ui.div(note, class_="note") if note else None, class_="sect")

    mx = max(p for _, p in d["top_margins"]) or 1
    margins = ui.div(ui.h6("Most likely final margins"), *[
        ui.div(ui.span(("Tie" if k == 0 else f"{H if k > 0 else A} by {abs(k)}"), class_="lbl"),
               ui.div(class_="fill", style=f"width:{180 * p / mx:.0f}px"), ui.span(pct(p, 1)), class_="mbar")
        for k, p in d["top_margins"]], class_="sect")

    def injcol(team, side):
        qb, qv = d["qbs"][side]
        lst = d["injuries"].get(team) or []
        items = [ui.div(ui.span(p.get("status"), class_=f"chip {p.get('status')}"),
                        f"{p.get('player')} ({p.get('pos')})", ui.tags.small(f" {pct(p.get('snap_share') or 0)} of snaps",
                                                                            class_="muted"), class_="injrow")
                 for p in lst if (p.get("snap_share") or 0) >= 0.1][:8]
        return ui.div(ui.tags.b(team), ui.div(f"QB: {qb}", class_="injrow"),
                      ui.div(f"Injury effect on {team}'s score: {d['inj_pts'][side]:+.1f} pts", class_="injrow"),
                      *(items or [ui.div("No significant injuries listed", class_="injrow")]))

    inj = ui.div(ui.h6("Quarterbacks & injuries (ESPN, live)"), ui.div(injcol(A, "away"), injcol(H, "home"), class_="inj"),
                 class_="sect")
    note = ui.div(f"EV and cover chances use the fair line {side_name(d, fair)}: spreads 10% model / 90% sharp market, "
                  "totals 15% / 85%, the mixes that tested best against closing lines. Positive EV in green.", class_="note")
    return ui.div(BACK, detail_header(d), head, reco_box(d, unit), bars, sim_section(d),
                  table("Spread", rows_s), table("Total", rows_t), table("Moneyline", rows_m),
                  margins, inj, note, class_="detail")


def soccer_detail(d, unit):
    H, A = d["home"], d["away"]
    hn, an = team_meta(d, "home")["name"], team_meta(d, "away")["name"]
    pm, pf, ps = d["p_model"], d["p_fair"], d.get("sharp_1x2")
    fav = max(range(3), key=lambda i: pm[i])
    head = ui.div(
        ui.div(ui.div(ui.div("Prediction", class_="k"), pred_value(d)),
               ui.div(ui.div("Expected goals (model)", class_="k"), ui.div(f"{d['xg'][0]:.2f} - {d['xg'][1]:.2f}", class_="v")),
               ui.div(ui.div("Model", class_="k"), ui.div(f"{pct(pm[0])} / {pct(pm[1])} / {pct(pm[2])}", class_="v")),
               ui.div(ui.div("Books (no-vig)", class_="k"),
                      ui.div(f"{pct(ps[0])} / {pct(ps[1])} / {pct(ps[2])}" if ps else "-", class_="v")), class_="row2"),
        ui.div(f"Home / draw / away. Model's most likely result: {[hn + ' win', 'draw', an + ' win'][fav]}; "
               f"fair Asian handicap {H} {d['ah_line']:+.2f}, fair total {d['total_line']:.2f} goals.", class_="sub"),
        lineup_line(d, hn, an), verdict_soccer(d, hn, an), class_="headline")
    bars = ui.div(ui.h6("Result probability"),
                  ui.div("Model", class_="barlabel"),
                  prob_bar([(H, pm[0], "var(--c-home)"), ("Draw", pm[1], "var(--c-draw)"), (A, pm[2], "var(--c-away)")]),
                  *([ui.div("Books (no-vig)", class_="barlabel"),
                     prob_bar([(H, ps[0], "var(--c-home)"), ("Draw", ps[1], "var(--c-draw)"),
                               (A, ps[2], "var(--c-away)")])] if ps else []),
                  *([ui.div("Model + market (used for the prediction and the prices)", class_="barlabel"),
                     prob_bar([(H, pf[0], "var(--c-home)"), ("Draw", pf[1], "var(--c-draw)"),
                               (A, pf[2], "var(--c-away)")])] if ps and pf else []),
                  class_="sect")
    r1 = []
    for b in ordered_books(d["books1x2"]):
        v = d["books1x2"][b]
        r1.append(ui.tags.tr(ui.tags.td(BOOKS.get(b, b)),
                             *[ui.tags.td(amer(v[i]), ui.tags.small(ev_span(pf[i] * v[i] - 1))) for i in range(3)]))
    t1 = ui.div(ui.h6("Match result - price and EV"), ui.tags.table(
        ui.tags.thead(ui.tags.tr(ui.tags.th("Book"), ui.tags.th(H), ui.tags.th("Draw"), ui.tags.th(A))),
        ui.tags.tbody(*r1), class_="bk"), class_="sect") if r1 else None
    r2 = []
    for b in ordered_books(d["booksou"]):
        o, u = d["booksou"][b]
        r2.append(ui.tags.tr(ui.tags.td(BOOKS.get(b, b)),
                             ui.tags.td(amer(o), ui.tags.small(ev_span(d["po_fair"] * o - 1))),
                             ui.tags.td(amer(u), ui.tags.small(ev_span((1 - d["po_fair"]) * u - 1)))))
    t2 = ui.div(ui.h6(f"Over / under 2.5 goals - model over {pct(d['po_fair'])}"), ui.tags.table(
        ui.tags.thead(ui.tags.tr(ui.tags.th("Book"), ui.tags.th("Over 2.5"), ui.tags.th("Under 2.5"))),
        ui.tags.tbody(*r2), class_="bk"), class_="sect") if r2 else None
    extra = ui.div(ui.h6("Other markets (model)"),
                   ui.div(f"Both teams to score {pct(d['btts'])}  -  Over 1.5 {pct(d['ou'][1.5])}  -  "
                          f"Over 3.5 {pct(d['ou'][3.5])}"),
                   ui.div(ui.div("Most likely scores (exact, blended with the market)", class_="barlabel"),
                          ui.div(*[ui.span(f"{x}-{y}  {pct(p, 1)}") for x, y, p in d["scores"]], class_="scores")),
                   class_="sect")

    lu = d.get("lineup")
    lu_type = (lu or {}).get("type")

    def abs_col(team, name, side):
        lst = sorted(d["absences"].get(team, []), key=lambda x: -(x["att_share"] + x["min_share"] * 0.1))
        items = [ui.div(ui.span("out" if x["p_out"] >= 0.9 else "doubt", class_=f"chip {'out' if x['p_out'] >= 0.9 else 'doubt'}"),
                        f"{x['player']}", ui.tags.small(f" {x['reason'][:40]}", class_="muted"), class_="injrow")
                 for x in lst[:8]]
        starters = ((lu or {}).get(side) or {}).get("starters") or []
        xi = ui.div(ui.tags.small(("Confirmed XI: " if lu_type == "confirmed" else "Predicted XI: ") +
                                  ", ".join(p["name"].split()[-1] for p in starters)), class_="injrow") if starters else None
        m = d["mult"][0 if side == "home" else 1]
        return ui.div(ui.tags.b(name), ui.div(f"Availability effect on {name}'s xG: x{m:.2f}", class_="injrow"),
                      *(items or [ui.div("No notable absences", class_="injrow")]), xi)
    avail = ui.div(ui.h6("Lineups & absences (FotMob / FPL)" + (" - lineups confirmed" if lu_type == "confirmed" else "")),
                   ui.div(abs_col(H, hn, "home"), abs_col(A, an, "away"), class_="inj"), class_="sect")
    note = ui.div("EV uses fair probabilities: 5% model, 95% sharp market (Kalshi, else Betfair), the mix that tested "
                  "best. Positive EV in green.", class_="note")
    return ui.div(BACK, detail_header(d), head, reco_box(d, unit), bars, sim_section(d), t1, t2, extra, avail, note,
                  class_="detail")


# ---------------------------------------------------------------------------------- UI
# Free ESPN score update whenever the app is opened or you come back to its tab (at most every 2 minutes),
# and every 3 minutes while a game on screen is live. No Odds API credits; the model itself never re-runs.
AUTO_UPDATE_JS = """
(function () {
  var last = 0;
  function trig(force) {
    if (!window.Shiny || !Shiny.setInputValue) return;
    var now = Date.now();
    if (!force && now - last < 120000) return;
    last = now;
    Shiny.setInputValue('auto_upd', now, {priority: 'event'});
  }
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') trig(false);
  });
  window.addEventListener('focus', function () { trig(false); });
  (function hook() {  // Shiny signals 'connected' through jQuery
    if (window.jQuery) {
      jQuery(document).on('shiny:connected', function () { setTimeout(function () { trig(true); }, 1200); });
    } else { setTimeout(hook, 100); }
  })();
  setInterval(function () {
    if (document.visibilityState === 'visible' && document.querySelector('.live')) trig(true);
  }, 180000);
})();
"""



def value_boxes(prefix):
    return ui.layout_columns(
        ui.value_box("Record (W-L-P)", ui.output_text(f"{prefix}_record")),
        ui.value_box("Profit", ui.output_text(f"{prefix}_profit")),
        ui.value_box("ROI", ui.output_text(f"{prefix}_roi")),
        ui.value_box("Avg closing-line value", ui.output_text(f"{prefix}_clv")),
        col_widths={"sm": (6, 6, 6, 6), "lg": (3, 3, 3, 3)},
    )


app_ui = ui.page_navbar(
    ui.nav_panel(
        "Games",
        ui.div(ui.input_action_button("upd_m", "Update scores (free)", class_="btn-upd-m"),
               ui.input_action_button("run_m", "Run model", class_="btn-run-m"), class_="run-m-wrap"),
        ui.output_ui("run_status"),
        ui.input_radio_buttons("league", None, {"ALL": "All", **COMP_NAMES}, selected="ALL", inline=True),
        ui.layout_columns(ui.output_ui("game_list"), ui.output_ui("game_detail"),
                          col_widths={"sm": (12, 12), "lg": (5, 7)}),
        value="games",
    ),
    ui.nav_panel(
        "Bets to take",
        ui.div(ui.input_action_button("upd_m2", "Update scores (free)", class_="btn-upd-m"),
               ui.input_action_button("run_m2", "Run model", class_="btn-run-m"), class_="run-m-wrap"),
        ui.output_ui("run_status2"),
        ui.output_ui("bet_cards"),
        ui.output_ui("run_notes"),
        value="bets",
    ),
    ui.nav_panel(
        "Results",
        ui.div("Finished games: the model's bet on each one and whether it hit. Updated by either button - the "
               "free score update is enough.", class_="pagehint"),
        ui.output_ui("results_view"),
        value="results",
    ),
    ui.nav_panel(
        "Model history",
        ui.div(H.INTRO, class_="hintro"),
        ui.div(
            ui.input_radio_buttons("h_sport", None, {"nfl": "NFL", "soccer": "Soccer"}, selected="nfl", inline=True),
            ui.output_ui("h_filters"),
            ui.input_action_button("h_build", "Rebuild history (free, ~3 min)", class_="btn-upd-m",
                                   style="flex:0 0 auto;margin:0"),
            class_="hfilt"),
        ui.output_text("h_status"),
        ui.output_ui("h_view"),
        value="history",
    ),
    ui.nav_panel(
        "Pick tracker",
        value_boxes("trk"),
        ui.div(ui.input_action_button("grade", "Grade finished games"), ui.output_text("grade_status"),
               class_="statusline", style="margin-top:14px"),
        ui.output_data_frame("picks_table"),
        ui.div("Closing-line value (CLV) compares your price with the sharp closing price. 'CLV Kalshi' uses Kalshi's "
               "last price before kickoff: expected profit per $1 at that price, available right after kickoff and "
               "for every competition. Positive CLV over many bets is the best sign of a real edge; a few dozen "
               "results are mostly luck.", class_="note"),
        ui.output_ui("lean_summary"),
        ui.output_data_frame("leans_table"),
        ui.output_ui("pricelog_view"),
        value="tracker",
    ),
    ui.nav_panel(
        "Settings",
        ui.layout_columns(
            ui.card(
                ui.card_header("Staking"),
                ui.input_numeric("unit", "Unit size ($)", 10, min=1, step=1),
                ui.input_numeric("bankroll", "Bankroll ($) - sets unit sizing via quarter-Kelly", 1000, min=10, step=50),
                ui.div("Stakes are quarter-Kelly rounded to the nearest 0.5 unit (min 0.5u, max 3u).", class_="note"),
            ),
            ui.card(
                ui.card_header("What to recommend"),
                ui.input_numeric("edge_nfl", "Minimum edge NFL (%)", 3, min=0, step=0.5),
                ui.input_numeric("edge_soccer", "Minimum edge soccer (%)", 3, min=0, step=0.5),
                ui.input_numeric("soccer_days", "Soccer: days ahead", 3, min=1, max=10),
                ui.input_checkbox_group("comps", "Soccer competitions",
                                        {k: v for k, v in COMP_NAMES.items() if k != "NFL"},
                                        selected=["EPL", "LALIGA", "SERIEA", "UCL", "UEL"]),
            ),
            ui.card(
                ui.card_header("Data sources"),
                ui.output_ui("cloud_status"),
                ui.output_ui("key_status"),
                ui.div("Live: ESPN (scores, NFL injuries, logos), FotMob (soccer injuries, suspensions, lineups), "
                       "Fantasy Premier League, nflverse, football-data.co.uk. Add or change keys with keys.bat.",
                       class_="note"),
            ),
            col_widths={"sm": (12, 12, 12), "lg": (4, 4, 4)},
        ),
        ui.input_action_button("save", "Save settings", class_="btn-primary"),
        ui.output_text("save_status"),
        value="settings",
    ),
    ui.nav_spacer(),
    ui.nav_control(T.theme_button()),
    ui.nav_control(ui.input_action_button("upd", "Update scores (free)", class_="btn-upd",
                                          title="ESPN only - no Odds API credits")),
    ui.nav_control(ui.input_action_button("run", "Run model", class_="btn-run",
                                          title="Full re-price - free: DraftKings via ESPN, sharp prices via Kalshi")),
    id="nav",
    title=T.brand(),
    window_title=T.APP_NAME,
    navbar_options=ui.navbar_options(underline=False),
    header=ui.TagList(ui.head_content(ui.HTML(T.FONTS), ui.tags.script(ui.HTML(T.PAGE_JS))),
                      ui.tags.style(ui.HTML(T.CSS)), ui.tags.script(ui.HTML(AUTO_UPDATE_JS))),
    fillable=False,
)


# ---------------------------------------------------------------------------------- server
def server(input, output, session):
    result = reactive.value(engine.load_last())  # show the last saved run; nothing is re-run on open
    selected = reactive.value(None)
    tracker_tick = reactive.value(0)
    grade_msg = reactive.value("")
    save_msg = reactive.value("")

    @reactive.effect
    def _load_settings():  # once, from config.json
        c = slate.user_config()
        ui.update_numeric("unit", value=c.get("unit", 10))
        ui.update_numeric("bankroll", value=c.get("bankroll", 1000))
        ui.update_numeric("edge_nfl", value=round(100 * c["min_edge"]["nfl"], 2))
        ui.update_numeric("edge_soccer", value=round(100 * c["min_edge"]["soccer"], 2))
        ui.update_numeric("soccer_days", value=c.get("soccer_days", 3))
        ui.update_checkbox_group("comps", selected=c.get("soccer_comps"))

    async def _with_progress(fn, first_msg):
        """Run a slow job in a worker thread while the progress bar shows its current step."""
        state = {"f": 0.01, "m": first_msg}
        with ui.Progress(min=0, max=1) as p:
            p.set(state["f"], message=state["m"])
            fut = asyncio.get_running_loop().run_in_executor(None, lambda: fn(lambda f, m: state.update(f=f, m=m)))
            while not fut.done():
                p.set(state["f"], message=state["m"])
                await asyncio.sleep(0.4)
            return await fut

    running = {"on": False, "auto": False}
    RUN_BTNS, UPD_BTNS = ("run", "run_m", "run_m2"), ("upd", "upd_m", "upd_m2")

    async def _job(fn, first_msg, busy_label):
        for _ in range(150):  # a background score update finishes in seconds: wait for it, don't drop the tap
            if not (running["on"] and running["auto"]):
                break
            await asyncio.sleep(0.2)
        if running["on"]:  # ignore extra taps while something is running
            return None
        running["on"] = True
        for b in RUN_BTNS + UPD_BTNS:
            ui.update_action_button(b, disabled=True)
        try:
            return await _with_progress(fn, first_msg)
        except Exception as e:
            ui.notification_show(f"{busy_label} failed: {e}", type="error", duration=15)
            return None
        finally:
            running["on"] = False
            for b in RUN_BTNS + UPD_BTNS:
                ui.update_action_button(b, disabled=False)

    @reactive.effect
    @reactive.event(input.run, input.run_m, input.run_m2)
    async def _run():
        if cloud.enabled():  # runs on GitHub (works with the PC off too); results are copied back here
            res = await _job(lambda cb: cloud.trigger("run", cb), "Starting the model on GitHub...", "Run")
        else:
            res = await _job(lambda cb: engine.run(cb), "Starting full run (free odds)...", "Run")
        if res is not None:
            result.set(res)
            selected.set(None)
            tracker_tick.set(tracker_tick() + 1)

    @reactive.effect
    @reactive.event(input.upd, input.upd_m, input.upd_m2)
    async def _update():
        if cloud.enabled():
            res = await _job(lambda cb: cloud.trigger("scores", cb), "Updating scores on GitHub...", "Score update")
        else:
            res = await _job(lambda cb: engine.refresh_scores(cb), "Updating scores from ESPN (free)...", "Score update")
        if res is not None:
            result.set(res)
            tracker_tick.set(tracker_tick() + 1)
            ui.notification_show(res.notes[0] if res.notes else "Scores updated", duration=6)

    async def _cloud_pull():
        """Cloud mode: copy newer results from GitHub (a scheduled run, or one started on the phone)."""
        if running["on"]:
            return
        running["on"] = running["auto"] = True
        try:
            changed = await asyncio.get_running_loop().run_in_executor(None, cloud.pull)
        except Exception:
            changed = False
        finally:
            running["on"] = running["auto"] = False
        res = engine.load_last() if changed else None
        if res is not None:
            result.set(res)
            tracker_tick.set(tracker_tick() + 1)
            hist_tick.set(hist_tick() + 1)
            ui.notification_show("New results from GitHub", duration=4)

    @reactive.effect
    @reactive.event(input.auto_upd)
    async def _auto_update():
        """Quiet free score update (ESPN) when the app is opened / refocused. Redraws only if something moved."""
        if cloud.enabled():
            await _cloud_pull()
            return
        r = result()
        if r is None or running["on"]:
            return
        sc = getattr(r, "scores_at", None) or r.ran_at
        if sc is not None and pd.Timestamp.now() - pd.Timestamp(sc) < pd.Timedelta(seconds=100):
            return
        before = engine.scores_signature(r)
        running["on"] = running["auto"] = True
        try:
            res = await asyncio.get_running_loop().run_in_executor(None, lambda: engine.refresh_scores(publish="auto"))
        except Exception:
            res = None
        finally:
            running["on"] = running["auto"] = False
        if res is None:
            return
        if engine.scores_signature(res) != before or res.settled:
            result.set(res)
            tracker_tick.set(tracker_tick() + 1)
            ui.notification_show("Scores updated from ESPN" + (f" - graded {len(res.settled)} bet(s)" if res.settled else ""),
                                 duration=4)
        else:
            r.scores_at = res.scores_at  # nothing moved: just refresh the 'updated' time
            scores_seen.set(res.scores_at)

    scores_seen = reactive.value(None)

    def status_chips():
        scores_seen()
        return status_bar(result())

    @render.ui
    def run_status():
        return status_chips()

    @render.ui
    def run_status2():
        return status_chips()

    def filtered():
        r = result()
        if r is None:
            return []
        lg = input.league()
        return [(i, d) for i, d in enumerate(r.games) if lg == "ALL" or d["comp"] == lg]

    @reactive.effect
    @reactive.event(input.pick_game)
    def _pick():
        selected.set(int(input.pick_game()))

    @reactive.effect
    @reactive.event(input.goto_game)
    def _goto():
        r = result()
        if r is None:
            return
        target = input.goto_game()
        for i, d in enumerate(r.games):
            if engine.game_id(d) == target:
                selected.set(i)
                ui.update_radio_buttons("league", selected="ALL")
                ui.update_navs("nav", selected="games")
                break

    def current_index():
        items = filtered()
        if not items:
            return None
        sel = selected()
        if sel is not None and any(i == sel for i, _ in items):
            return sel
        return items[0][0]

    @render.ui
    def game_list():
        r = result()
        if r is None:
            return ui.div(ui.h4("Ready when you are"),
                          ui.p("Press Run model. In about a minute it pulls DraftKings' lines (via ESPN) and sharp "
                               "prices from Kalshi, injuries and lineups from ESPN and FotMob, and prices every game. "
                               "All free: no API credits."),
                          class_="empty")
        items = filtered()
        if not items:
            return ui.div("No upcoming games for this league in the window.", class_="empty")
        with reactive.isolate():  # tapping a card highlights it in the browser; don't redraw the whole list
            cur = current_index()
        out, last_day = [], None
        for i, d in items:
            dk = day_key(d)
            if dk != last_day:
                out.append(ui.div(pd.Timestamp(dk).strftime("%A, %B %d").replace(" 0", " "), class_="daygroup"))
                last_day = dk
            out.append(game_card(i, d, i == cur))
        return ui.div(*out)

    @render.ui
    def game_detail():
        r = result()
        if r is None:
            return None
        i = current_index()
        if i is None:
            return None
        d = r.games[i]
        unit = slate.user_config().get("unit", 10)
        try:
            return nfl_detail(d, unit) if d["sport"] == "nfl" else soccer_detail(d, unit)
        except Exception as e:  # never let one odd game break the page
            return ui.div(f"Could not draw this game: {e}", class_="empty")

    @render.ui
    def bet_cards():
        r = result()
        if r is None:
            return ui.div(ui.h4("Ready when you are"), ui.p("Press Run model to see the bets worth taking."),
                          class_="empty")
        unit = slate.user_config().get("unit", 10)
        every = all_bets_table(r.games, lambda i, d: f"bmActive({i}); " + click("goto_game", engine.game_id(d)) + OPEN,
                               unit, r.ran_at)
        gidx = {engine.game_id(x): j for j, x in enumerate(r.games)}
        if not r.recommended:
            c = slate.user_config()
            return ui.div(ui.div(ui.h4("No bets clear the bar right now"),
                                 ui.p(f"Nothing beats the sharp price by the minimum edge (NFL {c['min_edge']['nfl']:.0%}, "
                                      f"soccer {c['min_edge']['soccer']:.0%}). That's normal. The model's best bet on "
                                      "every game is listed below anyway."), class_="empty"), every)
        cards = []
        for p in r.recommended:
            is_new = engine._key(p) in r.new_ids
            match = f"{p['away']} @ {p['home']}" if p["sport"] == "nfl" else f"{p['home']} v {p['away']}"
            units = p.get("units") or p["stake"] / unit
            gid = f"{p['sport']}|{p['home']}|{p['away']}"
            gd = next((x for x in r.games if engine.game_id(x) == gid), None)
            wtxt = why_text(gd, p, "rec") if gd else ""
            cards.append(ui.div(
                ui.div(ui.span(f"{COMP_NAMES.get(p['comp'], p['comp'])} - {pd.Timestamp(p['game_date']):%a %d %b}"),
                       " ", ui.span("NEW", class_="badge-new") if is_new else ui.span("tracked", class_="badge-old"),
                       class_="gtop"),
                ui.div(match, style="margin-top:6px;font-weight:600"),
                ui.div(ui.tags.b("Prediction: "), pred_text(gd), class_="note") if gd else None,
                ui.div(engine.describe_bet(p), class_="bet"),
                ui.div(f"{amer(p['odds'])} at {BOOKS.get(p['book'], p['book'])}"),
                ui.div(f"{units:g}u  -  ${units * unit:,.2f}", class_="stake"),
                ui.div(ui.tags.b("Why: "), wtxt, class_="why") if wtxt else None,
                ui.div(ui.HTML(f"Edge <b>{p['edge']:+.1%}</b> - model {p['model_prob']:.1%} vs price {1 / p['odds']:.1%}"
                               f"<br>Fair price {amer(1 / p['model_prob'])}; only bet at {amer(p['odds'])} or better"
                               f"<br><u>Open game page</u>"), class_="note"),
                class_=f"bm-card {p['sport']}",
                onclick=(f"bmActive({gidx[gid]}); " if gid in gidx else "") + click("goto_game", gid) + OPEN))
        return ui.div(ui.div(*cards, class_="bm-cards"), every)

    @render.ui
    def run_notes():
        r = result()
        if r is None:
            return None
        lines = [f"Graded: {s}" for s in r.settled] + list(r.notes)
        return ui.div(*[ui.div(x) for x in lines], class_="note") if lines else None

    # ---- results (model vs books vs actual)
    @render.ui
    def results_view():
        tracker_tick()
        from . import results as R
        return R.view(slate.user_config().get("unit", 10))

    # ---- model history (past seasons replayed + live predictions vs actual results)
    hist_tick = reactive.value(0)

    @reactive.calc
    def hist_frame():
        hist_tick()
        tracker_tick()
        return H.frame()

    @render.ui
    def h_filters():
        d = hist_frame()
        sp = input.h_sport()
        d = d[d["sport"] == sp] if len(d) else d
        seasons = sorted(d["season"].dropna().unique(), reverse=True) if len(d) else []
        out = [ui.input_select("h_season", None, {"ALL": "All seasons", **{s: s for s in seasons}}, width="150px")]
        if sp == "soccer":
            out.insert(0, ui.input_select("h_comp", None, {"ALL": "All leagues", **{c: H.COMPS[c] for c in H.SOCCER}},
                                          width="190px"))
        return ui.div(*out)

    @render.text
    def h_status():
        b = H.built_at()
        return f"History last built {b}. Rebuild after a few weeks to add newly finished games." if b else \
            "History not built yet - press Rebuild history (free, no API credits)."

    @render.ui
    def h_view():
        d = hist_frame()
        sp = input.h_sport()
        if d.empty:
            return ui.div("No history yet. Press Rebuild history - it replays past seasons using free data only.",
                          class_="empty")
        d = d[d["sport"] == sp]
        label = []
        try:
            comp = input.h_comp() if sp == "soccer" else "ALL"
        except Exception:
            comp = "ALL"
        try:
            season = input.h_season()
        except Exception:
            season = "ALL"
        if comp and comp != "ALL":
            d = d[d["comp"] == comp]
            label.append(H.COMPS.get(comp, comp))
        if season and season != "ALL":
            d = d[d["season"] == season]
            label.append(season)
        return H.view(d, sp, " · ".join(label) or "all seasons")

    @reactive.effect
    @reactive.event(input.h_build)
    async def _h_build():
        if cloud.enabled():
            n = await _job(lambda cb: (cloud.trigger("history", cb), len(H.frame()))[1],
                           "Rebuilding the history on GitHub...", "History build")
        else:
            n = await _job(lambda cb: H.build(cb), "Replaying past seasons (no API credits)...", "History build")
        if n is not None:
            hist_tick.set(hist_tick() + 1)
            ui.notification_show(f"History built: {n:,} games", duration=6)

    # ---- tracker
    def all_picks_df():
        tracker_tick()
        return db.picks()

    def picks_df():  # recommended + hand-logged bets only
        from . import picks as PK
        return PK.main_picks(all_picks_df())

    @render.ui
    def lean_summary():
        from . import picks as PK
        d = all_picks_df()
        unit = slate.user_config().get("unit", 10)
        boxes = []
        for src, title in (("lean", "Leans (edge > 0, under the minimum)"), ("leastbad", "Least-bad (most likely outcome)")):
            sm = PK.summary(d[d["source"] == src]) if len(d) else {"n": 0, "open": 0}
            if sm.get("record"):
                val = f"{sm['record']}  ·  {sm['pnl'] / unit:+.1f}u  ·  ROI {sm['roi']:+.1%}"
                kk = d.loc[d["source"] == src, "kclv"].dropna() if "kclv" in d else []
                sub = f"CLV {sm['clv']}" + (f" · vs Kalshi {kk.mean():+.1%}" if len(kk) else "") + f" · {sm['open']} open"
            else:
                val, sub = "-", f"{sm['open']} open, none graded yet"
            boxes.append(ui.value_box(title, val, ui.div(sub, style="font-size:.85rem")))
        return ui.div(ui.h5("The model's best bet on every other game", style="margin-top:26px"),
                      ui.div("Tracked at a notional 0.5u each so you can see whether the model's small edges hold up. "
                             "Not included in the record above.", class_="note"),
                      ui.layout_columns(*boxes, col_widths={"sm": (12, 12), "lg": (6, 6)}))

    @render.data_frame
    def leans_table():
        from . import picks as PK
        d = PK.lean_picks(all_picks_df())
        if d.empty:
            return render.DataGrid(pd.DataFrame({"": ["No leans yet - they're logged on the next Run model."]}))
        recs = d.to_dict("records")
        t = pd.DataFrame({
            "date": [pd.Timestamp(x["game_date"]).strftime("%a %d %b") for x in recs],
            "type": [KIND_LABEL["lean" if x["source"] == "lean" else "neg"] for x in recs],
            "match": [f"{x['away']} @ {x['home']}" if x["sport"] == "nfl" else f"{x['home']} v {x['away']}" for x in recs],
            "bet": [engine.describe_bet(x) for x in recs],
            "odds": [amer(x["odds"]) for x in recs],
            "book": [BOOKS.get(x["book"], x["book"]) for x in recs],
            "edge": [f"{x['edge']:+.1%}" if pd.notna(x["edge"]) else "" for x in recs],
            "status": [x["status"] for x in recs],
            "score": [x["score"] or "" for x in recs],
            "P/L": [f"${x['pnl']:+,.2f}" if pd.notna(x["pnl"]) else "" for x in recs],
            "CLV": [("" if pd.isna(x["clv"]) else f"{x['clv']:+.1%}" if x["market"] in ("1X2", "OU25", "ML")
                     else f"{x['clv']:+.1f} pts") for x in recs],
            "CLV Kalshi": ["" if pd.isna(x.get("kclv")) else f"{x['kclv']:+.1%}" for x in recs],
        }).iloc[::-1]
        return render.DataGrid(t, filters=True, width="100%")

    @render.ui
    def pricelog_view():
        tracker_tick()
        from . import pricelog
        try:
            return pricelog.view()
        except Exception as e:
            return ui.div(f"Price log unavailable: {e}", class_="note")

    def settled():
        d = picks_df()
        return d[d["status"].isin(["won", "lost", "push"])]

    @render.text
    def trk_record():
        s = settled()
        return f"{(s.status == 'won').sum()}-{(s.status == 'lost').sum()}-{(s.status == 'push').sum()}" if len(s) else "-"

    @render.text
    def trk_profit():
        s = settled()
        if not len(s):
            return "-"
        unit = slate.user_config().get("unit", 10)
        return f"${s.pnl.sum():+,.2f} ({s.pnl.sum() / unit:+.1f}u)"

    @render.text
    def trk_roi():
        s = settled()
        return f"{s.pnl.sum() / s.stake.sum():+.1%}" if len(s) and s.stake.sum() else "-"

    @render.text
    def trk_clv():
        k = picks_df()["kclv"].dropna() if "kclv" in picks_df() else []
        if len(k):  # vs Kalshi's last pre-kickoff price: the sharp close, same scale for every market
            return f"{k.mean():+.1%} vs Kalshi ({len(k)} bet{'s' if len(k) != 1 else ''})"
        s = settled()
        c = s.loc[s["market"].isin(["1X2", "OU25", "ML"]), "clv"].dropna()
        pts = s.loc[s["market"].isin(["SPREAD", "TOTAL"]), "clv"].dropna()
        parts = ([f"{c.mean():+.1%}"] if len(c) else []) + ([f"{pts.mean():+.1f} pts"] if len(pts) else [])
        return " / ".join(parts) or "-"

    @render.data_frame
    def picks_table():
        d = picks_df()
        if d.empty:
            return render.DataGrid(pd.DataFrame({"": ["No picks yet - press Run model."]}))
        unit = slate.user_config().get("unit", 10)
        recs = d.to_dict("records")
        t = pd.DataFrame({
            "date": [pd.Timestamp(x["game_date"]).strftime("%a %d %b") for x in recs],
            "league": [COMP_NAMES.get(x["comp"], x["comp"]) for x in recs],
            "match": [f"{x['away']} @ {x['home']}" if x["sport"] == "nfl" else f"{x['home']} v {x['away']}" for x in recs],
            "bet": [engine.describe_bet(x) for x in recs],
            "odds": [amer(x["odds"]) for x in recs],
            "book": [BOOKS.get(x["book"], x["book"]) for x in recs],
            "units": [round(x["stake"] / unit, 2) for x in recs],
            "stake": [f"${x['stake']:,.2f}" for x in recs],
            "edge": [f"{x['edge']:+.1%}" if pd.notna(x["edge"]) else "" for x in recs],
            "status": [x["status"] for x in recs],
            "score": [x["score"] or "" for x in recs],
            "P/L": [f"${x['pnl']:+,.2f}" if pd.notna(x["pnl"]) else "" for x in recs],
            "CLV": [("" if pd.isna(x["clv"]) else f"{x['clv']:+.1%}" if x["market"] in ("1X2", "OU25", "ML")
                     else f"{x['clv']:+.1f} pts") for x in recs],
            "CLV Kalshi": ["" if pd.isna(x.get("kclv")) else f"{x['kclv']:+.1%}" for x in recs],
        }).iloc[::-1]
        return render.DataGrid(t, filters=True, width="100%")

    @reactive.effect
    @reactive.event(input.grade)
    async def _grade():
        from . import data_soccer as ds, espn, nfl, picks
        if cloud.enabled():  # grading happens in the runs on GitHub: run a score update there
            res = await _job(lambda cb: cloud.trigger("scores", cb), "Grading on GitHub...", "Grading")
            if res is not None:
                result.set(res)
                grade_msg.set(f"  Graded {len(res.settled)} pick(s)." if res.settled else "  Nothing new to grade yet.")
                tracker_tick.set(tracker_tick() + 1)
            return

        def job(cb):
            cb(0.2, "Loading NFL scores...")
            g = nfl.load_games()
            cb(0.5, "Loading soccer scores...")
            df = ds.load_all(verbose=False)
            cb(0.8, "Checking ESPN finals...")
            eg = espn.all_games(["NFL"] + list(slate.user_config()["soccer_comps"]))
            return picks.settle(df, g, eg)
        msgs = await _with_progress(job, "Loading final scores...")
        grade_msg.set(f"  Graded {len(msgs)} pick(s)." if msgs else "  Nothing new to grade yet.")
        tracker_tick.set(tracker_tick() + 1)

    @render.text
    def grade_status():
        return grade_msg()

    # ---- settings
    @render.ui
    def key_status():
        c = slate.user_config()
        if c.get("odds_source", "free") == "odds_api":
            ok = "connected" if c.get("odds_api_key") else "not set"
            odds_txt = f"Odds: The Odds API (key {ok}; uses credits)"
        else:
            odds_txt = "Odds: free. DraftKings via ESPN, sharp prices via Kalshi (no key, no credits)"
        return ui.div(ui.p(odds_txt), ui.p("ESPN, Kalshi, FotMob, FPL: connected (no key needed)"))

    @reactive.effect
    @reactive.event(input.save)
    async def _save():
        path = slate.ROOT / "config.json"
        c = json.loads(path.read_text()) if path.exists() else dict(slate.DEFAULTS)
        c.update({"unit": float(input.unit() or 10), "bankroll": float(input.bankroll() or 1000),
                  "min_edge": {"nfl": float(input.edge_nfl() or 0) / 100, "soccer": float(input.edge_soccer() or 0) / 100},
                  "soccer_days": int(input.soccer_days() or 3), "soccer_comps": list(input.comps())})
        path.write_text(json.dumps(c, indent=2))
        save_msg.set("  Saved. The next run uses these settings.")
        try:  # settings.json (no keys in it) is what the runs on GitHub read
            sent = await asyncio.get_running_loop().run_in_executor(None, lambda: cloud.save_settings(c))
        except Exception as e:
            sent = f"Couldn't write settings.json: {e}"
        if sent:
            save_msg.set(f"  Saved. The next run uses these settings. {sent}")
        tracker_tick.set(tracker_tick() + 1)

    cloud_tick = reactive.value(0)

    @render.ui
    def cloud_status():
        cloud_tick()
        c = cloud._config()
        url = cloud.page_url()
        if "cloud" not in c:  # `python -m betmodel cloud setup` hasn't been run
            return ui.div(ui.tags.b("Runs on this PC. "), "To run on GitHub as well (with the PC off, on a schedule and "
                          "from the phone page's Run button), set it up once: python -m betmodel cloud setup", class_="note")
        on = bool(c.get("cloud"))
        return ui.div(
            ui.input_switch("cloud_on", "Run on GitHub (works with the PC off)", value=on),
            ui.div("Run model and Update scores start the run on GitHub and copy the results here. " if on else
                   "Run model and Update scores run on this PC, and those results stay on this PC. ",
                   "GitHub also runs by itself: the model every 2 hours, scores and results every 30 minutes. ",
                   ui.tags.a("Open the phone page", href=url, target="_blank") if url else None, class_="note"))

    @reactive.effect
    @reactive.event(input.cloud_on, ignore_init=True)
    async def _cloud_switch():
        on = bool(input.cloud_on())
        if on == bool(cloud._config().get("cloud")):
            return
        cloud._set_config(cloud=on)
        cloud_tick.set(cloud_tick() + 1)
        ui.notification_show("Runs now happen on GitHub" if on else "Runs now happen on this PC", duration=5)
        if on:
            await _cloud_pull()

    @render.text
    def save_status():
        return save_msg()


app = App(app_ui, server)
