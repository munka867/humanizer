"""Research API: strategy registry, experiment builder (REAL subprocess runs on SYNTHETIC daily bars), results, data coverage, import.

What is real vs stubbed (also stated in the final report):
* REAL: end-to-end experiment runs execute scripts/run_daily.py in a subprocess against SYNTHETIC daily bars written to a tmp dir
  (software test only; no result here says anything about markets); a failed job is produced by a real process failure.
* REAL: registry facts are compared with docs/DAILY_RESULTS.md, docs/EXPERIMENT_LOG.md and the committed results/daily JSON.
* FIXTURE: all import files are SYNTHETIC; no real IBKR export exists, so the parser is fixture-tested only.
"""
import ast
import http.client
import json
import math
import re
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from command_center import api_data, api_research  # noqa: E402
from command_center.server import make_server  # noqa: E402

TOKEN = "test-token"
FIX = ROOT / "tests" / "fixtures"
REFUSAL = ("requires explicit user approval recorded by the user plus an approved risk policy and a broker adapter; "
           "not available")


def synth_daily(path: Path, symbol: str, seed: int, n: int = 520) -> None:
    """SYNTHETIC random-walk daily bars (valid OHLC). Software test data only."""
    import random
    import datetime as dt
    rnd = random.Random(seed)
    px, d, rows = 100.0, dt.date(2022, 1, 3), []
    while len(rows) < n:
        if d.weekday() < 5:
            o = px * (1 + rnd.gauss(0, 0.004))
            c = o * (1 + rnd.gauss(0, 0.01))
            hi = max(o, c) * (1 + abs(rnd.gauss(0, 0.004)))
            lo = min(o, c) * (1 - abs(rnd.gauss(0, 0.004)))
            rows.append(f"{d.isoformat()} 14:30:00+00:00,{o:.2f},{hi:.2f},{lo:.2f},{c:.2f},{1000000 + rnd.randint(0, 99999)}.0,SYNTHETIC")
            px = c
        d += dt.timedelta(days=1)
    path.write_text("ts_open,open,high,low,close,volume,contract\n" + "\n".join(rows) + "\n")


@pytest.fixture()
def env(tmp_path):
    data = tmp_path / "SYNTHETIC_data"
    data.mkdir()
    synth_daily(data / "SPY_1d.csv", "SPY", 1)
    synth_daily(data / "QQQ_1d.csv", "QQQ", 2)
    paths = {"data_dir": data, "experiments_dir": tmp_path / "experiments", "imports_dir": tmp_path / "ibkr_exports",
             "test_log": tmp_path / "test_access_log.jsonl", "manifest": tmp_path / "no_manifest.json"}
    s = make_server(tmp_path / "events.sqlite3", port=0, token=TOKEN)
    s.research_paths = paths
    t = threading.Thread(target=s.serve_forever, daemon=True)
    t.start()
    yield s, paths, tmp_path
    s.stopping = True
    c = s.__dict__.get("_research_ctx")
    if c is not None and c.queue is not None:
        c.queue.shutdown()
    s.shutdown()
    s.server_close()


def call(srv, method, path, body=None, token=TOKEN):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=20)
    h = {"Content-Type": "application/json"}
    if token:
        h["X-CC-Token"] = token
    c.request(method, path, json.dumps(body) if body is not None else None, h)
    r = c.getresponse()
    raw = r.read()
    try:
        return r.status, json.loads(raw)
    except ValueError:
        return r.status, raw


