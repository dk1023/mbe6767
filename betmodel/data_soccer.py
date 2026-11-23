"""Soccer data: domestic leagues (football-data.co.uk) + UEFA competitions (fixturedownload.com).

Domestic leagues give results, shots, xG (2026-27+) and sharp closing odds (Pinnacle up to
2025-26, Betfair Exchange after). UEFA matches are what link the leagues together so a single
model can rate Arsenal against Inter against Benfica on one scale.
"""
from __future__ import annotations

import io
import json
import time
import unicodedata
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "soccer"

# football-data.co.uk "main" leagues (rich columns incl. odds, shots)
MAIN_LEAGUES = {
    "EPL": "E0", "CHAMP": "E1", "LALIGA": "SP1", "LALIGA2": "SP2", "SERIEA": "I1", "SERIEB": "I2",
    "BUNDES": "D1", "BUNDES2": "D2", "LIGUE1": "F1", "LIGUE2": "F2", "ERED": "N1", "BELG": "B1",
    "PORT": "P1", "TURK": "T1", "GREECE": "G1", "SCOT": "SC0",
}
# "extra" leagues (results + closing odds only) - mainly to anchor teams that play in Europe
EXTRA_LEAGUES = {"AUSTRIA": "AUT", "SWISS": "SWZ", "DENMARK": "DNK", "NORWAY": "NOR",
                 "SWEDEN": "SWE", "POLAND": "POL", "ROMANIA": "ROU"}
UEFA_COMPS = {"UCL": "champions-league", "UEL": "europa-league", "UECL": "conference-league"}

# user-facing aliases for --comp
COMP_ALIASES = {"PL": "EPL", "PREMIER": "EPL", "PREMIERLEAGUE": "EPL", "SERIA": "SERIEA", "SERI A": "SERIEA",
                "SERIE A": "SERIEA", "LA LIGA": "LALIGA", "CL": "UCL", "CHAMPIONS": "UCL", "EL": "UEL",
                "EUROPA": "UEL", "CONFERENCE": "UECL"}


def season_codes(n_back: int, today: pd.Timestamp | None = None) -> list[str]:
    """football-data season codes, e.g. ['2324','2425','2526','2627']."""
    today = today or pd.Timestamp.today()
    start = today.year if today.month >= 7 else today.year - 1
    years = range(start - n_back, start + 1)
    return [f"{y % 100:02d}{(y + 1) % 100:02d}" for y in years]


def _get(url: str, dest: Path, max_age_h: float) -> bytes:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and (time.time() - dest.stat().st_mtime) < max_age_h * 3600:
        return dest.read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (betmodel)"})
    try:
        raw = urllib.request.urlopen(req, timeout=60).read()
    except Exception as e:  # network trouble: fall back to cache if we have it
        if dest.exists():
            print(f"  ! {url} failed ({e}); using cached copy")
            return dest.read_bytes()
        raise
    dest.write_bytes(raw)
    return raw


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return " ".join(s.replace(".", " ").replace("-", " ").split()).lower()


