import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tradelab.daily_trend import accounting as A
from tradelab.daily_trend import analysis as AN
from tradelab.daily_trend import forward as FW
from tradelab.daily_trend import signals as S

ROOT = Path(__file__).resolve().parents[1]
C0 = A.TrendCosts(slippage_bps=1.0, half_spread_usd=0.005, commission_usd=0.35, position_usd=10000.0)


def test_cost_arithmetic():
    # open 100: 1e-4 + 0.005/100 + 0.35/10000 = 1e-4 + 5e-5 + 3.5e-5 = 1.85e-4
    assert C0.frac(100.0) == pytest.approx(1.85e-4, abs=1e-15)
    assert C0.scaled(1.5).frac(100.0) == pytest.approx(1.5 * 1.85e-4, abs=1e-15)
    assert C0.scaled(2.0).frac(50.0) == pytest.approx(2 * (1e-4 + 1e-4 + 3.5e-5), abs=1e-15)


def test_four_accounting_cases_to_1e12():
    o, c, pc = 100.0, 103.0, 98.0
    cf = 1.85e-4
    f = lambda p, pp: float(A.daily_returns(o, c, pc, p, pp, C0))
    assert f(1, 1) == pytest.approx(103 / 98 - 1, abs=1e-12)                 # hold
    assert f(1, 0) == pytest.approx(103 / 100 - 1 - cf, abs=1e-12)           # buy at open
    assert f(0, 1) == pytest.approx(100 / 98 - 1 - cf, abs=1e-12)            # sell at open
    assert f(0, 0) == 0.0                                                     # cash
    # vector broadcast
    r = A.daily_returns(np.array([o, o]), np.array([c, c]), np.array([pc, pc]), np.array([1, 0]), np.array([0, 1]), C0)
    assert r[0] == pytest.approx(103 / 100 - 1 - cf, abs=1e-12) and r[1] == pytest.approx(100 / 98 - 1 - cf, abs=1e-12)


def test_total_return_and_drawdown_hand():
    r = np.array([0.10, -0.20, 0.05])
    assert A.total_return(r) == pytest.approx(1.1 * 0.8 * 1.05 - 1, abs=1e-12)
    # equity 1, 1.1, 0.88, 0.924 -> peak 1.1, trough .88 -> dd 0.2
    assert A.max_drawdown(r) == pytest.approx(0.2, abs=1e-12)
    assert A.ann_vol(np.array([0.01, -0.01])) == pytest.approx(np.std([0.01, -0.01], ddof=1) * np.sqrt(252), abs=1e-12)


def mk(closes, opens=None, start="2020-01-01"):
    idx = pd.bdate_range(start, periods=len(closes)).tz_localize("UTC") + pd.Timedelta(hours=14, minutes=30)
    idx.name = "ts_open"
    c = np.asarray(closes, dtype=float)
    o = c if opens is None else np.asarray(opens, dtype=float)
    return pd.DataFrame({"open": o, "high": np.maximum(o, c), "low": np.minimum(o, c), "close": c, "volume": 1e6}, index=idx)


def test_sma_warmup_and_rule():
    c = pd.Series(np.arange(1.0, 301.0))
    s, first = S.sma_signal(c, 200)
    assert first == 199 and s[:199].sum() == 0 and s[199] == 1           # rising: close > SMA
    s2, first2 = S.cross_signal(c, 50, 200)
    assert first2 == 199 and s2[:199].sum() == 0 and s2[199] == 1
    d, f3 = S.donchian_signal(c, 100, 50)
    assert f3 == 100 and d[:100].sum() == 0 and d[100] == 1              # close 101 > max of previous 100 closes (100)
    short = pd.Series(np.arange(1.0, 150.0))
    assert S.sma_signal(short, 200)[0].sum() == 0 and S.sma_signal(short, 200)[1] == len(short)


