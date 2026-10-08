"""Research API (RESEARCH worker): strategy registry, experiment builder/queue, normalised results, audit.

* GET  /api/strategies, /api/strategies/<id>;  POST /api/strategies/<id>/state   (candidate|testing|rejected only)
* POST /api/experiments; GET /api/experiments, /api/experiments/<id>, /api/experiments/<id>/result, /api/experiments/compare?ids=a,b
* GET  /api/runs/committed, /api/runs/committed/<id>   (results/daily/*_summary.json normalised to the same schema)
* GET  /api/research/audit

Rules enforced here (research/CLAUDE.md): no order routes, no broker imports, no shell, the sealed final test is never runnable
from this module, Monte Carlo is labelled a conditional simulation, no 'profitable' / 'live-ready' flag is ever produced, and
training / validation / out-of-sample / forward-paper results are returned under separate keys.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from .api_data import (ApiError, Ctx, clean, ctx, handler, need_obj, rel, segment_problems)
from .jobs import JobQueue, subprocess_runner, utc_now
from .router import route

ROOT = Path(__file__).resolve().parents[1]
MUTABLE_STATES = ("candidate", "testing", "rejected")
ALL_STATES = ("candidate", "testing", "rejected", "approved_paper", "approved_live")
REFUSAL_MSG = ("requires explicit user approval recorded by the user plus an approved risk policy and a broker adapter; "
               "not available")
SEGMENTS_ALLOWED = ("train", "validation")
COST_SCENARIOS = {"x1": 1.0, "x1_5": 1.5, "x2": 2.0}   # equals configs/daily_costs.yaml scenarios (asserted in tests)
PREREG_SEED = 20261008
SYM_RE = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}\Z")
EXP_ID_RE = re.compile(r"^exp_\d{8}T\d{6}_[0-9a-f]{8}$")
MAX_ACTIVE_JOBS = 20

METRIC_DEFINITIONS = {
    "n": "closed trades in the segment, pooled across the selected symbols (trades are assigned to a segment by entry time; purged/straddling trades dropped)",
    "n_days_with_trades": "distinct New York entry dates with at least one trade",
    "ev_usd": "mean net P&L per trade in USD after modelled costs (cost scenario x1 unless a cost_stress row is named)",
    "ev_r": "mean net P&L divided by risk_usd = quantity x ATR14 (a normalisation only; the strategy has no stop)",
    "ev_bps": "mean net return per trade in basis points of entry notional",
    "hit": "fraction of trades with net P&L > 0",
    "avg_win": "mean net P&L of winning trades (USD)",
    "avg_loss": "mean net P&L of losing trades (USD)",
    "pf": "profit factor: gross wins / gross losses on net P&L",
    "maxdd_usd": "maximum drawdown of the cumulative closed-trade net P&L, USD",
    "exposure_days": "days with a trade / candidate trading days in the segment",
    "total_usd": "sum of net P&L over the segment, USD",
    "ci_usd": "95% day-block bootstrap confidence interval of mean net P&L per trade (USD); resamples whole trading days",
    "ci_r": "same, in R units",
    "p_day_one_sided": "day-clustered t-test, one-sided p for mean net P&L > 0",
    "p_day_two_sided": "day-clustered t-test, two-sided p",
    "p_bonferroni_day": "two-sided day-level p multiplied by the number of registered variants (16), capped at 1",
    "dsr": "deflated-Sharpe-style probability (approximation documented in tradelab.validation.stats.deflated_sharpe)",
    "top5_share": "(sum of the 5 best trades) / (total net P&L); undefined (null) when total <= 0",
    "longest_losing_streak": "longest run of consecutive losing trades in time order",
    "cost_stress": "EV re-priced as gross P&L minus m x modelled costs for m in {1.0, 1.5, 2.0}; cost model parameters are unverified assumptions (configs/daily_costs.yaml)",
    "B_D0": "baseline: every eligible day, long, open to close, same sizing and costs (drift reference, not the bar for a signal)",
    "B_D1": "baseline: matched random-day permutation (same count and long/short split per instrument, 10,000 draws); p is one-sided P(null >= observed)",
    "monte_carlo": "CONDITIONAL SIMULATION: resamples the same observed trades (method below); it adds no independent market evidence",
}
CAVEATS = [
    "Monte Carlo resamples the same evidence and adds no independent market evidence.",
    "Cost model parameters are unverified assumptions (configs/daily_costs.yaml); FX, SEC/FINRA fees and dividends are not modelled.",
    "Pooled symbols are highly correlated on the same day; inference clusters by date but n overstates independent information (docs/REVIEW.md D-01, D-10).",
    "A development verdict is not a profitability claim. 'Suitable for further paper testing' is the best attainable development outcome.",
]


def load_registry(c: Ctx) -> list[dict]:
    try:
        data = json.loads((Path(c.paths["configs_dir"]) / "strategies.json").read_text(encoding="utf-8"))
        return data["strategies"]
    except (OSError, ValueError, KeyError):
        raise ApiError(500, "strategy registry (configs/strategies.json) is missing or unreadable")


def find_strategy(c: Ctx, sid: str) -> dict | None:
    return next((s for s in load_registry(c) if s["id"] == sid), None)


def effective_state(c: Ctx, s: dict) -> tuple[str, list[dict]]:
    hist = c.rdb.all("SELECT ts, from_state, to_state, reason FROM strategy_state WHERE strategy_id=? ORDER BY id", (s["id"],))
    return (hist[-1]["to_state"] if hist else s["state"]), hist


def public_strategy(c: Ctx, s: dict, detail: bool = False) -> dict:
    state, hist = effective_state(c, s)
    out = {k: s[k] for k in ("id", "family", "name", "version", "verdict", "status_note", "rules", "source_research", "dataset",
                              "experiments", "validation", "approval_history") if k in s}
    out["state"] = state
    out["registered_state"] = s["state"]
    out["allowed_state_transitions"] = [x for x in MUTABLE_STATES if x != state]
    out["approved_states_available"] = False
    out["approved_states_note"] = REFUSAL_MSG
    out["runnable"] = bool(s.get("runnable"))
    if not s.get("runnable"):
        out["not_runnable_reason"] = s.get("not_runnable_reason")
    if s.get("dataset_note"):
        out["dataset_note"] = s["dataset_note"]
    refused = c.rdb.one("SELECT COUNT(*) AS n FROM audit WHERE kind='strategy.state' AND subject=? AND outcome='refused_approval'", (s["id"],))
    out["refused_approval_attempts"] = refused["n"] if refused else 0
    if detail:
        out["state_history"] = hist
        out["facts"] = s.get("facts")
        out["rule_links"] = {"doc": s["rules"]["doc"], "api": f"/api/docs/{Path(s['rules']['doc']).name}"}
        if s.get("dataset") and s["dataset"].get("manifest_sha256"):
            live = _live_manifest_sha(c)
            out["dataset_matches_current_manifest"] = {k: live.get(k) == v for k, v in s["dataset"]["manifest_sha256"].items()}
    return out


def _live_manifest_sha(c: Ctx) -> dict:
    try:
        return {e["symbol"]: e["sha256"] for e in json.loads(Path(c.paths["manifest"]).read_text())}
    except (OSError, ValueError, KeyError):
        return {}


def emit_event(c: Ctx, etype: str, payload: dict) -> None:
    """Best-effort audit event into the shared event store (append-only). Never raises."""
    try:
        if c.store is not None:
            c.store.insert([{"event_type": etype, "source": "research_api", "run_id": "research-audit", "payload": payload}])
    except Exception:
        pass


# ------------------------------------------------------------------ strategies
@route("GET", r"/api/strategies")
@handler
def api_strategies(h, m, q, body):
    c = ctx(h.server)
    return {"strategies": [public_strategy(c, s) for s in load_registry(c)],
            "states": list(ALL_STATES), "mutable_states": list(MUTABLE_STATES),
            "note": "approved_paper / approved_live cannot be set through this API: " + REFUSAL_MSG}


@route("GET", r"/api/strategies/([A-Za-z0-9_.@=,\-]+)")
@handler
def api_strategy(h, m, q, body):
    c = ctx(h.server)
    s = find_strategy(c, m.group(1))
    if not s:
        raise ApiError(404, "strategy not registered", code="strategy_not_registered")
    return public_strategy(c, s, detail=True)


@route("POST", r"/api/strategies/([A-Za-z0-9_.@=,\-]+)/state")
@handler
def api_strategy_state(h, m, q, body):
    c = ctx(h.server)
    b = need_obj(body)
    s = find_strategy(c, m.group(1))
    if not s:
        raise ApiError(404, "strategy not registered", code="strategy_not_registered")
    new, reason = b.get("state"), b.get("reason")
    if new not in ALL_STATES:
        raise ApiError(400, f"state must be one of {list(ALL_STATES)}")
    if not isinstance(reason, str) or len(reason.strip()) < 3 or len(reason) > 2000:
        raise ApiError(400, "reason is required (3-2000 characters)")
    cur, _ = effective_state(c, s)
    if new in ("approved_paper", "approved_live"):
        detail = {"from": cur, "requested": new, "reason": reason.strip()}
        c.rdb.audit("strategy.state", s["id"], "refused_approval", detail)
        emit_event(c, "incident", {"severity": "warning", "summary": f"REFUSED approval transition {s['id']}: {cur} -> {new}"})
        raise ApiError(403, REFUSAL_MSG, code="approval_not_available", strategy_id=s["id"], requested=new)
    if new == cur:
        raise ApiError(400, f"strategy is already in state {cur!r}")
    c.rdb.run("INSERT INTO strategy_state(strategy_id,ts,from_state,to_state,reason) VALUES(?,?,?,?,?)",
              (s["id"], utc_now(), cur, new, reason.strip()))
    c.rdb.audit("strategy.state", s["id"], "changed", {"from": cur, "to": new, "reason": reason.strip()})
    emit_event(c, "decision.summary", {"summary": f"Strategy {s['id']} state {cur} -> {new}: {reason.strip()[:300]}"})
    return public_strategy(c, s, detail=True)


# ------------------------------------------------------------------ experiments
def preregistered(c: Ctx, log_id: str) -> dict | None:
    """Row of docs/EXPERIMENT_LOG.md whose first cell equals log_id and whose line says REGISTERED."""
    try:
        text = (Path(c.paths["docs_dir"]) / "EXPERIMENT_LOG.md").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        cells = [x.strip() for x in line.strip().strip("|").split("|")]
        if cells and cells[0] == log_id and any(x.upper().startswith("REGISTERED") for x in cells[1:]):
            return {"id": cells[0], "row": line.strip()}
    return None


def deviation_row(c: Ctx, sid: str, seed: int, syms: list[str]) -> str:
    """Exact first-cell id a deviating run must have in docs/EXPERIMENT_LOG.md (status cell starting REGISTERED)."""
    return f"{sid}@seed={seed},symbols={'+'.join(sorted(syms))}"


def deviation_registered(c: Ctx, sid: str, seed: int, syms: list[str]) -> bool:
    want = deviation_row(c, sid, seed, syms)
    try:
        text = (Path(c.paths["docs_dir"]) / "EXPERIMENT_LOG.md").read_text(encoding="utf-8")
    except OSError:
        return False
    for line in text.splitlines():
        cells = [x.strip() for x in line.strip().strip("|").split("|")]
        if cells and cells[0] == want and any(x.upper().startswith("REGISTERED") for x in cells[1:]):
            return True
    return False


def log_test_refusal(c: Ctx, sid, who: str) -> None:
    c.rdb.audit("experiment.create", sid, "refused_test_segment", {"segment": "test", "who": who})
    emit_event(c, "incident", {"severity": "warning", "summary": f"REFUSED experiment on sealed test segment (strategy {sid})"})
    try:
        p = Path(c.paths["test_log"])
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"event": "final_test_access", "granted": False, "ts": utc_now(), "strategy_id": str(sid)[:128],
                                 "config_hash": None, "reason": "final test is reserved; the dashboard experiment builder never runs it",
                                 "purpose": "command_center POST /api/experiments", "source": "command_center"}) + "\n")
    except OSError:
        pass


def get_queue(c: Ctx) -> JobQueue:
    if c.queue is None:
        c.queue = JobQueue(c.rdb, c.paths["root"], runner=c.paths.get("runner") or subprocess_runner,
                           timeout_s=c.paths.get("timeout_s") or 900)
    return c.queue


def daily_cfg(c: Ctx) -> dict:
    import yaml
    return yaml.safe_load((Path(c.paths["configs_dir"]) / "daily_strategy.yaml").read_text())


def reject(c, sid, code, status, message, **extra):
    c.rdb.audit("experiment.create", sid if isinstance(sid, str) else None, f"refused:{code}", {"message": message, **{k: v for k, v in extra.items() if k != "inputs_inadequate"}})
    raise ApiError(status, message, code=code, **extra)


@route("POST", r"/api/experiments")
@handler
def api_create_experiment(h, m, q, body):
    c = ctx(h.server)
    b = need_obj(body)
    allowed = {"strategy_id", "symbols", "segment", "cost_scenario", "seed", "notes"}
    extra = sorted(set(b) - allowed)
    if extra:
        raise ApiError(400, f"unknown fields: {extra}")
    sid, seg = b.get("strategy_id"), b.get("segment")
    if seg == "test":
        log_test_refusal(c, sid, "api")
        raise ApiError(403, "segment 'test' is the sealed final test and is reserved; it is evaluated at most once per frozen strategy "
                            "by the research coordinator via the final-test guard, never from this builder. This attempt was logged.",
                       code="test_segment_reserved")
    if seg not in SEGMENTS_ALLOWED:
        raise ApiError(400, f"segment must be one of {list(SEGMENTS_ALLOWED)}")
    if not isinstance(sid, str) or not sid:
        raise ApiError(400, "strategy_id is required")
    strat = find_strategy(c, sid)
    if not strat:
        reject(c, sid, "strategy_not_registered", 400, "strategy is not registered in configs/strategies.json")
    if not strat.get("runnable"):
        reject(c, sid, "strategy_not_runnable", 422, "strategy is registered but cannot be run by this builder",
               reason=strat.get("not_runnable_reason"))
    cs = b.get("cost_scenario", "x1")
    if cs not in COST_SCENARIOS:
        raise ApiError(400, f"cost_scenario must be one of {list(COST_SCENARIOS)}")
    seed = b.get("seed", PREREG_SEED)
    if isinstance(seed, bool) or not isinstance(seed, int) or not (0 <= seed < 2**31):
        raise ApiError(400, "seed must be an integer in [0, 2^31)")
    notes = b.get("notes", "")
    if not isinstance(notes, str) or len(notes) > 2000:
        raise ApiError(400, "notes must be a string (<=2000 chars)")
    cfg = daily_cfg(c)
    syms = b.get("symbols", list(cfg["universe"]))
    if (not isinstance(syms, list) or not syms or len(syms) > 10 or not all(isinstance(x, str) and SYM_RE.match(x) for x in syms)
            or len(set(syms)) != len(syms)):
        raise ApiError(400, "symbols must be a non-empty list of unique upper-case tickers")
    incompatible = [{"symbol": x, "code": "not_in_strategy_universe",
                     "detail": f"{x} is not in the registered universe {cfg['universe']} of {sid}"} for x in syms if x not in cfg["universe"]]
    if incompatible:
        reject(c, sid, "inputs_incompatible", 422, "inputs are incompatible with the registered strategy", inputs_inadequate=incompatible)
    log_id = strat["runnable"]["experiment_log_id"]
    reg = preregistered(c, log_id)
    if not reg:
        reject(c, sid, "not_preregistered", 422, f"{log_id} is not registered in docs/EXPERIMENT_LOG.md; pre-registration is required before any run")
    probs = segment_problems(c, syms, seg, cfg["split"])
    if probs:
        c.rdb.audit("experiment.create", sid, "refused:inputs_inadequate", {"problems": probs[:20]})
        raise ApiError(422, "inputs_inadequate", code="inputs_inadequate", inputs_inadequate=probs)
    deviates = sorted(syms) != sorted(cfg["universe"]) or seed != PREREG_SEED
    if deviates and not deviation_registered(c, sid, seed, syms):
        row = deviation_row(c, sid, seed, syms)
        reject(c, sid, "not_preregistered", 422,
               "this run deviates from the pre-registered defaults (all universe symbols, seed 20261008) and is not registered. Register first, then run: add a row to "
               f"docs/EXPERIMENT_LOG.md whose first cell is exactly '{row}' and whose status starts with REGISTERED.",
               required_log_id=row, defaults={"seed": PREREG_SEED, "symbols": list(cfg["universe"])})
    q_ = get_queue(c)
    active = [j for j in q_.list(200) if j["status"] in ("queued", "running")]
    if len(active) >= MAX_ACTIVE_JOBS:
        raise ApiError(429, f"too many queued/running experiments (max {MAX_ACTIVE_JOBS})")
    import uuid
    import time
    jid = f"exp_{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}_{uuid.uuid4().hex[:8]}"
    out_dir = Path(c.paths["experiments_dir"]) / jid
    variant = strat["runnable"]["variant"]
    argv = [sys.executable, str(Path(c.paths["root"]) / "scripts" / "run_daily.py"), "--segment", seg, "--variant", variant,
            "--symbols", ",".join(syms), "--seed", str(seed), "--out-dir", str(out_dir),
            "--data-dir", str(Path(c.paths["data_dir"]).resolve()), "--progress"]
    params = {"strategy_id": sid, "pipeline_variant": variant, "symbols": syms, "segment": seg, "cost_scenario": cs, "seed": seed,
              "notes": notes, "pre_registration": {"experiment_log_id": log_id, "doc": "docs/EXPERIMENT_LOG.md", "found": True},
              "deviates_from_preregistered_defaults": deviates, "deviation_registered": bool(deviates),
              "deviation_note": ("symbols/seed differ from the pre-registered defaults (all universe symbols, seed 20261008) but this run is registered in docs/EXPERIMENT_LOG.md; "
                                 "the job ledger (research.sqlite3) also records this run."
                                 if deviates else None),
              "cost_scenario_semantics": "the pipeline always prices x1/x1.5/x2; cost_scenario selects which row is shown as the headline",
              "dataset_manifest_sha256": {s: v for s, v in _live_manifest_sha(c).items() if s in syms}}
    job = q_.submit("experiment", params, argv, str(out_dir), job_id=jid)
    c.rdb.audit("experiment.create", sid, "queued", {"job_id": jid, "segment": seg, "symbols": syms, "seed": seed, "cost_scenario": cs})
    return 202, {"ok": True, "experiment": job, "links": {"self": f"/api/experiments/{jid}", "result": f"/api/experiments/{jid}/result"}}


def _exp_public(c: Ctx, job: dict, detail=False) -> dict:
    out = dict(job)
    p = job["params"]
    out["strategy_id"], out["segment"], out["symbols"] = p.get("strategy_id"), p.get("segment"), p.get("symbols")
    out["seed"], out["cost_scenario"] = p.get("seed"), p.get("cost_scenario")
    out["links"] = {"self": f"/api/experiments/{job['id']}", "result": f"/api/experiments/{job['id']}/result"}
    if not detail:
        out.pop("params", None)
    return out


def _get_exp(c: Ctx, jid: str) -> dict:
    if not EXP_ID_RE.match(jid):
        raise ApiError(404, "experiment not found")
    job = get_queue(c).get(jid)
    if not job or job["kind"] != "experiment":
        raise ApiError(404, "experiment not found")
    return job


@route("GET", r"/api/experiments")
@handler
def api_experiments(h, m, q, body):
    c = ctx(h.server)
    jobs = [j for j in get_queue(c).list(int(q.get("limit", 100))) if j["kind"] == "experiment"]
    return {"experiments": [_exp_public(c, j) for j in jobs], "worker": "single worker thread; jobs run one at a time in submission order"}


@route("GET", r"/api/experiments/compare")
@handler
def api_compare(h, m, q, body):
    c = ctx(h.server)
    ids = [x for x in (q.get("ids") or "").split(",") if x]
    if not (2 <= len(ids) <= 6) or len(set(ids)) != len(ids):
        raise ApiError(400, "ids must list 2-6 distinct run ids (experiment ids or committed:<segment>:<variant>)")
    results = [result_for(c, i) for i in ids]
    rows, warns = [], []
    for r in results:
        ev = r["evidence"]

        def brief(blk):
            if not blk:
                return None
            pooled = blk["metrics"]["pooled"]
            sel = blk["cost_stress"]["selected_scenario"]["row"] if blk["cost_stress"].get("selected_scenario") else None
            return {"n": pooled.get("n"), "ev_usd": pooled.get("ev_usd"), "ci_usd": pooled.get("ci_usd"), "hit": pooled.get("hit"),
                    "maxdd_usd": pooled.get("maxdd_usd"), "selected_cost_row": sel}
        rows.append({"id": r["id"], "strategy_id": r["strategy"]["id"], "run": r["run"], "training": brief(ev["training"]),
                     "validation": brief(ev["validation"]), "out_of_sample_test": ev["out_of_sample_test"],
                     "forward_paper": ev["forward_paper"], "verdict": r["verdict"]})
    for key in ("symbols", "seed", "cost_scenario"):
        if len({json.dumps(r["run"].get(key)) for r in results}) > 1:
            warns.append(f"runs differ in {key}: not like-for-like")
    return {"ids": ids, "table": rows, "results": results, "warnings": warns,
            "note": "Segments are never merged: training and validation are separate keys. Monte Carlo blocks are conditional simulations."}


@route("GET", r"/api/experiments/(exp_[A-Za-z0-9_]+)")
@handler
def api_experiment(h, m, q, body):
    c = ctx(h.server)
    return _exp_public(c, _get_exp(c, m.group(1)), detail=True)


@route("GET", r"/api/experiments/(exp_[A-Za-z0-9_]+)/result")
@handler
def api_experiment_result(h, m, q, body):
    c = ctx(h.server)
    return result_for(c, m.group(1))


# ------------------------------------------------------------------ result normalisation
def _load_summary(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))   # python json accepts the pipeline's NaN; clean() converts to null at output


def _mc_block(vr: dict, account_size) -> dict | None:
    mc = vr.get("monte_carlo")
    if not mc or "distributions" not in mc:
        return None
    return {"label": "CONDITIONAL SIMULATION: resamples the observed trades of this segment; adds no independent market evidence",
            "method": mc.get("method"), "seed": mc.get("seed"), "paths": mc.get("n_sims"),
            "horizon": {"definition": "each path resamples the segment's own trades (day-block bootstrap); there is no forward calendar horizon",
                        "trades_per_path": mc["distributions"].get("trades_per_path")},
            "percentiles": {k: v for k, v in mc["distributions"].items() if k != "trades_per_path"},
            "probabilities": mc.get("probabilities"),
            "thresholds": {"account_size_usd": account_size, "max_drawdown_limit_usd": None, "daily_loss_limit_usd": None,
                           "note": "limits are not configured in the pipeline run, so p_breach_* are null; p_ruin is as defined in tradelab.validation.montecarlo"},
            "pipeline_caveat": mc.get("caveat")}


def _evidence_block(summary: dict, variant: str, cost_scenario: str | None, account_size) -> dict:
    seg = summary["segment"]
    vr = summary["variants"][variant]
    rows = vr.get("cost_stress", [])
    sel = None
    if cost_scenario:
        mult = COST_SCENARIOS[cost_scenario]
        sel = {"name": cost_scenario, "multiplier": mult, "row": next((r for r in rows if r.get("cost_mult") == mult), None)}
    split = summary.get("split", {})
    return {"segment": seg, "window_utc": split.get(seg), "purge": split.get("purge"),
            "n_candidate_days_union": summary.get("n_candidate_days_union"), "n_eligible_per_symbol": summary.get("n_eligible_per_symbol"),
            "metrics": {"pooled": vr["pooled"], "per_symbol": vr["per_symbol"], "long_short": vr["long_short"]},
            "signal_counts_per_symbol_long_short": vr.get("signal_counts_per_symbol_long_short"),
            "cost_stress": {"rows": rows, "selected_scenario": sel},
            "baselines": {"B_D0": summary.get("B_D0"), "B_D1": vr.get("B_D1")},
            "quarters": vr.get("quarters"), "dropped_by_split": vr.get("dropped_by_split"),
            "monte_carlo": _mc_block(vr, account_size)}


def normalise(summary: dict, variant: str, *, rid: str, source: dict, run: dict, cost_scenario: str | None, account_size) -> dict:
    seg = summary["segment"]
    key = {"train": "training", "validation": "validation"}.get(seg)
    if key is None:
        raise ApiError(409, "unsupported segment in summary")
    evidence = {"training": None, "validation": None,
                "out_of_sample_test": {"available": False, "reason": "the final test is sealed; it is evaluated at most once per frozen strategy via the final-test guard (results/test_access_log.jsonl)"},
                "forward_paper": {"available": False, "reason": "no paper trading has occurred: no approved broker adapter or risk engine exists"}}
    evidence[key] = _evidence_block(summary, variant, cost_scenario, account_size)
    vd = (summary.get("verdict") or {}).get(variant)
    verdict = None
    if vd:
        reasons = list(vd.get("reasons", []))
        verdict = {"value": vd.get("verdict"), "reasons": reasons, "failed_gates": [r for r in reasons if r.startswith("not met")],
                   "inputs": vd.get("inputs"), "rule": "validation.verdict.classify_development as produced by the pipeline (docs/VALIDATION_PLAN.md section 5)",
                   "scope": "development verdict on train+validation; says nothing about the sealed test, and is not a profitability or readiness claim"}
    return {"schema": "cc.result.v1", "id": rid, "source": source,
            "labels": ["descriptive backtest" + (" + Monte Carlo (conditional simulation)" if evidence[key].get("monte_carlo") else ""),
                       "SYNTHETIC data (software test only)" if run.get("synthetic_data") else "market data: IBKR connector daily bars"],
            "strategy": {"id": run.get("registry_id"), "pipeline_variant": variant, "pipeline_strategy_id": summary["variants"][variant].get("strategy_id")},
            "run": run, "metric_definitions": METRIC_DEFINITIONS, "evidence": evidence, "verdict": verdict,
            "caveats": CAVEATS}


def account_size_of(c: Ctx):
    try:
        return daily_cfg(c)["mc"]["account_size"]
    except Exception:
        return None


def result_for(c: Ctx, rid: str) -> dict:
    if rid.startswith("committed:"):
        return committed(c, rid)
    job = _get_exp(c, rid)
    if job["status"] in ("queued", "running"):
        raise ApiError(409, f"experiment is {job['status']}; no result yet", job_status=job["status"], progress=job["progress"])
    if job["status"] == "failed":
        raise ApiError(409, "experiment failed; there is no result. See the experiment's stderr_tail and error.", job_status="failed",
                       exit_code=job["exit_code"], error=job["error"], stderr_tail=job["stderr_tail"])
    p = job["params"]
    f = get_queue(c).out_dir_abs(job["id"]) / f"{p['segment']}_summary.json"
    if not f.is_file():
        raise ApiError(500, "job completed but its summary file is missing")
    summary = _load_summary(f)
    run = {"segment": p["segment"], "symbols": p["symbols"], "seed": p["seed"], "cost_scenario": p["cost_scenario"],
           "registry_id": p["strategy_id"], "job_id": job["id"], "started_at": job["started_at"], "finished_at": job["finished_at"],
           "exit_code": job["exit_code"], "notes": p.get("notes"), "pre_registration": p.get("pre_registration"),
           "deviates_from_preregistered_defaults": p.get("deviates_from_preregistered_defaults"),
           "dataset_manifest_sha256": p.get("dataset_manifest_sha256"),
           "synthetic_data": "SYNTHETIC" in str(c.paths["data_dir"]).upper()}
    return normalise(summary, p["pipeline_variant"], rid=job["id"], source={"kind": "experiment", "job_id": job["id"], "file": f"{rel(c, f)}"},
                     run=run, cost_scenario=p["cost_scenario"], account_size=account_size_of(c))


# ------------------------------------------------------------------ committed runs
COMMITTED_RE = re.compile(r"^committed:(train|validation):(D3_V[123]_[a-z0-9_]+)$")


def committed_list(c: Ctx) -> list[dict]:
    out = []
    for seg in ("train", "validation"):
        f = Path(c.paths["results_daily"]) / f"{seg}_summary.json"
        if not f.is_file():
            continue
        s = _load_summary(f)
        for v, vr in s.get("variants", {}).items():
            vd = (s.get("verdict") or {}).get(v)
            out.append({"id": f"committed:{seg}:{v}", "segment": seg, "variant": v, "strategy_id": vr.get("strategy_id"),
                        "n": vr["pooled"].get("n"), "ev_usd": vr["pooled"].get("ev_usd"),
                        "verdict": vd.get("verdict") if vd else None, "file": rel(c, f)})
    return out


def committed(c: Ctx, rid: str) -> dict:
    mt = COMMITTED_RE.match(rid)
    if not mt:
        raise ApiError(404, "unknown committed run id")
    seg, variant = mt.groups()
    f = Path(c.paths["results_daily"]) / f"{seg}_summary.json"
    if not f.is_file():
        raise ApiError(404, "committed summary not found")
    s = _load_summary(f)
    if variant not in s.get("variants", {}):
        raise ApiError(404, "variant not in committed summary")
    cfg = daily_cfg(c)
    reg = next((x["id"] for x in load_registry(c) if (x.get("runnable") or {}).get("variant") == variant), None)
    run = {"segment": seg, "symbols": list(s["n_eligible_per_symbol"].keys()), "seed": (s["variants"][variant].get("monte_carlo") or {}).get("seed"),
           "cost_scenario": None, "registry_id": reg, "job_id": None, "origin": "committed pipeline output (scripts/run_daily.py defaults)",
           "dataset_manifest_sha256": _live_manifest_sha(c), "synthetic_data": False}
    return normalise(s, variant, rid=rid, source={"kind": "committed_run", "file": rel(c, f)}, run=run, cost_scenario="x1",
                     account_size=cfg["mc"]["account_size"])


@route("GET", r"/api/runs/committed")
@handler
def api_committed_list(h, m, q, body):
    c = ctx(h.server)
    return {"runs": committed_list(c), "note": "results/daily/*_summary.json; train and validation segments are separate runs"}


@route("GET", r"/api/runs/committed/(committed:[A-Za-z0-9_:]+)")
@handler
def api_committed_one(h, m, q, body):
    return committed(ctx(h.server), m.group(1))


@route("GET", r"/api/research/audit")
@handler
def api_audit(h, m, q, body):
    c = ctx(h.server)
    rows = c.rdb.all("SELECT id, ts, kind, subject, outcome, detail FROM audit ORDER BY id DESC LIMIT ?", (max(1, min(int(q.get("limit", 200)), 1000)),))
    for r in rows:
        r["detail"] = json.loads(r["detail"])
    return {"entries": rows}


# ------------------------------------------------------------------ series / trade log (aggregates from the pipeline's CSV output)
SERIES_DEFS = {
    "net_pnl": "sum of net P&L (after modelled costs) of trades entered that New York date, USD",
    "gross_pnl": "same before costs", "costs": "modelled round-trip costs of those trades (unverified assumptions)",
    "n_trades": "trades entered that date", "equity": "cumulative net P&L since the start of the segment (USD, starts at 0; not an account balance)",
    "drawdown": "equity minus its running maximum (including the starting 0), USD; closed-trade basis",
}


def _csv_rows(path: Path, variant: str) -> list[dict]:
    import csv
    with open(path, newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if r.get("variant") == variant]


def _run_files(c: Ctx, rid: str) -> tuple[Path, str, str, dict]:
    """(directory with the CSVs, variant, segment, extra info) for an experiment id or committed id."""
    if rid.startswith("committed:"):
        mt = COMMITTED_RE.match(rid)
        if not mt:
            raise ApiError(404, "unknown committed run id")
        seg, variant = mt.groups()
        d = Path(c.paths["experiments_dir"]) / "committed_repro" / seg
        info = {"origin": "reproduced with the pre-registered settings into results/experiments/committed_repro (not the committed files themselves)"}
        chk = Path(c.paths["experiments_dir"]) / "committed_repro" / "REPRO_CHECK.json"
        try:
            rep = json.loads(chk.read_text())
            info["reproduction_equals_committed_summary"] = rep["segments"][seg]["equal"]
        except (OSError, ValueError, KeyError):
            info["reproduction_equals_committed_summary"] = None
        if not (d / "daily_series.csv").is_file():
            raise ApiError(404, "series not generated yet; run `python -m command_center.repro_committed` (reproduces and verifies the committed runs)",
                           code="series_not_generated")
        return d, variant, seg, info
    job = _get_exp(c, rid)
    if job["status"] != "completed":
        raise ApiError(409, f"experiment is {job['status']}; no series", job_status=job["status"])
    d = get_queue(c).out_dir_abs(job["id"])
    if not (d / "daily_series.csv").is_file():
        raise ApiError(404, "this run has no series file (it predates series output)")
    return d, job["params"]["pipeline_variant"], job["params"]["segment"], {}


@route("GET", r"/api/(?:experiments/(exp_[A-Za-z0-9_]+)|runs/committed/(committed:[A-Za-z0-9_:]+))/series")
@handler
def api_series(h, m, q, body):
    c = ctx(h.server)
    d, variant, seg, info = _run_files(c, m.group(1) or m.group(2))
    rows = _csv_rows(d / "daily_series.csv", variant)
    cols = ["date", "net_pnl", "gross_pnl", "costs", "n_trades", "equity", "drawdown"]
    out = [[r["date"]] + [float(r[k]) if k != "n_trades" else int(r[k]) for k in cols[1:]] for r in rows]
    return {"id": m.group(1) or m.group(2), "segment": seg, "variant": variant, "columns": cols, "rows": out, "definitions": SERIES_DEFS,
            "label": "per-trading-day aggregates of the segment's trades; not prices; not an account balance", **info}


@route("GET", r"/api/(?:experiments/(exp_[A-Za-z0-9_]+)|runs/committed/(committed:[A-Za-z0-9_:]+))/trades")
@handler
def api_trades(h, m, q, body):
    c = ctx(h.server)
    d, variant, seg, info = _run_files(c, m.group(1) or m.group(2))
    try:
        off, lim = max(0, int(q.get("offset", 0))), max(1, min(int(q.get("limit", 100)), 500))
    except ValueError:
        raise ApiError(400, "offset/limit must be integers")
    rows = _csv_rows(d / "trades.csv", variant)
    cols = ["trade_id", "symbol", "side", "entry_date", "exit_date", "qty", "entry_px", "exit_px", "pnl_gross", "costs", "pnl_net", "r_multiple"]
    page = [{k: (r[k] if k in ("symbol", "entry_date", "exit_date") else float(r[k])) for k in cols} for r in rows[off:off + lim]]
    for r in page:
        r["side"] = "short" if r["side"] < 0 else "long"
    return {"id": m.group(1) or m.group(2), "segment": seg, "total": len(rows), "offset": off, "limit": lim, "trades": page,
            "note": "side: long/short; prices are the daily open (entry) and close (exit) of the traded bar; r_multiple uses ATR14 risk_usd (no stop exists)"}