# fixturedownload name (normalised) -> football-data name. Fuzzy matching handles the rest.
MANUAL_ALIASES = {
    "manchester city": "Man City", "man city": "Man City", "manchester united": "Man United",
    "man utd": "Man United", "man united": "Man United", "newcastle": "Newcastle",
    "tottenham": "Tottenham", "spurs": "Tottenham", "nottingham forest": "Nott'm Forest",
    "nottm forest": "Nott'm Forest", "wolverhampton": "Wolves", "west ham united": "West Ham",
    "brighton & hove albion": "Brighton", "aston villa": "Aston Villa",
    "inter": "Inter", "internazionale": "Inter", "inter milan": "Inter", "ac milan": "Milan", "milan": "Milan",
    "juventus": "Juventus", "atalanta": "Atalanta", "roma": "Roma", "as roma": "Roma", "lazio": "Lazio",
    "napoli": "Napoli", "fiorentina": "Fiorentina", "bologna": "Bologna",
    "real madrid": "Real Madrid", "barcelona": "Barcelona", "atletico": "Ath Madrid",
    "atletico madrid": "Ath Madrid", "atl madrid": "Ath Madrid", "athletic club": "Ath Bilbao",
    "athletic bilbao": "Ath Bilbao", "real sociedad": "Sociedad", "sociedad": "Sociedad",
    "real betis": "Betis", "betis": "Betis", "villarreal": "Villarreal", "sevilla": "Sevilla",
    "celta": "Celta", "celta vigo": "Celta", "rayo vallecano": "Vallecano", "girona": "Girona",
    "bayern": "Bayern Munich", "bayern munchen": "Bayern Munich", "bayern munich": "Bayern Munich",
    "dortmund": "Dortmund", "borussia dortmund": "Dortmund", "leverkusen": "Leverkusen",
    "bayer leverkusen": "Leverkusen", "rb leipzig": "RB Leipzig", "leipzig": "RB Leipzig",
    "frankfurt": "Ein Frankfurt", "eintracht frankfurt": "Ein Frankfurt", "stuttgart": "Stuttgart",
    "vfb stuttgart": "Stuttgart", "hoffenheim": "Hoffenheim", "freiburg": "Freiburg",
    "sc freiburg": "Freiburg", "union berlin": "Union Berlin", "wolfsburg": "Wolfsburg",
    "m gladbach": "M'gladbach", "monchengladbach": "M'gladbach", "mainz": "Mainz",
    "psg": "Paris SG", "paris": "Paris SG", "paris saint germain": "Paris SG", "marseille": "Marseille",
    "monaco": "Monaco", "lille": "Lille", "lyon": "Lyon", "nice": "Nice", "lens": "Lens", "rennes": "Rennes",
    "brest": "Brest", "strasbourg": "Strasbourg",
    "psv": "PSV Eindhoven", "psv eindhoven": "PSV Eindhoven", "feyenoord": "Feyenoord", "ajax": "Ajax",
    "az alkmaar": "AZ Alkmaar", "az": "AZ Alkmaar", "twente": "Twente", "utrecht": "Utrecht",
    "go ahead eagles": "Go Ahead Eagles",
    "benfica": "Benfica", "sporting cp": "Sp Lisbon", "sporting": "Sp Lisbon", "porto": "Porto",
    "fc porto": "Porto", "braga": "Sp Braga", "sc braga": "Sp Braga", "vitoria sc": "Guimaraes",
    "club brugge": "Club Brugge", "union sg": "St. Gilloise", "union saint gilloise": "St. Gilloise",
    "anderlecht": "Anderlecht", "genk": "Genk", "gent": "Gent", "antwerp": "Antwerp",
    "celtic": "Celtic", "rangers": "Rangers", "hearts": "Hearts", "aberdeen": "Aberdeen",
    "galatasaray": "Galatasaray", "fenerbahce": "Fenerbahce", "besiktas": "Besiktas",
    "trabzonspor": "Trabzonspor", "basaksehir": "Buyuksehyr",
    "olympiacos": "Olympiakos", "olympiakos": "Olympiakos", "paok": "PAOK", "aek athens": "AEK",
    "panathinaikos": "Panathinaikos",
    "salzburg": "Salzburg", "rb salzburg": "Salzburg", "sturm graz": "Sturm Graz", "rapid wien": "Rapid Vienna",
    "lask": "LASK", "austria wien": "Austria Vienna",
    "young boys": "Young Boys", "basel": "Basel", "servette": "Servette", "lugano": "Lugano",
    "copenhagen": "FC Copenhagen", "fc copenhagen": "FC Copenhagen", "midtjylland": "Midtjylland",
    "fc midtjylland": "Midtjylland", "brondby": "Brondby", "aarhus": "Aarhus", "agf": "Aarhus",
    "bodo/glimt": "Bodo/Glimt", "bodo glimt": "Bodo/Glimt", "molde": "Molde", "brann": "Brann",
    "malmo": "Malmo FF", "malmo ff": "Malmo FF", "djurgarden": "Djurgarden", "hacken": "Hacken",
    "legia warsaw": "Legia", "legia": "Legia", "lech poznan": "Lech", "jagiellonia": "Jagiellonia",
    "rakow": "Rakow", "fcsb": "FCSB", "cfr cluj": "CFR Cluj",
    "atleti": "Ath Madrid", "atletico de madrid": "Ath Madrid", "legia warszawa": "Legia",
    "n e c": "Nijmegen", "nec": "Nijmegen", "lausanne sport": "Lausanne", "sint truidense": "St Truiden",
    "u craiova": "Univ. Craiova", "universitatea craiova": "Univ. Craiova",
    # The Odds API / FPL spellings
    "tottenham hotspur": "Tottenham", "brighton and hove albion": "Brighton", "wolverhampton wanderers": "Wolves",
    "newcastle united": "Newcastle", "leeds united": "Leeds", "leicester city": "Leicester", "ipswich town": "Ipswich",
    "afc bournemouth": "Bournemouth", "nott'm forest": "Nott'm Forest", "west ham": "West Ham",
    "paris saint germain": "Paris SG", "atletico madrid": "Ath Madrid", "atletico de madrid": "Ath Madrid",
    "athletic bilbao": "Ath Bilbao", "deportivo alaves": "Alaves", "alaves": "Alaves", "rcd espanyol": "Espanol",
    "espanyol": "Espanol", "deportivo la coruna": "La Coruna", "racing santander": "Santander",
    "real valladolid": "Valladolid", "rcd mallorca": "Mallorca", "ca osasuna": "Osasuna",
    "hellas verona": "Verona", "as roma": "Roma", "ss lazio": "Lazio", "ssc napoli": "Napoli",
    "inter milan": "Inter", "ac milan": "Milan", "bayer leverkusen": "Leverkusen", "sporting lisbon": "Sp Lisbon",
    "sporting cp": "Sp Lisbon", "fc porto": "Porto", "sl benfica": "Benfica", "psv eindhoven": "PSV Eindhoven",
}
# UEFA-only teams that appear under two spellings
UEFA_DEDUPE = {"omonoia": "Omonia", "shakhtar donetsk": "Shakhtar", "crvena zvezda": "Crvena Zvezda",
               "red star belgrade": "Crvena Zvezda", "gnk dinamo": "Dinamo Zagreb", "dinamo zagreb": "Dinamo Zagreb",
               "ferencvaros": "Ferencvaros", "slavia prague": "Slavia Praha", "sparta prague": "Sparta Praha"}


