"""Soccer availability: injuries, suspensions and confirmed lineups -> expected-goal multipliers.

Sources
  * Fantasy Premier League API (free, no key): status + chance of playing + season xG/xA for every
    Premier League player.
  * API-Football (free key, 100 requests/day, https://dashboard.api-football.com): injuries for
    La Liga, Serie A, UCL, UEL (and EPL), plus confirmed lineups ~1 hour before kickoff. Optional:
    put the key in config.json ("api_football_key") or the APIFOOTBALL_KEY environment variable.

How an absence is valued (a heuristic, not fitted - there is no free historical injury data to fit
it on):
  attack  : team xG x (1 - 0.5 * player's share of team xG + 0.7*xA)   (replacement covers half)
  defence : opponent xG x (1 + minutes-share * {GK 8%, DEF 4%, MID 2%})
  weighted by the probability the player misses the game; multipliers capped at +/-25%.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import db
from .data_soccer import DATA_DIR, _get, norm_name, resolve_team

FPL_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
AF_BASE = "https://v3.football.api-sports.io"
AF_LEAGUES = {"EPL": 39, "LALIGA": 140, "SERIEA": 135, "BUNDES": 78, "LIGUE1": 61, "UCL": 2, "UEL": 3, "UECL": 848}
DEF_W = {"GK": 0.08, "DEF": 0.04, "MID": 0.02, "FWD": 0.0, "UNK": 0.02}
REPLACEMENT = 0.5


@dataclass
class Absence:
    team: str
    player: str
    pos: str
    p_out: float
    att_share: float
    min_share: float
    reason: str
    source: str


@dataclass
class Availability:
    absences: dict = field(default_factory=dict)  # team -> [Absence]
    notes: list = field(default_factory=list)
    lineups: dict = field(default_factory=dict)   # (home, away) -> FotMob lineup info (predicted/confirmed)

    def multipliers(self, home: str, away: str) -> tuple[float, float, list]:
        """(multiplier on home xG, multiplier on away xG, lines explaining it)."""
        def att(t):
            return 1 - REPLACEMENT * sum(a.p_out * a.att_share for a in self.absences.get(t, []))

        def leak(t):
            return 1 + sum(a.p_out * a.min_share * DEF_W.get(a.pos, 0) for a in self.absences.get(t, []))

        mh = float(np.clip(att(home) * leak(away), 0.75, 1.25))
        ma = float(np.clip(att(away) * leak(home), 0.75, 1.25))
        lines = []
        for t in (home, away):
            for a in sorted(self.absences.get(t, []), key=lambda a: -(a.att_share + a.min_share * DEF_W.get(a.pos, 0)))[:6]:
                if a.p_out * max(a.att_share, a.min_share) >= 0.05:
                    lines.append(f"{t}: {a.player} ({a.pos}) {a.reason} - {a.p_out:.0%} out, "
                                 f"{a.min_share:.0%} of minutes, {a.att_share:.0%} of attack [{a.source}]")
        return mh, ma, lines


def config() -> dict:
    p = DATA_DIR.parent.parent / "config.json"
    return json.loads(p.read_text()) if p.exists() else {}


# ------------------------------------------------------------------------- FPL (Premier League)
def fpl_absences(model_teams: list[str]) -> dict:
    raw = _get(FPL_URL, DATA_DIR / "fpl_bootstrap.json", max_age_h=2)
    js = json.loads(raw)
    teams = {t["id"]: t["name"] for t in js["teams"]}
    pos = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
    el = pd.DataFrame(js["elements"])
    for c in ["expected_goals", "expected_assists"]:
        el[c] = pd.to_numeric(el[c], errors="coerce").fillna(0)
    el["contrib"] = el["expected_goals"] + 0.7 * el["expected_assists"]
    out: dict = {}
    for tid, g in el.groupby("team"):
        try:
            name = resolve_team(teams[tid], model_teams)
        except SystemExit:
            continue
        games = max(1.0, g["starts"].max() if "starts" in g else g["minutes"].max() / 90)
        tot = max(g["contrib"].sum(), 1e-6)
        for r in g.itertuples():
            ch = r.chance_of_playing_next_round
            if r.status == "a" and (ch is None or pd.isna(ch) or ch >= 100):
                continue
            p_out = 1 - (ch / 100) if ch is not None and not pd.isna(ch) else (0.0 if r.status == "a" else 1.0)
            if p_out <= 0:
                continue
            ms = min(1.0, r.minutes / (90 * games))
            if ms < 0.15:
                continue
            out.setdefault(name, []).append(Absence(name, r.web_name, pos.get(r.element_type, "MID"), p_out,
                                                    r.contrib / tot, ms, (r.news or r.status)[:60], "FPL"))
    return out


# ------------------------------------------------------------------------- API-Football
DAILY_BUDGET = 90  # stay under the free plan's 100 requests/day
POS_MAP = {"Goalkeeper": "GK", "Defender": "DEF", "Midfielder": "MID", "Attacker": "FWD", "G": "GK", "D": "DEF",
           "M": "MID", "F": "FWD"}
# fallback value when a player's stats could not be fetched within the budget
POS_DEFAULT = {"GK": (0.0, 0.8), "DEF": (0.03, 0.6), "MID": (0.07, 0.5), "FWD": (0.12, 0.5)}


class BudgetExceeded(RuntimeError):
    pass


class APIFootball:
    def __init__(self, key: str):
        self.key = key
        self.calls = 0

    def get(self, path: str, ttl_h: float = 6, **params) -> list:
        q = urllib.parse.urlencode(params)
        cache_key = f"af:{path}?{q}"
        hit = db.cache_get(cache_key, ttl_h)
        if hit is not None:
            return hit
        if db.api_calls_today("api-football") >= DAILY_BUDGET:
            raise BudgetExceeded("daily request budget used up; using cached/fallback values")
        req = urllib.request.Request(f"{AF_BASE}/{path}?{q}", headers={"x-apisports-key": self.key})
        js = json.loads(urllib.request.urlopen(req, timeout=30).read())
        self.calls += 1
        db.count_api_call("api-football")
        errs = js.get("errors")
        if errs:  # e.g. plan does not cover this season, bad key, rate limit
            raise RuntimeError(f"{errs}")
        res = js.get("response", [])
        db.cache_put(cache_key, res)
        return res


def _player_stats(api: APIFootball, pid: int, season: int) -> dict | None:
    """{name,pos,min,g,a} for one player this season (cached for a week)."""
    try:
        res = api.get("players", ttl_h=168, id=pid, season=season)
    except BudgetExceeded:
        return None
    if not res:
        return None
    r = res[0]
    mins = goals = assists = 0
    posn = "MID"
    for s in r.get("statistics", []):
        gm = s.get("games") or {}
        mins += gm.get("minutes") or 0
        posn = POS_MAP.get(gm.get("position"), posn)
        goals += (s.get("goals") or {}).get("total") or 0
        assists += (s.get("goals") or {}).get("assists") or 0
    return {"name": r["player"]["name"], "pos": posn, "min": mins, "g": goals, "a": assists}


def af_absences(df: pd.DataFrame, model_teams: list[str], comps: list[str], days: int = 3) -> tuple[dict, list]:
    key = os.environ.get("APIFOOTBALL_KEY") or config().get("api_football_key")
    if not key:
        return {}, []
    if db.cache_get(f"af:plan_blocked:{key[-6:]}", 24):
        return {}, []  # free plan can't see this season; checked within the last day, FotMob covers it
    api = APIFootball(key)
    today = pd.Timestamp.today().normalize()
    season = today.year if today.month >= 7 else today.year - 1
    now = pd.Timestamp.utcnow().tz_localize(None)
    out, notes = {}, []

    def value(name, st, pid_pos, games, gf):
        if st:
            return st["pos"], (st["g"] + 0.7 * st["a"]) / gf, min(1.0, st["min"] / (90 * games))
        pos = pid_pos or "MID"
        a, m = POS_DEFAULT.get(pos, (0.05, 0.5))
        return pos, a, m

    for comp in comps:
        lid = AF_LEAGUES.get(comp)
        if not lid:
            continue
        try:
            fx = api.get("fixtures", ttl_h=6, league=lid, season=season,
                         **{"from": today.strftime("%Y-%m-%d"), "to": (today + pd.Timedelta(days=days)).strftime("%Y-%m-%d")})
            team_ids = {}
            for f in fx:
                for side in ("home", "away"):
                    try:
                        team_ids[f["teams"][side]["id"]] = resolve_team(f["teams"][side]["name"], model_teams)
                    except SystemExit:
                        pass
            # one injuries call per league per match day
            for day in sorted({f["fixture"]["date"][:10] for f in fx}):
                for it in api.get("injuries", ttl_h=3, league=lid, season=season, date=day):
                    name = team_ids.get(it["team"]["id"])
                    if not name:
                        continue
                    pid = it["player"]["id"]
                    if any(a.player == it["player"]["name"] for a in out.get(name, [])):
                        continue
                    st = _player_stats(api, pid, season)
                    games, gf = max(1, _team_games(df, name)), max(1.0, _team_goals(df, name))
                    pos, att, ms = value(name, st, None, games, gf)
                    if ms < 0.15:
                        continue
                    p_out = 1.0 if it["player"].get("type") == "Missing Fixture" else 0.5
                    out.setdefault(name, []).append(Absence(name, it["player"]["name"], pos, p_out, att, ms,
                                                            it["player"].get("reason") or "", "API-Football"))
            # confirmed lineups (released ~1h before kickoff): regulars missing from the XI
            for f in fx:
                ko = pd.Timestamp(f["fixture"]["date"]).tz_convert(None)
                if not (-0.5 * 3600 < (ko - now).total_seconds() < 2 * 3600):
                    continue
                for lu in api.get("fixtures/lineups", ttl_h=0.25, fixture=f["fixture"]["id"]):
                    name = team_ids.get(lu["team"]["id"])
                    if not name or not lu.get("startXI"):
                        continue
                    xi = {p["player"]["id"] for p in lu["startXI"]}
                    regulars = _regulars(api, lu["team"]["id"], season)
                    games, gf = max(1, _team_games(df, name)), max(1.0, _team_goals(df, name))
                    for pid, st in regulars.items():
                        ms = min(1.0, st["min"] / (90 * games))
                        if ms >= 0.6 and pid not in xi and not any(a.player == st["name"] for a in out.get(name, [])):
                            out.setdefault(name, []).append(Absence(name, st["name"], st["pos"], 1.0,
                                                                    (st["g"] + 0.7 * st["a"]) / gf, ms,
                                                                    "not in confirmed XI", "lineup"))
                    notes.append(f"confirmed lineup used: {name}")
        except BudgetExceeded as e:
            notes.append(f"API-Football {comp}: {e}")
        except Exception as e:
            if "plan" in str(e).lower():  # plan can't see this season: don't waste requests on other leagues
                db.cache_put(f"af:plan_blocked:{key[-6:]}", True)
                break
            notes.append(f"API-Football {comp}: {e}")
    notes.append(f"API-Football requests this run: {api.calls} "
                 f"(today: {db.api_calls_today('api-football')}/{DAILY_BUDGET} budget, free plan allows 100)")
    return out, notes


def _regulars(api: APIFootball, team_id: int, season: int) -> dict:
    """Season stats for a squad (2-3 requests, cached 3 days) - only fetched for imminent kickoffs."""
    out, page = {}, 1
    while True:
        try:
            res = api.get("players", ttl_h=72, team=team_id, season=season, page=page)
        except BudgetExceeded:
            break
        for r in res:
            mins = goals = assists = 0
            posn = "MID"
            for s in r.get("statistics", []):
                gm = s.get("games") or {}
                mins += gm.get("minutes") or 0
                posn = POS_MAP.get(gm.get("position"), posn)
                goals += (s.get("goals") or {}).get("total") or 0
                assists += (s.get("goals") or {}).get("assists") or 0
            out[r["player"]["id"]] = {"name": r["player"]["name"], "pos": posn, "min": mins, "g": goals, "a": assists}
        if len(res) < 20 or page >= 3:
            break
        page += 1
    return out


def _season_rows(df, team):
    start = pd.Timestamp.today() - pd.Timedelta(days=330)
    return df[(df["date"] >= start) & df["hg"].notna() & ((df["home"] == team) | (df["away"] == team))]


def _team_games(df, team):
    return len(_season_rows(df, team))


def _team_goals(df, team):
    r = _season_rows(df, team)
    return float(r.loc[r["home"] == team, "hg"].sum() + r.loc[r["away"] == team, "ag"].sum())


FM_ATT = {"FWD": 1.6, "MID": 1.0, "DEF": 0.3, "GK": 0.0, "UNK": 0.8}  # UNK: FotMob gives no position


def _fm_absence(team, p, avg_mv, p_out, reason, source):
    """Value a FotMob player by market value relative to the team's average starter."""
    imp = float(np.clip((p.get("mv") or 0) / avg_mv, 0.1, 3.0)) if avg_mv else 0.5
    pos = p.get("pos") or "UNK"
    return Absence(team, p["name"], pos, p_out, 0.09 * imp * FM_ATT.get(pos, 1.0), min(1.0, 0.75 * imp), reason, source)


