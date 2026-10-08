"""Command-centre: schemas, append-only store, derived state, stale logic, static no-broker check."""
import ast
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from command_center import derive  # noqa: E402
from command_center.schemas import ROLES, ValidationError, validate_event  # noqa: E402
from command_center.store import Store  # noqa: E402

CC = ROOT / "command_center"


def msg(eid="m1", **kw):
    e = {"event_id": eid, "event_type": "message.sent", "source": "test", "run_id": "r1",
         "payload": {"sender": "lead", "recipient": "backtester", "preview": "hi"}}
    e.update(kw)
    return e


def test_roles_exact():
    assert list(ROLES) == ["lead", "data_ibkr", "strategy_researcher", "backtester", "validator", "risk_execution", "monitor"]


def test_idempotent_insert_reports_duplicates(tmp_path):
    s = Store(tmp_path / "e.db")
    r1 = s.insert([msg("a"), msg("b")])
    assert [x["seq"] for x in r1["accepted"]] == [1, 2]
    r2 = s.insert([msg("a"), msg("c")])
    assert r2["duplicates"] == ["a"] and len(r2["accepted"]) == 1 and r2["accepted"][0]["seq"] == 3
    assert s.count() == 3


def test_append_only(tmp_path):
    import sqlite3
    s = Store(tmp_path / "e.db")
    s.insert([msg("a")])
    with pytest.raises(sqlite3.DatabaseError):
        s._db.execute("UPDATE events SET source='x'")
    with pytest.raises(sqlite3.DatabaseError):
        s._db.execute("DELETE FROM events")


@pytest.mark.parametrize("mutate,err", [
    (lambda e: e.update(event_type="nope"), "event_type"),
    (lambda e: e.update(source=""), "source"),
    (lambda e: e.update(agent_id="ghost"), "agent_id"),
    (lambda e: e["payload"].pop("preview"), "preview"),
    (lambda e: e["payload"].update(recipient="ghost"), "recipient"),
    (lambda e: e.update(payload_version=99), "payload_version"),
    (lambda e: e.update(timestamp_utc="not-a-date"), "timestamp"),
    (lambda e: e.update(timestamp_utc="2026-01-01T00:00:00"), "timezone"),
    (lambda e: e.update(status="bogus"), "status"),
])
def test_schema_validation_rejects(mutate, err):
    e = msg()
    mutate(e)
    with pytest.raises(ValidationError, match=err):
        validate_event(e)


def test_type_specific_requirements():
    with pytest.raises(ValidationError):
        validate_event({"event_type": "agent.status", "source": "t", "payload": {"status": "working"}})  # needs agent_id
    with pytest.raises(ValidationError):
        validate_event({"event_type": "agent.status", "agent_id": "lead", "source": "t", "payload": {"status": "50%"}})
    with pytest.raises(ValidationError, match="PAPER"):
        validate_event({"event_type": "mode.changed", "source": "t", "payload": {"mode": "PAPER"}})
    with pytest.raises(ValidationError, match="LIVE"):
        validate_event({"event_type": "mode.changed", "source": "t", "payload": {"mode": "LIVE"}})
    ok = validate_event({"event_type": "agent.status", "agent_id": "lead", "source": "t", "payload": {"status": "working"}})
    assert ok["timestamp_utc"].endswith("Z") and ok["run_id"] == "live"


def test_invalid_events_rejected_not_stored(tmp_path):
    s = Store(tmp_path / "e.db")
    r = s.insert([msg("ok"), msg("bad", event_type="zzz")])
    assert len(r["accepted"]) == 1 and r["rejected"][0]["index"] == 1 and s.count() == 1


def test_restart_persistence_new_process(tmp_path):
    db = tmp_path / "e.db"
    s = Store(db)
    s.insert([msg("a"), msg("b")])
    s.close()
    out = subprocess.run([sys.executable, "-c",
                          "import sys; sys.path.insert(0, %r); from command_center.store import Store; "
                          "s=Store(%r); print([e['event_id'] for e in s.events()], s.max_seq())" % (str(ROOT), str(db))],
                         capture_output=True, text=True, check=True).stdout
    assert "['a', 'b'] 2" in out


