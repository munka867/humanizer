#!/usr/bin/env python3
"""Post a real event to the command centre.

  python command_center/emit.py --type message.sent --agent lead --recipient backtester --preview '...'
Token: --token, $CC_TOKEN, or command_center/.cc_token (written by the server when it generates one).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent


def build_event(a) -> dict:
    payload = json.loads(a.payload) if a.payload else {}
    t = a.type
    if t == "message.sent":
        payload.setdefault("sender", a.agent)
        if a.recipient: payload["recipient"] = a.recipient
        if a.preview: payload["preview"] = a.preview
        if a.kind: payload["kind"] = a.kind
    elif t == "agent.status":
        if a.status: payload["status"] = a.status
        if a.task: payload["task"] = a.task
    elif t in ("task.created", "task.updated"):
        if a.task_id: payload["task_id"] = a.task_id
        if a.title: payload["title"] = a.title
        if a.status: payload["status"] = a.status
        if a.owner: payload["owner"] = a.owner
    elif t == "task.dependency":
        if a.task_id: payload["task_id"] = a.task_id
        if a.depends_on: payload["depends_on"] = a.depends_on
    elif t == "tool.activity":
        if a.tool: payload["tool"] = a.tool
        if a.summary: payload["summary"] = a.summary
    elif t == "artifact.created":
        if a.path: payload["path"] = a.path
        if a.title: payload["title"] = a.title
        if a.url: payload["url"] = a.url
    elif t == "test.result":
        if a.name: payload["name"] = a.name
        if a.outcome: payload["outcome"] = a.outcome
        if a.summary: payload["detail"] = a.summary
    elif t == "decision.summary":
        if a.summary: payload["summary"] = a.summary
    elif t == "incident":
        if a.severity: payload["severity"] = a.severity
        if a.summary: payload["summary"] = a.summary
    elif t == "mode.changed":
        if a.mode: payload["mode"] = a.mode
    ev = {"event_type": t, "run_id": a.run_id, "source": a.source, "payload": payload}
    if a.agent: ev["agent_id"] = a.agent
    if a.event_id: ev["event_id"] = a.event_id
    if a.timestamp: ev["timestamp_utc"] = a.timestamp
    if a.parent_task_id: ev["parent_task_id"] = a.parent_task_id
    if a.correlation_id: ev["correlation_id"] = a.correlation_id
    return ev


def load_token(cli):
    if cli: return cli
    if os.environ.get("CC_TOKEN"): return os.environ["CC_TOKEN"]
    f = HERE / ".cc_token"
    return f.read_text().strip() if f.exists() else ""


def post(url, events, token):
    req = urllib.request.Request(url.rstrip("/") + "/api/events", data=json.dumps(events).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-CC-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--type", required=True)
    p.add_argument("--agent"); p.add_argument("--recipient"); p.add_argument("--preview"); p.add_argument("--kind")
    p.add_argument("--status"); p.add_argument("--task"); p.add_argument("--task-id"); p.add_argument("--title")
    p.add_argument("--owner"); p.add_argument("--depends-on"); p.add_argument("--tool"); p.add_argument("--summary")
    p.add_argument("--path"); p.add_argument("--url-link", dest="url"); p.add_argument("--name"); p.add_argument("--outcome")
    p.add_argument("--severity"); p.add_argument("--mode"); p.add_argument("--payload", help="JSON object merged into payload")
    p.add_argument("--run-id", default=os.environ.get("CC_RUN_ID", "live"))
    p.add_argument("--source", default="emit"); p.add_argument("--event-id"); p.add_argument("--timestamp")
    p.add_argument("--parent-task-id"); p.add_argument("--correlation-id")
    p.add_argument("--server", default=os.environ.get("CC_URL", "http://127.0.0.1:8765"))
    p.add_argument("--token")
    a = p.parse_args(argv)
    code, body = post(a.server, [build_event(a)], load_token(a.token))
    print(json.dumps(body))
    return 0 if code == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
