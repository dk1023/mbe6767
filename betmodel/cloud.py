"""OddSea on GitHub: the model runs in GitHub Actions, so it works with the PC off and can be started from the
phone page.

  every 2 hours        full model run (fresh DraftKings + Kalshi prices, new picks)
  every 30 minutes     scores, grading and results (skipped while no game is live or waiting for a result)
  any time             the Run button on the phone page or in the PC app

Between runs the database travels as a file attached to the repo's "oddsea-data" release (GitHub keeps it, and it
is not part of the code history); every run publishes the phone page to GitHub Pages.

On the PC (python -m betmodel cloud ...):
  setup      one time: upload the code and this PC's data, switch the page to GitHub Actions, start a first run
  run        start a model run on GitHub and wait for it   (scores and history work the same way)
  pull       copy the newest results from GitHub to this PC
  status     what GitHub is doing now
  on / off   the app's Run buttons use GitHub (on) or this PC (off)
The workflow (.github/workflows/oddsea.yml) calls `plan` and `job`.

Only the standard library is imported at the top, so `plan` works before any packages are installed.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SITE = ROOT / "snapshot"
CONFIG = ROOT / "config.json"
SETTINGS = ROOT / "settings.json"
STAMP = DATA / "cloud_state.json"          # which GitHub copy of the data this PC has
WORKFLOW = "oddsea.yml"
TAG = "oddsea-data"                        # the release whose files carry the data between runs
STATE_FILES = ("betmodel.db", "last_run.pkl", "lineup_ratings.pkl")
FILE_DIRS = ("nfl", "soccer")              # downloaded source files (re-downloadable; GitHub's cache keeps them)
KEEP_STATES = 4                            # newest data files kept on the release (older ones are deleted)
SEED_EVERY_DAYS = 7                        # refresh the saved copy of the source files this often
API = os.environ.get("ODDSEA_GITHUB_API") or "https://api.github.com"  # (override only for local tests)
SETTINGS_KEYS = ("bankroll", "unit", "kelly_fraction", "max_stake_pct", "min_edge", "soccer_comps", "soccer_days",
                 "pick_rules", "odds_source")
KINDS = {"run": "Running the model", "scores": "Updating scores and results", "history": "Rebuilding the model history"}
# files uploaded as the code (never config.json, data/, snapshot/ or logs/)
CODE = ("betmodel/*.py", "betmodel/*.json", "requirements.txt", "requirements-cloud.txt", "settings.json", "README.md",
        ".gitignore", ".github/workflows/oddsea.yml", "config.example.json", "*.bat", "betmodel.cmd")
# workflow step names as the progress display words them
STEP_TEXT = {"Set up job": "Starting a GitHub computer", "Get the code": "Getting the code",
             "Decide what to run": "Getting ready", "Set up Python": "Setting up Python",
             "Install packages": "Installing packages", "Restore the NFL and soccer files": "Loading the NFL and soccer data",
             "Upload the page": "Preparing the page", "Publish the page": "Publishing the page"}
STEP_WEIGHT = {"Run the model": 8, "Install packages": 2}


# ============================================================================ GitHub
class GitHubError(RuntimeError):
    def __init__(self, status: int, message: str, need: str | None = None):
        super().__init__(message)
        self.status, self.need = status, need


def _gh(method: str, path: str, tok: str | None = None, body=None, data: bytes | None = None,
        ctype: str | None = None, timeout: float = 60):
    """One GitHub REST call; returns the decoded JSON (None for an empty reply). GETs are retried on hiccups."""
    url = path if path.startswith("http") else API + path
    h = {"Accept": "application/vnd.github+json", "User-Agent": "OddSea", "X-GitHub-Api-Version": "2022-11-28"}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    if body is not None:
        data, ctype = json.dumps(body).encode(), "application/json"
    if ctype:
        h["Content-Type"] = ctype
    tries = 3 if method == "GET" else 1
    for attempt in range(tries):
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
            return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                msg = json.loads(raw).get("message") or ""
            except Exception:
                msg = raw[:200].decode("utf-8", "ignore").strip()
            if e.code >= 500 and attempt < tries - 1:
                time.sleep(2 + 3 * attempt)
                continue
            raise GitHubError(e.code, f"GitHub said {e.code}" + (f" ({msg})" if msg else ""),
                              e.headers.get("X-Accepted-GitHub-Permissions")) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            if attempt < tries - 1:
                time.sleep(2 + 3 * attempt)
                continue
            raise GitHubError(0, f"couldn't reach GitHub ({e})") from None


def in_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def _config() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8-sig"))  # -sig: a file saved by Notepad starts with a BOM
    except Exception:
        return {}


def _set_config(**kw) -> None:
    c = _config()
    c.update(kw)
    CONFIG.write_text(json.dumps(c, indent=2), encoding="utf-8")


def repo() -> str | None:
    return os.environ.get("GITHUB_REPOSITORY") if in_actions() else (_config().get("github_repo") or None)


def token() -> str | None:
    if in_actions():
        return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    return _config().get("github_token") or None  # this PC's key (not GH_TOKEN, which other tools may set)


def enabled() -> bool:
    """On the PC: do the app's Run / Update buttons use GitHub (set by `cloud setup`, switched with on/off)?"""
    c = _config()
    return not in_actions() and bool(c.get("cloud") and c.get("github_token") and c.get("github_repo"))


