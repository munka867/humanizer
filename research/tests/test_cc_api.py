"""Command-centre HTTP API: auth, SSE replay, commands, static UI labelling."""
import http.client
import json
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from command_center.server import make_server  # noqa: E402

TOKEN = "test-token"
STATIC = ROOT / "command_center" / "static"


@pytest.fixture()
def srv(tmp_path):
    s = make_server(tmp_path / "e.db", port=0, token=TOKEN, docs_dir=tmp_path / "docs", results_dir=tmp_path / "res")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "A.md").write_text("# Alpha doc\nbody")
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s
    s.stopping = True
    s.shutdown()
    s.server_close()


def call(srv, method, path, body=None, token=TOKEN, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    h = {"Content-Type": "application/json"}
    if token:
        h["X-CC-Token"] = token
    h.update(headers or {})
    c.request(method, path, json.dumps(body) if body is not None else None, h)
    r = c.getresponse()
    raw = r.read()
    try:
        return r.status, json.loads(raw)
    except ValueError:
        return r.status, raw


def ev(i, **kw):
    e = {"event_id": f"e{i}", "event_type": "message.sent", "source": "test", "run_id": "r1",
         "payload": {"sender": "lead", "recipient": "validator", "preview": f"m{i}"}}
    e.update(kw)
    return e


def read_sse(srv, last_id, n, path="/api/stream?run_id=r1", extra=None, timeout=6):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=timeout)
    c.request("GET", path, headers={"Last-Event-ID": str(last_id)})
    r = c.getresponse()
    assert r.status == 200 and r.getheader("Content-Type").startswith("text/event-stream")
    got, cur = [], {}
    if extra:
        threading.Thread(target=extra, daemon=True).start()
    while len(got) < n:
        line = r.fp.readline().decode().rstrip("\n")
        if line == "":
            if cur.get("event") == "cc":
                got.append((int(cur["id"]), json.loads(cur["data"])))
            cur = {}
        elif not line.startswith(":"):
            k, _, v = line.partition(": ")
            cur[k] = v
    c.close()
    return got


def test_post_requires_token(srv):
    assert call(srv, "POST", "/api/events", ev(1), token=None)[0] == 401
    assert call(srv, "POST", "/api/events", ev(1), token="wrong")[0] == 401
    assert call(srv, "POST", "/api/commands", {"command": "cancel_research"}, token=None)[0] == 401
    assert call(srv, "GET", "/api/snapshot")[0] == 200
    assert srv.store.count() == 0


def test_rejects_non_local_host_and_non_json(srv):
    assert call(srv, "GET", "/api/health", headers={"Host": "evil.example.com"})[0] == 403
    assert call(srv, "POST", "/api/events", ev(1), headers={"Origin": "http://evil.example.com"})[0] == 403
    assert call(srv, "POST", "/api/events", ev(1), headers={"Content-Type": "text/plain"})[0] == 400


def test_single_batch_and_dedupe(srv):
    s, b = call(srv, "POST", "/api/events", ev(1))
    assert s == 200 and b["accepted"][0]["seq"] == 1
    s, b = call(srv, "POST", "/api/events", [ev(1), ev(2), ev(3, event_type="bad")])
    assert s == 200 and b["duplicates"] == ["e1"] and len(b["accepted"]) == 1 and b["rejected"][0]["index"] == 2
    s, b = call(srv, "POST", "/api/events", ev(9, event_type="bad"))
    assert s == 400
    s, b = call(srv, "GET", "/api/events?after_seq=1&limit=10")
    assert [e["seq"] for e in b["events"]] == [2]


def test_sse_replay_no_gaps_no_dupes_then_live(srv):
    call(srv, "POST", "/api/events", [ev(i) for i in range(1, 9)])

    def later():
        time.sleep(0.6)
        call(srv, "POST", "/api/events", [ev(9), ev(10)])

    got = read_sse(srv, 3, 7, extra=later)
    seqs = [g[0] for g in got]
    assert seqs == [4, 5, 6, 7, 8, 9, 10]
    assert [g[1]["event_id"] for g in got] == [f"e{i}" for i in range(4, 11)]


def test_sse_run_filter(srv):
    call(srv, "POST", "/api/events", [ev(1), ev(2, run_id="other"), ev(3)])
    assert [g[0] for g in read_sse(srv, 0, 2)] == [1, 3]


def test_sse_sends_ping_for_staleness(srv, monkeypatch):
    import command_center.server as sm
    monkeypatch.setattr(sm, "SSE_PING_S", 0.2)
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
    c.request("GET", "/api/stream")
    r = c.getresponse()
    seen = ""
    t0 = time.time()
    while "event: ping" not in seen and time.time() - t0 < 4:
        seen += r.fp.readline().decode()
    assert "event: ping" in seen and ": hb" in seen