def wait_done(srv, jid, timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st, j = call(srv, "GET", f"/api/experiments/{jid}")
        assert st == 200
        if j["status"] in ("completed", "failed"):
            return j
        time.sleep(0.2)
    raise AssertionError("job did not finish")


def walk_keys(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k
            yield from walk_keys(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk_keys(v)


# ------------------------------------------------------------------ registry
def test_registry_matches_docs_facts(env):
    srv, _, _ = env
    st, d = call(srv, "GET", "/api/strategies")
    assert st == 200
    by = {s["id"]: s for s in d["strategies"]}
    assert set(by) == {"H1_sweep_reversal_v1", "H2_ttrades_mechanical", "D3_V1_c2_both", "D3_V2_c2_long", "D3_V3_c2_short"}
    assert by["H1_sweep_reversal_v1"]["state"] == "candidate" and "NOT RUN: no data" in by["H1_sweep_reversal_v1"]["status_note"]
    assert by["H2_ttrades_mechanical"]["state"] == "candidate" and "not implemented" in by["H2_ttrades_mechanical"]["status_note"]
    assert by["D3_V1_c2_both"]["state"] == "rejected" and by["D3_V2_c2_long"]["state"] == "rejected"
    assert by["D3_V3_c2_short"]["verdict"] == "INCONCLUSIVE" and by["D3_V3_c2_short"]["state"] == "testing"
    for s in d["strategies"]:
        assert s["approval_history"] == []
        assert s["rules"]["doc"].startswith("docs/") and (ROOT / s["rules"]["doc"]).is_file()
        for v in s["validation"] + s["source_research"]:
            assert (ROOT / v["doc"]).is_file()
    log = (ROOT / "docs" / "EXPERIMENT_LOG.md").read_text()
    results = (ROOT / "docs" / "DAILY_RESULTS.md").read_text()
    reg = json.loads((ROOT / "configs" / "strategies.json").read_text())["strategies"]
    for s in reg:
        for e in s["experiments"]:
            assert e in log, e
        if s["family"] == "H3":
            f = s["facts"]
            assert f"**{s['id']}: {s['verdict']}**" in results
            for seg in ("train", "validation"):
                assert f"n={f[seg]['n']}" in log.replace(" ", "") or f"n={f[seg]['n']}" in log, (s["id"], seg)
            # committed pipeline output agrees with the registry
            summ = json.loads((ROOT / "results" / "daily" / "validation_summary.json").read_text())
            assert summ["verdict"][s["id"]]["verdict"] == s["verdict"]
            assert summ["variants"][s["id"]]["pooled"]["n"] == f["validation"]["n"]
            assert round(summ["variants"][s["id"]]["pooled"]["ev_usd"], 2) == f["validation"]["ev_usd"]
            man = {e["symbol"]: e["sha256"] for e in json.loads((ROOT / "data/raw/ibkr_connector/MANIFEST.json").read_text())}
            assert s["dataset"]["manifest_sha256"] == man
    st, one = call(srv, "GET", "/api/strategies/D3_V1_c2_both")
    assert st == 200 and one["state"] == "rejected" and "dataset_matches_current_manifest" in one
    assert call(srv, "GET", "/api/strategies/NOPE")[0] == 404


def test_cost_scenarios_match_yaml():
    import yaml
    sc = yaml.safe_load((ROOT / "configs" / "daily_costs.yaml").read_text())["scenarios"]
    assert sc == api_research.COST_SCENARIOS


def test_state_transitions_and_approval_refused(env):
    srv, _, _ = env
    st, r = call(srv, "POST", "/api/strategies/D3_V3_c2_short/state", {"state": "candidate", "reason": "re-open for review"})
    assert st == 200 and r["state"] == "candidate" and r["state_history"][-1]["reason"] == "re-open for review"
    assert call(srv, "POST", "/api/strategies/D3_V3_c2_short/state", {"state": "rejected"})[0] == 400          # reason required
    assert call(srv, "POST", "/api/strategies/D3_V3_c2_short/state", {"state": "bogus", "reason": "xxx"})[0] == 400
    for target in ("approved_paper", "approved_live"):
        st, r = call(srv, "POST", "/api/strategies/D3_V3_c2_short/state", {"state": target, "reason": "please"})
        assert st == 403 and r["error"] == REFUSAL
    st, one = call(srv, "GET", "/api/strategies/D3_V3_c2_short")
    assert one["state"] == "candidate" and one["refused_approval_attempts"] == 2 and one["approval_history"] == []
    st, a = call(srv, "GET", "/api/research/audit")
    outcomes = [e["outcome"] for e in a["entries"]]
    assert outcomes.count("refused_approval") == 2 and "changed" in outcomes
    # the refusals also reached the shared event store as incidents
    evs = srv.store.events(0, 100)
    assert any(e["event_type"] == "incident" and "REFUSED approval" in e["payload"]["summary"] for e in evs)


# ------------------------------------------------------------------ experiments
def test_test_segment_refused_and_logged(env):
    srv, paths, _ = env
    st, r = call(srv, "POST", "/api/experiments", {"strategy_id": "D3_V1_c2_both", "segment": "test", "symbols": ["SPY"]})
    assert st == 403 and r["code"] == "test_segment_reserved"
    st, r = call(srv, "POST", "/api/experiments", {"strategy_id": "does-not-exist", "segment": "test"})
    assert st == 403                                      # refused even for an unknown strategy
    lines = [json.loads(x) for x in paths["test_log"].read_text().splitlines()]
    assert len(lines) == 2 and all(x["granted"] is False and x["event"] == "final_test_access" for x in lines)
    a = call(srv, "GET", "/api/research/audit")[1]["entries"]
    assert [e["outcome"] for e in a].count("refused_test_segment") == 2
    assert call(srv, "GET", "/api/experiments")[1]["experiments"] == []   # nothing queued


def test_unregistered_and_unrunnable_strategies_refused(env):
    srv, _, _ = env
    st, r = call(srv, "POST", "/api/experiments", {"strategy_id": "MadeUp", "segment": "train"})
    assert st == 400 and r["code"] == "strategy_not_registered"
    st, r = call(srv, "POST", "/api/experiments", {"strategy_id": "H1_sweep_reversal_v1", "segment": "train"})
    assert st == 422 and r["code"] == "strategy_not_runnable" and "NOT RUN" in r["reason"]
    assert call(srv, "POST", "/api/experiments", {"strategy_id": "D3_V1_c2_both", "segment": "holdout"})[0] == 400
    assert call(srv, "POST", "/api/experiments", {"strategy_id": "D3_V1_c2_both", "segment": "train", "cost_scenario": "x9"})[0] == 400
    assert call(srv, "POST", "/api/experiments", {"strategy_id": "D3_V1_c2_both", "segment": "train", "seed": True})[0] == 400
    assert call(srv, "POST", "/api/experiments", {"strategy_id": "D3_V1_c2_both", "segment": "train", "bogus": 1})[0] == 400
    assert call(srv, "POST", "/api/experiments", {"strategy_id": "D3_V1_c2_both", "segment": "train", "symbols": ["../x"]})[0] == 400


def test_unregistered_in_experiment_log_refused(env, tmp_path):
    srv, paths, tp = env
    docs = tp / "docs"
    docs.mkdir()
    (docs / "EXPERIMENT_LOG.md").write_text("| D3-V1 | x | y | none | z | NOT REGISTERED |\n")
    srv.research_paths["docs_dir"] = docs
    srv.__dict__.pop("_research_ctx", None)
    st, r = call(srv, "POST", "/api/experiments", {"strategy_id": "D3_V1_c2_both", "segment": "train", "symbols": ["SPY"]})
    assert st == 422 and r["code"] == "not_preregistered"


def test_inadequate_inputs_structured(env):
    srv, paths, _ = env
    st, r = call(srv, "POST", "/api/experiments", {"strategy_id": "D3_V1_c2_both", "segment": "train", "symbols": ["IWM"]})
    assert st == 422 and r["error"] == "inputs_inadequate"
    assert r["inputs_inadequate"][0]["symbol"] == "IWM" and r["inputs_inadequate"][0]["code"] == "no_data"
    st, r = call(srv, "POST", "/api/experiments", {"strategy_id": "D3_V1_c2_both", "segment": "train", "symbols": ["AAPL"]})
    assert st == 422 and r["inputs_inadequate"][0]["code"] == "not_in_strategy_universe"
    # too short for the validation segment
    synth_daily(paths["data_dir"] / "SPY_1d.csv", "SPY", 1, n=120)
    st, r = call(srv, "POST", "/api/experiments", {"strategy_id": "D3_V1_c2_both", "segment": "validation", "symbols": ["SPY"]})
    assert st == 422 and any(p["code"] == "segment_too_short" for p in r["inputs_inadequate"])
    # corrupt data (non-positive price) is rejected
    synth_daily(paths["data_dir"] / "QQQ_1d.csv", "QQQ", 2)
    f = paths["data_dir"] / "QQQ_1d.csv"
    lines = f.read_text().splitlines()
    parts = lines[5].split(",")
    parts[1] = "-1.0"
    lines[5] = ",".join(parts)
    f.write_text("\n".join(lines) + "\n")
    st, r = call(srv, "POST", "/api/experiments", {"strategy_id": "D3_V1_c2_both", "segment": "train", "symbols": ["QQQ"]})
    assert st == 422 and any(p["code"] == "nonpositive_price" for p in r["inputs_inadequate"])
    assert call(srv, "GET", "/api/experiments")[1]["experiments"] == []


def test_real_experiment_end_to_end_on_synthetic_data(env):
    srv, paths, _ = env
    body = {"strategy_id": "D3_V3_c2_short", "symbols": ["SPY", "QQQ"], "segment": "validation", "cost_scenario": "x1_5",
            "seed": 123, "notes": "SYNTHETIC software test"}
    st, r = call(srv, "POST", "/api/experiments", body)
    assert st == 202, r
    jid = r["experiment"]["id"]
    assert r["experiment"]["status"] in ("queued", "running")
    j = wait_done(srv, jid)
    assert j["status"] == "completed" and j["exit_code"] == 0 and j["started_at"] and j["finished_at"]
    # progress is what the process really reported: every planned step finished, no invented percentages
    pr = j["progress"]
    assert pr["steps_total"] == pr["steps_done"] == len(pr["steps_finished"]) and pr["steps_total"] >= 4
    assert pr["steps_finished"][0].startswith("load bars") and pr["steps_finished"][-1].startswith("write summary")
    assert "percent" not in json.dumps(pr)
    assert "##PROGRESS" not in j["stderr_tail"] and j["stdout_tail"]
    assert j["params"]["deviates_from_preregistered_defaults"] is True and "EXPERIMENT_LOG" in j["params"]["deviation_note"]
    # result
    st, res = call(srv, "GET", f"/api/experiments/{jid}/result")
    assert st == 200 and res["schema"] == "cc.result.v1"
    ev = res["evidence"]
    assert ev["training"] is None and ev["validation"]["segment"] == "validation"
    assert ev["out_of_sample_test"]["available"] is False and ev["forward_paper"]["available"] is False
    assert res["run"]["synthetic_data"] is True and "SYNTHETIC data (software test only)" in res["labels"]
    assert set(res["run"]["symbols"]) == {"SPY", "QQQ"} and res["run"]["seed"] == 123
    cs = ev["validation"]["cost_stress"]
    assert [r_["cost_mult"] for r_ in cs["rows"]] == [1.0, 1.5, 2.0] and cs["selected_scenario"]["multiplier"] == 1.5
    assert "B_D0" in ev["validation"]["baselines"]
    mc = ev["validation"]["monte_carlo"]
    if mc:
        assert mc["method"] == "day_block" and mc["seed"] == 123 and mc["paths"] == 5000
        assert "CONDITIONAL SIMULATION" in mc["label"] and "no independent market evidence" in mc["label"]
        assert {"p05", "p50", "p95"} <= set(mc["percentiles"]["total_pnl_usd"]) and "thresholds" in mc and "horizon" in mc
    v = res["verdict"]
    assert v["value"] in ("REJECTED", "INCONCLUSIVE", "SUITABLE FOR FURTHER PAPER TESTING")
    assert isinstance(v["reasons"], list) and v["failed_gates"] == [x for x in v["reasons"] if x.startswith("not met")]
    keys = {k.lower() for k in walk_keys(res)}
    assert not {k for k in keys if "profitable" in k or "live_ready" in k or "ready_for_live" in k}
    assert "metric_definitions" in res and "ci_usd" in res["metric_definitions"]
    # strict JSON (no NaN tokens)
    raw = http.client.HTTPConnection("127.0.0.1", srv.server_address[1])
    raw.request("GET", f"/api/experiments/{jid}/result")
    txt = raw.getresponse().read().decode()
    assert "NaN" not in txt and "Infinity" not in txt
    # listing + compare against a committed run (separate keys, not merged)
    lst = call(srv, "GET", "/api/experiments")[1]["experiments"]
    assert lst[0]["id"] == jid and "params" not in lst[0]
    st, cmp_ = call(srv, "GET", f"/api/experiments/compare?ids={jid},committed:validation:D3_V3_c2_short")
    assert st == 200 and len(cmp_["table"]) == 2
    assert all(row["training"] is None for row in cmp_["table"]) and cmp_["warnings"]
    assert all(row["validation"] and "n" in row["validation"] for row in cmp_["table"])
    assert call(srv, "GET", f"/api/experiments/compare?ids={jid}")[0] == 400
    # output directory is per job and contains only aggregates
    out = paths["experiments_dir"] / jid
    assert (out / "validation_summary.json").is_file() and not (ROOT / "results" / "experiments" / jid).exists()


def test_train_experiment_has_no_verdict(env):
    srv, _, _ = env
    st, r = call(srv, "POST", "/api/experiments", {"strategy_id": "D3_V2_c2_long", "symbols": ["SPY"], "segment": "train"})
    assert st == 202
    j = wait_done(srv, r["experiment"]["id"])
    assert j["status"] == "completed"
    res = call(srv, "GET", f"/api/experiments/{j['id']}/result")[1]
    assert res["evidence"]["validation"] is None and res["evidence"]["training"]["segment"] == "train"
    assert res["verdict"] is None


def test_failed_job_is_reported_honestly(env):
    """REAL process failure: the data file disappears after pre-validation, so run_daily exits non-zero."""
    srv, paths, _ = env
    gate = threading.Event()
    real = api_research.subprocess_runner

    def runner(argv, cwd, on_progress, on_output, timeout_s):
        gate.wait(10)
        return real(argv, cwd, on_progress, on_output, timeout_s)
    srv.research_paths["runner"] = runner
    st, r = call(srv, "POST", "/api/experiments", {"strategy_id": "D3_V1_c2_both", "symbols": ["SPY"], "segment": "train"})
    assert st == 202
    jid = r["experiment"]["id"]
    (paths["data_dir"] / "SPY_1d.csv").unlink()
    gate.set()
    j = wait_done(srv, jid)
    assert j["status"] == "failed" and j["exit_code"] not in (0, None) and j["finished_at"]
    assert "No such file" in j["stderr_tail"] or "FileNotFound" in j["stderr_tail"]
    assert not re.search(r"(?<![\w.])/(home|usr|tmp|root)/", j["stderr_tail"])      # no absolute paths outside the repo
    st, res = call(srv, "GET", f"/api/experiments/{jid}/result")
    assert st == 409 and res["job_status"] == "failed" and res["exit_code"] == j["exit_code"]


def test_queue_runs_one_job_at_a_time(env):
    srv, _, _ = env
    active, peak, lock = [0], [0], threading.Lock()

    def stub(argv, cwd, on_progress, on_output, timeout_s):      # STUB runner: concurrency check only, no pipeline
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.15)
        on_progress({"step": "stub step", "done": 1, "total": 1})
        with lock:
            active[0] -= 1
        return 0
    srv.research_paths["runner"] = stub
    ids = []
    for _ in range(3):
        st, r = call(srv, "POST", "/api/experiments", {"strategy_id": "D3_V1_c2_both", "symbols": ["SPY"], "segment": "train"})
        assert st == 202
        ids.append(r["experiment"]["id"])
    for i in ids:
        assert wait_done(srv, i)["status"] == "completed"
    assert peak[0] == 1


def test_redaction_of_paths():
    from command_center.jobs import redact
    s = redact(f"File \"{ROOT}/scripts/run_daily.py\", line 3 in /usr/lib/python3/site-packages/x.py date 2022/10/12", ROOT)
    assert "scripts/run_daily.py" in s and "/usr/lib" not in s and "<path>" in s and "2022/10/12" in s


# ------------------------------------------------------------------ committed runs
def test_committed_runs_normalised(env):
    srv, _, _ = env
    st, lst = call(srv, "GET", "/api/runs/committed")
    ids = {r["id"] for r in lst["runs"]}
    assert "committed:validation:D3_V1_c2_both" in ids and "committed:train:D3_V3_c2_short" in ids and len(ids) == 6
    st, r = call(srv, "GET", "/api/runs/committed/committed:validation:D3_V1_c2_both")
    assert st == 200 and r["schema"] == "cc.result.v1" and r["source"]["kind"] == "committed_run"
    assert r["verdict"]["value"] == "REJECTED" and r["verdict"]["reasons"] == ["train net EV/trade <= 0 at 1.0x costs"]
    assert r["evidence"]["training"] is None and r["evidence"]["validation"]["metrics"]["pooled"]["n"] == 152
    assert r["evidence"]["validation"]["monte_carlo"]["method"] == "day_block"
    v3 = call(srv, "GET", "/api/runs/committed/committed:validation:D3_V3_c2_short")[1]
    assert v3["verdict"]["value"] == "INCONCLUSIVE" and "not met: n_val >= 100" in v3["verdict"]["failed_gates"]
    tr = call(srv, "GET", "/api/runs/committed/committed:train:D3_V1_c2_both")[1]
    assert tr["evidence"]["validation"] is None and tr["verdict"] is None
    assert call(srv, "GET", "/api/runs/committed/committed:test:D3_V1_c2_both")[0] == 404
    cmp_ = call(srv, "GET", "/api/experiments/compare?ids=committed:train:D3_V1_c2_both,committed:validation:D3_V1_c2_both")[1]
    assert cmp_["table"][0]["training"]["n"] == 471 and cmp_["table"][1]["validation"]["n"] == 152
    keys = {k.lower() for k in walk_keys(r)}
    assert not {k for k in keys if "profitable" in k or "ready" in k}


# ------------------------------------------------------------------ data coverage
def test_real_data_coverage(tmp_path):
    s = make_server(tmp_path / "e.db", port=0, token=TOKEN)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        if not (ROOT / "data" / "processed" / "SPY_1d.csv").is_file():
            pytest.skip("local market data not present")
        st, d = call(s, "GET", "/api/data/coverage?symbols=SPY,QQQ&start=2022-10-13&end=2026-10-07")
        assert st == 200
        spy = next(x for x in d["symbols"] if x["symbol"] == "SPY")
        assert spy["n_bars"] == 1000 and spy["first"] == "2022-10-12" and spy["last"] == "2026-10-07"
        assert spy["resolution"] == "1D" and spy["adjustment"] == "unadjusted"
        assert {f["code"]: f["count"] for f in spy["quality_failures"]} == {"open_or_close_outside_high_low": 15}   # docs/DATA_FINDINGS.md
        assert spy["blocking_failures"] == [] and spy["manifest_mismatch"] == []
        assert d["adequacy"]["adequate"] is True
        st, d = call(s, "GET", "/api/data/coverage?symbols=SPY&start=2020-01-01&end=2026-10-07")
        assert d["adequacy"]["adequate"] is False and d["adequacy"]["reasons"][0]["code"] == "range_not_covered"
        st, d = call(s, "GET", "/api/data/coverage?symbols=SPY&start=2023-01-01&end=2024-01-01&resolution=5min")
        assert any(r["code"] == "resolution_unavailable" for r in d["adequacy"]["reasons"])
        assert call(s, "GET", "/api/data/coverage?symbols=SPY")[0] == 400
        assert call(s, "GET", "/api/data/coverage?symbols=sp%20y&start=2023-01-01&end=2024-01-01")[0] == 400
    finally:
        s.shutdown()
        s.server_close()


def test_coverage_detects_gaps_and_missing(env):
    srv, paths, _ = env
    f = paths["data_dir"] / "SPY_1d.csv"
    lines = f.read_text().splitlines()
    del lines[100:110]                         # remove 10 consecutive sessions
    f.write_text("\n".join(lines) + "\n")
    d = call(srv, "GET", "/api/data/coverage")[1]
    spy = next(x for x in d["symbols"] if x["symbol"] == "SPY")
    assert spy["missing_sessions"] >= 9 and spy["gaps"] and spy["synthetic"] is True
    assert spy["adjustment"].startswith("unknown")        # no manifest entry for synthetic data: not claimed
    adq = call(srv, "GET", "/api/data/coverage?symbols=SPY&start=2022-03-01&end=2022-08-01")[1]["adequacy"]
    assert adq["adequate"] is False and any(r["code"] == "too_many_missing_sessions" for r in adq["reasons"])


# ------------------------------------------------------------------ import
def read_fx(name):
    return (FIX / name).read_text(encoding="utf-8")


def test_import_preview_valid_csv(env):
    srv, _, _ = env
    st, p = call(srv, "POST", "/api/import/preview", {"filename": "SYNTHETIC_flex_trades.csv", "content": read_fx("SYNTHETIC_flex_trades.csv")})
    assert st == 200 and p["format"] == "ibkr_flex_csv" and p["container"] == "csv"
    assert p["row_counts"] == {"total": 3, "valid": 3, "quarantined": 0} and p["missing_required_fields"] == []
    assert len(p["preview_rows"]) == 3 and p["header_fields"][0] == "ClientAccountID"
    assert p["preview_rows"][0]["values"]["ClientAccountID"] != "SYNTH0001"           # account ids masked
    m = {x["normalized_field"]: x for x in p["field_mapping"]}
    assert m["price"]["source_field"] == "TradePrice" and m["symbol"]["status"] == "mapped"
    assert p["reconciliation"] == "not performed (no normalization implemented)"
    assert "UNVERIFIED" in p["support"] and "SYNTHETIC" in p["support"]
    assert p["duplicate_of"] is None and p["would_commit"] is True


def test_import_preview_invalid_rows_quarantined(env):
    srv, _, _ = env
    st, p = call(srv, "POST", "/api/import/preview", {"filename": "SYNTHETIC_flex_invalid.csv", "content": read_fx("SYNTHETIC_flex_invalid.csv")})
    assert st == 200 and p["format"] == "ibkr_flex_csv"
    assert p["row_counts"] == {"total": 5, "valid": 1, "quarantined": 4}
    q = {x["line"]: x["reason"] for x in p["quarantined"]}
    assert sorted(q) == [3, 4, 5, 6]
    assert q[3] == "unparseable date/time in DateTime" and q[4] == "column count 9 != header 15"
    assert q[5] == "missing value for required field Symbol" and q[6] == "non-numeric value in Quantity"


def test_import_preview_xml_confirms_and_unknown(env):
    srv, _, _ = env
    st, p = call(srv, "POST", "/api/import/preview", {"filename": "SYNTHETIC_flex_trades.xml", "content": read_fx("SYNTHETIC_flex_trades.xml")})
    assert st == 200 and p["format"] == "ibkr_flex_xml" and p["container"] == "xml"
    assert p["row_counts"]["total"] == 4 and p["row_counts"]["quarantined"] == 2
    assert sorted(x["line"] for x in p["quarantined"]) == [9, 10] and p["preview_rows"][0]["line"] == 7
    st, p = call(srv, "POST", "/api/import/preview", {"filename": "SYNTHETIC_trade_confirms.csv", "content": read_fx("SYNTHETIC_trade_confirms.csv")})
    assert p["format"] == "trade_confirmation_like" and p["row_counts"]["valid"] == 2
    st, p = call(srv, "POST", "/api/import/preview", {"filename": "x.csv", "content": "a,b\n1,2\n"})
    assert p["format"] == "unknown" and p["would_commit"] is False
    st, p = call(srv, "POST", "/api/import/preview", {"filename": "bad.xml", "content": "<FlexQueryResponse><Trades></FlexQueryResponse>"})
    assert p["parse_error"] and "line" in p["parse_error"]
    st, p = call(srv, "POST", "/api/import/preview", {"filename": "evil.xml", "content": '<!DOCTYPE x [<!ENTITY a "b">]><FlexQueryResponse/>'})
    assert "DTD" in p["parse_error"]
    assert call(srv, "POST", "/api/import/preview", {"filename": "a.csv", "content": ""})[0] == 400
    # 5 MB cap is enforced here; NOTE server.py's MAX_BODY (1,000,000) rejects larger bodies first (limitation reported)
    with pytest.raises(api_data.ApiError) as ei:
        api_data.check_upload_body({"filename": "a.csv", "content": "x" * (5 * 1024 * 1024 + 1)})
    assert ei.value.status == 413
    assert call(srv, "POST", "/api/import/preview", {"content": "x"})[0] == 400


def test_import_commit_registry_and_duplicate(env):
    srv, paths, _ = env
    body = {"filename": "../../etc/SYNTHETIC_flex_trades.csv", "content": read_fx("SYNTHETIC_flex_trades.csv")}
    st, r = call(srv, "POST", "/api/import/commit", body)
    assert st == 201 and r["created"] is True and r["duplicate"] is False
    e = r["entry"]
    assert e["row_counts"]["total"] == 3 and e["size_bytes"] == len(body["content"].encode()) and e["imported_at"] and e["filename"].startswith("SYNTHETIC")
    files = list(paths["imports_dir"].iterdir())
    assert len(files) == 1 and files[0].read_bytes() == body["content"].encode() and files[0].parent == paths["imports_dir"]
    assert oct(files[0].stat().st_mode & 0o777) == "0o600"
    st, r2 = call(srv, "POST", "/api/import/commit", body)
    assert st == 200 and r2["duplicate"] is True and r2["created"] is False and r2["existing"]["id"] == e["id"]
    assert len(list(paths["imports_dir"].iterdir())) == 1
    st, reg = call(srv, "GET", "/api/import/registry")
    assert len(reg["entries"]) == 1 and reg["entries"][0]["sha256"] == e["sha256"]
    st, p = call(srv, "POST", "/api/import/preview", body)
    assert p["duplicate_of"]["id"] == e["id"] and p["would_commit"] is False
    st, r3 = call(srv, "POST", "/api/import/commit", {"filename": "u.csv", "content": "a,b\n1,2\n"})
    assert st == 422 and len(list(paths["imports_dir"].iterdir())) == 1
    assert "SYNTH0001" not in json.dumps(reg)


def test_account_exports_separate_from_market_data():
    assert api_data.Ctx.__init__ and "ibkr_exports" in (ROOT / ".gitignore").read_text()
    src = (ROOT / "command_center" / "api_data.py").read_text()
    assert "data/raw/ibkr_exports" in src or "ibkr_exports" in src


# ------------------------------------------------------------------ auth / safety
def test_all_posts_require_token(env):
    srv, _, _ = env
    for path, body in [("/api/strategies/D3_V1_c2_both/state", {"state": "testing", "reason": "abc"}),
                       ("/api/experiments", {"strategy_id": "D3_V1_c2_both", "segment": "train"}),
                       ("/api/import/preview", {"filename": "a.csv", "content": "x"}),
                       ("/api/import/commit", {"filename": "a.csv", "content": "x"})]:
        assert call(srv, "POST", path, body, token=None)[0] == 401
        assert call(srv, "POST", path, body, token="wrong")[0] == 401
    posts = [(m, rx.pattern) for m, rx, fn in __import__("command_center.router", fromlist=["ROUTES"]).ROUTES
             if m == "POST" and fn.__module__ in ("command_center.api_research", "command_center.api_data")]
    assert len(posts) == 4


def test_no_shell_true_no_order_routes_no_broker_imports():
    for name in ("api_research.py", "api_data.py", "jobs.py"):
        tree = ast.parse((ROOT / "command_center" / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "shell":
                raise AssertionError(f"{name} passes shell=")
            if isinstance(node, ast.Call):
                f = node.func
                fn = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                assert fn not in ("system", "popen", "eval", "exec"), (name, fn)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = [a.name for a in node.names] + [getattr(node, "module", None) or ""]
                assert not any(re.search(r"ib_insync|ibapi|ibkr|ib_async|alpaca|ccxt|broker", m_ or "", re.I) for m_ in mods), (name, mods)
    from command_center import router
    router.load_modules()
    for m, rx, fn in router.ROUTES:
        if fn.__module__ in ("command_center.api_research", "command_center.api_data"):
            assert not re.search(r"order|trade|execut|position|broker|live", rx.pattern, re.I), rx.pattern
    # approved_* can never be written to the state table by this module
    assert "approved" not in " ".join(api_research.MUTABLE_STATES)


def test_json_helpers_nan_safe():
    assert api_data.clean({"a": float("nan"), "b": [float("inf"), 1.0]}) == {"a": None, "b": [None, 1.0]}
    assert not math.isnan(1.0)
