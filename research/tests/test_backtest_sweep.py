import datetime as dt
import pandas as pd
import pytest
from tradelab.backtest.engine import run_backtest
from tradelab.backtest.sessions import is_half_day
from tradelab.backtest.sweep_reversal import SweepParams, SweepReversal
from test_backtest_helpers import (ECFG, MES, SHORT_SWEEP, LONG_SWEEP, history, to_utc, to_df,
                                   prev_day_rows, on_bars, rth_bars)

D, P = "2026-06-02", "2026-06-01"


def strat(**kw):
    return SweepReversal(MES, SweepParams(**kw))


def go(bars, **kw):
    return run_backtest(bars, strat(**kw), ECFG)


def test_short_sweep_signal_and_end_to_end_hand_computed():
    over = {"10:30": SHORT_SWEEP, "10:35": (5003.0, 5003.5, 4996.25, 4997.0)}
    res = go(history(D, P, over))
    assert len(res.signals) == 1
    s = res.signals[0]
    assert s.side == -1 and s.stop_px == 5006.25 and s.signal_ts == to_utc(D, "10:35")
    t = res.trades.iloc[0]
    # fill at 10:35 open 5003.0: D = 3.25, target = 5003 - 6.5 = 4996.5, bar low 4996.25 trades through
    assert (t.entry_px, t.exit_px, t.exit_reason) == (5003.0, 4996.5, "target")
    assert t.pnl_gross == pytest.approx(32.5) and t.costs == pytest.approx(1.95)
    assert t.pnl_net == pytest.approx(30.55) and t.risk_usd == pytest.approx(16.25)
    assert t.r_multiple == pytest.approx(30.55 / 16.25)


def test_long_sweep_mirrored():
    res = go(history(D, P, {"10:30": LONG_SWEEP}))
    s = res.signals[0]
    assert s.side == 1 and s.stop_px == 4993.75


@pytest.mark.parametrize("td,pd_,utc_off", [
    ("2026-03-06", "2026-03-05", 5),   # EST
    ("2026-03-09", "2026-03-06", 4),   # Monday after DST start; overnight spans the switch
    ("2026-06-02", "2026-06-01", 4),   # EDT
    ("2026-11-02", "2026-10-30", 5),   # Monday after DST end
    ("2026-11-03", "2026-11-02", 5),
])
def test_window_is_et_across_dst(td, pd_, utc_off):
    ok = go(history(td, pd_, {"10:30": SHORT_SWEEP}))
    assert len(ok.signals) == 1
    assert ok.signals[0].signal_ts == pd.Timestamp(f"{td} {10 + utc_off}:35", tz="UTC")  # independent UTC arithmetic
    # first RTH bar (09:30 ET = 09:30+offset UTC) is never a signal bar
    first = go(history(td, pd_, {"09:30": SHORT_SWEEP}))
    assert first.signals == []
    # 09:35 ET is the first eligible bar
    assert len(go(history(td, pd_, {"09:35": SHORT_SWEEP})).signals) == 1
    # window end: 10:55 bar allowed (fills 11:00), 11:00 bar not
    assert len(go(history(td, pd_, {"10:55": SHORT_SWEEP})).signals) == 1
    assert go(history(td, pd_, {"11:00": SHORT_SWEEP})).signals == []


def test_same_utc_clock_differs_by_dst():
    # a sweep stamped 14:30 UTC is 10:30 ET in summer (signal) but 09:30 ET in winter (first bar, none)
    summer = history("2026-06-02", "2026-06-01")
    winter = history("2026-01-13", "2026-01-12")
    for df, d, expect in ((summer, "2026-06-02", 1), (winter, "2026-01-13", 0)):
        df = df.copy()
        ts = pd.Timestamp(f"{d} 14:30", tz="UTC")
        df.loc[ts, ["open", "high", "low", "close"]] = SHORT_SWEEP
        assert len(go(df).signals) == expect


def test_overnight_pre_open_bar_never_signals():
    over = {"2026-06-02T09:25": SHORT_SWEEP}
    rows = prev_day_rows(P) + on_bars(D, over=over) + rth_bars(D)
    assert go(to_df(rows)).signals == []


def test_level_must_be_exceeded_by_one_tick_and_rejected_by_close():
    assert go(history(D, P, {"10:30": (5003, 5005.0, 5002, 5003.75)})).signals == []        # only touches 5005
    assert go(history(D, P, {"10:30": (5003, 5006.0, 5002, 5005.5)})).signals == []        # close not back below
    assert len(go(history(D, P, {"10:30": (5003, 5005.25, 5002, 5002.0)})).signals) == 1   # exactly 1 tick over