def test_sma_uses_inclusive_window_at_t():
    c = pd.Series([1.0] * 199 + [3.0])
    s, _ = S.sma_signal(c, 200)       # SMA = (199+3)/200 = 1.01 < 3
    assert s[199] == 1
    c2 = pd.Series([2.0] * 199 + [1.0])
    assert S.sma_signal(c2, 200)[0][199] == 0


def test_donchian_state_persistence():
    # 100 flat at 10, break out to 11 (enter), drift between 10.5.. stays long, then drop below min of last 50 -> exit, then stay cash
    c = [10.0] * 100 + [11.0] + [10.8] * 10 + [10.9] * 10          # idx 100 entry
    c += [9.0]                                                      # below min(prev 50) -> exit
    c += [9.5] * 5                                                   # no entry, no exit -> stay cash
    s, _ = S.donchian_signal(pd.Series(c), 100, 50)
    assert s[100] == 1 and all(s[100:121]) and s[121] == 0 and s[122:].sum() == 0
    # persistence: states between triggers carry over (s[105] is long though close 10.8 < high 11)
    assert s[105] == 1


def test_no_lookahead_truncate_and_garble():
    rng = np.random.default_rng(1)
    c = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 600))))
    for kind in ({"kind": "sma200"}, {"kind": "cross", "fast": 50, "slow": 200}, {"kind": "donchian", "entry": 100, "exit": 50}):
        full, _ = S.make_signal(kind, c)
        for cut in (250, 400, 555):
            tr, _ = S.make_signal(kind, c.iloc[:cut + 1])
            g = c.copy()
            g.iloc[cut + 1:] = rng.uniform(1, 1000, len(c) - cut - 1)
            gs, _ = S.make_signal(kind, g)
            assert np.array_equal(full[:cut + 1], tr) and np.array_equal(full[:cut + 1], gs[:cut + 1])


def test_position_timing_signal_at_t_traded_at_open_t_plus_1():
    bars = mk(list(np.linspace(100, 160, 260)))
    f = AN.symbol_frame(bars, {"kind": "sma200"})
    first = 199
    assert f["s"].iloc[first] == 1 and f["p"].iloc[first] == 0 and f["p"].iloc[first + 1] == 1
    assert not f["elig"].iloc[first] and f["elig"].iloc[first + 1]
    assert f["pp"].iloc[first + 1] == 0 and f["pp"].iloc[first + 2] == 1


def test_run_length_shuffle_preserves_structure():
    p = np.array([1] * 5 + [0] * 3 + [1] * 2 + [0] * 7 + [1] * 4 + [0] * 1)
    first, on, off = AN.run_structure(p)
    rng = np.random.default_rng(0)
    seen = set()
    for _ in range(50):
        q = AN.shuffle_runs(first, on, off, rng)
        assert len(q) == len(p) and q.sum() == p.sum() and q[0] == p[0]
        assert (np.diff(q) != 0).sum() == (np.diff(p) != 0).sum()
        seen.add(tuple(q))
    assert len(seen) > 1
    # starting in cash
    p2 = np.array([0, 0, 1, 1, 1, 0])
    f2, on2, off2 = AN.run_structure(p2)
    q = AN.shuffle_runs(f2, on2, off2, rng)
    assert q[0] == 0 and q.sum() == 3 and (np.diff(q) != 0).sum() == 2


def _win(seed=0, n=300):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, n + 1)))
    o = c[:-1] * (1 + rng.normal(0, 0.002, n))
    p = (rng.random(n) < 0.5).astype(int)
    pp = np.concatenate([[0], p[:-1]])
    return {"o": o, "c": c[1:], "pc": c[:-1], "p": p, "pp": pp}


def test_null_deterministic_by_seed():
    w = {"X": _win(1), "Y": _win(2)}
    a = AN.random_timing_null(w, C0, 5, 200, chunk=70)
    b = AN.random_timing_null(w, C0, 5, 200, chunk=70)
    c = AN.random_timing_null(w, C0, 6, 200, chunk=70)
    assert np.array_equal(a["pooled"]["total"], b["pooled"]["total"]) and not np.array_equal(a["pooled"]["total"], c["pooled"]["total"])
    assert len(a["X"]["mdd"]) == 200


