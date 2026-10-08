import numpy as np
import pandas as pd
import pytest

from tradelab.contracts import TRADE_COLUMNS
from tradelab.daily import baselines as B
from tradelab.daily import engine as E
from tradelab.daily import signals as S
from tradelab.validation import montecarlo as MC
from tradelab.validation import stats as ST
from tradelab.contracts import Instrument

COSTS = E.DailyCosts()


def mkbars(rows, start="2024-01-02", hour=14, minute=30):
    """rows: list of (o,h,l,c); consecutive business days; ts_open 14:30 UTC (09:30 EST)."""
    idx = pd.bdate_range(start, periods=len(rows)).tz_localize("UTC") + pd.Timedelta(hours=hour, minutes=minute)
    idx.name = "ts_open"
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 1e6
    return df


FLAT = [(100, 101, 99, 100)] * 16  # TR = 2 every day after the first


def bullish_case():
    # day 16: sweeps prior low 99 (low 98), closes back inside (100); high 100.5 <= 101 -> bullish only
    # day 17: open 100 close 101.5 (the traded day)
    return mkbars(FLAT + [(100, 100.5, 98, 100), (100, 102, 99.5, 101.5), (101.5, 102, 101, 101.5)])


def test_hand_computed_long_trade_to_the_cent():
    bars = bullish_case()
    t = E.run_variant({"SPY": bars}, "D3_V1_c2_both", "both", COSTS)
    assert len(t) == 1
    r = t.iloc[0]
    assert list(t.columns) == TRADE_COLUMNS + ["symbol"]
    assert r.side == 1 and r.symbol == "SPY" and r.exit_reason == "eod" and r.bars_held == 1
    assert r.strategy == "D3_V1_c2_both@universe=SPY"
    assert r.entry_px == 100 and r.exit_px == 101.5
    assert r.qty == pytest.approx(100.0)
    assert r.pnl_gross == pytest.approx(150.0)
    # costs: commission 2*0.35 + spread 2*0.005*100 + slip 10000*1e-4 + 10150*0.5e-4
    assert r.costs == pytest.approx(0.70 + 1.00 + 1.00 + 0.5075, abs=1e-9)
    assert r.costs == pytest.approx(3.2075, abs=1e-9)
    assert r.pnl_net == pytest.approx(146.7925, abs=1e-9)
    atr = (13 * 2 + 2.5) / 14
    assert r.risk_usd == pytest.approx(100 * atr)
    assert r.r_multiple == pytest.approx(146.7925 / (100 * atr))
    # timestamps: signal at day-16 close 16:00 NY (21:00 UTC), entry = day-17 open, exit = day-17 close
    assert r.signal_ts == pd.Timestamp("2024-01-24 21:00", tz="UTC")
    assert r.entry_ts == pd.Timestamp("2024-01-25 14:30", tz="UTC")
    assert r.exit_ts == pd.Timestamp("2024-01-25 21:00", tz="UTC")


def test_hand_computed_short_trade_and_cost_multiplier():
    # day 16: high 102 > 101 sweep, close 100 inside (99,101) -> bearish. day 17 open 100 close 98.
    bars = mkbars(FLAT + [(100, 102, 99.5, 100), (100, 100.5, 97.5, 98), (98, 99, 97, 98)])
    t = E.run_variant({"QQQ": bars}, "D3_V1_c2_both", "both", COSTS)
    assert len(t) == 1 and t.iloc[0].side == -1
    r = t.iloc[0]
    assert r.pnl_gross == pytest.approx(200.0)               # short 100 sh, 100 -> 98
    assert r.costs == pytest.approx(0.70 + 1.00 + 1.00 + 9800 * 0.5e-4)  # exit notional 100*98
    t2 = E.run_variant({"QQQ": bars}, "x", "both", COSTS.scaled(2.0))
    assert t2.iloc[0].costs == pytest.approx(2 * r.costs)
    assert t2.iloc[0].pnl_net == pytest.approx(r.pnl_gross - 2 * r.costs)


def test_variants_filter_sides():
    bars = bullish_case()
    assert len(E.run_variant({"SPY": bars}, "v", "long", COSTS)) == 1
    assert len(E.run_variant({"SPY": bars}, "v", "short", COSTS)) == 0


def test_envelope_repair_close_above_high():
    # close 103 reported above high 101 -> H* = 103, L* unchanged
    bars = mkbars([(100, 101, 99, 103), (100, 101, 99, 99.5)])
    env = S.repair_envelope(bars)
    assert env["hstar"].iloc[0] == 103 and env["lstar"].iloc[0] == 99
    # close below low
    bars = mkbars([(100, 101, 99, 98)])
    assert S.repair_envelope(bars)["lstar"].iloc[0] == 98


