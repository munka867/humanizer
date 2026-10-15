"""IbkrPaperBroker: BrokerPort implementation over ib_async, PAPER ONLY.

* Lazy, optional ib_async (pin 2.1.0, BSD-2-Clause). Tests inject a fake module; nothing here runs unless a user calls connect().
* Every order: STK only, USD/CAD, MKT/LMT/STP, DAY/GTC, outsideRth False unless allowed, account set explicitly, orderRef = versioned HMAC-tagged
  reference of the intent id (made by the bound service; foreign or untagged refs never map to our orders).
* An order is accepted only with a valid PaperOrderPermit minted by ExecutionService for THAT order (single use, short expiry).
* Callbacks are mapped to BrokerEvents idempotently. An orderStatus is never a fill; fills come from execDetails (reqExecutions at reconcile).
* Ambiguous outcomes become 'uncertain' events (never blind retry); connectivity changes become 'resync_required'."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from tradelab.brokers.ibkr_paper import errors as E
from tradelab.brokers.ibkr_paper import guard as G
from tradelab.execution.broker import (AccountInfo, BrokerDisconnected, BrokerEvent, BrokerOrder, BrokerRefused, BrokerTimeout, OrderRequest)
from tradelab.execution.common import D, utcnow
from tradelab.execution.orders import client_order_id_for
from tradelab.execution.permit import PermitError
from tradelab.execution.risk import Quote

ALLOWED_CURRENCIES = ("USD", "CAD")
ALLOWED_TYPES = ("MKT", "LMT", "STP")
ALLOWED_TIF = ("DAY", "GTC")
WORKING_STATUSES = {"PendingSubmit", "ApiPending", "PreSubmitted", "Submitted"}
ACK_STATUSES = {"PreSubmitted", "Submitted"}
CANCEL_STATUSES = {"Cancelled", "ApiCancelled"}
_INTENT_RE = re.compile(r"^[0-9a-f]{32}$")
UNSET = 1e300


class AdapterUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ContractSpec:
    symbol: str
    currency: str = "USD"
    exchange: str = "SMART"
    primary_exchange: str | None = None


def load_ib_async():
    try:
        import ib_async            # the ONLY order-capable import of ib_async in tradelab
    except ImportError as e:
        raise AdapterUnavailable("ib_async is not installed. Install the pinned version with:  pip install ib_async==2.1.0") from e
    return ib_async


def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(v) or abs(v) >= UNSET) else v


class IbkrPaperBroker:
    is_simulated = False

    def __init__(self, contracts: dict[int, ContractSpec], *, ib_module=None, host="127.0.0.1", port=7497, expected_account: str | None = None,
                 clock=utcnow, allow_outside_rth=False, whatif_on_submit=True, max_init_margin_change: Decimal | None = None,
                 connect_timeout=8.0, event_pump_seconds=0.05):
        G.check_target(host, port, G.CLIENT_ID)                 # fail at construction, not only at connect
        self._mod, self._contracts = ib_module, dict(contracts)
        self.host, self.port, self.expected_account = host, port, expected_account
        self._clock, self._allow_rth, self._whatif_on_submit, self._max_margin = clock, allow_outside_rth, whatif_on_submit, max_init_margin_change
        self._timeout, self._pump = connect_timeout, event_pump_seconds
        self.ib = None
        self.accounts: list[str] = []
        self._events: list[BrokerEvent] = []
        self._trades: dict[str, object] = {}
        self._acked: set[str] = set()
        self._cancel_acked: set[str] = set()
        self._unacked: set[str] = set()
        self._exec_seen: set[str] = set()
        self._filled_seen: dict[str, Decimal] = {}
        self._resync_flagged: set[str] = set()
        self._qualified: dict[int, object] = {}
        self.commissions: dict[str, tuple[Decimal, str]] = {}
        self.diagnostics: list[dict] = []            # plain-language audit of every mapped error / refusal
        self.connected = False
        self.needs_reconcile = False
        self._consume = self._make_ref = self._parse_ref = None      # bound once by ExecutionService (bind_authority)
        self.unresolved_corrections: list[str] = []
        self._exec_base: dict[str, str] = {}

    def bind_authority(self, consume, make_ref, parse_ref):
        """One-shot: the ExecutionService hands over its permit verifier and orderRef tagger. An unbound adapter refuses every order."""
        if self._consume is not None:
            raise RuntimeError("adapter is already bound to an ExecutionService")
        self._consume, self._make_ref, self._parse_ref = consume, make_ref, parse_ref

    def acknowledge_corrections(self):
        self.unresolved_corrections = []

    # ------------------------------------------------------------------ connection
    def connect(self):
        G.check_target(self.host, self.port, G.CLIENT_ID)
        mod = self._mod or load_ib_async()
        self._mod = mod
        ib = mod.IB()
        ib.connect(self.host, self.port, clientId=G.CLIENT_ID, timeout=self._timeout, readonly=False)
        try:
            self.accounts = G.check_accounts(ib.managedAccounts(), self.expected_account)
        except G.GuardError:
            try:
                ib.disconnect()
            finally:
                self.connected = False
            raise
        self.ib, self.connected = ib, True
        ib.orderStatusEvent += self._on_status
        ib.execDetailsEvent += self._on_exec
        ib.commissionReportEvent += self._on_commission
        ib.errorEvent += self._on_error
        ib.disconnectedEvent += self._on_disconnected
        return self.accounts

    def disconnect(self):
        if self.ib is not None:
            self.ib.disconnect()
        self.connected = False

    def _require(self):
        if not self.connected or self.ib is None or not self.ib.isConnected():
            self.connected = False
            raise BrokerDisconnected("not connected to TWS")

    def _diag(self, kind, **kw):
        self.diagnostics.append({"ts": self._clock().isoformat(), "kind": kind, **kw})

    # ------------------------------------------------------------------ id mapping
    @staticmethod
    def _coid(order_ref, order_id=0, perm_id=0, parse=None) -> str:
        """Client order id of OUR order, only if the orderRef carries a valid HMAC tag (versioned format); anything else is FOREIGN."""
        iid = parse(order_ref) if parse is not None else None
        if iid and _INTENT_RE.match(iid):
            return client_order_id_for(iid)
        return f"FOREIGN-{order_id or perm_id or 'x'}"

    def _cid(self, order_ref, order_id=0, perm_id=0) -> str:
        return self._coid(order_ref, order_id, perm_id, self._parse_ref)

    @staticmethod
    def _exec_stem(exec_id: str) -> str:
        """IBKR convention (UNVERIFIED here): a corrected execution re-uses the id with a higher last revision component."""
        return exec_id.rsplit(".", 1)[0] if "." in exec_id else exec_id

    def _emit(self, kind, coid, **kw):
        self._events.append(BrokerEvent(kind, coid, ts=self._clock(), **kw))

    # ------------------------------------------------------------------ callbacks
    def _on_status(self, trade):
        o, s = trade.order, trade.orderStatus
        coid = self._cid(getattr(o, "orderRef", ""), getattr(o, "orderId", 0), getattr(s, "permId", 0))
        bid = str(getattr(o, "orderId", 0) or getattr(s, "permId", 0))
        st = s.status
        if st in ACK_STATUSES and coid not in self._acked:
            self._acked.add(coid)
            self._unacked.discard(coid)
            self._emit("ack", coid, broker_order_id=bid)
        elif st in CANCEL_STATUSES and coid not in self._cancel_acked:
            self._cancel_acked.add(coid)
            self._unacked.discard(coid)
            self._emit("cancel_ack", coid, broker_order_id=bid)
        elif st == "Inactive":
            self._diag("inactive", coid=coid)
            self._emit("uncertain", coid, broker_order_id=bid, reason="order is Inactive at IBKR (rejected, unfunded or held); reconcile")
        # 'Filled' / PendingSubmit / ApiPending: no event. Quantities arrive ONLY through execDetails.

    def _on_exec(self, trade, fill):
        ex = fill.execution
        if ex.execId in self._exec_seen:
            return
        acct = getattr(ex, "acctNumber", None)
        if acct and acct not in self.accounts:
            self._diag("foreign_account_execution", exec_id=ex.execId)
            return
        stem = self._exec_stem(ex.execId)
        if stem in self._exec_base and self._exec_base[stem] != ex.execId:
            # same execution re-sent with another revision: do NOT auto-count; a human must review before new entries
            self.unresolved_corrections.append(ex.execId)
            self._diag("execution_correction", exec_id=ex.execId, first_seen=self._exec_base[stem])
            self._exec_seen.add(ex.execId)
            self._emit("resync_required", "", reason=f"execution {ex.execId} looks like a correction of {self._exec_base[stem]}; human review required")
            return
        self._exec_base[stem] = ex.execId
        self._exec_seen.add(ex.execId)
        coid = self._cid(getattr(ex, "orderRef", "") or getattr(trade.order, "orderRef", ""), ex.orderId, ex.permId)
        qty = D(ex.shares)
        self._filled_seen[coid] = self._filled_seen.get(coid, Decimal(0)) + qty
        self._events.append(BrokerEvent("fill", coid, str(ex.orderId or ex.permId), ex.execId, fill.contract.conId,
                                        1 if str(ex.side).upper() in ("BOT", "BUY") else -1, qty, D(ex.price),
                                        getattr(fill, "time", None) or self._clock()))

    def _on_commission(self, trade, fill, report):
        self.commissions[report.execId] = (D(report.commission), getattr(report, "currency", ""))

    def _on_error(self, reqId, errorCode, errorString, contract=None):
        known = any(getattr(t.order, "orderId", None) == reqId for t in self._trades.values())
        order_related = known or (isinstance(reqId, int) and reqId > 0)
        action, text = E.classify(int(errorCode), order_related)
        coid = next((c for c, t in self._trades.items() if getattr(t.order, "orderId", None) == reqId), f"FOREIGN-{reqId}")
        self._diag("ibkr_error", code=errorCode, req_id=reqId, action=action, plain=text, raw=str(errorString)[:200])
        if action == "info":
            return
        if action in ("resync", "disconnect"):
            if action == "disconnect":
                self.connected = False
            self.needs_reconcile = True
            for c in list(self._unacked):
                self._emit("uncertain", c, reason=f"{text} (code {errorCode})")
            self._emit("resync_required", "", reason=f"{text} (code {errorCode})")
            return
        if not order_related:
            return
        reason = f"{text} [{errorCode}: {str(errorString)[:120]}]"
        if action == "reject":
            self._unacked.discard(coid)
            self._emit("reject", coid, reason=reason)
        elif action == "cancelled":
            if coid not in self._cancel_acked:
                self._cancel_acked.add(coid)
                self._emit("cancel_ack", coid)
        elif action == "cancel_fail":
            self._emit("cancel_reject", coid, reason=reason)
        else:
            self._emit("uncertain", coid, reason=reason)

    def _on_disconnected(self):
        self.connected = False
        self.needs_reconcile = True
        for c in list(self._unacked):
            self._emit("uncertain", c, reason="connection to TWS lost before the order was acknowledged")
        self._emit("resync_required", "", reason="TWS connection lost")

    # ------------------------------------------------------------------ order building
    def _contract(self, con_id: int):
        spec = self._contracts.get(con_id)
        if spec is None:
            raise BrokerRefused(f"conId {con_id} is not in the adapter's contract table")
        if spec.currency not in ALLOWED_CURRENCIES:
            raise BrokerRefused(f"currency {spec.currency} not allowed (USD/CAD only)")
        if con_id in self._qualified:
            return self._qualified[con_id]
        c = self._mod.Stock(symbol=spec.symbol, exchange=spec.exchange, currency=spec.currency, conId=con_id,
                            **({"primaryExchange": spec.primary_exchange} if spec.primary_exchange else {}))
        try:
            q = self.ib.qualifyContracts(c)
        except Exception as e:
            raise BrokerRefused(f"could not qualify contract {con_id}: {type(e).__name__}") from e
        if not q or getattr(q[0], "conId", None) != con_id or getattr(q[0], "secType", "STK") != "STK":
            raise BrokerRefused(E.ERRORS[200][1])
        self._qualified[con_id] = q[0]
        return q[0]

    def _build_order(self, req_like, account: str, ref: str | None):
        side, qty, ot = req_like.side, D(req_like.qty), req_like.order_type
        if ot not in ALLOWED_TYPES:
            raise BrokerRefused(f"order type {ot} not allowed")
        if req_like.tif not in ALLOWED_TIF:
            raise BrokerRefused(f"time-in-force {req_like.tif} not allowed")
        if req_like.outside_rth and not self._allow_rth:
            raise BrokerRefused("outsideRth orders are not enabled for this adapter")
        if qty <= 0 or qty != qty.to_integral_value():
            raise BrokerRefused("quantity must be a positive whole number of shares")
        kw = dict(action="BUY" if side > 0 else "SELL", totalQuantity=float(qty), orderType=ot, tif=req_like.tif,
                  outsideRth=bool(req_like.outside_rth), account=account, transmit=True)
        if ref:
            kw["orderRef"] = ref
        if ot == "LMT":
            kw["lmtPrice"] = float(D(req_like.limit_px))
        if ot == "STP":
            kw["auxPrice"] = float(D(req_like.stop_px))
        return self._mod.Order(**kw)

    def _whatif(self, contract, order) -> bool | None:
        """True = margin impact acceptable, False = clearly not, None = what-if unavailable."""
        try:
            st = self.ib.whatIfOrder(contract, order)
        except Exception as e:
            self._diag("whatif_unavailable", error=type(e).__name__)
            return None
        if st is None:
            return None
        warn = getattr(st, "warningText", "") or ""
        chg = _f(getattr(st, "initMarginChange", None))
        after, ewl = _f(getattr(st, "initMarginAfter", None)), _f(getattr(st, "equityWithLoanAfter", None))
        self._diag("whatif", warning=warn[:120], init_margin_change=chg, init_margin_after=after, equity_with_loan_after=ewl)
        if warn:
            return False
        if chg is None and after is None:
            return None
        if self._max_margin is not None and chg is not None and Decimal(str(chg)) > self._max_margin:
            return False
        if after is not None and ewl is not None and after > ewl:
            return False
        return True

    def margin_hook(self, proposal) -> bool | None:
        """For ExecutionService(margin_hook=...): what-if of the proposed order."""
        try:
            self._require()
            account = self.expected_account or (self.accounts[0] if len(self.accounts) == 1 else None)
            if account is None:
                return None
            contract = self._contract(proposal.con_id)
            return self._whatif(contract, self._build_order(proposal, account, None))
        except (BrokerRefused, BrokerDisconnected):
            return None

    # ------------------------------------------------------------------ BrokerPort
    def submit(self, req: OrderRequest) -> str:
        try:
            self._require()
        except BrokerDisconnected as e:
            raise BrokerRefused("not connected to TWS; nothing was sent") from e
        if self._consume is None:
            raise BrokerRefused("adapter is not bound to an ExecutionService; orders are refused")
        try:
            self._consume(req.permit, req, self._clock())
            permit = req.permit
            G.check_order_account(permit.account_id, self.accounts, self.expected_account)
            contract = self._contract(req.con_id)
            if not req.intent_id or not _INTENT_RE.match(req.intent_id):
                raise BrokerRefused("intent id missing: orderRef could not be set")
            ref = self._make_ref(req.intent_id)
            if not ref:
                raise BrokerRefused("orderRef could not be tagged (approval key unavailable to the service); order not sent")
            order = self._build_order(req, permit.account_id, ref)
            if self._whatif_on_submit and self._whatif(contract, order) is False:
                raise BrokerRefused("what-if margin check failed or carries a warning; order not sent")
        except PermitError as e:
            self._diag("refused", why=str(e))
            raise BrokerRefused(str(e)) from e
        except G.GuardError as e:
            self._diag("refused", why=str(e))
            self.disconnect()
            raise BrokerRefused(str(e)) from e
        except BrokerRefused as e:
            self._diag("refused", why=str(e))
            raise
        self._unacked.add(req.client_order_id)
        try:
            trade = self.ib.placeOrder(contract, order)
        except Exception as e:                 # may or may not have reached TWS: ambiguous
            raise BrokerTimeout(f"placeOrder raised {type(e).__name__}: outcome unknown") from e
        self._trades[req.client_order_id] = trade
        return str(trade.order.orderId)

    def cancel(self, client_order_id: str) -> None:
        self._require()
        trade = self._trades.get(client_order_id)
        if trade is None:
            for t in self.ib.openTrades():
                if self._cid(getattr(t.order, "orderRef", ""), t.order.orderId, t.orderStatus.permId) == client_order_id:
                    trade = t
        if trade is None:
            self._emit("cancel_reject", client_order_id, reason="order not found at this client; reconcile")
            return
        if getattr(trade.order, "clientId", G.CLIENT_ID) != G.CLIENT_ID:
            raise BrokerRefused("order belongs to another API client; this adapter cancels only its own orders")
        self.ib.cancelOrder(trade.order)

    def _mine(self, account) -> bool:
        return (not account) or account in self.accounts

    def open_orders(self) -> list[BrokerOrder]:
        self._require()
        out = []
        for t in self.ib.reqAllOpenOrders():
            o, s = t.order, t.orderStatus
            if not self._mine(getattr(o, "account", "")) or s.status not in WORKING_STATUSES:
                continue
            out.append(BrokerOrder(str(o.orderId or s.permId), self._cid(getattr(o, "orderRef", ""), o.orderId, s.permId), t.contract.conId,
                                   1 if o.action == "BUY" else -1, D(o.totalQuantity), D(s.filled), o.orderType,
                                   None if _f(o.lmtPrice) in (None, 0.0) else D(o.lmtPrice),
                                   None if _f(o.auxPrice) in (None, 0.0) else D(o.auxPrice), "working"))
        return out

    def executions(self) -> list[BrokerEvent]:
        self._require()
        out, stems = [], {}
        for f in self.ib.reqExecutions():
            ex = f.execution
            if not self._mine(getattr(ex, "acctNumber", "")):
                continue
            stem = self._exec_stem(ex.execId)
            if stem in stems and stems[stem] != ex.execId:         # correction: keep the first, flag for human review
                if ex.execId not in self.unresolved_corrections:
                    self.unresolved_corrections.append(ex.execId)
                self._diag("execution_correction", exec_id=ex.execId, first_seen=stems[stem])
                continue
            stems[stem] = ex.execId
            out.append(BrokerEvent("fill", self._cid(getattr(ex, "orderRef", ""), ex.orderId, ex.permId), str(ex.orderId or ex.permId), ex.execId,
                                   f.contract.conId, 1 if str(ex.side).upper() in ("BOT", "BUY") else -1, D(ex.shares), D(ex.price),
                                   getattr(f, "time", None) or self._clock()))
        return out

    def positions(self) -> dict[int, Decimal]:
        self._require()
        pos: dict[int, Decimal] = {}
        for p in self.ib.reqPositions():
            if self._mine(p.account) and getattr(p.contract, "secType", "STK") == "STK" and D(p.position) != 0:
                pos[p.contract.conId] = pos.get(p.contract.conId, Decimal(0)) + D(p.position)
        return {k: v for k, v in pos.items() if v != 0}

    def account(self) -> AccountInfo:
        self._require()
        acct = self.expected_account or (self.accounts[0] if len(self.accounts) == 1 else None)
        if acct is None:
            raise BrokerDisconnected("no single paper account selected")
        vals = {v.tag: v for v in self.ib.accountSummary(acct) if v.currency in ("USD", "BASE", "CAD") and v.account == acct}
        try:
            eq, av = D(vals["NetLiquidation"].value), D(vals["AvailableFunds"].value)
        except (KeyError, InvalidOperation) as e:
            raise BrokerDisconnected("account summary incomplete") from e
        return AccountInfo(acct, eq, av, self._clock())

    def server_time(self) -> datetime:
        self._require()
        t = self.ib.reqCurrentTime()
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)

    def shortable(self, con_id: int):
        return None          # not verified from this connection: shorting stays blocked by the risk engine

    def poll_events(self) -> list[BrokerEvent]:
        self._require()
        self.ib.sleep(self._pump)             # lets ib_async deliver pending callbacks
        for coid, t in self._trades.items():  # status says filled but execDetails never arrived -> do not guess: force reconciliation
            seen = self._filled_seen.get(coid, Decimal(0))
            if D(t.orderStatus.filled) > seen and coid not in self._resync_flagged:
                self._resync_flagged.add(coid)
                self._emit("resync_required", coid, reason=f"status shows {t.orderStatus.filled} filled but only {seen} execution detail(s) seen")
        ev, self._events = self._events, []
        return ev

    # ------------------------------------------------------------------ market data (for the smoke test)
    def quote(self, con_id: int) -> Quote | None:
        self._require()
        contract = self._contract(con_id)
        tk = self.ib.reqTickers(contract)
        if not tk:
            return None
        t = tk[0]
        bid, ask, last = _f(t.bid), _f(t.ask), _f(t.last)
        if bid is None or ask is None or bid <= 0 or ask <= 0:
            return None
        ts = getattr(t, "time", None)
        return Quote(D(bid), D(ask), D(last) if last else None, ts if ts is not None else self._clock())
