"""Command line interface.

  python -m betmodel soccer predict Arsenal Chelsea
  python -m betmodel soccer predict Inter "Real Madrid" --comp UCL --odds +180 +240 +150
  python -m betmodel soccer fixtures --comp EPL,LALIGA,SERIEA,UCL,UEL --days 7
  python -m betmodel soccer ratings --league SERIEA
  python -m betmodel nfl predict KC BUF --spread -2.5 --total 48.5 --ml -135 +115
  python -m betmodel nfl week
  python -m betmodel nfl ratings
  python -m betmodel soccer backtest | python -m betmodel nfl backtest
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from . import odds as O
from .theme import APP_NAME

warnings.filterwarnings("ignore")
CFG_PATH = Path(__file__).resolve().parent / "tuned.json"


def cfg() -> dict:
    return json.loads(CFG_PATH.read_text()) if CFG_PATH.exists() else {}


def pct(x):
    return f"{100 * x:5.1f}%"


def price_row(label, p, offered=None, kelly_frac=0.25):
    dec, am = O.fair_odds(p)
    s = f"  {label:<22} {pct(p)}   fair {dec:6.2f} ({am:>5})"
    if offered is not None:
        e = O.edge(p, offered)
        k = O.kelly(p, offered, kelly_frac)
        flag = "  <-- VALUE" if e > 0.02 else ""
        s += f"   offered {offered:6.2f} ({O.to_american(offered):>5})  edge {e:+6.1%}  stake {k:.1%}{flag}"
    return s


# ============================================================================ SOCCER
def _soccer_model(args):
    from . import data_soccer as ds
    from .soccer import SoccerModel, SoccerParams
    df = ds.load_all(verbose=not args.quiet)
    params = SoccerParams(**cfg().get("soccer", {}).get("params", {}))
    return SoccerModel(params).fit(df), df


def soccer_predict(args):
    from . import data_soccer as ds
    m, df = _soccer_model(args)
    home, away = ds.resolve_team(args.home, m.teams), ds.resolve_team(args.away, m.teams)
    comp = ds.COMP_ALIASES.get(args.comp.upper(), args.comp.upper()) if args.comp else None
    mult, why = (1.0, 1.0), []
    if not args.no_injuries:
        from . import soccer_injuries as si
        av = si.load(df, m.teams, [comp or m.team_comp.get(home, "EPL")])
        mh, ma, why = av.multipliers(home, away)
        mult = (mh, ma)
    mk = m.predict(home, away, comp, args.neutral, mult)
    w = cfg().get("soccer", {}).get("blend_w", 0.25)
    p = mk.x12()
    print(f"\n{home} vs {away}   [{comp or m.team_comp.get(home, '?')}{', neutral' if args.neutral else ''}]")
    if why or mult != (1.0, 1.0):
        print(f"Availability adjustment: {home} xG x{mult[0]:.2f}, {away} xG x{mult[1]:.2f}")
        for line in why:
            print("   " + line)
    print(f"Model: expected goals {mk.lh:.2f} - {mk.la:.2f}   fair AH line (home) {mk.fair_ah_line():+.2f}   "
          f"fair total {mk.fair_total_line():.2f}")
    offered = [O.parse_odds(x) for x in args.odds] if args.odds else None
    if offered or args.sharp:
        ref = [O.parse_odds(x) for x in (args.sharp or args.odds)]
        pm = O.devig(ref, "shin")
        pb = O.pool(p, pm, w)
        print(f"\nMarket (de-vigged {'sharp' if args.sharp else 'offered'} odds): "
              f"{pct(pm[0])} {pct(pm[1])} {pct(pm[2])}   -> blended with model weight {w:.2f}")
        print("(all markets below are derived from the blended prices)")
        p_use = pb
        mk = mk.reweight(pb)
    else:
        p_use = p
    print("\n1X2" + ("  (blended)" if offered or args.sharp else "  (model only - pass --odds to blend with the market)"))
    for i, lab in enumerate(["Home", "Draw", "Away"]):
        print(price_row(lab, p_use[i], offered[i] if offered else None))
    if offered or args.sharp:
        print("  model-only:            " + "  ".join(pct(x) for x in p))
    print("\nDouble chance / DNB")
    q = p_use
    print(price_row("Home or Draw (1X)", q[0] + q[1]))
    print(price_row("Away or Draw (X2)", q[2] + q[1]))
    print(price_row("Home DNB", q[0] / (q[0] + q[2])))
    print(price_row("Away DNB", q[2] / (q[0] + q[2])))
    print("\nTotals (over)")
    for ln in [1.5, 2.0, 2.25, 2.5, 2.75, 3.0, 3.5]:
        wn, ps, ls = mk.total(ln)
        fair = mk.fair_asian(wn, ps, ls)
        print(f"  Over {ln:<5} win {pct(wn)} push {pct(ps)}   fair over {fair:5.2f} ({O.to_american(fair):>5})   "
              f"fair under {mk.fair_asian(ls, ps, wn):5.2f} ({O.to_american(mk.fair_asian(ls, ps, wn)):>5})")
    if args.ou:
        ln, oo, uo = float(args.ou[0]), O.parse_odds(args.ou[1]), O.parse_odds(args.ou[2])
        wn, ps, ls = mk.total(ln)
        print(f"  -> Over {ln} @ {oo:.2f}: EV {wn * (oo - 1) - ls:+.1%}   Under @ {uo:.2f}: EV {ls * (uo - 1) - wn:+.1%}")
    print("\nAsian handicap (home)")
    for ln in [-1.5, -1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 1.0, 1.5]:
        wn, ps, ls = mk.handicap(ln)
        print(f"  Home {ln:+5.2f}  win {pct(wn)} push {pct(ps)}   fair home {mk.fair_asian(wn, ps, ls):5.2f}   "
              f"fair away {mk.fair_asian(ls, ps, wn):5.2f}")
    if args.ah:
        ln, ho, ao = float(args.ah[0]), O.parse_odds(args.ah[1]), O.parse_odds(args.ah[2])
        wn, ps, ls = mk.handicap(ln)
        print(f"  -> Home {ln:+} @ {ho:.2f}: EV {wn * (ho - 1) - ls:+.1%}   Away @ {ao:.2f}: EV {ls * (ao - 1) - wn:+.1%}")
    print("\nOther")
    print(price_row("Both teams to score", mk.btts()))
    print(price_row("BTTS - No", 1 - mk.btts()))
    print(price_row(f"{home} over 1.5", mk.team_total("home", 1.5)))
    print(price_row(f"{away} over 1.5", mk.team_total("away", 1.5)))
    print("\nMost likely scores: " + ", ".join(f"{a}-{b} {100 * q:.1f}%" for a, b, q in mk.top_scores(8)))


def _print_soccer_slate(rows: pd.DataFrame):
    if rows.empty:
        print("No soccer fixtures in range.")
        return
    t = pd.DataFrame({
        "date": rows["date"].dt.strftime("%a %d %b"), "comp": rows["comp"],
        "match": rows["home"] + " v " + rows["away"], "xG": rows["xg"],
        "H": rows["pH"].map(pct), "D": rows["pD"].map(pct), "A": rows["pA"].map(pct),
        "O2.5": rows["pO25"].map(pct),
        "fair H/D/A": [f"{O.to_american(1 / a)}/{O.to_american(1 / b)}/{O.to_american(1 / c)}"
                       for a, b, c in zip(rows.pH, rows.pD, rows.pA)],
        "inj adj": [f"x{h:.2f}/x{a:.2f}" if (h, a) != (1.0, 1.0) else "" for h, a in rows["inj"]],
        "best price vs fair": rows["best"]})
    print(t.to_string(index=False))


def _soccer_comps(s):
    from . import data_soccer as ds
    return [ds.COMP_ALIASES.get(c.strip().upper(), c.strip().upper()) for c in s.split(",")]


def soccer_fixtures(args):
    from . import slate, soccer_injuries as si, picks
    m, df = _soccer_model(args)
    comps = _soccer_comps(args.comp)
    av = si.load(df, m.teams, comps)
    rows, cands = slate.soccer_slate(m, df, comps, args.days, av)
    _print_soccer_slate(rows)
    for n in av.notes:
        print("  note:", n)
    if args.log:
        new = picks.log(cands)
        print(f"\nLogged {len(new)} new pick(s) (min edge {slate.user_config()['min_edge']['soccer']:.0%}).")


def soccer_ratings(args):
    from . import data_soccer as ds
    m, _ = _soccer_model(args)
    lg = ds.COMP_ALIASES.get(args.league.upper(), args.league.upper()) if args.league else None
    r = m.ratings(lg).head(args.top)
    r["attack xG"] = np.exp(m.mu + r["attack"])
    r["conceded xG"] = np.exp(m.mu - r["defence"])
    print("Rating = expected goal difference per match vs an average European team on neutral ground\n")
    print(r[["team", "league", "rating", "attack xG", "conceded xG"]].round(2).to_string())
    if not lg:
        print("\nLeague average strength:")
        for k, v in sorted(m.league_strength.items(), key=lambda x: -x[1]):
            print(f"  {k:<10} {v:+.2f}")


def soccer_backtest(args):
    from . import data_soccer as ds, backtest_soccer as B
    from .soccer import SoccerParams
    df = ds.load_all(verbose=not args.quiet)
    params = SoccerParams(**cfg().get("soccer", {}).get("params", {}))
    bt = B.walk_forward(df, args.start, args.end, params, verbose=not args.quiet)
    print(B.report(B.evaluate(bt)))


# ============================================================================ NFL
def _nfl_model(args):
    from . import nfl
    g = nfl.load_games()
    return nfl.NFLModel(nfl.NFLParams(**cfg().get("nfl", {}).get("params", {}))).fit(g), g


def _nfl_lines(m, mu, tau, spread=None, spread_odds=("-110", "-110"), total=None, tot_odds=("-110", "-110"),
               ml=None, home="Home", away="Away"):
    md, td = m.margin_dist(mu), m.total_dist(tau)
    ph = m.win_prob(mu)
    print(f"  Moneyline   {home:<4} {pct(ph)} fair {O.to_american(1 / ph):>5}   {away:<4} {pct(1 - ph)} fair {O.to_american(1 / (1 - ph)):>5}")
    if ml:
        hd, ad = O.parse_odds(ml[0]), O.parse_odds(ml[1])
        print(f"     offered {O.to_american(hd)} / {O.to_american(ad)}:  {home} EV {O.edge(ph, hd):+.1%}  "
              f"{away} EV {O.edge(1 - ph, ad):+.1%}   (Kelly {O.kelly(ph, hd):.1%} / {O.kelly(1 - ph, ad):.1%})")
    print(f"  Fair spread {home} {-mu:+.1f}    fair total {tau:.1f}")
    lines = sorted({round(-mu * 2) / 2 + d for d in (-3, -2, -1, -0.5, 0, 0.5, 1, 2, 3)} | ({spread} if spread is not None else set()))
    print("  Spread (home)   cover   push    fair price")
    for ln in lines:
        w_, p_, l_ = m.line_probs(md, ln)
        fair = 1 + l_ / w_ if w_ > 0 else np.inf
        tag = ""
        if spread is not None and abs(ln - spread) < 1e-9:
            hd, ad = O.parse_odds(spread_odds[0]), O.parse_odds(spread_odds[1])
            tag = f"   <- offered: {home} EV {w_ * (hd - 1) - l_:+.1%}, {away} EV {l_ * (ad - 1) - w_:+.1%}"
        print(f"   {home} {ln:+5.1f}     {pct(w_)}  {pct(p_)}   {O.to_american(fair):>5}{tag}")
    tl = sorted({round(tau * 2) / 2 + d for d in (-3, -1.5, -0.5, 0, 0.5, 1.5, 3)} | ({total} if total is not None else set()))
    print("  Total           over    push    fair over")
    for ln in tl:
        w_, p_, l_ = m.line_probs(td, -ln)
        fair = 1 + l_ / w_ if w_ > 0 else np.inf
        tag = ""
        if total is not None and abs(ln - total) < 1e-9:
            od, ud = O.parse_odds(tot_odds[0]), O.parse_odds(tot_odds[1])
            tag = f"   <- offered: over EV {w_ * (od - 1) - l_:+.1%}, under EV {l_ * (ud - 1) - w_:+.1%}"
        print(f"   {ln:5.1f}          {pct(w_)}  {pct(p_)}   {O.to_american(fair):>5}{tag}")


def _nfl_game_row(g, home, away):
    """The scheduled (unplayed) game between these teams, if it's on the schedule."""
    r = g[(g["home_team"] == home) & (g["away_team"] == away) & g["result"].isna()]
    return next(r.itertuples(), None)


