"""IBKR (read-only) API. Routes register through router.py. NO endpoint here can place, change or cancel an order.

GET  /api/ibkr/status                  connection state, plain-language last error, per-feed freshness, library check
POST /api/ibkr/connect {kind}          token. kind: paper_tws | paper_gateway (PAPER only; live ports are always refused, no override). The login must be a DU (paper) account.
POST /api/ibkr/disconnect              token
POST /api/ibkr/history                 token. {symbols:[...], bar_size:1d|1h|15m|5m|1m, years|duration, use_rth} -> queued job (one at a time)
POST /api/ibkr/history/stop {id}     token
GET  /api/ibkr/history/jobs            jobs with real progress and output files
GET  /api/ibkr/account                 read-only snapshot, account id masked
Live quotes: GET /api/quotes?live=1 (api_market.py) uses the manager when connected.
Nothing here has been run against a real TWS/Gateway.
"""
from __future__ import annotations

import re

from .router import route
from command_center.ibkr import manager as M  # our own read-only adapter package (not a third-party broker library)
from command_center.ibkr.guard import GuardError
from command_center.ibkr.readonly_client import LibraryMissing
from command_center.ibkr import errors as E
from .schemas import ValidationError

SYM_RE = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}\Z")
EX_RE = re.compile(r"[A-Z0-9]{2,12}\Z")


def get_manager():
    return M.get_manager()


def _audit(h, action: str, outcome: str, detail: dict | None = None):
    try:
        h.server.store.insert([{"event_type": "audit.config", "source": "ibkr", "payload": {"area": "ibkr", "action": action, "outcome": outcome, **({"detail": detail} if detail else {})}}])
    except Exception:
        pass


@route("GET", r"/api/ibkr/status")
def status(h, m, q, body):
    st = get_manager().status()
    st["setup_guide"] = "docs/IBKR_SETUP.md"
    st["honesty"] = "Not yet run against a real TWS/Gateway. Read-only: this app cannot place, change or cancel orders."
    return h._json(200, {"ok": True, **st})


@route("POST", r"/api/ibkr/connect")
def connect(h, m, q, body):
    kind = body.get("kind") if isinstance(body, dict) else None
    if not isinstance(kind, str):
        raise ValidationError("kind is required")
    mgr = get_manager()
    try:
        st = mgr.connect(kind)
    except GuardError as e:
        _audit(h, "connect", "refused", {"kind": kind[:40]})
        return h._err(400, str(e), error_code="refused_by_guard", info=E.explain_exception(e))
    except LibraryMissing as e:
        _audit(h, "connect", "failed", {"kind": kind[:40], "why": "ib_async missing"})
        return h._err(400, str(e), error_code="library_missing", info=E.explain_exception(e))
    _audit(h, "connect", "applied", {"kind": kind, "port": st.get("port")})
    return h._json(200, {"ok": True, **st})


@route("POST", r"/api/ibkr/disconnect")
def disconnect(h, m, q, body):
    st = get_manager().disconnect()
    _audit(h, "disconnect", "applied")
    return h._json(200, {"ok": True, **st})


def _items(raw):
    if not isinstance(raw, list) or not raw or len(raw) > 10:
        raise ValidationError("symbols must be a list of 1 to 10 instruments")
    out = []
    for r in raw:
        d = r if isinstance(r, dict) else {"symbol": r}
        sym = str(d.get("symbol", "")).upper()
        if not SYM_RE.fullmatch(sym):
            raise ValidationError(f"invalid symbol {str(d.get('symbol'))[:12]!r}")
        ex = d.get("exchange")
        if ex not in (None, ""):
            ex = str(ex).upper()
            if not EX_RE.fullmatch(ex):
                raise ValidationError("invalid exchange")
        else:
            ex = None
        cid = d.get("conid")
        if cid not in (None, ""):
            if isinstance(cid, bool) or not isinstance(cid, int) or cid <= 0:
                raise ValidationError("conid must be a positive integer")
        else:
            cid = None
        out.append({"symbol": sym, "exchange": ex, "conid": cid, "currency": "USD"})
    return out


@route("POST", r"/api/ibkr/history")
def history(h, m, q, body):
    if not isinstance(body, dict):
        raise ValidationError("body must be an object")
    mgr = get_manager()
    try:
        items = _items(body.get("symbols"))
        job = mgr.submit_history(items, body.get("bar_size"), body.get("years"), body.get("duration"), body.get("use_rth", True))
    except ValueError as e:
        return h._err(400, str(e))
    except RuntimeError as e:
        return h._err(409, str(e))
    _audit(h, "history_download", "applied", {"job": job["id"], "bar_size": job["bar_size"], "n": len(items)})
    return h._json(200, {"ok": True, "job": job, "note": "Writes ONLY to a new data/processed_<date>/ snapshot (data/fake_<date>/ in simulated mode); the frozen data/processed/ is never touched."})


@route("POST", r"/api/ibkr/history/stop")
def history_stop(h, m, q, body):
    jid = body.get("id") if isinstance(body, dict) else None
    if not isinstance(jid, str) or not re.fullmatch(r"[0-9a-f]{12}", jid):
        raise ValidationError("id required")
    return h._json(200, {"ok": get_manager().cancel_job(jid)})


@route("GET", r"/api/ibkr/history/jobs")
def jobs(h, m, q, body):
    return h._json(200, {"ok": True, "jobs": get_manager().list_jobs()})


@route("GET", r"/api/ibkr/account")
def account(h, m, q, body):
    mgr = get_manager()
    snap = mgr.account_snapshot()
    if not snap:
        return h._json(200, {"ok": True, "connected": False, "reason": "not connected to TWS/Gateway", "values": {}})
    vals = {k: {"value": v[0], "currency": v[1]} for k, v in snap["values"].items()}
    return h._json(200, {"ok": True, "connected": True, "fake": mgr.fake, "account_masked": M.mask_account(snap["account"]), "as_of": snap["as_of"], "values": vals, "pnl": snap["pnl"],
                         "n_positions": len(snap["positions"]), "n_open_orders": len(snap["open_orders"]), "n_executions": len(snap["executions"]),
                         "note": "Read-only snapshot. Orders/executions are hidden from the API while TWS 'Read-Only API' is ON (official docs)."})
