"""Versioned event schemas and validation for the command-centre event store.

Pure stdlib. No broker / trading imports (enforced by tests).
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

ROLES = {
    "lead": "Lead / Coordinator",
    "data_ibkr": "Data (IBKR read-only market data)",
    "strategy_researcher": "Strategy Researcher",
    "backtester": "Backtester",
    "validator": "Validator",
    "risk_execution": "Risk & Execution",
    "monitor": "Monitor",
}
AGENT_STATUSES = ("queued", "working", "awaiting_dependency", "awaiting_approval", "idle", "failed", "complete")
TASK_STATUSES = ("queued", "working", "awaiting_dependency", "awaiting_approval", "failed", "complete")
MODES = ("DEMO", "BACKTEST", "REPLAY", "SHADOW", "PAPER", "LIVE")
SELECTABLE_MODES = ("DEMO", "BACKTEST", "REPLAY")
BLOCKED_MODES = {
    "PAPER": "blocked: requires approval & broker adapter",
    "LIVE": "blocked: requires approval & broker adapter",
    "SHADOW": "not implemented in this slice: requires a market-data feed and a shadow-order recorder",
}
COMMANDS = ("assign_task", "request_update", "cancel_research", "approve_proposal", "set_concurrency", "set_mode")
EVENT_TYPES = (
    "agent.status", "task.created", "task.updated", "task.dependency", "message.sent",
    "tool.activity", "artifact.created", "test.result", "decision.summary", "incident",
    "mode.changed", "approval.requested", "approval.resolved", "heartbeat", "command.recorded", "audit.config",
)
MAX_PAYLOAD_BYTES = 64 * 1024
_ID_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,128}$")


class ValidationError(ValueError):
    pass


def _str(v, name, maxlen=2000):
    if not isinstance(v, str) or not v.strip():
        raise ValidationError(f"payload.{name} must be a non-empty string")
    if len(v) > maxlen:
        raise ValidationError(f"payload.{name} too long (>{maxlen})")


def _opt_str(v, name):
    if v is not None:
        _str(v, name)


def _enum(values):
    def check(v, name):
        if v not in values:
            raise ValidationError(f"payload.{name} must be one of {list(values)}")
    return check


def _agent(v, name):
    if v not in ROLES:
        raise ValidationError(f"payload.{name} must be one of {list(ROLES)}")


def _agent_or_user(v, name):
    if v not in ROLES and v != "user":
        raise ValidationError(f"payload.{name} must be one of {list(ROLES)} or 'user'")


def _list(v, name):
    if not isinstance(v, list):
        raise ValidationError(f"payload.{name} must be a list")


def _dict(v, name):
    if not isinstance(v, dict):
        raise ValidationError(f"payload.{name} must be an object")


def _bool(v, name):
    if not isinstance(v, bool):
        raise ValidationError(f"payload.{name} must be a boolean")


def _msg(v, name):
    _str(v, name, 500)


_SECRET_KEY_RE = re.compile(r"token|secret|passw|api[_-]?key|credential|authorization", re.I)


def _no_secret_keys(v, name, depth=0):
    if depth > 6:
        raise ValidationError(f"payload.{name} nested too deeply")
    if isinstance(v, dict):
        for k, x in v.items():
            if _SECRET_KEY_RE.search(str(k)):
                raise ValidationError(f"payload.{name} must not carry secrets (key {k!r}); audit records never store credentials")
            _no_secret_keys(x, name, depth + 1)
    elif isinstance(v, list):
        for x in v:
            _no_secret_keys(x, name, depth + 1)


def _jsonval(v, name):
    """Small JSON value (scalar / small structure) used for audit old/new values."""
    try:
        blob = json.dumps(v)
    except (TypeError, ValueError):
        raise ValidationError(f"payload.{name} must be JSON-serialisable") from None
    if len(blob) > 2000:
        raise ValidationError(f"payload.{name} too long (>2000 chars as JSON)")
    _no_secret_keys(v, name)


def _audit_detail(v, name):
    _dict(v, name)
    _jsonval(v, name)


def _label(v, name):
    _str(v, name, 128)
    if not re.fullmatch(r"[A-Za-z0-9_.:\- ]{1,128}", v):
        raise ValidationError(f"payload.{name} may only contain letters, digits and _ . : - space")


# type -> version -> {"required": {field: check}, "optional": {field: check}, "needs_agent": bool}
SCHEMAS: dict[str, dict[int, dict[str, Any]]] = {
    "agent.status": {1: {"needs_agent": True, "required": {"status": _enum(AGENT_STATUSES)},
                         "optional": {"task": _opt_str, "blockers": _list, "usage": _dict}}},
    "task.created": {1: {"required": {"task_id": _str, "title": _str},
                         "optional": {"owner": _agent, "status": _enum(TASK_STATUSES)}}},
    "task.updated": {1: {"required": {"task_id": _str},
                         "optional": {"status": _enum(TASK_STATUSES), "owner": _agent, "title": _str}}},
    "task.dependency": {1: {"required": {"task_id": _str, "depends_on": _str}, "optional": {}}},
    "message.sent": {1: {"required": {"sender": _agent_or_user, "recipient": _agent_or_user, "preview": _msg},
                         "optional": {"kind": _enum(("message", "delegation"))}}},
    "tool.activity": {1: {"needs_agent": True, "required": {"tool": _str, "summary": _str},
                          "optional": {"sources": _list, "target": _opt_str}}},
    "artifact.created": {1: {"required": {"path": _str}, "optional": {"title": _opt_str, "url": _opt_str, "kind": _opt_str}}},
    "test.result": {1: {"required": {"name": _str, "outcome": _enum(("pass", "fail", "skip", "error"))},
                        "optional": {"detail": _opt_str, "command": _opt_str}}},
    "decision.summary": {1: {"required": {"summary": _str}, "optional": {"sources": _list}}},
    "incident": {1: {"required": {"severity": _enum(("info", "warning", "critical")), "summary": _str}, "optional": {}}},
    "mode.changed": {1: {"required": {"mode": _enum(MODES)}, "optional": {"note": _opt_str}}},
    "approval.requested": {1: {"required": {"approval_id": _str, "summary": _str}, "optional": {"requested_by": _agent}}},
    "approval.resolved": {1: {"required": {"approval_id": _str, "resolution": _enum(("approved", "rejected", "expired"))},
                              "optional": {"note": _opt_str}}},
    "heartbeat": {1: {"needs_agent": True, "required": {}, "optional": {}}},
    "audit.config": {1: {"required": {"area": _label, "action": _label},
                         "optional": {"target": _opt_str, "old": _jsonval, "new": _jsonval,
                                      "outcome": _enum(("applied", "refused", "failed", "info")), "reason": _opt_str,
                                      "detail": _audit_detail}}},
    "command.recorded": {1: {"required": {"command": _enum(COMMANDS), "accepted": _bool, "runtime_attached": _bool, "message": _str},
                             "optional": {"args": _dict}}},
}


def utc_now_iso() -> str:
    dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def normalize_ts(ts: str) -> str:
    if not isinstance(ts, str):
        raise ValidationError("timestamp_utc must be an ISO-8601 string")
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValidationError(f"timestamp_utc invalid: {e}") from None
    if dt.tzinfo is None:
        raise ValidationError("timestamp_utc must carry a timezone (use Z)")
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def validate_event(raw: Any) -> dict:
    """Validate + normalise one incoming event; returns a dict ready to store."""
    if not isinstance(raw, dict):
        raise ValidationError("event must be a JSON object")
    et = raw.get("event_type")
    if et not in SCHEMAS:
        raise ValidationError(f"event_type must be one of {list(EVENT_TYPES)}")
    eid = raw.get("event_id") or uuid.uuid4().hex
    if not isinstance(eid, str) or not _ID_RE.match(eid):
        raise ValidationError("event_id must match [A-Za-z0-9_.:-]{1,128}")
    ts = normalize_ts(raw["timestamp_utc"]) if raw.get("timestamp_utc") else utc_now_iso()
    run_id = raw.get("run_id") or "live"
    if not isinstance(run_id, str) or not _ID_RE.match(run_id):
        raise ValidationError("run_id must match [A-Za-z0-9_.:-]{1,128}")
    source = raw.get("source")
    if not isinstance(source, str) or not source.strip() or len(source) > 64:
        raise ValidationError("source is required (e.g. 'emit', 'hook', 'ui', 'demo')")
    agent_id = raw.get("agent_id")
    if agent_id is not None and agent_id not in ROLES:
        raise ValidationError(f"agent_id must be one of {list(ROLES)}")
    for k in ("parent_task_id", "correlation_id"):
        if raw.get(k) is not None and (not isinstance(raw[k], str) or len(raw[k]) > 128):
            raise ValidationError(f"{k} must be a string <=128 chars")
    status = raw.get("status")
    if status is not None and status not in AGENT_STATUSES:
        raise ValidationError(f"status must be one of {list(AGENT_STATUSES)}")
    ver = raw.get("payload_version", 1)
    if isinstance(ver, bool) or not isinstance(ver, int) or ver not in SCHEMAS[et]:
        raise ValidationError(f"payload_version {ver!r} unsupported for {et}; supported {list(SCHEMAS[et])}")
    payload = raw.get("payload", {})
    if not isinstance(payload, dict):
        raise ValidationError("payload must be an object")
    if et == "message.sent" and agent_id is None and payload.get("sender") in ROLES:
        agent_id = payload["sender"]
    spec = SCHEMAS[et][ver]
    if spec.get("needs_agent") and agent_id is None:
        raise ValidationError(f"{et} requires agent_id")
    for f, chk in spec["required"].items():
        if f not in payload:
            raise ValidationError(f"payload.{f} is required for {et}")
        chk(payload[f], f)
    for f, chk in spec["optional"].items():
        if f in payload and payload[f] is not None:
            chk(payload[f], f)
    if et == "mode.changed" and payload["mode"] in BLOCKED_MODES and payload["mode"] != "SHADOW":
        raise ValidationError(f"mode {payload['mode']} {BLOCKED_MODES[payload['mode']]}")
    blob = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    if len(blob.encode()) > MAX_PAYLOAD_BYTES:
        raise ValidationError("payload too large")
    return {
        "event_id": eid, "event_type": et, "timestamp_utc": ts, "run_id": run_id, "agent_id": agent_id,
        "parent_task_id": raw.get("parent_task_id"), "correlation_id": raw.get("correlation_id"),
        "source": source.strip(), "status": status, "payload_version": ver, "payload": payload,
    }
