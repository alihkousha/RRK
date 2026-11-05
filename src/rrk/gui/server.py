"""The scraper's local web GUI: start and watch runs of main.py, and explore
what they stored (search, profiles, links between people and companies, leads).

    cd src/rrk && python3 -m gui.server        # then open http://127.0.0.1:8765

It listens on 127.0.0.1 only, as it starts processes.  A run goes on when the
GUI is closed; its settings, log and exit code are kept in runs/.
"""
from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import re
import shlex
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import config
from database.connection import connect, connect_explorer, connect_readonly
from database.repository import AdRepository
from gui.explore import Explorer, build_graph, build_notices, graph_outdated, index_texts

HERE = Path(__file__).resolve().parent
SCRAPER = HERE.parent  # main.py's directory: runs start there, as from the shell
STATIC = HERE / "static"
RUNS = SCRAPER / "runs"
log = logging.getLogger("rrk.gui")
GRAPH_EVERY = 600  # s: the graph is rebuilt at most this often while texts keep coming
mimetypes.add_type("font/woff2", ".woff2")

# The main.py options the GUI may pass, each with its smallest value (None: a
# switch).  Never --page-size (grid pages stay at the browser's 100 rows) or
# --db (runs write to the database the GUI reads).
RUN_OPTIONS = {
    "--type": re.compile(r"all|\d+(,\d+)*"),
    "--missing-only": None,
    "--resume": None,
    "--new-only": None,
    "--years": re.compile(r"\d{4}-\d{4}"),  # main.py checks the order
    "--export": None,
    "--detail-limit": 0,
    "--workers": 1,
    "--detail-delay": 0.0,
    "--page-delay": 0.0,
    "--sessions-per-type": 1,
    "--types-at-once": 1,
    "--detail-backlog": 0,
    "--no-detail-backlog": None,
    "--timeout": 1.0,
    "--verify-ssl": None,
    "--debug": None,
}
DEFAULTS = {"--type": config.AD_TYPE, "--workers": config.WORKERS, "--detail-delay": config.DETAIL_DELAY, "--page-delay": config.PAGE_DELAY,
            "--sessions-per-type": config.SESSIONS_PER_TYPE, "--types-at-once": config.TYPES_AT_ONCE,
            "--detail-backlog": config.DETAIL_BACKLOG, "--timeout": config.TIMEOUT}


def check_args(args) -> list[str]:
    """*args* for main.py, if each is an option the GUI offers with a valid value."""
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise ValueError("args must be a list of strings")
    checked: list[str] = []
    given = iter(args)
    for flag in given:
        if flag not in RUN_OPTIONS or flag in checked:
            raise ValueError(f"unexpected option {flag}")
        rule = RUN_OPTIONS[flag]
        checked.append(flag)
        if rule is None:
            continue
        value = next(given, "")
        try:
            valid = rule.fullmatch(value) if isinstance(rule, re.Pattern) else type(rule)(value) >= rule
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(f"{flag}: not a valid value: {value!r}")
        checked.append(value)
    return checked


