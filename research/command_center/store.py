"""Append-only SQLite (WAL) event store."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from .schemas import ValidationError, validate_event

DDL = """
CREATE TABLE IF NOT EXISTS events(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT UNIQUE NOT NULL,
  event_type TEXT NOT NULL,
  timestamp_utc TEXT NOT NULL,
  run_id TEXT NOT NULL,
  agent_id TEXT,
  parent_task_id TEXT,
  correlation_id TEXT,
  source TEXT NOT NULL,
  status TEXT,
  payload_version INTEGER NOT NULL,
  payload JSON NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_run ON events(run_id, seq);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;
"""
COLS = "seq,event_id,event_type,timestamp_utc,run_id,agent_id,parent_task_id,correlation_id,source,status,payload_version,payload"


def _row(r) -> dict:
    d = dict(r)
    d["payload"] = json.loads(d["payload"])
    return d


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=15, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(DDL)

    def close(self):
        with self._lock:
            self._db.close()

    def insert(self, raws: list) -> dict:
        """Validate and insert; duplicates (by event_id) are ignored and reported."""
        res = {"accepted": [], "duplicates": [], "rejected": []}
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                for i, raw in enumerate(raws):
                    try:
                        e = validate_event(raw)
                    except ValidationError as ex:
                        res["rejected"].append({"index": i, "error": str(ex)})
                        continue
                    if self._db.execute("SELECT 1 FROM events WHERE event_id=?", (e["event_id"],)).fetchone():
                        res["duplicates"].append(e["event_id"])  # checked first so duplicates never burn a seq number
                        continue
                    cur = self._db.execute(
                        "INSERT OR IGNORE INTO events(event_id,event_type,timestamp_utc,run_id,agent_id,parent_task_id,"
                        "correlation_id,source,status,payload_version,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (e["event_id"], e["event_type"], e["timestamp_utc"], e["run_id"], e["agent_id"], e["parent_task_id"],
                         e["correlation_id"], e["source"], e["status"], e["payload_version"],
                         json.dumps(e["payload"], separators=(",", ":"))))
                    if cur.rowcount:
                        res["accepted"].append({"event_id": e["event_id"], "seq": cur.lastrowid})
                    else:
                        res["duplicates"].append(e["event_id"])
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise
        return res

    def events(self, after_seq=0, limit=500, run_id=None, upto_seq=None) -> list[dict]:
        q, a = f"SELECT {COLS} FROM events WHERE seq>?", [after_seq]
        if run_id:
            q += " AND run_id=?"; a.append(run_id)
        if upto_seq is not None:
            q += " AND seq<=?"; a.append(upto_seq)
        q += " ORDER BY seq LIMIT ?"; a.append(max(1, min(int(limit), 5000)))
        with self._lock:
            return [_row(r) for r in self._db.execute(q, a)]

    def max_seq(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]

    def latest_run(self):
        with self._lock:
            r = self._db.execute("SELECT run_id FROM events ORDER BY seq DESC LIMIT 1").fetchone()
            return r[0] if r else None

    def runs(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT run_id, COUNT(*) n, MIN(timestamp_utc) first_ts, MAX(timestamp_utc) last_ts, MAX(seq) last_seq, "
                "GROUP_CONCAT(DISTINCT source) sources FROM events GROUP BY run_id ORDER BY last_seq DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r); d["sources"] = sorted((d["sources"] or "").split(",")); d["has_demo"] = "demo" in d["sources"]
            out.append(d)
        return out

    def search(self, q: str, limit=100, run_id=None) -> list[dict]:
        esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{esc}%"
        sql = (f"SELECT {COLS} FROM events WHERE (payload LIKE ? ESCAPE '\\' OR event_type LIKE ? ESCAPE '\\' "
               "OR agent_id LIKE ? ESCAPE '\\' OR event_id LIKE ? ESCAPE '\\' OR COALESCE(parent_task_id,'') LIKE ? ESCAPE '\\')")
        a = [like] * 5
        if run_id:
            sql += " AND run_id=?"; a.append(run_id)
        sql += " ORDER BY seq DESC LIMIT ?"; a.append(max(1, min(int(limit), 500)))
        with self._lock:
            return [_row(r) for r in self._db.execute(sql, a)]

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
