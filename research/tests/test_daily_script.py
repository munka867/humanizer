import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("run_daily", ROOT / "scripts" / "run_daily.py")
RD = importlib.util.module_from_spec(spec)
sys.modules["run_daily"] = RD
spec.loader.exec_module(RD)


def test_test_segment_refused_without_freeze_and_logged(tmp_path):
    cfg, costs_raw = RD.load_cfg()
    log = tmp_path / "log.jsonl"
    called = []
    with pytest.raises(RD.G.FinalTestRefused):
        RD.guard_test_segment("D3_V1_c2_both", cfg, costs_raw, lambda: called.append(1), log_path=log)
    assert not called
    assert RD.G.read_log(log)[0]["granted"] is False


def test_test_segment_runs_only_after_freeze(tmp_path):
    cfg, costs_raw = RD.load_cfg()
    log = tmp_path / "log.jsonl"
    RD.G.freeze_config(RD.sid(cfg, "D3_V1_c2_both"), RD.config_hash_for("D3_V1_c2_both", cfg, costs_raw), log_path=log, git={})
    r = RD.guard_test_segment("D3_V1_c2_both", cfg, costs_raw, lambda: "ok", log_path=log)
    assert r.result == "ok" and r.untouched
    # a different variant is not frozen
    with pytest.raises(RD.G.FinalTestRefused):
        RD.guard_test_segment("D3_V2_c2_long", cfg, costs_raw, lambda: "ok", log_path=log)
