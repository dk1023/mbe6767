"""One full model run: load data, fit both models, pull live availability (ESPN / FotMob / FPL),
grade old picks, price every game, log new picks. Shared by the Shiny app and the `daily` command."""
from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import data_soccer as ds, db, espn, nfl, nfl_injuries, odds_api, picks, slate, soccer_injuries as si
from .soccer import SoccerModel, SoccerParams


@dataclass
class RunResult:
    ran_at: pd.Timestamp
    nfl_rows: pd.DataFrame
    nfl_week: tuple | None
    soccer_rows: pd.DataFrame
    recommended: list            # all bets clearing the minimum edge right now
    new_ids: set                 # keys of picks logged this run
    settled: list
    games: list = field(default_factory=list)   # per-game detail records for the game pages
    notes: list = field(default_factory=list)
    credits_left: str | None = None
    scores_at: pd.Timestamp | None = None    # last free ESPN score update


def _key(p):
    return (p["sport"], p["game_date"], p["home"], p["away"], p["market"], str(p["selection"]))


def game_id(d: dict) -> str:
    return f"{d['sport']}|{d['home']}|{d['away']}"


def run(progress=lambda frac, msg: None, cfg: dict | None = None, log_picks: bool = True,
        publish: bool = True) -> RunResult:
    cfg = cfg or slate.user_config()
    tuned = slate.tuned()
    notes = []
    if cfg.get("odds_source", "free") != "odds_api":  # Kalshi's price lists, fetched together in the background
        import threading
        from . import freeodds
        threading.Thread(target=freeodds.warm, args=(["NFL"] + list(cfg["soccer_comps"]),), daemon=True).start()
    progress(0.04, "Loading NFL schedule, lines, QBs and injury reports")
    g = nfl.load_games()
    nm = nfl.NFLModel(nfl.NFLParams(**tuned.get("nfl", {}).get("params", {}))).fit(g)
    try:  # the 100,000-game simulations match the model's own margin shape
        from . import simulate
        simulate.save_margin_shape(nm)
    except Exception as e:
        print(f"  ! could not save the NFL margin shape: {e}")

    progress(0.20, "Live scores and NFL injury statuses from ESPN")
    espn_games = espn.all_games(["NFL"] + list(cfg["soccer_comps"]))
    up = g[g["result"].isna()]
    if not up.empty:
        season = int(up["season"].min())
        week = int(up[up["season"] == season]["week"].min())
        ids = [e["espn_id"] for e in espn_games if e["comp"] == "NFL"]
        try:
            live = espn.nfl_injuries(ids)
            g = nfl_injuries.apply_live(g, live, season, week)
            notes.append(f"ESPN: live injury statuses for {len(live)} NFL teams")
        except Exception as e:
            notes.append(f"ESPN NFL injuries unavailable: {e}")

    progress(0.32, "Loading soccer results from 23 leagues + UEFA")
    df = ds.load_all(verbose=False)
    progress(0.45, "Fitting soccer ratings")
    sm = SoccerModel(SoccerParams(**tuned.get("soccer", {}).get("params", {}))).fit(df)
    details: list = []
    progress(0.63, "Pricing the NFL week: DraftKings vs the sharp price (Kalshi)")
    nrows, ncands, sw = slate.nfl_slate(nm, g, None, cfg, details)
    progress(0.72, "Injuries and lineups: FotMob + Fantasy Premier League")
    av = si.load(df, sm.teams, cfg["soccer_comps"], cfg["soccer_days"])
    progress(0.80, "Player ratings: adding new matches from FotMob, valuing lineups")
    lu_state = None
    try:
        from . import lineup
        lu_state = lineup.current(update=True)
    except Exception as e:
        print(f"  ! lineup model unavailable ({e}); using the injury list instead")
    progress(0.86, "Pricing soccer matches")
    srows, scands = slate.soccer_slate(sm, df, cfg["soccer_comps"], cfg["soccer_days"], av, cfg, details, lu_state)
    _attach_espn(details, espn_games, sm.teams)
    progress(0.90, "Grading finished picks")
    settled = picks.settle(df, g, espn_games, _espn_index(details))
    try:  # price log: grade earlier runs' prices against the closing line (free data only)
        from . import pricelog
        n_graded = pricelog.grade(nm, g, df)
        if n_graded:
            notes.append(f"Price log: graded {n_graded:,} logged prices against the closing line")
        n_k = pricelog.grade_kalshi()
        if n_k:
            notes.append(f"Graded {n_k:,} prices and bets against Kalshi's closing price")
    except Exception as e:
        notes.append(f"Price log grading failed: {e}")

    recommended = sorted(ncands + scands, key=lambda c: c["game_date"])
    new = picks.log(recommended) if log_picks else []
    if log_picks:
        try:
            n_lean = picks.log_leans(details, cfg)
            notes.append(f"Tracking the model's best bet on {n_lean} other game(s) as leans (0.5u, kept separate)")
        except Exception as e:
            notes.append(f"Lean tracking failed: {e}")
    res = RunResult(pd.Timestamp.now(), nrows, sw, srows, recommended, {_key(p) for p in new}, settled,
                    sorted(details, key=lambda d: (pd.Timestamp(d["kickoff"]) if d.get("kickoff") is not None
                                                   and pd.notna(d.get("kickoff")) else d["date"])),
                    notes + av.notes,
                    odds_api.remaining_credits() if cfg.get("odds_source", "free") == "odds_api" else None)
    for d in res.games:
        try:
            db.save_prediction(_prediction_row(d, res.ran_at))
        except Exception as e:
            print(f"  ! could not save prediction {d['home']} v {d['away']}: {e}")
    _save_last(res)
    if publish:
        progress(0.96, "Saving / publishing the snapshot page")
        try:
            from . import snapshot  # imports the app's page builders
            res.notes.append(snapshot.save_and_publish(res))
        except Exception as e:
            res.notes.append(f"Snapshot failed: {e}")
    progress(1.0, "Done")
    return res


