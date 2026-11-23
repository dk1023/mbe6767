"""Walk-forward NFL backtest against closing spreads, totals and moneylines."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import odds as O
from .nfl import NFLModel, NFLParams


def walk_forward(g: pd.DataFrame, seasons: list[int], params: NFLParams | None = None, verbose=True) -> pd.DataFrame:
    params = params or NFLParams()
    out = []
    for s in seasons:
        for wk, games in g[(g["season"] == s) & g["result"].notna()].groupby("week"):
            as_of = games["date"].min()
            m = NFLModel(params).fit(g, as_of=as_of)
            for r in games.itertuples():
                if r.home_team not in m.teams or r.away_team not in m.teams:
                    continue
                mu, tau = m.expected(r.home_team, r.away_team, neutral=r.neutral,
                                     rest_diff=(r.home_rest - r.away_rest) if pd.notna(r.home_rest) else 0,
                                     home_backup=r.home_backup, away_backup=r.away_backup,
                                     home_qb=r.home_qb_id, away_qb=r.away_qb_id,
                                     inj_home=m.game_injuries(r, "home"), inj_away=m.game_injuries(r, "away"),
                                     roof=getattr(r, "roof", None), wind=getattr(r, "wind", None))
                pw = m.win_prob(mu)
                pl = 1 - pw
                out.append({"season": s, "week": wk, "date": r.date, "game_id": r.game_id,
                            "home": r.home_team, "away": r.away_team,
                            "home_score": r.home_score, "away_score": r.away_score,
                            "result": r.result, "total": r.total, "model_margin": mu, "model_total": tau,
                            "spread_line": r.spread_line, "total_line": r.total_line,
                            "p_home_ml": pw / (pw + pl), "home_ml": r.home_moneyline, "away_ml": r.away_moneyline,
                            "roof": getattr(r, "roof", None), "wind": getattr(r, "wind", np.nan),
                            "temp": getattr(r, "temp", np.nan), "div_game": getattr(r, "div_game", 0)})
            if verbose:
                print(f"\r  NFL backtest {s} wk {wk} ({len(out)} games)", end="")
    if verbose:
        print()
    return pd.DataFrame(out)


def evaluate(bt: pd.DataFrame) -> dict:
    d = bt.dropna(subset=["spread_line", "total_line"]).copy()
    res = {"n": len(d)}
    e_m, e_l = d.result - d.model_margin, d.result - d.spread_line
    res["margin_rmse_model"], res["margin_rmse_close"] = float(np.sqrt((e_m ** 2).mean())), float(np.sqrt((e_l ** 2).mean()))
    res["margin_mae_model"], res["margin_mae_close"] = float(e_m.abs().mean()), float(e_l.abs().mean())
    t_m, t_l = d.total - d.model_total, d.total - d.total_line
    res["total_rmse_model"], res["total_rmse_close"] = float(np.sqrt((t_m ** 2).mean())), float(np.sqrt((t_l ** 2).mean()))
    # best linear blend of model and closing number
    a = np.linspace(0, 1, 21)
    res["spread_blend_w"] = float(a[np.argmin([((d.result - (x * d.model_margin + (1 - x) * d.spread_line)) ** 2).mean() for x in a])])
    res["total_blend_w"] = float(a[np.argmin([((d.total - (x * d.model_total + (1 - x) * d.total_line)) ** 2).mean() for x in a])])
    # ATS when the model disagrees with the closing spread
    ats = {}
    for thr in [1, 2, 3]:
        diff = d.model_margin - d.spread_line
        sel = d[diff.abs() >= thr]
        cover = np.sign(sel.result - sel.spread_line)
        pick = np.sign(sel.model_margin - sel.spread_line)
        decided = cover != 0
        ats[thr] = (int(decided.sum()), float((cover[decided] == pick[decided]).mean()) if decided.any() else np.nan)
    res["ats"] = ats
    # moneyline log loss
    ml = d.dropna(subset=["home_ml", "away_ml"])
    ml = ml[ml.result != 0]
    if len(ml):
        y = (ml.result > 0).astype(int).to_numpy()
        pk = np.array([O.devig([O.parse_odds(h), O.parse_odds(a_)], "shin")[0] for h, a_ in zip(ml.home_ml, ml.away_ml)])
        pm = np.clip(ml.p_home_ml.to_numpy(), 1e-6, 1 - 1e-6)
        ll = lambda p: float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))
        res["ml_n"], res["ml_ll_model"], res["ml_ll_close"] = len(ml), ll(pm), ll(pk)
    return res


def report(r: dict) -> str:
    L = [f"Games: {r['n']}",
         f"Margin RMSE  model {r['margin_rmse_model']:.2f} | closing spread {r['margin_rmse_close']:.2f}   "
         f"(MAE {r['margin_mae_model']:.2f} vs {r['margin_mae_close']:.2f}); best blend model weight {r['spread_blend_w']:.2f}",
         f"Total RMSE   model {r['total_rmse_model']:.2f} | closing total  {r['total_rmse_close']:.2f}; "
         f"best blend model weight {r['total_blend_w']:.2f}"]
    for thr, (n, hit) in r["ats"].items():
        L.append(f"   ATS vs close when model differs by >= {thr} pt: {hit:.1%} over {n} games (52.4% = break-even at -110)")
    if "ml_n" in r:
        L.append(f"Moneyline log-loss model {r['ml_ll_model']:.4f} | close {r['ml_ll_close']:.4f} (n={r['ml_n']})")
    return "\n".join(L)
