"""FAKE ib_async surface for tests of tradelab.brokers.ibkr_paper. NOTHING here talks to a network or a broker.
Mimics only the attributes/methods the adapter uses; callbacks are queued and delivered on sleep() like the real event loop."""
from __future__ import annotations

import itertools
from collections import deque
from datetime import datetime, timezone
from types import SimpleNamespace

T0 = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
NaN = float("nan")


class _Event:
    def __init__(self):
        self._subs = []

    def __iadd__(self, fn):
        self._subs.append(fn)
        return self

    def __isub__(self, fn):
        self._subs = [f for f in self._subs if f != fn]
        return self

    def emit(self, *a):
        for f in list(self._subs):
            f(*a)


class Stock(SimpleNamespace):
    def __init__(self, symbol="", exchange="", currency="", conId=0, primaryExchange="", **kw):
        super().__init__(secType="STK", symbol=symbol, exchange=exchange, currency=currency, conId=conId, primaryExchange=primaryExchange)


class Order(SimpleNamespace):
    def __init__(self, **kw):
        base = dict(orderId=0, permId=0, clientId=0, action="", totalQuantity=0.0, orderType="", lmtPrice=0.0, auxPrice=0.0, tif="DAY",
                    outsideRth=False, account="", orderRef="", transmit=True)
        base.update(kw)
        super().__init__(**base)


