"""Free odds (no API keys, no credits), in the same shape as odds_api.prices() so the slates can use either.

  DraftKings  ESPN's public scoreboards (the feed the app already reads for scores): NFL moneyline, spread and
              total; soccer result and DraftKings' main goal total. Current prices before kickoff.
  Kalshi      a regulated US exchange with an official public API (no key): game winner, spread and total
              ladders. Its mid prices sit within about 1-2 points of Pinnacle's no-vig prices, so it stands in
              as the sharp price. A Kalshi price is used only when its bid/ask gap is tight (MAX_GAP), so thin
              or stale quotes can't make fake edges. Stored as fair decimal odds (1 / chance), book 'kalshi'.

Records: {(home, away): {'date': kickoff UTC, 'h2h': {book: {H, A[, D]}}, 'spreads': {book: {H: (line, price),
A: ...}}, 'totals': {book: {OVER: (line, price), UNDER: ...}}, 'updated': {}}}
"""
from __future__ import annotations

import json
import re
import urllib.request

import pandas as pd

from . import db, espn

KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI = KALSHI_API + "/markets"
CLOSE_GAP = 0.05  # widest bid/ask accepted for a closing price
SERIES = {"NFL": ("KXNFLGAME", "KXNFLSPREAD", "KXNFLTOTAL"), "EPL": ("KXEPLGAME", None, "KXEPLTOTAL"),
          "LALIGA": ("KXLALIGAGAME", None, "KXLALIGATOTAL"), "SERIEA": ("KXSERIEAGAME", None, "KXSERIEATOTAL"),
          "UCL": ("KXUCLGAME", None, "KXUCLTOTAL"), "UEL": ("KXUELGAME", None, "KXUELTOTAL")}
NFL_CODES = {"JAC": "JAX", "LAR": "LA", "WSH": "WAS"}  # Kalshi -> nflverse
MAX_GAP = 0.04   # widest bid/ask (dollars on a $1 contract) for a Kalshi price to count as the sharp price
CACHE_H = 0.05   # 3 minutes


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (oddsea)"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


def kalshi_markets(series: str) -> list[dict]:
    """Every open market in a Kalshi series (cached 3 minutes; only the fields used here)."""
    key = f"kalshi:{series}"
    hit = db.cache_get(key, CACHE_H)
    if hit is not None:
        return hit
    out, cursor = [], ""
    for _ in range(20):
        js = _get(f"{KALSHI}?series_ticker={series}&status=open&limit=1000" + (f"&cursor={cursor}" if cursor else ""))
        out += js.get("markets") or []
        cursor = js.get("cursor") or ""
        if not cursor:
            break
    slim = [{k: m.get(k) for k in ("ticker", "event_ticker", "yes_sub_title", "floor_strike", "yes_bid_dollars",
                                   "yes_ask_dollars", "rules_primary", "occurrence_datetime")} for m in out]
    db.cache_put(key, slim)
    return slim


def _mid(m: dict):
    """Mid price as a chance, or None when the quote is too wide (or empty) to trust."""
    try:
        b, a = float(m["yes_bid_dollars"]), float(m["yes_ask_dollars"])
    except (TypeError, ValueError, KeyError):
        return None
    if a <= 0 or b <= 0 or a < b or a - b > MAX_GAP:
        return None
    return (a + b) / 2


def _code(m: dict) -> str:  # 'KXNFLGAME-26NOV30CARTB' -> '26NOV30CARTB'
    return str(m.get("event_ticker") or "").split("-", 1)[-1]


def _suffix(m: dict) -> str:  # 'KXNFLGAME-26NOV30CARTB-TB' -> 'TB'
    return str(m.get("ticker") or "").rsplit("-", 1)[-1]


def _kick(m: dict, code: str):
    """Approximate kickoff (UTC). Kalshi's occurrence time is about 3 hours after kickoff, so this is only used
    for games ESPN doesn't list; grading always uses the model's own kickoff time."""
    t = m.get("occurrence_datetime")
    if t:
        try:
            return pd.Timestamp(t).tz_convert(None) - pd.Timedelta(hours=3)
        except (TypeError, ValueError):
            pass
    try:
        return pd.to_datetime(code[:7], format="%y%b%d")
    except ValueError:
        return None


def _main_line(points):
    """From [(line, P(over line))], the line whose chance is closest to 50% (the 'main' line)."""
    pts = [(x, p) for x, p in points if p is not None and 0.03 < p < 0.97]
    return min(pts, key=lambda t: abs(t[1] - 0.5)) if pts else None


