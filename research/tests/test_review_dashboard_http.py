"""Phase-4 reviewer tests: dashboard HTTP security/safety (docs/REVIEW.md 'Dashboard security audit (phase 4)').
In-process server on an ephemeral port with a TEMP db/ledger/imports dir; nothing touches the repo's results/ or data/.
xfail(strict=True) = reproduces a finding that is still open; remove the marker when fixed."""
import ast
import http.client
import json
import re
import socket
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from command_center import api_research, router  # noqa: E402
from command_center.server import make_server  # noqa: E402

TOKEN = "review-token"


@pytest.fixture()
def srv(tmp_path):
    s = make_server(tmp_path / "events.sqlite3", port=0, token=TOKEN)
    s.research_paths = {"experiments_dir": tmp_path / "exp", "imports_dir": tmp_path / "imp",
                        "test_log": tmp_path / "test_access_log.jsonl"}
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s, tmp_path
    s.stopping = True
    s.shutdown(); s.server_close()


def call(s, method, path, body=None, token=TOKEN, headers=None, raw=None):
    c = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=15)
    h = {"Content-Type": "application/json"}
    if token:
        h["X-CC-Token"] = token
    h.update(headers or {})
    c.request(method, path, raw if raw is not None else (json.dumps(body) if body is not None else None), h)
    r = c.getresponse()
    data = r.read()
    try:
        return r.status, json.loads(data), r
    except ValueError:
        return r.status, data, r


def rawsock(s, request: bytes):
    k = socket.create_connection(("127.0.0.1", s.server_address[1]), timeout=4)
    k.sendall(request)
    try:
        return k.recv(200).split(b"\r\n")[0]
    except OSError:
        return b""


SAMPLE_POSTS = ["/api/events", "/api/commands", "/api/prefs", "/api/audit", "/api/watchlists", "/api/watchlists/abc/items",
                "/api/strategies/D3_V1_c2_both/state", "/api/experiments", "/api/import/preview", "/api/import/commit"]


def test_every_post_route_is_covered_and_requires_the_token(srv):
    s, _ = srv
    router.load_modules()
    pats = [rx for m, rx, _ in router.ROUTES if m == "POST"]
    for rx in pats:   # a newly added POST route must be added to SAMPLE_POSTS (forces a conscious auth test)
        assert any(rx.match(p) for p in SAMPLE_POSTS), f"uncovered POST route {rx.pattern}"
    for p in SAMPLE_POSTS:
        assert call(s, "POST", p, {}, token=None)[0] == 401
        assert call(s, "POST", p, {}, token="wrong")[0] == 401


@pytest.mark.parametrize("host", ["evil.com", "localhost.evil.com", "127.0.0.1.evil.com", "evil.com:80", "127.0.0.1@evil.com"])
def test_dns_rebinding_hosts_rejected(srv, host):
    assert call(srv[0], "GET", "/api/health", headers={"Host": host})[0] == 403


@pytest.mark.parametrize("origin", ["http://evil.com", "null", "http://localhost.evil.com"])
def test_cross_origin_post_rejected_even_with_token(srv, origin):
    assert call(srv[0], "POST", "/api/prefs", {"key": "x1", "value": 1}, headers={"Origin": origin})[0] == 403


def test_no_cors_headers_and_non_get_post_methods_unimplemented(srv):
    st, _, r = call(srv[0], "GET", "/api/health")
    assert r.getheader("Access-Control-Allow-Origin") is None
    for m in ("PUT", "DELETE", "PATCH", "OPTIONS"):
        assert call(srv[0], m, "/api/prefs", {})[0] == 501


@pytest.mark.parametrize("path", ["/static/../server.py", "/static/%2e%2e/server.py", "/static/..%2fserver.py",
                                  "/static/%2e%2e%2f%2e%2e%2f.cc_token", "/api/docs/..%2f..%2fCLAUDE.md", "/api/docs/../CLAUDE.md",
                                  "/api/results/..%2f..%2fCLAUDE.md", "/api/docs/.md", "/static//etc/passwd"])
def test_path_traversal_blocked(srv, path):
    assert call(srv[0], "GET", path)[0] == 404


def test_import_commit_filename_cannot_escape_import_dir(srv):
    s, tmp = srv
    csv = "Symbol,Quantity,TradePrice,DateTime\nSPY,1,100.5,2024-01-02 10:00:00\n"
    for i, fn in enumerate(["../../../../tmp/evil.csv", "..\\..\\x.csv", "/etc/passwd", ".hidden", 'a"b<script>.csv']):
        st, b, _ = call(s, "POST", "/api/import/commit", {"filename": fn, "content": csv + f"#{i}\n"})
        assert st == 201, b
    stored = list((tmp / "imp").iterdir())
    assert len(stored) == 5 and all(p.parent == tmp / "imp" and not p.name.startswith("..") for p in stored)
    assert not (tmp / "evil.csv").exists()


def test_xml_entity_bomb_and_xxe_are_not_expanded(srv):
    s, _ = srv
    bomb = ('<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
            '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">]><FlexQueryResponse><Trades><Trade symbol="&c;"/></Trades></FlexQueryResponse>')
    xxe = '<?xml version="1.0"?><!DOCTYPE l [<!ENTITY x SYSTEM "file:///etc/passwd">]><FlexQueryResponse><Trades><Trade symbol="&x;"/></Trades></FlexQueryResponse>'
    for doc in (bomb, xxe):
        st, b, _ = call(s, "POST", "/api/import/commit", {"filename": "x.xml", "content": doc})
        assert st in (200, 422) and "root:" not in json.dumps(b)


