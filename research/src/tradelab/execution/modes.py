"""Execution modes and the submit gate.

DEMO, BACKTEST, REPLAY : may use a SIMULATED broker only.
SHADOW                 : produces intents/decisions; NEVER calls the broker port.
PAPER                  : refused unless every gate in `assert_may_submit` holds.
LIVE                   : ALWAYS refused. There is no configuration, flag or env var in this codebase that enables it.
"""
from __future__ import annotations

import enum
import hmac
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable

from tradelab.execution.common import canon, parse_iso, sha256_hex, utcnow

KEY_ENV = "CC_APPROVAL_KEY"
APPROVED_STATE = "approved_paper"
MAX_VALID_HOURS = 24.0
APPROVAL_FIELDS = ("account_id", "instruments", "strategy_id", "strategy_version", "capital_allocation",
                   "risk_policy_sha256", "issued_utc", "expires_utc")


class Mode(str, enum.Enum):
    DEMO = "DEMO"
    BACKTEST = "BACKTEST"
    REPLAY = "REPLAY"
    SHADOW = "SHADOW"
    PAPER = "PAPER"
    LIVE = "LIVE"


SIMULATED_ONLY = (Mode.DEMO, Mode.BACKTEST, Mode.REPLAY)


class ModeDenied(RuntimeError):
    """The requested action is not permitted in this mode / with this evidence."""


def sign_payload(key: bytes, payload: dict) -> str:
    return hmac.new(key, canon(payload).encode(), hashlib.sha256).hexdigest()


def make_approval(key: bytes, *, account_id: str, instruments: Iterable[int], strategy_id: str, strategy_version: str,
                  capital_allocation: str, risk_policy_sha256: str, issued_utc: datetime, expires_utc: datetime) -> dict:
    payload = {"account_id": str(account_id), "instruments": sorted(int(i) for i in instruments), "strategy_id": strategy_id,
               "strategy_version": strategy_version, "capital_allocation": str(capital_allocation),
               "risk_policy_sha256": risk_policy_sha256, "issued_utc": issued_utc.isoformat(), "expires_utc": expires_utc.isoformat()}
    return {"payload": payload, "hmac_sha256": sign_payload(key, payload)}


def _read_key(env) -> bytes:
    k = env.get(KEY_ENV)
    if not k:
        raise ModeDenied(f"PAPER refused: {KEY_ENV} is not set (only the user holds the approval key)")
    return k.encode()


def verify_approval(path: Path, key: bytes, *, now_utc: datetime) -> dict:
    path = Path(path)
    if not path.is_file():
        raise ModeDenied(f"PAPER refused: approval record missing: {path}")
    try:
        rec = json.loads(path.read_text())
        payload, mac = rec["payload"], rec["hmac_sha256"]
    except Exception as e:  # malformed
        raise ModeDenied(f"PAPER refused: approval record unreadable: {e}") from e
    if not isinstance(payload, dict) or set(payload) != set(APPROVAL_FIELDS):
        raise ModeDenied("PAPER refused: approval record has unexpected or missing fields")
    try:
        mac_ok = hmac.compare_digest(sign_payload(key, payload).encode(), str(mac).encode("utf-8", "replace"))
    except Exception:
        mac_ok = False
    if not mac_ok:
        raise ModeDenied("PAPER refused: approval HMAC does not verify (tampered or wrong key)")
    # P7-08: the CLI limits are enforced here too (a record made directly with make_approval and the key obeys them as well)
    if not str(payload["account_id"]).startswith("DU"):
        raise ModeDenied("PAPER refused: approval is not for a paper (DU) account")
    issued, expires = parse_iso(payload["issued_utc"]), parse_iso(payload["expires_utc"])
    if not (timedelta(0) < expires - issued <= timedelta(hours=MAX_VALID_HOURS)):
        raise ModeDenied(f"PAPER refused: approval validity must be > 0 and at most {MAX_VALID_HOURS:g} hours")
    if now_utc < issued:
        raise ModeDenied("PAPER refused: approval issued in the future")
    if now_utc >= expires:
        raise ModeDenied("PAPER refused: approval expired")
    return payload


def assert_may_submit(mode: Mode, *, broker_is_simulated: bool, registry_state: Callable[[str, str], str | None] | None = None,
                      strategy_id: str | None = None, strategy_version: str | None = None, instruments: Iterable[int] = (),
                      risk_policy_path: Path | None = None, approval_path: Path | None = None,
                      broker_account_id: str | None = None, now_utc: datetime | None = None, env=None,
                      engine_policy_sha256: str | None = None) -> dict | None:
    """Return normally iff an order MAY be sent to the broker port in this mode; otherwise raise ModeDenied.
    Returns the verified approval payload for PAPER, None otherwise."""
    env = os.environ if env is None else env
    now_utc = now_utc or utcnow()
    mode = Mode(mode)
    if mode is Mode.LIVE:
        raise ModeDenied("LIVE is refused unconditionally in this codebase")
    if mode is Mode.SHADOW:
        raise ModeDenied("SHADOW never calls the broker port")
    if mode in SIMULATED_ONLY:
        if not broker_is_simulated:
            raise ModeDenied(f"{mode.value} may only use a simulated broker")
        return None
    # ---- PAPER
    key = _read_key(env)
    if registry_state is None or strategy_id is None or strategy_version is None:
        raise ModeDenied("PAPER refused: no strategy registry source / strategy identity supplied")
    state = registry_state(strategy_id, strategy_version)
    if state != APPROVED_STATE:
        raise ModeDenied(f"PAPER refused: strategy state is {state!r}, need {APPROVED_STATE!r}")
    if risk_policy_path is None or not Path(risk_policy_path).is_file():
        raise ModeDenied("PAPER refused: risk policy file missing")
    if approval_path is None:
        raise ModeDenied("PAPER refused: approval record path not supplied")
    p = verify_approval(Path(approval_path), key, now_utc=now_utc)
    if sha256_hex(Path(risk_policy_path).read_bytes()) != p["risk_policy_sha256"]:
        raise ModeDenied("PAPER refused: risk policy sha256 differs from the approved one")
    if engine_policy_sha256 is not None and engine_policy_sha256 != p["risk_policy_sha256"]:
        raise ModeDenied("PAPER refused: the risk engine in use was not loaded from the approved policy (sha256 differs)")
    if (p["strategy_id"], p["strategy_version"]) != (strategy_id, strategy_version):
        raise ModeDenied("PAPER refused: approval is for a different strategy/version")
    if not {int(i) for i in instruments} <= set(p["instruments"]):
        raise ModeDenied("PAPER refused: instrument not covered by the approval")
    if broker_account_id is None or str(broker_account_id) != p["account_id"]:
        raise ModeDenied("PAPER refused: broker-reported account id differs from the approved account")
    return p
