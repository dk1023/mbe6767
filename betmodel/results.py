"""Model vs books vs what actually happened, for every finished game the model priced."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import db

COMP_NAMES = {"NFL": "NFL", "EPL": "Premier League", "LALIGA": "La Liga", "SERIEA": "Serie A",
              "UCL": "Champions League", "UEL": "Europa League"}


def _side(home, away, m):
    if m is None or pd.isna(m):
        return "-"
    if abs(m) < 0.05:
        return "Pick'em"
    return f"{home if m > 0 else away} by {abs(m):.1f}"


def table() -> tuple[pd.DataFrame, dict]:
    """(rows for display, summary stats). Only games ESPN shows as final."""
    p = db.predictions()
    p = p[(p["state"] == "post") & p["home_score"].notna()]
    rows, nfl, soc = [], [], []
    for r in p.itertuples():
        hs, as_ = int(r.home_score), int(r.away_score)
        base = {"date": pd.Timestamp(r.game_date).strftime("%a %d %b"), "league": COMP_NAMES.get(r.comp, r.comp),
                "sport": r.sport}
        if r.sport == "nfl":
            actual = hs - as_
            me = abs(actual - r.model_margin)
            be = abs(actual - r.market_margin) if pd.notna(r.market_margin) else np.nan
            closer = ("Model" if me < be - 0.25 else "Books" if be < me - 0.25 else "Tie") if np.isfinite(be) else "-"
            lean, lean_res = "", ""
            if pd.notna(r.market_margin) and abs(r.model_margin - r.market_margin) >= 1.0:
                home_lean = r.model_margin > r.market_margin
                lean = f"{r.home if home_lean else r.away} vs line"
                cov = actual - r.market_margin
                lean_res = "push" if abs(cov) < 1e-9 else ("won" if (cov > 0) == home_lean else "lost")
            tot_res = ""
            if pd.notna(r.market_total) and abs(r.model_total - r.market_total) >= 1.5:
                over = r.model_total > r.market_total
                d = (hs + as_) - r.market_total
                tot_res = f"{'Over' if over else 'Under'}: " + ("push" if abs(d) < 1e-9 else "won" if (d > 0) == over else "lost")
            rows.append({**base, "match": f"{r.away} @ {r.home}", "final": f"{r.away} {as_} - {r.home} {hs}",
                         "result": _side(r.home, r.away, actual).replace(".0", ""),
                         "model said": _side(r.home, r.away, r.model_margin),
                         "books said": _side(r.home, r.away, r.market_margin),
                         "closer": closer, "model lean ATS": f"{lean} - {lean_res}" if lean else "no lean",
                         "model total lean": tot_res or "no lean"})
            nfl.append({"me": me, "be": be, "closer": closer, "lean": lean_res, "tot": tot_res.split(": ")[-1] if tot_res else ""})
        else:
            out = 0 if hs > as_ else 1 if hs == as_ else 2
            pm = [r.p_home, r.p_draw, r.p_away]
            pk = [r.m_home, r.m_draw, r.m_away]
            have_k = all(pd.notna(x) for x in pk)
            y = np.eye(3)[out]
            brier_m = float(np.sum((np.array(pm) - y) ** 2))
            brier_k = float(np.sum((np.array(pk, float) - y) ** 2)) if have_k else np.nan
            fav_hit = int(np.argmax(pm)) == out
            label = ["home win", "draw", "away win"][out]
            closer = ("Model" if pm[out] > pk[out] + 0.01 else "Books" if pk[out] > pm[out] + 0.01 else "Tie") if have_k else "-"
            rows.append({**base, "match": f"{r.home} v {r.away}", "final": f"{r.home} {hs} - {as_} {r.away}",
                         "result": label,
                         "model said": f"{pm[0]:.0%} / {pm[1]:.0%} / {pm[2]:.0%}",
                         "books said": f"{pk[0]:.0%} / {pk[1]:.0%} / {pk[2]:.0%}" if have_k else "-",
                         "closer": closer,
                         "model lean ATS": f"model gave the result {pm[out]:.0%}" + (f" vs books {pk[out]:.0%}" if have_k else ""),
                         "model total lean": (f"xG {r.xg_home:.1f}-{r.xg_away:.1f}" if pd.notna(r.xg_home) else "")})
            soc.append({"pm": pm[out], "pk": pk[out] if have_k else np.nan, "bm": brier_m, "bk": brier_k, "fav": fav_hit,
                        "closer": closer})
    stats = {}
    if nfl:
        n = pd.DataFrame(nfl)
        lw, ll = (n["lean"] == "won").sum(), (n["lean"] == "lost").sum()
        tw, tl = (n["tot"] == "won").sum(), (n["tot"] == "lost").sum()
        stats["nfl"] = {"games": len(n), "model_closer": int((n["closer"] == "Model").sum()),
                        "books_closer": int((n["closer"] == "Books").sum()),
                        "model_err": float(n["me"].mean()), "books_err": float(n["be"].mean()),
                        "lean_record": f"{lw}-{ll}", "total_record": f"{tw}-{tl}"}
    if soc:
        s = pd.DataFrame(soc)
        stats["soccer"] = {"games": len(s), "model_closer": int((s["closer"] == "Model").sum()),
                           "books_closer": int((s["closer"] == "Books").sum()),
                           "brier_model": float(s["bm"].mean()), "brier_books": float(s["bk"].mean()),
                           "fav_hit": float(s["fav"].mean())}
    out = pd.DataFrame(rows)
    return (out.iloc[::-1].reset_index(drop=True) if len(out) else out), stats


# ------------------------------------------------------------------ the model's prediction for a game
def _call(s: dict, exact: str) -> str:
    """The most common simulated outcome. In a dead heat (top two within 0.5% of the runs, which is simulation
    noise) the model's exact favourite decides, so noise can't flip a coin-flip game's call."""
    counts = {"H": s["home_wins"], "A": s["away_wins"]}
    if s["sport"] == "soccer":
        counts["D"] = s["ties"]
    top = sorted(counts.items(), key=lambda kv: -kv[1])
    if len(top) > 1 and top[0][1] - top[1][1] < 0.005 * s["n"] and exact in counts:
        return exact
    return top[0][0]