class Runs:
    """main.py runs, one at a time: two would share the database and rrk.ir."""

    def __init__(self, db: Path) -> None:
        self.db = db
        self.lock = threading.Lock()
        RUNS.mkdir(exist_ok=True)

    def start(self, args: list[str]) -> dict:
        with self.lock:
            if self.active():
                raise RuntimeError("a run is going already")
            run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
            if (RUNS / f"{run_id}.json").exists():
                raise RuntimeError("a run started this second: start again")
            command = [sys.executable, "-u", "-W", "ignore", "main.py", *args, "--db", str(self.db)]
            logfile = RUNS / f"{run_id}.log"
            # sh keeps main.py's exit code next to its log, even if the GUI is closed meanwhile.
            process = subprocess.Popen(["sh", "-c", '"$@" >"$0" 2>&1; echo $? >"$0.exit"', logfile, *command],
                                       cwd=SCRAPER, stdin=subprocess.DEVNULL, start_new_session=True)
            self._save({"id": run_id, "args": args, "pid": process.pid,
                        "command": shlex.join(["python3", "-W", "ignore", "main.py", *args]),
                        "started": datetime.now().isoformat(timespec="seconds")})
        threading.Thread(target=process.wait, daemon=True).start()  # reaps it: no zombie looks alive
        for _ in range(100):  # until it is sh: just forked, it still looks like this server
            if _alive(process.pid) or process.poll() is not None:
                break
            time.sleep(0.01)
        log.info("run %s started: %s", run_id, " ".join(args))
        return self.get(run_id)

    def stop(self, run_id: str) -> dict | None:
        """Stop at once.  Stored pages stay stored: the database commits per page."""
        if (run := self.get(run_id)) is None or run["state"] != "running":
            return run
        self._save(json.loads((RUNS / f"{run_id}.json").read_text()) | {"stop": True})
        os.killpg(run["pid"], signal.SIGTERM)  # sh and main.py: start_new_session made them a group
        log.info("run %s stopped", run_id)
        return self.get(run_id)

    def get(self, run_id: str, offset: int | None = None) -> dict | None:
        """The run and its state; with an *offset*, also its progress and its
        log from there (-1: the end of it)."""
        if not re.fullmatch(r"\d{8}-\d{6}", run_id) or not (RUNS / f"{run_id}.json").exists():
            return None
        run = json.loads((RUNS / f"{run_id}.json").read_text())
        logfile = RUNS / f"{run_id}.log"
        exit_file = RUNS / f"{run_id}.log.exit"
        if _alive(run["pid"]):
            run["state"] = "stopping" if run.get("stop") else "running"
            if offset is not None:
                run.update(self._progress(run["started"]))
        else:
            if exit_file.exists():
                run["exit"] = int(exit_file.read_text().strip() or -1)
            run["state"] = ("finished" if run.get("exit") == 0 else "failed" if "exit" in run
                            else "stopped" if run.get("stop") else "failed")
            if "ended" not in run:  # first look since it ended: keep what it did
                ended = datetime.fromtimestamp(logfile.stat().st_mtime) if logfile.exists() else datetime.now()
                run.update(self._progress(run["started"]), ended=ended.isoformat(timespec="seconds"))
                self._save({k: v for k, v in run.items() if k != "state"})
        if offset is not None and logfile.exists():
            run["log"], run["offset"] = _read_log(logfile, offset)
        return run

    def list(self) -> list[dict]:
        return [run for path in sorted(RUNS.glob("*.json"), reverse=True)[:100]
                if (run := self.get(path.stem)) is not None]

    def active(self) -> dict | None:
        return next((run for run in self.list() if run["state"] in ("running", "stopping")), None)

    def _progress(self, started: str) -> dict:
        # shortcut: two full scans (~0.2 s for 400k ads) per look; index last_seen/fetched_at if they get slow
        with closing(connect_readonly(self.db)) as conn:
            return {"listed": conn.execute("SELECT count(*) FROM ads WHERE last_seen >= ?", (started,)).fetchone()[0],
                    "details": conn.execute("SELECT count(*) FROM details WHERE fetched_at >= ?",
                                            (started,)).fetchone()[0]}

    @staticmethod
    def _save(run: dict) -> None:
        (RUNS / f"{run['id']}.json").write_text(json.dumps(run, ensure_ascii=False, indent=1))


def _alive(pid: int) -> bool:
    # shortcut: Linux /proc; elsewhere os.kill(pid, 0), minus the check that the pid is still ours
    try:
        return b"main.py" in Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False


def _read_log(path: Path, offset: int, most: int = 1 << 19) -> tuple[str, int]:
    """Whole lines of *path* from byte *offset* (-1: about its last 200 kB), and where they end."""
    with open(path, "rb") as file:
        if offset < 0:
            offset = max(0, file.seek(0, os.SEEK_END) - 200_000)
            file.seek(offset)
            offset += len(file.readline()) if offset else 0  # from a line's start
        file.seek(offset)
        data = file.read(most)
    if (cut := data.rfind(b"\n") + 1) or len(data) < most:  # a line still being written waits
        data = data[:cut]
    return data.decode("utf-8", "replace"), offset + len(data)


