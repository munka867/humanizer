"""Market backend: bars, quotes, watchlists, portfolio, status. Uses SYNTHETIC_* fixtures generated into tmp (software test only)."""
import ast
import datetime as dt
import http.client
import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from command_center import api_market, router  # noqa: E402
from command_center.server import make_server  # noqa: E402

TOKEN = "test-token"

# date, open, high, low, close, volume  (SYNTHETIC_SYNA; session dates in New York, ts_open 13:30 UTC = EDT)
SYNA = [("2026-09-28", 100, 105, 99, 104, 10), ("2026-09-29", 104, 108, 103, 107, 20), ("2026-09-30", 107, 110, 102, 103, 30),
        ("2026-10-01", 103, 104, 95, 96, 40), ("2026-10-02", 96, 99, 94, 98, 50), ("2026-10-05", 98, 101, 97, 100, 60),
        ("2026-10-06", 100, 103, 99, 102, 70), ("2026-10-07", 102, 106, 101, 105, 80)]
SYNB = [("2026-10-06", 50, 51, 49, 50.5, 1000), ("2026-10-07", 50.5, 52, 50, 51.25, 2000)]
MANIFEST = [
    {"symbol": "SYNA", "contract_id": 1001, "exchange": "ARCA", "retrieved_on": "2026-10-08", "delayed_seconds": 900,
     "adjusted_for_dividends": False, "processed_file": "data/processed/SYNTHETIC_SYNA_1d.csv"},
    {"symbol": "SYNB", "contract_id": 1002, "exchange": "NASDAQ", "retrieved_on": "2026-10-08", "delayed_seconds": 900,
     "adjusted_for_dividends": False, "processed_file": "data/processed/SYNTHETIC_SYNB_1d.csv"},
    {"symbol": "DUP", "contract_id": 1003, "exchange": "ARCA", "retrieved_on": "2026-10-08", "delayed_seconds": 900,
     "adjusted_for_dividends": False, "processed_file": "data/processed/SYNTHETIC_DUPA_1d.csv"},
    {"symbol": "DUP", "contract_id": 1004, "exchange": "NYSE", "retrieved_on": "2026-10-08", "delayed_seconds": 900,
     "adjusted_for_dividends": False, "processed_file": "data/processed/SYNTHETIC_DUPN_1d.csv"},
]


def write_csv(path, rows, sym):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["ts_open,open,high,low,close,volume,contract"]
    lines += [f"{d} 13:30:00+00:00,{o},{h},{l},{c},{float(v)},{sym}" for d, o, h, l, c, v in rows]
    path.write_text("\n".join(lines) + "\n")


@pytest.fixture()
def data_root(tmp_path, monkeypatch):
    root = tmp_path / "root"
    (root / "data" / "raw" / "ibkr_connector").mkdir(parents=True)
    (root / "data" / "raw" / "ibkr_connector" / "MANIFEST.json").write_text(json.dumps(MANIFEST))
    write_csv(root / "data/processed/SYNTHETIC_SYNA_1d.csv", SYNA, "SYNA")
    write_csv(root / "data/processed/SYNTHETIC_SYNB_1d.csv", SYNB, "SYNB")
    write_csv(root / "data/processed/SYNTHETIC_DUPA_1d.csv", SYNB, "DUP")
    write_csv(root / "data/processed/SYNTHETIC_DUPN_1d.csv", SYNB, "DUP")
    monkeypatch.setattr(api_market, "DATA_ROOT", root)
    monkeypatch.setattr(api_market, "_now", lambda: dt.datetime(2026, 10, 8, 15, 0, tzinfo=dt.timezone.utc))
    return root


def start(db):
    s = make_server(db, port=0, token=TOKEN)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s


def stop(s):
    s.stopping = True
    s.shutdown()
    s.server_close()


@pytest.fixture()
def srv(tmp_path, data_root):
    s = start(tmp_path / "ev" / "e.db")
    yield s
    stop(s)


def call(srv, method, path, body=None, token=TOKEN):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    h = {"Content-Type": "application/json"}
    if token:
        h["X-CC-Token"] = token
    c.request(method, path, json.dumps(body) if body is not None else None, h)
    r = c.getresponse()
    raw = r.read()
    return r.status, json.loads(raw)


