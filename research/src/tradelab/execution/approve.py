"""USER-RUN helper that writes a signed PAPER approval record. Agents must not run this and do not hold the key.

    export CC_APPROVAL_KEY='<long random secret that never enters the repo>'
    python -m tradelab.execution.approve --account DU0000000 --instruments 123456 --strategy-id X --strategy-version v1 \\
        --capital 5000 --risk-policy configs/risk_policy.yaml --valid-hours 24 --out configs/approvals/paper_approval.json

The record binds account, instruments, strategy id+version, capital, the risk policy sha256 and an expiry.
Changing any of them (or the policy file) invalidates it. This does NOT approve LIVE: LIVE does not exist here.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

from tradelab.execution.common import sha256_hex, utcnow
from tradelab.execution.modes import KEY_ENV, MAX_VALID_HOURS, make_approval


def build_record(key: bytes, args) -> dict:
    now = utcnow()
    return make_approval(key, account_id=args.account, instruments=args.instruments, strategy_id=args.strategy_id,
                         strategy_version=args.strategy_version, capital_allocation=args.capital,
                         risk_policy_sha256=sha256_hex(Path(args.risk_policy).read_bytes()),
                         issued_utc=now, expires_utc=now + timedelta(hours=args.valid_hours))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tradelab.execution.approve", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--account", required=True)
    ap.add_argument("--instruments", required=True, nargs="+", type=int, help="IB conIds")
    ap.add_argument("--strategy-id", required=True)
    ap.add_argument("--strategy-version", required=True)
    ap.add_argument("--capital", required=True, help="capital allocation, e.g. 5000")
    ap.add_argument("--risk-policy", required=True)
    ap.add_argument("--valid-hours", type=float, default=24.0)
    ap.add_argument("--out", default="configs/approvals/paper_approval.json")
    a = ap.parse_args(argv)
    if not a.account.startswith("DU"):
        print("Refused: only paper accounts (ids starting with DU) can be approved.", file=sys.stderr)
        return 2
    if not (0 < a.valid_hours <= MAX_VALID_HOURS):
        print(f"Refused: --valid-hours must be > 0 and at most {MAX_VALID_HOURS}.", file=sys.stderr)
        return 2
    key = os.environ.get(KEY_ENV)
    if not key:
        print(f"{KEY_ENV} not set; refusing.", file=sys.stderr)
        return 2
    rec = build_record(key.encode(), a)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(rec, indent=1, sort_keys=True))
    print(f"wrote {a.out}; expires {rec['payload']['expires_utc']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
