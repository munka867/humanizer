"""SIMULATED IB for tests and the opt-in CC_IBKR_FAKE=1 server mode. NOT real market data, NOT a real account.

Mimics the small slice of the ib_async IB API that readonly_client.py uses: delayed ticks (marketDataType 3), scripted pacing errors,
disconnects, a fake read-only account, deterministic synthetic history. Every record derived from it carries source='fake'; downloads go to
data/fake_<YYYYMMDD>/ and never to data/processed*. The prices are a deterministic function of the timestamp (so overlapping chunks agree).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import math
import random
from types import SimpleNamespace
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
FAKE_ACCOUNT = "DUFAKE001"   # DU = paper-account prefix, as the real adapter requires
HEAD = dt.datetime(2018, 1, 2, tzinfo=dt.timezone.utc)   # fake "earliest data" so paging ends
BAR_SECS = {"1 min": 60, "5 mins": 300, "15 mins": 900, "1 hour": 3600}


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


class FakeStock(SimpleNamespace):
    def __init__(self, symbol="", exchange="", currency="", **kw):
        super().__init__(secType="STK", symbol=symbol, exchange=exchange, currency=currency, conId=kw.get("conId", 0), primaryExchange=kw.get("primaryExchange", ""))


def _px(conid: int, ts: float) -> float:
    base = 40 + (conid % 400)
    return round(base + 6 * math.sin(ts / 86400 / 9.0) + 3 * math.sin(ts / 86400 / 2.3 + conid) + random.Random(int(ts) ^ conid).uniform(-0.4, 0.4), 2)


def _duration_td(s: str) -> dt.timedelta:
    n, u = s.split()
    n = int(n)
    return {"S": dt.timedelta(seconds=n), "D": dt.timedelta(days=n), "W": dt.timedelta(weeks=n), "M": dt.timedelta(days=31 * n), "Y": dt.timedelta(days=366 * n)}[u]


class FakeIB:
    """Script hooks (all optional, set by tests): refuse_connect=Exception, history_errors=[(code,text)...] consumed one per history call,
    slow=seconds added to each history call."""

    def __init__(self):
        self.errorEvent = _Event()
        self.disconnectedEvent = _Event()
        self._connected = False
        self._types = 1
        self._tickers = {}
        self.refuse_connect = None
        self.history_errors = []
        self.history_calls = []        # (conid, end, duration, bar, rth)
        self.connect_args = None
        self.market_data_type_calls = []
        self._req = 0
        self.account = {"NetLiquidation": ("98765.43", "USD"), "BuyingPower": ("197530.86", "USD"), "TotalCashValue": ("0", "USD"), "GrossPositionValue": ("0", "USD"),
                        "AvailableFunds": ("98765.43", "USD")}
        self.fake_positions = []
        self.fake_fills = []
        self.fake_orders = []
        self.unqualifiable = set()
        self.order_surface_called = False
        self.accounts = [FAKE_ACCOUNT]   # tests set e.g. ["U1234567"] to simulate a LIVE login (must be refused)

    # connection ---------------------------------------------------------------
    async def connectAsync(self, host, port, clientId=1, timeout=4, readonly=False, **kw):
        self.connect_args = {"host": host, "port": port, "clientId": clientId, "readonly": readonly}
        if self.refuse_connect:
            raise self.refuse_connect
        self._connected = True

    def disconnect(self):
        was = self._connected
        self._connected = False
        if was:
            self.disconnectedEvent.emit()

    def drop(self, code=1100, text="Connectivity between IB and TWS has been lost."):
        """Simulate TWS losing its connection (test hook)."""
        self.errorEvent.emit(-1, code, text, None)
        self._connected = False
        self.disconnectedEvent.emit()

    def isConnected(self):
        return self._connected

    def managedAccounts(self):
        return list(self.accounts)

    async def reqCurrentTimeAsync(self):
        return dt.datetime.now(dt.timezone.utc)

    # contracts ----------------------------------------------------------------
    async def qualifyContractsAsync(self, *cs, **kw):
        out = []
        for c in cs:
            if c.symbol in self.unqualifiable:
                out.append(None)
                continue
            cid = c.conId or (100000 + sum(map(ord, c.symbol)))
            out.append(SimpleNamespace(secType=getattr(c, "secType", "STK"), symbol=c.symbol, conId=cid, exchange="SMART", primaryExchange=c.primaryExchange or "ARCA", currency=c.currency or "USD"))
        return out

    # market data --------------------------------------------------------------
    def reqMarketDataType(self, n):
        self.market_data_type_calls.append(n)
        self._types = n

    def reqMktData(self, contract, *a, **k):
        tk = SimpleNamespace(contract=contract, last=float("nan"), bid=float("nan"), ask=float("nan"), volume=float("nan"), close=float("nan"), time=None,
                             marketDataType=1, delayedLast=float("nan"), delayedBid=float("nan"), delayedAsk=float("nan"), delayedVolume=float("nan"), delayedClose=float("nan"))
        now = dt.datetime.now(dt.timezone.utc)
        tk.marketDataType = self._types if self._types in (1, 2, 3, 4) else 1
        p = _px(contract.conId, now.timestamp() - (900 if tk.marketDataType in (3, 4) else 0))
        tk.last, tk.bid, tk.ask = p, round(p - 0.01, 2), round(p + 0.01, 2)
        tk.volume, tk.close = 1_234_567.0, _px(contract.conId, now.timestamp() - 86400)
        tk.time = now - dt.timedelta(minutes=15 if tk.marketDataType in (3, 4) else 0)
        self._tickers[contract.conId] = tk
        return tk

    def cancelMktData(self, contract):
        return self._tickers.pop(contract.conId, None) is not None

    # history ------------------------------------------------------------------
    async def reqHistoricalDataAsync(self, contract, endDateTime, durationStr, barSizeSetting, whatToShow, useRTH, formatDate=1, keepUpToDate=False, chartOptions=None, timeout=60):
        self._req += 1
        bars = _Bars()
        bars.reqId = self._req
        end = endDateTime if isinstance(endDateTime, dt.datetime) else dt.datetime.now(dt.timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=dt.timezone.utc)
        self.history_calls.append((contract.conId, end, durationStr, barSizeSetting, bool(useRTH)))
        if self.history_errors:
            code, text = self.history_errors.pop(0)
            self.errorEvent.emit(bars.reqId, code, text, contract)
            return bars
        start = end - _duration_td(durationStr)
        out = []
        if barSizeSetting == "1 day":
            d = start.astimezone(NY).date()
            last = end.astimezone(NY).date()
            while d <= last:
                if d.weekday() < 5 and dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc) >= HEAD:
                    out.append(self._bar(contract.conId, d, dt.datetime.combine(d, dt.time(14, 30), dt.timezone.utc), 86400))
                d += dt.timedelta(days=1)
        else:
            step = BAR_SECS[barSizeSetting]
            lo, hi = (dt.time(9, 30), dt.time(16, 0)) if useRTH else (dt.time(4, 0), dt.time(20, 0))
            d = start.astimezone(NY).date()
            last = end.astimezone(NY).date()
            while d <= last:
                if d.weekday() < 5:
                    t = dt.datetime.combine(d, lo, NY)
                    stop = dt.datetime.combine(d, hi, NY)
                    while t < stop:
                        tu = t.astimezone(dt.timezone.utc)
                        if start < tu <= end and tu >= HEAD:
                            out.append(self._bar(contract.conId, tu, tu, step))
                        t += dt.timedelta(seconds=step)
                d += dt.timedelta(days=1)
        if not out:
            self.errorEvent.emit(bars.reqId, 162, "Historical Market Data Service error message:HMDS query returned no data", contract)
        bars.extend(out)
        return bars

    @staticmethod
    def _bar(conid, date, ts, step):
        t = ts.timestamp()
        o, c = _px(conid, t), _px(conid, t + step)
        hi, lo = max(o, c) + 0.15, min(o, c) - 0.15
        return SimpleNamespace(date=date if step == 86400 else ts, open=o, high=round(hi, 2), low=round(lo, 2), close=c, volume=float(int(abs(c * 1000)) % 90000 + 1000), average=c, barCount=10)

    async def reqHeadTimeStampAsync(self, contract, whatToShow, useRTH, formatDate=1):
        return HEAD

    # account (read) ------------------------------------------------------------
    async def accountSummaryAsync(self, account=""):
        return [SimpleNamespace(account=FAKE_ACCOUNT, tag=k, value=v[0], currency=v[1], modelCode="") for k, v in self.account.items()]

    def positions(self, account=""):
        return list(self.fake_positions)

    def openTrades(self):
        return list(self.fake_orders)

    def fills(self):
        return list(self.fake_fills)

    def reqPnL(self, account, modelCode=""):
        return SimpleNamespace(dailyPnL=float("nan"), unrealizedPnL=float("nan"), realizedPnL=float("nan"))


class _Bars(list):
    reqId = 0
