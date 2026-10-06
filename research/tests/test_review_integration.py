"""Independent review (phase 2): integration of backtest output with validation + an independent
re-simulation oracle. SYNTHETIC data only: tests software, says nothing about markets."""
import math
import numpy as np
import pandas as pd
import pytest
from tradelab.contracts import TRADE_COLUMNS, Instrument
from tradelab.data.synthetic import make_synthetic_bars
from tradelab.data.loader import resample_bars
from tradelab.validation import montecarlo as M, stats as S
from tradelab.validation.splits import make_split, apply_split

import importlib.util, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("run_backtest_script", ROOT / "scripts" / "run_backtest.py")
rb = importlib.util.module_from_spec(spec); spec.loader.exec_module(rb)

MES = Instrument("MES", 5.0, 0.25)


@pytest.fixture(scope="module")
def bars5(tmp_path_factory):
    b = resample_bars(make_synthetic_bars(7, start="2024-01-02", days=90), "5min")
    p = tmp_path_factory.mktemp("bars") / "SYNTHETIC_MES_5m.csv"
    b.to_csv(p, index_label="ts_open")
    return b, p


def run(p, strategy, out, scenario="x1", extra=()):
    rc = rb.main(["--bars", str(p), "--strategy", strategy, "--out", str(out), "--scenario", scenario, *extra])
    assert rc == 0
    return pd.read_csv(out / "trades.csv", parse_dates=["signal_ts", "entry_ts", "exit_ts"])


@pytest.fixture(scope="module")
def h1(bars5, tmp_path_factory):
    return run(bars5[1], "sweep_reversal", tmp_path_factory.mktemp("h1"))


def test_trades_csv_matches_contract_and_feeds_validation(h1):
    assert list(h1.columns) == TRADE_COLUMNS and len(h1) >= 20
    for c in ("signal_ts", "entry_ts", "exit_ts"):
        assert str(h1[c].dt.tz) == "UTC"
    assert (h1.exit_ts >= h1.entry_ts).all() and (h1.signal_ts == h1.entry_ts).all()
    assert (h1.costs > 0).all() and np.allclose(h1.pnl_net, h1.pnl_gross - h1.costs)
    assert h1.trade_id.is_unique
    m = S.metrics(h1, 600.0, n_variants_tried=13, n_boot=300, seed=1)
    assert m["n_trades"] == len(h1) and math.isfinite(m["ev_usd"])
    assert 0 < m["exposure"] < 0.5 and m["max_dd_usd"] >= 0
    r = M.run_montecarlo(h1, M.MCConfig(seed=1, n_sims=50, instrument=MES, worse_stop_ticks=1, fill_miss_frac=0.2))
    assert r["n_trades_input"] == len(h1)
    assert "no independent market evidence" in r["caveat"]


def test_split_purge_on_engine_trades(h1):
    s = make_split(h1.entry_ts.min().normalize(), h1.exit_ts.max(), purge=pd.Timedelta(days=1))
    res = apply_split(h1, s)
    kept = len(res.train) + len(res.validation) + len(res.test)
    assert kept + res.n_dropped == len(h1) and kept >= 0.8 * len(h1)
    # intraday trades never straddle unless the boundary falls inside RTH
    assert res.dropped["straddle"] <= 1


def test_cost_scenario_x1_5_equals_cost_stress_reprice(bars5, h1, tmp_path):
    h15 = run(bars5[1], "sweep_reversal", tmp_path, scenario="x1_5")
    assert (h15.exit_ts == h1.exit_ts).all() and np.allclose(h15.pnl_gross, h1.pnl_gross)  # costs don't move fills
    cs = M.cost_stress(h1, MES, multipliers=(1.5,), extra_ticks=(0.0,), n_boot=20)
    assert cs.ev_usd.iloc[0] == pytest.approx(h15.pnl_net.mean())


def test_cost_arithmetic_independent(h1):
    """costs recomputed from the YAML numbers by hand: 2 sides commission + half spread + slippage (+1 tick extra on stops)."""
    tick = 1.25
    comm = 2 * (0.85 + 0.35)
    entry = (0.5 + 0.25) * tick
    exp = comm + entry + np.where(h1.exit_reason.isin(["stop", "ambiguous_stop"]), (0.5 + 0.25 + 1.0) * tick,
                                  np.where(h1.exit_reason == "target", 0.0, (0.5 + 0.25) * tick))
    assert np.allclose(h1.costs, exp * h1.qty)


def _oracle(bars, tr, target_r, limit_through=0.25, time_exit=11 * 60 + 30):
    """Independent re-simulation of one trade from bars + (side, entry_px, risk). Returns (exit_px, reason, exit_ts)."""
    side, ep = int(tr.side), tr.entry_px
    D = tr.risk_usd / tr.qty / 5.0
    stop = ep - side * D
    tgt = ep + side * target_r * D
    tgt = math.floor(tgt / 0.25 + 1e-9) * 0.25 if side == -1 else math.ceil(tgt / 0.25 - 1e-9) * 0.25
    i0 = bars.index.get_loc(tr.entry_ts)
    for j in range(i0, len(bars)):
        ts, row = bars.index[j], bars.iloc[j]
        et = ts.tz_convert("America/New_York")
        tod = et.hour * 60 + et.minute
        if j > i0:
            if (side == 1 and row.open <= stop) or (side == -1 and row.open >= stop):
                return row.open, "stop", ts
            if tod >= time_exit:
                return row.open, "time", ts
        s_hit = row.low <= stop if side == 1 else row.high >= stop
        t_hit = row.high >= tgt + limit_through if side == 1 else row.low <= tgt - limit_through
        if s_hit and t_hit:
            return stop, "ambiguous_stop", ts + pd.Timedelta(minutes=5)
        if s_hit:
            return stop, "stop", ts + pd.Timedelta(minutes=5)
        if t_hit:
            return tgt, "target", ts + pd.Timedelta(minutes=5)
    raise AssertionError("no exit")


@pytest.mark.parametrize("strategy,cfgid", [("sweep_reversal", "h1"), ("orb", "b1"), ("random_entry", "b0")])
def test_fill_oracle_matches_engine(bars5, tmp_path, strategy, cfgid):
    bars, p = bars5
    t = run(p, strategy, tmp_path, extra=["--reps", "1"] if strategy == "random_entry" else [])
    assert len(t) >= 5
    bad = []
    for tr in t.itertuples():
        px, reason, ts = _oracle(bars, tr, 2.0)
        if not (math.isclose(px, tr.exit_px) and reason == tr.exit_reason and ts == tr.exit_ts):
            bad.append((tr.trade_id, (px, reason, ts), (tr.exit_px, tr.exit_reason, tr.exit_ts)))
        # entry at the NEXT bar's open, strictly after signal bar close
        assert bars.loc[tr.entry_ts, "open"] == tr.entry_px
    assert not bad, bad[:3]


def test_b0_and_h1_use_identical_exit_and_cost_machinery(bars5, h1, tmp_path):
    b0 = run(bars5[1], "random_entry", tmp_path, extra=["--reps", "1"])
    for t in (h1, b0):
        assert set(t.exit_reason) <= {"stop", "target", "time", "eod", "ambiguous_stop"}
    # same cost formula: cost depends only on exit kind and qty
    key = lambda t: t.groupby(t.exit_reason.map(lambda r: "stop" if "stop" in r else r)).costs.agg(["min", "max"])
    for k, r in key(b0).iterrows():
        if k in key(h1).index:
            assert r["min"] == pytest.approx(key(h1).loc[k, "min"])