def nfl_predict(args):
    m, g = _nfl_model(args)
    home, away = args.home.upper(), args.away.upper()
    for t in (home, away):
        if t not in m.teams:
            raise SystemExit(f"Unknown team {t}. Use abbreviations: {', '.join(m.teams)}")
    hq = m.find_qb(args.home_qb) if args.home_qb else None
    aq = m.find_qb(args.away_qb) if args.away_qb else None
    row = _nfl_game_row(g, home, away)
    ih = ia = None
    if row is not None and not args.no_injuries:
        ih, ia = m.game_injuries(row, "home"), m.game_injuries(row, "away")
        hq = hq or (row.home_qb_id if isinstance(row.home_qb_id, str) else None)
        aq = aq or (row.away_qb_id if isinstance(row.away_qb_id, str) else None)
    print(f"QBs: {away} {m.qb_names.get(aq or m.team_qb.get(away))}  |  {home} {m.qb_names.get(hq or m.team_qb.get(home))}")
    if ih is not None:
        hp, ap = m.injury_points(ih, ia)
        print(f"Injury report adjustment: {home} {hp:+.1f} pts, {away} {ap:+.1f} pts "
              f"(week {row.week} report; see `nfl injuries`)")
    mu, tau = m.expected(home, away, args.neutral, args.rest_diff, int(args.home_backup_qb), int(args.away_backup_qb),
                         args.adj_home, args.adj_away, hq, aq, ih, ia)
    c = cfg().get("nfl", {})
    if args.spread is not None and c.get("spread_blend_w") is not None:
        wb = c["spread_blend_w"]
        mu_b = wb * mu + (1 - wb) * (-args.spread)
        print(f"(model margin {mu:+.1f} blended with market {-args.spread:+.1f} at model weight {wb:.2f} -> {mu_b:+.1f})")
        mu = mu_b
    if args.total is not None and c.get("total_blend_w") is not None:
        wb = c["total_blend_w"]
        tau = wb * tau + (1 - wb) * args.total
    print(f"\n{away} @ {home}{' (neutral)' if args.neutral else ''}:  expected margin {home} {mu:+.1f}, total {tau:.1f}")
    _nfl_lines(m, mu, tau, args.spread, args.spread_odds, args.total, args.total_odds, args.ml, home, away)


