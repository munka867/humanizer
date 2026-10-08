"""Reproduce the committed daily runs (train, validation; pre-registered settings, all variants) into
results/experiments/committed_repro/<segment>/ with series + trade log, and VERIFY the regenerated summaries equal
results/daily/<segment>_summary.json. Never overwrites committed files. Usage: python -m command_center.repro_committed"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _eq(a, b, path="", diffs=None, tol=0.0):
    diffs = [] if diffs is None else diffs
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                diffs.append(f"{path}/{k}: key only in {'reproduced' if k in a else 'committed'}")
            else:
                _eq(a[k], b[k], f"{path}/{k}", diffs, tol)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            diffs.append(f"{path}: length {len(a)} != {len(b)}")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                _eq(x, y, f"{path}[{i}]", diffs, tol)
    elif isinstance(a, float) and isinstance(b, float):
        if not ((math.isnan(a) and math.isnan(b)) or a == b or abs(a - b) <= tol * max(1.0, abs(b))):
            diffs.append(f"{path}: {a!r} != {b!r}")
    elif a != b:
        diffs.append(f"{path}: {a!r} != {b!r}")
    return diffs


def reproduce(root: Path = ROOT, committed_dir: Path | None = None, out_base: Path | None = None) -> dict:
    committed_dir = committed_dir or root / "results" / "daily"
    out_base = out_base or root / "results" / "experiments" / "committed_repro"
    report = {"segments": {}, "all_equal": True}
    for seg in ("train", "validation"):
        out = out_base / seg
        out.mkdir(parents=True, exist_ok=True)
        rc = subprocess.run([sys.executable, str(root / "scripts" / "run_daily.py"), "--segment", seg, "--out-dir", str(out)],
                            cwd=str(root), capture_output=True, text=True, stdin=subprocess.DEVNULL)
        entry = {"exit_code": rc.returncode}
        if rc.returncode != 0:
            entry.update(equal=False, diffs=["run failed: " + rc.stderr[-300:]])
        else:
            a = json.loads((out / f"{seg}_summary.json").read_text())
            b = json.loads((committed_dir / f"{seg}_summary.json").read_text())
            diffs = _eq(a, b)
            entry.update(equal=not diffs, diffs=diffs[:50], n_diffs=len(diffs))
        report["segments"][seg] = entry
        report["all_equal"] &= entry["equal"]
    (out_base / "REPRO_CHECK.json").write_text(json.dumps(report, indent=1))
    return report


if __name__ == "__main__":
    r = reproduce()
    for seg, e in r["segments"].items():
        print(seg, "EQUAL" if e["equal"] else "*** MISMATCH ***", e.get("diffs", [])[:5])
    sys.exit(0 if r["all_equal"] else 1)