class FakeIB:
    def __init__(self):
        self.orderStatusEvent, self.execDetailsEvent, self.commissionReportEvent = _Event(), _Event(), _Event()
        self.errorEvent, self.disconnectedEvent = _Event(), _Event()
        self.accounts = ["DUFAKE001"]
        self.connected = False
        self.connect_args = None
        self.refuse_connect = None
        self.client_id = None
        self.script = deque()            # per placeOrder: accept | fill | partial | reject | silent | duplicate | fill_before_ack | raise
        self.placed = []                 # (contract, order) pairs actually "sent"
        self.cancel_calls = []
        self.trades: list = []
        self.foreign_trades: list = []
        self.foreign_positions = {}
        self.fills: list = []
        self.pending: list = []
        self._ids = itertools.count(100)
        self._exec = itertools.count(1)
        self.price = 100.0
        self.whatif_result = "ok"        # ok | warn | raise | none
        self.unqualifiable = set()
        self.ticker = dict(bid=99.99, ask=100.01, last=100.0, time=T0)
        self.summary = {"NetLiquidation": "100000", "AvailableFunds": "90000"}
        self.cancel_mode = "ok"          # ok | not_found | too_late
        self.order_calls = 0

    # ------------------------------------------------------------- connection
    def connect(self, host, port, clientId=1, timeout=4, readonly=False, **kw):
        self.connect_args = dict(host=host, port=port, clientId=clientId, readonly=readonly)
        if self.refuse_connect:
            raise self.refuse_connect
        self.client_id, self.connected = clientId, True

    def isConnected(self):
        return self.connected

    def disconnect(self):
        was, self.connected = self.connected, False
        if was:
            self.disconnectedEvent.emit()

    def managedAccounts(self):
        return list(self.accounts)

    def reqCurrentTime(self):
        return T0

    def sleep(self, secs=0):
        while self.pending:
            self.pending.pop(0)()
        return True

    # ------------------------------------------------------------- helpers to script callbacks
    def _q(self, fn):
        self.pending.append(fn)

    def status(self, trade, status, filled=None):
        def go():
            trade.orderStatus.status = status
            if filled is not None:
                trade.orderStatus.filled = filled
                trade.orderStatus.remaining = trade.order.totalQuantity - filled
            self.orderStatusEvent.emit(trade)
        self._q(go)

    def fill(self, trade, shares, price, exec_id=None, commission=0.35, account=None):
        exec_id = exec_id or f"EX{next(self._exec)}"
        ex = SimpleNamespace(execId=exec_id, time=T0, acctNumber=account or trade.order.account, side="BOT" if trade.order.action == "BUY" else "SLD",
                             shares=float(shares), price=float(price), permId=trade.orderStatus.permId, orderId=trade.order.orderId,
                             orderRef=trade.order.orderRef, clientId=trade.order.clientId)
        fl = SimpleNamespace(contract=trade.contract, execution=ex, commissionReport=None, time=T0)

        def go():
            self.fills.append(fl)
            trade.fills.append(fl)
            self.execDetailsEvent.emit(trade, fl)
            self.commissionReportEvent.emit(trade, fl, SimpleNamespace(execId=exec_id, commission=commission, currency="USD"))
        self._q(go)
        return exec_id

    def error(self, req_id, code, msg="", contract=None):
        self._q(lambda: self.errorEvent.emit(req_id, code, msg, contract))

    # ------------------------------------------------------------- orders
    def qualifyContracts(self, *cs):
        return [c for c in cs if c.conId not in self.unqualifiable]

    def whatIfOrder(self, contract, order):
        if self.whatif_result == "raise":
            raise RuntimeError("what-if unavailable")
        if self.whatif_result == "none":
            return None
        warn = "Insufficient margin" if self.whatif_result == "warn" else ""
        return SimpleNamespace(warningText=warn, initMarginChange="100.0", initMarginAfter="100.0", equityWithLoanAfter="99000.0")

    def placeOrder(self, contract, order):
        self.order_calls += 1
        beh = self.script.popleft() if self.script else "accept"
        if beh == "raise":
            raise RuntimeError("socket write failed")
        order.orderId, order.clientId = next(self._ids), self.client_id
        permId = 900000 + order.orderId
        trade = SimpleNamespace(contract=contract, order=order, fills=[], log=[],
                                orderStatus=SimpleNamespace(orderId=order.orderId, status="PendingSubmit", filled=0.0, remaining=order.totalQuantity,
                                                            avgFillPrice=0.0, permId=permId, clientId=order.clientId))
        self.placed.append((contract, order))
        self.trades.append(trade)
        qty, px = order.totalQuantity, order.lmtPrice or self.price
        if beh == "accept":
            self.status(trade, "Submitted")
        elif beh == "fill":
            self.status(trade, "Submitted"); self.fill(trade, qty, px); self.status(trade, "Filled", qty)
        elif beh == "partial":
            self.status(trade, "Submitted"); self.fill(trade, qty / 2, px); self.status(trade, "Submitted", qty / 2)
        elif beh == "reject":
            self.error(order.orderId, 201, "Order rejected - reason: simulated")
        elif beh == "fill_before_ack":
            self.fill(trade, qty, px); self.status(trade, "Submitted"); self.status(trade, "Filled", qty)
        elif beh == "duplicate":
            self.status(trade, "Submitted"); self.status(trade, "Submitted")
            self.fill(trade, qty, px, exec_id="DUPX"); self.fill(trade, qty, px, exec_id="DUPX"); self.status(trade, "Filled", qty)
        elif beh == "status_only_fill":          # status says Filled but execDetails never arrives
            self.status(trade, "Submitted"); self.status(trade, "Filled", qty)
        elif beh == "silent":
            pass
        return trade

    def cancelOrder(self, order, manualCancelOrderTime=""):
        self.cancel_calls.append(order.orderId)
        trade = next((t for t in self.trades if t.order.orderId == order.orderId), None)
        if self.cancel_mode == "not_found":
            self.error(order.orderId, 135, "Can't find order")
        elif self.cancel_mode == "too_late":
            self.error(order.orderId, 161, "Cancel attempted when order is not in a cancellable state")
        elif trade is not None:
            self.status(trade, "Cancelled")
        return trade

    def openTrades(self):
        return [t for t in self.trades if t.orderStatus.status not in ("Filled", "Cancelled")]

    def reqAllOpenOrders(self):
        return self.openTrades() + list(self.foreign_trades)

    def reqExecutions(self, f=None):
        return list(self.fills)

    def reqPositions(self):
        pos = {}
        for fl in self.fills:
            e = fl.execution
            pos[(e.acctNumber, fl.contract.conId)] = pos.get((e.acctNumber, fl.contract.conId), 0.0) + (e.shares if e.side == "BOT" else -e.shares)
        out = [SimpleNamespace(account=a, contract=SimpleNamespace(conId=c, secType="STK"), position=q) for (a, c), q in pos.items() if q]
        out += [SimpleNamespace(account=a, contract=SimpleNamespace(conId=c, secType="STK"), position=q) for (a, c), q in self.foreign_positions.items()]
        return out

    def accountSummary(self, account=""):
        return [SimpleNamespace(account=account, tag=k, value=v, currency="USD") for k, v in self.summary.items()]

    def reqTickers(self, *cs):
        return [SimpleNamespace(**self.ticker)]

    def reqMarketDataType(self, n):
        self.market_data_type = n

    def reqMktData(self, contract, *a, **k):
        outer = self

        class _LiveTicker:                       # like ib_async's Ticker: reflects the latest ticks, not a snapshot
            def __getattr__(self, name):
                try:
                    return outer.ticker[name]
                except KeyError:
                    raise AttributeError(name)
        return _LiveTicker()


def foreign_trade(order_id=555, con_id=1001, action="SELL", qty=3, account="DUFAKE001", status="Submitted", ref=""):
    return SimpleNamespace(contract=SimpleNamespace(conId=con_id), order=Order(orderId=order_id, clientId=7, action=action, totalQuantity=qty,
                                                                              orderType="STP", auxPrice=1.0, account=account, orderRef=ref),
                           orderStatus=SimpleNamespace(orderId=order_id, status=status, filled=0.0, remaining=qty, permId=order_id + 5000))


MODULE = SimpleNamespace(IB=FakeIB, Stock=Stock, Order=Order)