def _print_nfl_slate(m, rows, sw):
    if rows.empty:
        print("No upcoming NFL games on the schedule.")
        return
    print(f"\nNFL {sw[0]} week {sw[1]}   ('inj pts' = injury-report effect on away/home score)\n")
    print(rows.fillna("").to_string(index=False))


def nfl_week(args):
    from . import slate, picks
    m, g = _nfl_model(args)
    rows, cands, sw = slate.nfl_slate(m, g, args.week)
    _print_nfl_slate(m, rows, sw)
    if args.log:
        new = picks.log(cands)
        print(f"\nLogged {len(new)} new pick(s) (min edge {slate.user_config()['min_edge']['nfl']:.0%}).")


def nfl_ratings(args):
    m, _ = _nfl_model(args)
    r = m.ratings()
    print(f"Points vs average team, neutral field.  HFA {m.hfa:+.2f}, rest {m.beta['rest']:+.2f}/day, "
          f"backup QB {m.beta['backup_qb']:+.2f}")
    if getattr(m, "beta_inj", None):
        print("Injury costs per starter out (points): " +
              ", ".join(f"{k[4:]} {v:+.2f}" for k, v in m.beta_inj.items()))
    print()
    print(r.round(2).to_string())


def nfl_injuries(args):
    from . import nfl_injuries as ni
    m, g = _nfl_model(args)
    d = ni.DETAIL
    up = g[g["result"].isna()]
    if d is None or d.empty or up.empty:
        print("No injury report data available.")
        return
    season, wk = int(up["season"].min()), int(up[up["season"] == up["season"].min()]["week"].min())
    cur = d[(d["season"] == season) & (d["week"] == wk)].sort_values(["team", "snap_share"], ascending=[True, False])
    if args.team:
        cur = cur[cur["team"] == args.team.upper()]
    print(f"NFL {season} week {wk} injury report (players with >5% recent snap share):\n")
    print(cur[["team", "player", "pos", "status", "snap_share"]].to_string(index=False))


