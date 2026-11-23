"""Walk-forward soccer backtest against sharp closing prices.

Every week the model is refit using only matches played before that week, then it prices that
week's games. We score it against Pinnacle / Betfair Exchange closing odds (the sharpest public
benchmark - the same prices DraftKings and FanDuel lean on), test blends of model + market, and
simulate betting the *opening* line to measure closing-line value (CLV).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import odds as O
from .soccer import SoccerModel, SoccerParams

EVAL_COMPS = ["EPL", "LALIGA", "SERIEA", "BUNDES", "LIGUE1", "UCL", "UEL"]


def walk_forward(df: pd.DataFrame, start: str, end: str | None = None, params: SoccerParams | None = None,
                 step_days: int = 7, verbose: bool = True) -> pd.DataFrame:
    params = params or SoccerParams()
    end_ts = pd.Timestamp(end) if end else df["date"].max() + pd.Timedelta(days=1)
    t = pd.Timestamp(start)
    out = []
    while t < end_ts:
        nxt = t + pd.Timedelta(days=step_days)
        wk = df[(df["date"] >= t) & (df["date"] < nxt) & df["hg"].notna() & df["comp"].isin(EVAL_COMPS)]
        if len(wk):
            m = SoccerModel(params).fit(df, as_of=t)
            known = set(m.teams)
            for r in wk.itertuples():
                if r.home not in known or r.away not in known:
                    continue
                mk = m.predict(r.home, r.away, r.comp, bool(r.neutral))
                p = mk.x12()
                sub = m.uefa_model if (r.comp in ("UCL", "UEL", "UECL") and getattr(m, "uefa_model", None)) else m
                out.append({"date": r.date, "comp": r.comp, "season": getattr(r, "season", None),
                            "home": r.home, "away": r.away, "lh": mk.lh, "la": mk.la, "rho": sub.rho,
                            "hg": r.hg, "ag": r.ag, "pH": p[0], "pD": p[1], "pA": p[2],
                            "pO25": mk.total(2.5)[0], "cH": r.cH, "cD": r.cD, "cA": r.cA,
                            "cO25": r.cO25, "cU25": r.cU25, "oH": r.oH, "oD": r.oD, "oA": r.oA})
        if verbose:
            print(f"\r  backtest through {t.date()}  ({len(out)} matches)", end="")
        t = nxt
    if verbose:
        print()
    return pd.DataFrame(out)


def _logloss(P, y):
    return float(-np.mean(np.log(np.clip(P[np.arange(len(y)), y], 1e-12, 1))))


def _brier(P, y):
    Y = np.zeros_like(P)
    Y[np.arange(len(y)), y] = 1
    return float(np.mean(np.sum((P - Y) ** 2, axis=1)))


def evaluate(bt: pd.DataFrame, edge_min: float = 0.03) -> dict:
    u = bt[bt["comp"].isin(["UCL", "UEL"])]
    res = {}
    if len(u):  # no odds in the UEFA feed: score against outcomes, with a home/draw/away base rate
        yu = np.where(u.hg > u.ag, 0, np.where(u.hg == u.ag, 1, 2))
        base = np.bincount(yu, minlength=3) / len(yu)
        res["uefa_n"] = len(u)
        res["uefa_ll"] = _logloss(u[["pH", "pD", "pA"]].to_numpy(), yu)
        res["uefa_base_ll"] = _logloss(np.tile(base, (len(u), 1)), yu)
    bt = bt[~bt["comp"].isin(["UCL", "UEL"])]
    d = bt.dropna(subset=["cH", "cD", "cA"]).copy()
    y = np.where(d.hg > d.ag, 0, np.where(d.hg == d.ag, 1, 2))
    Pm = d[["pH", "pD", "pA"]].to_numpy()
    Pk = np.array([O.devig(r, "shin") for r in d[["cH", "cD", "cA"]].to_numpy()])
    res.update({"n": len(d), "model_logloss": _logloss(Pm, y), "market_logloss": _logloss(Pk, y),
                "model_brier": _brier(Pm, y), "market_brier": _brier(Pk, y)})
    grid = np.round(np.arange(0, 1.01, 0.05), 2)
    lls = [_logloss(O.pool(Pm, Pk, w), y) for w in grid]
    res["best_w"] = float(grid[int(np.argmin(lls))])
    res["blend_logloss"] = float(min(lls))
    res["by_comp"] = {}
    for c, g in d.groupby("comp"):
        ii = d.index.get_indexer(g.index)
        res["by_comp"][c] = (len(g), _logloss(Pm[ii], y[ii]), _logloss(Pk[ii], y[ii]))

    # totals 2.5
    t = d.dropna(subset=["cO25", "cU25"])
    if len(t):
        yo = ((t.hg + t.ag) > 2.5).astype(int).to_numpy()
        pm = t["pO25"].to_numpy()
        pk = np.array([O.devig(r, "shin")[0] for r in t[["cO25", "cU25"]].to_numpy()])
        ll = lambda p: float(-np.mean(yo * np.log(p) + (1 - yo) * np.log(1 - p)))
        res["ou25_n"], res["ou25_model_ll"], res["ou25_market_ll"] = len(t), ll(pm), ll(pk)

    # betting simulation on opening prices with the market-blended probabilities
    b = d.dropna(subset=["oH", "oD", "oA"])
    if len(b):
        ii = d.index.get_indexer(b.index)
        Po = np.array([O.devig(r, "shin") for r in b[["oH", "oD", "oA"]].to_numpy()])
        res["open_n"] = len(b)
        res["open_model_ll"] = _logloss(Pm[ii], y[ii])
        res["open_market_ll"] = _logloss(Po, y[ii])
        res["open_blend_ll"] = min(_logloss(O.pool(Pm[ii], Po, w), y[ii]) for w in grid)
        res["open_best_w"] = float(grid[int(np.argmin([_logloss(O.pool(Pm[ii], Po, w), y[ii]) for w in grid]))])
        # at bet time you only know the opening market, so blend the model with that
        Popen = np.array([O.devig(r, "shin") for r in b[["oH", "oD", "oA"]].to_numpy()])
        Pb = O.pool(Pm[ii], Popen, max(res["best_w"], 0.3))
        Oo = b[["oH", "oD", "oA"]].to_numpy()
        Cfair = 1 / Pk[ii]
        bets = []
        for k in range(len(b)):
            for s in range(3):
                e = Pb[k, s] * Oo[k, s] - 1
                if e > edge_min:
                    st = O.kelly(Pb[k, s], Oo[k, s])
                    won = y[ii][k] == s
                    bets.append({"edge": e, "stake": st, "pnl": st * ((Oo[k, s] - 1) if won else -1),
                                 "flat": (Oo[k, s] - 1) if won else -1, "clv": Oo[k, s] / Cfair[k, s] - 1})
        bets = pd.DataFrame(bets)
        if len(bets):
            res["bets"] = len(bets)
            res["flat_roi"] = float(bets.flat.mean())
            res["avg_clv"] = float(bets.clv.mean())
            res["pct_beat_close"] = float((bets.clv > 0).mean())
            res["kelly_growth"] = float(bets.pnl.sum())
    return res


def report(res: dict) -> str:
    L = [f"Matches scored: {res['n']}",
         f"1X2 log-loss  model {res['model_logloss']:.4f} | sharp close {res['market_logloss']:.4f} | "
         f"blend {res['blend_logloss']:.4f} (model weight {res['best_w']:.2f})",
         f"1X2 Brier     model {res['model_brier']:.4f} | sharp close {res['market_brier']:.4f}"]
    for c, (n, a, b) in res["by_comp"].items():
        L.append(f"   {c:<7} n={n:<5} model {a:.4f}  close {b:.4f}")
    if "ou25_n" in res:
        L.append(f"O/U 2.5 log-loss model {res['ou25_model_ll']:.4f} | close {res['ou25_market_ll']:.4f} (n={res['ou25_n']})")
    if "uefa_n" in res:
        L.append(f"UCL/UEL (no odds in feed): model log-loss {res['uefa_ll']:.4f} vs base-rate {res['uefa_base_ll']:.4f} (n={res['uefa_n']})")
    if "open_n" in res:
        L.append(f"vs early prices (n={res['open_n']}): model {res['open_model_ll']:.4f} | early market "
                 f"{res['open_market_ll']:.4f} | blend {res['open_blend_ll']:.4f} (model weight {res['open_best_w']:.2f})")
    if "bets" in res:
        L.append(f"Bets vs opening line: {res['bets']}  flat ROI {res['flat_roi']:+.1%}  "
                 f"avg CLV {res['avg_clv']:+.1%}  beat close {res['pct_beat_close']:.0%}")
    return "\n".join(L)
