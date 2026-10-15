"""Shared helpers: Decimal-safe money, canonical JSON, UTC clock."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, getcontext

getcontext().prec = 28


def D(x) -> Decimal:
    """Decimal from int/str/Decimal; floats go through str() (never binary float arithmetic on money)."""
    if isinstance(x, Decimal):
        return x
    if x is None:
        raise ValueError("None is not a number")
    return Decimal(str(x))


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def sha256_hex(b: bytes | str) -> str:
    return hashlib.sha256(b.encode() if isinstance(b, str) else b).hexdigest()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(ts: datetime) -> str:
    if ts.tzinfo is None:
        raise ValueError("naive datetime")
    return ts.astimezone(timezone.utc).isoformat()


def parse_iso(s: str) -> datetime:
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:
        raise ValueError("naive timestamp")
    return d.astimezone(timezone.utc)
