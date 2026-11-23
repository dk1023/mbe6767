"""ESPN public site feeds: live scores / game status, team logos and records, and NFL injury
statuses (the final Friday designations, which arrive here before nflverse has them).
Unofficial but public JSON; responses are cached briefly in the local database."""
from __future__ import annotations

import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from . import db, odds as O

BASE = "https://site.api.espn.com/apis/site/v2/sports"
LEAGUES = {"NFL": "football/nfl", "EPL": "soccer/eng.1", "LALIGA": "soccer/esp.1", "SERIEA": "soccer/ita.1",
           "UCL": "soccer/uefa.champions", "UEL": "soccer/uefa.europa"}
NFL_ABBR = {"WSH": "WAS", "LAR": "LA", "JAC": "JAX"}  # ESPN -> nflverse


def _get(url: str, ttl_h: float):
    hit = db.cache_get("espn:" + url, ttl_h)
    if hit is not None:
        return hit
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (betmodel)"})
    js = json.loads(urllib.request.urlopen(req, timeout=20).read())
    db.cache_put("espn:" + url, js)
    return js


def scoreboard(comp: str, days: int = 4, dates: list | None = None) -> list[dict]:
    """Games for a competition: ids, status, scores, logos, records, ESPN's own line.
    dates: explicit days (YYYYMMDD) to fetch - used by the free score update."""
    today = pd.Timestamp.today().normalize()
    if dates:
        urls = [f"{BASE}/{LEAGUES[comp]}/scoreboard?dates={d}" for d in sorted(set(dates))]
    elif comp == "NFL":
        urls = [f"{BASE}/{LEAGUES[comp]}/scoreboard"]
    else:  # soccer feeds only accept one day per request
        urls = [f"{BASE}/{LEAGUES[comp]}/scoreboard?dates={(today + pd.Timedelta(days=i)):%Y%m%d}"
                for i in range(-1, days + 1)]

    def fetch(u):
        try:
            return _get(u, ttl_h=0.05).get("events", [])  # ~3 minutes: scores and status move
        except Exception:
            return []
    with ThreadPoolExecutor(4) as ex:
        events = [e for evs in ex.map(fetch, urls) for e in evs]
    out = []
    for e in events:
        c = e["competitions"][0]
        teams = {t["homeAway"]: t for t in c["competitors"]}
        if "home" not in teams or "away" not in teams:
            continue
        st = e["status"]["type"]
        rec = {"espn_id": e["id"], "comp": comp, "date_utc": pd.Timestamp(e["date"]).tz_convert(None),
               "state": st.get("state"), "status": st.get("shortDetail") or st.get("detail"),
               "espn_line": ((c.get("odds") or [None])[0] or {}).get("details"), "dk": _safe_dk(c)}
        for side, t in teams.items():
            tm = t["team"]
            abbr = tm.get("abbreviation", "")
            rec[f"{side}_name"] = NFL_ABBR.get(abbr, abbr) if comp == "NFL" else tm.get("displayName")
            rec[f"{side}_display"] = tm.get("displayName")
            rec[f"{side}_short"] = tm.get("shortDisplayName") or tm.get("displayName")
            rec[f"{side}_logo"] = tm.get("logo")
            rec[f"{side}_score"] = t.get("score")
            recs = t.get("records") or []
            rec[f"{side}_record"] = recs[0].get("summary") if recs else ""
        out.append(rec)
    return out


def _safe_dk(c: dict):
    try:  # an odd odds block must never cost us the scores
        return draftkings(c)
    except Exception:
        return None


def draftkings(c: dict) -> dict | None:
    """DraftKings' current prices from a scoreboard competition (ESPN's odds provider 100), as decimal odds:
    {'h2h': {H, A[, D]}, 'spreads': {H: (line, price), A: ...}, 'totals': {OVER: (line, price), UNDER: ...}}.
    ESPN shows odds only before kickoff; 'close' there means the current price."""
    o = next((x for x in (c.get("odds") or []) if isinstance(x, dict)
              and str((x.get("provider") or {}).get("id")) == "100"), None)
    if not o:
        return None

    def dec(v):
        try:
            s = str(v).strip().upper()
            return 2.0 if s in ("EVEN", "EV") else O.parse_odds(s)
        except (TypeError, ValueError):
            return None

    def num(v):
        try:
            return float(str(v).strip().lstrip("ouOU"))
        except (TypeError, ValueError):
            return None
    cur = lambda node: (node or {}).get("close") or {}
    h2h, spreads, totals = {}, {}, {}
    ml = o.get("moneyline") or {}
    for side, k in (("home", "H"), ("away", "A"), ("draw", "D")):
        v = dec(cur(ml.get(side)).get("odds"))
        if v:
            h2h[k] = v
    if "D" not in h2h and (o.get("drawOdds") or {}).get("moneyLine") is not None:
        v = dec(o["drawOdds"]["moneyLine"])
        if v:
            h2h["D"] = v
    ps = o.get("pointSpread") or {}
    for side, k in (("home", "H"), ("away", "A")):
        cl = cur(ps.get(side))
        v, ln = dec(cl.get("odds")), num(cl.get("line"))
        if v and ln is not None:
            spreads[k] = (ln, v)
    to = o.get("total") or {}
    for side, k in (("over", "OVER"), ("under", "UNDER")):
        cl = cur(to.get(side))
        v, ln = dec(cl.get("odds")), num(cl.get("line"))
        if v and ln is not None:
            totals[k] = (ln, v)
    return {"h2h": h2h, "spreads": spreads, "totals": totals} if (h2h or spreads or totals) else None


def nfl_injuries(event_ids: list[str]) -> dict:
    """{team_abbr: [{'player','pos','status'}]} from each game's ESPN summary (cached 20 minutes, so the runs
    every half hour before kickoff pick up the inactives list as soon as it's out)."""
    def one(eid):
        try:
            return _get(f"{BASE}/football/nfl/summary?event={eid}", ttl_h=1 / 3)
        except Exception:
            return {}
    out = {}
    with ThreadPoolExecutor(4) as ex:
        for js in ex.map(one, event_ids):
            for team in js.get("injuries", []) or []:
                abbr = (team.get("team") or {}).get("abbreviation", "")
                abbr = NFL_ABBR.get(abbr, abbr)
                for i in team.get("injuries", []):
                    a = i.get("athlete") or {}
                    out.setdefault(abbr, []).append({"player": a.get("displayName"),
                                                     "pos": (a.get("position") or {}).get("abbreviation"),
                                                     "status": i.get("status")})
    return out


def all_games(comps: list[str], dates: dict | None = None) -> list[dict]:
    """dates: optional {comp: [YYYYMMDD, ...]} to fetch specific days instead of the default window."""
    games = []
    for c in comps:
        try:
            games += scoreboard(c, dates=(dates or {}).get(c))
        except Exception as e:
            print(f"  ! ESPN {c}: {e}")
    return games
