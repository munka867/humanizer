"""Broker port + SimulatedBroker. There is NO real adapter in this package.

Events (acks, fills, ...) are pulled with poll_events(); the simulated broker can duplicate, reorder, drop or delay them.
The broker is the source of truth for what exists on its side; the ledger is the source of truth for what we intended."""
from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Protocol, runtime_checkable

from tradelab.execution.common import D


class BrokerTimeout(Exception):
    """No answer: the order MAY or MAY NOT exist at the broker."""


class BrokerDisconnected(Exception):
    """Connection lost: outcome unknown."""


@dataclass(frozen=True)
class OrderRequest:
    client_order_id: str
    con_id: int
    side: int
    qty: Decimal
    order_type: str
    limit_px: Decimal | None = None
    stop_px: Decimal | None = None
    tif: str = "DAY"
    outside_rth: bool = False


@dataclass(frozen=True)
class BrokerOrder:
    broker_order_id: str
    client_order_id: str
    con_id: int
    side: int
    qty: Decimal
    filled: Decimal
    order_type: str
    limit_px: Decimal | None
    stop_px: Decimal | None
    status: str          # "working" | "filled" | "canceled" | "rejected"


@dataclass(frozen=True)
class BrokerEvent:
    kind: str            # ack | reject | fill | cancel_ack | cancel_reject
    client_order_id: str
    broker_order_id: str | None = None
    exec_id: str | None = None
    con_id: int | None = None
    side: int | None = None
    qty: Decimal | None = None
    price: Decimal | None = None
    ts: datetime | None = None
    reason: str = ""


@dataclass(frozen=True)
class AccountInfo:
    account_id: str
    equity: Decimal
    available_funds: Decimal
    ts: datetime


@runtime_checkable
class BrokerPort(Protocol):
    is_simulated: bool
    def submit(self, req: OrderRequest) -> str: ...            # returns broker_order_id (ack); may raise BrokerTimeout/Disconnected
    def cancel(self, client_order_id: str) -> None: ...
    def open_orders(self) -> list[BrokerOrder]: ...
    def executions(self) -> list[BrokerEvent]: ...             # full execution history (fill events)
    def positions(self) -> dict[int, Decimal]: ...
    def account(self) -> AccountInfo: ...
    def server_time(self) -> datetime: ...
    def poll_events(self) -> list[BrokerEvent]: ...
    def shortable(self, con_id: int) -> bool | None: ...


EPOCH = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)


