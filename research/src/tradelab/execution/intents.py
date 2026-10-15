"""Persistent intent ledger. intent_id is a stable hash, and a UNIQUE constraint makes insertion idempotent, so a
duplicate signal or a duplicate callback can never create a second intent."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from tradelab.execution.common import D, iso, sha256_hex
from tradelab.execution.db import tx

ENTRY, PROTECTIVE, EXIT = "entry", "protective", "exit"
PURPOSES = (ENTRY, PROTECTIVE, EXIT)
ORDER_TYPES = ("MKT", "LMT", "STP")


@dataclass(frozen=True)
class Proposal:
    """What a strategy (via the adapter) asks for. Carries NO authority: the service decides."""
    strategy_id: str
    strategy_version: str
    signal_id: str
    con_id: int
    symbol: str
    side: int                      # +1 buy, -1 sell
    qty: Decimal
    order_type: str = "MKT"
    limit_px: Decimal | None = None
    stop_px: Decimal | None = None   # order stop price (STP) OR, for entries, the planned protective stop used for risk sizing
    tif: str = "DAY"
    outside_rth: bool = False
    purpose: str = ENTRY

    def __post_init__(self):
        if self.side not in (1, -1):
            raise ValueError("side must be +1/-1")
        if self.order_type not in ORDER_TYPES or self.purpose not in PURPOSES:
            raise ValueError("bad order_type/purpose")
        if D(self.qty) <= 0:
            raise ValueError("qty must be > 0")
        if self.order_type == "LMT" and self.limit_px is None:
            raise ValueError("LMT needs limit_px")
        if self.order_type == "STP" and self.stop_px is None:
            raise ValueError("STP needs stop_px")


def make_intent_id(strategy_id: str, strategy_version: str, signal_id: str, con_id: int, side: int, purpose: str = ENTRY) -> str:
    return sha256_hex("|".join([strategy_id, strategy_version, signal_id, str(int(con_id)), str(int(side)), purpose]))[:32]


class IntentLedger:
    def __init__(self, conn):
        self.conn = conn

    def get(self, intent_id: str):
        return self.conn.execute("SELECT * FROM intents WHERE intent_id=?", (intent_id,)).fetchone()

    def insert(self, p: Proposal, mode: str, now) -> tuple[object, bool]:
        """Idempotent. Returns (row, created). A second call with the same identity returns the EXISTING row."""
        iid = make_intent_id(p.strategy_id, p.strategy_version, p.signal_id, p.con_id, p.side, p.purpose)
        with tx(self.conn) as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO intents(intent_id,strategy_id,strategy_version,signal_id,con_id,symbol,side,qty,order_type,"
                "limit_px,stop_px,tif,outside_rth,purpose,mode,created_utc,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (iid, p.strategy_id, p.strategy_version, p.signal_id, int(p.con_id), p.symbol, p.side, str(D(p.qty)), p.order_type,
                 None if p.limit_px is None else str(D(p.limit_px)), None if p.stop_px is None else str(D(p.stop_px)),
                 p.tif, int(p.outside_rth), p.purpose, mode, iso(now), "PROPOSED"))
            created = cur.rowcount == 1
        return self.get(iid), created

    def set_status(self, intent_id: str, status: str, risk_decision_id: str | None = None):
        with tx(self.conn) as c:
            if risk_decision_id is None:
                c.execute("UPDATE intents SET status=? WHERE intent_id=?", (status, intent_id))
            else:
                c.execute("UPDATE intents SET status=?, risk_decision_id=? WHERE intent_id=?", (status, risk_decision_id, intent_id))
