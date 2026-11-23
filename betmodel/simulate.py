"""Simulate every game 100,000 times from the model's own numbers and count the outcomes.

The model already works out exact chances (what infinitely many simulations would converge to), so
simulating doesn't change who it favours or by how much: 100,000 runs land within about a fifth of a point
of the exact chance. What it adds is the outcome as counts, the most common final score, and the
simulation's own spread and total.

NFL     Played drive by drive. Each team gets 9-11 scoring chances (same count for both). A drive ends in a
        touchdown (6, 7 or 8 points), a field goal, a safety for the defence, or nothing, with chances set so
        each team averages the points the model expects. A tie goes to overtime (usually a field goal or
        touchdown wins it; about 1 in 10 stays tied). The simulated games are then reweighted so their final
        margins follow the model's own margin distribution (real key numbers: 3, 7, 10 ...), with the model's
        projected margin as the 50/50 line, while each game keeps its realistic score. So the simulation's
        spread and win chances agree with the model's.
Soccer  Goals drawn from the model's score grid: Poisson goals with the Dixon-Coles low-score correction.

Seeds are fixed per game, so the same inputs always give the same simulation. Results are also saved in the
local database, so each game is only simulated once.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache

import numpy as np

N_SIMS = 100_000
CALIB_SIMS = 20_000                  # the win-rate matching runs on the first 20,000 (same random numbers)
DRIVES, DRIVE_P = np.array([9, 10, 11]), np.array([0.25, 0.50, 0.25])
MEAN_DRIVES = float((DRIVES * DRIVE_P).sum())
FG_PER_TD = 0.70                     # field-goal drives per touchdown drive (league-average shape)
TD_PTS, TD_P = np.array([6, 7, 8]), np.array([0.04, 0.93, 0.03])  # missed PAT / PAT / two-point try
TD_MEAN = float((TD_PTS * TD_P).sum())
SAFETY_P = 0.004                     # per scoreless drive: 2 points to the defence
OT_TIE_P = 0.10                      # overtime that ends tied (regular season)
OT_TD_P = 0.40                       # overtime won by a touchdown (+6), else a field goal (+3)
MAX_D = int(DRIVES.max())


def _seed(*key) -> int:
    return int(hashlib.md5("|".join(map(str, key)).encode()).hexdigest()[:8], 16)


def _top(codes: np.ndarray, k: int = 10):
    """Most common (home, away) scores from codes home*100+away, as [(home, away, count)]."""
    vals, cnt = np.unique(codes, return_counts=True)
    order = np.argsort(-cnt, kind="stable")[:k]
    return [(int(vals[i] // 100), int(vals[i] % 100), int(cnt[i])) for i in order]


def _mode_where(codes: np.ndarray, mask: np.ndarray):
    if not mask.any():
        return None
    t = _top(codes[mask], 1)[0]
    return t[0], t[1], t[2]


def _hist(x: np.ndarray) -> dict:
    v, c = np.unique(x, return_counts=True)
    return {int(a): int(b) for a, b in zip(v, c)}


def cover(s: dict, home_line: float) -> tuple[float, float, float]:
    """(win, push, lose) for the HOME side at a spread (e.g. -3.5), from the simulated margins."""
    w = p = 0
    for m, c in s["margins"].items():
        v = m + home_line
        if v > 1e-9:
            w += c
        elif abs(v) <= 1e-9:
            p += c
    n = s["n"]
    return w / n, p / n, 1 - (w + p) / n


def over_under(s: dict, line: float) -> tuple[float, float, float]:
    """(over, push, under) at a total, from the simulated totals."""
    o = p = 0
    for t, c in s["totals"].items():
        if t > line + 1e-9:
            o += c
        elif abs(t - line) <= 1e-9:
            p += c
    n = s["n"]
    return o / n, p / n, 1 - (o + p) / n


def _quarter(x: float) -> bool:
    return int(round(x * 4)) % 2 == 1


def fair_spread(s: dict) -> float:
    """The simulation's own spread: the home line where each side wins about as often as it loses (pushes left
    out). NFL in half points; soccer as an Asian handicap in quarter goals (a quarter line splits the stake)."""
    if "_spread" in s:  # the simulation dict is cached and shared, so the answer is remembered on it
        return s["_spread"]
    step = 0.25 if s["sport"] == "soccer" else 0.5
    best = None
    for L in np.arange(-40.0, 40.0 + step / 2, step):
        if step == 0.25 and _quarter(L):
            a, b = cover(s, L - 0.25), cover(s, L + 0.25)
            ev = ((a[0] - a[2]) + (b[0] - b[2])) / 2
        else:
            w, _, lo = cover(s, L)
            ev = w - lo
        if best is None or abs(ev) < abs(best[1]) - 1e-12:
            best = (float(L), ev)
    s["_spread"] = best[0]
    return best[0]


def fair_total(s: dict) -> float:
    """The simulation's own total: the line where overs and unders come up about equally often."""
    if "_total" in s:
        return s["_total"]
    step = 0.25 if s["sport"] == "soccer" else 0.5
    best = None
    for T in np.arange(step, 120.0, step):
        if step == 0.25 and _quarter(T):
            a, b = over_under(s, T - 0.25), over_under(s, T + 0.25)
            ev = ((a[0] - a[2]) + (b[0] - b[2])) / 2
        else:
            o, _, u = over_under(s, T)
            ev = o - u
        if best is None or abs(ev) < abs(best[1]) - 1e-12:
            best = (float(T), ev)
    s["_total"] = best[0]
    return best[0]


