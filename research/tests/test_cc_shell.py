"""Command-centre SHELL: api_core (prefs, instruments, connections, audit), audit schema, static shell contract, AA contrast."""
import http.client
import json
import re
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from command_center.server import make_server  # noqa: E402

TOKEN = "shell-test-token"
STATIC = ROOT / "command_center" / "static"
WS_IDS = ["command_center", "stocks", "portfolio", "agents", "research", "backtest", "data", "settings"]

MANIFEST = [
    {"symbol": "SPY", "contract_id": 756733, "exchange": "ARCA", "n_bars": 1000, "first": "2022-10-12 13:30:00+00:00",
     "last": "2026-10-07 13:30:00+00:00", "retrieved_on": "2026-10-08", "source_tag": "Last", "delayed_seconds": 900,
     "adjusted_for_dividends": False, "raw_file": "data/raw/ibkr_connector/SECRET_PATH.json", "sha256": "abc"},
    {"symbol": "QQQ", "contract_id": 320227571, "exchange": "NASDAQ", "name": "Invesco QQQ Trust", "n_bars": 999,
     "last": "2026-10-07 13:30:00+00:00", "retrieved_on": "2026-10-08", "delayed_seconds": 900},
    {"symbol": "ZZZ", "exchange": "WEIRD"},  # no contract id, unknown exchange
]


@pytest.fixture()
def srv(tmp_path):
    s = make_server(tmp_path / "e.db", port=0, token=TOKEN, docs_dir=tmp_path / "docs", results_dir=tmp_path / "res")
    (tmp_path / "man.json").write_text(json.dumps(MANIFEST))
    s.manifest_path = tmp_path / "man.json"
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s
    s.stopping = True
    s.shutdown()
    s.server_close()


def call(srv, method, path, body=None, token=TOKEN, headers=None, raw_body=None):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    h = {"Content-Type": "application/json"}
    if token:
        h["X-CC-Token"] = token
    h.update(headers or {})
    c.request(method, path, raw_body if raw_body is not None else (json.dumps(body) if body is not None else None), h)
    r = c.getresponse()
    raw = r.read()
    try:
        return r.status, json.loads(raw)
    except ValueError:
        return r.status, raw


# ------------------------------------------------------------------ prefs
def test_prefs_roundtrip_and_persistence(srv, tmp_path):
    assert call(srv, "GET", "/api/prefs")[1]["prefs"] == {}
    s, b = call(srv, "POST", "/api/prefs", {"prefs": {"shell.nav_collapsed": True, "table.pos": {"w": {"a": 90}, "hide": ["b"]}, "ui.mode": "BACKTEST"}})
    assert s == 200 and b["saved"] == ["shell.nav_collapsed", "table.pos", "ui.mode"]
    got = call(srv, "GET", "/api/prefs")[1]["prefs"]
    assert got["shell.nav_collapsed"] is True and got["table.pos"]["w"] == {"a": 90} and got["ui.mode"] == "BACKTEST"
    assert call(srv, "GET", "/api/prefs?key=ui.mode")[1]["value"] == "BACKTEST"
    assert call(srv, "GET", "/api/prefs?key=nope")[0] == 404
    # single-key form + delete
    assert call(srv, "POST", "/api/prefs", {"key": "ui.theme", "value": "light"})[0] == 200
    assert call(srv, "POST", "/api/prefs", {"delete": ["ui.mode"]})[0] == 200
    assert "ui.mode" not in call(srv, "GET", "/api/prefs")[1]["prefs"]
    # lives in a separate SQLite file next to the events DB, and the events DB is untouched
    assert (tmp_path / "prefs.sqlite3").is_file() and (tmp_path / "e.db").is_file()
    assert call(srv, "GET", "/api/health")[1]["max_seq"] == 0
    # survives a new server over the same files
    from command_center.api_core import PrefsStore
    assert PrefsStore(tmp_path / "prefs.sqlite3").get("ui.theme")["value"] == "light"


def test_prefs_validation(srv):
    for bad in ("Upper", "has space", "x" * 65, "", "a/b", "ünï"):
        s, b = call(srv, "POST", "/api/prefs", {"prefs": {bad: 1}})
        assert s == 400 and b["ok"] is False, bad
    assert call(srv, "POST", "/api/prefs", {"prefs": {"k" * 64: 1}})[0] == 200
    # 64KB value limit (JSON length); all-or-nothing
    assert call(srv, "POST", "/api/prefs", {"prefs": {"big": "x" * (64 * 1024)}})[0] == 400
    assert call(srv, "POST", "/api/prefs", {"prefs": {"ok.one": 1, "big": "x" * 70000}})[0] == 400
    assert "ok.one" not in call(srv, "GET", "/api/prefs")[1]["prefs"]
    assert call(srv, "POST", "/api/prefs", {"prefs": {"almost": "x" * (64 * 1024 - 10)}})[0] == 200
    assert call(srv, "POST", "/api/prefs", {})[0] == 400
    assert call(srv, "POST", "/api/prefs", {"prefs": []})[0] == 400
    assert call(srv, "POST", "/api/prefs", ["x"])[0] == 400
    assert call(srv, "POST", "/api/prefs", raw_body="{not json")[0] == 400