def _fd_season_label(code: str) -> str:
    return f"20{code[:2]}-{code[2:]}"


def load_domestic(n_back: int = 3, max_age_h: float = 6) -> pd.DataFrame:
    seasons = season_codes(n_back)
    frames = []
    for comp, code in MAIN_LEAGUES.items():
        for s in seasons:
            url = f"https://www.football-data.co.uk/mmz4281/{s}/{code}.csv"
            try:
                raw = _get(url, DATA_DIR / "fd" / f"{s}_{code}.csv", max_age_h if s == seasons[-1] else 24 * 365)
            except Exception as e:
                print(f"  ! skip {comp} {s}: {e}")
                continue
            d = pd.read_csv(io.BytesIO(raw), encoding="latin1", on_bad_lines="skip")
            d = d.dropna(subset=["HomeTeam", "AwayTeam"])
            if d.empty:
                continue
            out = pd.DataFrame({
                "date": pd.to_datetime(d["Date"], dayfirst=True, errors="coerce"),
                "comp": comp, "season": _fd_season_label(s),
                "home": d["HomeTeam"].str.strip(), "away": d["AwayTeam"].str.strip(),
                "hg": pd.to_numeric(d.get("FTHG"), errors="coerce"),
                "ag": pd.to_numeric(d.get("FTAG"), errors="coerce"),
            })
            for src, dst in [("HxG", "hxg"), ("AxG", "axg"), ("HST", "hst"), ("AST", "ast"),
                             ("HS", "hs"), ("AS", "as_")]:
                out[dst] = pd.to_numeric(d[src], errors="coerce") if src in d else np.nan
            # sharpest available closing odds: Pinnacle, else Betfair Exchange, else market average
            for k, cols in {"cH": ["PSCH", "BFECH", "AvgCH"], "cD": ["PSCD", "BFECD", "AvgCD"],
                            "cA": ["PSCA", "BFECA", "AvgCA"], "cO25": ["PC>2.5", "BFEC>2.5", "AvgC>2.5"],
                            "cU25": ["PC<2.5", "BFEC<2.5", "AvgC<2.5"],
                            "oH": ["PSH", "BFEH", "AvgH"], "oD": ["PSD", "BFED", "AvgD"], "oA": ["PSA", "BFEA", "AvgA"]}.items():
                v = pd.Series(np.nan, index=d.index)
                for c in cols:
                    if c in d:
                        v = v.fillna(pd.to_numeric(d[c], errors="coerce"))
                out[k] = v
            frames.append(out)
    for comp, code in EXTRA_LEAGUES.items():
        try:
            raw = _get(f"https://www.football-data.co.uk/new/{code}.csv", DATA_DIR / "fd" / f"new_{code}.csv", max_age_h)
        except Exception as e:
            print(f"  ! skip {comp}: {e}")
            continue
        d = pd.read_csv(io.BytesIO(raw), encoding="latin1", on_bad_lines="skip")
        d["date"] = pd.to_datetime(d["Date"], dayfirst=True, errors="coerce")
        d = d[d["date"] >= pd.Timestamp.today() - pd.Timedelta(days=365 * (n_back + 1))]
        out = pd.DataFrame({"date": d["date"], "comp": comp, "season": d["Season"].astype(str),
                            "home": d["Home"].str.strip(), "away": d["Away"].str.strip(),
                            "hg": pd.to_numeric(d["HG"], errors="coerce"),
                            "ag": pd.to_numeric(d["AG"], errors="coerce")})
        for c in ["hxg", "axg", "hst", "ast", "hs", "as_", "cO25", "cU25", "oH", "oD", "oA"]:
            out[c] = np.nan
        for k, src in {"cH": "PSCH", "cD": "PSCD", "cA": "PSCA"}.items():
            out[k] = pd.to_numeric(d.get(src), errors="coerce")
        frames.append(out)
    df = pd.concat(frames, ignore_index=True).dropna(subset=["date"])
    for c in ["cH", "cD", "cA", "cO25", "cU25", "oH", "oD", "oA"]:
        df.loc[~(df[c] > 1.01), c] = np.nan  # junk / missing prices
    df["neutral"] = False
    return df.sort_values("date").reset_index(drop=True)