def _mode_near(codes, h, a, mask, eh: float, ea: float, w: float = 4.0):
    """Most common score among `mask` games within w points of each team's expected score. NFL exact scores are
    spread thin (the most common happens about 1 time in 100) and 3-point games like 20-17 top the list for any
    matchup, so the plain mode says little; this is the most common score that looks like the expected game."""
    near = mask & (np.abs(h - eh) <= w) & (np.abs(a - ea) <= w)
    return _mode_where(codes, near) or _mode_where(codes, mask)


@lru_cache(maxsize=4096)
def nfl(mu: float, tau: float, p_home: float | None = None, key: str = "", n: int = N_SIMS) -> dict:
    """Simulate an NFL game n times. mu = the model's projected home margin (its fair spread), tau = expected
    total, p_home = the model's home win probability (used only when the model's margin shape isn't saved yet)."""
    shape = margin_shape()
    tag = f"{shape['sd']:.3f}" if shape else "none"
    return _stored(f"sim:{VERSION}:nfl:{key}:{mu}:{tau}:{p_home}:{n}:{tag}", lambda: _nfl(mu, tau, p_home, key, n, shape))


VERSION = 3  # bump when the simulation changes, so saved results are redone


def save_margin_shape(nm) -> None:
    """Save the fitted NFL model's margin shape (key-number weights + spread) so simulations can match it."""
    from . import db
    db.cache_put("nfl:margin_shape", {"keys": {str(k): float(w) for k, w in nm.key_margin.items()},
                                      "sd": float(nm.sd_margin)})


def margin_shape() -> dict | None:
    from . import db
    try:
        s = db.cache_get("nfl:margin_shape", 24 * 365)
    except Exception:
        return None
    if not s or not s.get("keys"):
        return None
    return {"keys": {int(k): float(w) for k, w in s["keys"].items()}, "sd": float(s["sd"])}


def _decode(s: dict) -> dict:
    s = dict(s)
    s["margins"] = {int(k): v for k, v in s["margins"].items()}
    s["totals"] = {int(k): v for k, v in s["totals"].items()}
    s["top"] = [tuple(x) for x in s["top"]]
    s["avg"] = tuple(s["avg"])
    for k in ("mode_home", "mode_away", "mode_draw"):
        s[k] = tuple(s[k]) if s.get(k) else None
    return s


def _stored(key: str, compute) -> dict:
    """A simulation result saved in the local database (simulate once per game and set of numbers)."""
    from . import db
    try:
        hit = db.cache_get(key, 24 * 365)
        if hit is not None:
            return _decode(hit)
    except Exception:
        pass
    s = compute()
    try:
        db.cache_put(key, s)
    except Exception:
        pass
    return s