def test_snapshot_derivation_and_no_invented_usage(tmp_path):
    s = Store(tmp_path / "e.db")
    s.insert([
        {"event_id": "1", "event_type": "agent.status", "agent_id": "backtester", "source": "demo", "run_id": "r", "payload": {"status": "working", "task": "t"}},
        {"event_id": "2", "event_type": "agent.status", "agent_id": "lead", "source": "demo", "run_id": "r",
         "payload": {"status": "idle", "usage": {"input_tokens": 12}}},
        {"event_id": "3", "event_type": "task.created", "source": "demo", "run_id": "r", "payload": {"task_id": "A", "title": "a", "owner": "data_ibkr"}},
        {"event_id": "4", "event_type": "task.created", "source": "demo", "run_id": "r", "payload": {"task_id": "B", "title": "b", "owner": "backtester", "status": "awaiting_dependency"}},
        {"event_id": "5", "event_type": "task.dependency", "source": "demo", "run_id": "r", "payload": {"task_id": "B", "depends_on": "A"}},
    ])
    snap = derive.snapshot(s.events(run_id="r"), "r", s.max_seq())
    ag = {a["agent_id"]: a for a in snap["agents"]}
    assert ag["backtester"]["usage"] is None and ag["backtester"]["usage_note"] is None  # never invented
    assert ag["lead"]["usage"] == {"input_tokens": 12}
    assert ag["data_ibkr"]["status"] is None and not ag["data_ibkr"]["has_events"]
    assert any("waits on: A" in b for b in ag["backtester"]["blockers"])
    assert snap["tasks"][1]["unmet"] == ["A"] and snap["dependencies"][0]["from_agent"] == "data_ibkr"
    assert "percent" not in str(snap).lower()


def test_demo_labelling_in_snapshot(tmp_path):
    s = Store(tmp_path / "e.db")
    s.insert([msg("d", source="demo")])
    assert derive.snapshot(s.events(), "r1", 1)["has_demo"] is True
    s2 = Store(tmp_path / "f.db")
    s2.insert([msg("x", source="emit")])
    assert derive.snapshot(s2.events(), "r1", 1)["has_demo"] is False


def test_stale_state_logic():
    assert derive.stale_state(None, 100)["state"] == "connecting"
    assert derive.stale_state(100, 110, 15)["state"] == "live"
    assert derive.stale_state(100, 116, 15)["state"] == "stale"
    assert derive.stale_state(100, 116, 15)["as_of"] == 100  # UI shows last known time, never "now"


def test_search_and_runs(tmp_path):
    s = Store(tmp_path / "e.db")
    s.insert([msg("a", payload={"sender": "lead", "recipient": "backtester", "preview": "alpha needle"}), msg("b", run_id="r2")])
    assert [e["event_id"] for e in s.search("needle")] == ["a"]
    assert s.search("100%") == []
    assert {r["run_id"] for r in s.runs()} == {"r1", "r2"}


def test_no_broker_imports():
    banned = ("ibapi", "ib_insync", "ib_async", "ibkr", "ccxt", "alpaca", "tradier", "oandapyv20", "robin_stocks", "futu", "tda")
    files = list(CC.glob("*.py"))
    assert files
    for f in files:
        tree = ast.parse(f.read_text())
        for n in ast.walk(tree):
            mods = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""] if isinstance(n, ast.ImportFrom) else []
            for m in mods:
                assert not any(m.split(".")[0].lower().startswith(b) for b in banned), f"{f.name} imports {m}"
    for f in (CC / "static").glob("*.js"):
        txt = f.read_text()
        assert "https://" not in txt.replace("https?://", "") and "cdn" not in txt.lower()
