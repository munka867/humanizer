"""Derive display state from events (single source of truth for the UI snapshot)."""
from __future__ import annotations

from datetime import datetime

from .schemas import AGENT_STATUSES, MODES, ROLES, TASK_STATUSES, utc_now_iso

STALE_AFTER_S = 15


def stale_state(last_seen_epoch, now_epoch, threshold=STALE_AFTER_S) -> dict:
    """Connection staleness: 'connecting' if nothing ever seen, 'stale' if silence > threshold."""
    if last_seen_epoch is None:
        return {"state": "connecting", "as_of": None, "silence_s": None}
    silence = now_epoch - last_seen_epoch
    return {"state": "stale" if silence > threshold else "live", "as_of": last_seen_epoch, "silence_s": silence}


def snapshot(events: list[dict], run_id, max_seq: int, concurrency_budget: int = 3, replay: bool = False) -> dict:
    server_time = utc_now_iso()
    agents = {a: {
        "agent_id": a, "role": ROLES[a], "status": None, "task": None, "status_since": None, "last_activity": None,
        "last_heartbeat": None, "completed_count": 0, "blockers": [], "usage": None, "usage_note": None,
        "message_count": 0, "has_events": False, "demo": False, "reported_blockers": [],
    } for a in ROLES}
    tasks: dict[str, dict] = {}
    deps: list[dict] = []
    messages: list[dict] = []
    artifacts: list[dict] = []
    approvals: dict[str, dict] = {}
    commands: list[dict] = []
    mode = None
    concurrency = None
    sources: set[str] = set()
    last_ts = None
    status_done: list[tuple] = []  # (agent, task text, seq) for transitions into 'complete'

    def touch(a, ts, demo):
        if a in agents:
            ag = agents[a]
            ag["has_events"] = True
            ag["last_activity"] = ts
            ag["demo"] = ag["demo"] or demo

    for e in events:
        p, t, ts, src = e["payload"], e["event_type"], e["timestamp_utc"], e["source"]
        demo = src == "demo"
        sources.add(src)
        last_ts = ts
        aid = e["agent_id"]
        if t == "agent.status":
            touch(aid, ts, demo)
            ag = agents[aid]
            if ag["status"] != p["status"]:
                ag["status_since"] = ts
                if p["status"] == "complete":
                    status_done.append((aid, p.get("task"), e["seq"]))
            ag["status"] = p["status"]
            ag["task"] = p.get("task")
            ag["reported_blockers"] = p.get("blockers") or []
            if isinstance(p.get("usage"), dict):
                ag["usage"] = p["usage"]
                ag["usage_note"] = f"reported by event {e['event_id']}"
        elif t == "heartbeat":
            if aid in agents:
                agents[aid]["last_heartbeat"] = ts
                agents[aid]["has_events"] = True
        elif t == "task.created":
            tasks[p["task_id"]] = {"task_id": p["task_id"], "title": p["title"], "owner": p.get("owner") or aid,
                                   "status": p.get("status") or "queued", "created": ts, "updated": ts,
                                   "demo": demo, "seq": e["seq"]}
            touch(aid, ts, demo)
        elif t == "task.updated":
            tk = tasks.setdefault(p["task_id"], {"task_id": p["task_id"], "title": p.get("title") or p["task_id"], "owner": aid,
                                                 "status": "queued", "created": ts, "updated": ts, "demo": demo, "seq": e["seq"]})
            for k in ("status", "owner", "title"):
                if p.get(k):
                    tk[k] = p[k]
            tk["updated"] = ts
            tk["demo"] = tk["demo"] or demo
            touch(aid, ts, demo)
        elif t == "task.dependency":
            deps.append({"task_id": p["task_id"], "depends_on": p["depends_on"], "seq": e["seq"], "demo": demo})
        elif t == "message.sent":
            messages.append({"seq": e["seq"], "timestamp_utc": ts, "sender": p["sender"], "recipient": p["recipient"],
                             "preview": p["preview"], "kind": p.get("kind", "message"), "demo": demo, "event_id": e["event_id"]})
            touch(p["sender"], ts, demo)
            if p["sender"] in agents:
                agents[p["sender"]]["message_count"] += 1
        elif t in ("tool.activity", "test.result", "decision.summary"):
            touch(aid, ts, demo)
        elif t == "artifact.created":
            artifacts.append({"seq": e["seq"], "timestamp_utc": ts, "agent_id": aid, "path": p["path"],
                              "title": p.get("title"), "url": p.get("url"), "kind": p.get("kind"), "demo": demo})
            touch(aid, ts, demo)
        elif t == "mode.changed":
            mode = p["mode"]
        elif t == "approval.requested":
            approvals[p["approval_id"]] = {"approval_id": p["approval_id"], "summary": p["summary"], "requested_by": p.get("requested_by"),
                                           "requested_at": ts, "resolution": None, "demo": demo}
        elif t == "approval.resolved":
            ap = approvals.setdefault(p["approval_id"], {"approval_id": p["approval_id"], "summary": "(request event not in this run)",
                                                         "requested_by": None, "requested_at": None, "demo": demo})
            ap["resolution"] = p["resolution"]
            ap["resolved_at"] = ts
        elif t == "command.recorded":
            commands.append({"seq": e["seq"], "timestamp_utc": ts, "command": p["command"], "accepted": p["accepted"],
                             "runtime_attached": p["runtime_attached"], "message": p["message"], "args": p.get("args", {})})
            if p["command"] == "set_concurrency" and p["accepted"]:
                concurrency = p.get("args", {}).get("value")

    # completed_count rule: distinct completed tasks owned by the agent (task.created/updated -> complete)
    # + each agent.status transition into 'complete' whose task text is not already a counted task title
    # (deduped by task text; with no task text each transition counts once).
    done_titles: dict[str, set] = {a: set() for a in agents}
    for tk in tasks.values():
        if tk["owner"] in agents and tk["status"] == "complete":
            agents[tk["owner"]]["completed_count"] += 1
            done_titles[tk["owner"]].add(tk["title"])
    counted: dict[str, set] = {a: set() for a in agents}
    for a, task_text, seq in status_done:
        key = task_text if task_text else f"#seq{seq}"
        if key in done_titles[a] or key in counted[a]:
            continue
        counted[a].add(key)
        agents[a]["completed_count"] += 1
    for tk in tasks.values():
        tk["depends_on"] = [d["depends_on"] for d in deps if d["task_id"] == tk["task_id"]]
        tk["unmet"] = [d for d in tk["depends_on"] if tasks.get(d, {}).get("status") != "complete"]
    for a, ag in agents.items():
        bl = list(ag.pop("reported_blockers", []))
        for tk in tasks.values():
            if tk["owner"] == a and tk["status"] == "awaiting_dependency" and tk["unmet"]:
                bl.append(f"task {tk['task_id']} waits on: {', '.join(tk['unmet'])}")
            if tk["owner"] == a and tk["status"] == "awaiting_approval":
                bl.append(f"task {tk['task_id']} awaits approval")
        ag["blockers"] = bl
    mode_is_default = mode is None
    if mode is None:
        mode = "DEMO" if sources == {"demo"} else "BACKTEST"
    dep_edges = []
    for d in deps:
        dep_edges.append({**d, "from_agent": tasks.get(d["depends_on"], {}).get("owner"),
                          "to_agent": tasks.get(d["task_id"], {}).get("owner")})
    return {
        "run_id": run_id, "seq": max((e["seq"] for e in events), default=0), "store_max_seq": max_seq,
        "server_time": server_time, "reference_time": (last_ts if replay and last_ts else server_time),
        "last_event_time": last_ts, "mode": mode, "mode_is_default": mode_is_default, "modes": list(MODES),
        "has_demo": "demo" in sources, "sources": sorted(sources),
        "agents": list(agents.values()), "tasks": list(tasks.values()), "dependencies": dep_edges,
        "messages": messages[-200:], "artifacts": artifacts, "approvals": list(approvals.values()),
        "commands": commands[-100:],
        "concurrency": {"recorded": concurrency, "budget_max": concurrency_budget, "enforced": False,
                        "note": "recorded only; no agent runtime is attached"},
        "statuses": list(AGENT_STATUSES), "task_statuses": list(TASK_STATUSES), "event_count": len(events),
    }