def test_envelope_repair_changes_signal():
    # prior day: high 101 but close 103 (repaired H* = 103). Day t: high 102, close 100.5.
    # Unrepaired it would be bearish (102 > 101, 99 < 100.5 < 101); repaired H*_{t-1}=103 > 102 -> no sweep.
    rows = FLAT + [(100, 101, 99, 103), (101, 102, 100, 100.5)]
    assert S.raw_c2(S.repair_envelope(mkbars(rows))).iloc[-1] == 0
    # control: consistent prior bar (close 100.9 inside high 101) -> bearish C2
    rows2 = FLAT + [(100, 101, 99, 100.9), (101, 102, 100, 100.5)]
    assert S.raw_c2(S.repair_envelope(mkbars(rows2))).iloc[-1] == -1


def test_outside_day_gives_no_signal():
    # day 16 sweeps both prior high and prior low, closes inside -> both conditions -> no signal
    bars = mkbars(FLAT + [(100, 102, 98, 100), (100, 101, 99, 100), (100, 101, 99, 100)])
    tab = S.signal_table(bars)
    assert tab["raw_signal"].iloc[16] == 0
    assert (tab["signal"] == 0).all()


def test_close_outside_prior_range_no_signal():
    # sweep of prior high but close above prior high (breakout, not C2)
    bars = mkbars(FLAT + [(100, 103, 100, 102.5), (102, 103, 101, 102), (102, 103, 101, 102)])
    assert (S.signal_table(bars)["raw_signal"] == 0).all()


def test_gap_over_4_days_no_signal():
    rows = FLAT + [(100, 100.5, 98, 100), (100, 102, 99.5, 101.5), (101.5, 102, 101, 101.5)]
    bars = mkbars(rows)
    # shift day 17 and 18 forward 7 calendar days -> t+1 is >4 days after t
    idx = list(bars.index)
    idx[17] = idx[17] + pd.Timedelta(days=7)
    idx[18] = idx[18] + pd.Timedelta(days=7)
    bars.index = pd.DatetimeIndex(idx, name="ts_open")
    assert len(E.run_variant({"SPY": bars}, "v", "both", COSTS)) == 0
    # 4-day gap (Fri -> Tue over a holiday-style gap) is allowed: day 16 is Thu 2024-01-25? build explicitly
    bars2 = bullish_case()
    assert len(E.run_variant({"SPY": bars2}, "v", "both", COSTS)) == 1
    idx = list(bars2.index)
    idx[17] = idx[16] + pd.Timedelta(days=4)
    idx[18] = idx[17] + pd.Timedelta(days=1)
    bars2.index = pd.DatetimeIndex(idx, name="ts_open")
    assert len(E.run_variant({"SPY": bars2}, "v", "both", COSTS)) == 1
    idx[17] = idx[16] + pd.Timedelta(days=5)
    idx[18] = idx[17] + pd.Timedelta(days=1)
    bars2.index = pd.DatetimeIndex(idx, name="ts_open")
    assert len(E.run_variant({"SPY": bars2}, "v", "both", COSTS)) == 0


def test_last_bar_and_warmup_have_no_trade():
    # signal pattern in the last bar has no t+1 -> no trade; patterns before ATR warm-up are dropped
    bars = mkbars(FLAT + [(100, 100.5, 98, 100)])
    assert len(E.run_variant({"SPY": bars}, "v", "both", COSTS)) == 0
    early = mkbars([(100, 101, 99, 100), (100, 100.5, 98, 100), (100, 102, 99, 101), (101, 102, 100, 101)])
    assert S.raw_c2(S.repair_envelope(early)).iloc[1] == 1
    assert len(E.run_variant({"SPY": early}, "v", "both", COSTS)) == 0


def _random_bars(n=120, seed=3):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    o = c * (1 + rng.normal(0, 0.003, n))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.004, n)))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.004, n)))
    return mkbars(list(zip(o, h, l, c)))


def test_no_lookahead_truncate_and_garble():
    bars = _random_bars()
    full = S.signal_table(bars)
    assert (full["raw_signal"] != 0).sum() > 5
    for cut in (40, 77, 100):
        trunc = S.signal_table(bars.iloc[:cut + 1])
        garbled = bars.copy()
        rng = np.random.default_rng(0)
        for c in ("open", "high", "low", "close"):
            garbled.iloc[cut + 1:, garbled.columns.get_loc(c)] = rng.uniform(1, 1000, len(bars) - cut - 1)
        g = S.signal_table(garbled)
        cols = ["hstar", "lstar", "atr", "raw_signal"]
        pd.testing.assert_frame_equal(full.iloc[:cut + 1][cols], trunc[cols])
        pd.testing.assert_frame_equal(full.iloc[:cut + 1][cols], g.iloc[:cut + 1][cols])
        # eligibility depends on t+1's DATE only: with dates kept, garbled prices leave signals at <= cut unchanged
        pd.testing.assert_series_equal(full["signal"].iloc[:cut + 1], g["signal"].iloc[:cut + 1])


