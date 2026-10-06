import pandas as pd
import pytest
from dataclasses import replace
from tradelab.contracts import TRADE_COLUMNS
from tradelab.backtest.engine import run_backtest
from test_backtest_helpers import ECFG, COST, ScriptedStrategy, one_day, to_utc, rth_bars, to_df

D = "2026-06-02"   # Tuesday, EDT
D2 = "2026-06-03"


def run(bars, script, cfg=ECFG):
    return run_backtest(bars, ScriptedStrategy(script), cfg)


def sig_at(hm, date=D, **kw):
    return {to_utc(date, hm): kw}


def one(res):
    assert len(res.trades) == 1, res.diagnostics
    return res.trades.iloc[0]


def test_short_target_hand_computed():
    over = {"10:05": (5005, 5006, 5001, 5002), "10:10": (5002, 5003, 4994.75, 4995)}
    t = one(run(one_day(D, over), sig_at("10:00", side=-1, stop_px=5010.0)))
    assert (t.side, t.entry_px, t.exit_px, t.exit_reason) == (-1, 5005.0, 4995.0, "target")  # target = 5005-2*5
    assert t.pnl_gross == pytest.approx(50.0)
    assert t.costs == pytest.approx(1.95)             # 0.70 commission + 1.25 entry; limit exit free
    assert t.pnl_net == pytest.approx(48.05)
    assert t.risk_usd == pytest.approx(25.0)
    assert t.r_multiple == pytest.approx(48.05 / 25.0)
    assert t.bars_held == 2
    assert t.signal_ts == to_utc(D, "10:05") and t.entry_ts == to_utc(D, "10:05")
    assert t.exit_ts == to_utc(D, "10:15")             # intrabar exit stamped at bar close


def test_target_requires_trade_through_not_touch():
    over = {"10:05": (5005, 5006, 5001, 5002), "10:10": (5002, 5003, 4995.0, 4996)}  # touches 4995.00 only
    t = one(run(one_day(D, over), sig_at("10:00", side=-1, stop_px=5010.0)))
    assert t.exit_reason == "time" and t.exit_ts == to_utc(D, "11:30")


def test_long_stop_hand_computed():
    over = {"10:05": (5000, 5001, 4994.75, 4996)}
    t = one(run(one_day(D, over), sig_at("10:00", side=1, stop_px=4995.0)))
    assert (t.entry_px, t.exit_px, t.exit_reason) == (5000.0, 4995.0, "stop")
    assert t.pnl_gross == pytest.approx(-25.0)
    assert t.costs == pytest.approx(0.70 + 1.25 + 2.50)    # 4.45
    assert t.pnl_net == pytest.approx(-29.45)
    assert t.r_multiple == pytest.approx(-29.45 / 25)
    assert t.bars_held == 1


def test_stop_touch_exactly_counts():
    over = {"10:05": (5000, 5001, 4995.0, 4996)}
    assert one(run(one_day(D, over), sig_at("10:00", side=1, stop_px=4995.0))).exit_reason == "stop"


def test_ambiguous_bar_assumes_stop():
    over = {"10:05": (5000, 5010.25, 4994.75, 5000)}   # touches stop 4995 and target 5010 (through 1 tick)
    t = one(run(one_day(D, over), sig_at("10:00", side=1, stop_px=4995.0)))
    assert t.exit_reason == "ambiguous_stop" and t.exit_px == 4995.0
    assert t.pnl_net == pytest.approx(-29.45)


def test_short_ambiguous_symmetric():
    over = {"10:05": (5005, 5010.0, 4994.75, 5000)}
    t = one(run(one_day(D, over), sig_at("10:00", side=-1, stop_px=5010.0)))
    assert t.exit_reason == "ambiguous_stop" and t.exit_px == 5010.0 and t.pnl_gross == pytest.approx(-25.0)


def test_gap_through_stop_fills_at_open():
    over = {"10:10": (4993.0, 4994.0, 4992.0, 4993.5)}
    t = one(run(one_day(D, over), sig_at("10:00", side=1, stop_px=4995.0)))
    assert t.exit_reason == "stop" and t.exit_px == 4993.0
    assert t.pnl_gross == pytest.approx(-35.0)
    assert t.costs == pytest.approx(4.45) and t.pnl_net == pytest.approx(-39.45)
    assert t.exit_ts == to_utc(D, "10:10")           # at the open
    assert t.bars_held == 1                           # entry bar only


def test_short_gap_through_stop():
    over = {"10:10": (5012.0, 5013.0, 5011.0, 5012.0)}
    t = one(run(one_day(D, over), sig_at("10:00", side=-1, stop_px=5010.0)))
    assert t.exit_px == 5012.0 and t.pnl_gross == pytest.approx((5000 - 5012) * 5)


def test_time_exit_at_1130_open():
    over = {"11:30": (5003.0, 5004.0, 5002.0, 5003.0)}
    t = one(run(one_day(D, over), sig_at("10:55", side=1, stop_px=4995.0)))
    assert (t.entry_ts, t.exit_ts, t.exit_reason) == (to_utc(D, "11:00"), to_utc(D, "11:30"), "time")
    assert t.exit_px == 5003.0 and t.pnl_gross == pytest.approx(15.0)
    assert t.costs == pytest.approx(0.70 + 1.25 + 1.25) and t.bars_held == 6


