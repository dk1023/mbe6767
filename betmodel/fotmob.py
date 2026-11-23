"""FotMob: injured / suspended players and predicted or confirmed lineups for every league we price.
Unofficial public JSON (the same feed fotmob.com uses); cached so a run makes ~1 request per match."""
from __future__ import annotations

import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from . import db

BASE = "https://www.fotmob.com/api/data"
LEAGUE_IDS = {47: "EPL", 87: "LALIGA", 55: "SERIEA", 42: "UCL", 73: "UEL"}


def _get(url: str, ttl_h: float):
    hit = db.cache_get("fotmob:" + url, ttl_h)
    if hit is not None:
        return hit
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (betmodel)"})
    js = json.loads(urllib.request.urlopen(req, timeout=20).read())
    db.cache_put("fotmob:" + url, js)
    time.sleep(0.2)  # be polite
    return js


def fixtures(comps: list[str], days: int = 3) -> list[dict]:
    want = {k for k, v in LEAGUE_IDS.items() if v in comps}
    out = []
    for i in range(days + 1):
        day = (pd.Timestamp.today() + pd.Timedelta(days=i)).strftime("%Y%m%d")
        try:
            js = _get(f"{BASE}/matches?date={day}", ttl_h=2)
        except Exception as e:
            print(f"  ! FotMob fixtures {day}: {e}")
            continue
        for lg in js.get("leagues", []):
            if lg.get("primaryId") not in want:
                continue
            for m in lg.get("matches", []):
                st = m.get("status") or {}
                if st.get("finished") or st.get("started") or st.get("cancelled"):
                    continue
                out.append({"id": m["id"], "comp": LEAGUE_IDS[lg["primaryId"]], "home": m["home"]["name"],
                            "away": m["away"]["name"], "utc": st.get("utcTime")})
    return out


def lineup(match_id: int, kickoff_utc: str | None) -> dict:
    """{'type': 'predicted'|'confirmed', 'home': {...}, 'away': {...}} with starters and unavailable."""
    near = False
    if kickoff_utc:
        mins = (pd.Timestamp(kickoff_utc).tz_convert(None) - pd.Timestamp.now(tz="UTC").tz_localize(None)).total_seconds() / 60
        near = mins < 120
    js = _get(f"{BASE}/matchDetails?matchId={match_id}", ttl_h=0.25 if near else 2)
    lu = (js.get("content") or {}).get("lineup") or {}
    out = {"type": "confirmed" if lu.get("lineupType") not in (None, "predicted") else "predicted"}
    for side in ("home", "away"):
        t = lu.get(f"{side}Team") or {}
        starters = t.get("starters") or []
        avg_mv = (t.get("totalStarterMarketValue") or sum(p.get("marketValue") or 0 for p in starters)) / max(len(starters), 1)
        out[side] = {
            "name": t.get("name"), "id": t.get("id"), "avg_mv": avg_mv,
            "starters": [{"name": p.get("name"), "id": p.get("id"), "mv": p.get("marketValue") or 0,
                          "pos": _pos(p.get("positionId"))}
                         for p in starters],
            "subs": [{"name": p.get("name"), "mv": p.get("marketValue") or 0, "pos": _pos(p.get("positionId"))}
                     for p in t.get("subs") or []],
            "unavailable": [{"name": u.get("name"), "mv": u.get("marketValue") or 0,
                             "type": (u.get("unavailability") or {}).get("type") or "",
                             "back": (u.get("unavailability") or {}).get("expectedReturn") or ""}
                            for u in t.get("unavailable") or []],
        }
    return out


def _pos(pid) -> str:
    if pid is None:
        return "MID"
    if pid == 11:
        return "GK"
    if 30 <= pid < 50:
        return "DEF"
    if 50 <= pid < 80:
        return "MID"
    if 80 <= pid < 200:
        return "FWD"
    return "MID"


def all_lineups(comps: list[str], days: int = 3) -> list[dict]:
    fx = fixtures(comps, days)

    def one(m):
        try:
            return {**m, "lineup": lineup(m["id"], m["utc"])}
        except Exception as e:
            return {**m, "lineup": None, "error": str(e)}
    with ThreadPoolExecutor(4) as ex:
        return list(ex.map(one, fx))
