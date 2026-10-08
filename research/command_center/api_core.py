"""Core shell API (SHELL worker): prefs, instruments, connections, audit.

Routes register through router.py. No broker/trading imports; nothing here can place an order.
 GET  /api/prefs[?key=k]       server-persisted UI preferences (no token needed to read; loopback only)
 POST /api/prefs               token required. {"prefs": {key: json, ...}} and/or {"delete": [key, ...]}
 GET  /api/instruments?q=&limit=   instruments with local daily-bar data, from data/raw/ibkr_connector/MANIFEST.json
 GET  /api/connections         honest broker / market-data / event-stream state (no secrets, db path redacted)
 GET  /api/audit?limit=&before_seq=    list audit.config events
 POST /api/audit               token required. Records an 'audit.config' event in the event store.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from . import router
from .schemas import ValidationError, utc_now_iso

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
KEY_RE = re.compile(r"^[a-z0-9_.-]{1,64}$")
MAX_VALUE_BYTES = 64 * 1024
MAX_PREF_KEYS = 1000
US_EXCHANGES = {"ARCA", "NASDAQ", "NYSE", "AMEX", "BATS", "IEX", "ISLAND", "NYSEARCA"}
Q_RE = re.compile(r"^[A-Za-z0-9 .\-_]{0,32}$")
BROKER_REASON = "not_connected: no broker adapter implemented"

_prefs_lock = threading.Lock()


# --------------------------------------------------------------------------- prefs
class PrefsStore:
    """Tiny SQLite key/value file next to the events DB (separate file: events DB stays append-only)."""

    def __init__(self, path):
        self.path = str(path)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=15, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("CREATE TABLE IF NOT EXISTS prefs(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_utc TEXT NOT NULL)")

    def all(self) -> dict:
        with self._lock:
            rows = self._db.execute("SELECT key, value, updated_utc FROM prefs ORDER BY key").fetchall()
        return {k: {"value": json.loads(v), "updated_utc": u} for k, v, u in rows}

    def get(self, key):
        with self._lock:
            r = self._db.execute("SELECT value, updated_utc FROM prefs WHERE key=?", (key,)).fetchone()
        return None if r is None else {"value": json.loads(r[0]), "updated_utc": r[1]}

    def apply(self, sets: dict, deletes: list) -> None:
        now = utc_now_iso()
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                for k, v in sets.items():
                    self._db.execute("INSERT INTO prefs(key,value,updated_utc) VALUES(?,?,?) "
                                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_utc=excluded.updated_utc",
                                     (k, json.dumps(v, separators=(",", ":")), now))
                for k in deletes:
                    self._db.execute("DELETE FROM prefs WHERE key=?", (k,))
                n = self._db.execute("SELECT COUNT(*) FROM prefs").fetchone()[0]
                if n > MAX_PREF_KEYS:
                    raise ValidationError(f"too many preference keys (max {MAX_PREF_KEYS})")
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise


def prefs_store(server) -> PrefsStore:
    ps = getattr(server, "_prefs_store", None)
    if ps is None:
        with _prefs_lock:
            ps = getattr(server, "_prefs_store", None)
            if ps is None:
                d = Path(server.store.path).resolve().parent
                ps = server._prefs_store = PrefsStore(d / "prefs.sqlite3")
    return ps


def _check_key(k):
    if not isinstance(k, str) or not KEY_RE.match(k):
        raise ValidationError(f"invalid preference key {str(k)[:40]!r}: must match ^[a-z0-9_.-]{{1,64}}$")


def _check_value(k, v):
    try:
        blob = json.dumps(v, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise ValidationError(f"value for {k!r} is not valid JSON") from None
    if len(blob.encode()) > MAX_VALUE_BYTES:
        raise ValidationError(f"value for {k!r} too large (max {MAX_VALUE_BYTES // 1024} KB)")


@router.route("GET", r"/api/prefs")
def get_prefs(h, m, q, body):
    ps = prefs_store(h.server)
    if q.get("key"):
        _check_key(q["key"])
        r = ps.get(q["key"])
        if r is None:
            return h._err(404, "no such preference", key=q["key"])
        return h._json(200, {"ok": True, "key": q["key"], **r})
    allp = ps.all()
    h._json(200, {"ok": True, "prefs": {k: v["value"] for k, v in allp.items()},
                  "updated": {k: v["updated_utc"] for k, v in allp.items()}})


@router.route("POST", r"/api/prefs")
def post_prefs(h, m, q, body):
    if not isinstance(body, dict):
        raise ValidationError("body must be an object")
    sets = body.get("prefs", {})
    if "key" in body:  # single-key convenience form
        sets = {**(sets if isinstance(sets, dict) else {}), body["key"]: body.get("value")}
    deletes = body.get("delete", [])
    if not isinstance(sets, dict) or not isinstance(deletes, list):
        raise ValidationError("prefs must be an object and delete a list")
    if not sets and not deletes:
        raise ValidationError("nothing to do: send prefs and/or delete")
    if len(sets) + len(deletes) > 200:
        raise ValidationError("too many keys in one request (max 200)")
    for k, v in sets.items():
        _check_key(k)
        _check_value(k, v)
    for k in deletes:
        _check_key(k)
    prefs_store(h.server).apply(sets, deletes)
    h._json(200, {"ok": True, "saved": sorted(sets), "deleted": sorted(deletes)})


# --------------------------------------------------------------------------- manifest / instruments
def _manifest_path(server) -> Path:
    return Path(getattr(server, "manifest_path", None) or os.environ.get("CC_MANIFEST")
                or ROOT / "data" / "raw" / "ibkr_connector" / "MANIFEST.json")


def _load_manifest(server):
    """Returns (entries, reason). Never raises; reason explains an empty result."""
    p = _manifest_path(server)
    if not p.is_file():
        return [], "manifest not found (no IBKR connector daily-bar files have been retrieved)"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], "manifest unreadable or not valid JSON"
    if isinstance(data, dict):
        data = data.get("instruments") or data.get("files") or []
    if not isinstance(data, list):
        return [], "manifest has an unexpected shape"
    return [e for e in data if isinstance(e, dict) and isinstance(e.get("symbol"), str)], None


def _instrument(e: dict) -> dict:
    ex = e.get("exchange") if isinstance(e.get("exchange"), str) else None
    cid = e.get("contract_id") if isinstance(e.get("contract_id"), int) and not isinstance(e.get("contract_id"), bool) else None
    name = next((e[k].strip() for k in ("name", "company_name", "long_name") if isinstance(e.get(k), str) and e[k].strip()), None)
    cur, basis = None, None
    if isinstance(e.get("currency"), str) and e["currency"]:
        cur, basis = e["currency"], "manifest"
    elif ex in US_EXCHANGES:
        cur, basis = "USD", "inferred from US primary exchange (not stated in manifest)"
    return {
        "key": f"{cid}:{ex}" if cid is not None else None,
        "symbol": e["symbol"], "con_id": cid, "exchange": ex, "currency": cur, "currency_basis": basis,
        "name": name,  # null unless the manifest carries one; never invented
        "data": {"n_bars": e.get("n_bars"), "first": e.get("first"), "last": e.get("last"), "interval": "1d",
                 "retrieved_on": e.get("retrieved_on"), "delayed_seconds": e.get("delayed_seconds"),
                 "source_tag": e.get("source_tag"), "adjusted_for_dividends": e.get("adjusted_for_dividends")},
    }


@router.route("GET", r"/api/instruments")
def get_instruments(h, m, q, body):
    qq = q.get("q", "")
    if not Q_RE.match(qq):
        raise ValidationError("q may contain letters, digits, space . - _ (max 32 chars)")
    try:
        limit = int(q.get("limit", 50))
    except ValueError:
        raise ValidationError("limit must be an integer") from None
    limit = max(1, min(limit, 200))
    entries, reason = _load_manifest(h.server)
    items = [_instrument(e) for e in entries]
    needle = qq.strip().lower()
    if needle:
        def score(i):
            s = i["symbol"].lower()
            n = (i["name"] or "").lower()
            if s == needle:
                return 0
            if s.startswith(needle):
                return 1
            if needle in s or (n and needle in n):
                return 2
            return None
        scored = [(score(i), i) for i in items]
        items = [i for sc, i in sorted((x for x in scored if x[0] is not None), key=lambda x: (x[0], x[1]["symbol"]))]
    else:
        items.sort(key=lambda i: i["symbol"])
    h._json(200, {"ok": True, "q": qq, "available": reason is None, "reason": reason, "total": len(items),
                  "source": "IBKR connector MANIFEST.json (symbols with local daily bars only; not a full symbol directory)",
                  "instruments": items[:limit]})


# --------------------------------------------------------------------------- connections
def _age_days(iso_date):
    try:
        d = date.fromisoformat(str(iso_date)[:10])
    except ValueError:
        return None
    return (datetime.now(timezone.utc).date() - d).days


@router.route("GET", r"/api/connections")
def get_connections(h, m, q, body):
    entries, reason = _load_manifest(h.server)
    st = h.server.store
    retrieved = sorted({str(e["retrieved_on"]) for e in entries if e.get("retrieved_on")})
    delays = sorted({e["delayed_seconds"] for e in entries if isinstance(e.get("delayed_seconds"), (int, float))})
    lasts = sorted(str(e["last"]) for e in entries if e.get("last"))
    last_ret = retrieved[-1] if retrieved else None
    age = _age_days(last_ret) if last_ret else None
    if not entries:
        md_state = "unavailable"
    elif age is None or age > 5:
        md_state = "stale"
    else:
        md_state = "historical"  # daily bars only: never 'realtime'
    broker = {"state": "not_connected", "reason": BROKER_REASON, "adapter": None, "execution": "none",
              "detail": "Execution: none — broker adapter not built. Read-only IBKR account data is authorised but is not wired into this app.",
              "setup_complete": False}
    md = {"state": md_state, "source": "IBKR connector daily-bar files", "interval": "1d", "instruments": len(entries),
          "last_retrieved_on": last_ret, "age_days": age,
          "delayed_seconds": delays[-1] if len(delays) == 1 else (delays or None),
          "last_bar": lasts[-1] if lasts else None, "reason": reason,
          "note": "Daily bars only; no intraday or streaming quotes are available."}
    es = {"state": "available", "max_seq": st.max_seq(), "server_time": utc_now_iso(), "runtime_attached": False,
          "transport": "SSE /api/stream with Last-Event-ID replay"}
    prefs_db = Path(st.path).resolve().parent / "prefs.sqlite3"
    h._json(200, {"ok": True, "broker": broker, "market_data": md, "event_stream": es,
                  "storage": {"events_db": "[redacted]", "prefs_db": "[redacted]", "prefs_db_exists": prefs_db.is_file()},
                  "server_time": utc_now_iso()})


# --------------------------------------------------------------------------- audit
@router.route("POST", r"/api/audit")
def post_audit(h, m, q, body):
    if not isinstance(body, dict):
        raise ValidationError("body must be an object")
    allowed = {"area", "action", "target", "old", "new", "outcome", "reason", "detail", "run_id"}
    extra = set(body) - allowed
    if extra:
        raise ValidationError(f"unknown fields: {sorted(extra)}")
    payload = {k: body[k] for k in allowed - {"run_id"} if k in body and body[k] is not None}
    run_id = body.get("run_id") or h.server.store.latest_run() or "live"
    ev = {"event_id": uuid.uuid4().hex, "event_type": "audit.config", "run_id": run_id, "source": "ui", "payload": payload}
    res = h.server.store.insert([ev])
    if res["rejected"]:
        return h._err(400, res["rejected"][0]["error"])
    a = res["accepted"][0]
    h._json(200, {"ok": True, "event_id": a["event_id"], "seq": a["seq"]})


@router.route("GET", r"/api/audit")
def get_audit(h, m, q, body):
    try:
        limit = max(1, min(int(q.get("limit", 100)), 500))
        before = int(q["before_seq"]) if q.get("before_seq") else None
    except ValueError:
        raise ValidationError("limit and before_seq must be integers") from None
    st = h.server.store
    sql = ("SELECT seq,event_id,timestamp_utc,run_id,source,payload FROM events WHERE event_type='audit.config'"
           + (" AND seq<?" if before else "") + " ORDER BY seq DESC LIMIT ?")
    args = ([before] if before else []) + [limit]
    with st._lock:
        rows = st._db.execute(sql, args).fetchall()
    items = [{"seq": r[0], "event_id": r[1], "timestamp_utc": r[2], "run_id": r[3], "source": r[4], **json.loads(r[5])} for r in rows]
    h._json(200, {"ok": True, "items": items, "next_before_seq": items[-1]["seq"] if len(items) == limit else None})


@router.route("GET", r"/api/auth/check")
def auth_check(h, m, q, body):
    """Reports whether the X-CC-Token header sent with this request is valid (no side effects)."""
    supplied = bool(h.headers.get("X-CC-Token"))
    return h._json(200, {"ok": True, "token_supplied": supplied, "token_valid": bool(supplied and h._token_ok())})