def predict_nfl(home, away, mu, tau, p_home=None, names=None, key: str = "") -> dict | None:
    """The model's call after simulating the game 10,000 times (simulate.nfl): the team that won most often,
    with the final score that came up most often among its wins. prob = its share of the 10,000."""
    if mu is None or tau is None or pd.isna(mu) or pd.isna(tau):
        return None
    from . import simulate
    nm = names or {}
    ph = None if p_home is None or pd.isna(p_home) else float(p_home)
    s = simulate.nfl(round(float(mu), 3), round(float(tau), 3), None if ph is None else round(ph, 4), f"{home}|{away}|{key}")
    res = _call(s, "H" if mu >= 0 else "A")
    winner = home if res == "H" else away
    m = s["mode_home"] if res == "H" else s["mode_away"]
    h, a = (m[0], m[1]) if m else (int(round((tau + mu) / 2)), int(round((tau - mu) / 2)))
    wins = s["home_wins"] if res == "H" else s["away_wins"]
    return {"winner": winner, "result": res, "score": (h, a), "prob": wins / s["n"], "sim": s,
            "text": f"{nm.get(winner, winner)} {max(h, a)}-{min(h, a)}"}


def predict_soccer(home, away, p3, xg, names=None, rho: float = -0.05, key: str = "") -> dict | None:
    """The model's call after simulating the match 10,000 times from its expected goals (simulate.soccer): the
    result that came up most often (home win / draw / away win) and its most common scoreline."""
    if p3 is None or any(x is None or pd.isna(x) for x in p3):
        return None
    nm = names or {}
    s = None
    if xg is not None and all(x is not None and pd.notna(x) for x in xg):
        from . import simulate
        s = simulate.soccer(round(float(xg[0]), 3), round(float(xg[1]), 3), rho, f"{home}|{away}|{key}")
    if s:
        res = _call(s, "HDA"[int(np.argmax(p3))])
        m = {"H": s["mode_home"], "D": s["mode_draw"], "A": s["mode_away"]}[res]
        hg, ag = (m[0], m[1]) if m else (None, None)
        prob = {"H": s["home_wins"], "D": s["ties"], "A": s["away_wins"]}[res] / s["n"]
    else:  # no expected goals: the model's most likely result, no scoreline
        res, hg, ag = "HDA"[int(np.argmax(p3))], None, None
        prob = float(max(p3))
    who = {"H": nm.get(home, home), "D": "Draw", "A": nm.get(away, away)}[res]
    sc = f" {hg}-{ag}" if hg is not None else ""
    if res == "A" and hg is not None:
        sc = f" {ag}-{hg}"  # winner's goals first
    return {"winner": {"H": home, "D": None, "A": away}[res], "result": res, "score": (hg, ag),
            "prob": prob, "sim": s, "text": f"{who}{sc}"}


