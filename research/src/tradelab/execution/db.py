"""SQLite (WAL) ledger. Everything the service must remember across a crash lives here; money is stored as TEXT Decimal."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS intents(
  intent_id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL, strategy_version TEXT NOT NULL, signal_id TEXT NOT NULL,
  con_id INTEGER NOT NULL, symbol TEXT, side INTEGER NOT NULL, qty TEXT NOT NULL, order_type TEXT NOT NULL,
  limit_px TEXT, stop_px TEXT, tif TEXT NOT NULL, outside_rth INTEGER NOT NULL, purpose TEXT NOT NULL, mode TEXT NOT NULL,
  created_utc TEXT NOT NULL, status TEXT NOT NULL, risk_decision_id TEXT,
  UNIQUE(strategy_id, strategy_version, signal_id, con_id, side, purpose));
CREATE TABLE IF NOT EXISTS orders(
  order_id INTEGER PRIMARY KEY AUTOINCREMENT, client_order_id TEXT UNIQUE NOT NULL, intent_id TEXT NOT NULL,
  broker_order_id TEXT, state TEXT NOT NULL, qty TEXT NOT NULL, filled_qty TEXT NOT NULL DEFAULT '0',
  strategy_id TEXT NOT NULL, con_id INTEGER NOT NULL, side INTEGER NOT NULL, purpose TEXT NOT NULL,
  order_type TEXT NOT NULL, limit_px TEXT, stop_px TEXT, created_utc TEXT NOT NULL, updated_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS order_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, client_order_id TEXT NOT NULL, from_state TEXT, to_state TEXT, reason TEXT, ts TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS executions(
  exec_id TEXT PRIMARY KEY, client_order_id TEXT, broker_order_id TEXT, con_id INTEGER NOT NULL, side INTEGER NOT NULL,
  qty TEXT NOT NULL, price TEXT NOT NULL, ts TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'broker');
CREATE TABLE IF NOT EXISTS risk_decisions(
  decision_id TEXT PRIMARY KEY, intent_id TEXT, allowed INTEGER NOT NULL, reducing INTEGER NOT NULL, reasons TEXT NOT NULL,
  policy_hash TEXT NOT NULL, policy_version TEXT NOT NULL, ts TEXT NOT NULL, details TEXT);
CREATE TABLE IF NOT EXISTS incidents(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, kind TEXT NOT NULL, severity TEXT NOT NULL, detail TEXT,
  resolved_ts TEXT, resolver TEXT);
CREATE TABLE IF NOT EXISTS shadow_orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT, intent_id TEXT UNIQUE, ts TEXT NOT NULL, detail TEXT);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def open_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def tx(conn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def kv_get(conn, key, default=None):
    r = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


def kv_set(conn, key, value):
    conn.execute("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