def load_uefa(n_back: int = 3, max_age_h: float = 6) -> pd.DataFrame:
    today = pd.Timestamp.today()
    start = today.year if today.month >= 7 else today.year - 1
    rows = []
    for comp, slug in UEFA_COMPS.items():
        for y in range(start - n_back, start + 1):
            url = f"https://fixturedownload.com/feed/json/{slug}-{y}"
            try:
                raw = _get(url, DATA_DIR / "uefa" / f"{slug}-{y}.json", max_age_h if y == start else 24 * 365)
                js = json.loads(raw)
            except Exception as e:
                if "404" not in str(e):  # a missing old season (e.g. UECL 2023) is expected
                    print(f"  ! skip {comp} {y}: {e}")
                continue
            for m in js:
                rnd = str(m.get("RoundNumber", ""))
                rows.append({
                    "date": pd.to_datetime(m["DateUtc"].replace("Z", ""), errors="coerce"),
                    "comp": comp, "season": f"{y}-{(y + 1) % 100:02d}",
                    "home_raw": m["HomeTeam"], "away_raw": m["AwayTeam"],
                    "hg": m.get("HomeTeamScore"), "ag": m.get("AwayTeamScore"),
                    "round": rnd, "location": m.get("Location"),
                })
    df = pd.DataFrame(rows)
    df["hg"] = pd.to_numeric(df["hg"], errors="coerce")
    df["ag"] = pd.to_numeric(df["ag"], errors="coerce")
    # finals are at neutral venues: the last fixture of each competition-season
    df["neutral"] = False
    for (_, _), g in df.groupby(["comp", "season"]):
        last = g["date"].max()
        if len(g[g["date"] == last]) == 1 and g["date"].dt.month.max() >= 5:
            if last.month in (5, 6):
                df.loc[g.index[g["date"] == last], "neutral"] = True
    return df


def build_name_map(domestic_names: set[str], uefa_names: set[str]) -> dict[str, str]:
    """Map fixturedownload names onto football-data names (exact, manual alias, then fuzzy)."""
    import difflib
    by_norm = {norm_name(n): n for n in domestic_names}
    out = {}
    for raw in uefa_names:
        n = norm_name(raw)
        if n in MANUAL_ALIASES and MANUAL_ALIASES[n] in domestic_names:
            out[raw] = MANUAL_ALIASES[n]
        elif n in by_norm:
            out[raw] = by_norm[n]
        else:
            stripped = " ".join(w for w in n.split() if w not in {"fc", "cf", "sc", "ac", "afc", "fk", "sk", "club", "de", "cd"})
            cand = difflib.get_close_matches(stripped, list(by_norm), n=1, cutoff=0.86)
            # unmatched: keep as its own UEFA-only team (merge known duplicate spellings)
            out[raw] = by_norm[cand[0]] if cand else UEFA_DEDUPE.get(n, raw)
    return out


