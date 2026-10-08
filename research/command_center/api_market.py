"""MARKET backend: bars, quotes, watchlists, portfolio (not connected), signals, markers, market status.

READ-ONLY market data from the IBKR connector's daily files. STOCKS/ETFs only. No broker/trading imports, no endpoint that
places, modifies or cancels orders. Anything without an entitled source is reported as unavailable, never invented.

Aggregation rules (1W / 1M), all from daily bars:
  * session date = bar ts_open converted to America/New_York (date part).
  * week = ISO week (Mon-Sun) of the session date; month = calendar month of the session date.
  * open = first daily open, high = max high, low = min low, close = last daily close, volume = sum, time = first bar's ts_open.
  * the LAST period is flagged "partial": true when it may still receive bars (New York today <= period end date) and its last
    bar is not the final weekday of the period. Holidays are not modelled, so a holiday-shortened week that has ended is
    reported as complete only once New York today is past the period end.
Bar timestamps are session-open instants (bar_time_label 'session'); daily bars carry no intraday information.
"""
from __future__ import annotations
import calendar
import csv
import datetime as dt
import json
import re
import sys
import threading
from pathlib import Path
from zoneinfo import ZoneInfo

from . import router
from .router import route

NY = ZoneInfo("America/New_York")
RESEARCH_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = RESEARCH_ROOT  # tests monkeypatch this
UNAVAILABLE = "unavailable: no entitled source connected"
STALE_DAYS = 4
INTERVALS = ("1D", "1W", "1M")
_SYM_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_EX_RE = re.compile(r"^[A-Z0-9]{2,12}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_cache: dict = {}
_lock = threading.Lock()


def _now() -> dt.datetime:
    """Current instant (aware, UTC). Tests monkeypatch this."""
    return dt.datetime.now(dt.timezone.utc)


class ApiError(Exception):
    def __init__(self, code, msg, **extra):
        super().__init__(msg)
        self.code, self.msg, self.extra = code, msg, extra


def _guard(h, fn):
    try:
        return fn()
    except ApiError as e:
        return h._err(e.code, e.msg, **e.extra)


# ------------------------------------------------------------------ instruments & bars
def instruments() -> list[dict]:
    mf = Path(DATA_ROOT) / "data" / "raw" / "ibkr_connector" / "MANIFEST.json"
    try:
        rows = json.loads(mf.read_text())
    except (OSError, ValueError):
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        try:
            csv_path = Path(DATA_ROOT) / r["processed_file"]
            out.append({"symbol": str(r["symbol"]).upper(), "conid": int(r["contract_id"]), "exchange": str(r["exchange"]).upper(),
                        "retrieved_on": r.get("retrieved_on"), "delayed_seconds": r.get("delayed_seconds"),
                        "adjusted_for_dividends": bool(r.get("adjusted_for_dividends", False)),
                        "csv": csv_path, "data_present": csv_path.is_file()})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _public(ins):
    return {"symbol": ins["symbol"], "conid": ins["conid"], "exchange": ins["exchange"], "currency": "USD", "data_present": ins["data_present"]}


def resolve(symbol, exchange=None, conid=None) -> dict:
    ins = instruments()
    if conid not in (None, ""):
        try:
            conid = int(conid)
        except (TypeError, ValueError):
            raise ApiError(400, "conid must be an integer") from None
    if exchange not in (None, ""):
        exchange = str(exchange).upper()
        if not _EX_RE.match(exchange):
            raise ApiError(400, "invalid exchange")
    else:
        exchange = None
    if symbol in (None, "") and conid is None:
        raise ApiError(400, "symbol is required")
    sym = None
    if symbol not in (None, ""):
        sym = str(symbol).strip().upper()
        if not _SYM_RE.match(sym):
            raise ApiError(400, "invalid symbol")
    m = [i for i in ins if (sym is None or i["symbol"] == sym) and (conid is None or i["conid"] == conid)
         and (exchange is None or i["exchange"] == exchange)]
    if not m:
        raise ApiError(404, "unknown instrument", reason="not in the connector manifest; only instruments with downloaded daily data are available",
                       available=[_public(i) for i in ins])
    if len(m) > 1:
        raise ApiError(400, "ambiguous ticker: specify exchange (or conid)", candidates=[_public(i) for i in m])
    return m[0]


def _load(ins) -> list[dict]:
    p = ins["csv"]
    if not p.is_file():
        raise ApiError(404, "no data file present for this instrument", reason="processed CSV is git-ignored and missing locally")
    key = (str(p), p.stat().st_mtime_ns)
    with _lock:
        if key in _cache:
            return _cache[key]
    rows = []
    with p.open(newline="") as f:
        for r in csv.DictReader(f):
            try:
                ts = dt.datetime.fromisoformat(r["ts_open"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=dt.timezone.utc)
                rows.append({"ts": ts, "date": ts.astimezone(NY).date(), "open": float(r["open"]), "high": float(r["high"]),
                             "low": float(r["low"]), "close": float(r["close"]), "volume": float(r["volume"])})
            except (KeyError, ValueError):
                continue
    rows.sort(key=lambda x: x["ts"])
    with _lock:
        _cache.clear()
        _cache[key] = rows
    return rows


def _num(v):
    return int(v) if float(v).is_integer() and abs(v) < 2**53 else v


def _bar(ts, o, h, l, c, v):
    return {"time": int(ts.timestamp()), "open": o, "high": h, "low": l, "close": c, "volume": _num(v)}


def _period_end(d: dt.date, interval: str) -> dt.date:
    if interval == "1W":
        return d + dt.timedelta(days=6 - d.weekday())
    return dt.date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def _last_weekday(end: dt.date) -> dt.date:
    while end.weekday() > 4:
        end -= dt.timedelta(days=1)
    return end


def aggregate(rows: list[dict], interval: str, today: dt.date) -> list[dict]:
    """Daily -> weekly/monthly per the module docstring. `rows` sorted ascending."""
    if interval == "1D":
        out = [_bar(r["ts"], r["open"], r["high"], r["low"], r["close"], r["volume"]) for r in rows]
        return out
    groups: list[list[dict]] = []
    last_key = None
    for r in rows:
        d = r["date"]
        k = d.isocalendar()[:2] if interval == "1W" else (d.year, d.month)
        if k != last_key:
            groups.append([])
            last_key = k
        groups[-1].append(r)
    out = []
    for g in groups:
        out.append(_bar(g[0]["ts"], g[0]["open"], max(x["high"] for x in g), min(x["low"] for x in g), g[-1]["close"], sum(x["volume"] for x in g)))
    if groups:
        end = _period_end(groups[-1][-1]["date"], interval)
        if today <= end and groups[-1][-1]["date"] < _last_weekday(end):
            out[-1]["partial"] = True
    return out


def _date_param(q, name):
    v = q.get(name)
    if v in (None, ""):
        return None
    if not _DATE_RE.match(v):
        raise ApiError(400, f"{name} must be YYYY-MM-DD")
    try:
        return dt.date.fromisoformat(v)
    except ValueError:
        raise ApiError(400, f"{name} is not a valid date") from None


@route("GET", r"/api/bars")
def api_bars(h, m, q, body):
    def go():
        interval = (q.get("interval") or "1D").upper()
        if interval not in INTERVALS:
            raise ApiError(400, "interval must be one of 1D, 1W, 1M", intraday="unavailable: no intraday history source connected")
        d_from, d_to = _date_param(q, "from"), _date_param(q, "to")
        if d_from and d_to and d_from > d_to:
            raise ApiError(400, "from must not be after to")
        ins = resolve(q.get("symbol"), q.get("exchange"), q.get("conid"))
        rows = _load(ins)
        today = _now().astimezone(NY).date()
        bars = aggregate(rows, interval, today)
        # from/to filter on the session date of the bar's first daily row
        if d_from or d_to:
            keep = []
            for b in bars:
                d = dt.datetime.fromtimestamp(b["time"], dt.timezone.utc).astimezone(NY).date()
                if (not d_from or d >= d_from) and (not d_to or d <= d_to):
                    keep.append(b)
            bars = keep
        meta = {"symbol": ins["symbol"], "conid": ins["conid"], "exchange": ins["exchange"], "currency": "USD",
                "source": "IBKR connector (daily files)", "feed": "delayed", "delayed_seconds": ins["delayed_seconds"],
                "retrieved_on": ins["retrieved_on"], "adjusted_for_dividends": ins["adjusted_for_dividends"], "session": "regular",
                "timezone": "America/New_York", "bar_time_label": "session", "interval": interval,
                "first": bars[0]["time"] if bars else None, "last": bars[-1]["time"] if bars else None, "n": len(bars)}
        return h._json(200, {"meta": meta, "bars": bars,
                             "limits": {"intervals": list(INTERVALS), "intraday": "unavailable: no intraday history source connected",
                                        "extended_hours": "unavailable"}})
    return _guard(h, go)


# ------------------------------------------------------------------ quotes
def quote_for(ins, today: dt.date) -> dict:
    rows = _load(ins)
    if len(rows) < 2:
        raise ApiError(404, "fewer than two daily bars", reason="cannot compute change vs previous close")
    last, prev = rows[-1], rows[-2]
    change = last["close"] - prev["close"]
    age = (today - last["date"]).days
    q = {"symbol": ins["symbol"], "conid": ins["conid"], "exchange": ins["exchange"], "currency": "USD",
         "last": last["close"], "prev_close": prev["close"], "change": round(change, 6),
         "pct_change": round(change / prev["close"] * 100, 6) if prev["close"] else None,
         "volume": _num(last["volume"]), "as_of": last["date"].isoformat(), "prev_close_date": prev["date"].isoformat(),
         "freshness": "historical", "stale": age > STALE_DAYS, "session": "regular", "delayed_seconds": ins["delayed_seconds"],
         "source": "IBKR connector (daily files)", "last_is": "close of the latest daily bar (not a streaming quote)",
         "extended_hours": "unavailable: change is regular-session close vs previous regular-session close only"}
    if q["stale"]:
        q["stale_reason"] = f"as_of {q['as_of']} is {age} calendar days before {today.isoformat()} (limit {STALE_DAYS})"
    return q


@route("GET", r"/api/quotes")
def api_quotes(h, m, q, body):
    def go():
        raw = q.get("symbols") or ""
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if not parts:
            raise ApiError(400, "symbols is required, e.g. symbols=SPY,QQQ (SYMBOL:EXCHANGE to disambiguate)")
        if len(parts) > 20:
            raise ApiError(400, "at most 20 symbols")
        today = _now().astimezone(NY).date()
        quotes, errors = [], []
        for p in parts:
            sym, _, ex = p.partition(":")
            try:
                quotes.append(quote_for(resolve(sym, ex or None), today))
            except ApiError as e:
                errors.append({"symbol": sym.upper(), "error": e.msg, **e.extra})
        return h._json(200, {"quotes": quotes, "errors": errors, "server_date_ny": today.isoformat(),
                             "note": "Historical daily closes only; no streaming quotes. Extended-hours change: " + UNAVAILABLE})
    return _guard(h, go)


# ------------------------------------------------------------------ watchlists
def _store(h):
    from .market_store import MarketStore, db_path_for
    ms = MarketStore(db_path_for(h.server.store.path))
    ms.seed_if_needed(instruments())
    return ms


def _audit(h, ms, detail: dict):
    """Emit 'audit.config' only if schemas.py defines it; otherwise record in the local audit table."""
    try:
        from . import schemas
        if "audit.config" in getattr(schemas, "EVENT_TYPES", ()) and "audit.config" in getattr(schemas, "SCHEMAS", {}):
            res = h.server.store.insert([{"event_type": "audit.config", "source": "market", "payload": {"area": "watchlists", **detail}}])
            if res.get("accepted"):
                return "event"
    except Exception:  # audit must never break the write; fall back to the local table
        pass
    ms.record_audit("watchlist." + detail.get("action", "?"), detail)
    return "local"


def _wl_payload(ms):
    return {"watchlists": ms.lists(), "available_instruments": [_public(i) for i in instruments()],
            "note": "Watchlist contents are instruments with data available in this app, not recommendations."}


@route("GET", r"/api/watchlists")
def api_watchlists(h, m, q, body):
    return h._json(200, _wl_payload(_store(h)))


def _wrap_store(h, fn):
    from .market_store import StoreError
    try:
        return fn()
    except StoreError as e:
        return h._err(e.code, str(e))
    except ApiError as e:
        return h._err(e.code, e.msg, **e.extra)


@route("POST", r"/api/watchlists")
def api_watchlists_post(h, m, q, body):
    def go():
        if not isinstance(body, dict):
            raise ApiError(400, "body must be an object")
        act, ms = body.get("action"), _store(h)
        if act == "create":
            d = ms.create(body.get("name"))
        elif act == "rename":
            d = ms.rename(body.get("id"), body.get("name"))
        elif act == "delete":
            d = ms.delete(body.get("id"))
        else:
            raise ApiError(400, "action must be create, rename or delete")
        d["audit"] = _audit(h, ms, dict(d))
        return h._json(200, {"ok": True, "result": d, **_wl_payload(ms)})
    return _wrap_store(h, go)


@route("POST", r"/api/watchlists/([A-Za-z0-9_\-]{1,64})/items")
def api_watchlist_items(h, m, q, body):
    def go():
        if not isinstance(body, dict):
            raise ApiError(400, "body must be an object")
        wid, act, ms = m.group(1), body.get("action"), _store(h)

        def key(o):
            if not isinstance(o, dict) or isinstance(o.get("conid"), bool) or not isinstance(o.get("conid"), int) or not isinstance(o.get("exchange"), str):
                raise ApiError(400, "conid (integer) and exchange (string) are required")
            ex = o["exchange"].upper()
            hit = [i for i in instruments() if i["conid"] == o["conid"] and i["exchange"] == ex]
            if not hit:
                raise ApiError(404, "instrument not available", reason="only instruments with data available in this app can be added")
            return hit[0]
        if act == "add":
            d = ms.add_item(wid, key(body))
        elif act == "remove":
            ins = key(body)
            d = ms.remove_item(wid, ins["conid"], ins["exchange"])
        elif act == "reorder":
            order = body.get("order")
            if not isinstance(order, list) or len(order) > 100:
                raise ApiError(400, "order must be a list of {conid, exchange}")
            d = ms.reorder(wid, [(key(o)["conid"], key(o)["exchange"]) for o in order])
        else:
            raise ApiError(400, "action must be add, remove or reorder")
        d["audit"] = _audit(h, ms, dict(d))
        return h._json(200, {"ok": True, "result": d, **_wl_payload(ms)})
    return _wrap_store(h, go)


# ------------------------------------------------------------------ portfolio / signals / markers
def _metric(name):
    return {"value": None, "reason": f"unavailable: no broker adapter implemented; {name} is unknown, not zero", "currency": None, "as_of": None}


@route("GET", r"/api/portfolio")
def api_portfolio(h, m, q, body):
    why = "unavailable: no broker adapter implemented"
    return h._json(200, {
        "broker": {"state": "not_connected", "reason": "no broker adapter implemented", "mode": "none"},
        "positions": [], "working_orders": [], "fills": [],
        "unavailable_reason": why,
        "metrics": {k: _metric(k) for k in ("equity", "daily_pnl", "buying_power", "exposure", "risk_usage")},
        "note": ("The connected IBKR account was read on 2026-10-08 and was empty/new (all zero), but this app has no live account "
                 "feed, so nothing here reflects account state. Unknown values are null, never zero. Read-only: this API cannot place, "
                 "modify or cancel orders."),
    })


@route("GET", r"/api/signals")
def api_signals(h, m, q, body):
    return h._json(200, {"signals": [], "reason": "no approved strategy"})


@route("GET", r"/api/markers")
def api_markers(h, m, q, body):
    def go():
        ins = resolve(q.get("symbol"), q.get("exchange"), q.get("conid"))
        return h._json(200, {"symbol": ins["symbol"], "conid": ins["conid"], "exchange": ins["exchange"], "markers": [],
                             "reason": "no real trade records exist; markers are only ever derived from real trades"})
    return _guard(h, go)


# ------------------------------------------------------------------ market status
def _holiday_candidates(year: int):
    try:
        src = str(RESEARCH_ROOT / "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        from tradelab.data.calendar import holiday_candidates
        return holiday_candidates(year)
    except Exception:
        return None


def market_status(now_utc: dt.datetime) -> dict:
    t = now_utc.astimezone(NY)
    d = t.date()
    open_t, close_t = dt.time(9, 30), dt.time(16, 0)
    weekday = d.weekday() < 5
    clock_open = weekday and open_t <= t.time() < close_t
    cands = _holiday_candidates(d.year)
    cand = None if cands is None else d in cands
    if not weekday:
        state, why = "closed", "weekend"
    elif clock_open:
        state, why = "open", "within 09:30-16:00 America/New_York"
    else:
        state, why = "closed", "before 09:30" if t.time() < open_t else "after 16:00"
    clock_state = state
    if cand and weekday:
        state, why = "unverified", "date is a holiday/early-close candidate; NYSE schedule not verified"
    nxt = t.replace(hour=9, minute=30, second=0, microsecond=0)
    if t >= nxt:
        nxt += dt.timedelta(days=1)
    while nxt.weekday() > 4:
        nxt += dt.timedelta(days=1)
    return {"exchange": "NYSE", "session": "regular", "state": state, "clock_state": clock_state, "reason": why,
            "now_ny": t.isoformat(), "timezone": "America/New_York", "regular_hours": "09:30-16:00 Mon-Fri",
            "next_regular_open_ny_clock_only": nxt.isoformat(),
            "holiday_candidate": cand, "schedule_verified": False,
            "schedule_note": ("schedule unverified: holidays come from tradelab data/calendar.py candidates (not an NYSE feed); early closes "
                              "(e.g. 13:00) are not modelled and are flagged unverified" if cands is not None else
                              "schedule unverified: holiday calendar unavailable; weekday clock only; early closes not modelled"),
            "extended_hours": "unavailable"}


@route("GET", r"/api/market/status")
def api_market_status(h, m, q, body):
    return h._json(200, market_status(_now()))
