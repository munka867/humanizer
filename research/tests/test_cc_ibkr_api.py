"""IBKR API: auth, guard, live quotes + portfolio from the SIMULATED IB, history jobs, audit, no order routes, server starts without ib_async."""
import http.client
import json
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_cc_market as T  # noqa: E402  (reuse the SYNTHETIC market fixtures)
from test_cc_market import data_root  # noqa: E402,F401  (pytest fixture)
from command_center import api_market, router  # noqa: E402
from command_center.ibkr import manager as MM  # noqa: E402
from command_center.ibkr.fake_ib import FakeIB, FakeStock  # noqa: E402
from command_center.server import make_server  # noqa: E402

TOKEN = T.TOKEN
call, wait_for = T.call, None


def wf(fn, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("timed out")


@pytest.fixture()
def env(tmp_path, data_root, monkeypatch):
    ib = FakeIB()
    m = MM.IBKRManager(ib_factory=lambda: ib, stock_factory=FakeStock, source="fake", data_root=tmp_path / "root", env={}, backoff=(0.05, 0.1), connect_timeout=2)
    MM.set_manager(m)
    s = make_server(tmp_path / "ev" / "e.db", port=0, token=TOKEN)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s, m, ib
    s.stopping = True
    s.shutdown()
    s.server_close()
    m.shutdown()
    MM.set_manager(None)


def connect(s, m, kind="paper_tws"):
    st, j = call(s, "POST", "/api/ibkr/connect", {"kind": kind})
    assert st == 200, j
    wf(lambda: m.status()["state"] == "connected")


def test_status_public_and_honest(env):
    s, m, ib = env
    st, j = call(s, "GET", "/api/ibkr/status", token=None)
    assert st == 200 and j["state"] == "disconnected" and j["execution"] == "none" and j["readonly"] is True and j["fake"] is True
    assert "SIMULATED" in j["banner"] and "Not yet run against a real" in j["honesty"] and j["allowed_kinds"] == ["paper_tws", "paper_gateway"]


def test_post_routes_require_token(env):
    s, m, ib = env
    for path, body in (("/api/ibkr/connect", {"kind": "paper_tws"}), ("/api/ibkr/disconnect", {}), ("/api/ibkr/history", {"symbols": ["SPY"], "bar_size": "1d", "years": 1}), ("/api/ibkr/history/stop", {"id": "a" * 12})):
        assert call(s, "POST", path, body, token=None)[0] == 401
        assert call(s, "POST", path, body, token="wrong")[0] == 401
    assert m.status()["state"] == "disconnected"


def test_connect_guard_and_validation(env):
    s, m, ib = env
    st, j = call(s, "POST", "/api/ibkr/connect", {"kind": "live_tws_readonly"})
    assert st == 400 and j["error_code"] == "refused_by_guard" and "LIVE" in j["error"] and j["info"]["fix"]
    assert call(s, "POST", "/api/ibkr/connect", {"kind": "nope"})[0] == 400
    assert call(s, "POST", "/api/ibkr/connect", {})[0] == 400
    assert call(s, "POST", "/api/ibkr/connect", {"kind": "paper_tws", "host": "8.8.8.8"})[0] == 200      # host is not client-controlled: ignored
    wf(lambda: m.status()["state"] == "connected")
    assert ib.connect_args["host"] == "127.0.0.1" and ib.connect_args["readonly"] is True


def test_connect_audits_and_disconnect(env):
    s, m, ib = env
    connect(s, m)
    st, j = call(s, "POST", "/api/ibkr/disconnect", {})
    assert st == 200 and j["state"] == "disconnected"
    ev = [e for e in s.store.events(0, 100) if e["event_type"] == "audit.config" and e["payload"].get("area") == "ibkr"]
    assert {e["payload"]["action"] for e in ev} >= {"connect", "disconnect"} and all(e["source"] == "ibkr" for e in ev)


def test_live_quotes_labelled_and_fallback(env):
    s, m, ib = env
    st, j = call(s, "GET", "/api/quotes?symbols=SYNA:ARCA&live=1")
    q = j["quotes"][0]
    assert q["freshness"] == "historical" and j["live"]["connected"] is False and "not connected" in q["live_unavailable_reason"]    # unchanged historical quote + reason
    connect(s, m)
    st, j = call(s, "GET", "/api/quotes?symbols=SYNA:ARCA,SYNB:NASDAQ&live=1")
    assert st == 200 and len(j["quotes"]) == 2
    for q in j["quotes"]:
        assert q["freshness"] == "delayed" and q["market_data_type"] == 3 and q["record_source"] == "fake" and "SIMULATED" in q["source"]
        assert q["last"] and q["as_of"].endswith("+00:00") and q["stale"] is False and q["delayed_seconds"] == 900
    assert j["live"]["fake"] is True
    ib.reqMarketDataType(1)
    m.client._ReadOnlyClient__ib.market_data_type_calls  # noqa: B018
    st, j2 = call(s, "GET", "/api/quotes?symbols=SYNA:ARCA")                  # without live=1 the old behaviour is unchanged
    assert j2["quotes"][0]["freshness"] == "historical" and "live" not in j2


def test_portfolio_shows_read_only_account_when_connected_and_never_invents(env):
    s, m, ib = env
    st, j = call(s, "GET", "/api/portfolio")
    assert j["broker"]["state"] == "not_connected" and j["metrics"]["equity"]["value"] is None
    ib.account["GrossPositionValue"] = ("0", "USD")
    connect(s, m)
    st, j = call(s, "GET", "/api/portfolio")
    assert j["broker"]["state"] == "connected" and j["broker"]["mode"] == "read-only" and j["broker"]["fake"] is True
    mt = j["metrics"]
    assert mt["equity"]["value"] == 98765.43 and mt["equity"]["currency"] == "USD" and mt["equity"]["as_of"]
    assert mt["buying_power"]["value"] == 197530.86
    assert mt["exposure"]["value"] == 0.0 and mt["exposure"]["as_of"]                  # a zero the broker reported is a zero WITH as_of
    assert mt["daily_pnl"]["value"] is None and mt["daily_pnl"]["reason"]              # unknown stays null with a reason
    assert mt["risk_usage"]["value"] is None and "risk policy" in mt["risk_usage"]["reason"]
    assert j["positions"] == [] and "no stock positions" in j["empty_reason"]["positions"]
    assert "Read-Only API" in j["empty_reason"]["working_orders"] and "SIMULATED" in j["note"]
    assert "FAKE0001" not in json.dumps(j) and j["broker"]["account_masked"] == "F*****01"


def test_portfolio_positions_orders_fills_mapping(env):
    s, m, ib = env
    from types import SimpleNamespace as N
    c = N(conId=1001, symbol="SYNA", primaryExchange="ARCA", exchange="SMART", currency="USD", secType="STK")
    ib.fake_positions = [N(account="FAKE0001", contract=c, position=10.0, avgCost=100.5), N(account="FAKE0001", contract=N(conId=7, symbol="ES", secType="FUT", currency="USD", exchange="CME"), position=1.0, avgCost=5000.0)]
    ib.fake_orders = [N(contract=c, order=N(orderId=5, permId=99, action="BUY", totalQuantity=10.0, orderType="LMT", lmtPrice=99.0, auxPrice=0.0, tif="DAY", outsideRth=False),
                        orderStatus=N(status="Submitted", filled=4.0, remaining=6.0))]
    connect(s, m)
    j = call(s, "GET", "/api/portfolio")[1]
    assert [p["symbol"] for p in j["positions"]] == ["SYNA"] and j["positions"][0]["shares"] == 10.0 and j["positions"][0]["mark"] is None and "1 non-stock" not in j["empty_reason"]["positions"]
    assert j["working_orders"][0]["status"] == "partial" and j["working_orders"][0]["side"] == "buy" and j["working_orders"][0]["limit"] == 99.0


def test_history_endpoint_validation_and_job(env, tmp_path):
    s, m, ib = env
    ok = {"symbols": ["SPY"], "bar_size": "1d", "years": 1}
    assert call(s, "POST", "/api/ibkr/history", ok)[0] == 409                      # not connected
    connect(s, m)
    for bad in ({**ok, "bar_size": "2h"}, {**ok, "years": 0}, {**ok, "years": 99}, {**ok, "symbols": []}, {**ok, "symbols": ["SPY\n"]}, {**ok, "symbols": ["spy$"]}, {**ok, "symbols": [{"symbol": "SPY", "exchange": "A;B"}]},
                {**ok, "symbols": [{"symbol": "SPY", "conid": "1"}]}, {**ok, "use_rth": "yes"}, {**ok, "symbols": ["A"] * 11}, {"symbols": ["SPY"], "bar_size": "1d"}):
        st, j = call(s, "POST", "/api/ibkr/history", bad)
        assert st == 400 and j["ok"] is False, bad
    st, j = call(s, "POST", "/api/ibkr/history", ok)
    assert st == 200 and j["job"]["status"] in ("queued", "running", "done") and "frozen" in j["note"]
    jid = j["job"]["id"]
    wf(lambda: call(s, "GET", "/api/ibkr/history/jobs")[1]["jobs"][0]["status"] in ("done", "error"), 20)
    jb = call(s, "GET", "/api/ibkr/history/jobs")[1]["jobs"][0]
    assert jb["id"] == jid and jb["status"] == "done" and jb["dir"].startswith("data/fake_") and jb["outputs"][0]["rows"] > 200 and jb["progress"]["chunks_done"] >= 1
    root = tmp_path / "root"
    assert (root / jb["outputs"][0]["path"]).exists() and not (root / "data" / "processed").exists()
    st, j = call(s, "POST", "/api/ibkr/history/stop", {"id": "z"})
    assert st == 400


def test_account_endpoint_masks_account(env):
    s, m, ib = env
    st, j = call(s, "GET", "/api/ibkr/account")
    assert j["connected"] is False
    connect(s, m)
    j = call(s, "GET", "/api/ibkr/account")[1]
    assert j["connected"] and j["account_masked"] == "F*****01" and "FAKE0001" not in json.dumps(j) and j["values"]["NetLiquidation"]["value"] == "98765.43" and j["as_of"]


def test_no_order_routes_registered_by_ibkr_module(env):
    mine = [(mth, rx.pattern) for mth, rx, fn in router.ROUTES if fn.__module__.endswith("api_ibkr")]
    assert len(mine) >= 7
    for mth, pat in mine:
        assert mth in ("GET", "POST") and pat.startswith("/api/ibkr/")
        assert not any(w in pat.lower() for w in ("order", "trade", "place", "cancel", "modify", "execute", "submit", "fill", "transmit")), pat
    s, m, ib = env
    for p in ("/api/ibkr/order", "/api/ibkr/orders", "/api/ibkr/place", "/api/ibkr/cancel", "/api/orders"):
        assert call(s, "POST", p, {})[0] == 404


def test_server_starts_and_status_works_without_ib_async(monkeypatch, tmp_path, data_root):
    from command_center.ibkr import manager as mm, readonly_client as rc
    msg = "ib_async could not be imported (ModuleNotFoundError: No module named 'ib_async'). Install it with: python -m pip install ib_async==2.1.0"
    monkeypatch.setattr(rc, "load_ib_async", lambda: (_ for _ in ()).throw(rc.LibraryMissing(msg)))
    MM.set_manager(MM.IBKRManager(data_root=tmp_path, env={}))
    s = make_server(tmp_path / "ev2" / "e.db", port=0, token=TOKEN)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        st, j = call(s, "GET", "/api/ibkr/status")
        assert st == 200 and j["library"]["installed"] is False and "pip install ib_async==2.1.0" in j["library"]["message"] and j["state"] == "disconnected"
        st, j = call(s, "POST", "/api/ibkr/connect", {"kind": "paper_tws"})
        assert st == 400 and j["error_code"] == "library_missing" and "pip install ib_async==2.1.0" in j["info"]["fix"]
        assert call(s, "GET", "/api/health")[0] == 200 and call(s, "GET", "/api/watchlists")[0] == 200            # everything else keeps working
        assert call(s, "GET", "/api/quotes?symbols=SYNA:ARCA&live=1")[1]["quotes"][0]["freshness"] == "historical"
    finally:
        s.stopping = True
        s.shutdown()
        s.server_close()
        MM.set_manager(None)


def test_importing_the_server_does_not_import_ib_async():
    import subprocess
    code = "import sys; sys.path.insert(0, %r); import command_center.server, command_center.api_ibkr, command_center.api_market; sys.exit(1 if 'ib_async' in sys.modules else 0)" % str(ROOT)
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0