def nfl_backtest(args):
    from . import nfl, backtest_nfl as B
    g = nfl.load_games()
    seasons = [int(s) for s in args.seasons.split(",")]
    bt = B.walk_forward(g, seasons, nfl.NFLParams(**cfg().get("nfl", {}).get("params", {})), verbose=not args.quiet)
    print(B.report(B.evaluate(bt)))


def history_cmd(args):
    from . import history as H
    if args.action == "build":
        H.build(lambda f, m: print(f"\r  {m:<70}", end="", flush=True))
        print()
    d = H.frame()
    if d.empty:
        print("No history yet: python -m betmodel history build")
        return
    for season, g in d[d.sport == "nfl"].groupby("season"):
        s = H.nfl_stats(g)
        print(f"NFL {season}: {s['n']:>3} games | winner model {s['model_winner']:.1%} books {s['books_winner']:.1%} | "
              f"margin miss model {s['model_mae']:.2f} books {s['books_mae']:.2f} | leans {s['lean_w']}-{s['lean_l']} "
              f"({s['lean_units']:+.1f}u)")
    for (season, comp), g in d[d.sport == "soccer"].groupby(["season", "comp"]):
        s = H.soccer_stats(g)
        print(f"{comp:<6} {season}: {s['n']:>3} matches | favourite model {s['model_fav']:.1%} books {s['books_fav']:.1%} | "
              f"log-loss model {s['model_ll']:.4f} books {s['books_ll']:.4f}")


