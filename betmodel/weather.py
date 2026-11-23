"""Kickoff wind forecasts for open-air NFL stadiums (Open-Meteo: free, no key, 16-day forecast).

Wind is the one weather factor that measurably moves NFL scoring: in the 2019-26 walk-forward
backtest, adding it cut the model's totals error from 13.34 to 13.23 points per game.
Domes and retractable roofs get no wind. If the forecast can't be fetched, the model uses
average conditions, i.e. exactly what it did before.
"""
from __future__ import annotations

import json
import urllib.request

import pandas as pd

from . import db

# home stadium coordinates (lat, lon)
STADIUMS = {
    "ARI": (33.5276, -112.2626), "ATL": (33.7554, -84.4008), "BAL": (39.2780, -76.6227), "BUF": (42.7738, -78.7870),
    "CAR": (35.2258, -80.8528), "CHI": (41.8623, -87.6167), "CIN": (39.0955, -84.5161), "CLE": (41.5061, -81.6995),
    "DAL": (32.7473, -97.0945), "DEN": (39.7439, -105.0201), "DET": (42.3400, -83.0456), "GB": (44.5013, -88.0622),
    "HOU": (29.6847, -95.4107), "IND": (39.7601, -86.1639), "JAX": (30.3240, -81.6373), "KC": (39.0489, -94.4839),
    "LV": (36.0909, -115.1833), "LAC": (33.9535, -118.3392), "LA": (33.9535, -118.3392), "MIA": (25.9580, -80.2389),
    "MIN": (44.9738, -93.2581), "NE": (42.0909, -71.2643), "NO": (29.9511, -90.0812), "NYG": (40.8135, -74.0745),
    "NYJ": (40.8135, -74.0745), "PHI": (39.9008, -75.1675), "PIT": (40.4468, -80.0158), "SF": (37.4030, -121.9700),
    "SEA": (47.5952, -122.3316), "TB": (27.9759, -82.5033), "TEN": (36.1665, -86.7713), "WAS": (38.9078, -76.8645),
}
URL = ("https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=wind_speed_10m,temperature_2m"
       "&wind_speed_unit=mph&temperature_unit=fahrenheit&timezone=UTC&forecast_days=16")


def open_air(roof) -> bool:
    """Only true outdoor games get wind; retractable roofs (often blank before game day) count as closed."""
    return isinstance(roof, str) and roof in ("outdoors", "open")


def _hourly(team: str) -> dict | None:
    key = f"openmeteo:{team}"
    hit = db.cache_get(key, 3)
    if hit is not None:
        return hit
    lat, lon = STADIUMS[team]
    req = urllib.request.Request(URL.format(lat=lat, lon=lon), headers={"User-Agent": "betmodel"})
    body = json.loads(urllib.request.urlopen(req, timeout=20).read())
    h = body.get("hourly") or {}
    out = {"time": h.get("time", []), "wind": h.get("wind_speed_10m", []), "temp": h.get("temperature_2m", [])}
    db.cache_put(key, out)
    return out


def kickoff_wind(home: str, kickoff_utc: pd.Timestamp, roof, neutral: bool = False) -> tuple[float | None, float | None]:
    """(wind mph, temp F) averaged over the 3 game hours, or (None, None) if not applicable/available."""
    if neutral or not open_air(roof) or home not in STADIUMS or kickoff_utc is None or pd.isna(kickoff_utc):
        return None, None
    try:
        h = _hourly(home)
    except Exception as e:
        print(f"  ! weather for {home}: {e}")
        return None, None
    if not h or not h["time"]:
        return None, None
    t = pd.to_datetime(h["time"])
    sel = (t >= kickoff_utc.floor("h")) & (t <= kickoff_utc.floor("h") + pd.Timedelta(hours=3))
    w = [x for x, s in zip(h["wind"], sel) if s and x is not None]
    c = [x for x, s in zip(h["temp"], sel) if s and x is not None]
    if not w:
        return None, None
    return float(sum(w) / len(w)), (float(sum(c) / len(c)) if c else None)