def ts(d):
    return int(dt.datetime.fromisoformat(d + " 13:30:00+00:00").timestamp())


# ---------------------------------------------------------------- bars
def test_bars_daily_match_csv(srv):
    st, j = call(srv, "GET", "/api/bars?symbol=SYNA&exchange=ARCA&interval=1D")
    assert st == 200 and len(j["bars"]) == len(SYNA)
    for b, (d, o, h, l, c, v) in zip(j["bars"], SYNA):
        assert b == {"time": ts(d), "open": o, "high": h, "low": l, "close": c, "volume": v}
    m = j["meta"]
    assert (m["symbol"], m["conid"], m["exchange"], m["currency"], m["feed"], m["delayed_seconds"]) == ("SYNA", 1001, "ARCA", "USD", "delayed", 900)
    assert m["adjusted_for_dividends"] is False and m["session"] == "regular" and m["timezone"] == "America/New_York"
    assert m["bar_time_label"] == "session" and m["n"] == 8 and m["first"] == ts("2026-09-28") and m["last"] == ts("2026-10-07")
    assert m["source"] == "IBKR connector (daily files)"
    assert j["limits"]["intervals"] == ["1D", "1W", "1M"] and j["limits"]["intraday"].startswith("unavailable")
    assert j["limits"]["extended_hours"] == "unavailable"


def test_bars_weekly_hand_computed(srv):
    j = call(srv, "GET", "/api/bars?symbol=SYNA&exchange=ARCA&interval=1W")[1]
    assert j["bars"] == [
        {"time": ts("2026-09-28"), "open": 100, "high": 110, "low": 94, "close": 98, "volume": 150},
        {"time": ts("2026-10-05"), "open": 98, "high": 106, "low": 97, "close": 105, "volume": 210, "partial": True}]


def test_bars_monthly_hand_computed(srv):
    j = call(srv, "GET", "/api/bars?symbol=SYNA&interval=1M")[1]
    assert j["bars"] == [
        {"time": ts("2026-09-28"), "open": 100, "high": 110, "low": 99, "close": 103, "volume": 60},
        {"time": ts("2026-10-01"), "open": 103, "high": 106, "low": 94, "close": 105, "volume": 300, "partial": True}]


def test_last_period_not_partial_when_complete(srv, monkeypatch):
    monkeypatch.setattr(api_market, "_now", lambda: dt.datetime(2026, 11, 20, 15, 0, tzinfo=dt.timezone.utc))
    j = call(srv, "GET", "/api/bars?symbol=SYNA&interval=1W")[1]
    assert all("partial" not in b for b in j["bars"])


def test_bars_from_to_and_validation(srv):
    j = call(srv, "GET", "/api/bars?symbol=SYNA&from=2026-10-01&to=2026-10-05")[1]
    assert [b["time"] for b in j["bars"]] == [ts("2026-10-01"), ts("2026-10-02"), ts("2026-10-05")]
    for bad in ("interval=5m", "from=nope", "from=2026-13-40", "from=2026-10-05&to=2026-10-01"):
        st, j = call(srv, "GET", f"/api/bars?symbol=SYNA&{bad}")
        assert st == 400 and j["ok"] is False and j["error"]
    assert call(srv, "GET", "/api/bars?symbol=SY%20A;")[0] == 400
    assert call(srv, "GET", "/api/bars")[0] == 400


def test_unknown_symbol_404(srv):
    st, j = call(srv, "GET", "/api/bars?symbol=ZZZZ")
    assert st == 404 and j["ok"] is False and j["reason"]
    assert call(srv, "GET", "/api/bars?symbol=SYNA&exchange=NYSE")[0] == 404  # right ticker, wrong venue


def test_conid_exchange_disambiguation(srv):
    st, j = call(srv, "GET", "/api/bars?symbol=DUP")
    assert st == 400 and "exchange" in j["error"] and len(j["candidates"]) == 2
    a = call(srv, "GET", "/api/bars?symbol=DUP&exchange=NYSE")[1]["meta"]
    b = call(srv, "GET", "/api/bars?symbol=DUP&exchange=ARCA")[1]["meta"]
    assert (a["conid"], b["conid"]) == (1004, 1003)
    assert call(srv, "GET", "/api/bars?conid=1003")[1]["meta"]["exchange"] == "ARCA"