def prediction_hit(pred: dict | None, hs, as_) -> bool | None:
    if pred is None or hs is None or as_ is None or pd.isna(hs) or pd.isna(as_):
        return None
    actual = "H" if hs > as_ else "A" if as_ > hs else "D"
    return pred["result"] == actual


# ------------------------------------------------------------------ the model's best bet on every game
KINDS = {"auto": ("rec", "RECOMMENDED"), "lean": ("lean", "LEAN"), "leastbad": ("neg", "LEAST-BAD")}
SPORTS = [("nfl", "NFL"), ("soccer", "Soccer")]


def best_bets() -> pd.DataFrame:
    """One bet per game: the recommended bet if there was one, otherwise the tracked lean / least-bad bet.
    Adds u1 = profit if exactly 1 unit had been staked (won: odds - 1, lost: -1, push: 0)."""
    d = db.picks()
    if d.empty:
        return d
    d = d[d["source"].isin(list(KINDS))].copy()
    d["prio"] = d["source"].map({"auto": 0, "lean": 1, "leastbad": 2})
    d = d.sort_values(["prio", "id"]).drop_duplicates(["sport", "game_date", "home", "away"], keep="first")
    d["u1"] = np.where(d["status"] == "won", d["odds"] - 1, np.where(d["status"] == "lost", -1.0, 0.0))
    p = db.predictions()
    if len(p):
        d = d.merge(p[["sport", "home", "away", "game_date", "kickoff", "model_margin", "market_margin",
                       "model_total", "p_home", "p_draw", "p_away", "m_home", "m_draw", "m_away", "xg_home", "xg_away",
                       "home_score", "away_score"]],
                    on=["sport", "home", "away", "game_date"], how="left")
    else:
        for c in ("model_margin", "market_margin", "model_total", "p_home", "p_draw", "p_away", "m_home", "m_draw",
                  "m_away", "xg_home", "xg_away", "home_score", "away_score"):
            d[c] = np.nan
    d = d.sort_values(["game_date", "id"]).reset_index(drop=True)
    preds, hits = [], []
    for r in d.itertuples():
        hs, as_ = r.home_score, r.away_score
        if (pd.isna(hs) or pd.isna(as_)) and isinstance(r.score, str):  # fall back to the graded score text
            hs, as_ = _parse_score(r.sport, r.score)
        pr = row_prediction(r)
        preds.append(pr)
        hits.append(prediction_hit(pr, hs, as_) if r.status in ("won", "lost", "push") else None)
    d["pred"], d["pred_hit"] = preds, hits
    return d


def _parse_score(sport, s):
    import re
    nums = [int(x) for x in re.findall(r"\d+", s or "")]
    if len(nums) < 2:
        return None, None
    return (nums[1], nums[0]) if sport == "nfl" else (nums[0], nums[1])  # NFL text is 'AWAY a - HOME h'


def row_prediction(r) -> dict | None:
    if r.sport == "nfl":
        return predict_nfl(r.home, r.away, r.model_margin, r.model_total, r.p_home, key=str(r.game_date))
    return predict_soccer(r.home, r.away, [r.p_home, r.p_draw, r.p_away], [r.xg_home, r.xg_away], key=str(r.game_date))


def pred_record(d: pd.DataFrame) -> dict:
    h = [x for x in d["pred_hit"] if x is not None] if len(d) else []
    w = sum(1 for x in h if x)
    return {"w": w, "l": len(h) - w, "n": len(h), "pct": w / len(h) if h else None}