def keep_indexing(db: Path, status: dict, warm=None) -> None:
    """Read new full texts into the explorer's database now and every 30 s, and rebuild
    the graph from them when they changed, at most every GRAPH_EVERY s.  A round reads the
    pages stored since the round before; the rounds that may rebuild the graph read them
    all again, for pages fetched on a retry long after their first attempt.  Each round ends
    with *warm*: the overview's counts take seconds, better here than in a page load."""
    repo = AdRepository(connect_explorer(db))
    after, swept = 0, -GRAPH_EVERY
    while True:
        status["busy"] = True
        try:
            if not repo.conn.execute("SELECT 1 FROM notices LIMIT 1").fetchone():  # databases from before it
                with repo.conn:
                    repo.conn.execute("BEGIN")
                    build_notices(repo.conn)
            due = time.monotonic() - swept >= GRAPH_EVERY
            top = repo.conn.execute("SELECT coalesce(max(rowid), 0) FROM details").fetchone()[0]
            if done := index_texts(repo, after=0 if due else after):
                log.info("read %d new ad texts", done)
            after = top
            if due:
                swept = time.monotonic()
                if graph_outdated(repo.conn):
                    build_graph(repo.conn)
                    log.info("graph rebuilt in %.0f s", time.monotonic() - swept)
            if warm:
                warm()
            # The WAL only shrinks when a checkpoint finds no reader: it reached 3.9 GB without this.
            repo.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.OperationalError as exc:  # the scraper held the database too long: next round
            log.warning("reading texts: %s", exc)
        except Exception:  # the thread must live on: the next round may work
            log.exception("reading texts")
        finally:
            status["busy"] = False
        time.sleep(30)


def _keys(value: str | None) -> list[str]:
    return [k for k in (value or "").split(",") if k]


def _page(q: dict) -> dict:
    """The order, grouping and page a list asks for."""
    return {"sort": q.get("sort"), "desc": q.get("dir") != "asc", "group": q.get("group"),
            "offset": int(q.get("offset", 0)), "limit": max(1, min(int(q.get("limit", 50)), 5000))}


ROUTES = {
    ("GET", "/api/overview"): lambda h, q, b: h.explorer.overview(),
    ("GET", "/api/types"): lambda h, q, b: {"types": h.explorer.types()},
    ("GET", "/api/search"): lambda h, q, b: h.explorer.search(q.get("q", ""), ads=q.get("ads") != "0"),
    ("GET", "/api/search/text"): lambda h, q, b: h.explorer.search_text(q.get("q", "")),
    ("GET", "/api/entity"): lambda h, q, b: h.explorer.entity(q.get("key", "")),
    ("GET", "/api/ad"): lambda h, q, b: h.explorer.ad(q.get("code", "")),
    ("GET", "/api/ads"): lambda h, q, b: h.explorer.ads(
        types=_keys(q.get("types")), start=q.get("from"), end=q.get("to"), text=q.get("q"), title=q.get("title"),
        company=q.get("company"), issue=q.get("issue"), office=q.get("office"), events=_keys(q.get("events")),
        every=q.get("every") == "1", named=q.get("named"), who=q.get("who"), role=q.get("role"),
        capital_min=q.get("capital_min"), capital_max=q.get("capital_max"), issues_min=q.get("issues_min"),
        issues_max=q.get("issues_max"), full=q.get("full") == "1", **_page(q)),
    ("GET", "/api/nodes"): lambda h, q, b: h.explorer.nodes(q.get("kind", ""), q, **_page(q)),
    ("GET", "/api/links"): lambda h, q, b: h.explorer.links(q, **_page(q)),
    ("GET", "/api/expand"): lambda h, q, b: h.explorer.expand(q.get("key", ""), _keys(q.get("have")),
                                                              max(1, min(int(q.get("limit", 30)), 400))),
    ("GET", "/api/subgraph"): lambda h, q, b: h.explorer.subgraph(_keys(q.get("keys")), _keys(q.get("have"))),
    ("GET", "/api/common"): lambda h, q, b: h.explorer.common(_keys(q.get("keys")), _keys(q.get("have")),
                                                              service=q.get("service") == "1"),
    ("GET", "/api/path"): lambda h, q, b: h.explorer.path(q.get("from", ""), q.get("to", ""), set(_keys(q.get("avoid"))),
                                                          service=q.get("service") == "1") or {"nodes": [], "edges": []},
    ("GET", "/api/link"): lambda h, q, b: h.explorer.link(q.get("a", ""), q.get("b", "")),
    ("GET", "/api/leads"): lambda h, q, b: h.explorer.leads(q.get("from"), q.get("to"), q.get("current") == "1",
                                                            q.get("q")),
    ("GET", "/api/status"): lambda h, q, b: {"index": dict(h.explorer.index_status(), **h.indexing),
                                             "run": h.runs.active()},
    ("GET", "/api/runs"): lambda h, q, b: {"runs": h.runs.list(), "defaults": DEFAULTS},
    ("GET", "/api/run"): lambda h, q, b: h.runs.get(q.get("id", ""), int(q.get("offset", -1))),
    ("POST", "/api/runs"): lambda h, q, b: h.runs.start(check_args(b.get("args"))),
    ("POST", "/api/run/stop"): lambda h, q, b: h.runs.stop(str(b.get("id", ""))),
}