def test_missing_csv_is_404_not_empty(srv, data_root):
    (data_root / "data/processed/SYNTHETIC_SYNB_1d.csv").unlink()
    st, j = call(srv, "GET", "/api/bars?symbol=SYNB")
    assert st == 404 and j["reason"]


# ---------------------------------------------------------------- quotes
def test_quotes_change_vs_prior_close(srv):
    j = call(srv, "GET", "/api/quotes?symbols=SYNA,SYNB")[1]
    a, b = j["quotes"]
    assert (a["last"], a["prev_close"], a["change"]) == (105, 102, 3)
    assert a["pct_change"] == pytest.approx(3 / 102 * 100, abs=1e-5)
    assert (a["as_of"], a["prev_close_date"], a["volume"]) == ("2026-10-07", "2026-10-06", 80)
    assert a["freshness"] == "historical" and a["stale"] is False and a["session"] == "regular"
    assert a["extended_hours"].startswith("unavailable")
    assert b["change"] == pytest.approx(0.75)
    assert "streaming" in j["note"]


def test_quotes_stale_logic(srv, monkeypatch):
    for day, stale in ((11, False), (12, True)):  # as_of 10-07: 4 days -> fresh, 5 days -> stale
        monkeypatch.setattr(api_market, "_now", lambda day=day: dt.datetime(2026, 10, day, 15, 0, tzinfo=dt.timezone.utc))
        q = call(srv, "GET", "/api/quotes?symbols=SYNA")[1]["quotes"][0]
        assert q["stale"] is stale and q["freshness"] == "historical"
        assert ("stale_reason" in q) is stale


def test_quotes_errors_not_fabricated(srv):
    j = call(srv, "GET", "/api/quotes?symbols=SYNA,NOPE,DUP")[1]
    assert [q["symbol"] for q in j["quotes"]] == ["SYNA"]
    assert {e["symbol"] for e in j["errors"]} == {"NOPE", "DUP"}
    assert call(srv, "GET", "/api/quotes")[0] == 400
    assert call(srv, "GET", "/api/quotes?symbols=DUP:NYSE")[1]["quotes"][0]["conid"] == 1004


# ---------------------------------------------------------------- watchlists
def post(srv, path, body, token=TOKEN):
    return call(srv, "POST", path, body, token)


def test_watchlist_seed_and_crud(srv):
    j = call(srv, "GET", "/api/watchlists")[1]
    assert len(j["watchlists"]) == 1 and j["watchlists"][0]["name"] == "Research data (3 ETFs)"
    seed = j["watchlists"][0]
    assert "not recommendations" in seed["note"].lower() and len(seed["items"]) == 4
    st, r = post(srv, "/api/watchlists", {"action": "create", "name": "Mine"})
    assert st == 200
    wid = r["result"]["id"]
    assert r["result"]["audit"] in ("event", "local")
    assert post(srv, "/api/watchlists", {"action": "create", "name": "mine"})[0] == 409
    assert post(srv, "/api/watchlists", {"action": "create", "name": ""})[0] == 400
    assert post(srv, "/api/watchlists", {"action": "nope"})[0] == 400
    assert post(srv, "/api/watchlists", {"action": "rename", "id": wid, "name": "Renamed"})[0] == 200
    base = f"/api/watchlists/{wid}/items"
    assert post(srv, base, {"action": "add", "conid": 1001, "exchange": "ARCA"})[0] == 200
    assert post(srv, base, {"action": "add", "conid": 1002, "exchange": "NASDAQ"})[0] == 200
    assert post(srv, base, {"action": "add", "conid": 1001, "exchange": "ARCA"})[0] == 409
    assert post(srv, base, {"action": "add", "conid": 9999, "exchange": "ARCA"})[0] == 404  # not an available instrument
    assert post(srv, base, {"action": "add", "conid": 1001, "exchange": "NYSE"})[0] == 404  # conid+exchange pair, not ticker
    assert post(srv, base, {"action": "add", "conid": "1001", "exchange": "ARCA"})[0] == 400
    st, r = post(srv, base, {"action": "reorder", "order": [{"conid": 1002, "exchange": "NASDAQ"}, {"conid": 1001, "exchange": "ARCA"}]})
    assert st == 200
    mine = [w for w in r["watchlists"] if w["id"] == wid][0]
    assert [i["symbol"] for i in mine["items"]] == ["SYNB", "SYNA"] and mine["name"] == "Renamed"
    assert post(srv, base, {"action": "reorder", "order": [{"conid": 1002, "exchange": "NASDAQ"}]})[0] == 400  # incomplete
    assert post(srv, base, {"action": "remove", "conid": 1002, "exchange": "NASDAQ"})[0] == 200
    assert post(srv, base, {"action": "remove", "conid": 1002, "exchange": "NASDAQ"})[0] == 404
    assert post(srv, "/api/watchlists/unknownid/items", {"action": "add", "conid": 1001, "exchange": "ARCA"})[0] == 404
    assert post(srv, "/api/watchlists", {"action": "delete", "id": wid})[0] == 200
    assert [w["name"] for w in call(srv, "GET", "/api/watchlists")[1]["watchlists"]] == ["Research data (3 ETFs)"]