LAST_RUN = Path(__file__).resolve().parent.parent / "data" / "last_run.pkl"


def _save_last(res: RunResult) -> None:
    LAST_RUN.parent.mkdir(parents=True, exist_ok=True)
    with open(LAST_RUN, "wb") as f:
        pickle.dump(res, f)


def load_last() -> RunResult | None:
    """The most recent saved run (full model run, possibly with newer scores), or None."""
    try:
        with open(LAST_RUN, "rb") as f:
            return pickle.load(f)
    except Exception:
        return None


def _espn_index(details) -> dict:
    return {(d["sport"], d["home"], d["away"]): d["espn"] for d in details if d.get("espn")}


def _prediction_row(d: dict, ran_at) -> dict:
    e = d.get("espn") or {}
    ko = d.get("kickoff")
    row = {"sport": d["sport"], "comp": d["comp"], "game_date": pd.Timestamp(d["date"]).strftime("%Y-%m-%d"),
           "home": d["home"], "away": d["away"],
           "kickoff": str(pd.Timestamp(ko)) if ko is not None and pd.notna(ko) else None,
           "predicted_at": str(ran_at), "state": e.get("state"), "espn_id": e.get("espn_id")}
    try:
        row["home_score"], row["away_score"] = int(e.get("home_score")), int(e.get("away_score"))
    except (TypeError, ValueError):
        pass
    if d["sport"] == "nfl":
        row.update(model_margin=d["mu_model"], market_margin=d.get("mu_market"), model_total=d["tau_model"],
                   market_total=d.get("tau_market"), p_home=d["win_model"], m_home=d.get("win_market"))
    else:
        pm, ps = d["p_model"], d.get("sharp_1x2") or [None] * 3
        row.update(p_home=pm[0], p_draw=pm[1], p_away=pm[2], m_home=ps[0], m_draw=ps[1], m_away=ps[2],
                   xg_home=d["xg"][0], xg_away=d["xg"][1], model_total=d["xg"][0] + d["xg"][1])
    return row