# ---- sealed final test
def test_test_segment_refused_and_ledger_gets_only_a_denied_row(srv):
    s, tmp = srv
    st, b, _ = call(s, "POST", "/api/experiments", {"strategy_id": "D3_V1_c2_both", "segment": "test"})
    assert st == 403 and b["code"] == "test_segment_reserved"
    rows = [json.loads(l) for l in (tmp / "test_access_log.jsonl").read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["granted"] is False
    sys.path.insert(0, str(ROOT / "src"))
    from tradelab.validation.final_test_guard import granted_accesses
    assert granted_accesses(tmp / "test_access_log.jsonl") == []     # a refused attempt never consumes the single look
    for seg in ("Test", "test ", ["test"], "train,test", None, "TEST"):
        assert call(s, "POST", "/api/experiments", {"strategy_id": "D3_V1_c2_both", "segment": seg})[0] == 400


def test_experiment_argv_cannot_be_injected(srv):
    s, _ = srv
    base = {"strategy_id": "D3_V1_c2_both", "segment": "validation"}
    for bad in (["SPY", "--segment"], ["-SPY"], ["SPY,QQQ --segment test"], ["../x"], "SPY"):
        assert call(s, "POST", "/api/experiments", {**base, "symbols": bad})[0] in (400, 422)
    for seed in ("1 --segment test", -1, True, 2 ** 40, 1.5):
        assert call(s, "POST", "/api/experiments", {**base, "seed": seed})[0] == 400
    assert call(s, "POST", "/api/experiments", {**base, "out_dir": "/tmp/x"})[0] == 400      # unknown field
    assert call(s, "POST", "/api/experiments", {**base, "strategy_id": "../../etc/passwd"})[0] == 400
    assert call(s, "POST", "/api/experiments", {**base, "variant": "x --segment test"})[0] == 400


def test_approval_states_unreachable(srv):
    s, _ = srv
    for st_ in ("approved_paper", "approved_live"):
        assert call(s, "POST", "/api/strategies/D3_V3_c2_short/state", {"state": st_, "reason": "please"})[0] == 403
    for m in ("PAPER", "LIVE"):
        assert call(s, "POST", "/api/commands", {"command": "set_mode", "args": {"mode": m}})[0] == 403


def test_no_broker_or_order_code_in_server_modules():
    banned = {"ib_insync", "ibapi", "ib_async", "alpaca", "requests", "httpx", "aiohttp", "websockets", "websocket", "socket", "smtplib", "ftplib"}
    for f in (ROOT / "command_center").glob("*.py"):
        if f.name.startswith(("verify_", "repro", "seed_demo", "emit", "load_events")):
            continue
        for node in ast.walk(ast.parse(f.read_text())):
            names = [a.name.split(".")[0] for a in node.names] if isinstance(node, ast.Import) else \
                    [node.module.split(".")[0]] if isinstance(node, ast.ImportFrom) and node.module else []
            assert not banned & set(names), (f.name, names)
            if f.name != "jobs.py":   # the single subprocess site is jobs.subprocess_runner (argv list, no shell)
                assert "subprocess" not in names and "os.system" not in f.read_text(), f.name
        src = f.read_text()
        assert "shell=True" not in src and "os.system" not in src and "eval(" not in src
    router.load_modules()
    for m, rx, _ in router.ROUTES:   # state-changing routes: no order/execution/broker vocabulary
        if m == "POST":
            assert not re.search(r"order|trade|execute|broker|position", rx.pattern, re.I), rx.pattern


# ---- open findings (strict xfail reproducers)
def test_content_type_must_be_exactly_json(srv):
    assert call(srv[0], "POST", "/api/prefs", {"key": "ct1", "value": 1}, headers={"Content-Type": "text/plain;x=application/json"})[0] == 400


def test_malformed_bodies_get_a_400(srv):
    s, _ = srv
    pre = b"POST /api/prefs HTTP/1.1\r\nHost: 127.0.0.1\r\nX-CC-Token: " + TOKEN.encode() + b"\r\nContent-Type: application/json\r\n"
    assert b"400" in rawsock(s, pre + b"Content-Length: abc\r\n\r\n{}")
    body = b'{"key":"a","value":"\xff\xfe"}'
    assert b"400" in rawsock(s, pre + b"Content-Length: %d\r\n\r\n" % len(body) + body)


def test_symbol_regex_rejects_trailing_newline():
    assert api_research.SYM_RE.match("SPY\n") is None


def test_security_headers_present(srv):
    _, _, r = call(srv[0], "GET", "/")
    assert r.getheader("Content-Security-Policy") and "frame-ancestors" in (r.getheader("Content-Security-Policy") or "")


@pytest.mark.skipif(not (ROOT / "data" / "raw" / "ibkr_connector" / "MANIFEST.json").exists()
                    or not (ROOT / "data" / "processed" / "SPY_1d.csv").exists(), reason="real daily data not present")
def test_sealed_test_period_prices_are_not_served_unmarked(srv):
    sys.path.insert(0, str(ROOT / "src"))
    import pandas as pd
    from tradelab.validation.splits import make_split
    s, _ = srv
    st, b, _ = call(s, "GET", "/api/bars?symbol=SPY&exchange=ARCA&interval=1D")
    assert st == 200
    t = [x["time"] for x in b["bars"]]                      # only timestamps are used here, never prices
    split = make_split(pd.Timestamp(t[0], unit="s", tz="UTC"), pd.Timestamp(t[-1], unit="s", tz="UTC"), (0.6, 0.2, 0.2), pd.Timedelta(days=3))
    cut = split.test[0].timestamp()
    sealed = [x for x in t if x >= cut]
    assert not sealed or b["meta"].get("sealed_from") is not None, f"{len(sealed)} sealed-period bars served unmarked"
