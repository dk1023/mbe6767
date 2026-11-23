"""Local SQLite database (data/betmodel.db).

Tables
  picks         every recommendation (and any bet you log by hand), graded automatically
  injuries      snapshot of the injury/availability list used for each run
  odds          snapshot of prices seen at recommendation time (line-movement history)
  api_cache     cached API responses (keeps API-Football inside its free quota)
  api_calls     per-day request counter
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pandas as pd

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "betmodel.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS picks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    sport TEXT NOT NULL, comp TEXT, game_date TEXT NOT NULL,
    home TEXT NOT NULL, away TEXT NOT NULL,
    market TEXT NOT NULL,          -- 1X2 | OU25 | SPREAD | TOTAL | ML
    selection TEXT NOT NULL,       -- H/D/A, OVER/UNDER, or team code
    line REAL,                     -- spread/total line for the selection (NULL for 1X2/ML)
    odds REAL NOT NULL,            -- decimal price recorded
    book TEXT,
    model_prob REAL, fair_odds REAL, edge REAL,
    stake REAL NOT NULL,           -- currency units
    source TEXT NOT NULL DEFAULT 'auto',
    status TEXT NOT NULL DEFAULT 'open',   -- open | won | lost | push | void
    score TEXT, close_odds REAL, close_line REAL, clv REAL, pnl REAL, settled_at TEXT,
    UNIQUE (sport, game_date, home, away, market, selection, source)
);
CREATE TABLE IF NOT EXISTS injuries (
    snap_at TEXT, sport TEXT, team TEXT, player TEXT, pos TEXT, status TEXT,
    p_out REAL, value REAL, source TEXT
);
CREATE TABLE IF NOT EXISTS odds (
    snap_at TEXT, sport TEXT, comp TEXT, game_date TEXT, home TEXT, away TEXT,
    market TEXT, selection TEXT, line REAL, odds REAL, book TEXT
);
CREATE TABLE IF NOT EXISTS predictions (
    sport TEXT, comp TEXT, game_date TEXT, home TEXT, away TEXT, kickoff TEXT, predicted_at TEXT,
    model_margin REAL, market_margin REAL, model_total REAL, market_total REAL,
    p_home REAL, p_draw REAL, p_away REAL, m_home REAL, m_draw REAL, m_away REAL,
    xg_home REAL, xg_away REAL,
    home_score INTEGER, away_score INTEGER, state TEXT, espn_id TEXT,
    PRIMARY KEY (sport, home, away, game_date)
);
CREATE TABLE IF NOT EXISTS kalshi_games (
    sport TEXT, game_date TEXT, home TEXT, away TEXT, kickoff TEXT, code TEXT,
    s_game TEXT, s_spread TEXT, s_total TEXT, home_k TEXT, away_k TEXT,
    PRIMARY KEY (sport, game_date, home, away)
);
CREATE TABLE IF NOT EXISTS api_cache (key TEXT PRIMARY KEY, saved REAL, body TEXT);
CREATE TABLE IF NOT EXISTS api_calls (day TEXT, source TEXT, n INTEGER, PRIMARY KEY (day, source));
"""


# price log columns added after the first version of the odds table (added to older databases on first use)
ODDS_EXTRA = {"kickoff": "TEXT", "hrs": "REAL", "updated": "TEXT", "p_fair": "REAL", "p_mkt": "REAL", "edge": "REAL",
              "edge_mkt": "REAL", "rec": "INTEGER", "clv": "REAL", "close_line": "REAL", "close_odds": "REAL",
              "graded_at": "TEXT"}
# Kalshi closing price for a bet: kclose = its fair chance at Kalshi's last pre-kickoff price,
# kclv = expected profit per 1 staked at that chance (closing-line value vs a sharp close)
KALSHI_EXTRA = {"kclv": "REAL", "kclose": "REAL", "kgraded_at": "TEXT"}
_migrated = False


def connect() -> sqlite3.Connection:
    global _migrated
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    if not _migrated:
        for table, cols in (("odds", {**ODDS_EXTRA, **KALSHI_EXTRA}), ("picks", KALSHI_EXTRA)):
            have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
            for c, t in cols.items():
                if c not in have:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {c} {t}")
        con.commit()
        _migrated = True
    return con