def test_prefs_write_needs_token(srv):
    assert call(srv, "POST", "/api/prefs", {"prefs": {"a": 1}}, token=None)[0] == 401
    assert call(srv, "POST", "/api/prefs", {"prefs": {"a": 1}}, token="wrong")[0] == 401
    assert call(srv, "GET", "/api/prefs")[1]["prefs"] == {}  # reads need no token; nothing was written
    assert call(srv, "POST", "/api/audit", {"area": "ui", "action": "x"}, token=None)[0] == 401


# ------------------------------------------------------------------ instruments
def test_instruments_from_manifest_never_invented(srv):
    s, b = call(srv, "GET", "/api/instruments", token=None)
    assert s == 200 and b["available"] is True and b["total"] == 3
    by = {i["symbol"]: i for i in b["instruments"]}
    spy = by["SPY"]
    assert spy["con_id"] == 756733 and spy["exchange"] == "ARCA" and spy["currency"] == "USD"
    assert spy["name"] is None  # manifest has no name for SPY: never invented
    assert "inferred" in spy["currency_basis"]
    assert by["QQQ"]["name"] == "Invesco QQQ Trust"  # name only because the manifest carries it
    assert by["ZZZ"]["con_id"] is None and by["ZZZ"]["currency"] is None and by["ZZZ"]["key"] is None
    assert "SECRET_PATH" not in json.dumps(b) and "sha256" not in json.dumps(b)
    assert [i["symbol"] for i in call(srv, "GET", "/api/instruments?q=sp")[1]["instruments"]] == ["SPY"]
    assert [i["symbol"] for i in call(srv, "GET", "/api/instruments?q=invesco")[1]["instruments"]] == ["QQQ"]
    assert call(srv, "GET", "/api/instruments?q=nothing")[1]["instruments"] == []
    assert call(srv, "GET", "/api/instruments?q=%3Cscript%3E")[0] == 400
    assert call(srv, "GET", "/api/instruments?limit=abc")[0] == 400
    assert len(call(srv, "GET", "/api/instruments?limit=1")[1]["instruments"]) == 1


def test_instruments_without_manifest_is_honest(srv, tmp_path):
    srv.manifest_path = tmp_path / "missing.json"
    s, b = call(srv, "GET", "/api/instruments")
    assert s == 200 and b["available"] is False and b["instruments"] == [] and "not found" in b["reason"]
    (tmp_path / "bad.json").write_text("{nope")
    srv.manifest_path = tmp_path / "bad.json"
    assert call(srv, "GET", "/api/instruments")[1]["available"] is False


# ------------------------------------------------------------------ connections
def test_connections_honesty(srv, tmp_path):
    s, b = call(srv, "GET", "/api/connections", token=None)
    assert s == 200
    assert b["broker"]["state"] == "not_connected" and b["broker"]["reason"] == "not_connected: no broker adapter implemented"
    assert b["broker"]["adapter"] is None and b["broker"]["execution"] == "none" and b["broker"]["setup_complete"] is False
    md = b["market_data"]
    assert md["interval"] == "1d" and md["instruments"] == 3 and md["last_retrieved_on"] == "2026-10-08"
    assert md["delayed_seconds"] == 900 and md["state"] in ("historical", "stale")  # never 'realtime' for daily bars
    assert md["state"] != "realtime"
    assert b["event_stream"]["runtime_attached"] is False
    dumped = json.dumps(b)
    assert str(tmp_path) not in dumped and TOKEN not in dumped and "e.db" not in dumped
    assert b["storage"]["events_db"] == "[redacted]"
    srv.manifest_path = tmp_path / "missing.json"
    md = call(srv, "GET", "/api/connections")[1]["market_data"]
    assert md["state"] == "unavailable" and md["last_retrieved_on"] is None and md["reason"]


def test_connections_old_retrieval_is_stale(srv, tmp_path):
    (tmp_path / "old.json").write_text(json.dumps([{"symbol": "SPY", "contract_id": 1, "exchange": "ARCA", "retrieved_on": "2020-01-01", "last": "2019-12-31"}]))
    srv.manifest_path = tmp_path / "old.json"
    assert call(srv, "GET", "/api/connections")[1]["market_data"]["state"] == "stale"