def refresh_scores(progress=lambda frac, msg: None, publish: bool | str = True) -> RunResult:
    """FREE update: ESPN only - no Odds API credits, no model refit. Pulls live / final scores for the
    games in the last run, grades picks, records results against the model's predictions, and
    republishes the snapshot page. publish='auto' (the app's automatic updates) uploads to GitHub at most
    every 15 minutes; the phone page fetches live scores itself in between."""
    res = load_last()
    if res is None:
        raise RuntimeError("Run the model once first - the free update needs a run's predictions to compare against.")
    progress(0.1, "Fetching live and final scores from ESPN (free)")
    dates: dict = {}
    for d in res.games:
        e = d.get("espn") or {}
        t = e.get("date_utc") if e.get("date_utc") is not None else d.get("kickoff")
        t = pd.Timestamp(t) if t is not None and pd.notna(t) else pd.Timestamp(d["date"])
        for off in (-1, 0, 1):  # ESPN days are US dates; cover the UTC boundary
            dates.setdefault(d["comp"], []).append((t + pd.Timedelta(days=off)).strftime("%Y%m%d"))
    fresh = espn.all_games(list(dates), dates)
    by_id = {g["espn_id"]: g for g in fresh}
    updated = 0
    for d in res.games:
        old = d.get("espn")
        if old and old.get("espn_id") in by_id:
            d["espn"] = by_id[old["espn_id"]]
            updated += 1
            e = d["espn"]
            try:
                hs, as_ = int(e["home_score"]), int(e["away_score"])
            except (TypeError, ValueError):
                hs = as_ = None
            db.update_score(d["sport"], d["home"], d["away"], pd.Timestamp(d["date"]).strftime("%Y-%m-%d"),
                            hs, as_, e.get("state"))
            if e.get("state") == "post" and d["sport"] == "nfl" and hs is not None:
                d["final"] = f"{as_}-{hs}"
    progress(0.6, "Grading finished picks from ESPN finals")
    res.settled = picks.settle(None, None, fresh, _espn_index(res.games))
    try:  # closing-line value vs Kalshi's last pre-kickoff price (free) for games that have kicked off
        from . import pricelog
        pricelog.grade_kalshi(max_games=25)
    except Exception as e:
        print(f"  ! Kalshi closing prices: {e}")
    res.scores_at = pd.Timestamp.now()
    res.notes = [n for n in res.notes if not n.startswith(("Snapshot", "Scores updated"))]
    live = sum(1 for d in res.games if (d.get("espn") or {}).get("state") == "in")
    done = sum(1 for d in res.games if (d.get("espn") or {}).get("state") == "post")
    res.notes.insert(0, f"Scores updated {res.scores_at:%a %d %b %I:%M %p} from ESPN (free, no API credits): "
                        f"{updated} games checked, {live} live, {done} final.")
    _save_last(res)
    if publish:
        progress(0.85, "Publishing the snapshot page")
        try:
            from . import snapshot
            res.notes.append(snapshot.save_and_publish(res, min_gap_min=15 if publish == "auto" else 0))
        except Exception as e:
            res.notes.append(f"Snapshot failed: {e}")
    progress(1.0, "Done")
    return res


def scores_signature(res) -> tuple:
    """What a score update can change, to skip redrawing the app when nothing moved."""
    return tuple(((d.get("espn") or {}).get("state"), (d.get("espn") or {}).get("home_score"),
                  (d.get("espn") or {}).get("away_score"), (d.get("espn") or {}).get("status"))
                 for d in (res.games if res else []))


def _attach_espn(details, espn_games, soccer_teams):
    idx = {}
    for e in espn_games:
        if e["comp"] == "NFL":
            idx[("nfl", e["home_name"], e["away_name"])] = e
        else:
            try:
                h = ds.resolve_team(e["home_name"], soccer_teams)
                a = ds.resolve_team(e["away_name"], soccer_teams)
                idx[("soccer", h, a)] = e
            except SystemExit:
                pass
    for d in details:
        d["espn"] = idx.get((d["sport"], d["home"], d["away"]))


def describe_bet(p: dict) -> str:
    """Plain-English bet, e.g. 'TB +4.5', 'Chelsea to win', 'Over 2.5 goals'."""
    m, s, line = p["market"], str(p["selection"]), p.get("line")
    if m == "1X2":
        return {"H": f"{p['home']} to win", "D": "Draw", "A": f"{p['away']} to win"}.get(s, s)
    if m == "OU25":
        return f"{s.title()} 2.5 goals"
    if m == "SPREAD":
        return f"{s} {line:+g}"
    if m == "TOTAL":
        return f"{s.title()} {line:g} points"
    if m == "ML":
        return f"{s} moneyline"
    return f"{m} {s}"