# ============================================================================ DAILY + PICKS
def daily(args):
    """Update everything, grade finished picks, print today's slates and log new picks."""
    from . import slate, picks, soccer_injuries as si, odds_api
    c = slate.user_config()
    print("=" * 100)
    print(f"{APP_NAME} daily run  {pd.Timestamp.now():%a %d %b %Y %H:%M}   bankroll {c['bankroll']}  "
          f"(Odds API key: {'yes' if odds_api.key() else 'no'}, API-Football key: "
          f"{'yes' if c.get('api_football_key') else 'no'})")
    print("=" * 100)
    nm, g = _nfl_model(args)
    sm, df = _soccer_model(args)
    msgs = picks.settle(df, g)
    if msgs:
        print("\nGraded picks:")
        for s in msgs:
            print("  " + s)
    new_all = []
    if not args.soccer_only:
        rows, cands, sw = slate.nfl_slate(nm, g, None, c)
        _print_nfl_slate(nm, rows, sw)
        new_all += picks.log(cands)
    if not args.nfl_only:
        comps = c["soccer_comps"]
        av = si.load(df, sm.teams, comps)
        print(f"\nSOCCER next {c['soccer_days']} days ({', '.join(comps)})\n")
        rows, cands = slate.soccer_slate(sm, df, comps, c["soccer_days"], av, c)
        _print_soccer_slate(rows)
        for n in av.notes:
            print("  note:", n)
        new_all += picks.log(cands)
    print("\nNEW PICKS LOGGED" if new_all else "\nNo new picks cleared the minimum edge "
          f"(soccer {c['min_edge']['soccer']:.0%}, NFL {c['min_edge']['nfl']:.0%}).")
    for p in new_all:
        ln = "" if p.get("line") is None else f" {p['line']:+g}" if p["market"] == "SPREAD" else f" {p['line']:g}"
        print(f"  {p['game_date']}  {p['away']} @ {p['home'] if p['sport'] == 'nfl' else ''}".rstrip() if p["sport"] == "nfl"
              else f"  {p['game_date']}  {p['home']} v {p['away']}", end="")
        print(f"   {p['market']} {p['selection']}{ln} @ {p['odds']:.2f} ({O.to_american(p['odds'])}, {p['book']})  "
              f"edge {p['edge']:+.1%}  stake {p['stake']:.2f}")
    cr = odds_api.remaining_credits()
    if cr is not None:
        print(f"\nOdds API credits remaining this month: {cr}")
    print("\n" + picks.report(last=10))


def picks_cmd(args):
    from . import picks, db
    if args.action == "settle":
        from . import data_soccer as ds, nfl
        msgs = picks.settle(ds.load_all(verbose=False), nfl.load_games())
        print("\n".join(msgs) or "Nothing to settle.")
    elif args.action == "export":
        out = Path(args.out)
        db.picks().to_csv(out, index=False)
        print(f"Wrote {out.resolve()}")
    elif args.action == "delete":
        db.delete_pick(args.id)
        print(f"Deleted pick #{args.id}")
    elif args.action == "add":
        dec = O.parse_odds(args.odds)
        ok = db.add_pick({"sport": args.sport, "comp": args.comp, "game_date": args.date, "home": args.home,
                          "away": args.away, "market": args.market.upper(), "selection": args.selection.upper()
                          if args.market.upper() in ("1X2", "OU25", "TOTAL") else args.selection,
                          "line": args.line, "odds": dec, "book": args.book, "model_prob": None, "fair_odds": None,
                          "edge": None, "stake": args.stake, "source": "manual"})
        print("Logged." if ok else "Already logged.")
    else:
        print(picks.report(last=args.last))


def _phone_addresses(port: int) -> list[str]:
    """Addresses other devices can use: home Wi-Fi IPs and the Tailscale (100.x) address if installed."""
    import socket
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    try:  # the address used for outbound traffic (usually the Wi-Fi one)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    out = []
    for ip in sorted(ips):
        if ip.startswith("127."):
            continue
        kind = "Tailscale - works from anywhere" if ip.startswith("100.") else "home Wi-Fi only"
        out.append(f"  http://{ip}:{port}    ({kind})")
    out.append(f"  http://{socket.gethostname().lower()}:{port}    (Tailscale name, if MagicDNS is on)")
    return out


def _port_busy(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _is_betmodel(port: int) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3) as r:
            page = r.read(20000).decode("utf-8", "ignore")
            return "betmodel" in page or APP_NAME in page  # older copies say betmodel, newer ones the app name
    except Exception:
        return False


def _listener_pid(port: int) -> int | None:
    import subprocess
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[1].endswith(f":{port}") and parts[3].upper() == "LISTENING":
            return int(parts[4])
    return None


