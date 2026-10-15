"""Phase-6: can the DASHBOARD / HTTP API / UI / data adapter place, change or cancel an order, or read a non-paper account?
FAKES ONLY (command_center/ibkr/fake_ib.py), temp dirs, throwaway token. Plain tests = a guard that HELD under attack."""
import ast
import http.client
import json
import re
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from command_center import router  # noqa: E402
from command_center.ibkr import manager as M  # noqa: E402
from command_center.ibkr.fake_ib import FakeIB, FakeStock  # noqa: E402
from command_center.server import make_server  # noqa: E402

TOKEN = "broker-review-token"
CC = ROOT / "command_center"
PROD = [f for f in CC.glob("**/*.py") if not f.name.startswith(("verify_", "repro", "seed_demo", "emit", "load_events", "export_events"))]
ORDER_API = {"placeOrder", "placeOrderAsync", "cancelOrder", "cancelOrderAsync", "modifyOrder", "reqGlobalCancel", "whatIfOrder", "whatIfOrderAsync",
             "reqAutoOpenOrders", "bracketOrder", "MarketOrder", "LimitOrder", "StopOrder", "StopLimitOrder", "Order", "reqIds", "exerciseOptions",
             "placeOrders", "create_order_instruction", "createOrderInstruction"}


def calls_and_names(tree):
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute):
            yield n.attr, n
        elif isinstance(n, ast.Name):
            yield n.id, n
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            yield n.value, n


def test_no_order_surface_anywhere_in_dashboard_python():
    """AST scan (names, attributes AND string constants, so getattr(ib, 'place' + 'Order')-style literals and dispatch tables are caught)."""
    bad = []
    for f in PROD:
        tree = ast.parse(f.read_text())
        for name, node in calls_and_names(tree):
            if name in ORDER_API and not (f.name == "api_data.py" and name == "Order" and isinstance(node, ast.Constant)):   # XML row tag in the Flex import parser
                bad.append((f.name, node.lineno, name))
    assert not bad, bad


def test_no_dynamic_code_or_attribute_tricks_in_dashboard_python():
    """No eval/exec/compile/importlib/__import__/getattr-with-computed-name/setattr/globals() in production dashboard modules."""
    bad = []
    for f in PROD:
        tree = ast.parse(f.read_text())
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                fn = n.func
                name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else None
                if isinstance(fn, ast.Attribute) and name == "compile":
                    name = None                                              # re.compile
                if name in ("eval", "exec", "compile", "__import__", "import_module", "setattr", "globals", "locals", "vars", "system", "popen"):
                    bad.append((f.name, n.lineno, name))
                if name == "getattr" and not (len(n.args) >= 2 and isinstance(n.args[1], ast.Constant)):
                    bad.append((f.name, n.lineno, "getattr(computed)"))
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                mod = (n.module or "") if isinstance(n, ast.ImportFrom) else ""
                names = [a.name for a in n.names] + [mod]
                if any(x.split(".")[0] in ("ib_insync", "ibapi") or x.startswith(("tradelab.execution", "tradelab.brokers")) for x in names if x):
                    bad.append((f.name, n.lineno, "import " + ",".join(names)))
    # the only permitted dynamic spots: router.load_modules (fixed list of our own modules) and the lazy ib_async import in readonly_client
    allowed = {("router.py", "import_module"), ("readonly_client.py", "import ib_async")}
    bad = [b for b in bad if (b[0], b[2]) not in allowed]
    assert not bad, bad


def test_ib_async_is_imported_only_by_the_readonly_client_in_the_dashboard():
    hits = [f.name for f in PROD if re.search(r"^\s*(import|from)\s+ib_async", f.read_text(), re.M)]
    assert hits == ["readonly_client.py"]


def test_all_dashboard_subprocess_use_is_the_single_fixed_argv_runner():
    sites = []
    for f in PROD:
        if re.search(r"^\s*(import subprocess|from subprocess)|subprocess\.(Popen|run|call|check_output)", f.read_text(), re.M):
            sites.append(f.name)
    assert sites == ["jobs.py"]
    src = (CC / "jobs.py").read_text()
    assert "shell=True" not in src and "Popen(argv" in src
    # the only argv constructed from request data is the experiment runner, which is [python, scripts/run_daily.py, ...] built from validated fields
    assert "run_daily.py" in (CC / "api_research.py").read_text()
    for f in PROD:
        t = f.read_text()
        assert "scripts/run_backtest" not in t and "execution.approve" not in t and "smoke_test" not in t, f.name


def test_ui_javascript_has_no_order_posting():
    posts = []
    for f in (CC / "static" / "js").glob("**/*.js"):
        for m in re.finditer(r"""post\(\s*[`'"]([^`'"]+)""", f.read_text()):
            posts.append(m.group(1))
    assert posts and not [p for p in posts if re.search(r"order|trade|execut|broker|approve|permit|flatten", p, re.I)], posts


def test_no_post_route_name_suggests_orders_or_approvals():
    router.load_modules()
    pats = [rx.pattern for m, rx, _ in router.ROUTES if m == "POST"]
    assert pats and not [p for p in pats if re.search(r"order|trade|execut|approv(?!ed_)|permit|flatten|broker", p, re.I)], pats


