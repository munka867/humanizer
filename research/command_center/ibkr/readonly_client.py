"""Thin READ-ONLY wrapper around an ib_async-style IB object.

Exposes only: connect, disconnect, server time, qualify STOCK contracts, set market-data type, quote subscribe/read, historical bars,
head timestamp, account summary / positions / open-orders (read) / executions (read), P&L read.
The underlying IB object is private (name-mangled) and never returned. No order-sending, changing or cancelling call is exposed or referenced
in this package (static + runtime tests). Stocks/ETFs only: any other security type is refused.
"""
from __future__ import annotations

import asyncio
import collections
import datetime as dt
import math
from dataclasses import dataclass, asdict

from .guard import Target

DATA_TYPE_LABEL = {1: "live", 2: "frozen", 3: "delayed", 4: "delayed_frozen"}
SOURCE_REAL = "ibkr"


class LibraryMissing(RuntimeError):
    pass


class NotStockError(ValueError):
    pass


def load_ib_async():
    """Lazy import so the server starts without the optional dependency."""
    try:
        import ib_async  # noqa: PLC0415
        return ib_async
    except Exception as e:  # ImportError, or a library that fails to import on this Python
        raise LibraryMissing(f"ib_async could not be imported ({type(e).__name__}: {e}). Install it with: python -m pip install ib_async==2.1.0") from e


def library_info() -> dict:
    try:
        lib = load_ib_async()
        return {"installed": True, "version": getattr(lib, "__version__", None) or getattr(getattr(lib, "version", None), "__version__", None), "message": None}
    except LibraryMissing as e:
        return {"installed": False, "version": None, "message": str(e)}


