"""Phase-5 reviewer: the worker's library (tradelab.daily_trend) against the independent loop oracle on TRAIN and VALIDATION only
(dataset A truncated strictly before the sealed start; nothing at/after 2025-12-20 is ever evaluated). Tolerance 1e-9."""
import datetime as dt
import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests")); sys.path.insert(0, str(ROOT / "src"))
import oracle_trend as O                                   # noqa: E402
import oracle_trend_run as R                               # noqa: E402

pytest.importorskip("tradelab.daily_trend.analysis")
from tradelab.daily_trend import accounting as A, analysis as AN      # noqa: E402

DATA = ROOT / "data" / "processed"
pytestmark = pytest.mark.skipif(not (DATA / "SPY_1d.csv").exists(), reason="dataset A not present")
KINDS = {"V1": {"kind": "sma200"}, "V2": {"kind": "cross", "fast": 50, "slow": 200}, "V3": {"kind": "donchian", "entry": 100, "exit": 50}}


@pytest.fixture(scope="module")
def world():
    sp, cutoff = O.segment_bounds(str(DATA))
    bars = {}
    for s in R.SYMS:
        df = pd.read_csv(DATA / f"{s}_1d.csv")
        df["ts_open"] = pd.to_datetime(df["ts_open"], utc=True)
        df = df.set_index("ts_open")
        bars[s] = AN.truncate_before(df, pd.Timestamp(cutoff))
    return sp, bars


def worker_numbers(world, v, seg, purge):
    sp, bars = world
    start, end = (sp.train[0], sp.train[1]) if seg == "train" else (sp.validation[0] + (sp.purge if purge else pd.Timedelta(0)), sp.validation[1])
    frames = {s: AN.symbol_frame(bars[s], KINDS[v]) for s in R.SYMS}
    dates = AN.common_dates(frames, start, end)
    win = AN.window(frames, dates, A.TrendCosts())
    out = {}
    for s in R.SYMS:
        w = win[s]
        out[s] = AN.segment_metrics(w["r"], w["r_bh"], w["p"], w["pp"][0])
    r, rbh = AN.pooled(win, "r"), AN.pooled(win, "r_bh")
    out["POOLED"] = AN.segment_metrics(r, rbh, np.mean([win[s]["p"] for s in R.SYMS], axis=0), 0)
    return out


@pytest.mark.parametrize("purge", [True, False])
@pytest.mark.parametrize("v", ["V1", "V2", "V3"])
@pytest.mark.parametrize("seg", ["train", "validation"])
def test_worker_matches_oracle(world, v, seg, purge):
    mine = R.run(purge_validation=purge, common_only=True)[f"{v}/{seg}"]
    theirs = worker_numbers(world, v, seg, purge)
    for s in ("SPY", "QQQ", "IWM", "POOLED"):
        m, t = mine[s], theirs[s]
        assert t["n_days"] == m["n"], (s, "n")
        for a, b in (("total_return", "total"), ("bh_total_return", "total_bh"), ("max_dd", "mdd"), ("bh_max_dd", "mdd_bh"),
                     ("dd_reduction", "dd_reduction"), ("vol", "vol"), ("time_in_market", "time_in_market")):
            assert t[b] == pytest.approx(m[a], abs=1e-9), (s, a, m[a], t[b])
        if s != "POOLED":
            assert t["switches"] == m["switches"], (s, "switches")
            assert t["buys"] == m["round_trips"], (s, "buys")


# ---------------------------------------------------------------- audit of the worker's inference helpers (synthetic inputs)
def test_run_length_shuffle_preserves_time_in_market_switches_first_state():
    rng = np.random.default_rng(1)
    for p in ([1, 1, 0, 0, 0, 1, 0, 1, 1, 1, 0], [0, 0, 1, 1, 0, 0, 0, 1], [1] * 6, [0] * 4 + [1] * 3):
        p = np.array(p)
        f, on, off = AN.run_structure(p)
        for _ in range(300):
            q = AN.shuffle_runs(f, on, off, rng)
            assert len(q) == len(p) and q.sum() == p.sum() and q[0] == p[0] and (np.diff(q) != 0).sum() == (np.diff(p) != 0).sum()


def test_moving_block_indices_are_contiguous_and_in_range():
    idx = AN.block_indices(23, 10, 50, np.random.default_rng(0))
    assert idx.shape == (50, 23) and idx.min() >= 0 and idx.max() < 23
    assert (np.diff(idx[:, :10], axis=1) == 1).all()


def test_verdict_truth_table_matches_preregistered_rule():
    m = lambda tot, bh, red: {"total": tot, "total_bh": bh, "dd_reduction": red}
    bad = m(0.10, 0.20, 0.10)
    assert AN.verdict({"train": bad, "validation": bad})[0] == "REJECTED"
    assert AN.verdict({"train": bad, "validation": m(0.10, 0.20, 0.30)})[0] == "INCONCLUSIVE"       # DD reduction >= 25% in one segment
    assert AN.verdict({"train": bad, "validation": m(0.25, 0.20, 0.10)})[0] == "INCONCLUSIVE"       # beat B_H in one segment
    assert AN.verdict({"train": bad, "validation": m(0.10, 0.20, float("nan"))})[0] == "INCONCLUSIVE"
    assert AN.verdict({"train": bad, "validation": bad})[0] != "SUITABLE"


def test_null_has_few_distinct_arrangements_for_short_run_structures():
    """Documents P5-02: with 3 on-runs / 2 off-runs the shuffle null has at most 3! x 2! = 12 distinct series per ETF, so a 10,000-draw
    'percentile' printed to 3 decimals is far more precise than the null supports."""
    rng = np.random.default_rng(0)
    p = np.array([1] * 20 + [0] * 5 + [1] * 30 + [0] * 7 + [1] * 10)
    f, on, off = AN.run_structure(p)
    distinct = {tuple(AN.shuffle_runs(f, on, off, rng)) for _ in range(2000)}
    assert len(distinct) <= 12


def test_script_refuses_test_segment():
    import importlib.util
    spec = importlib.util.spec_from_file_location("rt", ROOT / "scripts" / "run_trend.py")
    rt = importlib.util.module_from_spec(spec); spec.loader.exec_module(rt)
    assert rt.main(["--segment", "test"]) == 2


def test_nothing_at_or_after_sealed_start_survives_truncation(world):
    sp, bars = world
    assert all(b.index.max() < sp.test[0] for b in bars.values())


@pytest.mark.xfail(strict=True, reason="P5-03: pooled scope reports no switch / round-trip counts (spec: 'per segment, per ETF and pooled')")
def test_pooled_scope_reports_switch_counts():
    import importlib.util
    spec = importlib.util.spec_from_file_location("rt", ROOT / "scripts" / "run_trend.py")
    rt = importlib.util.module_from_spec(spec); spec.loader.exec_module(rt)
    cfg = rt.load_cfg()
    cfg["b_r_null"]["n_draws"] = 50; cfg["bootstrap"].update(blocks=[10], n_resamples=100); cfg["mc"].update(blocks=[10], n_paths=50)
    cfg["variants"] = {"D4_V1_sma200": cfg["variants"]["D4_V1_sma200"]}
    costs, scen, bars, split, bounds = rt.setup(cfg)
    res = rt.analyse_segment("validation", cfg, costs, scen, bars, bounds)
    assert "switches" in res["variants"]["D4_V1_sma200"]["scopes"]["pooled"]