def save_kalshi_games(rows: list[dict]) -> None:
    """Which Kalshi markets belong to each game, with the model's kickoff time (to fetch closing prices later)."""
    if not rows:
        return
    cols = ["sport", "game_date", "home", "away", "kickoff", "code", "s_game", "s_spread", "s_total", "home_k", "away_k"]
    with connect() as con:
        con.executemany(f"INSERT OR REPLACE INTO kalshi_games ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                        [[r.get(c) for c in cols] for r in rows])


def now() -> str:
    return pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")


# ------------------------------------------------------------------ API cache / budget
def cache_get(key: str, ttl_h: float):
    with connect() as con:
        r = con.execute("SELECT saved, body FROM api_cache WHERE key=?", (key,)).fetchone()
    if r and time.time() - r[0] < ttl_h * 3600:
        return json.loads(r[1])
    return None


def cache_put(key: str, obj) -> None:
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO api_cache VALUES (?,?,?)", (key, time.time(), json.dumps(obj)))


def _utc_day() -> str:
    return pd.Timestamp.utcnow().strftime("%Y-%m-%d")


def api_calls_today(source: str) -> int:
    with connect() as con:
        r = con.execute("SELECT n FROM api_calls WHERE day=? AND source=?", (_utc_day(), source)).fetchone()
    return r[0] if r else 0


def count_api_call(source: str) -> None:
    with connect() as con:
        con.execute("INSERT INTO api_calls VALUES (?,?,1) ON CONFLICT(day, source) DO UPDATE SET n=n+1",
                    (_utc_day(), source))


# ------------------------------------------------------------------ snapshots
def save_injuries(sport: str, rows: list[tuple]) -> None:
    """rows: (team, player, pos, status, p_out, value, source)"""
    t = now()
    with connect() as con:
        con.executemany("INSERT INTO injuries VALUES (?,?,?,?,?,?,?,?,?)", [(t, sport, *r) for r in rows])


ODDS_FIELDS = ["sport", "comp", "game_date", "home", "away", "market", "selection", "line", "odds", "book",
               "kickoff", "hrs", "updated", "p_fair", "p_mkt", "edge", "edge_mkt", "rec"]


ODDS_KEY = ["sport", "game_date", "home", "away", "market", "selection", "line", "book"]


def _same_price(a, b, tol: float) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= tol


def save_odds(rows: list[dict]) -> None:
    """Price log: every price a run saw, with the edge the model gave it (graded against the close later).
    A price is only logged again when it (or the model's chance for it) has moved since it was last logged,
    so runs every couple of hours don't fill the log with copies."""
    if not rows:
        return
    t = now()
    with connect() as con:
        con.execute(f"CREATE INDEX IF NOT EXISTS odds_key ON odds ({', '.join(ODDS_KEY)})")
        fresh = []
        for r in rows:
            where = " AND ".join(f"{c} IS ?" for c in ODDS_KEY)
            last = con.execute(f"SELECT odds, p_fair FROM odds WHERE {where} ORDER BY rowid DESC LIMIT 1",
                               [r.get(c) for c in ODDS_KEY]).fetchone()
            if last and _same_price(last[0], r.get("odds"), 1e-9) and _same_price(last[1], r.get("p_fair"), 0.005):
                continue
            fresh.append(r)
        con.executemany(f"INSERT INTO odds (snap_at, {', '.join(ODDS_FIELDS)}) "
                        f"VALUES (?, {', '.join('?' * len(ODDS_FIELDS))})",
                        [(t, *[r.get(c) for c in ODDS_FIELDS]) for r in fresh])


# ------------------------------------------------------------------ predictions (model vs result)
PRED_COLS = ["sport", "comp", "game_date", "home", "away", "kickoff", "predicted_at", "model_margin", "market_margin",
             "model_total", "market_total", "p_home", "p_draw", "p_away", "m_home", "m_draw", "m_away",
             "xg_home", "xg_away", "home_score", "away_score", "state", "espn_id"]


def save_prediction(p: dict) -> None:
    """Insert or refresh a game's pre-game prediction. Once a game has started, its prediction is frozen
    and only the score/state fields are updated."""
    with connect() as con:
        cur = con.execute("SELECT state FROM predictions WHERE sport=? AND home=? AND away=? AND game_date=?",
                          (p["sport"], p["home"], p["away"], p["game_date"])).fetchone()
        if not cur and p.get("state") in ("in", "post"):
            return  # no honest pre-game prediction exists for a game that had already started
        if cur and cur[0] in ("in", "post"):
            con.execute("UPDATE predictions SET home_score=?, away_score=?, state=? "
                        "WHERE sport=? AND home=? AND away=? AND game_date=?",
                        (p.get("home_score"), p.get("away_score"), p.get("state"),
                         p["sport"], p["home"], p["away"], p["game_date"]))
        else:
            con.execute(f"INSERT OR REPLACE INTO predictions ({', '.join(PRED_COLS)}) "
                        f"VALUES ({', '.join('?' * len(PRED_COLS))})", [p.get(c) for c in PRED_COLS])


def update_score(sport, home, away, game_date, home_score, away_score, state) -> None:
    with connect() as con:
        con.execute("UPDATE predictions SET home_score=?, away_score=?, state=? "
                    "WHERE sport=? AND home=? AND away=? AND game_date=?",
                    (home_score, away_score, state, sport, home, away, game_date))


def predictions() -> pd.DataFrame:
    with connect() as con:
        return pd.read_sql_query("SELECT * FROM predictions ORDER BY kickoff", con)


# ------------------------------------------------------------------ picks
PICK_COLS = ["sport", "comp", "game_date", "home", "away", "market", "selection", "line", "odds", "book",
             "model_prob", "fair_odds", "edge", "stake", "source"]


def add_pick(p: dict) -> bool:
    """Insert a pick; returns False if the same pick was already logged (first price is kept)."""
    vals = [p.get(c) for c in PICK_COLS]
    with connect() as con:
        cur = con.execute(f"INSERT OR IGNORE INTO picks (created_at, {', '.join(PICK_COLS)}) "
                          f"VALUES (?, {', '.join('?' * len(PICK_COLS))})", [now(), *vals])
        return cur.rowcount > 0


def picks(status: str | None = None) -> pd.DataFrame:
    with connect() as con:
        q = "SELECT * FROM picks" + (" WHERE status=?" if status else "") + " ORDER BY game_date, id"
        return pd.read_sql_query(q, con, params=(status,) if status else ())


def update_pick(pid: int, **fields) -> None:
    keys = ", ".join(f"{k}=?" for k in fields)
    with connect() as con:
        con.execute(f"UPDATE picks SET {keys} WHERE id=?", [*fields.values(), pid])


def delete_pick(pid: int) -> None:
    with connect() as con:
        con.execute("DELETE FROM picks WHERE id=?", (pid,))