def kalshi_nfl() -> dict:
    """{(home, away): {'date', 'h2h', 'spreads', 'totals'}} from Kalshi's NFL winner / spread / total markets."""
    ev = {}
    for m in kalshi_markets("KXNFLGAME"):
        ev.setdefault(_code(m), {})[_suffix(m)] = m
    out, bycode = {}, {}
    for code, ms in ev.items():
        if len(ms) != 2:
            continue
        t1, t2 = list(ms)
        teams = code[7:]  # NFL codes are away + home, e.g. 26NOV23CINWAS
        if teams == t1 + t2:
            away, home = t1, t2
        elif teams == t2 + t1:
            away, home = t2, t1
        else:
            continue
        rec = {"date": _kick(ms[home], code), "h2h": {}, "spreads": {}, "totals": {}, "_h": home, "_a": away,
               "kalshi": {"code": code, "s_game": "KXNFLGAME", "s_spread": "KXNFLSPREAD", "s_total": "KXNFLTOTAL",
                          "home_k": home, "away_k": away}}
        ph, pa = _mid(ms[home]), _mid(ms[away])
        if ph and pa:
            ph = ph / (ph + pa)
            rec["h2h"] = {"H": 1 / ph, "A": 1 / (1 - ph)}
        out[(NFL_CODES.get(home, home), NFL_CODES.get(away, away))] = rec
        bycode[code] = rec
    # spreads: 'X wins by over s' -> chance the home margin beats a line
    pts = {}
    for m in kalshi_markets("KXNFLSPREAD"):
        r, mt = bycode.get(_code(m)), re.match(r"([A-Z]+)(\d+)$", _suffix(m))
        p, s = _mid(m), m.get("floor_strike")
        if r is None or not mt or p is None or s is None:
            continue
        if mt.group(1) == r["_h"]:
            pts.setdefault(_code(m), []).append((float(s), p))          # P(home margin > s)
        elif mt.group(1) == r["_a"]:
            pts.setdefault(_code(m), []).append((-float(s), 1 - p))     # P(home margin > -s)
    for code, pl in pts.items():
        b = _main_line(pl)
        if b:
            x, p = b  # home -x covers with chance p
            bycode[code]["spreads"] = {"H": (-x, 1 / p), "A": (x, 1 / (1 - p))}
    tp = {}
    for m in kalshi_markets("KXNFLTOTAL"):
        p, s = _mid(m), m.get("floor_strike")
        if _code(m) in bycode and p is not None and s is not None:
            tp.setdefault(_code(m), []).append((float(s), p))
    for code, pl in tp.items():
        b = _main_line(pl)
        if b:
            bycode[code]["totals"] = {"OVER": (b[0], 1 / b[1]), "UNDER": (b[0], 1 / (1 - b[1]))}
    for r in out.values():
        r.pop("_h"), r.pop("_a")
    return out


def kalshi_soccer(comp: str, resolve) -> dict:
    """{(home, away): {'date', 'h2h': {H, D, A}, 'totals': over/under 2.5}} from Kalshi's soccer markets."""
    gser, _, tser = SERIES[comp]
    ev = {}
    for m in kalshi_markets(gser):
        ev.setdefault(_code(m), {})[_suffix(m)] = m
    out, bycode = {}, {}
    for code, ms in ev.items():
        tie = ms.get("TIE")
        sides = [k for k in ms if k != "TIE"]
        if tie is None or len(sides) != 2:
            continue
        teams = code[7:]  # soccer codes are home + away
        if teams == sides[0] + sides[1]:
            hk, ak = sides
        elif teams == sides[1] + sides[0]:
            ak, hk = sides
        else:
            continue
        names = re.search(r"the (.+?) vs (.+?) professional", str(tie.get("rules_primary") or ""))
        hn = names.group(1).strip() if names else ms[hk].get("yes_sub_title")
        an = names.group(2).strip() if names else ms[ak].get("yes_sub_title")
        try:
            h, a = resolve(hn), resolve(an)
        except (SystemExit, Exception):
            continue
        rec = {"date": _kick(tie, code), "h2h": {}, "spreads": {}, "totals": {},
               "kalshi": {"code": code, "s_game": gser, "s_spread": None, "s_total": tser, "home_k": hk, "away_k": ak}}
        ph, pdr, pa = _mid(ms[hk]), _mid(tie), _mid(ms[ak])
        if ph and pdr and pa:
            s = ph + pdr + pa
            rec["h2h"] = {"H": s / ph, "D": s / pdr, "A": s / pa}
        out[(h, a)] = rec
        bycode[code] = rec
    if tser:
        for m in kalshi_markets(tser):
            r, p = bycode.get(_code(m)), _mid(m)
            if r is not None and p is not None and m.get("floor_strike") == 2.5:
                r["totals"] = {"OVER": (2.5, 1 / p), "UNDER": (2.5, 1 / (1 - p))}
    return out