class Handler(BaseHTTPRequestHandler):
    explorer: Explorer
    runs: Runs
    indexing: dict
    hosts: set[str]

    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def _handle(self) -> None:
        # Another site's page can reach 127.0.0.1 too: refuse foreign Host names
        # (DNS rebinding), and POSTs that aren't JSON (a cross-site form can't send it).
        if self.headers.get("Host") not in self.hosts:
            return self._send(403, {"error": "unknown host"})
        if self.command == "POST" and self.headers.get("Content-Type") != "application/json":
            return self._send(403, {"error": "JSON only"})
        url = urlsplit(self.path)
        route = ROUTES.get((self.command, url.path))
        if route is None:
            return self._static(url.path) if self.command == "GET" else self._send(404, {"error": "not found"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}") \
                if self.command == "POST" else {}
            result = route(self, {k: v[-1] for k, v in parse_qs(url.query).items()}, body)
        except ValueError as exc:
            return self._send(400, {"error": str(exc)})
        except RuntimeError as exc:
            return self._send(409, {"error": str(exc)})
        except Exception as exc:
            log.exception("%s %s", self.command, self.path)
            return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})
        if result is None:
            return self._send(404, {"error": "not found"})
        self._send(200, result)

    def _static(self, path: str) -> None:
        file = (STATIC / (path.lstrip("/") or "index.html")).resolve()
        if not file.is_relative_to(STATIC) or not file.is_file():
            file = STATIC / "index.html"  # the app routes the rest itself
        self._send(200, file.read_bytes(), mimetypes.guess_type(file.name)[0] or "application/octet-stream")

    def _send(self, status: int, body, content_type: str = "application/json; charset=utf-8") -> None:
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args) -> None:  # the UI polls: keep the terminal quiet
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Local GUI for the rrk.ir scraper and its data.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", type=Path, default=config.DB_PATH, help=f"default {config.DB_PATH}")
    args = parser.parse_args()
    if sqlite3.sqlite_version_info < (3, 43):
        raise SystemExit(f"the explorer's text search needs SQLite 3.43 or newer; this Python has {sqlite3.sqlite_version}")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)-8s %(message)s",
                        datefmt="%H:%M:%S")
    db = args.db.resolve()
    if not db.exists():  # the explorer only reads it
        connect(db).close()
    Handler.explorer, Handler.runs, Handler.indexing = Explorer(db), Runs(db), {"busy": False}
    Handler.hosts = {f"{host}:{args.port}" for host in ("127.0.0.1", "localhost")}
    threading.Thread(target=keep_indexing, args=(db, Handler.indexing, Handler.explorer.overview), daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"rrk explorer: http://127.0.0.1:{args.port}/  (Ctrl+C closes the GUI; a running scrape goes on)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