def bet_record(d: pd.DataFrame) -> dict:
    s = d[d["status"].isin(["won", "lost", "push"])] if len(d) else d
    w, l, pu = (int((s["status"] == x).sum()) for x in ("won", "lost", "push")) if len(s) else (0, 0, 0)
    u = float(s["u1"].sum()) if len(s) else 0.0
    return {"w": w, "l": l, "p": pu, "n": len(s), "units": u, "roi": u / len(s) if len(s) else None,
            "hit": w / (w + l) if (w + l) else None,
            "open": int((d["status"] == "open").sum()) if len(d) else 0}


def _rec_txt(r):
    return f"{r['w']}-{r['l']}" + (f"-{r['p']}" if r["p"] else "")


def _said(r) -> tuple[str, str]:
    if r.sport == "nfl":
        return _side(r.home, r.away, r.model_margin), _side(r.home, r.away, r.market_margin)
    pm = [r.p_home, r.p_draw, r.p_away]
    pk = [r.m_home, r.m_draw, r.m_away]
    f = lambda v: f"{v[0]:.0%} / {v[1]:.0%} / {v[2]:.0%}" if all(pd.notna(x) for x in v) else "-"
    return f(pm), f(pk)


def view(unit: float = 10.0, limit: int = 200):
    """The Results page: record up top, then a section per sport with every graded best bet
    (green = hit, red = miss) and the running total if 1 unit had gone on each."""
    from htmltools import tags
    from . import engine
    from .odds import to_american
    d = best_bets()
    if d.empty or not d["status"].isin(["won", "lost", "push"]).any():
        return tags.div("No graded bets yet. After games finish, press Update scores (free).", class_="empty")
    amer = lambda x: to_american(x) if pd.notna(x) and x > 1 else "-"

    def kpi(k, v, sub, tone=None):
        return tags.div(tags.div(k, class_="k"), tags.div(v, class_="v"), tags.div(sub, class_="s"),
                        class_=f"rkpi {tone}" if tone else "rkpi")

    def money(r):
        return f"{r['units']:+.2f}u (${r['units'] * unit:+,.2f})"

    tot = bet_record(d)
    ptot = pred_record(d)
    rec = bet_record(d[d["source"] == "auto"])
    top = tags.div(
        tags.div(tags.h4("How the bets did"),
                 tags.div("Recommended bets are the ones worth money: DraftKings beats the sharp price (Kalshi, no vig) "
                          "by your minimum edge. Every other game shows the model's lean or least-bad bet, which "
                          "aren't expected to profit. Profit assumes exactly 1 unit on every bet. The prediction column "
                          "is the model's own call on who wins (soccer: home win / draw / away win).", class_="note"),
                 class_="rhead"),
        tags.div(kpi("Recommended bets", _rec_txt(rec) if rec["n"] else "-",
                     (f"{money(rec)} at 1u each" + (f" · {rec['open']} open" if rec["open"] else "")) if rec["n"]
                     else f"{rec['open']} open, none graded yet",
                     tone=("pos" if rec["units"] >= 0 else "neg") if rec["n"] else None),
                 kpi("Every game (incl. leans & least-bad)", _rec_txt(tot),
                     f"{tot['hit']:.1%} hit · {tot['open']} still open" if tot["hit"] is not None else f"{tot['open']} open"),
                 kpi("1 unit on every game", money(tot), f"ROI {tot['roi']:+.1%} on {tot['n']} bets" if tot["roi"] is not None else "",
                     tone=("pos" if tot["units"] >= 0 else "neg") if tot["n"] else None),
                 kpi("Predictions right", f"{ptot['w']}-{ptot['l']}" if ptot["n"] else "-",
                     f"{ptot['pct']:.0%} called the result" if ptot["pct"] is not None else ""),
                 *[kpi(lbl, _rec_txt(r), f"{money(r)}") for sp, lbl in SPORTS
                   for r in [bet_record(d[d["sport"] == sp])] if r["n"]],
                 class_="rkpis"))
    sections = [top]
    for sp, label in SPORTS:
        x = d[d["sport"] == sp]
        if x.empty:
            continue
        r = bet_record(x)
        kinds = []
        for src, (cls, lab) in KINDS.items():
            kr = bet_record(x[x["source"] == src])
            if kr["n"] or kr["open"]:
                kinds.append(tags.div(tags.span(lab, class_=f"kchip {cls}"),
                                      tags.b(f" {_rec_txt(kr)}" if kr["n"] else " -"),
                                      tags.span(f"  {kr['units']:+.2f}u" if kr["n"] else f"  {kr['open']} open",
                                                class_="ku"), class_="kindrow"))
        g = x[x["status"].isin(["won", "lost", "push"])].copy()
        g["run"] = g["u1"].cumsum()
        rows = []
        for t in g.iloc[::-1].head(limit).itertuples():
            cls, lab = KINDS[t.source]
            res = {"won": "HIT", "lost": "MISS", "push": "PUSH"}[t.status]
            ms, bs = _said(t)
            pr = t.pred
            mark = "" if t.pred_hit is None else (" ✓" if t.pred_hit else " ✗")
            pcell = tags.td(tags.span((pr["text"] if pr else ms) + mark,
                                      class_="pok" if t.pred_hit else "pbad" if t.pred_hit is False else ""),
                            tags.small((f"{pr['prob']:.0%} · " if pr and pr.get("prob") is not None else "") + f"books {bs}"))
            match = f"{t.away} @ {t.home}" if sp == "nfl" else f"{t.home} v {t.away}"
            rows.append(tags.tr(
                tags.td(pd.Timestamp(t.game_date).strftime("%a %d %b")),
                tags.td(match, tags.small(COMP_NAMES.get(t.comp, t.comp)) if sp == "soccer" else None),
                tags.td(t.score or ""),
                tags.td(tags.span(lab, class_=f"kchip {cls}"), " ",
                        tags.b(f"{engine.describe_bet(t._asdict())} {amer(t.odds)}"),
                        tags.small(f"edge {t.edge:+.1%}" if pd.notna(t.edge) else "")),
                tags.td(tags.span(res, class_=f"res {t.status}")),
                tags.td(f"{t.u1:+.2f}u"), tags.td(f"{t.run:+.2f}u"),
                pcell,
                class_=f"rrow {t.status}"))
        pr_ = pred_record(x)
        sections.append(tags.div(
            tags.div(tags.h4(label), tags.div(
                tags.span(f"{_rec_txt(r)}", class_="big"),
                tags.span(f"{r['hit']:.1%} hit" if r["hit"] is not None else ""),
                tags.span(f"1u each: {money(r)}", class_="pos" if r["units"] >= 0 else "neg"),
                tags.span(f"predictions {pr_['w']}-{pr_['l']}" if pr_["n"] else ""),
                tags.span(f"{r['open']} open" if r["open"] else ""), class_="srec"), class_="shead"),
            tags.div(*kinds, class_="kinds"),
            tags.div(tags.table(tags.thead(tags.tr(*[tags.th(h) for h in
                                                     ["Date", "Game", "Final", "Best bet", "Result", "1u P/L",
                                                      "Running", "Prediction"]])),
                                tags.tbody(*rows), class_="bk rtable"), class_="sect") if rows else
            tags.div("Nothing graded yet.", class_="note"),
            class_="rsec"))
    return tags.div(*sections)


# Styles for this page live in theme.py (shared by the app and the phone page).


def summary_lines(stats: dict) -> list[tuple[str, str]]:
    """(label, value) pairs for the KPI boxes."""
    out = []
    if "nfl" in stats:
        s = stats["nfl"]
        out += [("NFL: closer to the final margin", f"Model {s['model_closer']} - Books {s['books_closer']}"),
                ("NFL: avg miss (points)", f"Model {s['model_err']:.1f} - Books {s['books_err']:.1f}"),
                ("NFL: model leans vs the line", f"Spread {s['lean_record']}  |  Total {s['total_record']}")]
    if "soccer" in stats:
        s = stats["soccer"]
        out += [("Soccer: more weight on the actual result", f"Model {s['model_closer']} - Books {s['books_closer']}"),
                ("Soccer: Brier score (lower is better)", f"Model {s['brier_model']:.3f} - Books {s['brier_books']:.3f}"),
                ("Soccer: model favourite won", f"{s['fav_hit']:.0%} of {s['games']}")]
    return out
