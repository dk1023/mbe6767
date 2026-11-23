"""Live DraftKings / FanDuel / Pinnacle prices from The Odds API (https://the-odds-api.com).

Free key: 500 credits a month. One call = one league x one market group; listing bookmakers
explicitly costs 1 credit per market, so a daily run is ~13 credits (NFL h2h+spreads+totals,
5 soccer competitions h2h+totals) = ~400/month. Responses are cached for an hour.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

import pandas as pd

from . import db

BASE = "https://api.the-odds-api.com/v4/sports/{sport}/odds"
SPORT_KEYS = {"NFL": "americanfootball_nfl", "EPL": "soccer_epl", "LALIGA": "soccer_spain_la_liga",
              "SERIEA": "soccer_italy_serie_a", "UCL": "soccer_uefa_champs_league",
              "UEL": "soccer_uefa_europa_league", "BUNDES": "soccer_germany_bundesliga", "LIGUE1": "soccer_france_ligue_one"}
BOOKS = ["draftkings", "fanduel", "pinnacle", "betfair_ex_eu"]
BETTABLE = ("draftkings",)                     # the sportsbooks you can bet at (add e.g. "fanduel" if you open one)
SHARP = ("pinnacle", "betfair_ex_eu", "kalshi")  # reference prices, best first (kalshi = the free source)

NFL_TEAMS = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL", "Buffalo Bills": "BUF",
    "Carolina Panthers": "CAR", "Chicago Bears": "CHI", "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE",
    "Dallas Cowboys": "DAL", "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX", "Kansas City Chiefs": "KC",
    "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC", "Los Angeles Rams": "LA", "Miami Dolphins": "MIA",
    "Minnesota Vikings": "MIN", "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT", "San Francisco 49ers": "SF",
    "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB", "Tennessee Titans": "TEN", "Washington Commanders": "WAS",
}


def key() -> str | None:
    from .soccer_injuries import config
    return os.environ.get("ODDS_API_KEY") or config().get("odds_api_key") or None


def fetch(comp: str) -> list:
    """Raw events for a competition (cached 1h). Empty list if no key."""
    k = key()
    if not k or comp not in SPORT_KEYS:
        return []
    markets = "h2h,spreads,totals" if comp == "NFL" else "h2h,totals"
    params = {"apiKey": k, "markets": markets, "oddsFormat": "decimal", "bookmakers": ",".join(BOOKS)}
    ck = f"oddsapi:{comp}:{markets}"
    hit = db.cache_get(ck, 1.0)
    if hit is not None:
        return hit
    url = BASE.format(sport=SPORT_KEYS[comp]) + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "betmodel"}), timeout=30) as r:
        body = json.loads(r.read())
        remaining = r.headers.get("x-requests-remaining")
    db.count_api_call("odds-api")
    db.cache_put(ck, body)
    if remaining is not None:
        db.cache_put("oddsapi:remaining", remaining)
    return body


def remaining_credits():
    return db.cache_get("oddsapi:remaining", 24 * 31)


def prices(comp: str, resolve) -> dict:
    """{(home, away): {'date': Timestamp, 'h2h': {book: {sel: price}}, 'spreads': {book: {sel: (point, price)}},
    'totals': {book: {'OVER': (point, price), 'UNDER': (...)}}}}. `resolve` maps API names to ours."""
    out = {}
    for ev in fetch(comp):
        try:
            h, a = resolve(ev["home_team"]), resolve(ev["away_team"])
        except SystemExit:
            continue
        rec = {"date": pd.Timestamp(ev["commence_time"]).tz_convert(None), "h2h": {}, "spreads": {}, "totals": {},
               "updated": {}}  # (market, book) -> when that book's price last updated (UTC), to spot stale prices
        for bk in ev.get("bookmakers", []):
            for mk in bk.get("markets", []):
                if mk.get("key") not in ("h2h", "spreads", "totals"):  # e.g. Betfair 'h2h_lay'
                    continue
                upd = mk.get("last_update") or bk.get("last_update")
                try:
                    rec["updated"][(mk["key"], bk["key"])] = pd.Timestamp(upd).tz_convert(None) if upd else None
                except (TypeError, ValueError):
                    rec["updated"][(mk["key"], bk["key"])] = None
                d = {}
                for o in mk.get("outcomes", []):
                    nm = o["name"]
                    sel = ("H" if nm == ev["home_team"] else "A" if nm == ev["away_team"] else
                           "D" if nm == "Draw" else nm.upper())
                    d[sel] = (o.get("point"), o["price"]) if mk["key"] != "h2h" else o["price"]
                rec[mk["key"]][bk["key"]] = d
        out[(h, a)] = rec
    return out


def best_bettable(market: dict, sel: str, point=None):
    """Best DraftKings/FanDuel price for a selection (optionally at a given line): (price, book, point)."""
    best = None
    for b in BETTABLE:
        v = market.get(b, {}).get(sel)
        if v is None:
            continue
        pt, pr = (v if isinstance(v, tuple) else (None, v))
        if point is not None and pt is not None and abs(pt - point) > 1e-9:
            continue
        if best is None or pr > best[0]:
            best = (pr, b, pt)
    return best


def sharp(market: dict):
    for b in SHARP:
        if market.get(b):
            return b, market[b]
    return None, None
