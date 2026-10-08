#!/usr/bin/env python3
"""Seed clearly-labelled DEMO events (source='demo'). Not real activity: no real agents, trades, P&L or test results.

Deterministic event_ids (demo-NNNN) so re-seeding is idempotent (duplicates are ignored and reported).
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from emit import load_token, post  # noqa: E402


def demo_events(run_id="demo-run-1", base=None):
    base = base or (datetime.now(timezone.utc) - timedelta(minutes=10))
    out, n = [], [0]

    def ev(dt, t, agent=None, **payload):
        n[0] += 1
        e = {"event_id": f"{run_id}-{n[0]:04d}", "event_type": t, "run_id": run_id, "source": "demo",
             "timestamp_utc": (base + timedelta(seconds=dt)).strftime("%Y-%m-%dT%H:%M:%S.000Z"), "payload": payload}
        if agent: e["agent_id"] = agent
        out.append(e)

    ev(0, "mode.changed", mode="DEMO", note="DEMO seed data - not real activity")
    for a, s, tk in [("lead", "working", "DEMO: plan research slice"), ("data_ibkr", "queued", None), ("strategy_researcher", "queued", None),
                     ("backtester", "queued", None), ("validator", "queued", None), ("risk_execution", "idle", None), ("monitor", "working", "DEMO: watch event feed")]:
        ev(1, "agent.status", a, status=s, task=tk)
    ev(3, "task.created", "lead", task_id="D1", title="DEMO: define data request", owner="data_ibkr")
    ev(3, "task.created", "lead", task_id="D2", title="DEMO: draft strategy spec", owner="strategy_researcher")
    ev(3, "task.created", "lead", task_id="D3", title="DEMO: run baseline backtest", owner="backtester")
    ev(3, "task.created", "lead", task_id="D4", title="DEMO: validate split & costs", owner="validator")
    ev(4, "task.dependency", task_id="D3", depends_on="D1")
    ev(4, "task.dependency", task_id="D3", depends_on="D2")
    ev(4, "task.dependency", task_id="D4", depends_on="D3")
    ev(6, "message.sent", "lead", sender="lead", recipient="data_ibkr", kind="delegation", preview="DEMO: please prepare the data request")
    ev(7, "agent.status", "data_ibkr", status="working", task="DEMO: prepare data request")
    ev(7, "task.updated", "data_ibkr", task_id="D1", status="working")
    ev(8, "message.sent", "lead", sender="lead", recipient="strategy_researcher", kind="delegation", preview="DEMO: draft the strategy spec")
    ev(9, "agent.status", "strategy_researcher", status="working", task="DEMO: draft strategy spec")
    ev(9, "task.updated", "strategy_researcher", task_id="D2", status="working")
    ev(12, "tool.activity", "data_ibkr", tool="read_file", summary="DEMO: read docs/DATA_REQUEST.md", sources=["docs/DATA_REQUEST.md"])
    ev(14, "agent.status", "backtester", status="awaiting_dependency", task="DEMO: run baseline backtest", blockers=["DEMO: waiting for data + spec"])
    ev(14, "task.updated", "backtester", task_id="D3", status="awaiting_dependency")
    ev(18, "artifact.created", "data_ibkr", path="docs/DATA_REQUEST.md", title="DEMO artifact link (existing doc, not produced by a demo agent)")
    ev(20, "task.updated", "data_ibkr", task_id="D1", status="complete")
    ev(20, "agent.status", "data_ibkr", status="complete", task="DEMO: prepare data request")
    ev(22, "message.sent", "data_ibkr", sender="data_ibkr", recipient="backtester", preview="DEMO: data request is ready")
    ev(24, "decision.summary", "strategy_researcher", summary="DEMO: keep the spec to one strategy, as per project rules", sources=["research/CLAUDE.md"])
    ev(26, "test.result", "validator", name="DEMO placeholder check", outcome="skip", detail="DEMO row only - no test was run")
    ev(28, "approval.requested", "lead", approval_id="demo-approval-1", summary="DEMO: approve moving D3 forward", requested_by="lead")
    ev(30, "incident", "monitor", severity="info", summary="DEMO: example incident row - nothing happened")
    ev(32, "heartbeat", "monitor")
    return out


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--server", default=os.environ.get("CC_URL", "http://127.0.0.1:8765"))
    p.add_argument("--token"); p.add_argument("--run-id", default="demo-run-1")
    a = p.parse_args(argv)
    code, body = post(a.server, demo_events(a.run_id), load_token(a.token))
    print(f"HTTP {code}: accepted={len(body.get('accepted', []))} duplicates={len(body.get('duplicates', []))} rejected={body.get('rejected')}")
    return 0 if code == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