def prices(comp: str, resolve, days: int = 3) -> dict:
    """DraftKings (ESPN) + Kalshi for one competition, keyed (home, away) in the model's team names."""
    out = {}
    try:
        games = espn.scoreboard(comp, days=days)
    except Exception as e:
        print(f"  ! ESPN odds {comp}: {e}")
        games = []
    for e in games:
        dk = e.get("dk")
        if not dk or e.get("state") != "pre":
            continue
        try:
            h, a = (e["home_name"], e["away_name"]) if comp == "NFL" else (resolve(e["home_name"]), resolve(e["away_name"]))
        except (SystemExit, Exception):
            continue
        rec = out.setdefault((h, a), {"date": e["date_utc"], "h2h": {}, "spreads": {}, "totals": {}, "updated": {}})
        for mk in ("h2h", "spreads", "totals"):
            if dk.get(mk):
                rec[mk]["draftkings"] = dk[mk]
    try:
        ks = kalshi_nfl() if comp == "NFL" else kalshi_soccer(comp, resolve)
    except Exception as e:
        print(f"  ! Kalshi {comp}: {e}")
        ks = {}
    for key, k in ks.items():
        rec = out.get(key)
        if rec is None:
            if k.get("date") is None:
                continue
            rec = out.setdefault(key, {"date": k["date"], "h2h": {}, "spreads": {}, "totals": {}, "updated": {}})
        elif k.get("date") is not None and abs(pd.Timestamp(rec["date"]) - pd.Timestamp(k["date"])) > pd.Timedelta(days=2):
            continue  # same teams, different game
        for mk in ("h2h", "spreads", "totals"):
            if k.get(mk):
                rec[mk]["kalshi"] = k[mk]
        rec["kalshi"] = k.get("kalshi")
    return out


# ------------------------------------------------------------------------------- closing prices (for grading)
def event_markets(event_ticker: str) -> list[dict]:
    """Every market in a Kalshi event, open or finished (cached 30 days)."""
    key = f"kalshi:ev:{event_ticker}"
    hit = db.cache_get(key, 24 * 30)
    if hit is not None:
        return hit
    js = _get(f"{KALSHI}?event_ticker={event_ticker}&limit=1000")
    ms = [{k: m.get(k) for k in ("ticker", "yes_sub_title", "floor_strike")} for m in js.get("markets") or []]
    if ms:
        db.cache_put(key, ms)
    return ms


def _dollars(node):
    node = node or {}
    v = node.get("close_dollars")
    if v is None and node.get("close") is not None:  # older responses: cents
        v = float(node["close"]) / 100
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def close_mid(series: str, ticker: str | None, kickoff) -> float | None:
    """Kalshi's last mid price before kickoff (1-minute candles, else hourly), as a chance; None if unknown.
    Cached for good once fetched (call only after kickoff)."""
    if not ticker:
        return None
    ko = pd.Timestamp(kickoff)
    ko = ko.tz_localize("UTC") if ko.tzinfo is None else ko
    end = int(ko.timestamp())
    key = f"kalshi:close:{ticker}:{end}"
    hit = db.cache_get(key, 24 * 365)
    if hit is not None:
        return hit.get("p")
    p = None
    for interval, back_h in ((1, 2), (60, 24)):
        try:
            js = _get(f"{KALSHI_API}/series/{series}/markets/{ticker}/candlesticks"
                      f"?start_ts={end - back_h * 3600}&end_ts={end}&period_interval={interval}")
        except Exception:
            continue
        for c in reversed(js.get("candlesticks") or []):
            if int(c.get("end_period_ts") or 0) > end:
                continue
            b, a = _dollars(c.get("yes_bid")), _dollars(c.get("yes_ask"))
            if b is not None and a is not None and 0 < b <= a and a - b <= CLOSE_GAP:
                p = (a + b) / 2
                break
        if p is not None:
            break
    db.cache_put(key, {"p": p})
    return p
