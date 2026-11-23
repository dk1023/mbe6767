"""Model history: what the model predicted for every past game vs what actually happened.

Two sources, shown together:
  * backtest - walk-forward replays of past seasons. Before each week the model is refit using only
    games played earlier, then it predicts that week, so it never sees the result it is predicting.
    NFL 2018-now, soccer (EPL, La Liga, Serie A, UCL, UEL) from July 2024 (the data starts in 2023
    and the model needs a season to learn from).
  * live - the frozen pre-kickoff predictions saved by real runs (the Results tab). Where both exist
    for a game, the live one is used.

"Books" are the sharp closing numbers: the NFL closing spread/total/moneyline (nflverse consensus)
and Pinnacle's no-vig closing 1X2 for soccer. UCL/UEL have no historical odds in the free data.
Building uses no API credits and logs no picks.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from htmltools import HTML, tags

from . import db

COMPS = {"NFL": "NFL", "EPL": "Premier League", "LALIGA": "La Liga", "SERIEA": "Serie A",
         "UCL": "Champions League", "UEL": "Europa League"}
SOCCER = ["EPL", "LALIGA", "SERIEA", "UCL", "UEL"]
COLS = ["sport", "comp", "season", "game_date", "home", "away", "home_score", "away_score",
        "model_margin", "market_margin", "model_total", "market_total",
        "p_home", "p_draw", "p_away", "m_home", "m_draw", "m_away", "o_home", "o_draw", "o_away", "source"]
SPREAD_LEAN, TOTAL_LEAN, SOCCER_LEAN = 2.0, 2.0, 0.05
MODEL_C, BOOKS_C = "#2563eb", "#d97706"   # validated pair (CVD-safe); text stays in ink colours


def stats_poisson(k, lam):
    from scipy.stats import poisson
    return poisson.pmf(k, lam)


def _tuned() -> dict:
    p = Path(__file__).resolve().parent / "tuned.json"
    return json.loads(p.read_text()) if p.exists() else {}


def soccer_season(ts) -> str:
    ts = pd.Timestamp(ts)
    y = ts.year if ts.month >= 7 else ts.year - 1
    return f"{y}-{(y + 1) % 100:02d}"


# ------------------------------------------------------------------------------------------ build
def build(progress=lambda f, m: None, nfl_from: int = 2018, soccer_from: str = "2024-07-01") -> int:
    """Replay past seasons walk-forward and store every prediction. Returns rows stored."""
    from . import nfl, backtest_nfl, data_soccer as ds, backtest_soccer, odds as O
    from .soccer import SoccerParams
    t = _tuned()
    rows = []

    progress(0.02, "History: loading NFL data...")
    g = nfl.load_games()
    seasons = sorted(s for s in g["season"].unique() if s >= nfl_from)
    params = nfl.NFLParams(**t.get("nfl", {}).get("params", {}))
    for i, s in enumerate(seasons):
        progress(0.05 + 0.35 * i / len(seasons), f"History: replaying NFL {s} week by week...")
        bt = backtest_nfl.walk_forward(g, [int(s)], params, verbose=False)
        for r in bt.itertuples():
            mh = np.nan
            if pd.notna(r.home_ml) and pd.notna(r.away_ml):
                mh = O.devig([O.parse_odds(r.home_ml), O.parse_odds(r.away_ml)], "shin")[0]
            rows.append({"sport": "nfl", "comp": "NFL", "season": str(r.season),
                         "game_date": pd.Timestamp(r.date).strftime("%Y-%m-%d"), "home": r.home, "away": r.away,
                         "home_score": r.home_score, "away_score": r.away_score,
                         "model_margin": r.model_margin, "market_margin": r.spread_line,
                         "model_total": r.model_total, "market_total": r.total_line,
                         "p_home": r.p_home_ml, "m_home": mh, "source": "backtest"})

    progress(0.42, "History: loading soccer data...")
    df = ds.load_all(verbose=False)
    sp = SoccerParams(**t.get("soccer", {}).get("params", {}))
    end = df["date"].max() + pd.Timedelta(days=1)
    start = pd.Timestamp(soccer_from)
    chunks = pd.date_range(start, end + pd.Timedelta(days=60), freq="60D")
    parts = []
    for i in range(len(chunks) - 1):
        progress(0.45 + 0.35 * i / (len(chunks) - 1),
                 f"History: replaying soccer {chunks[i]:%b %Y} week by week...")
        bt = backtest_soccer.walk_forward(df, str(chunks[i].date()), str(chunks[i + 1].date()), sp, verbose=False)
        if not bt.empty:
            parts.append(bt[bt["comp"].isin(SOCCER)])
    bt = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    eff = None
    try:
        from . import lineup as LU
        eff = LU.load_effects()
    except Exception:
        pass
    if eff and len(bt):  # lineup model: same walk-forward discipline (coefficients from earlier months only)
        try:
            from .soccer import Markets
            F = LU.match_features(progress=lambda f, m: progress(0.8 + 0.15 * f, "History: " + m))
            W = LU.walk_forward_rates(LU.join_model(F, bt), eff["features"])
            adj = {(r.day, r.home, r.away): (r.lh_adj, r.la_adj) for r in W.itertuples()}
            bt["day"] = pd.to_datetime(bt["date"]).dt.normalize()
            for i, r in enumerate(bt.itertuples()):
                k = adj.get((r.day, r.home, r.away))
                if k is None:
                    continue
                g_ = np.arange(11)
                M = np.outer(stats_poisson(g_, k[0]), stats_poisson(g_, k[1]))
                rho = r.rho
                M[0, 0] *= 1 - k[0] * k[1] * rho; M[0, 1] *= 1 + k[0] * rho; M[1, 0] *= 1 + k[1] * rho; M[1, 1] *= 1 - rho
                M = np.clip(M, 0, None); M /= M.sum()
                p = Markets(M, k[0], k[1]).x12()
                bt.loc[bt.index[i], ["lh", "la", "pH", "pD", "pA"]] = [k[0], k[1], p[0], p[1], p[2]]
        except Exception as e:
            print(f"  ! lineup history skipped: {e}")
    if len(bt):
        for r in bt.itertuples():
            mk = [np.nan] * 3
            if all(pd.notna(x) for x in (r.cH, r.cD, r.cA)):
                mk = list(O.devig([r.cH, r.cD, r.cA], "shin"))
            rows.append({"sport": "soccer", "comp": r.comp, "season": soccer_season(r.date),
                         "game_date": pd.Timestamp(r.date).strftime("%Y-%m-%d"), "home": r.home, "away": r.away,
                         "home_score": r.hg, "away_score": r.ag, "model_margin": r.lh - r.la,
                         "model_total": r.lh + r.la, "p_home": r.pH, "p_draw": r.pD, "p_away": r.pA,
                         "m_home": mk[0], "m_draw": mk[1], "m_away": mk[2],
                         "o_home": r.cH, "o_draw": r.cD, "o_away": r.cA, "source": "backtest"})

    out = pd.DataFrame(rows, columns=COLS).drop_duplicates(["sport", "home", "away", "game_date"], keep="last")
    with db.connect() as con:
        out.to_sql("history", con, if_exists="replace", index=False)
        con.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
        con.execute("INSERT OR REPLACE INTO meta VALUES ('history_built', ?)", (db.now(),))
    progress(1.0, f"History built: {len(out)} games")
    return len(out)


def built_at() -> str | None:
    try:
        with db.connect() as con:
            r = con.execute("SELECT v FROM meta WHERE k='history_built'").fetchone()
        return r[0] if r else None
    except Exception:
        return None


def frame() -> pd.DataFrame:
    """Backtest rows + finished live predictions (live wins where both exist)."""
    try:
        with db.connect() as con:
            h = pd.read_sql_query("SELECT * FROM history", con)
    except Exception:
        h = pd.DataFrame(columns=COLS)
    p = db.predictions()
    p = p[(p["state"] == "post") & p["home_score"].notna() & p["comp"].isin(list(COMPS))].copy()
    if len(p):
        nfl_season = lambda t: str(t.year if t.month >= 3 else t.year - 1)  # Jan/Feb games belong to the prior season
        p["season"] = [nfl_season(pd.Timestamp(x)) if s == "nfl" else soccer_season(x)
                       for s, x in zip(p["sport"], p["game_date"])]
        p.loc[p["sport"] == "soccer", "model_margin"] = p["xg_home"] - p["xg_away"]
        p["source"] = "live"
        for c in COLS:
            if c not in p:
                p[c] = np.nan
        h = pd.concat([h, p[COLS]], ignore_index=True)
    if h.empty:
        return h
    h = h.drop_duplicates(["sport", "home", "away", "game_date"], keep="last")
    for c in COLS[6:-1]:
        h[c] = pd.to_numeric(h[c], errors="coerce")
    h = h.dropna(subset=["home_score", "away_score"])
    return h.sort_values("game_date").reset_index(drop=True)


# ------------------------------------------------------------------------------------------ stats
def _calib(p, y, bins=10, min_n=15):
    p, y = np.asarray(p, float), np.asarray(y, float)
    ok = np.isfinite(p) & np.isfinite(y)
    p, y = p[ok], y[ok]
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        s = (p >= lo) & (p < hi if hi < 1 else p <= hi)
        if s.sum() >= min_n:
            out.append((float(p[s].mean()), float(y[s].mean()), int(s.sum())))
    return out


def nfl_stats(d: pd.DataFrame) -> dict:
    d = d.dropna(subset=["model_margin"]).copy()
    act = d["home_score"] - d["away_score"]
    tot = d["home_score"] + d["away_score"]
    km = d["market_margin"].notna()
    dec = act != 0
    s = {"n": len(d)}
    mw = dec & (d["model_margin"] != 0)
    s["model_winner"] = float((np.sign(act[mw]) == np.sign(d.loc[mw, "model_margin"])).mean()) if mw.any() else np.nan
    bw = dec & km & (d["market_margin"] != 0)
    s["books_winner"] = float((np.sign(act[bw]) == np.sign(d.loc[bw, "market_margin"])).mean()) if bw.any() else np.nan
    s["model_mae"] = float((act[km] - d.loc[km, "model_margin"]).abs().mean()) if km.any() else np.nan
    s["books_mae"] = float((act[km] - d.loc[km, "market_margin"]).abs().mean()) if km.any() else np.nan
    kt = d["market_total"].notna() & d["model_total"].notna()
    s["model_tmae"] = float((tot[kt] - d.loc[kt, "model_total"]).abs().mean()) if kt.any() else np.nan
    s["books_tmae"] = float((tot[kt] - d.loc[kt, "market_total"]).abs().mean()) if kt.any() else np.nan
    # leans: bet the side the model prefers when it is 2+ points off the closing line, at -110
    diff = d["model_margin"] - d["market_margin"]
    sel = km & (diff.abs() >= SPREAD_LEAN)
    cov = act - d["market_margin"]
    won = sel & (cov != 0) & (np.sign(cov) == np.sign(diff))
    lost = sel & (cov != 0) & (np.sign(cov) != np.sign(diff))
    s["lean_w"], s["lean_l"], s["lean_p"] = int(won.sum()), int(lost.sum()), int((sel & (cov == 0)).sum())
    u = np.where(won, 100 / 110, np.where(lost, -1.0, 0.0))
    s["lean_units"] = float(u.sum())
    s["lean_curve"] = list(zip(d.loc[sel, "game_date"], np.cumsum(u[sel.to_numpy()])))
    tdiff = d["model_total"] - d["market_total"]
    tsel = kt & (tdiff.abs() >= TOTAL_LEAN)
    tc = tot - d["market_total"]
    tw = tsel & (tc != 0) & (np.sign(tc) == np.sign(tdiff))
    tl = tsel & (tc != 0) & (np.sign(tc) != np.sign(tdiff))
    s["tot_w"], s["tot_l"] = int(tw.sum()), int(tl.sum())
    s["tot_units"] = float((100 / 110) * tw.sum() - tl.sum())
    y = (act > 0).astype(float).where(dec)
    s["cal_model"] = _calib(d["p_home"], y)
    s["cal_books"] = _calib(d["m_home"], y)
    return s


def soccer_stats(d: pd.DataFrame) -> dict:
    d = d.dropna(subset=["p_home", "p_draw", "p_away"]).copy()
    out = np.where(d["home_score"] > d["away_score"], 0, np.where(d["home_score"] == d["away_score"], 1, 2))
    P = d[["p_home", "p_draw", "p_away"]].to_numpy(float)
    K = d[["m_home", "m_draw", "m_away"]].to_numpy(float)
    hk = np.all(np.isfinite(K), 1)
    Y = np.eye(3)[out]
    idx = np.arange(len(d))
    s = {"n": len(d), "n_books": int(hk.sum())}
    s["model_fav"] = float((P.argmax(1) == out).mean()) if len(d) else np.nan
    s["books_fav"] = float((K[hk].argmax(1) == out[hk]).mean()) if hk.any() else np.nan
    ll = lambda M, m: float(-np.mean(np.log(np.clip(M[idx[m], out[m]], 1e-12, 1))))
    s["model_ll"] = ll(P, hk) if hk.any() else np.nan
    s["books_ll"] = ll(K, hk) if hk.any() else np.nan
    s["model_ll_all"] = ll(P, np.ones(len(d), bool)) if len(d) else np.nan
    s["model_brier"] = float(((P[hk] - Y[hk]) ** 2).sum(1).mean()) if hk.any() else np.nan
    s["books_brier"] = float(((K[hk] - Y[hk]) ** 2).sum(1).mean()) if hk.any() else np.nan
    s["draws"] = float((out == 1).mean()) if len(d) else np.nan
    # leans: the outcome the model rates 5+ points above the books, bet at the closing price
    Odds = d[["o_home", "o_draw", "o_away"]].to_numpy(float)
    gap = np.where(np.isfinite(K), P - K, -1)
    pick = gap.argmax(1)
    sel = hk & (gap.max(1) >= SOCCER_LEAN) & np.all(np.isfinite(Odds), 1)
    win = pick == out
    u = np.where(win, Odds[idx, pick] - 1, -1.0)
    u = np.where(sel, u, 0.0)
    s["lean_n"], s["lean_w"] = int(sel.sum()), int((sel & win).sum())
    s["lean_units"] = float(u.sum())
    s["lean_curve"] = list(zip(d["game_date"].to_numpy()[sel], np.cumsum(u[sel])))
    s["cal_model"] = _calib(P.ravel(), Y.ravel())
    s["cal_books"] = _calib(K[hk].ravel(), Y[hk].ravel())
    return s


# ------------------------------------------------------------------------------------------ render
def _f(x, fmt):
    return "-" if x is None or (isinstance(x, float) and not np.isfinite(x)) else format(x, fmt)


def _kpi(k, v, sub=None):
    return tags.div(tags.div(k, class_="k"), tags.div(v, class_="v"), tags.div(sub, class_="s") if sub else None,
                    class_="kpi")


def calib_svg(model, books, title):
    W, H, L, B, T, R = 520, 300, 46, 36, 12, 14
    x = lambda p: L + p * (W - L - R)
    y = lambda p: H - B - p * (H - B - T)
    g = []
    # colours come from the theme classes (gl/ax/axl/ref, s-/f-model, s-/f-books, halo) so dark mode works;
    # the attributes are only the fallback
    for t in (0, .25, .5, .75, 1):
        g.append(f'<line class="gl" x1="{x(0)}" x2="{x(1)}" y1="{y(t)}" y2="{y(t)}" stroke="#e2e8f0"/>'
                 f'<text class="ax" x="{L - 6}" y="{y(t) + 4}" text-anchor="end" font-size="11" fill="#64748b">{t:.0%}</text>'
                 f'<text class="ax" x="{x(t)}" y="{H - B + 16}" text-anchor="middle" font-size="11" fill="#64748b">{t:.0%}</text>')
    g.append(f'<line class="ref" x1="{x(0)}" y1="{y(0)}" x2="{x(1)}" y2="{y(1)}" stroke="#94a3b8" stroke-dasharray="4 4"/>')
    g.append(f'<text class="ax" x="{x(.98)}" y="{y(.98) + 16}" text-anchor="end" font-size="11" fill="#64748b">perfect</text>')
    for pts, c, nm, k in ((books, BOOKS_C, "Books", "books"), (model, MODEL_C, "Model", "model")):
        if not pts:
            continue
        g.append(f'<polyline class="s-{k}" fill="none" stroke="{c}" stroke-width="2" points="'
                 + " ".join(f"{x(a):.1f},{y(b):.1f}" for a, b, _ in pts) + '"/>')
        for a, b, n in pts:
            g.append(f'<circle class="f-{k} halo" cx="{x(a):.1f}" cy="{y(b):.1f}" r="5" fill="{c}" stroke="#fff" stroke-width="2">'
                     f'<title>{nm}: said {a:.0%}, happened {b:.0%} ({n} outcomes)</title></circle>')
    g.append(f'<text class="axl" x="{(L + W - R) / 2}" y="{H - 4}" text-anchor="middle" font-size="11" fill="#334155">'
             f'Predicted chance</text>')
    g.append(f'<text class="axl" x="12" y="{(T + H - B) / 2}" text-anchor="middle" font-size="11" fill="#334155" '
             f'transform="rotate(-90 12 {(T + H - B) / 2})">How often it happened</text>')
    legend = tags.div(tags.span(tags.i(class_="m"), "Model"),
                      tags.span(tags.i(class_="b"), "Books (closing line)"), class_="lgd")
    return tags.div(tags.h6(title), legend,
                    HTML(f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="{title}">{"".join(g)}</svg>'),
                    tags.div("Dots on the dashed line mean the percentages were honest: things given 30% happened "
                             "about 30% of the time. Hover a dot for the numbers.", class_="note"), class_="hcard")


def curve_svg(curve, title, unit_word="units"):
    if len(curve) < 2:
        return tags.div(tags.h6(title), tags.div("Not enough leans yet.", class_="note"), class_="hcard")
    W, H, L, B, T, R = 520, 240, 46, 30, 14, 14
    v = np.array([c[1] for c in curve], float)
    lo, hi = min(0.0, v.min()), max(0.0, v.max())
    pad = max((hi - lo) * 0.08, 1)
    lo, hi = lo - pad, hi + pad
    n = len(v)
    x = lambda i: L + i / (n - 1) * (W - L - R)
    y = lambda u: T + (hi - u) / (hi - lo) * (H - B - T)
    g = []
    step = next(s for s in (1, 2, 5, 10, 20, 25, 50, 100, 200, 500, 1000) if (hi - lo) / s <= 6)
    for t in np.arange(np.ceil(lo / step) * step, hi + 1e-9, step):
        g.append(f'<line class="gl" x1="{L}" x2="{W - R}" y1="{y(t):.1f}" y2="{y(t):.1f}" stroke="#f1f5f9"/>'
                 f'<text class="ax" x="{L - 6}" y="{y(t) + 4:.1f}" text-anchor="end" font-size="11" fill="#64748b">{t:+.0f}</text>')
    g.append(f'<line class="zero" x1="{L}" x2="{W - R}" y1="{y(0):.1f}" y2="{y(0):.1f}" stroke="#94a3b8"/>')
    g.append(f'<polyline class="s-model" fill="none" stroke="{MODEL_C}" stroke-width="2" points="'
             + " ".join(f"{x(i):.1f},{y(u):.1f}" for i, u in enumerate(v)) + '"/>')
    step = max(1, n // 60)
    for i in list(range(0, n, step)) + [n - 1]:
        g.append(f'<circle cx="{x(i):.1f}" cy="{y(v[i]):.1f}" r="9" fill="transparent">'
                 f'<title>{pd.Timestamp(curve[i][0]):%d %b %Y}: {v[i]:+.1f} {unit_word} after {i + 1} bets</title></circle>')
    g.append(f'<circle class="f-model halo" cx="{x(n - 1):.1f}" cy="{y(v[-1]):.1f}" r="4" fill="{MODEL_C}" stroke="#fff" stroke-width="2"/>')
    first, last = pd.Timestamp(curve[0][0]), pd.Timestamp(curve[-1][0])
    g.append(f'<text class="ax" x="{L}" y="{H - 8}" font-size="11" fill="#64748b">{first:%b %Y}</text>'
             f'<text class="ax" x="{W - R}" y="{H - 8}" text-anchor="end" font-size="11" fill="#64748b">{last:%b %Y}</text>')
    return tags.div(tags.h6(title), HTML(f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="{title}">'
                                         f'{"".join(g)}</svg>'), class_="hcard")


def _season_table(d, sport):
    rows = []
    for (season, comp), g in d.groupby(["season", "comp"] if sport == "soccer" else ["season", "sport"]):
        if sport == "nfl":
            s = nfl_stats(g)
            rows.append(tags.tr(tags.td(season), tags.td(s["n"]),
                                tags.td(_f(s["model_winner"], ".1%")), tags.td(_f(s["books_winner"], ".1%")),
                                tags.td(_f(s["model_mae"], ".2f")), tags.td(_f(s["books_mae"], ".2f")),
                                tags.td(_f(s["model_tmae"], ".2f")), tags.td(_f(s["books_tmae"], ".2f")),
                                tags.td(f"{s['lean_w']}-{s['lean_l']}-{s['lean_p']}"),
                                tags.td(f"{s['lean_units']:+.1f}u")))
        else:
            s = soccer_stats(g)
            rows.append(tags.tr(tags.td(season), tags.td(COMPS.get(comp, comp)), tags.td(s["n"]),
                                tags.td(_f(s["model_fav"], ".1%")), tags.td(_f(s["books_fav"], ".1%")),
                                tags.td(_f(s["model_ll"], ".4f")), tags.td(_f(s["books_ll"], ".4f")),
                                tags.td(f"{s['lean_w']}/{s['lean_n']}" if s["lean_n"] else "-"),
                                tags.td(f"{s['lean_units']:+.1f}u" if s["lean_n"] else "-")))
    if sport == "nfl":
        head = ["Season", "Games", "Model picked winner", "Books' favourite won", "Model miss (pts)",
                "Books miss (pts)", "Model total miss", "Books total miss", "Leans W-L-P", "Leans P/L"]
    else:
        head = ["Season", "League", "Games", "Model favourite won", "Books' favourite won", "Model log-loss",
                "Books log-loss", "Leans won", "Leans P/L"]
    return tags.div(tags.h6("By season"), tags.div(tags.table(tags.thead(tags.tr(*[tags.th(h) for h in head])),
                                                              tags.tbody(*rows), class_="bk"), class_="sect"),
                    class_="hcard")


def _game_log(d, sport, limit):
    rows = []
    for r in d.iloc[::-1].head(limit).itertuples():
        hs, as_ = int(r.home_score), int(r.away_score)
        date = pd.Timestamp(r.game_date).strftime("%d %b %Y")
        src = "live" if r.source == "live" else ""
        if sport == "nfl":
            act = hs - as_
            side = lambda m: "-" if pd.isna(m) else ("Pick'em" if abs(m) < .05 else f"{r.home if m > 0 else r.away} by {abs(m):.1f}")
            res = "Tie" if act == 0 else f"{r.home if act > 0 else r.away} by {abs(act)}"
            lean = ""
            if pd.notna(r.market_margin) and abs(r.model_margin - r.market_margin) >= SPREAD_LEAN:
                hl = r.model_margin > r.market_margin
                cv = act - r.market_margin
                lean = f"{r.home if hl else r.away}: " + ("push" if cv == 0 else "won" if (cv > 0) == hl else "lost")
            rows.append(tags.tr(tags.td(date), tags.td(f"{r.away} @ {r.home}", tags.small(src)),
                                tags.td(f"{as_}-{hs}"), tags.td(res), tags.td(side(r.model_margin)),
                                tags.td(side(r.market_margin)), tags.td(lean or "-")))
        else:
            out = 0 if hs > as_ else 1 if hs == as_ else 2
            P = [r.p_home, r.p_draw, r.p_away]
            K = [r.m_home, r.m_draw, r.m_away]
            hk = all(pd.notna(k) for k in K)
            rows.append(tags.tr(tags.td(date), tags.td(f"{r.home} v {r.away}", tags.small(COMPS.get(r.comp, r.comp) + (" · live" if src else ""))),
                                tags.td(f"{hs}-{as_}"),
                                tags.td(f"{P[0]:.0%} / {P[1]:.0%} / {P[2]:.0%}"),
                                tags.td(f"{K[0]:.0%} / {K[1]:.0%} / {K[2]:.0%}" if hk else "-"),
                                tags.td(f"{P[out]:.0%}" + (f" vs {K[out]:.0%}" if hk else ""))))
    head = (["Date", "Game", "Final (away-home)", "Result", "Model said", "Books said", "Model lean ATS"] if sport == "nfl"
            else ["Date", "Match", "Final", "Model H/D/A", "Books H/D/A", "Chance given to the result (model vs books)"])
    return tags.div(tags.h6(f"Game by game (latest {min(limit, len(d))} of {len(d)})"),
                    tags.div(tags.table(tags.thead(tags.tr(*[tags.th(h) for h in head])), tags.tbody(*rows), class_="bk"),
                             class_="sect"), class_="hcard")


def view(d: pd.DataFrame, sport: str, label: str = "", log_limit: int = 150):
    """The whole history panel for one sport (already filtered by league/season)."""
    if d.empty:
        return tags.div("No finished games in this selection yet. Build the history first.", class_="empty")
    if sport == "nfl":
        s = nfl_stats(d)
        kp = [_kpi("Games", f"{s['n']:,}", label),
              _kpi("Picked the winner", f"Model {_f(s['model_winner'], '.1%')}", f"Books' favourite {_f(s['books_winner'], '.1%')}"),
              _kpi("Avg miss on the margin", f"Model {_f(s['model_mae'], '.2f')} pts", f"Books {_f(s['books_mae'], '.2f')} pts"),
              _kpi("Avg miss on the total", f"Model {_f(s['model_tmae'], '.2f')} pts", f"Books {_f(s['books_tmae'], '.2f')} pts"),
              _kpi(f"Model leans vs spread ({SPREAD_LEAN:g}+ pts off)",
                   f"{s['lean_w']}-{s['lean_l']}-{s['lean_p']}",
                   f"{s['lean_w'] / max(s['lean_w'] + s['lean_l'], 1):.1%} · {s['lean_units']:+.1f}u at -110 (52.4% breaks even)"),
              _kpi(f"Model leans vs total ({TOTAL_LEAN:g}+ pts off)", f"{s['tot_w']}-{s['tot_l']}",
                   f"{s['tot_w'] / max(s['tot_w'] + s['tot_l'], 1):.1%} · {s['tot_units']:+.1f}u at -110")]
        charts = [calib_svg(s["cal_model"], s["cal_books"], "Win probability: predicted vs actual (home team)"),
                  curve_svg(s["lean_curve"], "Running P/L: betting every model spread lean, 1u at -110")]
    else:
        s = soccer_stats(d)
        kp = [_kpi("Matches", f"{s['n']:,}", label + (f" · {s['n_books']:,} with closing odds" if s["n_books"] < s["n"] else "")),
              _kpi("Favourite won", f"Model {_f(s['model_fav'], '.1%')}", f"Books {_f(s['books_fav'], '.1%')} · draws {_f(s['draws'], '.0%')}"),
              _kpi("Log-loss (lower is better)", f"Model {_f(s['model_ll'], '.4f')}", f"Books {_f(s['books_ll'], '.4f')}"),
              _kpi("Brier score (lower is better)", f"Model {_f(s['model_brier'], '.4f')}", f"Books {_f(s['books_brier'], '.4f')}"),
              _kpi(f"Model leans ({SOCCER_LEAN:.0%}+ above books)", f"{s['lean_w']} won of {s['lean_n']}",
                   f"{s['lean_units']:+.1f}u at Pinnacle's closing price"),
              ]
        charts = [calib_svg(s["cal_model"], s["cal_books"], "Home / draw / away chances: predicted vs actual"),
                  curve_svg(s["lean_curve"], "Running P/L: betting every model lean, 1u at the closing price")]
    return tags.div(tags.div(*kp, class_="hkpis"), tags.div(*charts, class_="hcharts"),
                    _season_table(d, sport), _game_log(d, sport, log_limit))


# Styles for this page live in theme.py (shared by the app and the phone page).

INTRO = ("Every game here was predicted before it was played: past seasons are replayed week by week with the model "
         "refit only on earlier games, and games since you started running it use the predictions saved before "
         "kickoff (marked live). Books = the closing line, the toughest benchmark there is.")
