"""SQLite persistence for market workspace state (watchlists + local audit). Stdlib only; no broker imports.
File: market.sqlite3 next to the events DB. A fresh connection is opened per call so state survives server restarts."""
from __future__ import annotations
import json
import re
import sqlite3
import time
import uuid
from pathlib import Path

MAX_LISTS, MAX_ITEMS = 50, 100
_NAME_RE = re.compile(r"^[^\x00-\x1f\x7f<>]{1,60}$")
SEED_ID = "research-data-3-etfs"
SEED_NAME = "Research data (3 ETFs)"
SEED_NOTE = ("Instruments with data available in this app. Not recommendations, not a screen, not a ranking.")

SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlists(id TEXT PRIMARY KEY, name TEXT NOT NULL, note TEXT, position INTEGER NOT NULL, created_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS watchlist_items(list_id TEXT NOT NULL, conid INTEGER NOT NULL, exchange TEXT NOT NULL, symbol TEXT NOT NULL,
  position INTEGER NOT NULL, PRIMARY KEY(list_id, conid, exchange));
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, ts_utc TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL);
"""


class StoreError(ValueError):
    def __init__(self, msg, code=400):
        super().__init__(msg)
        self.code = code


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def db_path_for(events_store_path) -> Path:
    return Path(events_store_path).parent / "market.sqlite3"


class MarketStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._c() as c:
            c.executescript(SCHEMA)

    def _c(self):
        c = sqlite3.connect(str(self.path), timeout=15)
        c.row_factory = sqlite3.Row
        return _Ctx(c)

    # ---- seed
    def seed_if_needed(self, instruments: list[dict]):
        """Seed ONE list from the manifest, once (deleting it later does not re-create it)."""
        if not instruments:
            return
        with self._c() as c:
            if c.execute("SELECT 1 FROM meta WHERE k='seeded'").fetchone():
                return
            c.execute("INSERT INTO watchlists VALUES(?,?,?,?,?)", (SEED_ID, SEED_NAME, SEED_NOTE, 0, _now()))
            for i, ins in enumerate(instruments):
                c.execute("INSERT INTO watchlist_items VALUES(?,?,?,?,?)", (SEED_ID, ins["conid"], ins["exchange"], ins["symbol"], i))
            c.execute("INSERT INTO meta VALUES('seeded','1')")
            c.execute("INSERT INTO audit(ts_utc,action,detail) VALUES(?,?,?)", (_now(), "watchlist.seed", json.dumps({"id": SEED_ID})))

    # ---- reads
    def lists(self) -> list[dict]:
        with self._c() as c:
            out = []
            for r in c.execute("SELECT * FROM watchlists ORDER BY position, created_utc"):
                items = [{"symbol": i["symbol"], "conid": i["conid"], "exchange": i["exchange"]} for i in c.execute(
                    "SELECT * FROM watchlist_items WHERE list_id=? ORDER BY position", (r["id"],))]
                out.append({"id": r["id"], "name": r["name"], "note": r["note"], "created_utc": r["created_utc"], "items": items})
            return out

    def audit_rows(self, limit=100) -> list[dict]:
        with self._c() as c:
            return [dict(r) for r in c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))]

    # ---- writes (return audit detail dict)
    @staticmethod
    def _name(name):
        if not isinstance(name, str) or not _NAME_RE.match(name.strip()):
            raise StoreError("name must be 1-60 characters, no control characters or <>")
        return name.strip()

    def _unique(self, c, name, exclude=None):
        for r in c.execute("SELECT id FROM watchlists WHERE lower(name)=lower(?)", (name,)):
            if r["id"] != exclude:
                raise StoreError("a watchlist with that name already exists", 409)

    def create(self, name, note=None) -> dict:
        name = self._name(name)
        with self._c() as c:
            if c.execute("SELECT COUNT(*) FROM watchlists").fetchone()[0] >= MAX_LISTS:
                raise StoreError(f"at most {MAX_LISTS} watchlists")
            self._unique(c, name)
            wid = uuid.uuid4().hex[:12]
            pos = c.execute("SELECT COALESCE(MAX(position),-1)+1 FROM watchlists").fetchone()[0]
            c.execute("INSERT INTO watchlists VALUES(?,?,?,?,?)", (wid, name, None, pos, _now()))
            return {"action": "create", "id": wid, "name": name}

    def rename(self, wid, name) -> dict:
        name = self._name(name)
        with self._c() as c:
            self._need(c, wid)
            self._unique(c, name, exclude=wid)
            c.execute("UPDATE watchlists SET name=? WHERE id=?", (name, wid))
            return {"action": "rename", "id": wid, "name": name}

    def delete(self, wid) -> dict:
        with self._c() as c:
            self._need(c, wid)
            c.execute("DELETE FROM watchlist_items WHERE list_id=?", (wid,))
            c.execute("DELETE FROM watchlists WHERE id=?", (wid,))
            return {"action": "delete", "id": wid}

    @staticmethod
    def _need(c, wid):
        if not isinstance(wid, str) or not c.execute("SELECT 1 FROM watchlists WHERE id=?", (wid,)).fetchone():
            raise StoreError("watchlist not found", 404)

    def add_item(self, wid, ins: dict) -> dict:
        with self._c() as c:
            self._need(c, wid)
            n = c.execute("SELECT COUNT(*) FROM watchlist_items WHERE list_id=?", (wid,)).fetchone()[0]
            if n >= MAX_ITEMS:
                raise StoreError(f"at most {MAX_ITEMS} items per watchlist")
            if c.execute("SELECT 1 FROM watchlist_items WHERE list_id=? AND conid=? AND exchange=?", (wid, ins["conid"], ins["exchange"])).fetchone():
                raise StoreError("instrument already in this watchlist", 409)
            c.execute("INSERT INTO watchlist_items VALUES(?,?,?,?,?)", (wid, ins["conid"], ins["exchange"], ins["symbol"], n))
            return {"action": "add", "id": wid, "conid": ins["conid"], "exchange": ins["exchange"]}

    def remove_item(self, wid, conid, exchange) -> dict:
        with self._c() as c:
            self._need(c, wid)
            cur = c.execute("DELETE FROM watchlist_items WHERE list_id=? AND conid=? AND exchange=?", (wid, conid, exchange))
            if cur.rowcount == 0:
                raise StoreError("instrument not in this watchlist", 404)
            for i, r in enumerate(c.execute("SELECT conid, exchange FROM watchlist_items WHERE list_id=? ORDER BY position", (wid,)).fetchall()):
                c.execute("UPDATE watchlist_items SET position=? WHERE list_id=? AND conid=? AND exchange=?", (i, wid, r["conid"], r["exchange"]))
            return {"action": "remove", "id": wid, "conid": conid, "exchange": exchange}

    def reorder(self, wid, order: list[tuple[int, str]]) -> dict:
        with self._c() as c:
            self._need(c, wid)
            cur = {(r["conid"], r["exchange"]) for r in c.execute("SELECT conid, exchange FROM watchlist_items WHERE list_id=?", (wid,))}
            if len(set(order)) != len(order) or set(order) != cur:
                raise StoreError("order must list every current item exactly once")
            for i, (cid, ex) in enumerate(order):
                c.execute("UPDATE watchlist_items SET position=? WHERE list_id=? AND conid=? AND exchange=?", (i, wid, cid, ex))
            return {"action": "reorder", "id": wid, "n": len(order)}

    def record_audit(self, action: str, detail: dict):
        with self._c() as c:
            c.execute("INSERT INTO audit(ts_utc,action,detail) VALUES(?,?,?)", (_now(), action, json.dumps(detail)))


class _Ctx:
    def __init__(self, c):
        self.c = c

    def __enter__(self):
        return self.c

    def __exit__(self, et, ev, tb):
        try:
            if et is None:
                self.c.commit()
            else:
                self.c.rollback()
        finally:
            self.c.close()
        return False