def _num(v):
    """Finite number or None (IB uses nan and 1.79e308 as 'unset')."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f) or abs(f) > 1e300) else f


def _price(v):
    """Quote price or None; IB sends -1 for 'no bid/ask'."""
    f = _num(v)
    return None if (f is None or f < 0) else f


@dataclass
class QuoteTick:
    conid: int
    symbol: str
    exchange: str | None
    last: float | None
    bid: float | None
    ask: float | None
    volume: float | None
    prior_close: float | None
    time: str | None            # ISO UTC of the tick, or None if the feed gave none
    data_type: int | None       # 1 live, 2 frozen, 3 delayed, 4 delayed frozen (per tick type reported by TWS)
    data_type_label: str
    source: str

    def as_dict(self):
        return asdict(self)


class ReadOnlyClient:
    def __init__(self, target: Target, ib=None, stock_factory=None, source: str = SOURCE_REAL):
        self.target = target
        self.source = source
        self.errors = collections.deque(maxlen=200)    # (utc iso, reqId, code, text)
        self.on_error = None                            # optional callback(code, text, reqId)
        self.on_disconnect = None
        if ib is None:
            lib = load_ib_async()
            ib = lib.IB()
            stock_factory = stock_factory or lib.Stock
        elif stock_factory is None:
            lib = load_ib_async()
            stock_factory = lib.Stock
        self.__ib = ib
        self.__stock = stock_factory
        self.__tickers: dict[int, tuple] = {}           # conid -> (ticker, inst)
        self.__insts: dict[int, dict] = {}
        ib.errorEvent += self.__on_error
        ib.disconnectedEvent += self.__on_disc

    # ---------------------------------------------------------------- plumbing
    def __on_error(self, reqId, code, text, *rest):
        self.errors.append((dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), reqId, code, str(text)))
        if self.on_error:
            try:
                self.on_error(code, str(text), reqId)
            except Exception:
                pass

    def __on_disc(self, *a):
        if self.on_disconnect:
            try:
                self.on_disconnect()
            except Exception:
                pass

    def errors_for(self, req_id) -> list[tuple[int, str]]:
        return [(c, t) for (_, r, c, t) in self.errors if r == req_id]

    @property
    def connected(self) -> bool:
        return bool(self.__ib.isConnected())

    # ---------------------------------------------------------------- connection
    async def connect(self, timeout: float = 8.0):
        """Connect with the read-only flag. NOTE: in ib_async that flag only stops the client from asking TWS for order state; the real
        protection is TWS 'Read-Only API' (see docs/IBKR_SETUP.md) plus the fact that this wrapper has no order calls."""
        t = self.target
        await self.__ib.connectAsync(t.host, t.port, clientId=t.client_id, timeout=timeout, readonly=True)

    def disconnect(self):
        for conid in list(self.__tickers):
            self.unsubscribe(conid)
        self.__ib.disconnect()

    async def server_time(self) -> dt.datetime:
        t = await self.__ib.reqCurrentTimeAsync()
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)

    def managed_accounts(self) -> list[str]:
        return list(self.__ib.managedAccounts())

    # ---------------------------------------------------------------- contracts (stocks only)
    async def qualify_stock(self, symbol: str, exchange: str | None = None, currency: str = "USD", conid: int | None = None) -> dict:
        kw = {}
        if conid:
            kw["conId"] = int(conid)
        if exchange:
            kw["primaryExchange"] = exchange
        c = self.__stock(symbol, "SMART", currency, **kw)
        res = await self.__ib.qualifyContractsAsync(c)
        q = res[0] if res else None
        if q is None or isinstance(q, list):
            raise ValueError(f"{symbol}: contract not found or ambiguous (conId {conid}, exchange {exchange}). Pick the instrument by conId + exchange.")
        if getattr(q, "secType", "STK") != "STK":
            raise NotStockError(f"{symbol}: secType {q.secType!r} refused. This app handles stocks/ETFs only.")
        if conid and int(q.conId) != int(conid):
            raise ValueError(f"{symbol}: IBKR resolved conId {q.conId}, expected {conid}. Refusing to guess.")
        inst = {"conid": int(q.conId), "symbol": q.symbol, "exchange": getattr(q, "primaryExchange", None) or exchange, "currency": q.currency, "secType": "STK"}
        self.__insts[inst["conid"]] = {**inst, "_c": q}
        return inst

    def _contract(self, inst):
        known = self.__insts.get(inst["conid"])
        if not known:
            raise KeyError("contract not qualified")
        return known["_c"]

    # ---------------------------------------------------------------- market data
    def set_market_data_type(self, n: int):
        if n not in DATA_TYPE_LABEL:
            raise ValueError("market data type must be 1 (live), 2 (frozen), 3 (delayed) or 4 (delayed frozen)")
        self.__ib.reqMarketDataType(n)

    def subscribe_quote(self, inst: dict):
        if inst["conid"] in self.__tickers:
            return
        tk = self.__ib.reqMktData(self._contract(inst), "", False, False)
        self.__tickers[inst["conid"]] = (tk, inst)

    def unsubscribe(self, conid: int):
        item = self.__tickers.pop(conid, None)
        if item:
            try:
                self.__ib.cancelMktData(self._contract(item[1]))
            except Exception:
                pass

    def subscribed(self) -> list[int]:
        return list(self.__tickers)

    def quote(self, conid: int) -> QuoteTick | None:
        item = self.__tickers.get(conid)
        if not item:
            return None
        tk, inst = item
        last = _price(getattr(tk, "last", None))
        if last is None:
            last = _price(getattr(tk, "delayedLast", None))
        bid = _price(getattr(tk, "bid", None)) if _price(getattr(tk, "bid", None)) is not None else _price(getattr(tk, "delayedBid", None))
        ask = _price(getattr(tk, "ask", None)) if _price(getattr(tk, "ask", None)) is not None else _price(getattr(tk, "delayedAsk", None))
        vol = _num(getattr(tk, "volume", None))
        if vol is None:
            vol = _num(getattr(tk, "delayedVolume", None))
        close = _price(getattr(tk, "close", None))
        if close is None:
            close = _price(getattr(tk, "delayedClose", None))
        t = getattr(tk, "time", None)
        if t is not None and t.tzinfo is None:
            t = t.replace(tzinfo=dt.timezone.utc)
        dtp = getattr(tk, "marketDataType", None)
        dtp = dtp if dtp in DATA_TYPE_LABEL else None
        return QuoteTick(inst["conid"], inst["symbol"], inst.get("exchange"), last, bid, ask, vol, close,
                         t.astimezone(dt.timezone.utc).isoformat(timespec="seconds") if t else None, dtp, DATA_TYPE_LABEL.get(dtp, "unknown"), self.source)

    # ---------------------------------------------------------------- history
    async def historical_bars(self, inst: dict, end: dt.datetime | None, duration: str, bar_size: str, use_rth: bool, what_to_show: str = "TRADES", timeout: float = 90.0):
        """-> (bars, errors). bars: list of {time(datetime UTC | date for daily), open, high, low, close, volume}, oldest first.
        errors: [(code, text)] reported by TWS for this request. An empty list with no errors can still mean 'no data' or a timeout."""
        c = self._contract(inst)
        raw = await self.__ib.reqHistoricalDataAsync(c, end if end is not None else "", duration, bar_size, what_to_show, use_rth, 2, False, [], timeout)
        bars = []
        for b in raw:
            d = b.date
            if isinstance(d, dt.datetime):
                d = d.replace(tzinfo=dt.timezone.utc) if d.tzinfo is None else d.astimezone(dt.timezone.utc)
            bars.append({"time": d, "open": float(b.open), "high": float(b.high), "low": float(b.low), "close": float(b.close), "volume": float(b.volume)})
        bars.sort(key=lambda x: (x["time"] if isinstance(x["time"], dt.datetime) else dt.datetime.combine(x["time"], dt.time(), dt.timezone.utc)))
        return bars, self.errors_for(getattr(raw, "reqId", None))

    async def head_timestamp(self, inst: dict, use_rth: bool = True, what_to_show: str = "TRADES"):
        t = await self.__ib.reqHeadTimeStampAsync(self._contract(inst), what_to_show, use_rth, 2)
        if isinstance(t, dt.datetime) and t.tzinfo is None:
            t = t.replace(tzinfo=dt.timezone.utc)
        return t

    # ---------------------------------------------------------------- account (read)
    async def account_summary(self) -> list[dict]:
        rows = await self.__ib.accountSummaryAsync()
        return [{"account": r.account, "tag": r.tag, "value": r.value, "currency": r.currency} for r in rows]

    def positions(self) -> list[dict]:
        return [{"account": p.account, "conid": int(p.contract.conId), "symbol": p.contract.symbol, "exchange": getattr(p.contract, "primaryExchange", None) or getattr(p.contract, "exchange", None),
                 "secType": p.contract.secType, "currency": p.contract.currency, "position": float(p.position), "avg_cost": float(p.avgCost)} for p in self.__ib.positions()]

    def open_orders(self) -> list[dict]:
        out = []
        for t in self.__ib.openTrades():
            o, s, c = t.order, t.orderStatus, t.contract
            out.append({"order_id": o.orderId, "perm_id": o.permId, "symbol": c.symbol, "conid": int(c.conId), "side": o.action, "qty": float(o.totalQuantity), "type": o.orderType,
                        "limit": _price(o.lmtPrice), "stop": _price(o.auxPrice), "tif": o.tif, "outside_rth": bool(o.outsideRth), "status": s.status,
                        "filled": float(s.filled), "remaining": float(s.remaining)})
        return out

    def executions(self) -> list[dict]:
        out = []
        for f in self.__ib.fills():
            e, c, cr = f.execution, f.contract, f.commissionReport
            comm = _num(getattr(cr, "commission", None)) if cr is not None else None
            out.append({"time": f.time.astimezone(dt.timezone.utc).isoformat(timespec="seconds"), "symbol": c.symbol, "conid": int(c.conId), "side": "buy" if e.side == "BOT" else "sell",
                        "qty": float(e.shares), "price": float(e.price), "commission": comm if (comm is not None and comm < 1e9) else None, "order_id": e.orderId, "exec_id": e.execId, "currency": c.currency})
        return out

    def daily_pnl(self, account: str):
        """-> {'daily': float|None, 'unrealized': float|None, 'realized': float|None}; None when TWS has not reported it."""
        try:
            p = self.__ib.reqPnL(account)
            return {"daily": _num(getattr(p, "dailyPnL", None)), "unrealized": _num(getattr(p, "unrealizedPnL", None)), "realized": _num(getattr(p, "realizedPnL", None))}
        except Exception:
            return {"daily": None, "unrealized": None, "realized": None}