class SimulatedBroker:
    """Deterministic. Behaviours are scripted per submit via `script_submit(...)` (FIFO) and global switches.

    submit behaviours: ok | reject | lost_ack (order accepted, caller gets BrokerTimeout, no ack event) |
                       timeout_not_sent (BrokerTimeout, order never existed) | disconnect (BrokerDisconnected, never existed)
    fills: market orders fill at the current price on advance(); limit/stop trigger on the price path; `partial_fractions`
           splits a fill into pieces; `fill_delay_steps` delays fills by N advance() calls.
    global: duplicate_events (every event delivered twice), reverse_events (delivered in reverse order within one poll),
            cancel_races (a cancel loses to a fill that happens first)."""
    is_simulated = True

    def __init__(self, account_id="SIM-0001", equity="10000", available="10000", seed=0, shortable=None):
        self._acct = account_id
        self.equity, self.available = D(equity), D(available)
        self._clock = EPOCH
        self._rng = random.Random(seed)
        self._ids = itertools.count(1)
        self._orders: dict[str, dict] = {}
        self._fills: list[BrokerEvent] = []
        self._events: list[BrokerEvent] = []
        self._pos: dict[int, Decimal] = {}
        self._submit_script: list[str] = []
        self.duplicate_events = False
        self.reverse_events = False
        self.cancel_races = False
        self.partial_fractions: list[Decimal] | None = None
        self.fill_delay_steps = 0
        self.connected = True
        self._shortable = shortable or {}
        self.price: dict[int, Decimal] = {}
        self.account_stale = False
        self.auto_fill_market = False     # fill market orders at the last known price as soon as they are accepted
        self._exec_ids = itertools.count(1)

    # ---- scripting
    def script_submit(self, *behaviours: str):
        self._submit_script.extend(behaviours)

    def set_time(self, t: datetime):
        self._clock = t

    def restart(self):
        """Broker keeps orders/positions/executions (it is the truth); the client loses undelivered events."""
        self._events.clear()
        self.connected = True

    def inject_foreign_position(self, con_id, qty):
        self._pos[con_id] = self._pos.get(con_id, Decimal(0)) + D(qty)

    def inject_foreign_order(self, con_id, side, qty, order_type="STP", stop_px="1"):
        bid = f"SIM-{next(self._ids)}"
        self._orders[bid] = dict(bid=bid, coid="FOREIGN-" + bid, con_id=con_id, side=side, qty=D(qty), filled=Decimal(0),
                                 order_type=order_type, limit_px=None, stop_px=D(stop_px), status="working", delay=0)

    # ---- port
    def _emit(self, ev: BrokerEvent):
        self._events.append(ev)

    def submit(self, req: OrderRequest) -> str:
        if not self.connected:
            raise BrokerDisconnected("simulated disconnect")
        beh = self._submit_script.pop(0) if self._submit_script else "ok"
        if beh == "disconnect":
            self.connected = False
            raise BrokerDisconnected("simulated disconnect")
        if beh == "timeout_not_sent":
            raise BrokerTimeout("simulated timeout (never reached broker)")
        bid = f"SIM-{next(self._ids)}"
        if beh == "reject":
            self._emit(BrokerEvent("reject", req.client_order_id, bid, ts=self._clock, reason="simulated reject"))
            self._orders[bid] = dict(bid=bid, coid=req.client_order_id, con_id=req.con_id, side=req.side, qty=req.qty, filled=Decimal(0),
                                     order_type=req.order_type, limit_px=req.limit_px, stop_px=req.stop_px, status="rejected", delay=0)
            return bid
        self._orders[bid] = dict(bid=bid, coid=req.client_order_id, con_id=req.con_id, side=req.side, qty=req.qty, filled=Decimal(0),
                                 order_type=req.order_type, limit_px=req.limit_px, stop_px=req.stop_px, status="working",
                                 delay=self.fill_delay_steps)
        if beh == "lost_ack":
            raise BrokerTimeout("simulated lost ack (order IS at broker)")
        self._emit(BrokerEvent("ack", req.client_order_id, bid, ts=self._clock))
        if self.auto_fill_market and req.order_type == "MKT" and req.con_id in self.price and self._orders[bid]["delay"] == 0:
            self._fill(self._orders[bid], req.qty, self.price[req.con_id])
        return bid

    def cancel(self, client_order_id: str) -> None:
        if not self.connected:
            raise BrokerDisconnected("simulated disconnect")
        for o in self._orders.values():
            if o["coid"] == client_order_id:
                if o["status"] != "working":
                    self._emit(BrokerEvent("cancel_reject", client_order_id, o["bid"], ts=self._clock, reason=o["status"]))
                elif self.cancel_races:
                    # the fill wins the race: fill everything now, then report the cancel as rejected
                    self._fill(o, o["qty"] - o["filled"], self.price.get(o["con_id"], Decimal(1)))
                    self._emit(BrokerEvent("cancel_reject", client_order_id, o["bid"], ts=self._clock, reason="too late, filled"))
                else:
                    o["status"] = "canceled"
                    self._emit(BrokerEvent("cancel_ack", client_order_id, o["bid"], ts=self._clock))
                return
        self._emit(BrokerEvent("cancel_reject", client_order_id, None, ts=self._clock, reason="unknown order"))

    def open_orders(self):
        return [BrokerOrder(o["bid"], o["coid"], o["con_id"], o["side"], o["qty"], o["filled"], o["order_type"], o["limit_px"], o["stop_px"], o["status"])
                for o in self._orders.values() if o["status"] == "working"]

    def executions(self):
        return list(self._fills)

    def positions(self):
        return {k: v for k, v in self._pos.items() if v != 0}

    def account(self):
        ts = self._clock - timedelta(hours=1) if self.account_stale else self._clock
        return AccountInfo(self._acct, self.equity, self.available, ts)

    def server_time(self):
        return self._clock

    def shortable(self, con_id):
        return self._shortable.get(con_id)

    def poll_events(self):
        if not self.connected:
            raise BrokerDisconnected("simulated disconnect")
        ev, self._events = self._events, []
        if self.reverse_events:
            ev = ev[::-1]
        if self.duplicate_events:
            ev = [e for e in ev for _ in (0, 1)]
        return ev

    # ---- market simulation (the replay/test driver calls this)
    def _fill(self, o, qty, px):
        qty = min(D(qty), o["qty"] - o["filled"])
        if qty <= 0:
            return
        pieces = [qty]
        if self.partial_fractions:
            pieces = [qty * f for f in self.partial_fractions]
            pieces[-1] = qty - sum(pieces[:-1], Decimal(0))
        for pc in pieces:
            o["filled"] += pc
            self._pos[o["con_id"]] = self._pos.get(o["con_id"], Decimal(0)) + o["side"] * pc
            ev = BrokerEvent("fill", o["coid"], o["bid"], f"EX-{next(self._exec_ids)}", o["con_id"], o["side"], pc, px, self._clock)
            self._fills.append(ev)
            self._emit(ev)
        if o["filled"] >= o["qty"]:
            o["status"] = "filled"

    def advance(self, con_id: int, prices, now: datetime | None = None):
        """Feed an in-time-order price path for one instrument; working orders trigger/fill against it."""
        if now:
            self._clock = now
        for px in [D(p) for p in prices]:
            self.price[con_id] = px
            for o in list(self._orders.values()):
                if o["con_id"] != con_id or o["status"] != "working":
                    continue
                if o["delay"] > 0:
                    continue
                t = o["order_type"]
                hit = (t == "MKT" or
                       (t == "LMT" and ((o["side"] > 0 and px <= o["limit_px"]) or (o["side"] < 0 and px >= o["limit_px"]))) or
                       (t == "STP" and ((o["side"] < 0 and px <= o["stop_px"]) or (o["side"] > 0 and px >= o["stop_px"]))))
                if hit:
                    self._fill(o, o["qty"] - o["filled"], px)
        for o in self._orders.values():
            if o["delay"] > 0:
                o["delay"] -= 1