def page_url(rp: str | None = None) -> str | None:
    rp = rp or repo()
    if not rp or "/" not in rp:
        return None
    owner, name = rp.split("/", 1)
    return f"https://{owner.lower()}.github.io/{name}/"


_branch_cache: dict = {}


def _branch(rp: str, tk: str | None) -> str:
    if os.environ.get("GITHUB_REF_NAME") and in_actions():
        return os.environ["GITHUB_REF_NAME"]
    if rp not in _branch_cache:
        try:
            _branch_cache[rp] = (_gh("GET", f"/repos/{rp}", tk) or {}).get("default_branch") or "main"
        except GitHubError:
            return "main"
    return _branch_cache[rp]


def _utc_iso(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(s) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _output(k: str, v) -> None:
    """A step output for the workflow (ignored outside GitHub Actions)."""
    p = os.environ.get("GITHUB_OUTPUT")
    if p:
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{k}={v}\n")


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


# ============================================================================ settings
def settings_from(c: dict) -> dict:
    return {k: c[k] for k in SETTINGS_KEYS if k in c}


def _put_file(rp: str, tk: str, path: str, data: bytes, message: str) -> bool:
    """Create or update one file in the repo (False if it was already the same)."""
    sha = None
    try:
        cur = _gh("GET", f"/repos/{rp}/contents/{path}?ref={_branch(rp, tk)}", tk) or {}
        sha = cur.get("sha")
        if base64.b64decode(cur.get("content") or "") == data:
            return False
    except GitHubError as e:
        if e.status != 404:
            raise
    body = {"message": message, "content": base64.b64encode(data).decode(), "branch": _branch(rp, tk)}
    if sha:
        body["sha"] = sha
    _gh("PUT", f"/repos/{rp}/contents/{path}", tk, body)
    return True


def save_settings(c: dict) -> str:
    """Write settings.json (it holds no keys) and, when the runs happen on GitHub, upload it so they use it too."""
    SETTINGS.write_text(json.dumps(settings_from(c), indent=2) + "\n", encoding="utf-8")
    if not enabled():
        return ""
    try:
        _put_file(repo(), token(), "settings.json", SETTINGS.read_bytes(), "Settings from the OddSea app")
        return "Sent to GitHub too, so the runs there use them."
    except GitHubError as e:
        hint = " (the key needs Contents: Read and write)" if e.status in (403, 404) else ""
        return f"Saved on this PC, but couldn't send them to GitHub: {e}{hint}"


# ============================================================================ the data between runs
def _asset_name(prefix: str) -> str:
    return f"{prefix}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.tar.gz"


def _asset_time(a: dict) -> datetime | None:
    try:
        stem = a["name"].split("-", 1)[1].split(".", 1)[0]
        return datetime.strptime(stem, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except (KeyError, IndexError, ValueError):
        return None


def _release(rp: str, tk: str | None = None) -> dict | None:
    try:
        return _gh("GET", f"/repos/{rp}/releases/tags/{TAG}", tk)
    except GitHubError as e:
        if e.status == 404:
            return None
        raise


def _ensure_release(rp: str, tk: str) -> dict:
    rel = _release(rp, tk)
    if rel:
        return rel
    return _gh("POST", f"/repos/{rp}/releases", tk, {
        "tag_name": TAG, "target_commitish": _branch(rp, tk), "name": "OddSea data",
        "body": "OddSea's runs on GitHub keep their data here between runs (picks, results, the price log). "
                "Please don't delete this release or its files.",
        "prerelease": True, "make_latest": "false"})


def _assets(rel: dict | None, prefix: str) -> list[dict]:
    """The release's files starting with prefix, newest first (their names carry the UTC time)."""
    a = [x for x in (rel or {}).get("assets") or []
         if x.get("name", "").startswith(prefix + "-") and x.get("state", "uploaded") == "uploaded"]
    return sorted(a, key=lambda x: x["name"], reverse=True)


def _upload(rel: dict, path: Path, tk: str) -> dict:
    url = rel["upload_url"].split("{", 1)[0] + "?" + urllib.parse.urlencode({"name": path.name})
    return _gh("POST", url, tk, data=path.read_bytes(), ctype="application/gzip", timeout=900)


def _prune_assets(rp: str, prefix: str, keep: int, tk: str) -> None:
    for a in _assets(_release(rp, tk), prefix)[keep:]:
        try:
            _gh("DELETE", f"/repos/{rp}/releases/assets/{a['id']}", tk)
        except GitHubError as e:
            print(f"  ! couldn't delete the old {a['name']}: {e}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _download(asset: dict, dest: Path, tk: str | None = None) -> None:
    """Fetch a release file. The public link needs no key; a private repo's file goes through the API."""
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": "OddSea"})
            with urllib.request.urlopen(req, timeout=600) as r, open(dest, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
            return
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 404 and tk:  # private repo: the API hands out a short-lived link (no key on that one)
                req = urllib.request.Request(asset["url"], headers={
                    "Authorization": f"Bearer {tk}", "Accept": "application/octet-stream", "User-Agent": "OddSea"})
                try:
                    urllib.request.build_opener(_NoRedirect).open(req, timeout=60)
                except urllib.error.HTTPError as r:
                    loc = r.headers.get("Location")
                    if r.code in (301, 302, 303, 307, 308) and loc:
                        with urllib.request.urlopen(urllib.request.Request(loc, headers={"User-Agent": "OddSea"}),
                                                    timeout=600) as rr, open(dest, "wb") as f:
                            shutil.copyfileobj(rr, f, 1 << 20)
                        return
                    last = r
        except Exception as e:  # network hiccup: try again
            last = e
        time.sleep(2 + 4 * attempt)
    raise RuntimeError(f"couldn't download {asset.get('name')}: {last}")


def _prune_db(con: sqlite3.Connection) -> None:
    """Drop what the next run doesn't need: expired API replies (a run fetches fresh ones), anything tied to
    a key (API-Football / The Odds API, never used on GitHub), and old injury-list snapshots."""
    now, day = time.time(), 86400.0
    con.execute("DELETE FROM api_cache WHERE key LIKE 'af:%' OR key LIKE 'oddsapi:%' OR key LIKE 'snapshot:%'")
    con.execute("DELETE FROM api_cache WHERE (key LIKE 'espn:%' OR key LIKE 'fotmob:%' OR key LIKE 'openmeteo:%' OR "
                "(key LIKE 'kalshi:%' AND key NOT LIKE 'kalshi:ev:%' AND key NOT LIKE 'kalshi:close:%')) AND saved < ?",
                (now - 0.25 * day,))
    con.execute("DELETE FROM api_cache WHERE (key LIKE 'sim:%' AND saved < ?) OR (key LIKE 'kalshi:ev:%' AND saved < ?)",
                (now - 60 * day, now - 30 * day))
    con.execute("DELETE FROM injuries WHERE snap_at < ?", ((datetime.now() - timedelta(days=21)).strftime("%Y-%m-%d"),))
    con.execute("DELETE FROM api_calls WHERE day < ?",
                ((datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d"),))
    con.commit()


def _secret_bits() -> list[str]:
    """This PC's saved keys and distinctive pieces of them (pieces of a key can end up inside a cache entry name)."""
    bits = set()
    c = _config()
    for k in ("odds_api_key", "api_football_key", "github_token"):
        v = str(c.get(k) or "").strip()
        if len(v) < 12:
            continue
        core = v.split("_", 2)[-1] if v.startswith(("github_pat_", "ghp_", "gho_")) else v
        mid = len(core) // 2
        bits.update({v, core[:8], core[mid - 4:mid + 4], v[-8:], v[-6:]})
    return sorted(b for b in bits if len(b) >= 6)


def _scan(blobs: dict[str, bytes], bits: list[str]) -> None:
    for name, data in blobs.items():
        for b in bits:
            if b.encode() in data:
                raise RuntimeError(f"{name} contains part of one of your saved keys, so nothing was uploaded")


def export_state(dest: Path, bits: list[str] = ()) -> Path:
    """Pack the database (trimmed and compacted) and the saved run into dest (.tar.gz)."""
    if not (DATA / "betmodel.db").exists():
        raise RuntimeError("there is no database to save yet")
    tmp = Path(tempfile.mkdtemp(prefix="oddsea-"))
    try:
        files = {"betmodel.db": tmp / "betmodel.db"}
        src, dst = sqlite3.connect(DATA / "betmodel.db"), sqlite3.connect(files["betmodel.db"])
        try:
            src.backup(dst)  # a consistent copy even while the app is using the database
        finally:
            src.close()
        _prune_db(dst)
        dst.execute("VACUUM")  # rewrites the file, so deleted rows are really gone from it
        dst.close()
        for f in STATE_FILES[1:]:
            if (DATA / f).exists():
                shutil.copy2(DATA / f, tmp / f)
                files[f] = tmp / f
        files["cloud_state.json"] = tmp / "cloud_state.json"
        files["cloud_state.json"].write_text(json.dumps({
            "made_at": _utc_iso(), "by": "github" if in_actions() else "pc",
            "run": os.environ.get("GITHUB_RUN_ID")}), encoding="utf-8")
        _scan({n: p.read_bytes() for n, p in files.items()}, list(bits))
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(dest, "w:gz", compresslevel=6) as t:
            for n, p in files.items():
                t.add(p, arcname=n)
        return dest
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _replace(src: Path, dst: Path) -> None:
    for _ in range(15):
        try:
            os.replace(src, dst)
            return
        except PermissionError:  # Windows: something has the file open for a moment
            time.sleep(0.3)
        except OSError:  # e.g. the temp folder is on another drive
            break
    shutil.copyfile(src, dst)


def import_state(tgz: Path) -> dict:
    """Unpack a data file made by export_state into data/. The database is copied into the existing file with
    SQLite's backup, which is safe while the app has it open. Returns the file's stamp."""
    tmp = Path(tempfile.mkdtemp(prefix="oddsea-"))
    try:
        allowed = set(STATE_FILES) | {"cloud_state.json"}
        with tarfile.open(tgz) as t:
            members = t.getmembers()
            for m in members:
                if not m.isfile() or m.name not in allowed:
                    raise RuntimeError(f"unexpected entry in the data file: {m.name!r}")
            for m in members:  # written under our own names only: nothing from the archive picks a path
                with t.extractfile(m) as fsrc, open(tmp / m.name, "wb") as fdst:
                    shutil.copyfileobj(fsrc, fdst, 1 << 20)
        if not (tmp / "betmodel.db").exists():
            raise RuntimeError("the data file has no database in it")
        con = sqlite3.connect(tmp / "betmodel.db")
        try:
            ok = con.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            con.close()
        if ok != "ok":
            raise RuntimeError(f"the downloaded database is damaged ({ok})")
        DATA.mkdir(parents=True, exist_ok=True)
        for attempt in range(10):
            src, dst = sqlite3.connect(tmp / "betmodel.db"), sqlite3.connect(DATA / "betmodel.db", timeout=30)
            try:
                src.backup(dst)
                break
            except sqlite3.OperationalError:
                if attempt == 9:
                    raise
                time.sleep(0.5)
            finally:
                src.close()
                dst.close()
        for f in STATE_FILES[1:]:
            if (tmp / f).exists():
                _replace(tmp / f, DATA / f)
        return _read_json(tmp / "cloud_state.json")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def export_files(dest: Path, bits: list[str] = ()) -> Path:
    """Pack the downloaded NFL / soccer source files (a head start for a run whose cache is empty)."""
    with tarfile.open(dest, "w:gz", compresslevel=6) as t:
        for d in FILE_DIRS:
            for f in sorted((DATA / d).rglob("*")) if (DATA / d).exists() else []:
                if f.is_file():
                    if bits:
                        _scan({f.relative_to(ROOT).as_posix(): f.read_bytes()}, list(bits))
                    t.add(f, arcname=f.relative_to(DATA).as_posix())
    return dest


def import_files(tgz: Path) -> int:
    n = 0
    with tarfile.open(tgz) as t:
        for m in t.getmembers():
            parts = Path(m.name).parts
            if (not m.isfile() or not parts or parts[0] not in FILE_DIRS or ".." in parts
                    or Path(m.name).is_absolute() or ":" in m.name):
                continue
            out = DATA.joinpath(*parts)
            out.parent.mkdir(parents=True, exist_ok=True)
            with t.extractfile(m) as fsrc, open(out, "wb") as fdst:
                shutil.copyfileobj(fsrc, fdst, 1 << 20)
            os.utime(out, (m.mtime, m.mtime))  # keep the download time: freshness checks go by it
            n += 1
    return n


# ============================================================================ inside GitHub Actions
def _site_status(rp: str | None) -> dict | None:
    url = page_url(rp)
    if not url:
        return None
    try:
        req = urllib.request.Request(f"{url}status.json?t={int(time.time())}", headers={"User-Agent": "OddSea"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except Exception:
        return None


PREGAME_H = 3  # scheduled updates this close to a kickoff are full model runs


def plan(kind: str, event: str = "") -> dict:
    """What this workflow run should do. Runs you start always go. A scheduled score update becomes a full model
    run when a game kicks off within PREGAME_H hours (confirmed lineups, NFL inactives and late price moves come
    out then), and is skipped while no game is live or waiting for a result (the page fetches live scores itself)."""
    now = datetime.now(timezone.utc)
    kind = kind if kind in KINDS else "run"
    out = {"kind": kind, "go": "true", "day": now.strftime("%Y-%m-%d"), "why": "started by hand" if event != "schedule"
           else "scheduled"}
    if event == "schedule" and kind == "scores":
        st = _site_status(repo())
        if st is None:
            out["why"] = "no published page to check"
        else:
            due = soon = 0
            for g in st.get("games") or []:
                ko = _parse_utc(g.get("ko"))
                if g.get("st") == "post" or ko is None:
                    continue
                if g.get("st") == "in" or ko - timedelta(minutes=15) <= now <= ko + timedelta(hours=6):
                    due += 1
                if g.get("st") != "in" and now < ko <= now + timedelta(hours=PREGAME_H):
                    soon += 1
            last = _parse_utc(st.get("built"))
            if soon:
                out.update(kind="run", why=f"{soon} game(s) kick off within {PREGAME_H} hours: full run for confirmed "
                                           "lineups, inactives and fresh prices")
            elif due:
                out["why"] = f"{due} game(s) live or waiting for a result"
            elif last is None or now - last > timedelta(hours=8):
                out["why"] = "the last update was over 8 hours ago"
            else:
                out.update(go="false", why="no game is live or waiting for a result")
    for k, v in out.items():
        _output(k, v)
    print(f"Plan: {out['kind']} - {'go' if out['go'] == 'true' else 'skip'} ({out['why']})")
    return out


def _keepalive() -> None:
    """GitHub pauses a public repo's schedule after 60 days without a commit; add an empty one after 45."""
    if not in_actions():
        return
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%ct"], cwd=ROOT, capture_output=True, text=True,
                             timeout=30).stdout.strip()
        if not out or time.time() - int(out) < 45 * 86400:
            return
        who = {"GIT_AUTHOR_NAME": "OddSea", "GIT_COMMITTER_NAME": "OddSea",
               "GIT_AUTHOR_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
               "GIT_COMMITTER_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com"}
        subprocess.run(["git", "commit", "--allow-empty", "-m",
                        "Keep OddSea's schedule on (GitHub pauses it after 60 days without a commit)"],
                       cwd=ROOT, env={**os.environ, **who}, check=True, capture_output=True, timeout=60)
        subprocess.run(["git", "push"], cwd=ROOT, check=True, capture_output=True, timeout=120)
        print("Added a keep-alive commit")
    except Exception as e:
        print(f"  ! keep-alive commit failed: {e}")


def _refresh_seed(rp: str, tk: str) -> None:
    seeds = _assets(_release(rp, tk), "files")
    newest = _asset_time(seeds[0]) if seeds else None
    if newest and datetime.now(timezone.utc) - newest < timedelta(days=SEED_EVERY_DAYS):
        return
    if not (DATA / "nfl" / "games.csv").exists():
        return
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        out = export_files(Path(td) / _asset_name("files"))
        _upload(_release(rp, tk), out, tk)
        print(f"Saved a fresh copy of the source files ({out.stat().st_size / 1e6:.1f} MB)")
    _prune_assets(rp, "files", 1, tk)


def _say(f: float, m: str) -> None:
    print(f"[{100 * f:3.0f}%] {m}", flush=True)


def _job(kind: str, rp: str, tk: str) -> None:
    t0 = time.time()
    rel = _release(rp, tk)
    states = _assets(rel, "state")
    if not states:
        raise RuntimeError("no saved data on GitHub yet: run `python -m betmodel cloud setup` on the PC first")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        p = Path(td) / states[0]["name"]
        _download(states[0], p, tk)
        import_state(p)
    print(f"Restored {states[0]['name']} ({states[0]['size'] / 1e6:.1f} MB)")
    if not (DATA / "nfl" / "games.csv").exists():  # GitHub's cache was empty: start from the saved copy
        seeds = _assets(rel, "files")
        if seeds:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
                p = Path(td) / seeds[0]["name"]
                _download(seeds[0], p, tk)
                print(f"Restored {import_files(p)} NFL / soccer source files from {seeds[0]['name']}")
    os.environ["BETMODEL_NO_PUBLISH"] = "1"
    from . import engine
    if kind == "run":
        res = engine.run(_say, publish=False)
    elif kind == "scores":
        res = engine.refresh_scores(_say, publish=False)
    else:
        from . import history as H
        H.build(_say)
        res = engine.load_last()
    for n in (res.notes if res is not None else []):
        print("  note:", n)
    if res is not None:
        from . import snapshot
        snapshot.save(res)
        print(f"Page built: {SITE.name}/index.html ({(SITE / 'index.html').stat().st_size / 1e3:.0f} KB)")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        out = export_state(Path(td) / _asset_name("state"))
        _upload(rel, out, tk)
        print(f"Saved {out.name} ({out.stat().st_size / 1e6:.1f} MB)")
    _prune_assets(rp, "state", KEEP_STATES, tk)
    try:
        _refresh_seed(rp, tk)
    except Exception as e:
        print(f"  ! couldn't refresh the saved source files: {e}")
    _keepalive()
    _output("deploy", "true" if res is not None else "false")
    print(f"Done in {time.time() - t0:.0f}s")


def job(kind: str) -> None:
    """Inside GitHub Actions: restore the data, run, write the page, save the data back."""
    rp, tk = repo(), token()
    try:
        _job(kind, rp, tk)
    except Exception as e:
        if os.environ.get("GITHUB_EVENT_NAME") != "schedule":
            raise
        # A scheduled update that fails (a data source down, say) leaves the last page and data as they were.
        # GitHub e-mails every failed run, so it's reported as a warning instead; the next run tries again.
        import traceback
        traceback.print_exc()
        print(f"::warning title=OddSea {kind} skipped::{type(e).__name__}: {e}")
        _output("deploy", "false")


# ============================================================================ on the PC
def pull(force: bool = False) -> bool:
    """Copy the newest GitHub results to this PC if they're newer than what it has. True when it copied."""
    rp, tk = repo(), token()
    states = _assets(_release(rp, tk), "state")
    if not states:
        return False
    if not force and _read_json(STAMP).get("asset") == states[0]["name"]:
        return False
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        p = Path(td) / states[0]["name"]
        _download(states[0], p, tk)
        stamp = import_state(p)
    stamp.update(asset=states[0]["name"], pulled_at=_utc_iso())
    STAMP.write_text(json.dumps(stamp), encoding="utf-8")
    return True


def _runs(rp: str, tk: str, extra: str = "") -> list[dict]:
    js = _gh("GET", f"/repos/{rp}/actions/workflows/{WORKFLOW}/runs?per_page=10{extra}", tk) or {}
    return js.get("workflow_runs") or []


def dispatch(kind: str) -> int:
    """Start `kind` on GitHub. Returns the newest run id from just before, to recognise the new run."""
    rp, tk = repo(), token()
    try:
        before = max([r["id"] for r in _runs(rp, tk)] or [0])
        _gh("POST", f"/repos/{rp}/actions/workflows/{WORKFLOW}/dispatches", tk,
            {"ref": _branch(rp, tk), "inputs": {"kind": kind}})
    except GitHubError as e:
        if e.status == 404:
            raise RuntimeError(f"GitHub couldn't find OddSea's workflow on {rp} ({e}). If `python -m betmodel cloud "
                               "setup` has been run, the key needs 'Actions: Read and write' on the repo.") from None
        if e.status in (401, 403):
            raise RuntimeError(f"GitHub didn't let this PC's key start a run ({e}). It needs 'Actions: Read and write' "
                               f"on {rp}: github.com > Settings > Developer settings > Fine-grained tokens > your key "
                               "> Edit.") from None
        raise
    return before


def progress_of(rp: str, tk: str, run: dict) -> tuple[float, str]:
    """(fraction done, what it's doing) for a run that hasn't finished."""
    if run.get("status") != "in_progress":
        return 0.04, "waiting for a GitHub computer"
    jobs = (_gh("GET", f"/repos/{rp}/actions/runs/{run['id']}/jobs", tk) or {}).get("jobs") or []
    steps = [s for j in jobs for s in j.get("steps") or [] if not s["name"].startswith(("Post ", "Complete job"))]
    if not steps:
        return 0.06, "starting a GitHub computer"
    total = sum(STEP_WEIGHT.get(s["name"], 1) for s in steps) + (0 if len(jobs) > 1 else 1)  # +1: publishing
    done = sum(STEP_WEIGHT.get(s["name"], 1) for s in steps if s.get("status") == "completed")
    now = next((s["name"] for s in steps if s.get("status") == "in_progress"), None)
    text = STEP_TEXT.get(now, "fitting, pricing and simulating every game" if now == "Run the model" else (now or "working"))
    return max(0.05, min(0.95, done / max(total, 1))), text[0].lower() + text[1:]


def trigger(kind: str = "run", progress=lambda f, m: None, timeout_min: float = 50):
    """Run `kind` on GitHub, follow it, then copy the results to this PC. Returns the new saved run."""
    rp, tk = repo(), token()
    label = KINDS[kind]
    progress(0.02, f"{label} on GitHub: starting")
    run = None
    for attempt in range(2):
        before = dispatch(kind)
        t0, run = time.time(), None
        while True:
            time.sleep(3 if run is None else 5)
            if time.time() - t0 > timeout_min * 60:
                raise RuntimeError(f"still running on GitHub after {timeout_min:.0f} minutes"
                                   + (f": {run['html_url']}" if run else ""))
            if run is None:
                new = [r for r in _runs(rp, tk, "&event=workflow_dispatch") if r["id"] > before]
                if not new:
                    if time.time() - t0 > 120:
                        raise RuntimeError("GitHub took the request but no run showed up; check the repo's Actions tab")
                    continue
                run = min(new, key=lambda r: r["id"])
            run = _gh("GET", f"/repos/{rp}/actions/runs/{run['id']}", tk)
            if run["status"] == "completed":
                break
            f, m = progress_of(rp, tk, run)
            mins, secs = divmod(int(time.time() - t0), 60)
            progress(f, f"{label} on GitHub: {m} ({mins}:{secs:02d})")
        if run.get("conclusion") == "cancelled" and attempt == 0:  # a scheduled update took its place in the queue
            progress(0.03, f"{label} on GitHub: a scheduled update went first; starting again")
            continue
        break
    if run.get("conclusion") != "success":
        raise RuntimeError(f"the run on GitHub ended '{run.get('conclusion')}'. Details: {run.get('html_url')}")
    progress(0.97, "Copying the new results to this PC")
    pull()
    from . import engine
    progress(1.0, "Done")
    return engine.load_last()


def status() -> None:
    rp, tk = repo(), token()
    print(f"Repo {rp}   page {page_url(rp)}")
    print("This PC's Run buttons: " + ("GitHub" if enabled() else "this PC (python -m betmodel cloud on to use GitHub)"))
    try:
        runs = _runs(rp, tk)[:8]
    except GitHubError as e:
        if e.status != 404:
            raise
        print("No OddSea workflow on GitHub yet: python -m betmodel cloud setup")
        return
    for r in runs:
        print(f"  {r['created_at']}  {r['event']:<18} {r['status']:<11} {r.get('conclusion') or '':<10} {r['html_url']}")
    states = _assets(_release(rp, tk), "state")
    print("Saved data on GitHub: " + (", ".join(f"{a['name']} ({a['size'] / 1e6:.1f} MB)" for a in states) or "none"))
    print("This PC has: " + (_read_json(STAMP).get("asset") or "no GitHub copy yet"))


# ============================================================================ one-time setup
def code_files() -> dict[str, bytes]:
    out = {}
    for pat in CODE:
        for p in sorted(ROOT.glob(pat)):
            if p.is_file() and p.name != "config.json":
                out[p.relative_to(ROOT).as_posix()] = p.read_bytes()
    return out


def _commit_files(rp: str, tk: str, branch: str, files: dict[str, bytes], message: str) -> str | None:
    """One commit on `branch` adding / updating these files (the rest of the repo stays as it is).
    Returns the new commit, or None when nothing changed."""
    try:
        head = _gh("GET", f"/repos/{rp}/git/ref/heads/{branch}", tk)["object"]["sha"]
        base_tree = _gh("GET", f"/repos/{rp}/git/commits/{head}", tk)["tree"]["sha"]
        entries = []
        for path, data in files.items():
            blob = _gh("POST", f"/repos/{rp}/git/blobs", tk,
                       {"content": base64.b64encode(data).decode(), "encoding": "base64"})
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        tree = _gh("POST", f"/repos/{rp}/git/trees", tk, {"base_tree": base_tree, "tree": entries})
        if tree["sha"] == base_tree:
            return None
        commit = _gh("POST", f"/repos/{rp}/git/commits", tk, {"message": message, "tree": tree["sha"], "parents": [head]})
        _gh("PATCH", f"/repos/{rp}/git/refs/heads/{branch}", tk, {"sha": commit["sha"]})
    except GitHubError as e:
        if e.status in (403, 404, 422):
            raise RuntimeError(f"GitHub refused the code upload ({e}" + (f"; it asked for: {e.need}" if e.need else "")
                               + "). The key needs 'Workflows: Read and write' (plus 'Contents: Read and write') to "
                               "add the workflow file.") from None
        raise
    return commit["sha"]


def _pages_to_actions(rp: str, tk: str) -> str:
    try:
        cur = _gh("GET", f"/repos/{rp}/pages", tk)
    except GitHubError as e:
        if e.status != 404:
            raise
        cur = None
    if cur and cur.get("build_type") == "workflow":
        return "the page is already published by GitHub Actions"
    try:
        if cur:
            _gh("PUT", f"/repos/{rp}/pages", tk, {"build_type": "workflow"})
        else:
            _gh("POST", f"/repos/{rp}/pages", tk, {"build_type": "workflow"})
    except GitHubError as e:
        raise RuntimeError(
            f"GitHub didn't let the key switch the page to GitHub Actions ({e}"
            + (f"; it asked for: {e.need}" if e.need else "") + "). Nothing was uploaded. Either give the key "
            f"'Pages: Read and write', or switch it once by hand: https://github.com/{rp}/settings/pages > "
            "Build and deployment > Source: GitHub Actions. Then run the setup again.") from None
    return "the page is now published by GitHub Actions"


def setup(dry_run: bool = False, start: bool = True, force: bool = False) -> None:
    rp, tk = repo(), token()
    if not (rp and tk):
        raise SystemExit("Save your GitHub key and repo first: keys.bat (or python -m betmodel keys set github ...)")
    c = _config()
    SETTINGS.write_text(json.dumps(settings_from(c), indent=2) + "\n", encoding="utf-8")
    bits = _secret_bits()
    files = code_files()
    _scan(files, bits)
    print(f"Code: {len(files)} files ({sum(map(len, files.values())) / 1e6:.1f} MB), checked: none of your keys are in them")
    td = Path(tempfile.mkdtemp(prefix="oddsea-setup-"))
    try:
        state = export_state(td / _asset_name("state"), bits)
        seed = export_files(td / _asset_name("files"), bits)
        print(f"Data: {state.name} ({state.stat().st_size / 1e6:.1f} MB) and {seed.name} "
              f"({seed.stat().st_size / 1e6:.1f} MB), checked: none of your keys are in them")
        if dry_run:
            print("Dry run: nothing was uploaded.")
            return
        have = _assets(_release(rp, tk), "state")
        if have and not force and _read_json(STAMP).get("asset") != have[0]["name"]:
            raise SystemExit("GitHub already has newer data than this PC (the cloud runs are going). Run "
                             "`python -m betmodel cloud pull` first, or add --force to replace it with this PC's.")
        print("1/4 " + _pages_to_actions(rp, tk))
        rel = _ensure_release(rp, tk)
        for p in (state, seed):
            _upload(rel, p, tk)
        _prune_assets(rp, "state", KEEP_STATES, tk)
        _prune_assets(rp, "files", 1, tk)
        STAMP.write_text(json.dumps({"asset": state.name, "pulled_at": _utc_iso(), "by": "pc"}), encoding="utf-8")
        print(f"2/4 Data uploaded to the '{TAG}' release")
        branch = _branch(rp, tk)
        sha = _commit_files(rp, tk, branch, files, "OddSea: run the model on GitHub (code, workflow and settings)")
        print(f"3/4 Code uploaded ({branch} @ {sha[:7]})" if sha else f"3/4 Code on {branch} is already up to date")
    finally:
        shutil.rmtree(td, ignore_errors=True)
    _set_config(cloud=True)
    print("4/4 This PC's Run model / Update scores buttons now start the runs on GitHub")
    if start:
        for _ in range(40):  # GitHub takes a moment to register a new workflow file
            try:
                _gh("GET", f"/repos/{rp}/actions/workflows/{WORKFLOW}", tk)
                break
            except GitHubError as e:
                if e.status != 404:
                    raise
                time.sleep(3)
        print("Starting the first run on GitHub (about 5 minutes)...")
        trigger("run", lambda f, m: print(f"\r  {m:<96}", end="", flush=True))
        print(f"\nDone. Page: {page_url(rp)}")


def push_code(message: str = "Update OddSea") -> str:
    """Upload this PC's code (and settings.json) to GitHub, so the runs there use it. Checked for keys first."""
    rp, tk = repo(), token()
    files = code_files()
    _scan(files, _secret_bits())
    sha = _commit_files(rp, tk, _branch(rp, tk), files, message)
    return f"Code uploaded ({sha[:7]}); the next run on GitHub uses it." if sha else "GitHub already has this code."


# ============================================================================ command line
def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="betmodel cloud", description="Run OddSea on GitHub")
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("plan", help="(workflow) decide what this run does")
    p.add_argument("--kind", default="run")
    p.add_argument("--event", default="")
    p = sp.add_parser("job", help="(workflow) restore the data, run, write the page, save the data")
    p.add_argument("kind", choices=list(KINDS))
    p = sp.add_parser("setup", help="one time: upload the code and data, switch the page to GitHub Actions")
    p.add_argument("--dry-run", action="store_true", help="check and pack everything, upload nothing")
    p.add_argument("--no-start", action="store_true", help="don't start a first run")
    p.add_argument("--force", action="store_true", help="replace newer data on GitHub with this PC's")
    for k, h in (("run", "start a model run on GitHub and wait"), ("scores", "update scores on GitHub and wait"),
                 ("history", "rebuild the model history on GitHub and wait")):
        sp.add_parser(k, help=h)
    sp.add_parser("pull", help="copy the newest results from GitHub to this PC")
    p = sp.add_parser("code", help="upload this PC's code changes to GitHub (checked for keys first)")
    p.add_argument("-m", "--message", default="Update OddSea")
    sp.add_parser("status", help="what GitHub is doing")
    sp.add_parser("on", help="the app's Run buttons use GitHub")
    sp.add_parser("off", help="the app's Run buttons run on this PC")
    a = ap.parse_args(argv)
    if a.cmd == "plan":
        plan(a.kind, a.event)
    elif a.cmd == "job":
        job(a.kind)
    elif a.cmd == "setup":
        setup(a.dry_run, not a.no_start, a.force)
    elif a.cmd in KINDS:
        trigger(a.cmd, lambda f, m: print(f"\r  {m:<96}", end="", flush=True))
        print("\nDone; the results are on this PC and the page.")
    elif a.cmd == "pull":
        print("Copied the newest results from GitHub." if pull() else "This PC already has the newest results.")
    elif a.cmd == "code":
        print(push_code(a.message))
    elif a.cmd == "status":
        status()
    elif a.cmd in ("on", "off"):
        _set_config(cloud=a.cmd == "on")
        print("Run buttons now use GitHub." if a.cmd == "on" else "Run buttons now run on this PC.")


if __name__ == "__main__":
    main()