def test_same_day_correlated_trades_across_symbols():
    bars = bullish_case()
    t = E.run_variant({"SPY": bars, "QQQ": bars.copy(), "IWM": bars.copy()}, "D3_V1_c2_both", "both", COSTS)
    assert len(t) == 3 and t["trade_id"].is_unique
    assert t["entry_ts"].nunique() == 1 and set(t["symbol"]) == {"SPY", "QQQ", "IWM"}
    m = ST.metrics(t, equity_start=10000.0, n_boot=200, seed=1)
    assert m["n_trades"] == 3 and m["n_days"] == 1          # one cluster day: pooled by date
    assert m["ev_usd"] == pytest.approx(146.7925)


def test_validation_code_accepts_float_qty():
    t = E.run_variant({"SPY": _random_bars(400, 5), "QQQ": _random_bars(400, 6)}, "v", "both", COSTS)
    assert len(t) > 10 and (t["qty"] % 1 != 0).any()
    m = ST.metrics(t, equity_start=10000.0, n_boot=200, seed=1)
    assert np.isfinite(m["ev_usd"]) and np.isfinite(m["ev_r"]) and m["boot_ci_ev_r"] is not None
    inst = Instrument("ETF", 1.0, 0.01)
    cs = MC.cost_stress(t, inst, extra_ticks=(0.0,), n_boot=100, seed=1)
    assert len(cs) == 3 and cs.iloc[0]["ev_usd"] == pytest.approx(t["pnl_net"].mean())
    assert cs.iloc[2]["ev_usd"] == pytest.approx((t["pnl_gross"] - 2 * t["costs"]).mean())
    r = MC.run_montecarlo(t, MC.MCConfig(seed=7, n_sims=50, account_size=10000.0))
    assert "distributions" in r


def test_b_d0_and_null_determinism_and_counts():
    bars = _random_bars(400, 5)
    c = E.candidates(bars, "SPY")
    d0 = B.b_d0_trades(c, COSTS)
    assert len(d0) == len(c) and (d0["side"] == 1).all()
    n1 = B.matched_permutation_null({"SPY": c}, {"SPY": (7, 5)}, COSTS, seed=11, n_draws=300)
    n2 = B.matched_permutation_null({"SPY": c}, {"SPY": (7, 5)}, COSTS, seed=11, n_draws=300)
    n3 = B.matched_permutation_null({"SPY": c}, {"SPY": (7, 5)}, COSTS, seed=12, n_draws=300)
    assert n1["n"] == 12 and np.array_equal(n1["usd"], n2["usd"]) and not np.array_equal(n1["usd"], n3["usd"])
    # exhaustive check: drawing ALL days long has no randomness and equals B_D0 mean
    nall = B.matched_permutation_null({"SPY": c}, {"SPY": (len(c), 0)}, COSTS, seed=1, n_draws=20)
    assert np.allclose(nall["usd"], d0["pnl_net"].mean()) and np.allclose(nall["r"], d0["r_multiple"].mean())
    # all short: negated gross, same costs
    ns = B.matched_permutation_null({"SPY": c}, {"SPY": (0, len(c))}, COSTS, seed=1, n_draws=5)
    assert np.allclose(ns["usd"], (-d0["pnl_gross"] - d0["costs"]).mean())
    with pytest.raises(ValueError):
        B.matched_permutation_null({"SPY": c}, {"SPY": (len(c), 1)}, COSTS, n_draws=5)


def test_null_summary_pvalue_direction():
    null = {"n": 5, "seed": 1, "usd": np.arange(100.0), "r": np.arange(100.0), "bps": np.arange(100.0)}
    s = B.null_summary(null, {"usd": 99.5, "r": 99.5, "bps": 99.5})
    assert s["usd"]["p_one_sided"] == pytest.approx(1 / 101) and s["usd"]["percentile"] == 1.0
    s = B.null_summary(null, {"usd": -1, "r": -1, "bps": -1})
    assert s["usd"]["p_one_sided"] == 1.0 and s["usd"]["percentile"] == 0.0


def test_run_is_deterministic():
    bars = {"SPY": _random_bars(300, 1), "IWM": _random_bars(300, 2)}
    a = E.run_variant(bars, "v", "both", COSTS)
    b = E.run_variant(bars, "v", "both", COSTS)
    pd.testing.assert_frame_equal(a, b)