# ------------------------------------------------------------------ audit
def test_audit_records_event_and_lists(srv):
    s, b = call(srv, "POST", "/api/audit", {"area": "ui", "action": "mode", "target": "mode", "old": "DEMO", "new": "BACKTEST", "outcome": "applied", "reason": "display label only"})
    assert s == 200 and b["ok"] and b["seq"] == 1
    s, b = call(srv, "POST", "/api/audit", {"area": "ui", "action": "mode_refused", "new": "LIVE", "outcome": "refused", "reason": "no broker adapter"})
    assert s == 200
    items = call(srv, "GET", "/api/audit", token=None)[1]["items"]
    assert [i["action"] for i in items] == ["mode_refused", "mode"] and items[1]["old"] == "DEMO" and items[0]["outcome"] == "refused"
    # it is a real event in the append-only store, visible on the stream/events API
    evs = call(srv, "GET", "/api/events")[1]["events"]
    assert [e["event_type"] for e in evs] == ["audit.config", "audit.config"] and evs[0]["source"] == "ui"
    assert call(srv, "GET", "/api/audit?limit=1")[1]["next_before_seq"] == 2
    assert [i["seq"] for i in call(srv, "GET", "/api/audit?before_seq=2")[1]["items"]] == [1]
    assert call(srv, "GET", "/api/audit?limit=x")[0] == 400


def test_audit_validation_and_no_secrets(srv):
    bad = [{}, {"area": "ui"}, {"area": "ui", "action": "x", "outcome": "pwned"}, {"area": "ui", "action": "x", "bogus": 1},
           {"area": "<b>", "action": "x"}, {"area": "ui", "action": "x" * 200}, {"area": "ui", "action": "x", "detail": "notadict"},
           {"area": "ui", "action": "x", "old": "y" * 3000},
           {"area": "ui", "action": "x", "detail": {"api_token": "hunter2"}},
           {"area": "ui", "action": "x", "new": {"nested": {"Password": "x"}}}, ["list"]]
    for body in bad:
        s, b = call(srv, "POST", "/api/audit", body)
        assert s == 400 and b["ok"] is False, body
    assert call(srv, "GET", "/api/audit")[1]["items"] == []
    s, b = call(srv, "POST", "/api/audit", {"area": "ui", "action": "x", "detail": {"api_token": "hunter2"}})
    assert "hunter2" not in json.dumps(b)  # error text never echoes the secret value


def test_audit_config_schema_registered():
    from command_center.schemas import EVENT_TYPES, SCHEMAS, validate_event
    assert "audit.config" in EVENT_TYPES and 1 in SCHEMAS["audit.config"]
    e = validate_event({"event_type": "audit.config", "source": "ui", "payload": {"area": "settings", "action": "set", "new": 3}})
    assert e["payload"]["new"] == 3


def test_existing_event_posting_still_works_with_audit_type(srv):
    s, b = call(srv, "POST", "/api/events", {"event_type": "incident", "source": "t", "payload": {"severity": "info", "summary": "x"}})
    assert s == 200 and b["accepted"][0]["seq"] == 1
    snap = call(srv, "GET", "/api/snapshot")[1]
    assert snap["event_count"] == 1


# ------------------------------------------------------------------ static shell contract
def test_shell_served_and_credit_line(srv):
    s, html = call(srv, "GET", "/", token=None)
    assert s == 200
    txt = html.decode()
    assert 'href="https://www.tradingview.com/"' in txt and "noopener" in txt
    assert "Charts:" in txt and "TradingView Lightweight Charts&trade;" in txt and "(Apache-2.0)" in txt
    assert 'role="banner"' in txt and 'role="contentinfo"' in txt and "<nav" in txt and "<main" in txt and 'class="skip"' in txt
    assert call(srv, "GET", "/static/legacy/index.html", token=None)[0] == 200
    assert call(srv, "GET", "/static/js/core/main.js", token=None)[0] == 200
    assert call(srv, "GET", "/static/dev.html", token=None)[0] == 200


def test_eight_workspaces_registered_with_placeholder_modules():
    reg = (STATIC / "js" / "core" / "registry.js").read_text()
    for wid in WS_IDS:
        assert f'id: "{wid}"' in reg
        f = STATIC / "js" / "workspaces" / f"{wid}.js"
        assert f.is_file(), wid
        body = f.read_text()
        assert "export default" in body
    for title in ["Command Center", "Stocks & Charts", "Portfolio & Orders", "Agent Team", "Research & Strategies",
                  "Backtesting & Monte Carlo", "Data & Connections", "Settings & Audit"]:
        assert f'title: "{title}"' in reg
    assert (STATIC / "js" / "workspaces" / "_placeholder.js").is_file()
    assert "not implemented" not in (STATIC / "js" / "workspaces" / "_placeholder.js").read_text().lower()


