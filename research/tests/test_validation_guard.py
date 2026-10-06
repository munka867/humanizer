import json
import pytest
from tradelab.validation.final_test_guard import (evaluate_final_test, freeze_config, FinalTestRefused, read_log,
                                                  config_hash, UNTOUCHED_BANNER, is_frozen)

GIT = {"git_hash": "abc123", "git_dirty": False}


def test_refuses_without_freeze_and_logs_attempt(tmp_path):
    log = tmp_path / "log.jsonl"
    called = []
    with pytest.raises(FinalTestRefused):
        evaluate_final_test("s1", "h1", lambda: called.append(1), log, git=GIT)
    assert not called
    recs = read_log(log)
    assert len(recs) == 1 and recs[0]["granted"] is False


def test_changed_config_not_frozen(tmp_path):
    log = tmp_path / "log.jsonl"
    freeze_config("s1", "h1", log, git=GIT)
    with pytest.raises(FinalTestRefused):
        evaluate_final_test("s1", "h2", lambda: 1, log, git=GIT)


def test_first_access_ok_second_flagged(tmp_path):
    log = tmp_path / "sub" / "log.jsonl"
    freeze_config("s1", "h1", log, git=GIT)
    r1 = evaluate_final_test("s1", "h1", lambda: 42, log, git=GIT)
    assert r1.result == 42 and r1.untouched and UNTOUCHED_BANNER not in r1.banner
    r2 = evaluate_final_test("s1", "h1", lambda: 43, log, git=GIT)
    assert not r2.untouched and r2.access_number == 2 and UNTOUCHED_BANNER in r2.banner
    recs = [r for r in read_log(log) if r["event"] == "final_test_access"]
    assert [r["access_number"] for r in recs] == [1, 2]
    assert all(r["git_hash"] == "abc123" and r["config_hash"] == "h1" and "ts" in r for r in recs)


def test_other_strategy_second_look_also_contaminates(tmp_path):
    log = tmp_path / "log.jsonl"
    freeze_config("a", "h", log, git=GIT); freeze_config("b", "h", log, git=GIT)
    evaluate_final_test("a", "h", lambda: 1, log, git=GIT)
    r = evaluate_final_test("b", "h", lambda: 1, log, git=GIT)
    assert UNTOUCHED_BANNER in r.banner and r.strategy_accesses == 1


def test_access_logged_even_if_evaluator_crashes(tmp_path):
    log = tmp_path / "log.jsonl"
    freeze_config("s", "h", log, git=GIT)
    with pytest.raises(ZeroDivisionError):
        evaluate_final_test("s", "h", lambda: 1 / 0, log, git=GIT)
    assert any(r.get("granted") for r in read_log(log))
    r = evaluate_final_test("s", "h", lambda: 1, log, git=GIT)
    assert UNTOUCHED_BANNER in r.banner


def test_config_hash_stable_and_sensitive():
    assert config_hash({"a": 1, "b": 2}) == config_hash({"b": 2, "a": 1})
    assert config_hash({"a": 1}) != config_hash({"a": 2})
    assert is_frozen("x", "y", "/nonexistent/none.jsonl") is False


def test_refuses_synthetic_data_without_consuming_access(tmp_path):
    log = tmp_path / "log.jsonl"
    freeze_config("s", "h", log, git=GIT)
    with pytest.raises(FinalTestRefused):
        evaluate_final_test("s", "h", lambda: 1, log, git=GIT, data_is_synthetic=True)
    r = evaluate_final_test("s", "h", lambda: 1, log, git=GIT)
    assert r.untouched and r.access_number == 1
