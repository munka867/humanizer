"""Order state machine + execution (fill) store.

    NEW -> SUBMITTING -> ACKED -> PARTIALLY_FILLED -> FILLED
                |          |  \\-> CANCEL_PENDING -> CANCELED
                |          \\-> REJECTED
                \\-> UNCERTAIN (timeout / lost ack / disconnect) --reconciliation--> ACKED | FILLED | CANCELED | REJECTED
An ACK means "broker accepted the order". It is NOT a fill. Position quantities come ONLY from the executions table.
Executions are deduplicated on exec_id, so duplicate and out-of-order callbacks are idempotent."""
from __future__ import annotations

from decimal import Decimal

from tradelab.execution.common import D, iso
from tradelab.execution.db import tx

NEW, SUBMITTING, ACKED, PARTIAL, FILLED, CANCEL_PENDING, CANCELED, REJECTED, UNCERTAIN = (
    "NEW", "SUBMITTING", "ACKED", "PARTIALLY_FILLED", "FILLED", "CANCEL_PENDING", "CANCELED", "REJECTED", "UNCERTAIN")
TERMINAL = {FILLED, CANCELED, REJECTED}
WORKING = {SUBMITTING, ACKED, PARTIAL, CANCEL_PENDING, UNCERTAIN}   # may still produce fills
TRANSITIONS = {
    NEW: {SUBMITTING},
    SUBMITTING: {ACKED, REJECTED, UNCERTAIN, PARTIAL, FILLED},          # PARTIAL/FILLED: fill callback overtook the ack
    ACKED: {PARTIAL, FILLED, CANCEL_PENDING, REJECTED, CANCELED},
    PARTIAL: {PARTIAL, FILLED, CANCEL_PENDING, CANCELED},
    CANCEL_PENDING: {CANCELED, FILLED, PARTIAL, ACKED},                  # ACKED: cancel rejected / too late
    UNCERTAIN: {ACKED, PARTIAL, FILLED, REJECTED, CANCELED},             # ONLY via reconciliation or a late callback
    FILLED: set(), CANCELED: set(), REJECTED: set(),
}


class IllegalTransition(RuntimeError):
    pass


class OrderBook:
    def __init__(self, conn, incident_cb=None):
        self.conn = conn
        self._incident = incident_cb or (lambda *a, **k: None)

    # ---- reads
    def get(self, client_order_id: str):
        return self.conn.execute("SELECT * FROM orders WHERE client_order_id=?", (client_order_id,)).fetchone()

    def for_intent(self, intent_id: str):
        return self.conn.execute("SELECT * FROM orders WHERE intent_id=? ORDER BY order_id", (intent_id,)).fetchall()

    def by_state(self, *states):
        q = ",".join("?" * len(states))
        return self.conn.execute(f"SELECT * FROM orders WHERE state IN ({q}) ORDER BY order_id", states).fetchall()

    # ---- writes
    def create(self, intent, now) -> str:
        coid = f"{intent['intent_id'][:20]}-1"
        with tx(self.conn) as c:
            c.execute("INSERT INTO orders(client_order_id,intent_id,state,qty,strategy_id,con_id,side,purpose,order_type,limit_px,stop_px,"
                      "created_utc,updated_utc) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (coid, intent["intent_id"], NEW, intent["qty"], intent["strategy_id"], intent["con_id"], intent["side"],
                       intent["purpose"], intent["order_type"], intent["limit_px"], intent["stop_px"], iso(now), iso(now)))
            c.execute("INSERT INTO order_events(client_order_id,from_state,to_state,reason,ts) VALUES(?,?,?,?,?)", (coid, None, NEW, "created", iso(now)))
        return coid

    def transition(self, coid: str, new: str, now, reason: str = "", broker_order_id: str | None = None):
        row = self.get(coid)
        old = row["state"]
        if old == new:
            return old
        if new not in TRANSITIONS[old]:
            raise IllegalTransition(f"{coid}: {old} -> {new} not allowed")
        with tx(self.conn) as c:
            c.execute("UPDATE orders SET state=?, updated_utc=?, broker_order_id=COALESCE(?,broker_order_id) WHERE client_order_id=?",
                      (new, iso(now), broker_order_id, coid))
            c.execute("INSERT INTO order_events(client_order_id,from_state,to_state,reason,ts) VALUES(?,?,?,?,?)", (coid, old, new, reason, iso(now)))
        return new

    def apply_execution(self, ex: dict, now, source: str = "broker") -> bool:
        """Record a fill. Idempotent on exec_id. Returns True iff it was new. Drives PARTIALLY_FILLED/FILLED from SUM of fills."""
        coid = ex.get("client_order_id")
        with tx(self.conn) as c:
            cur = c.execute("INSERT OR IGNORE INTO executions(exec_id,client_order_id,broker_order_id,con_id,side,qty,price,ts,source) VALUES(?,?,?,?,?,?,?,?,?)",
                            (ex["exec_id"], coid, ex.get("broker_order_id"), int(ex["con_id"]), int(ex["side"]), str(D(ex["qty"])),
                             str(D(ex["price"])), iso(ex["ts"]), source))
            new = cur.rowcount == 1
        if not new:
            return False
        row = self.get(coid) if coid else None
        if row is None:
            self._incident("EXECUTION_UNKNOWN_ORDER", "high", {"exec_id": ex["exec_id"], "client_order_id": coid})
            return True
        filled = sum((D(r["qty"]) for r in self.conn.execute("SELECT qty FROM executions WHERE client_order_id=?", (coid,))), Decimal(0))
        with tx(self.conn) as c:
            c.execute("UPDATE orders SET filled_qty=?, updated_utc=? WHERE client_order_id=?", (str(filled), iso(now), coid))
        if filled > D(row["qty"]):
            self._incident("OVERFILL", "high", {"client_order_id": coid, "filled": str(filled), "qty": row["qty"]})
        state = row["state"]
        if state in (CANCELED, REJECTED, FILLED):
            if state != FILLED or filled > D(row["qty"]):
                self._incident("FILL_AFTER_TERMINAL", "high", {"client_order_id": coid, "state": state, "exec_id": ex["exec_id"]})
            return True
        target = FILLED if filled >= D(row["qty"]) else PARTIAL
        self.transition(coid, target, now, f"fill {ex['exec_id']}", ex.get("broker_order_id"))
        return True

    # ---- positions: FILLS ONLY
    def positions(self, strategy_id: str | None = None) -> dict[int, Decimal]:
        if strategy_id is None:
            rows = self.conn.execute("SELECT con_id, side, qty FROM executions").fetchall()
        else:
            rows = self.conn.execute("SELECT e.con_id, e.side, e.qty FROM executions e JOIN orders o ON o.client_order_id=e.client_order_id "
                                     "WHERE o.strategy_id=?", (strategy_id,)).fetchall()
        pos: dict[int, Decimal] = {}
        for r in rows:
            pos[r["con_id"]] = pos.get(r["con_id"], Decimal(0)) + r["side"] * D(r["qty"])
        return {k: v for k, v in pos.items() if v != 0}