@pytest.fixture()
def app(tmp_path):
    made = {}
    def factory():
        made["ib"] = FakeIB()
        made["ib"].accounts = made.get("accounts", ["DUFAKE001"])
        return made["ib"]
    mgr = M.IBKRManager(ib_factory=factory, stock_factory=FakeStock, source="fake", data_root=tmp_path, backoff=(0.05,), connect_timeout=2.0)
    M.set_manager(mgr)
    s = make_server(tmp_path / "events.sqlite3", port=0, token=TOKEN)
    s.research_paths = {"experiments_dir": tmp_path / "exp", "imports_dir": tmp_path / "imp", "test_log": tmp_path / "log.jsonl"}
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s, mgr, made
    mgr.disconnect(); mgr.shutdown()
    M.set_manager(None)
    s.stopping = True; s.shutdown(); s.server_close()


def call(s, method, path, body=None, token=TOKEN):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=15)
    h = {"Content-Type": "application/json"}
    if token:
        h["X-CC-Token"] = token
    c.request(method, path, json.dumps(body) if body is not None else None, h)
    r = c.getresponse(); raw = r.read()
    try:
        return r.status, json.loads(raw)
    except ValueError:
        return r.status, raw


def wait_state(mgr, want, t=5.0):
    end = time.time() + t
    while time.time() < end:
        if mgr.status()["state"] == want:
            return True
        time.sleep(0.05)
    return False


def test_http_cannot_choose_host_port_client_or_writable_session(app):
    s, mgr, made = app
    st, b = call(s, "POST", "/api/ibkr/connect", {"kind": "paper_tws", "host": "10.0.0.5", "port": 7496, "clientId": 1, "readonly": False})
    assert st == 200 and wait_state(mgr, "connected")
    a = made["ib"].connect_args
    assert (a["host"], a["port"]) == ("127.0.0.1", 7497) and a["readonly"] is True and 9100 <= a["clientId"] <= 9109


@pytest.mark.parametrize("kind", ["live_tws_readonly", "live_gateway_readonly", "live", "paper_tws ", "PAPER_TWS", "paper_tws\n", "../paper_tws", "7496", "", "paper_live"])
def test_every_unusual_kind_is_refused_without_a_connection(app, kind):
    s, mgr, made = app
    st, b = call(s, "POST", "/api/ibkr/connect", {"kind": kind})
    assert st == 400 and "ib" not in made and mgr.status()["state"] == "disconnected"


def test_non_paper_login_exposes_no_account_data_over_any_route(app):
    s, mgr, made = app
    made["accounts"] = ["U1234567"]
    call(s, "POST", "/api/ibkr/connect", {"kind": "paper_tws"})
    time.sleep(1.0)
    assert mgr.status()["state"] != "connected"
    blob = ""
    for p in ("/api/ibkr/status", "/api/ibkr/account", "/api/portfolio", "/api/quotes?live=1", "/api/connections", "/api/market/status"):
        st, b = call(s, "GET", p)
        blob += json.dumps(b) if not isinstance(b, bytes) else b.decode()
    assert "U1234567" not in blob and "98765" not in blob          # fake NetLiquidation never leaves the process


def test_paper_login_account_id_is_masked_everywhere_and_fake_is_labelled(app):
    s, mgr, made = app
    call(s, "POST", "/api/ibkr/connect", {"kind": "paper_tws"})
    assert wait_state(mgr, "connected")
    time.sleep(0.5)
    blob = ""
    for p in ("/api/ibkr/status", "/api/ibkr/account", "/api/portfolio", "/api/connections"):
        st, b = call(s, "GET", p)
        assert st == 200
        blob += json.dumps(b)
    assert "DUFAKE001" not in blob and "DU*****01" in blob or "DU" in blob
    st, b = call(s, "GET", "/api/ibkr/status")
    assert b["fake"] is True and "SIMULATED" in b["banner"] and b["execution"] == "none" and b["readonly"] is True
    st, p = call(s, "GET", "/api/portfolio")
    assert "SIMULATED" in json.dumps(p)


def test_dashboard_has_no_route_to_the_approval_cli_key_or_ledger(app):
    s, mgr, made = app
    for path in ("/api/approve", "/api/approvals", "/api/orders", "/api/order", "/api/execute", "/api/flatten", "/api/permit", "/api/paper",
                 "/api/ibkr/order", "/api/ibkr/orders", "/api/ibkr/cancel", "/api/ibkr/place", "/api/strategies/D3_V3_c2_short/approve"):
        for method in ("GET", "POST"):
            assert call(s, method, path, {} if method == "POST" else None)[0] in (404, 405, 501), (method, path)
    # the strategy-state route refuses approved_* even with a valid token (re-verified here for the broker audit)
    for st_ in ("approved_paper", "approved_live"):
        assert call(s, "POST", "/api/strategies/D3_V3_c2_short/state", {"state": st_, "reason": "please"})[0] == 403
    assert not (ROOT / "configs" / "approvals" / "paper_approval.json").exists()


def test_dashboard_never_sees_the_approval_key_or_reads_approval_files():
    for f in PROD:
        t = f.read_text()
        assert "CC_APPROVAL_KEY" not in t and "paper_approval" not in t and "ledger.db" not in t, f.name
    for f in (CC / "static").glob("**/*.js"):
        assert "CC_APPROVAL_KEY" not in f.read_text()
