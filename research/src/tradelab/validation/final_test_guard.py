"""Final-test access ledger (results/test_access_log.jsonl, append-only JSON lines).

Protocol:
  1. freeze_config(strategy_id, config_hash)  -> records a 'freeze' entry.
  2. evaluate_final_test(strategy_id, config_hash, evaluator)  -> refuses unless a
     matching freeze entry exists; logs the access BEFORE running the evaluator (so a
     crash still counts as a look); runs it; returns a FinalTestResult.
Every attempt (granted or refused) is written. The Nth granted access of the final
test (N>=2, counted across ALL strategies, since any look contaminates the data) is
flagged "NO LONGER UNTOUCHED". The ledger cannot stop someone from reading the data
directly; it is a discipline/audit device, not access control.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

DEFAULT_LOG = Path(__file__).resolve().parents[3] / "results" / "test_access_log.jsonl"
UNTOUCHED_BANNER = "NO LONGER UNTOUCHED"


class FinalTestRefused(RuntimeError):
    pass


def config_hash(config: Any) -> str:
    """sha256 of canonical JSON (sorted keys). Use the full strategy+cost+split config."""
    blob = json.dumps(config, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def git_state(cwd: Path | None = None) -> dict:
    def run(*a):
        return subprocess.run(["git", *a], cwd=cwd or Path(__file__).parent, capture_output=True,
                              text=True, timeout=10).stdout.strip()
    try:
        return {"git_hash": run("rev-parse", "HEAD") or "unknown", "git_dirty": bool(run("status", "--porcelain"))}
    except Exception:
        return {"git_hash": "unknown", "git_dirty": None}


def read_log(log_path=DEFAULT_LOG) -> list[dict]:
    p = Path(log_path)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def _append(log_path, rec: dict) -> None:
    p = Path(log_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps(rec, sort_keys=True, default=str) + "\n")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def freeze_config(strategy_id: str, cfg_hash: str, log_path=DEFAULT_LOG, git: dict | None = None,
                  note: str = "") -> dict:
    rec = {"event": "freeze", "ts": _now(), "strategy_id": strategy_id, "config_hash": cfg_hash,
           "note": note, **(git if git is not None else git_state())}
    _append(log_path, rec)
    return rec


def is_frozen(strategy_id: str, cfg_hash: str, log_path=DEFAULT_LOG) -> bool:
    return any(r.get("event") == "freeze" and r["strategy_id"] == strategy_id and r["config_hash"] == cfg_hash
               for r in read_log(log_path))


def granted_accesses(log_path=DEFAULT_LOG) -> list[dict]:
    return [r for r in read_log(log_path) if r.get("event") == "final_test_access" and r.get("granted")]


@dataclass
class FinalTestResult:
    result: Any
    access_number: int          # 1 = first look at the final test (any strategy)
    untouched: bool             # True only for the first access
    strategy_accesses: int      # how many times THIS strategy looked
    banner: str

    def __str__(self):
        return self.banner


def evaluate_final_test(strategy_id: str, cfg_hash: str, evaluator: Callable[[], Any],
                        log_path=DEFAULT_LOG, git: dict | None = None, purpose: str = "") -> FinalTestResult:
    git = git if git is not None else git_state()
    if not is_frozen(strategy_id, cfg_hash, log_path):
        _append(log_path, {"event": "final_test_access", "granted": False, "ts": _now(), "strategy_id": strategy_id,
                           "config_hash": cfg_hash, "reason": "config not frozen", "purpose": purpose, **git})
        raise FinalTestRefused(f"Refused: no freeze record for strategy={strategy_id} config_hash={cfg_hash[:12]}. "
                               "Call freeze_config() first; the config must not change afterwards.")
    prior = granted_accesses(log_path)
    n_total = len(prior) + 1
    n_strat = sum(1 for r in prior if r["strategy_id"] == strategy_id) + 1
    _append(log_path, {"event": "final_test_access", "granted": True, "ts": _now(), "strategy_id": strategy_id,
                       "config_hash": cfg_hash, "access_number": n_total, "strategy_access_number": n_strat,
                       "purpose": purpose, **git})
    if n_total == 1:
        banner = "FINAL TEST: first and only permitted access (untouched until now)."
    else:
        banner = (f"*** FINAL TEST {UNTOUCHED_BANNER} *** access #{n_total} overall "
                  f"({n_strat} for strategy '{strategy_id}'). Results from this look are NOT clean out-of-sample "
                  "evidence; the test set has been used for decisions and must be reported as contaminated.")
    result = evaluator()
    return FinalTestResult(result, n_total, n_total == 1, n_strat, banner)