def _cmdline(pid: int) -> str:
    import subprocess
    try:
        return subprocess.run(["powershell", "-NoProfile", "-Command",
                               f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"],
                              capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return ""


def _free_port(port: int) -> int:
    """If an older betmodel is still running on this port, offer to replace it (you confirm in this window);
    if anything else holds the port, use the next free one."""
    import subprocess, time
    if not _port_busy(port):
        return port
    if _is_betmodel(port):
        print(f"An older copy of {APP_NAME} is still running at http://127.0.0.1:{port} (probably another black window).")
        print("It doesn't have the latest changes.")
        try:
            ans = input("Close it and start the latest version? [Y/n] ").strip().lower()
        except EOFError:
            ans = "n"
        if ans in ("", "y", "yes"):
            pid = _listener_pid(port)
            if pid and "betmodel" in _cmdline(pid):
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
                for _ in range(20):
                    if not _port_busy(port):
                        print("Closed the old one.\n")
                        return port
                    time.sleep(0.25)
            print("Couldn't close it; close the other black window yourself when you can.")
    for p in range(port + 1, port + 40):
        if not _port_busy(p):
            print(f"Port {port} is in use, so this copy uses port {p}.\n")
            return p
    return port


def app_cmd(args):
    """Open the Shiny app in the browser. The model only runs when you press Run model."""
    from shiny import run_app
    args.port = _free_port(args.port)
    if getattr(args, "no_publish", False):  # a test copy: save the phone page locally, never upload it
        os.environ["BETMODEL_NO_PUBLISH"] = "1"
        print("Publishing to GitHub is off for this copy (--no-publish).")
    from . import cloud
    if cloud.enabled():  # the runs happen on GitHub: start from their newest results
        print("Getting the newest results from GitHub...")
        try:
            print("  copied them to this PC." if cloud.pull() else "  this PC already has them.")
        except Exception as e:
            print(f"  couldn't reach GitHub ({e}); showing this PC's copy for now.")
    if args.phone:
        print(f"{APP_NAME} is running and reachable from your phone. Open one of these on the phone:\n")
        print("\n".join(_phone_addresses(args.port)))
        print("\nKeep this window open and the PC awake. If Windows asks, allow Python on Private networks.\n")
        host = "0.0.0.0"
    else:
        print(f"Opening {APP_NAME} at http://127.0.0.1:{args.port}  (close this window to stop it)")
        host = "127.0.0.1"
    run_app("betmodel.app:app", host=host, port=args.port, launch_browser=not args.no_browser)


KEY_FIELDS = {"odds": "odds_api_key", "apifootball": "api_football_key", "github": "github_token",
              "repo": "github_repo"}


def keys_cmd(args):
    """Save API keys into config.json and test them with requests that don't use up the quota."""
    import urllib.request
    from . import slate
    path = slate.ROOT / "config.json"
    conf = json.loads(path.read_text()) if path.exists() else dict(slate.DEFAULTS)
    if args.action in ("set", "save"):
        if args.which not in KEY_FIELDS or not args.key:
            raise SystemExit("Usage: betmodel keys set odds YOURKEY   |   betmodel keys set apifootball YOURKEY")
        conf[KEY_FIELDS[args.which]] = args.key.strip()
        path.write_text(json.dumps(conf, indent=2))
        print(f"Saved {args.which} key to {path.name}.")
        if args.action == "save":  # used by keys.bat, which checks once at the end
            return
        print("Checking it...\n")
    # ---- check both keys
    k = conf.get("odds_api_key") or ""
    if not k:
        print("Odds API:     no key set   (betmodel keys set odds YOURKEY)")
    else:
        try:
            url = f"https://api.the-odds-api.com/v4/sports?apiKey={k}"  # free call, uses no credits
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "betmodel"}), timeout=20) as r:
                sports = json.loads(r.read())
                left, used = r.headers.get("x-requests-remaining"), r.headers.get("x-requests-used")
            have = {s["key"] for s in sports}
            from .odds_api import SPORT_KEYS
            want = ["NFL", "EPL", "LALIGA", "SERIEA", "UCL", "UEL"]
            in_season = [c for c in want if SPORT_KEYS[c] in have]
            print(f"Odds API:     OK - {left} credits left this month ({used} used). "
                  f"In season now: {', '.join(in_season) or 'none'}")
        except urllib.error.HTTPError as e:
            print(f"Odds API:     REJECTED ({e.code}) - check the key was copied exactly")
        except Exception as e:
            print(f"Odds API:     could not connect ({e})")
    # GitHub Pages (snapshot page you can open with the PC off)
    tok, repo = conf.get("github_token") or "", conf.get("github_repo") or ""
    if not (tok and repo):
        print("GitHub:       not set up  (optional - lets you view the latest run from anywhere)")
    else:
        try:
            req = urllib.request.Request(f"https://api.github.com/repos/{repo}", headers={
                "Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json", "User-Agent": "betmodel"})
            info = json.loads(urllib.request.urlopen(req, timeout=20).read())
            can_write = (info.get("permissions") or {}).get("push")
            owner, name = repo.split("/", 1)
            print(f"GitHub:       OK - repo {info.get('full_name')} ({'can upload' if can_write else 'NO upload permission'})."
                  f" Page: https://{owner.lower()}.github.io/{name}/")
            if not info.get("has_pages"):
                print("              Turn on Pages: repo Settings > Pages > Deploy from branch > main / (root) > Save")
        except urllib.error.HTTPError as e:
            print(f"GitHub:       REJECTED ({e.code}) - check the token and that the repo is 'yourname/reponame'")
        except Exception as e:
            print(f"GitHub:       could not connect ({e})")
    k = conf.get("api_football_key") or ""
    if not k:
        return
    try:
        def af(path_):
            req = urllib.request.Request(f"https://v3.football.api-sports.io/{path_}", headers={"x-apisports-key": k})
            return json.loads(urllib.request.urlopen(req, timeout=20).read())
        st = af("status")  # does not count against the daily quota
        if st.get("errors"):
            print(f"API-Football: REJECTED - {st['errors']}")
            return
        resp = st.get("response") or {}
        sub, req_ = resp.get("subscription", {}), resp.get("requests", {})
        print(f"API-Football: OK - plan {sub.get('plan')}, {req_.get('current')}/{req_.get('limit_day')} requests used today")
        # 1 request: ask for this season's data directly - league coverage flags don't reflect plan limits
        today = pd.Timestamp.today()
        season = today.year if today.month >= 7 else today.year - 1
        probe = af(f"injuries?league=39&season={season}&date={today:%Y-%m-%d}")
        if probe.get("errors"):
            print(f"              current season NOT available on this plan: {probe['errors']}")
            print("              -> the model keeps working; soccer injuries fall back to FPL (Premier League only)")
            return
        print(f"              season {season}: injuries and lineups available")
    except urllib.error.HTTPError as e:
        print(f"API-Football: REJECTED ({e.code}) - check the key was copied exactly")
    except Exception as e:
        print(f"API-Football: could not connect ({e})")


