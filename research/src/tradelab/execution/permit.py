"""PaperOrderPermit: proof that ExecutionService ran assert_may_submit(PAPER) for THIS order, just now.

* Constructible only through `mint_permit`, which refuses any caller outside tradelab.execution.service.
* Tamper-evident: fields are signed with a per-process random secret, so a hand-built or edited permit fails verification.
* Expiring (default 60 s), bound to one intent id and the exact order fields, and SINGLE USE (process-wide consumed set).
Real broker adapters call `consume_permit` before touching the network. This protects against mistakes and casual misuse in
one process; it is not a defence against hostile code running inside the same Python process."""
from __future__ import annotations

import hmac
import hashlib
import secrets
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from tradelab.execution.common import canon, iso, parse_iso

_SECRET = secrets.token_bytes(32)
_MINT = object()
_CONSUMED: set[str] = set()
_LOCK = threading.Lock()
TTL_SECONDS = 60


class PermitError(RuntimeError):
    pass


@dataclass(frozen=True)
class PaperOrderPermit:
    permit_id: str
    intent_id: str
    client_order_id: str
    account_id: str
    con_id: int
    side: int
    qty: str
    order_type: str
    limit_px: str | None
    stop_px: str | None
    tif: str
    outside_rth: bool
    issued_utc: str
    expires_utc: str
    sig: str
    _mint: object = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if self._mint is not _MINT:
            raise PermitError("PaperOrderPermit can only be created by ExecutionService (mint_permit)")

    def body(self) -> dict:
        return {k: getattr(self, k) for k in ("permit_id", "intent_id", "client_order_id", "account_id", "con_id", "side", "qty", "order_type",
                                              "limit_px", "stop_px", "tif", "outside_rth", "issued_utc", "expires_utc")}


def _sign(body: dict) -> str:
    return hmac.new(_SECRET, canon(body).encode(), hashlib.sha256).hexdigest()


def mint_permit(approval: dict, *, intent_id: str, client_order_id: str, con_id: int, side: int, qty, order_type: str, limit_px,
                stop_px, tif: str, outside_rth: bool, now: datetime, ttl_seconds: int = TTL_SECONDS) -> PaperOrderPermit:
    caller = sys._getframe(1).f_globals.get("__name__")
    if caller != "tradelab.execution.service":
        raise PermitError(f"permits may only be minted by ExecutionService (called from {caller})")
    body = dict(permit_id=secrets.token_hex(12), intent_id=intent_id, client_order_id=client_order_id, account_id=str(approval["account_id"]),
                con_id=int(con_id), side=int(side), qty=str(qty), order_type=order_type,
                limit_px=None if limit_px is None else str(limit_px), stop_px=None if stop_px is None else str(stop_px), tif=tif,
                outside_rth=bool(outside_rth), issued_utc=iso(now), expires_utc=iso(now + timedelta(seconds=ttl_seconds)))
    return PaperOrderPermit(sig=_sign(body), _mint=_MINT, **body)


def consume_permit(permit, req, now: datetime) -> PaperOrderPermit:
    """Validate and burn a permit for `req` (an OrderRequest). Raises PermitError with a plain-language reason."""
    if not isinstance(permit, PaperOrderPermit):
        raise PermitError("no valid order permit supplied")
    if not hmac.compare_digest(_sign(permit.body()), permit.sig):
        raise PermitError("permit signature invalid (edited or forged permit)")
    if now >= parse_iso(permit.expires_utc) or now < parse_iso(permit.issued_utc) - timedelta(seconds=5):
        raise PermitError("permit expired or not yet valid")
    checks = {"intent_id": req.intent_id, "client_order_id": req.client_order_id, "con_id": req.con_id, "side": req.side,
              "qty": str(req.qty), "order_type": req.order_type, "limit_px": None if req.limit_px is None else str(req.limit_px),
              "stop_px": None if req.stop_px is None else str(req.stop_px), "tif": req.tif, "outside_rth": bool(req.outside_rth)}
    for k, v in checks.items():
        if getattr(permit, k) != v:
            raise PermitError(f"permit does not match the order ({k})")
    with _LOCK:
        if permit.permit_id in _CONSUMED:
            raise PermitError("permit already used (single use)")
        _CONSUMED.add(permit.permit_id)
    return permit
