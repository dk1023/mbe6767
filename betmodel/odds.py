"""Odds utilities: format conversion, vig removal, fair prices, edge and Kelly staking."""
from __future__ import annotations

import numpy as np
from scipy.optimize import brentq


def parse_odds(x) -> float:
    """Accept American (-110, +150) or decimal (1.91) odds; return decimal."""
    s = str(x).strip()
    v = float(s)
    if s.startswith(("+", "-")) or abs(v) >= 100:
        return 1 + (v / 100 if v > 0 else 100 / -v)
    if v <= 1:
        raise ValueError(f"bad odds {x}")
    return v


def to_american(dec: float) -> str:
    if not np.isfinite(dec) or dec <= 1:
        return "n/a"
    return f"+{(dec - 1) * 100:.0f}" if dec >= 2 else f"{-100 / (dec - 1):.0f}"


def fair_odds(p: float) -> tuple[float, str]:
    p = float(np.clip(p, 1e-9, 1))
    return 1 / p, to_american(1 / p)


def overround(decimals) -> float:
    return float(np.sum(1 / np.asarray(decimals, float)) - 1)


def devig(decimals, method: str = "shin") -> np.ndarray:
    """Remove the bookmaker margin. 'shin' and 'power' correct favourite-longshot bias
    (books shade longshots more), which is how sharp shops back out true prices."""
    q = 1 / np.asarray(decimals, float)
    if not np.all(np.isfinite(q)) or np.any(q <= 0):
        return np.full(len(q), np.nan)
    if method == "mult":
        return q / q.sum()
    if method == "additive":
        return q - (q.sum() - 1) / len(q)
    if method == "power":
        k = brentq(lambda k: np.sum(q ** k) - 1, 0.5, 3.0)
        return q ** k
    if method == "shin":
        n, s = len(q), q.sum()
        if s <= 1:
            return q / s

        def probs(z):
            return (np.sqrt(z ** 2 + 4 * (1 - z) * q ** 2 / s) - z) / (2 * (1 - z))

        z = brentq(lambda z: probs(z).sum() - 1, 0.0, 0.4)
        p = probs(z)
        return p / p.sum()
    raise ValueError(method)


def edge(p: float, dec: float) -> float:
    """Expected value per unit staked."""
    return p * dec - 1


def kelly(p: float, dec: float, fraction: float = 0.25, cap: float = 0.05) -> float:
    """Fractional Kelly stake as share of bankroll (quarter Kelly, capped at 5% by default)."""
    b = dec - 1
    f = (p * b - (1 - p)) / b
    return float(np.clip(f * fraction, 0, cap))


def pool(p_model, p_market, w_model: float) -> np.ndarray:
    """Logarithmic opinion pool: p ~ p_model^w * p_market^(1-w). This is the standard way to
    combine a model with a sharp market; w is fitted in the backtests."""
    a = np.clip(np.asarray(p_model, float), 1e-9, 1)
    b = np.clip(np.asarray(p_market, float), 1e-9, 1)
    z = np.exp(w_model * np.log(a) + (1 - w_model) * np.log(b))
    return z / z.sum(axis=-1, keepdims=True)