def test_forced_flat_at_1555():
    t = one(run(one_day(D), sig_at("10:00", side=1, stop_px=4995.0, time_exit_min=16 * 60)))
    assert t.exit_reason == "eod" and t.exit_ts == to_utc(D, "15:55")


def test_one_trade_per_day_and_next_day_allowed():
    over = {"10:05": (5005, 5006, 5001, 5002), "10:10": (5002, 5003, 4994.75, 4995)}
    bars = to_df(rth_bars(D, over=over) + rth_bars(D2, over=over))
    script = {**sig_at("10:00", side=-1, stop_px=5010.0), **sig_at("10:30", side=-1, stop_px=5010.0),
              **sig_at("10:00", D2, side=-1, stop_px=5010.0)}
    res = run(bars, script)
    assert len(res.trades) == 2 and res.diagnostics["cancel_daily_limit"] == 1
    assert list(res.trades.trade_id) == [1, 2]


def test_cancel_when_next_bar_missing():
    res = run(one_day(D, skip=("10:05",)), sig_at("10:00", side=1, stop_px=4995.0))
    assert len(res.trades) == 0 and res.diagnostics["cancel_gap"] == 1


def test_cancel_when_risk_outside_at_fill():
    bars = one_day(D, {"10:05": (5009.0, 5009.5, 5008.0, 5009.0)})
    res = run(bars, sig_at("10:00", side=-1, stop_px=5010.0))   # D=1pt < 2
    assert len(res.trades) == 0 and res.diagnostics["cancel_risk_filter_at_fill"] == 1
    bars = one_day(D, {"10:05": (5011.0, 5012.0, 5010.5, 5011.0)})
    res = run(bars, sig_at("10:00", side=-1, stop_px=5010.0))   # open through stop
    assert res.diagnostics["cancel_open_through_stop"] == 1


def test_discontinuity_closes_at_previous_close_never_held():
    over = {"10:10": (5001, 5002, 5000, 5001.5)}
    t = one(run(one_day(D, over, skip=("10:15",)), sig_at("10:00", side=1, stop_px=4995.0)))
    assert t.exit_reason == "eod" and t.exit_px == 5001.5 and t.exit_ts == to_utc(D, "10:15")  # 10:10 bar close


def test_contract_change_while_in_position_closes():
    rows = rth_bars(D, contract="MESM6", end="10:10") + rth_bars(D, contract="MESU6", start="10:15")
    t = one(run(to_df(rows), sig_at("10:00", side=1, stop_px=4995.0)))
    assert t.exit_reason == "eod" and t.exit_ts == to_utc(D, "10:15")


def test_cancel_on_contract_change_at_fill():
    rows = rth_bars(D, contract="MESM6", end="10:00") + rth_bars(D, contract="MESU6", start="10:05")
    res = run(to_df(rows), sig_at("10:00", side=1, stop_px=4995.0))
    assert len(res.trades) == 0 and res.diagnostics["cancel_contract"] == 1


def test_end_of_data_closes_open_position():
    t = one(run(one_day(D, end="10:20"), sig_at("10:00", side=1, stop_px=4995.0)))
    assert t.exit_reason == "eod" and t.exit_ts == to_utc(D, "10:25")


def test_columns_and_identities():
    over = {"10:05": (5005, 5006, 5001, 5002), "10:10": (5002, 5003, 4994.75, 4995)}
    res = run(one_day(D, over), sig_at("10:00", side=-1, stop_px=5010.0))
    assert list(res.trades.columns) == TRADE_COLUMNS
    t = res.trades.iloc[0]
    assert t.pnl_net == pytest.approx(t.pnl_gross - t.costs) and t.costs > 0
    assert str(res.trades.signal_ts.dt.tz) == "UTC" and str(res.trades.exit_ts.dt.tz) == "UTC"
    assert t.strategy == "scripted"


def test_empty_result_has_columns():
    res = run(one_day(D), {})
    assert list(res.trades.columns) == TRADE_COLUMNS and len(res.trades) == 0


def test_risk_sizing_and_cost_scaling():
    over = {"10:05": (5005, 5006, 5001, 5002), "10:10": (5002, 5003, 4994.75, 4995)}
    cfg = replace(ECFG, sizing_mode="risk", risk_budget_usd=100.0, max_qty=3)   # 100/25 = 4 -> capped 3
    t = one(run(one_day(D, over), sig_at("10:00", side=-1, stop_px=5010.0), cfg))
    assert t.qty == 3 and t.pnl_gross == pytest.approx(150.0) and t.costs == pytest.approx(3 * 1.95)
    assert t.risk_usd == pytest.approx(75.0)
    cfg = replace(ECFG, sizing_mode="risk", risk_budget_usd=60.0, max_qty=10)    # 60/25 = 2.4 -> 2
    assert one(run(one_day(D, over), sig_at("10:00", side=-1, stop_px=5010.0), cfg)).qty == 2


def test_cost_multiplier_stress():
    over = {"10:05": (5005, 5006, 5001, 5002), "10:10": (5002, 5003, 4994.75, 4995)}
    cfg = replace(ECFG, cost=replace(COST, cost_multiplier=2.0))
    t = one(run(one_day(D, over), sig_at("10:00", side=-1, stop_px=5010.0), cfg))
    assert t.costs == pytest.approx(3.90) and t.pnl_net == pytest.approx(46.10)


def test_bad_index_rejected():
    df = one_day(D).tz_convert("America/New_York")
    with pytest.raises(ValueError):
        run(df, {})