def test_commands(srv):
    call(srv, "POST", "/api/events", [ev(1), {"event_id": "ap", "event_type": "approval.requested", "source": "test", "run_id": "r1",
                                              "payload": {"approval_id": "x1", "summary": "s"}}])
    s, b = call(srv, "POST", "/api/commands", {"command": "cancel_research", "run_id": "r1"})
    assert s == 200 and b["runtime_attached"] is False and "no runtime attached" in b["message"]
    assert "never touches positions or protective orders" in b["message"]
    s, b = call(srv, "POST", "/api/commands", {"command": "assign_task", "args": {"agent_id": "backtester", "title": "t"}, "run_id": "r1"})
    assert s == 200 and "NOT started" in b["message"]
    assert call(srv, "POST", "/api/commands", {"command": "assign_task", "args": {"agent_id": "ghost", "title": "t"}})[0] == 400
    for bad in (0, 4, "2", True, None):
        assert call(srv, "POST", "/api/commands", {"command": "set_concurrency", "args": {"value": bad}, "run_id": "r1"})[0] == 400
    for good in (1, 3):
        assert call(srv, "POST", "/api/commands", {"command": "set_concurrency", "args": {"value": good}, "run_id": "r1"})[0] == 200
    assert call(srv, "POST", "/api/commands", {"command": "approve_proposal", "args": {"approval_id": "nope"}, "run_id": "r1"})[0] == 409
    s, b = call(srv, "POST", "/api/commands", {"command": "approve_proposal", "args": {"approval_id": "x1"}, "run_id": "r1"})
    assert s == 200
    for m in ("PAPER", "LIVE"):
        s, b = call(srv, "POST", "/api/commands", {"command": "set_mode", "args": {"mode": m}, "run_id": "r1"})
        assert s == 403 and "requires approval & broker adapter" in b["message"]
    assert call(srv, "POST", "/api/commands", {"command": "set_mode", "args": {"mode": "BACKTEST"}, "run_id": "r1"})[0] == 200
    snap = call(srv, "GET", "/api/snapshot?run_id=r1")[1]
    assert snap["mode"] == "BACKTEST" and snap["concurrency"]["recorded"] == 3 and snap["concurrency"]["enforced"] is False
    assert all(a["resolution"] for a in snap["approvals"])
    assert any(not c["accepted"] for c in snap["commands"])  # refused commands are audited too
    assert call(srv, "POST", "/api/commands", {"command": "launch_missiles"})[0] == 400


def test_event_posting_cannot_set_live_mode(srv):
    s, b = call(srv, "POST", "/api/events", {"event_type": "mode.changed", "source": "x", "payload": {"mode": "LIVE"}})
    assert s == 400


def test_demo_labelling_snapshot_and_ui(srv):
    call(srv, "POST", "/api/events", ev(1, source="demo"))
    snap = call(srv, "GET", "/api/snapshot")[1]
    assert snap["has_demo"] is True and snap["mode"] == "DEMO" and "demo" in snap["sources"]
    assert any(a["demo"] for a in snap["agents"])
    s, html = call(srv, "GET", "/")
    assert s == 200 and b'id="banner-demo"' in html and b"source='demo'" in html
    js = (STATIC / "app.js").read_text()
    assert "s.has_demo" in js and "tag-demo" in js
    assert call(srv, "GET", "/api/runs")[1]["runs"][0]["has_demo"] is True


def test_ui_static_contract(srv):
    js = (STATIC / "app.js").read_text()
    assert "STALE: showing last known state as of" in js
    assert "Private reasoning is not shown" in js
    assert "blocked: requires approval & broker adapter" in js
    assert "prefers-reduced-motion" in (STATIC / "style.css").read_text()
    assert call(srv, "GET", "/static/../server.py")[0] == 404


def test_docs_index_and_runs(srv):
    s, b = call(srv, "GET", "/api/docs")
    assert b["items"][0]["title"] == "Alpha doc"
    assert call(srv, "GET", "/api/docs/A.md")[0] == 200
    assert call(srv, "GET", "/api/docs/..%2Fx.md")[0] == 404
    s, b = call(srv, "GET", "/api/results")
    assert b["items"] == []


def test_search_and_snapshot_upto(srv):
    call(srv, "POST", "/api/events", [ev(1), ev(2, payload={"sender": "lead", "recipient": "validator", "preview": "zebra"})])
    assert len(call(srv, "GET", "/api/search?q=zebra")[1]["events"]) == 1
    assert call(srv, "GET", "/api/search?q=")[0] == 400
    assert call(srv, "GET", "/api/snapshot?run_id=r1&upto_seq=1")[1]["event_count"] == 1
