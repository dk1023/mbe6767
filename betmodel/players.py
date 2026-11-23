"""Player-level match data from FotMob: who started, minutes, substitutions, each player's xG / xA,
and every shot. Stored in the local database so the lineup model can rate players.

collect() fetches each finished match once (about 1,300 matches a season across the 5 competitions)
and skips matches it already has, so later calls only add new games. Free, no API key.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from . import db

BASE = "https://www.fotmob.com/api/data"
LEAGUES = {"EPL": 47, "LALIGA": 87, "SERIEA": 55, "UCL": 42, "UEL": 73}
SEASONS = ["2023/2024", "2024/2025", "2025/2026", "2026/2027"]
USUAL_POS = {0: "GK", 1: "DEF", 2: "MID", 3: "FWD"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS pm_matches (id INTEGER PRIMARY KEY, comp TEXT, season TEXT, utc TEXT,
    home TEXT, away TEXT, home_id INTEGER, away_id INTEGER, hg INTEGER, ag INTEGER, ok INTEGER);
CREATE TABLE IF NOT EXISTS pm_apps (match_id INTEGER, team_id INTEGER, side TEXT, player_id INTEGER, name TEXT,
    pos TEXT, started INTEGER, minutes REAL, sub_in REAL, sub_out REAL, xg REAL, xa REAL, mv REAL,
    PRIMARY KEY (match_id, player_id));
CREATE TABLE IF NOT EXISTS pm_shots (match_id INTEGER, team_id INTEGER, minute REAL, xg REAL, own_goal INTEGER);
"""
_lock = threading.Lock()


def connect() -> sqlite3.Connection:
    con = db.connect()
    con.executescript(SCHEMA)
    return con


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (betmodel)"})
    for attempt in range(3):
        try:
            return json.loads(urllib.request.urlopen(req, timeout=30).read())
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 + 3 * attempt)


def season_matches(comp: str, season: str) -> list[dict]:
    js = _get(f"{BASE}/leagues?id={LEAGUES[comp]}&season={season}")
    out = []
    for m in (js.get("fixtures") or {}).get("allMatches") or []:
        st = m.get("status") or {}
        if not st.get("finished") or st.get("cancelled") or st.get("awarded"):
            continue
        try:
            hg, ag = [int(x) for x in st.get("scoreStr", "").replace(" ", "").split("-")[:2]]
        except Exception:
            continue
        out.append({"id": int(m["id"]), "comp": comp, "season": season, "utc": st.get("utcTime"),
                    "home": m["home"]["name"], "away": m["away"]["name"],
                    "home_id": int(m["home"]["id"]), "away_id": int(m["away"]["id"]), "hg": hg, "ag": ag})
    return out


def _stat(ps: dict, key: str):
    for grp in ps.get("stats") or []:
        for v in (grp.get("stats") or {}).values():
            if v.get("key") == key:
                return (v.get("stat") or {}).get("value")
    return None


def parse_match(m: dict, js: dict) -> tuple[list, list]:
    c = js.get("content") or {}
    lu = c.get("lineup") or {}
    pstats = c.get("playerStats") or {}
    apps = []
    for side in ("home", "away"):
        t = lu.get(f"{side}Team") or {}
        tid = m[f"{side}_id"]
        for started, group in ((1, t.get("starters") or []), (0, t.get("subs") or [])):
            for p in group:
                perf = p.get("performance") or {}
                ev = perf.get("substitutionEvents") or []
                sub_in = next((e.get("time") for e in ev if e.get("type") == "subIn"), None)
                sub_out = next((e.get("time") for e in ev if e.get("type") == "subOut"), None)
                if not started and sub_in is None:
                    continue  # unused substitute
                ps = pstats.get(str(p.get("id"))) or {}
                mins = _stat(ps, "minutes_played")
                if mins is None:
                    mins = (sub_out or 90) - (0 if started else (sub_in or 90))
                pos = "GK" if p.get("positionId") == 11 else (
                    _pos_from_id(p.get("positionId")) if started else USUAL_POS.get(p.get("usualPlayingPositionId"), "MID"))
                apps.append((m["id"], tid, side, int(p["id"]), p.get("name"), pos, started, float(mins or 0),
                             sub_in, sub_out, float(_stat(ps, "expected_goals") or 0),
                             float(_stat(ps, "expected_assists") or 0), float(p.get("marketValue") or 0)))
    shots = []
    for s in ((c.get("shotmap") or {}).get("shots") or []):
        mn = (s.get("min") or 0) + (s.get("minAdded") or 0) * 0  # added time counted at the period end minute
        shots.append((m["id"], int(s.get("teamId") or 0), float(mn), float(s.get("expectedGoals") or 0),
                      int(bool(s.get("isOwnGoal")))))
    return apps, shots


def _pos_from_id(pid) -> str:
    if pid is None:
        return "MID"
    if pid == 11:
        return "GK"
    if 30 <= pid < 50:
        return "DEF"
    if 50 <= pid < 80:
        return "MID"
    return "FWD"


def collect(comps=None, seasons=None, progress=lambda f, m: None, workers: int = 4) -> int:
    """Fetch every finished match not yet stored. Returns the number of matches added."""
    comps = comps or list(LEAGUES)
    seasons = seasons or SEASONS
    with connect() as con:
        have = {r[0] for r in con.execute("SELECT id FROM pm_matches WHERE ok=1")}
    todo = []
    for comp in comps:
        for s in seasons:
            try:
                todo += [m for m in season_matches(comp, s) if m["id"] not in have]
            except Exception as e:
                print(f"  ! FotMob {comp} {s}: {e}")
            time.sleep(0.3)
    done = {"n": 0}

    def one(m):
        try:
            js = _get(f"{BASE}/matchDetails?matchId={m['id']}")
            apps, shots = parse_match(m, js)
            ok = 1 if len(apps) >= 22 else 0
        except Exception:
            apps, shots, ok = [], [], 0
        with _lock:
            with connect() as con:
                con.execute("INSERT OR REPLACE INTO pm_matches VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (m["id"], m["comp"], m["season"], m["utc"], m["home"], m["away"], m["home_id"],
                             m["away_id"], m["hg"], m["ag"], ok))
                con.execute("DELETE FROM pm_apps WHERE match_id=?", (m["id"],))
                con.execute("DELETE FROM pm_shots WHERE match_id=?", (m["id"],))
                con.executemany("INSERT OR REPLACE INTO pm_apps VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", apps)
                con.executemany("INSERT INTO pm_shots VALUES (?,?,?,?,?)", shots)
            done["n"] += 1
            if done["n"] % 25 == 0:
                progress(done["n"] / max(len(todo), 1), f"Player data: {done['n']}/{len(todo)} matches")
        time.sleep(0.25)

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(one, todo))
    return len(todo)


def load() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    with connect() as con:
        m = pd.read_sql_query("SELECT * FROM pm_matches WHERE ok=1", con)
        a = pd.read_sql_query("SELECT * FROM pm_apps", con)
        s = pd.read_sql_query("SELECT * FROM pm_shots", con)
    m["utc"] = pd.to_datetime(m["utc"], utc=True, format="ISO8601").dt.tz_convert(None)
    return m, a, s


if __name__ == "__main__":
    n = collect(progress=lambda f, msg: print(msg, flush=True))
    print("added", n)