def pricelog_cmd(args):
    """Grade logged prices against the closing line (free data), then show which edges beat it."""
    from . import data_soccer as ds, nfl, pricelog, slate
    g = nfl.load_games()
    nm = nfl.NFLModel(nfl.NFLParams(**slate.tuned().get("nfl", {}).get("params", {}))).fit(g)
    n = pricelog.grade(nm, g, ds.load_all(verbose=False))
    print(f"Graded {n:,} price(s) just now.\n")
    print(pricelog.text_report())


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["cloud"]:  # its own sub-commands (and their --help)
        from . import cloud
        return cloud.main(argv[1:] or ["--help"])
    ap = argparse.ArgumentParser(prog="betmodel", description="NFL + European soccer pricing models")
    ap.add_argument("-q", "--quiet", action="store_true")
    sp = ap.add_subparsers(dest="sport", required=True)

    p = sp.add_parser("daily", help="grade picks, price today's NFL + soccer slates, log new picks")
    p.add_argument("--nfl-only", action="store_true"); p.add_argument("--soccer-only", action="store_true")
    p.set_defaults(fn=daily)

    p = sp.add_parser("scores", help="FREE update: ESPN scores, grade picks, compare with the model (no API credits)")
    p.set_defaults(fn=lambda a: print("\n".join(__import__("betmodel.engine", fromlist=["x"]).refresh_scores().notes)))

    p = sp.add_parser("history", help="model predictions vs actual results, past seasons (build: replay them, free)")
    p.add_argument("action", nargs="?", default="show", choices=["show", "build"])
    p.set_defaults(fn=history_cmd)

    p = sp.add_parser("pricelog", help="price log: grade logged prices against the closing line, show which edges beat it")
    p.set_defaults(fn=pricelog_cmd)

    p = sp.add_parser("cloud", help="run on GitHub: setup | run | scores | history | pull | status | on | off",
                      add_help=False)
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(fn=lambda a: __import__("betmodel.cloud", fromlist=["x"]).main(a.rest or ["--help"]))

    p = sp.add_parser("app", help="open the Shiny app")
    p.add_argument("--port", type=int, default=8765); p.add_argument("--no-browser", action="store_true")
    p.add_argument("--phone", action="store_true", help="also allow your phone (Wi-Fi / Tailscale) to connect")
    p.add_argument("--no-publish", action="store_true", help="never upload the phone page from this copy (testing)")
    p.set_defaults(fn=app_cmd)

    p = sp.add_parser("keys", help="save / test API keys")
    p.add_argument("action", nargs="?", default="check", choices=["check", "set", "save"])
    p.add_argument("which", nargs="?", help="odds | apifootball")
    p.add_argument("key", nargs="?")
    p.set_defaults(fn=keys_cmd)

    p = sp.add_parser("picks", help="pick tracker")
    p.add_argument("action", nargs="?", default="report", choices=["report", "settle", "add", "export", "delete"])
    p.add_argument("--last", type=int, default=25)
    p.add_argument("--out", default="picks_export.csv")
    p.add_argument("--id", type=int)
    p.add_argument("--sport", choices=["nfl", "soccer"]); p.add_argument("--comp")
    p.add_argument("--date", help="game date YYYY-MM-DD"); p.add_argument("--home"); p.add_argument("--away")
    p.add_argument("--market", help="1X2, OU25, SPREAD, TOTAL, ML"); p.add_argument("--selection", help="H/D/A, OVER/UNDER, or NFL team code")
    p.add_argument("--line", type=float); p.add_argument("--odds"); p.add_argument("--stake", type=float, default=10)
    p.add_argument("--book", default="manual")
    p.set_defaults(fn=picks_cmd)

    s = sp.add_parser("soccer").add_subparsers(dest="cmd", required=True)
    p = s.add_parser("predict")
    p.add_argument("home"); p.add_argument("away")
    p.add_argument("--comp", help="EPL, LALIGA, SERIEA, UCL, UEL ... (default: home team's league)")
    p.add_argument("--neutral", action="store_true")
    p.add_argument("--odds", nargs=3, metavar=("H", "D", "A"), help="prices you can bet (DraftKings/FanDuel), American or decimal")
    p.add_argument("--sharp", nargs=3, metavar=("H", "D", "A"), help="optional sharp reference prices (Pinnacle / exchange)")
    p.add_argument("--ou", nargs=3, metavar=("LINE", "OVER", "UNDER"))
    p.add_argument("--ah", nargs=3, metavar=("LINE", "HOME", "AWAY"))
    p.add_argument("--no-injuries", action="store_true")
    p.set_defaults(fn=soccer_predict)
    p = s.add_parser("fixtures"); p.add_argument("--comp", default="EPL,LALIGA,SERIEA,UCL,UEL"); p.add_argument("--days", type=int, default=7)
    p.add_argument("--log", action="store_true", help="save recommended picks to the tracker")
    p.set_defaults(fn=soccer_fixtures)
    p = s.add_parser("ratings"); p.add_argument("--league"); p.add_argument("--top", type=int, default=30)
    p.set_defaults(fn=soccer_ratings)
    p = s.add_parser("backtest"); p.add_argument("--start", default="2025-08-15"); p.add_argument("--end", default=None)
    p.set_defaults(fn=soccer_backtest)

    n = sp.add_parser("nfl").add_subparsers(dest="cmd", required=True)
    p = n.add_parser("predict")
    p.add_argument("home"); p.add_argument("away")
    p.add_argument("--spread", type=float, help="home spread as a book shows it, e.g. -3.5")
    p.add_argument("--spread-odds", nargs=2, default=["-110", "-110"], metavar=("HOME", "AWAY"))
    p.add_argument("--total", type=float)
    p.add_argument("--total-odds", nargs=2, default=["-110", "-110"], metavar=("OVER", "UNDER"))
    p.add_argument("--ml", nargs=2, metavar=("HOME", "AWAY"))
    p.add_argument("--neutral", action="store_true")
    p.add_argument("--rest-diff", type=float, default=0.0, help="home rest days minus away rest days")
    p.add_argument("--home-qb", help="starting QB name if not the usual starter, e.g. 'Mac Jones'")
    p.add_argument("--away-qb")
    p.add_argument("--home-backup-qb", action="store_true", help="flag the home starter as unfamiliar/rookie")
    p.add_argument("--away-backup-qb", action="store_true")
    p.add_argument("--adj-home", type=float, default=0.0, help="manual points adjustment")
    p.add_argument("--adj-away", type=float, default=0.0)
    p.add_argument("--no-injuries", action="store_true")
    p.set_defaults(fn=nfl_predict)
    p = n.add_parser("week"); p.add_argument("--week", type=int)
    p.add_argument("--log", action="store_true", help="save recommended picks to the tracker"); p.set_defaults(fn=nfl_week)
    p = n.add_parser("injuries"); p.add_argument("--team"); p.set_defaults(fn=nfl_injuries)
    p = n.add_parser("ratings"); p.set_defaults(fn=nfl_ratings)
    p = n.add_parser("backtest"); p.add_argument("--seasons", default="2023,2024,2025,2026"); p.set_defaults(fn=nfl_backtest)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()


