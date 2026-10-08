"""Research SQLite store (jobs, audit, strategy state, import registry) and a one-worker job queue.

File: research.sqlite3 next to the events DB (see store.py). Stdlib only. No broker/trading imports.
Subprocesses are always started from an argv LIST (never a shell). Progress is parsed from lines the pipeline process
really printed ('##PROGRESS {json}' on stderr); nothing is estimated or invented.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

TAIL_CHARS = 4000
PROGRESS_PREFIX = "##PROGRESS "
DEFAULT_TIMEOUT_S = 900

DDL = """
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, params TEXT NOT NULL, argv TEXT NOT NULL,
  out_dir TEXT, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, exit_code INTEGER,
  progress_done INTEGER, progress_total INTEGER, progress_step TEXT, steps_finished TEXT NOT NULL DEFAULT '[]',
  stdout_tail TEXT NOT NULL DEFAULT '', stderr_tail TEXT NOT NULL DEFAULT '', error TEXT);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, kind TEXT NOT NULL, subject TEXT, outcome TEXT NOT NULL,
  detail TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
CREATE TABLE IF NOT EXISTS strategy_state(
  id INTEGER PRIMARY KEY AUTOINCREMENT, strategy_id TEXT NOT NULL, ts TEXT NOT NULL, from_state TEXT NOT NULL,
  to_state TEXT NOT NULL, reason TEXT NOT NULL);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ResearchDB:
    """Thin thread-safe wrapper around one SQLite connection (WAL)."""

    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=15, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(DDL)

    def script(self, ddl: str) -> None:
        with self.lock:
            self.db.executescript(ddl)

    def run(self, sql: str, args=()):
        with self.lock:
            return self.db.execute(sql, args)

    def all(self, sql: str, args=()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def one(self, sql: str, args=()):
        with self.lock:
            r = self.db.execute(sql, args).fetchone()
            return dict(r) if r else None

    def audit(self, kind: str, subject: str | None, outcome: str, detail: dict) -> int:
        with self.lock:
            cur = self.db.execute("INSERT INTO audit(ts,kind,subject,outcome,detail) VALUES(?,?,?,?,?)",
                                  (utc_now(), kind, subject, outcome, json.dumps(detail, sort_keys=True)))
            return cur.lastrowid


# ------------------------------------------------------------------ redaction
_PATH_RE = re.compile(r"(?<![\w.:/-])/(?:[\w.\-+@~=]+/)*[\w.\-+@~=]+")


def redact(text: str, root: Path) -> str:
    """Make repo-internal absolute paths relative and replace every other absolute path with '<path>'."""
    if not text:
        return ""
    root_s = str(Path(root).resolve())
    text = text.replace(root_s + os.sep, "").replace(root_s, ".")

    def sub(m):
        return "<path>"
    return _PATH_RE.sub(sub, text)


def _tail(s: str) -> str:
    return s[-TAIL_CHARS:]


# ------------------------------------------------------------------ default runner
def subprocess_runner(argv: list[str], cwd: Path, on_progress: Callable[[dict], None], on_output: Callable[[str, str], None],
                      timeout_s: int = DEFAULT_TIMEOUT_S) -> int:
    """Run argv (list) without a shell. Streams stderr lines for '##PROGRESS' records. Returns the exit code
    (-9 when killed for exceeding the timeout)."""
    env = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT", "TMPDIR") if k in os.environ}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.Popen(argv, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                            env=env, text=True, encoding="utf-8", errors="replace")
    out_buf, err_buf = deque(maxlen=200), deque(maxlen=200)

    def pump_out():
        for line in proc.stdout:
            out_buf.append(line)
            on_output("stdout", "".join(out_buf))

    def pump_err():
        for line in proc.stderr:
            if line.startswith(PROGRESS_PREFIX):
                try:
                    on_progress(json.loads(line[len(PROGRESS_PREFIX):]))
                except ValueError:
                    pass
                continue
            err_buf.append(line)
            on_output("stderr", "".join(err_buf))

    threads = [threading.Thread(target=pump_out, daemon=True), threading.Thread(target=pump_err, daemon=True)]
    for t in threads:
        t.start()
    try:
        rc = proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        rc = -9
        err_buf.append(f"killed: exceeded {timeout_s}s timeout\n")
        on_output("stderr", "".join(err_buf))
    for t in threads:
        t.join(timeout=5)
    return rc


class JobQueue:
    """FIFO queue persisted in research.sqlite3, executed by exactly ONE worker thread."""

    def __init__(self, rdb: ResearchDB, cwd: Path, runner=subprocess_runner, timeout_s: int = DEFAULT_TIMEOUT_S):
        self.rdb, self.cwd, self.runner, self.timeout_s = rdb, Path(cwd), runner, timeout_s
        self._cv = threading.Condition()
        self._thread: threading.Thread | None = None
        self._stop = False
        # a job left 'running' by a previous process cannot be resumed: say so instead of pretending
        rdb.run("UPDATE jobs SET status='failed', finished_at=?, error=? WHERE status='running'",
                (utc_now(), "interrupted: the server process stopped while this job was running"))
        if rdb.one("SELECT 1 AS x FROM jobs WHERE status='queued' LIMIT 1"):
            self._ensure_worker()

    # ---- submission / reads
    def submit(self, kind: str, params: dict, argv: list[str], out_dir: str | None, job_id: str | None = None) -> dict:
        if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
            raise ValueError("argv must be a list of strings")
        jid = job_id or f"exp_{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}_{uuid.uuid4().hex[:8]}"
        self.rdb.run("INSERT INTO jobs(id,kind,status,params,argv,out_dir,created_at) VALUES(?,?,?,?,?,?,?)",
                     (jid, kind, "queued", json.dumps(params, sort_keys=True), json.dumps(argv), out_dir, utc_now()))
        self._ensure_worker()
        with self._cv:
            self._cv.notify_all()
        return self.get(jid)

    def _public(self, r: dict) -> dict:
        done, total = r["progress_done"], r["progress_total"]
        prog = None
        if total:
            prog = {"steps_done": done, "steps_total": total, "current_or_last_step": r["progress_step"],
                    "steps_finished": json.loads(r["steps_finished"]),
                    "basis": "steps the pipeline process reported as finished; not a time estimate"}
        return {"id": r["id"], "kind": r["kind"], "status": r["status"], "params": json.loads(r["params"]),
                "created_at": r["created_at"], "started_at": r["started_at"], "finished_at": r["finished_at"],
                "exit_code": r["exit_code"], "progress": prog,
                "stdout_tail": redact(r["stdout_tail"], self.cwd), "stderr_tail": redact(r["stderr_tail"], self.cwd),
                "error": redact(r["error"] or "", self.cwd) or None,
                "out_dir": redact(r["out_dir"] or "", self.cwd) or None}

    def get(self, job_id: str) -> dict | None:
        r = self.rdb.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        return self._public(r) if r else None

    def out_dir_abs(self, job_id: str) -> Path | None:
        r = self.rdb.one("SELECT out_dir FROM jobs WHERE id=?", (job_id,))
        return Path(r["out_dir"]) if r and r["out_dir"] else None

    def list(self, limit: int = 100) -> list[dict]:
        rows = self.rdb.all("SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?", (max(1, min(limit, 500)),))
        out = []
        for r in rows:
            p = self._public(r)
            p["stdout_tail"] = p["stderr_tail"] = None  # tails only on the detail endpoint
            out.append(p)
        return out

    # ---- worker
    def _ensure_worker(self) -> None:
        with self._cv:
            if self._thread and self._thread.is_alive():
                return
            self._stop = False
            self._thread = threading.Thread(target=self._loop, name="research-job-worker", daemon=True)
            self._thread.start()

    def shutdown(self, wait: float = 2.0) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        if self._thread:
            self._thread.join(timeout=wait)

    def _next(self):
        return self.rdb.one("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at, rowid LIMIT 1")

    def _loop(self) -> None:
        while True:
            with self._cv:
                if self._stop:
                    return
            r = self._next()
            if r is None:
                with self._cv:
                    if self._stop:
                        return
                    self._cv.wait(timeout=1.0)
                continue
            self._execute(r)

    def _execute(self, r: dict) -> None:
        jid = r["id"]
        argv = json.loads(r["argv"])
        self.rdb.run("UPDATE jobs SET status='running', started_at=? WHERE id=?", (utc_now(), jid))
        finished: list[str] = []

        def on_progress(p: dict) -> None:
            if not isinstance(p, dict) or not isinstance(p.get("done"), int) or not isinstance(p.get("total"), int):
                return
            finished.append(str(p.get("step", ""))[:200])
            self.rdb.run("UPDATE jobs SET progress_done=?, progress_total=?, progress_step=?, steps_finished=? WHERE id=?",
                         (p["done"], p["total"], finished[-1], json.dumps(finished), jid))

        def on_output(which: str, text: str) -> None:
            col = "stdout_tail" if which == "stdout" else "stderr_tail"
            self.rdb.run(f"UPDATE jobs SET {col}=? WHERE id=?", (_tail(text), jid))

        try:
            rc = self.runner(argv, self.cwd, on_progress, on_output, self.timeout_s)
            err = None if rc == 0 else f"process exited with code {rc}"
            status = "completed" if rc == 0 else "failed"
        except Exception as e:  # runner could not start the process
            rc, status, err = None, "failed", f"could not run: {type(e).__name__}: {e}"
        self.rdb.run("UPDATE jobs SET status=?, finished_at=?, exit_code=?, error=? WHERE id=?",
                     (status, utc_now(), rc, err, jid))