def test_core_js_safety_rules():
    core = sorted((STATIC / "js" / "core").glob("*.js")) + sorted((STATIC / "js" / "workspaces").glob("*.js"))
    assert len(core) >= 14
    for f in core:
        t = f.read_text()
        assert not re.search(r"\.innerHTML\s*=|\.outerHTML\s*=|insertAdjacentHTML|document\.write\(|\beval\(|new Function\(", t), f.name
        assert not re.search(r"https?://", t.replace("http://www.w3.org/2000/svg", "")), f"external URL in {f.name}"  # SVG namespace is not a request
        assert "cdn" not in t.lower(), f.name
        for banned in ("ibapi", "ib_insync", "/api/orders", "placeOrder", "submitOrder"):
            assert banned not in t, (f.name, banned)
    for css in (STATIC / "css").glob("*.css"):
        assert "@import" not in css.read_text() and "url(http" not in css.read_text()
    html = (STATIC / "index.html").read_text()
    assert not re.search(r'(src|href)="https?://(?!www\.tradingview\.com/)', html)


def test_no_order_endpoints_in_api_core():
    src = (ROOT / "command_center" / "api_core.py").read_text()
    import ast
    tree = ast.parse(src)
    for n in ast.walk(tree):
        mods = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""] if isinstance(n, ast.ImportFrom) else []
        for m in mods:
            assert not any(m.lower().startswith(b) for b in ("ibapi", "ib_insync", "ib_async", "alpaca", "ccxt")), m
    routes = re.findall(r'@router\.route\("(\w+)", r"([^"]+)"', src)
    assert not [r for r in routes if "order" in r[1] or "trade" in r[1]]


def test_core_readme_documents_every_public_function():
    readme = (STATIC / "js" / "core" / "README.md").read_text()
    for name in ["ctx.api.get", "ctx.api.post", "ctx.events.on", "ctx.events.snapshot", "ctx.freshness", "ctx.prefs.get", "ctx.prefs.set",
                 "ctx.fmt.money", "ctx.fmt.pct", "ctx.fmt.qty", "ctx.fmt.time", "ctx.fmt.missing", "ctx.ui.table", "ctx.ui.tabs", "ctx.ui.drawer",
                 "ctx.ui.toast", "ctx.ui.confirm", "ctx.ui.skeleton", "ctx.ui.empty", "ctx.ui.badge", "ctx.ui.resizer", "ctx.ui.tooltip",
                 "ctx.ui.popover", "ctx.env", "ctx.nav", "ctx.audit", "ctx.risk", "el(", "text("]:
        assert name in readme, name
    assert (STATIC / "js" / "core" / "READY").is_file()


# ------------------------------------------------------------------ AA contrast (numeric, from tokens.css + derived tints)
def _hex(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _lum(c):
    f = lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = map(f, c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ratio(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _mix(fg, bg, pct):  # CSS color-mix(in srgb, fg pct%, bg)
    return tuple(f * pct + b * (1 - pct) for f, b in zip(fg, bg))


def _themes():
    css = (STATIC / "css" / "tokens.css").read_text()
    dark = dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})", css.split(':root[data-theme="light"]')[0]))
    light_block = css.split(':root[data-theme="light"]')[1].split("}")[0]
    light = {**dark, **dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})", light_block))}
    return {"dark": dark, "light": light}


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_aa_contrast_text_and_status_tokens(theme):
    t = {k: _hex(v) for k, v in _themes()[theme].items()}
    fails = []

    def need(name, fg, bg, minimum=4.5):
        r = _ratio(fg, bg)
        if r < minimum:
            fails.append(f"{theme}: {name} {r:.2f} < {minimum}")
    for bgname in ("bg", "surface", "raised"):
        need(f"text on {bgname}", t["text"], t[bgname])
        need(f"text-2 on {bgname}", t["text-2"], t[bgname])
        for st in ("accent", "pos", "neg", "warn", "demo"):
            need(f"{st} text on {bgname}", t[st], t[bgname])
        need(f"focus ring on {bgname}", t["focus"], t[bgname], 3.0)
    # tinted badge / banner backgrounds (base.css: color-mix(var(--x) 14%, var(--surface)))
    for st in ("pos", "neg", "warn", "accent", "demo"):
        tint = _mix(t[st], t["surface"], 0.14)
        need(f"text on {st} tint", t["text"], tint)
        need(f"text-2 on {st} tint", t["text-2"], tint)
        need(f"{st} icon on its tint (graphics 3:1)", t[st], tint, 3.0)
    # filled buttons / counters: --bg text on solid colour
    for st in ("accent", "neg", "warn"):
        need(f"bg text on solid {st}", t["bg"], t[st])
    # hover row tint (accent 8% of surface) keeps text readable
    need("text on row hover", t["text"], _mix(t["accent"], t["surface"], 0.08))
    need("text-2 on hover chip", t["text-2"], _mix(t["accent"], t["raised"], 0.12))
    assert not fails, "\n".join(fails)
