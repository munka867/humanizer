"""Reviewer reproducers. Each test asserts the DESIRED behaviour; tests marked xfail(strict=True)
currently FAIL because of the finding with the same id in docs/REVIEW.md. When the owner fixes the
issue the strict xfail turns into an error, prompting removal of the marker. SYNTHETIC data only."""
import datetime as dt
import pandas as pd
import pytest
from test_backtest_helpers import ECFG, MES, SHORT_SWEEP, history, to_df, rth_bars
from tradelab.backtest import config as cfgmod
from tradelab.backtest.engine import run_backtest
from tradelab.backtest.sweep_reversal import SweepParams, SweepReversal
from tradelab.data.loader import load_bars
from tradelab.data.synthetic import make_synthetic_bars
from tradelab.data.loader import resample_bars
import importlib.util, pathlib, re, itertools

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("run_backtest_script", ROOT / "scripts" / "run_backtest.py")
rb = importlib.util.module_from_spec(spec); spec.loader.exec_module(rb)


def h1(bars):
    return run_backtest(bars, SweepReversal(MES, SweepParams(levels="both")), ECFG)


# ---- R-02 abbreviated holiday sessions are traded
def test_memorial_day_is_not_traded():
    bars = history("2025-05-27", "2025-05-23", over={"09:35": SHORT_SWEEP})   # Tue baseline sanity below
    assert len(h1(bars).trades) == 1                                           # a normal day does trade
    bars = history("2025-05-26", "2025-05-23", over={"09:35": SHORT_SWEEP})   # Memorial Day (Mon)
    assert len(h1(bars).trades) == 0


# ---- R-03 bar spacing not validated
def test_one_minute_bars_rejected_not_silently_empty():
    b1 = make_synthetic_bars(3, start="2024-01-02", days=6)
    with pytest.raises(ValueError):
        run_backtest(b1, SweepReversal(MES, SweepParams(levels="both")), ECFG)


# ---- R-04 engine accepts corrupt OHLC
def test_engine_rejects_high_below_low():
    bars = history("2025-03-04", "2025-03-03")
    bars.iloc[300, bars.columns.get_loc("high")] = bars.iloc[300]["low"] - 5
    with pytest.raises(ValueError):
        run_backtest(bars, SweepReversal(MES, SweepParams()), ECFG)


# ---- R-05 run_backtest.py bypasses the tolerant loader: naive local timestamps silently read as UTC
def test_run_backtest_script_rejects_naive_timestamps(tmp_path):
    b = history("2025-03-04", "2025-03-03", over={"09:35": SHORT_SWEEP})
    naive = b.copy()
    naive.index = naive.index.tz_convert("America/New_York").tz_localize(None)
    p = tmp_path / "SYNTHETIC_naive.csv"
    naive.to_csv(p, index_label="ts_open")
    with pytest.raises(Exception):
        rb.main(["--bars", str(p), "--strategy", "sweep_reversal", "--out", str(tmp_path / "o")])


# ---- R-06 loader accepts 'symbol' as the contract label
def test_loader_does_not_treat_root_symbol_as_contract(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("timestamp,open,high,low,close,volume,symbol\n2024-03-04T14:30:00Z,1,2,0.5,1.5,10,MES\n"
                 "2024-03-04T14:35:00Z,1,2,0.5,1.5,10,MES\n")
    df = load_bars(p)
    assert "contract" not in df.columns


# ---- R-07 / R-01 B0 trades every eligible day, not the days H1 trades
def test_b0_days_match_h1_days(tmp_path):
    b = resample_bars(make_synthetic_bars(7, start="2024-01-02", days=90), "5min")
    p = tmp_path / "SYNTHETIC_b.csv"; b.to_csv(p, index_label="ts_open")
    out = []
    for s in ("sweep_reversal", "random_entry"):
        rb.main(["--bars", str(p), "--strategy", s, "--out", str(tmp_path / s), "--reps", "1"])
        t = pd.read_csv(tmp_path / s / "trades.csv", parse_dates=["entry_ts"])
        out.append(set(t.entry_ts.dt.tz_convert("America/New_York").dt.date))
    assert out[1] <= out[0]


# ---- R-08 default variant id is not in the experiment log
def test_every_runnable_variant_id_is_logged():
    log = (ROOT / "docs" / "EXPERIMENT_LOG.md").read_text()
    for name in ("strategy_sweep_reversal", "strategy_orb", "strategy_random_entry"):
        cfg = cfgmod.load_yaml(ROOT / "configs" / f"{name}.yaml")
        strat, _, _ = cfgmod.build(cfg, ROOT / "configs" / "costs_mes.yaml")
        assert f"| {strat.name} |" in log, strat.name


def test_grid_variant_ids_all_logged_and_count_within_budget():
    """PASSES: 9 + 3 + 1 variants in the log match the spec grid exactly (<= 24 budget)."""
    log = (ROOT / "docs" / "EXPERIMENT_LOG.md").read_text()
    ids = re.findall(r"^\| ([A-Za-z0-9_@=.,]+) \|", log, flags=re.M)
    ids = [i for i in ids if i not in ("id",)]
    expect = []
    for name in ("strategy_sweep_reversal", "strategy_orb", "strategy_random_entry"):
        cfg = cfgmod.load_yaml(ROOT / "configs" / f"{name}.yaml")
        grid = cfg.get("grid", {})
        keys = sorted(grid)
        combos = itertools.product(*[grid[k] for k in keys]) if keys else [()]
        for c in combos:
            ov = dict(zip(keys, c))
            expect.append(cfgmod.variant_id(cfg, ov or None))
    assert sorted(ids) == sorted(expect) and len(ids) <= 24


# ---- R-09 B0 replications concatenated with duplicate trade_ids
def test_b0_reps_have_unique_trade_ids_or_rep_column(tmp_path):
    b = resample_bars(make_synthetic_bars(7, start="2024-01-02", days=60), "5min")
    p = tmp_path / "SYNTHETIC_b.csv"; b.to_csv(p, index_label="ts_open")
    rb.main(["--bars", str(p), "--strategy", "random_entry", "--out", str(tmp_path / "o"), "--reps", "3"])
    t = pd.read_csv(tmp_path / "o" / "trades.csv")
    assert "rep" in t.columns and not t.duplicated(["rep", "trade_id"]).any()   # (rep, trade_id) is the key, NOT trade_id
    assert t.trade_id.duplicated().any()    # documents: trade_id alone is not unique across reps