def _nfl(mu: float, tau: float, p_home, key: str, n: int, shape: dict | None = None) -> dict:
    rng = np.random.default_rng(_seed("nfl", key, round(mu, 3), round(tau, 3)))
    d = rng.choice(DRIVES, size=n, p=DRIVE_P)
    active = np.arange(MAX_D)[None, :] < d[:, None]
    u = rng.random((2, n, MAX_D), dtype=np.float32)    # drive outcomes (common random numbers)
    u[:, ~active] = 2.0                                 # drives a game doesn't have never score
    td_pts = rng.choice(TD_PTS, size=(2, n, MAX_D), p=TD_P).astype(np.int16)
    saf = (rng.random((2, n, MAX_D), dtype=np.float32) < SAFETY_P) & active[None]
    u_ot, u_ot2, u_tie = rng.random(n), rng.random(n), rng.random(n)

    def play(shift: float, k: int = n):
        exp = np.clip([(tau + mu + shift) / 2, (tau - mu - shift) / 2], 2.0, None)
        ptd = exp / MEAN_DRIVES / (TD_MEAN + 3 * FG_PER_TD)
        pfg = FG_PER_TD * ptd
        scale = np.minimum(1.0, 0.9 / (ptd + pfg))      # keep the scoring chance per drive below 90%
        ptd, pfg = ptd * scale, pfg * scale
        out = []
        for t in (0, 1):
            ut = u[t, :k]
            td = ut < ptd[t]
            fg = ~td & (ut < ptd[t] + pfg[t])
            out.append(((td * td_pts[t, :k]).sum(1) + 3 * fg.sum(1), (saf[t, :k] & ~td & ~fg).sum(1)))
        (ph, sh), (pa, sa) = out
        return ph + 2 * sa, pa + 2 * sh                 # a safety scores for the other team

    def win_rate(h, a):
        hw, aw = (h > a).sum(), (a > h).sum()
        return hw / max(hw + aw, 1)

    shift = 0.0
    if shape is None and p_home is not None and np.isfinite(p_home):
        # no saved margin shape yet: match the simulated win rate to the model's win probability instead
        lo, hi = -14.0, 14.0  # bisection on the first CALIB_SIMS runs (same random numbers)
        k = min(n, CALIB_SIMS)
        for _ in range(12):
            mid = (lo + hi) / 2
            if win_rate(*play(mid, k)) < p_home:
                lo = mid
            else:
                hi = mid
        shift = (lo + hi) / 2
    h, a = play(shift)
    tie = h == a                                        # overtime
    ot_home = u_ot < (p_home if p_home is not None and np.isfinite(p_home) else 0.5)
    stays = tie & (u_tie < OT_TIE_P)
    go = tie & ~stays
    add = np.where(u_ot2 < OT_TD_P, 6, 3)
    h = h + np.where(go & ot_home, add, 0)
    a = a + np.where(go & ~ot_home, add, 0)
    if shape is not None:
        # Reweight the simulated games so their final margins follow the model's own margin distribution
        # (its key numbers, and its projected margin as the 50/50 line); each game keeps its realistic score.
        from .nfl import NFLModel
        q = NFLModel._centered(shape["keys"], shape["sd"], mu)
        m = h - a
        vals, inv, cnt = np.unique(m, return_inverse=True, return_counts=True)
        target = np.array([q.get(int(v), 0.0) for v in vals])
        w = (target / cnt)[inv]
        if w.sum() > 0:
            pick = rng.choice(n, size=n, p=w / w.sum())
            h, a = h[pick], a[pick]
    codes = h * 100 + a
    eh, ea = float(h.mean()), float(a.mean())
    return {"sport": "nfl", "n": n, "home_wins": int((h > a).sum()), "away_wins": int((a > h).sum()),
            "ties": int((h == a).sum()), "avg": (eh, ea), "top": _top(codes, 8),
            "mode_home": _mode_near(codes, h, a, h > a, eh, ea), "mode_away": _mode_near(codes, h, a, a > h, eh, ea),
            "mode_draw": _mode_where(codes, h == a), "margins": _hist(h - a), "totals": _hist(h + a)}


def score_grid(lh: float, la: float, rho: float = -0.05, max_goals: int = 10) -> np.ndarray:
    from scipy.stats import poisson
    g = np.arange(max_goals)
    m = np.outer(poisson.pmf(g, lh), poisson.pmf(g, la))
    m[0, 0] *= 1 - lh * la * rho
    m[0, 1] *= 1 + lh * rho
    m[1, 0] *= 1 + la * rho
    m[1, 1] *= 1 - rho
    m = np.clip(m, 0, None)
    return m / m.sum()


@lru_cache(maxsize=4096)
def soccer(lh: float, la: float, rho: float = -0.05, key: str = "", n: int = N_SIMS) -> dict:
    """Simulate a match n times from the model's expected goals (lh, la)."""
    return _stored(f"sim:{VERSION}:soccer:{key}:{lh}:{la}:{rho}:{n}", lambda: _soccer(lh, la, rho, key, n))


def _soccer(lh: float, la: float, rho: float, key: str, n: int) -> dict:
    m = score_grid(lh, la, rho)
    rng = np.random.default_rng(_seed("soccer", key, round(lh, 3), round(la, 3)))
    k = rng.choice(m.size, size=n, p=m.ravel())
    h, a = k // m.shape[1], k % m.shape[1]
    codes = h * 100 + a
    return {"sport": "soccer", "n": n, "home_wins": int((h > a).sum()), "away_wins": int((a > h).sum()),
            "ties": int((h == a).sum()), "avg": (float(h.mean()), float(a.mean())), "top": _top(codes, 8),
            "mode_home": _mode_where(codes, h > a), "mode_away": _mode_where(codes, a > h),
            "mode_draw": _mode_where(codes, h == a), "margins": _hist(h - a), "totals": _hist(h + a)}


def most_common(s: dict) -> str:
    """'H', 'A' or 'D': the outcome that came up most often (draws only count in soccer)."""
    counts = {"H": s["home_wins"], "A": s["away_wins"]}
    if s["sport"] == "soccer":
        counts["D"] = s["ties"]
    return max(counts, key=lambda k: (counts[k], k == "H"))
