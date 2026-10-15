"""PaperOrderPermit + PermitAuthority (an explicit object capability).

ExecutionService creates ONE PermitAuthority and keeps it private. The authority holds, in closures:
  * a random secret that signs permits (tamper-evident; expiring 60 s; bound to the intent id and exact order fields; single use),
  * the approval key (CC_APPROVAL_KEY) used only to tag/verify broker `orderRef`s.
The service hands the broker adapter only the authority's verify/ref functions (`bind_authority`, one-shot). There are no module-level
secrets or mint functions and no caller-name checks.

HONEST LIMIT (P6-01): none of this can stop code that runs INSIDE the same Python process (it can read closures, call the broker
library directly, or patch these objects). The real protection is operational: the execution service runs as a separate OS process on the
user's own computer, CC_APPROVAL_KEY exists only in that process, TWS 'Read-Only API' is ON except during the user's smoke test, and no
assistant runs code there."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from tradelab.execution.common import canon, iso, parse_iso

TTL_SECONDS = 60
REF_VERSION = "v1"


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
    _consume: object = field(default=None, repr=False, compare=False)    # the issuing authority's verifier (convenience; adapters use their bound one)

    def body(self) -> dict:
        return {k: getattr(self, k) for k in ("permit_id", "intent_id", "client_order_id", "account_id", "con_id", "side", "qty", "order_type",
                                              "limit_px", "stop_px", "tif", "outside_rth", "issued_utc", "expires_utc")}


class PermitAuthority:
    def __init__(self, ref_key: bytes | None):
        secret = secrets.token_bytes(32)
        consumed: set[str] = set()
        lock = threading.Lock()
        key = ref_key

        def sign(body: dict) -> str:
            return hmac.new(secret, canon(body).encode(), hashlib.sha256).hexdigest()

        def consume(permit, req, now: datetime):
            """Validate and burn a permit for `req` (an OrderRequest). Raises PermitError with a plain-language reason."""
            if not isinstance(permit, PaperOrderPermit):
                raise PermitError("no valid order permit supplied")
            try:
                good = hmac.compare_digest(sign(permit.body()).encode(), str(permit.sig).encode())
            except Exception:
                good = False
            if not good:
                raise PermitError("permit signature invalid (edited or forged permit)")
            if now >= parse_iso(permit.expires_utc) or now < parse_iso(permit.issued_utc) - timedelta(seconds=5):
                raise PermitError("permit expired or not yet valid")
            checks = {"intent_id": req.intent_id, "client_order_id": req.client_order_id, "con_id": req.con_id, "side": req.side,
                      "qty": str(req.qty), "order_type": req.order_type, "limit_px": None if req.limit_px is None else str(req.limit_px),
                      "stop_px": None if req.stop_px is None else str(req.stop_px), "tif": req.tif, "outside_rth": bool(req.outside_rth)}
            for k, v in checks.items():
                if getattr(permit, k) != v:
                    raise PermitError(f"permit does not match the order ({k})")
            with lock:
                if permit.permit_id in consumed:
                    raise PermitError("permit already used (single use)")
                consumed.add(permit.permit_id)
            return permit

        def mint(approval: dict, *, intent_id, client_order_id, con_id, side, qty, order_type, limit_px, stop_px, tif, outside_rth, now,
                 ttl_seconds: int = TTL_SECONDS) -> PaperOrderPermit:
            body = dict(permit_id=secrets.token_hex(12), intent_id=intent_id, client_order_id=client_order_id, account_id=str(approval["account_id"]),
                        con_id=int(con_id), side=int(side), qty=str(qty), order_type=order_type,
                        limit_px=None if limit_px is None else str(limit_px), stop_px=None if stop_px is None else str(stop_px), tif=tif,
                        outside_rth=bool(outside_rth), issued_utc=iso(now), expires_utc=iso(now + timedelta(seconds=ttl_seconds)))
            return PaperOrderPermit(sig=sign(body), _consume=consume, **body)

        def _tag(intent_id: str) -> str:
            return hmac.new(key, f"orderRef|{REF_VERSION}|{intent_id}".encode(), hashlib.sha256).hexdigest()[:16]

        def make_ref(intent_id: str) -> str | None:
            """Versioned orderRef carrying an HMAC tag, so a foreign order with a similar prefix can never map to ours."""
            return None if key is None else f"{REF_VERSION}:{intent_id}:{_tag(intent_id)}"

        def parse_ref(ref) -> str | None:
            """intent id if `ref` is a well-formed, correctly tagged ref of ours; else None (= foreign)."""
            if key is None or not isinstance(ref, str):
                return None
            parts = ref.split(":")
            if len(parts) != 3 or parts[0] != REF_VERSION or len(parts[1]) != 32:
                return None
            return parts[1] if hmac.compare_digest(_tag(parts[1]).encode(), parts[2].encode("ascii", "replace")) else None

        self.mint, self.consume, self.make_ref, self.parse_ref = mint, consume, make_ref, parse_ref


def consume_permit(permit, req, now: datetime):
    """Convenience: verify with the verifier of the authority that issued `permit`. Adapters do NOT use this; they use the verifier the
    service bound to them. A permit without an issuing authority (hand-built) is refused."""
    fn = getattr(permit, "_consume", None)
    if not callable(fn):
        raise PermitError("no valid order permit supplied")
    return fn(permit, req, now)