def test_monte_carlo_and_bootstrap_deterministic():
    r = np.random.default_rng(0).normal(0, 0.01, 200)
    assert AN.monte_carlo(r, 10, 300, 7) == AN.monte_carlo(r, 10, 300, 7)
    assert AN.monte_carlo(r, 10, 300, 7) != AN.monte_carlo(r, 10, 300, 8)
    assert AN.bootstrap_mean_diff(r, 10, 300, 3) == AN.bootstrap_mean_diff(r, 10, 300, 3)


def test_verdict_rule():
    bad = {"total": 0.1, "total_bh": 0.2, "dd_reduction": 0.1}
    good_dd = {"total": 0.1, "total_bh": 0.2, "dd_reduction": 0.4}
    better = {"total": 0.3, "total_bh": 0.2, "dd_reduction": 0.1}
    assert AN.verdict({"train": bad, "validation": bad})[0] == "REJECTED"
    assert AN.verdict({"train": bad, "validation": good_dd})[0] == "INCONCLUSIVE"
    assert AN.verdict({"train": bad, "validation": better})[0] == "INCONCLUSIVE"
    assert AN.verdict({"train": good_dd, "validation": good_dd})[0] != "SUITABLE"


def test_window_bh_buys_once_with_cost():
    bars = mk([100.0] * 5, opens=[100.0] * 5)
    f = {"X": AN.symbol_frame(bars, {"kind": "sma200"})}
    f["X"]["elig"] = True
    dates = f["X"].index[1:]
    w = AN.window(f, dates, C0)
    assert w["X"]["r_bh"][0] == pytest.approx(-1.85e-4, abs=1e-12) and (w["X"]["r_bh"][1:] == 0).all()


def test_nothing_after_sealed_start_is_touched():
    bars = mk(list(np.linspace(100, 200, 400)))
    sealed = bars.index[300]
    garbled = bars.copy()
    garbled.iloc[300:, :4] = 12345.0
    t1, t2 = AN.truncate_before(bars, sealed), AN.truncate_before(garbled, sealed)
    assert len(t1) == 300 and t1.index.max() < sealed
    pd.testing.assert_frame_equal(t1, t2)
    for v in ({"kind": "sma200"}, {"kind": "donchian", "entry": 100, "exit": 50}):
        f1, f2 = AN.symbol_frame(t1, v), AN.symbol_frame(t2, v)
        pd.testing.assert_frame_equal(f1, f2)
        d = AN.common_dates({"X": f1}, bars.index[0], sealed)
        assert d.max() < sealed
        w1, w2 = AN.window({"X": f1}, d, C0), AN.window({"X": f2}, d, C0)
        assert np.array_equal(w1["X"]["r"], w2["X"]["r"])


def test_forward_only_new_days_and_overlap_assert():
    a = mk(list(np.linspace(100, 150, 260)))
    b_full = mk(list(np.linspace(100, 150, 260)) + [151, 152, 153, 154, 155])
    new = b_full.iloc[-5:]
    b = pd.concat([a.iloc[5:], new])
    fw = FW.forward_observation({"X": a}, {"X": b}, {"v": {"kind": "sma200"}}, C0)
    df = fw["v"]["X"]
    assert len(df) == 5 and list(df.columns) == ["pos", "ret", "ret_bh"]
    assert (df["pos"] == 1).all()
    bad = b.copy()
    bad.iloc[10, bad.columns.get_loc("close")] += 1
    with pytest.raises(AssertionError):
        FW.forward_observation({"X": a}, {"X": bad}, {"v": {"kind": "sma200"}}, C0)


def test_run_trend_refuses_test_segment():
    spec = importlib.util.spec_from_file_location("run_trend", ROOT / "scripts" / "run_trend.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["run_trend"] = m
    spec.loader.exec_module(m)
    assert m.main(["--segment", "test"]) == 2
