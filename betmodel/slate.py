"""Build priced slates (all upcoming games) and the picks worth logging.

Price sources, best first:
  1. The Odds API (if key): DraftKings / FanDuel prices to bet, Pinnacle / Betfair as the sharp anchor
  2. football-data.co.uk fixtures: Paddy Power (FanDuel's sister book) to bet, Betfair Exchange as anchor
  3. nflverse consensus NFL lines
A pick is only made when a market reference exists - the backtests show model-only disagreement
with the market is mostly noise.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import db, odds as O, odds_api

ROOT = Path(__file__).resolve().parent.parent
# Rules a bet must pass to be RECOMMENDED (on top of the minimum edge):
#   price_only     the edge must hold against the sharp books' no-vig price alone; the model can veto a bet
#                  (by lowering the blended chance) but can't create one - its own view doesn't beat the close
#   max_odds       no prices longer than this (decimal; 4.0 = +300): longshot edges are mostly de-vig noise
#   min_prob_gap   the win chance must beat the price's break-even by at least this much (0.015 = 1.5 points)
#   max_stale_min  skip when the sharp price is this many minutes older than the DraftKings / FanDuel price
PICK_RULES = {"price_only": True, "max_odds": {"nfl": 4.0, "soccer": 6.0}, "min_prob_gap": 0.015, "max_stale_min": 30}
DEFAULTS = {"bankroll": 1000, "unit": 10, "kelly_fraction": 0.25, "max_stake_pct": 0.03,
            "min_edge": {"soccer": 0.03, "nfl": 0.03},
            "soccer_comps": ["EPL", "LALIGA", "SERIEA", "UCL", "UEL"], "soccer_days": 3,
            "odds_api_key": "", "api_football_key": ""}


def user_config() -> dict:
    """Defaults, then settings.json (unit, bankroll, edges... no keys; the GitHub runs read it too),
    then config.json (this PC's own file, which also holds the keys)."""
    c = dict(DEFAULTS)
    for name in ("settings.json", "config.json"):
        p = ROOT / name
        if p.exists():
            try:
                c.update(json.loads(p.read_text(encoding="utf-8-sig")))
            except ValueError as e:
                print(f"  ! {name} could not be read ({e}); using the other settings")
    return c


def tuned() -> dict:
    p = Path(__file__).resolve().parent / "tuned.json"
    return json.loads(p.read_text()) if p.exists() else {}


def units_for(p, dec, cfg) -> float:
    """Quarter-Kelly stake expressed in units, rounded to the nearest 0.5u (min 0.5u, max 3u)."""
    dollars = cfg["bankroll"] * O.kelly(p, dec, cfg["kelly_fraction"], cfg["max_stake_pct"])
    u = dollars / cfg.get("unit", 10)
    return float(min(3.0, max(0.5, round(u * 2) / 2)))


def _stake(p, dec, cfg):
    """Dollar stake = units x unit size."""
    return round(units_for(p, dec, cfg) * cfg.get("unit", 10), 2)


def _ok(x):
    x = np.asarray(x, float)
    return x.size > 0 and np.all(np.isfinite(x)) and np.all(x > 1)


def live_prices(comp: str, resolve, cfg: dict) -> dict:
    """Live prices for a competition. Free by default (DraftKings via ESPN + Kalshi as the sharp price, no keys,
    no credits); set "odds_source": "odds_api" in config.json to use The Odds API instead."""
    if (cfg or {}).get("odds_source", "free") == "odds_api":
        return odds_api.prices(comp, resolve)
    from . import freeodds
    return freeodds.prices(comp, resolve, days=int((cfg or {}).get("soccer_days", 3)))


def pick_rules(cfg: dict) -> dict:
    r = {**PICK_RULES, **(cfg.get("pick_rules") or {})}
    if not isinstance(r["max_odds"], dict):
        r["max_odds"] = {"nfl": r["max_odds"], "soccer": r["max_odds"]}
    return r


def block_reason(sport: str, o: dict, cfg: dict) -> str:
    """Why an offer can't be a RECOMMENDED bet ('' if it can). o needs: odds, edge (blended chance),
    edge_mkt (sharp books alone, None if no sharp price), p_win / p_win_mkt (pushes left out), stale."""
    r = pick_rules(cfg)
    me = cfg["min_edge"][sport]
    if o["edge"] < me:
        return "below your minimum edge"
    if o.get("book") not in odds_api.BETTABLE:
        return f"that price is at {o.get('book')}, not a sportsbook you use"
    if r["price_only"]:
        if o.get("edge_mkt") is None:
            return "no sharp price to check it against"
        if o["edge_mkt"] < me:
            return (f"the edge comes from the model's opinion, not the price: against the sharp books alone "
                    f"it's {o['edge_mkt']:+.1%}")
    mx = r["max_odds"].get(sport)
    if mx and o["odds"] > mx + 1e-9:
        return f"longshot price (longer than {O.to_american(mx)}), where edges are mostly noise"
    ps = [x for x in (o.get("p_win"), o.get("p_win_mkt")) if x is not None]
    gap = (min(ps) if ps else 0.0) - 1 / o["odds"]
    if gap < r["min_prob_gap"]:
        return f"its chance is only {100 * gap:.1f} points above the price's break-even, too thin to trust"
    if o.get("stale"):
        return "the sharp price was older than this book's price, so the edge may not be real"
    return ""


def choose_best(evals: list[dict], sport: str, cfg: dict) -> dict | None:
    """The game's best offer. A recommendable one wins (ranked by the smaller of its blended and sharp-only
    edges, which is what's shown and staked); otherwise the highest blended edge, kept as a lean with the
    reason it wasn't recommended."""
    evals = [o for o in evals if o.get("book") in odds_api.BETTABLE]  # only prices you can actually bet
    if not evals:
        return None
    for o in evals:
        o["block"] = block_reason(sport, o, cfg)
    ok = [o for o in evals if not o["block"]]
    low = lambda a, b: a if b is None else min(a, b)
    if ok:
        b = dict(max(ok, key=lambda o: low(o["edge"], o.get("edge_mkt"))))
        b["edge_fair"], b["edge"] = b["edge"], low(b["edge"], b.get("edge_mkt"))
        b["model_prob"] = low(b["p_win"], b.get("p_win_mkt"))
    else:
        b = dict(max(evals, key=lambda o: o["edge"]))
    return {k: v for k, v in b.items() if not k.startswith("_")}


def _stale(updated: dict, mkt_key: str, book: str, sharp_book: str | None, rules: dict) -> bool:
    """True when the sharp book's price is more than max_stale_min older than this book's (both known)."""
    if not updated or not sharp_book or book == sharp_book:
        return False
    a, b = updated.get((mkt_key, book)), updated.get((mkt_key, sharp_book))
    return bool(a is not None and b is not None and (a - b) > pd.Timedelta(minutes=rules["max_stale_min"]))


def _hrs_to(kickoff) -> float | None:
    if kickoff is None or pd.isna(kickoff):
        return None
    k = pd.Timestamp(kickoff)
    k = k.tz_convert(None) if k.tzinfo is not None else k
    return round((k - pd.Timestamp.now(tz="UTC").tz_localize(None)).total_seconds() / 3600, 2)


def _log_prices(rows: list[dict], kalshi_rows: list[dict] | None = None) -> None:
    try:  # the log must never break a run
        db.save_odds(rows)
        db.save_kalshi_games(kalshi_rows or [])
    except Exception as e:
        print(f"  ! price log: {e}")


# ============================================================================== SOCCER
def soccer_slate(m, df, comps, days, av=None, cfg=None, details: list | None = None, lu_state=None):
    """details: if a list is passed, a full per-game record (every book's prices, score grid,
    absences, lineups) is appended for each match - used by the app's game pages.
    lu_state: lineup.LiveState - when given, a match with a FotMob lineup is adjusted by the
    player-rating lineup model instead of the injury list (the XI already reflects absences)."""
    from . import lineup as LU
    from . import data_soccer as ds
    cfg = cfg or user_config()
    w = tuned().get("soccer", {}).get("blend_w", 0.1)
    now = pd.Timestamp.today().normalize()
    horizon = now + pd.Timedelta(days=days + 1)
    games = {}  # (home, away) -> dict

    now_utc = pd.Timestamp.now(tz="UTC").tz_localize(None)

    def add(date, comp, h, a, neutral=False, kickoff_utc=None):
        if kickoff_utc is not None and pd.notna(kickoff_utc) and kickoff_utc < now_utc:
            return None  # already started
        if h in m.teams and a in m.teams and now - pd.Timedelta(days=1) <= date <= horizon:
            games.setdefault((h, a), {"date": date, "comp": comp, "neutral": neutral, "book": {}, "sharp": {},
                                      "kickoff": kickoff_utc, "books1x2": {}, "booksou": {}})
        g_ = games.get((h, a))
        if g_ is not None and kickoff_utc is not None and pd.notna(kickoff_utc):
            g_["kickoff"] = kickoff_utc
        return g_

    # fixture list + UK prices from football-data
    try:
        raw = ds._get("https://www.football-data.co.uk/fixtures.csv", ds.DATA_DIR / "fixtures.csv", 3)
        fx = pd.read_csv(io.BytesIO(raw), encoding="utf-8-sig")
        inv = {v: k for k, v in ds.MAIN_LEAGUES.items()}
        fx["comp"] = fx["Div"].map(inv)
        fx["date"] = pd.to_datetime(fx["Date"], dayfirst=True)
        # kickoff times are UK local time
        ko = pd.to_datetime(fx["Date"] + " " + fx.get("Time", pd.Series("12:00", index=fx.index)).fillna("12:00"),
                            dayfirst=True, errors="coerce")
        fx["ko_utc"] = ko.dt.tz_localize("Europe/London", ambiguous="NaT", nonexistent="NaT").dt.tz_convert(None)
        for r in fx[fx["comp"].isin(comps)].to_dict("records"):
            g = add(r["date"], r["comp"], r["HomeTeam"], r["AwayTeam"], kickoff_utc=r["ko_utc"])
            if not g:
                continue
            for name, cols in [("PaddyPower", ("PPH", "PPD", "PPA")), ("Bet365", ("B365H", "B365D", "B365A")),
                               ("Avg", ("AvgH", "AvgD", "AvgA"))]:
                v = [r.get(c, np.nan) for c in cols]
                if _ok(v):
                    g["book"].setdefault("1X2", (name, v))
                    g["books1x2"][name] = v
            for cols in [("BFEH", "BFED", "BFEA"), ("AvgH", "AvgD", "AvgA")]:
                v = [r.get(c, np.nan) for c in cols]
                if _ok(v):
                    g["sharp"].setdefault("1X2", v)
                    if cols[0] == "BFEH":
                        g["books1x2"]["Betfair Exchange"] = v
            for name, cols in [("Bet365", ("B365>2.5", "B365<2.5")), ("Avg", ("Avg>2.5", "Avg<2.5"))]:
                v = [r.get(c, np.nan) for c in cols]
                if _ok(v):
                    g["book"].setdefault("OU25", (name, v))
                    g["booksou"][name] = v
            for cols in [("BFE>2.5", "BFE<2.5"), ("Avg>2.5", "Avg<2.5")]:
                v = [r.get(c, np.nan) for c in cols]
                if _ok(v):
                    g["sharp"].setdefault("OU25", v)
                    if cols[0] == "BFE>2.5":
                        g["booksou"]["Betfair Exchange"] = v
    except Exception as e:
        print(f"  ! domestic fixtures unavailable: {e}")
    # UEFA fixtures
    up = df[df["comp"].isin(comps) & df["hg"].isna() & df["comp"].isin(["UCL", "UEL", "UECL"])]
    for r in up.itertuples():  # fixturedownload times are UTC
        add(r.date, r.comp, r.home, r.away, bool(r.neutral), kickoff_utc=r.date)
    # DraftKings + a sharp price (free: ESPN + Kalshi; or The Odds API): overrides the UK proxies when present
    for comp in comps:
        try:
            pr = live_prices(comp, lambda n: ds.resolve_team(n, m.teams), cfg)
        except Exception as e:
            print(f"  ! live odds {comp}: {e}")
            continue
        for (h, a), rec in pr.items():
            if rec["date"] < now_utc:
                games.pop((h, a), None)
                continue
            g = add(rec["date"], comp, h, a, kickoff_utc=rec["date"]) or games.get((h, a))
            if not g:
                continue
            # live books replace the UK proxies on the game page
            if rec["h2h"]:
                g["books1x2"] = {b: [v[s] for s in "HDA"] for b, v in rec["h2h"].items() if all(s in v for s in "HDA")}
            ou = {b: [v["OVER"][1], v["UNDER"][1]] for b, v in rec["totals"].items()
                  if "OVER" in v and "UNDER" in v and v["OVER"][0] == 2.5}
            if ou:
                g["booksou"] = ou
            bets = [odds_api.best_bettable(rec["h2h"], s) for s in "HDA"]
            if all(bets):
                g["book"]["1X2"] = ("/".join(sorted({b[1] for b in bets})), [b[0] for b in bets], [b[1] for b in bets])
            g["updated"] = rec.get("updated") or {}
            if rec.get("kalshi"):
                g["kalshi"] = rec["kalshi"]
            sb, sm = odds_api.sharp(rec["h2h"])
            if sm and all(s in sm for s in "HDA"):
                g["sharp"]["1X2"] = [sm[s] for s in "HDA"]
                g.setdefault("sharp_book", {})["h2h"] = sb
            ov, un = odds_api.best_bettable(rec["totals"], "OVER", 2.5), odds_api.best_bettable(rec["totals"], "UNDER", 2.5)
            if ov and un:
                g["book"]["OU25"] = (f"{ov[1]}/{un[1]}", [ov[0], un[0]], [ov[1], un[1]])
            sb, st = odds_api.sharp(rec["totals"])
            if st and "OVER" in st and st["OVER"][0] == 2.5:
                g["sharp"]["OU25"] = [st["OVER"][1], st["UNDER"][1]]
                g.setdefault("sharp_book", {})["totals"] = sb

    rows, cands, price_rows, kalshi_rows = [], [], [], []
    for (h, a), g in sorted(games.items(), key=lambda kv: kv[1]["date"]):
        mh, ma, why = av.multipliers(h, a) if av else (1.0, 1.0, [])
        lu_adj = None
        if lu_state is not None and av is not None and (h, a) in av.lineups:
            try:
                lu_adj = LU.live_adjust(lu_state, av.lineups[(h, a)])
            except Exception as e:
                print(f"  ! lineup model {h} v {a}: {e}")
            if lu_adj:
                mh, ma, info = lu_adj
                lu_adj = info
                why = [f"{t}: missing regulars " + ", ".join(n for n, _ in info[s]["missing"])
                       for s, t in (("home", h), ("away", a)) if info[s]["missing"]]
        mk = m.predict(h, a, g["comp"], g["neutral"], (mh, ma))
        mk_model = mk
        p = p_model = mk.x12()
        po_model = mk.total(2.5)[0]
        if "1X2" in g["sharp"]:
            p = O.pool(p, O.devig(g["sharp"]["1X2"], "shin"), w)
            mk = mk.reweight(p)
        po = mk.total(2.5)[0]
        if "OU25" in g["sharp"]:
            po = float(O.pool([po, 1 - po], O.devig(g["sharp"]["OU25"], "shin"), w)[0])
        row = {"date": g["date"], "comp": g["comp"], "home": h, "away": a, "pH": p[0], "pD": p[1], "pA": p[2],
               "pO25": po, "xg": f"{mk.lh:.2f}-{mk.la:.2f}", "inj": (mh, ma), "why": why, "best": ""}
        # sharp market alone (no model), to check that an edge comes from the price
        pk = O.devig(g["sharp"]["1X2"], "shin") if "1X2" in g["sharp"] else None
        pok = O.devig(g["sharp"]["OU25"], "shin") if "OU25" in g["sharp"] else None
        upd, sbk = g.get("updated") or {}, g.get("sharp_book") or {}

        def evaluate(market, s, i, price, book):
            if price is None or not np.isfinite(price) or price <= 1:
                return None
            pf = float(p[i] if market == "1X2" else (po if i == 0 else 1 - po))
            pmk = pk if market == "1X2" else pok
            pmk = None if pmk is None else float(pmk[i])
            mk_key = "h2h" if market == "1X2" else "totals"
            return {"market": market, "selection": s, "odds": float(price), "book": book,
                    "line": None if market == "1X2" else 2.5, "edge": O.edge(pf, price), "model_prob": pf, "p_win": pf,
                    "edge_mkt": None if pmk is None else O.edge(pmk, price), "p_win_mkt": pmk,
                    "stale": _stale(upd, mk_key, book, sbk.get(mk_key), pick_rules(cfg)), "_upd": upd.get((mk_key, book))}

        evals = []
        if "1X2" in g["book"] and "1X2" in g["sharp"]:
            b = g["book"]["1X2"]
            evals += [evaluate("1X2", s, i, b[1][i], b[2][i] if len(b) > 2 else b[0]) for i, s in enumerate("HDA")]
        if "OU25" in g["book"] and "OU25" in g["sharp"]:
            b = g["book"]["OU25"]
            evals += [evaluate("OU25", s, i, b[1][i], b[2][i] if len(b) > 2 else b[0])
                      for i, s in enumerate(["OVER", "UNDER"])]
        best = choose_best([e for e in evals if e], "soccer", cfg)
        if best:
            row["best"] = (f"{best['market']} {best['selection']} {O.to_american(best['odds'])} "
                           f"({best['book']}) {best['edge']:+.1%}")
            if not best.get("block"):
                cands.append({**best, "sport": "soccer", "comp": g["comp"], "game_date": g["date"].strftime("%Y-%m-%d"),
                              "home": h, "away": a, "fair_odds": 1 / best["model_prob"],
                              "stake": _stake(best["model_prob"], best["odds"], cfg),
                              "units": units_for(best["model_prob"], best["odds"], cfg), "source": "auto"})
        ko = g.get("kickoff")
        if g.get("kalshi") and ko is not None and pd.notna(ko):  # which Kalshi markets this game is
            kalshi_rows.append({"sport": "soccer", "game_date": g["date"].strftime("%Y-%m-%d"), "home": h, "away": a,
                                "kickoff": str(pd.Timestamp(ko)), **g["kalshi"]})
        if ko is None or pd.isna(ko) or pd.Timestamp(ko) > now_utc:  # price log: every book's pre-game prices
            hrs = _hrs_to(ko)
            allo = ([("1X2", s, i, v[i], bk) for bk, v in (g.get("books1x2") or {}).items() for i, s in enumerate("HDA")] +
                    [("OU25", s, i, v[i], bk) for bk, v in (g.get("booksou") or {}).items()
                     for i, s in enumerate(["OVER", "UNDER"])])
            for o in allo:
                e = evaluate(*o)
                if e is None:
                    continue
                rec = bool(best and not best.get("block") and e["book"] == best["book"]
                           and e["market"] == best["market"] and e["selection"] == best["selection"])
                price_rows.append({"sport": "soccer", "comp": g["comp"], "game_date": g["date"].strftime("%Y-%m-%d"),
                                   "home": h, "away": a, "market": e["market"], "selection": e["selection"],
                                   "line": e["line"], "odds": e["odds"], "book": e["book"],
                                   "kickoff": None if ko is None or pd.isna(ko) else str(ko), "hrs": hrs,
                                   "updated": None if e["_upd"] is None else str(e["_upd"]),
                                   "p_fair": e["p_win"], "p_mkt": e["p_win_mkt"], "edge": e["edge"],
                                   "edge_mkt": e["edge_mkt"], "rec": int(rec)})
        rows.append(row)
        if details is not None:
            ab = av.absences if av else {}
            details.append({
                "sport": "soccer", "comp": g["comp"], "home": h, "away": a, "date": g["date"],
                "kickoff": g.get("kickoff"), "xg": (mk_model.lh, mk_model.la), "mult": (mh, ma),
                "p_model": list(map(float, p_model)), "p_fair": list(map(float, p)),
                "po_model": float(po_model), "po_fair": float(po),
                "sharp_1x2": O.devig(g["sharp"]["1X2"], "shin").tolist() if "1X2" in g["sharp"] else None,
                "sharp_ou": O.devig(g["sharp"]["OU25"], "shin").tolist() if "OU25" in g["sharp"] else None,
                "books1x2": g["books1x2"], "booksou": g["booksou"],
                "btts": mk.btts(), "ah_line": mk.fair_ah_line(), "total_line": mk.fair_total_line(),
                "ou": {ln: mk.total(ln)[0] for ln in (1.5, 2.5, 3.5)},
                "scores": mk.top_scores(9),
                "absences": {t: [vars(x) for x in ab.get(t, [])] for t in (h, a)},
                "lineup": (av.lineups.get((h, a)) if av else None),
                "lineup_adj": lu_adj,
                "best": best,
                "pick": bool(best and not best.get("block")),
            })
    _log_prices(price_rows, kalshi_rows)
    return pd.DataFrame(rows), cands


# ============================================================================== NFL
def _nfl_abbr(name: str) -> str:
    if name not in odds_api.NFL_TEAMS:
        raise SystemExit(f"unknown NFL team {name}")
    return odds_api.NFL_TEAMS[name]


def _describe(b) -> str:
    if not b:
        return ""
    line = "" if b["line"] is None else f" {b['line']:+g}" if b["market"] == "SPREAD" else f" {b['line']:g}"
    return f"{b['market']} {b['selection']}{line} {O.to_american(b['odds'])} ({b['book']}) {b['edge']:+.1%}"


def nfl_slate(m, g, week=None, cfg=None, details: list | None = None):
    cfg = cfg or user_config()
    tc = tuned().get("nfl", {})
    ws, wt = tc.get("spread_blend_w", 0.1), tc.get("total_blend_w", 0.1)
    up = g[g["result"].isna()]
    if up.empty:
        return pd.DataFrame(), [], None
    season = up["season"].min()
    wk = week or int(up[up["season"] == season]["week"].min())
    games = g[(g["season"] == season) & (g["week"] == wk)]
    try:
        live = live_prices("NFL", _nfl_abbr, cfg)
    except Exception as e:
        print(f"  ! live odds NFL: {e}")
        live = {}
    rows, cands, price_rows, kalshi_rows = [], [], [], []
    from . import weather
    for r in games.itertuples():
        try:  # nflverse kickoff times are US Eastern
            ko = pd.Timestamp(f"{r.gameday} {r.gametime}").tz_localize("America/New_York").tz_convert(None)
        except Exception:
            ko = pd.Timestamp(r.gameday) + pd.Timedelta(hours=17)
        roof = getattr(r, "roof", None)
        if pd.notna(getattr(r, "wind", np.nan)):  # already played: observed conditions
            wind, temp = float(r.wind), float(r.temp) if pd.notna(r.temp) else None
        else:
            wind, temp = weather.kickoff_wind(r.home_team, ko, roof, bool(r.neutral))
        ih, ia = m.game_injuries(r, "home"), m.game_injuries(r, "away")
        mu, tau = m.expected(r.home_team, r.away_team, r.neutral,
                             (r.home_rest - r.away_rest) if pd.notna(r.home_rest) else 0, r.home_backup, r.away_backup,
                             home_qb=r.home_qb_id, away_qb=r.away_qb_id, inj_home=ih, inj_away=ia,
                             roof=roof, wind=wind)
        inj_pts = m.injury_points(ih, ia)
        lv = live.get((r.home_team, r.away_team))
        # market anchor: Pinnacle if available, else nflverse consensus
        mkt_mu, mkt_tau = r.spread_line, r.total_line
        offers = []  # (market, selection, line, price, book)
        if pd.notna(r.spread_line):  # consensus line, juice-adjusted
            ho = O.parse_odds(r.home_spread_odds) if pd.notna(r.home_spread_odds) else 1.909
            ao = O.parse_odds(r.away_spread_odds) if pd.notna(r.away_spread_odds) else 1.909
            mkt_mu = m.mu_from_market(-r.spread_line, O.devig([ho, ao], "mult")[0])
        if pd.notna(r.total_line):
            oo = O.parse_odds(r.over_odds) if pd.notna(r.over_odds) else 1.909
            uo = O.parse_odds(r.under_odds) if pd.notna(r.under_odds) else 1.909
            mkt_tau = m.tau_from_market(r.total_line, O.devig([oo, uo], "mult")[0])
        if lv:  # sharp book (Pinnacle) overrides the consensus anchor
            sb, sm = odds_api.sharp(lv["spreads"])
            if sm and "H" in sm and "A" in sm:
                mkt_mu = m.mu_from_market(sm["H"][0], O.devig([sm["H"][1], sm["A"][1]], "mult")[0])
            sb, st = odds_api.sharp(lv["totals"])
            if st and "OVER" in st and "UNDER" in st:
                mkt_tau = m.tau_from_market(st["OVER"][0], O.devig([st["OVER"][1], st["UNDER"][1]], "mult")[0])
            for b in odds_api.BETTABLE:
                for s, v in lv["spreads"].get(b, {}).items():
                    offers.append(("SPREAD", s, v[0], v[1], b))
                for s, v in lv["totals"].get(b, {}).items():
                    offers.append(("TOTAL", s, v[0], v[1], b))
                for s, v in lv["h2h"].get(b, {}).items():
                    offers.append(("ML", s, None, v, b))
        elif pd.notna(r.spread_line):
            ho = O.parse_odds(r.home_spread_odds) if pd.notna(r.home_spread_odds) else 1.909
            ao = O.parse_odds(r.away_spread_odds) if pd.notna(r.away_spread_odds) else 1.909
            offers += [("SPREAD", "H", -r.spread_line, ho, "consensus"), ("SPREAD", "A", r.spread_line, ao, "consensus")]
            if pd.notna(r.total_line):
                oo = O.parse_odds(r.over_odds) if pd.notna(r.over_odds) else 1.909
                uo = O.parse_odds(r.under_odds) if pd.notna(r.under_odds) else 1.909
                offers += [("TOTAL", "OVER", r.total_line, oo, "consensus"), ("TOTAL", "UNDER", r.total_line, uo, "consensus")]
            if pd.notna(r.home_moneyline) and pd.notna(r.away_moneyline):
                offers += [("ML", "H", None, O.parse_odds(r.home_moneyline), "consensus"),
                           ("ML", "A", None, O.parse_odds(r.away_moneyline), "consensus")]
        mu_b = ws * mu + (1 - ws) * mkt_mu if pd.notna(mkt_mu) else mu
        tau_b = wt * tau + (1 - wt) * mkt_tau if pd.notna(mkt_tau) else tau
        md, td = m.margin_dist(mu_b), m.total_dist(tau_b)
        # moneyline: anchor on the sharp moneyline itself (spread->win conversions overrate big dogs),
        # pooled with the model's own win probability at the backtest weight
        ml_ref = None
        if lv:
            _, sh = odds_api.sharp(lv["h2h"])
            if sh and "H" in sh and "A" in sh:
                ml_ref = O.devig([sh["H"], sh["A"]], "shin")[0]
        if ml_ref is None and pd.notna(r.home_moneyline) and pd.notna(r.away_moneyline):
            ml_ref = O.devig([O.parse_odds(r.home_moneyline), O.parse_odds(r.away_moneyline)], "shin")[0]
        pm = m.win_prob(mu)
        hw = float(O.pool([pm, 1 - pm], [ml_ref, 1 - ml_ref], ws)[0]) if ml_ref is not None else m.win_prob(mu_b)
        hl = 1 - hw
        # the sharp market's own numbers (no model), to check that an edge comes from the price
        md_k = m.margin_dist(mkt_mu) if pd.notna(mkt_mu) else None
        td_k = m.total_dist(mkt_tau) if pd.notna(mkt_tau) else None
        started = ko < pd.Timestamp.now(tz="UTC").tz_localize(None)
        upd = (lv or {}).get("updated") or {}
        sharp_bk = {k: odds_api.sharp(lv[k])[0] for k in ("spreads", "totals", "h2h")} if lv else {}

        def evaluate(mkt, s, line, price, book):
            if price is None or not np.isfinite(price) or price <= 1:
                return None
            if mkt == "SPREAD":
                f = m.line_probs(md, line, 1 if s == "H" else -1)
                k = m.line_probs(md_k, line, 1 if s == "H" else -1) if md_k is not None else None
            elif mkt == "TOTAL":
                f = m.line_probs(td, -line) if s == "OVER" else m.line_probs(td, line, -1)
                k = None if td_k is None else (m.line_probs(td_k, -line) if s == "OVER" else m.line_probs(td_k, line, -1))
            else:
                f = (hw, 0.0, hl) if s == "H" else (hl, 0.0, hw)
                k = None if ml_ref is None else ((ml_ref, 0.0, 1 - ml_ref) if s == "H" else (1 - ml_ref, 0.0, ml_ref))
            ex = lambda t: t[0] / (t[0] + t[2]) if t[0] + t[2] > 0 else 0.0  # win chance, pushes left out
            mk_key = {"SPREAD": "spreads", "TOTAL": "totals", "ML": "h2h"}[mkt]
            return {"market": mkt, "selection": r.home_team if s == "H" else r.away_team if s == "A" else s,
                    "line": line, "odds": float(price), "book": book,
                    "edge": f[0] * (price - 1) - f[2], "model_prob": ex(f), "p_win": ex(f),
                    "edge_mkt": None if k is None else k[0] * (price - 1) - k[2], "p_win_mkt": None if k is None else ex(k),
                    "stale": _stale(upd, mk_key, book, sharp_bk.get(mk_key), pick_rules(cfg)),
                    "_upd": upd.get((mk_key, book))}

        best = choose_best([e for e in (evaluate(*o) for o in offers) if e], "nfl", cfg)
        if lv and lv.get("kalshi"):  # which Kalshi markets this game is (for its closing prices)
            kalshi_rows.append({"sport": "nfl", "game_date": str(r.gameday)[:10], "home": r.home_team,
                                "away": r.away_team, "kickoff": str(ko), **lv["kalshi"]})
        if lv and not started and pd.isna(r.result):  # price log: every book's pre-game prices
            hrs = _hrs_to(ko)
            for b in set(lv["spreads"]) | set(lv["totals"]) | set(lv["h2h"]):
                allo = ([("SPREAD", s, v[0], v[1], b) for s, v in lv["spreads"].get(b, {}).items()] +
                        [("TOTAL", s, v[0], v[1], b) for s, v in lv["totals"].get(b, {}).items()] +
                        [("ML", s, None, v, b) for s, v in lv["h2h"].get(b, {}).items()])
                for o in allo:
                    e = evaluate(*o)
                    if e is None:
                        continue
                    rec = bool(best and not best.get("block") and e["book"] == best["book"] and e["market"] == best["market"]
                               and str(e["selection"]) == str(best["selection"]) and e["line"] == best["line"])
                    price_rows.append({"sport": "nfl", "comp": "NFL", "game_date": str(r.gameday)[:10],
                                       "home": r.home_team, "away": r.away_team, "market": e["market"],
                                       "selection": e["selection"], "line": e["line"], "odds": e["odds"], "book": b,
                                       "kickoff": str(ko), "hrs": hrs,
                                       "updated": None if e["_upd"] is None else str(e["_upd"]),
                                       "p_fair": e["p_win"], "p_mkt": e["p_win_mkt"], "edge": e["edge"],
                                       "edge_mkt": e["edge_mkt"], "rec": int(rec)})
        row = {"game": f"{r.away_team} @ {r.home_team}", "date": r.gameday,
               "QBs": f"{r.away_qb_name} / {r.home_qb_name}",
               "model": f"{r.home_team} {-mu:+.1f}", "market": f"{r.home_team} {-mkt_mu:+.1f}" if pd.notna(mkt_mu) else "",
               "inj pts": f"{inj_pts[1]:+.1f}/{inj_pts[0]:+.1f}", "model tot": round(tau, 1),
               "mkt tot": round(mkt_tau, 1) if pd.notna(mkt_tau) else "", "home win": f"{100 * hw / (hw + hl):.0f}%",
               "best bet": _describe(best)}
        if pd.notna(r.result):
            row["final"] = f"{int(r.away_score)}-{int(r.home_score)}"
        elif started:
            row["final"] = "in progress"
        elif best and not best.get("block"):
            cands.append({**best, "sport": "nfl", "comp": "NFL", "game_date": str(r.gameday),
                          "home": r.home_team, "away": r.away_team, "fair_odds": 1 / max(best["model_prob"], 1e-9),
                          "stake": _stake(best["model_prob"], best["odds"], cfg),
                              "units": units_for(best["model_prob"], best["odds"], cfg), "source": "auto"})
        rows.append(row)
        if details is not None:
            details.append(_nfl_detail(m, r, lv, mu, tau, mkt_mu, mkt_tau, mu_b, tau_b, md, td, hw, pm, ml_ref,
                                       ih, ia, inj_pts, best, ko, started, cfg))
            details[-1]["weather"] = {"roof": roof if isinstance(roof, str) else "retractable/unknown",
                                      "wind": wind, "temp": temp, "pts": 2 * m.weather_points(roof, wind)}
            add_view_probs(m, details[-1])
    _log_prices(price_rows, kalshi_rows)
    return pd.DataFrame(rows), cands, (season, wk)


def add_view_probs(m, d: dict) -> None:
    """For every book's spread and total, the cover / over chance under the model's OWN numbers (_model) and
    under the sharp market's (_mkt), next to the blended ones already stored. Used to pick the bet the model
    itself thinks is most likely and to explain each bet."""
    for key, mu_, tau_ in (("model", d.get("mu_model"), d.get("tau_model")),
                           ("mkt", d.get("mu_market"), d.get("tau_market"))):
        md_ = m.margin_dist(mu_) if mu_ is not None else None
        td_ = m.total_dist(tau_) if tau_ is not None else None
        for e in (d.get("books") or {}).values():
            s, t = e.get("spread"), e.get("total")
            if s and md_ is not None:
                hw_, hp_, _ = m.line_probs(md_, s["home_pt"], 1)
                aw_, ap_, _ = m.line_probs(md_, s["away_pt"], -1)
                s[f"home_cover_{key}"], s[f"away_cover_{key}"] = hw_, aw_
                s[f"home_push_{key}"], s[f"away_push_{key}"] = hp_, ap_
            if t and td_ is not None:
                ow, op_, _ = m.line_probs(td_, -t["pt"])
                uw, _, _ = m.line_probs(td_, t["pt"], -1)
                t[f"over_{key}"], t[f"under_{key}"], t[f"push_{key}"] = ow, uw, op_


def _nfl_detail(m, r, lv, mu, tau, mkt_mu, mkt_tau, mu_b, tau_b, md, td, hw, pm, ml_ref, ih, ia, inj_pts,
                best, ko, started, cfg):
    """Everything the game page shows: each book's numbers with the model's cover chance and EV."""
    from . import nfl_injuries as ni
    books = {}
    src = {}
    if lv:
        for b in set(lv["spreads"]) | set(lv["totals"]) | set(lv["h2h"]):
            src[b] = (lv["spreads"].get(b, {}), lv["totals"].get(b, {}), lv["h2h"].get(b, {}))
    elif pd.notna(r.spread_line):
        p = lambda x: O.parse_odds(x) if pd.notna(x) else 1.909
        src["consensus"] = ({"H": (-r.spread_line, p(r.home_spread_odds)), "A": (r.spread_line, p(r.away_spread_odds))},
                            {"OVER": (r.total_line, p(r.over_odds)), "UNDER": (r.total_line, p(r.under_odds))}
                            if pd.notna(r.total_line) else {},
                            {"H": p(r.home_moneyline), "A": p(r.away_moneyline)}
                            if pd.notna(r.home_moneyline) and pd.notna(r.away_moneyline) else {})
    for b, (sp, to, ml) in src.items():
        e = {}
        if "H" in sp and "A" in sp:
            hw_, hp_, hl_ = m.line_probs(md, sp["H"][0], 1)
            aw_, ap_, al_ = m.line_probs(md, sp["A"][0], -1)
            e["spread"] = {"home_pt": sp["H"][0], "home_price": sp["H"][1], "away_pt": sp["A"][0], "away_price": sp["A"][1],
                           "home_cover": hw_, "away_cover": aw_, "push": hp_,
                           "home_ev": hw_ * (sp["H"][1] - 1) - hl_, "away_ev": aw_ * (sp["A"][1] - 1) - al_}
        if "OVER" in to and "UNDER" in to:
            ow, op_, ol = m.line_probs(td, -to["OVER"][0])
            uw, up_, ul = m.line_probs(td, to["UNDER"][0], -1)
            e["total"] = {"pt": to["OVER"][0], "over_price": to["OVER"][1], "under_price": to["UNDER"][1],
                          "over": ow, "under": uw, "push": op_,
                          "over_ev": ow * (to["OVER"][1] - 1) - ol, "under_ev": uw * (to["UNDER"][1] - 1) - ul}
        if "H" in ml and "A" in ml:
            e["ml"] = {"home_price": ml["H"], "away_price": ml["A"],
                       "home_ev": hw * ml["H"] - 1, "away_ev": (1 - hw) * ml["A"] - 1}
        books[b] = e
    top = sorted(md.items(), key=lambda kv: -kv[1])[:8]
    inj = {}
    for team in (r.home_team, r.away_team):
        if team in ni.LIVE:
            inj[team] = ni.LIVE[team]
        elif ni.DETAIL is not None:
            d = ni.DETAIL[(ni.DETAIL["season"] == r.season) & (ni.DETAIL["week"] == r.week) & (ni.DETAIL["team"] == team)]
            inj[team] = d.rename(columns={"pos": "pos"}).to_dict("records")
        else:
            inj[team] = []
    qbv = lambda q: float(m.qb.get(q, 0.0)) if isinstance(q, str) else 0.0
    return {
        "sport": "nfl", "comp": "NFL", "home": r.home_team, "away": r.away_team, "date": pd.Timestamp(r.gameday),
        "kickoff": ko, "started": bool(started), "final": (f"{int(r.away_score)}-{int(r.home_score)}" if pd.notna(r.result) else None),
        "mu_model": float(mu), "tau_model": float(tau), "mu_market": float(mkt_mu) if pd.notna(mkt_mu) else None,
        "tau_market": float(mkt_tau) if pd.notna(mkt_tau) else None, "mu_fair": float(mu_b), "tau_fair": float(tau_b),
        "win_fair": float(hw), "win_model": float(pm), "win_market": float(ml_ref) if ml_ref is not None else None,
        "books": books, "top_margins": [(int(k), float(v)) for k, v in top],
        "qbs": {"home": (r.home_qb_name, qbv(r.home_qb_id)), "away": (r.away_qb_name, qbv(r.away_qb_id))},
        "injuries": inj, "inj_pts": {"home": float(inj_pts[0]), "away": float(inj_pts[1])},
        "rest": {"home": r.home_rest, "away": r.away_rest},
        "best": best, "pick": bool(best and not best.get("block") and not started and pd.isna(r.result)),
    }