def test_risk_filter_consumes_day():
    small = (5003.0, 5005.25, 5002.5, 5004.0)   # stop 5005.5, D_sig 1.5 < 2
    res = go(history(D, P, {"10:30": small, "10:40": SHORT_SWEEP}))
    assert res.signals == [] and res.diagnostics["strategy_rejected_risk_filter"] == 1
    big = (5003.0, 5017.0, 5002.5, 5004.0)      # stop 5017.25, D 13.25 > 12
    assert go(history(D, P, {"10:30": big})).signals == []


def test_both_sides_same_bar_no_signal_and_day_not_consumed():
    both = (5000.0, 5006.0, 4994.0, 5000.0)
    res = go(history(D, P, {"10:30": both, "10:40": SHORT_SWEEP}))
    assert len(res.signals) == 1 and res.signals[0].signal_ts == to_utc(D, "10:45")


def test_only_first_signal_per_day():
    res = go(history(D, P, {"10:00": SHORT_SWEEP, "10:30": LONG_SWEEP}))
    assert len(res.signals) == 1 and res.signals[0].side == -1


def test_level_set_selection():
    prev_only = (5008.0, 5013.0, 5007.0, 5009.5)   # sweeps prev high 5010 (close below) but closes above ON high 5005
    h = history(D, P, {"10:30": prev_only})
    assert len(go(h, levels="prev_rth").signals) == 1
    assert go(h, levels="overnight").signals == []
    assert len(go(h, levels="both").signals) == 1


def test_todays_own_extremes_are_not_levels():
    # a spike at 10:00 (closing above levels, no signal) must not make 10:30 a sweep of "today's high"
    over = {"10:00": (5006.0, 5008.0, 5005.5, 5007.0), "10:30": (5007.0, 5008.5, 5006.0, 5007.5)}
    assert go(history(D, P, over)).signals == []


def test_half_day_rule_and_skip():
    assert is_half_day(dt.date(2026, 12, 24)) and is_half_day(dt.date(2026, 11, 27))
    assert is_half_day(dt.date(2025, 7, 3)) and not is_half_day(dt.date(2026, 7, 3))
    assert not is_half_day(dt.date(2026, 11, 20)) and not is_half_day(dt.date(2026, 12, 25))
    res = go(history("2026-12-24", "2026-12-23", {"10:30": SHORT_SWEEP}))
    assert res.signals == [] and res.diagnostics["strategy_skip_half_day"] > 0


def test_roll_day_and_day_after_skipped():
    # contracts: Jun 1 A, Jun 2 B (roll), Jun 3 B (day after), Jun 4 B (normal)
    over = {"10:30": SHORT_SWEEP}
    rows = prev_day_rows("2026-06-01", "MESM6")
    for d, ctr in (("2026-06-02", "MESU6"), ("2026-06-03", "MESU6"), ("2026-06-04", "MESU6")):
        rows += on_bars(d, ctr) + rth_bars(d, ctr, over=over)
    res = go(to_df(rows))
    assert [s.signal_ts for s in res.signals] == [to_utc("2026-06-04", "10:35")]
    assert res.diagnostics["strategy_skip_roll_day"] > 0 and res.diagnostics["strategy_skip_day_after_roll"] > 0


def test_prev_incomplete_day_skipped_but_overnight_only_variant_trades():
    h = history(D, P, {"10:30": SHORT_SWEEP}, prev_skip=("14:00",))
    assert go(h, levels="both").signals == []
    assert len(go(h, levels="overnight").signals) == 1


def test_data_gap_today_before_decision_skips():
    h = history(D, P, {"10:30": SHORT_SWEEP}, today_skip=("09:50",))
    assert go(h).signals == []


def test_gap_after_decision_does_not_change_decision():
    a = go(history(D, P, {"10:30": SHORT_SWEEP}))
    b = go(history(D, P, {"10:30": SHORT_SWEEP}, today_skip=("12:00",)))
    assert a.signals == b.signals


def test_overnight_gap_invalidates_overnight_only():
    skip = tuple(f"{D}T{h:02d}:{m:02d}" for h in (3, 4) for m in range(0, 60, 5))   # 03:00-04:55 missing
    h = history(D, P, {"10:30": SHORT_SWEEP}, on_skip=skip)
    assert go(h, levels="both").signals == []
    assert go(h, levels="overnight").signals == []


def test_weekend_overnight_starts_sunday_1800():
    # Monday 2026-06-08: ON must start Sunday 18:00 ET; prev session Friday 06-05
    res = go(history("2026-06-08", "2026-06-05", {"10:30": SHORT_SWEEP}))
    assert len(res.signals) == 1


def test_missing_contract_label_raises():
    h = history(D, P, {"10:30": SHORT_SWEEP}).drop(columns="contract")
    with pytest.raises(ValueError):
        go(h)


def test_params_validation():
    with pytest.raises(ValueError):
        strat(levels="x")
    with pytest.raises(ValueError):
        strat(first_signal_bar="09:30")