def _quiet_resolve(name: str, teams: list[str], by_norm: dict) -> str | None:
    import difflib
    if name in teams:
        return name
    n = norm_name(name)
    if n in MANUAL_ALIASES and MANUAL_ALIASES[n] in teams:
        return MANUAL_ALIASES[n]
    if n in by_norm:
        return by_norm[n]
    stripped = " ".join(w for w in n.split() if w not in {"fc", "cf", "sc", "ac", "afc", "fk", "sk", "club", "de", "cd"})
    c = difflib.get_close_matches(stripped, list(by_norm), n=1, cutoff=0.8)
    return by_norm[c[0]] if c else None


def attach_fotmob_xg(df: pd.DataFrame, comps=("UCL", "UEL")) -> tuple[pd.DataFrame, int]:
    """Fill missing xG with FotMob's shot xG for matches in the player database. By default only the
    Champions League and Europa League, whose results feed has no shot data (the domestic leagues are rated
    from closing odds, and adding xG there tested slightly worse). Returns (df, matches filled)."""
    try:
        from . import players as P
        with P.connect() as con:
            m = pd.read_sql_query("SELECT id, comp, utc, home, away, home_id, away_id FROM pm_matches WHERE ok=1", con)
            s = pd.read_sql_query("SELECT match_id, team_id, SUM(xg) AS xg FROM pm_shots WHERE own_goal=0 "
                                  "GROUP BY match_id, team_id", con)
    except Exception:
        return df, 0
    if m.empty:
        return df, 0
    xg = {(r.match_id, r.team_id): r.xg for r in s.itertuples()}
    m["hx"] = [xg.get((i, t), 0.0) for i, t in zip(m["id"], m["home_id"])]
    m["ax"] = [xg.get((i, t), 0.0) for i, t in zip(m["id"], m["away_id"])]
    m["day"] = pd.to_datetime(m["utc"], utc=True, format="ISO8601").dt.tz_convert(None).dt.normalize()
    teams = sorted(set(df["home"].dropna()) | set(df["away"].dropna()))
    by_norm = {norm_name(t): t for t in teams}
    cache = {}
    res = lambda n: cache.setdefault(n, _quiet_resolve(n, teams, by_norm))
    lookup = {}
    for r in m.itertuples():
        h, a = res(r.home), res(r.away)
        if h and a:
            lookup.setdefault((h, a), []).append((r.day, r.hx, r.ax))
    df = df.copy()
    n = 0
    need = df["hg"].notna() & (df["hxg"].isna() if "hxg" in df else True)
    if comps:
        need &= df["comp"].isin(list(comps))
    for i in df.index[need]:
        cands = lookup.get((df.at[i, "home"], df.at[i, "away"]))
        if not cands:
            continue
        day = pd.Timestamp(df.at[i, "date"]).normalize()
        hit = [c for c in cands if abs((c[0] - day).days) <= 1]
        if hit:
            df.at[i, "hxg"], df.at[i, "axg"] = hit[0][1], hit[0][2]
            n += 1
    return df, n


def load_all(n_back: int = 3, max_age_h: float = 6, verbose: bool = True) -> pd.DataFrame:
    if verbose:
        print("Loading soccer data (cached for %.0fh)..." % max_age_h)
    dom = load_domestic(n_back, max_age_h)
    uefa = load_uefa(n_back, max_age_h)
    names = build_name_map(set(dom["home"]) | set(dom["away"]), set(uefa["home_raw"]) | set(uefa["away_raw"]))
    uefa["home"] = uefa["home_raw"].map(names)
    uefa["away"] = uefa["away_raw"].map(names)
    df = pd.concat([dom, uefa.drop(columns=["home_raw", "away_raw", "location", "round"])], ignore_index=True)
    return df.sort_values("date").reset_index(drop=True)


def resolve_team(name: str, teams: list[str]) -> str:
    import difflib
    if name in teams:
        return name
    n = norm_name(name)
    if n in MANUAL_ALIASES and MANUAL_ALIASES[n] in teams:
        return MANUAL_ALIASES[n]
    by_norm = {norm_name(t): t for t in teams}
    if n in by_norm:
        return by_norm[n]
    c = difflib.get_close_matches(n, list(by_norm), n=3, cutoff=0.6)
    if not c:
        raise SystemExit(f"Unknown team '{name}'.")
    if len(c) > 1 and difflib.SequenceMatcher(None, n, c[0]).ratio() < 0.85:
        print(f"  (matched '{name}' -> '{by_norm[c[0]]}'; other candidates: {[by_norm[x] for x in c[1:]]})")
    return by_norm[c[0]]