def test_watchlists_persist_across_restart_and_seed_once(tmp_path, data_root):
    db = tmp_path / "ev" / "e.db"
    s = start(db)
    try:
        wid = post(s, "/api/watchlists", {"action": "create", "name": "Keep"})[1]["result"]["id"]
        post(s, f"/api/watchlists/{wid}/items", {"action": "add", "conid": 1002, "exchange": "NASDAQ"})
        post(s, "/api/watchlists", {"action": "delete", "id": "research-data-3-etfs"})
    finally:
        stop(s)
    assert (tmp_path / "ev" / "market.sqlite3").is_file()
    s = start(db)
    try:
        wl = call(s, "GET", "/api/watchlists")[1]["watchlists"]
        assert [w["name"] for w in wl] == ["Keep"]  # deleted seed is not re-created
        assert [i["conid"] for i in wl[0]["items"]] == [1002]
    finally:
        stop(s)


def test_audit_feature_detect(srv, tmp_path):
    from command_center import schemas, market_store
    r = post(srv, "/api/watchlists", {"action": "create", "name": "Aud"})[1]["result"]
    if "audit.config" in schemas.EVENT_TYPES:
        assert r["audit"] == "event"
    else:
        assert r["audit"] == "local"
        rows = market_store.MarketStore(tmp_path / "ev" / "market.sqlite3").audit_rows()
        assert any(x["action"] == "watchlist.create" for x in rows)


# ---------------------------------------------------------------- portfolio & friends
def _walk(o):
    if isinstance(o, dict):
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)
    else:
        yield o


def test_portfolio_never_zero_for_unknown(srv):
    st, j = call(srv, "GET", "/api/portfolio")
    assert st == 200 and j["broker"]["state"] == "not_connected" and "no broker adapter" in j["broker"]["reason"]
    assert j["positions"] == [] and j["working_orders"] == [] and j["fills"] == [] and j["unavailable_reason"]
    assert set(j["metrics"]) == {"equity", "daily_pnl", "buying_power", "exposure", "risk_usage"}
    for k, m in j["metrics"].items():
        assert m["value"] is None and m["reason"] and m["currency"] is None and m["as_of"] is None, k
    nums = [v for v in _walk(j) if isinstance(v, (int, float)) and not isinstance(v, bool)]
    assert nums == []
    assert "2026-10-08" in j["note"] and "empty" in j["note"]


def test_signals_markers(srv):
    j = call(srv, "GET", "/api/signals")[1]
    assert j["signals"] == [] and j["reason"] == "no approved strategy"
    j = call(srv, "GET", "/api/markers?symbol=SYNA")[1]
    assert j["markers"] == [] and j["reason"]
    assert call(srv, "GET", "/api/markers?symbol=ZZZZ")[0] == 404


# ---------------------------------------------------------------- market status
def utc(*a):
    return dt.datetime(*a, tzinfo=dt.timezone.utc)


def test_market_status_clock():
    s = api_market.market_status(utc(2026, 10, 8, 15, 0))  # Thu 11:00 NY
    assert s["state"] == "open" and s["schedule_verified"] is False and "unverified" in s["schedule_note"]
    assert api_market.market_status(utc(2026, 10, 8, 13, 29))["state"] == "closed"   # 09:29 NY
    assert api_market.market_status(utc(2026, 10, 8, 13, 30))["state"] == "open"
    assert api_market.market_status(utc(2026, 10, 8, 20, 0))["state"] == "closed"    # 16:00 NY
    assert api_market.market_status(utc(2026, 10, 10, 15, 0))["reason"] == "weekend"
    # DST: 2026-01-14 15:00 UTC is 10:00 EST -> open; 14:00 UTC is 09:00 EST -> closed
    assert api_market.market_status(utc(2026, 1, 14, 15, 0))["state"] == "open"
    assert api_market.market_status(utc(2026, 1, 14, 14, 0))["state"] == "closed"