def _same_player(a: str, b: str) -> bool:
    a, b = norm_name(a), norm_name(b)
    return a == b or a in b or b in a or a.split()[-1] == b.split()[-1]


def fotmob_absences(model_teams: list[str], comps: list[str], days: int, existing: dict) -> tuple[dict, dict, list]:
    from . import fotmob
    out, lineups, notes = {}, {}, []
    games = fotmob.all_lineups(comps, days)
    for gm in games:
        lu = gm.get("lineup")
        if not lu:
            continue
        try:
            h, a = resolve_team(gm["home"], model_teams), resolve_team(gm["away"], model_teams)
        except SystemExit:
            continue
        lineups[(h, a)] = lu
        for side, team in (("home", h), ("away", a)):
            t = lu[side]
            have = existing.get(team, [])
            lst = []
            for u in t["unavailable"]:
                if any(_same_player(x.player, u["name"]) for x in have):
                    continue  # FPL already has this player with better data
                typ, back = u["type"], (u["back"] or "").lower()
                p_out = 1.0 if typ in ("injury", "suspension", "internationalDuty", "redCard") else 0.75
                if "doubt" in back:
                    p_out = 0.5
                elif "training" in back:
                    p_out = 0.3
                ab = _fm_absence(team, u, t["avg_mv"], p_out, f"{typ} - back {u['back']}".strip(" -"), "FotMob")
                if ab.min_share >= 0.3:
                    lst.append(ab)
            if lu["type"] == "confirmed":  # good players left on the bench are probably rested
                for s in t["subs"]:
                    ab = _fm_absence(team, s, t["avg_mv"], 0.7, "benched in confirmed lineup", "lineup")
                    if (s.get("mv") or 0) >= 1.2 * t["avg_mv"]:
                        lst.append(ab)
            if lst:
                out[team] = lst
    n_conf = sum(1 for v in lineups.values() if v["type"] == "confirmed")
    notes.append(f"FotMob: availability for {len(lineups)} matches ({n_conf} with confirmed lineups)")
    return out, lineups, notes


def load(df: pd.DataFrame, model_teams: list[str], comps: list[str], days: int = 3) -> Availability:
    av = Availability()
    try:
        av.absences.update(fpl_absences(model_teams))
    except Exception as e:
        av.notes.append(f"FPL injuries unavailable: {e}")
    try:
        fm, av.lineups, notes = fotmob_absences(model_teams, comps, days, av.absences)
        for t, lst in fm.items():
            av.absences.setdefault(t, []).extend(lst)
        av.notes += notes
    except Exception as e:
        av.notes.append(f"FotMob unavailable: {e}")
    af, notes = af_absences(df, model_teams, comps)
    av.notes += notes
    for t, lst in af.items():  # API-Football (with lineups) overrides FPL for the same team
        if lst:
            av.absences[t] = lst
    try:
        db.save_injuries("soccer", [(a.team, a.player, a.pos, a.reason, a.p_out, a.att_share, a.source)
                                    for lst in av.absences.values() for a in lst])
    except Exception:
        pass
    return av
