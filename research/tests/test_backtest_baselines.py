import pytest
from tradelab.backtest.baselines import (OpeningRangeBreakout, OrbParams, RandomEntry, RandomParams)
from tradelab.backtest.engine import run_backtest
from test_backtest_helpers import ECFG, MES, history, to_utc, to_df, rth_bars, prev_day_rows, on_bars

D, P = "2026-06-02", "2026-06-01"
OR = {"09:40": (5000, 5004.0, 4999, 5000), "09:50": (5000, 5001, 4996.0, 5000)}   # OR hi 5004, lo 4996


def orb(over, **kw):
    bars = history(D, P, {**OR, **over})
    s = OpeningRangeBreakout(MES, OrbParams(**kw))
    return run_backtest(bars, s, ECFG)


def test_orb_long_breakout_hand_computed():
    res = orb({"10:15": (5002, 5005.5, 5001, 5005.0), "10:20": (5005, 5006, 5004, 5005.5)})
    s = res.signals[0]
    assert s.side == 1 and s.stop_px == 4995.75 and s.signal_ts == to_utc(D, "10:20")
    t = res.trades.iloc[0]
    assert t.entry_px == 5005.0 and t.risk_usd == pytest.approx(9.25 * 5)
    # target = 5005 + 2*9.25 = 5023.5 not reached; flat bars -> time exit 11:30 at 5000 open
    assert t.exit_reason == "time" and t.pnl_gross == pytest.approx(-25.0)


def test_orb_short_and_no_signal_inside_range_or_before_1000():
    res = orb({"10:15": (4998, 4999, 4994.0, 4995.0)})
    assert res.signals[0].side == -1 and res.signals[0].stop_px == 5004.25
    assert orb({"09:55": (5000, 5010, 4999, 5009.0)}).signals == []      # OR bar, not a signal
    assert orb({"10:15": (5000, 5003, 4997, 5002.0)}).signals == []      # inside range


def test_orb_risk_filter_and_incomplete_or():
    wide = {"09:45": (5000, 5012.0, 4999, 5000), "10:15": (5002, 5013.5, 5001, 5013.0)}
    res = orb(wide)       # stop = 4995.75, D_sig = 17.25 > 12
    assert res.signals == [] and res.diagnostics["strategy_rejected_risk_filter"] == 1
    bars = history(D, P, {**OR, "10:15": (5002, 5005.5, 5001, 5005.0)}, today_skip=("09:45",))
    assert run_backtest(bars, OpeningRangeBreakout(MES), ECFG).signals == []


def test_orb_one_signal_per_day():
    res = orb({"10:15": (5002, 5005.5, 5001, 5005.0), "10:30": (4998, 4999, 4994.0, 4995.0)})
    assert len(res.signals) == 1


def b0(seed=7, rep=0, **kw):
    return RandomEntry(MES, RandomParams(seed=seed, rep=rep, **kw))


def multi_day_bars():
    rows = prev_day_rows("2026-06-01")
    for d in ("2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05"):
        rows += on_bars(d) + rth_bars(d)
    return to_df(rows)


def test_b0_deterministic_and_rep_varies():
    bars = multi_day_bars()
    a = run_backtest(bars, b0(), ECFG).signals
    b = run_backtest(bars, b0(), ECFG).signals
    assert a == b and len(a) == 4
    allsig = [tuple((s.signal_ts, s.side) for s in run_backtest(bars, b0(rep=r), ECFG).signals) for r in range(12)]
    assert len(set(allsig)) > 6
    assert {s[1] for sig in allsig for s in sig} == {1, -1}


def test_b0_inside_window_and_distance_sampling():
    bars = multi_day_bars()
    for r in range(15):
        for s in run_backtest(bars, b0(rep=r, stop_distances_pts=(3.0, 5.0)), ECFG).signals:
            et = s.signal_ts.tz_convert("America/New_York")
            assert (9, 40) <= (et.hour, et.minute) <= (11, 0)       # signal bar open 09:35..10:55
            assert s.meta["dist"] in (3.0, 5.0)
            assert abs(s.stop_px - s.ref_px) == pytest.approx(s.meta["dist"])


def test_b0_same_eligible_days_as_h1():
    # first day has no previous session -> no B0 signal, mirroring H1 eligibility
    rows = on_bars("2026-06-02") + rth_bars("2026-06-02")
    assert run_backtest(to_df(rows), b0(), ECFG).signals == []


def test_b0_matched_days_and_tods_and_distances():
    bars = multi_day_bars()
    p = dict(match_days=("2026-06-03", "2026-06-05"), match_tods=(10 * 60,), stop_distances_pts=(4.0,))
    for r in range(5):
        sigs = run_backtest(bars, b0(rep=r, **p), ECFG).signals
        assert [s.signal_ts.tz_convert("America/New_York").date().isoformat() for s in sigs] == ["2026-06-03", "2026-06-05"]
        assert all(s.signal_ts == to_utc(s.signal_ts.tz_convert("America/New_York").date(), "10:05") for s in sigs)
        assert all(abs(s.stop_px - s.ref_px) == 4.0 for s in sigs)
    assert run_backtest(bars, b0(match_days=()), ECFG).signals == []