def test_market_status_holiday_candidate_flagged():
    s = api_market.market_status(utc(2026, 12, 25, 16, 0))  # Fri, Christmas
    if s["holiday_candidate"] is None:
        pytest.skip("tradelab calendar not importable")
    assert s["holiday_candidate"] is True and s["state"] == "unverified" and s["clock_state"] == "open"


def test_market_status_endpoint(srv):
    j = call(srv, "GET", "/api/market/status")[1]
    assert j["state"] == "open" and j["exchange"] == "NYSE" and j["extended_hours"] == "unavailable"


# ---------------------------------------------------------------- safety
BROKER_MODS = {"ib_insync", "ib_async", "ibapi", "ibkr", "alpaca", "alpaca_trade_api", "robin_stocks", "ccxt", "tda", "schwab",
               "futu", "tradier", "oandapyv20", "binance", "interactive_brokers"}


def test_no_broker_imports():
    for f in (ROOT / "command_center").glob("*.py"):
        for n in ast.walk(ast.parse(f.read_text())):
            names = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""] if isinstance(n, ast.ImportFrom) else []
            for nm in names:
                assert nm.split(".")[0].lower() not in BROKER_MODS, f"{f.name} imports {nm}"


def test_no_order_endpoints_registered(srv):
    mine = [(m, rx.pattern) for m, rx, fn in router.ROUTES if fn.__module__.endswith("api_market")]
    assert mine, "market routes not registered"
    for m, pat in mine:
        assert m in ("GET", "POST")
        assert not any(w in pat.lower() for w in ("order", "trade", "place", "cancel", "modify", "execute", "submit", "fill")), pat
    # only watchlist POSTs exist in this module
    assert {p for m, p in mine if m == "POST"} == {r"/api/watchlists\Z", r"/api/watchlists/([A-Za-z0-9_\-]{1,64})/items\Z"}
    for p in ("/api/orders", "/api/order", "/api/portfolio/orders", "/api/trade"):
        assert call(srv, "POST", p, {})[0] == 404


def test_post_routes_require_token(srv):
    for p, b in (("/api/watchlists", {"action": "create", "name": "x"}), ("/api/watchlists/abc/items", {"action": "add"})):
        assert call(srv, "POST", p, b, token=None)[0] == 401
        assert call(srv, "POST", p, b, token="wrong")[0] == 401
    assert [w["name"] for w in call(srv, "GET", "/api/watchlists")[1]["watchlists"]] == ["Research data (3 ETFs)"]


# ---------------------------------------------------------------- real files (structure only)
def test_real_files_structure_only(monkeypatch):
    mf = ROOT / "data" / "raw" / "ibkr_connector" / "MANIFEST.json"
    if not mf.is_file():
        pytest.skip("no real manifest locally")
    monkeypatch.setattr(api_market, "DATA_ROOT", ROOT)
    ins = [i for i in api_market.instruments() if i["data_present"]]
    if not ins:
        pytest.skip("no real CSVs locally")
    today = dt.date(2026, 10, 8)
    for i in ins:
        rows = api_market._load(i)
        assert len(rows) > 1 and all(a["ts"] < b["ts"] for a, b in zip(rows, rows[1:]))
        assert all(r["low"] <= r["high"] for r in rows)
        # docs/DATA_FINDINGS.md: closing-auction prints can fall outside high/low on a few days; only require that it is rare
        outside = sum(not (r["low"] <= min(r["open"], r["close"]) and r["high"] >= max(r["open"], r["close"])) for r in rows)
        assert outside / len(rows) < 0.05
        for iv in ("1D", "1W", "1M"):
            bars = api_market.aggregate(rows, iv, today)
            assert 0 < len(bars) <= len(rows)
            assert sum(b["volume"] for b in bars) == pytest.approx(sum(r["volume"] for r in rows))
