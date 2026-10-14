"""Phase-5 reviewer: hand-computed micro-cases for the DAILY_SPEC2 accounting, signals and metrics, tested on the independent oracle
(tests/oracle_trend.py). These pin the spec text; test_review_trend_compare.py then compares the worker's implementation with them."""
import math
import pytest
import oracle_trend as O


def c_(px):  # cost fraction per spec: 1 bp + 0.5 cent / open + 0.35 bp
    return 0.0001 + 0.005 / px + 0.000035


def test_cost_formula_hand():
    assert O.cost(100.0) == pytest.approx(0.0001 + 0.00005 + 0.000035)
    assert O.cost(500.0, 2.0) == pytest.approx(2 * (0.0001 + 0.00001 + 0.000035))


def test_case_hold_uses_close_to_close():
    assert O.day_return(1, 1, o=999.0, c=102.0, c_prev=100.0) == pytest.approx(0.02)       # open and cost irrelevant


def test_case_buy_at_open_pays_cost_and_earns_open_to_close():
    assert O.day_return(0, 1, o=100.0, c=103.0, c_prev=50.0) == pytest.approx(0.03 - c_(100.0))   # overnight gap NOT earned


def test_case_sell_at_open_earns_overnight_only_and_pays_cost():
    assert O.day_return(1, 0, o=101.0, c=50.0, c_prev=100.0) == pytest.approx(0.01 - c_(101.0))   # intraday move NOT earned


def test_case_flat_is_zero_cash_earns_nothing():
    assert O.day_return(0, 0, o=101.0, c=90.0, c_prev=100.0) == 0.0


def test_metrics_hand_checked():
    m = O.metrics([0.10, -0.20, 0.05], [(0, 1), (1, 1), (1, 0)])
    assert m["total_return"] == pytest.approx(1.1 * 0.8 * 1.05 - 1)
    assert m["max_dd"] == pytest.approx(0.2)                                  # peak 1.1 -> 0.88
    assert m["switches"] == 2 and m["round_trips"] == 1 and m["time_in_market"] == pytest.approx(2 / 3)
    mean = (0.10 - 0.20 + 0.05) / 3
    sd = math.sqrt(sum((r - mean) ** 2 for r in (0.10, -0.20, 0.05)) / 2)
    assert m["vol"] == pytest.approx(sd * math.sqrt(252))


def test_drawdown_peak_includes_start_equity():
    assert O.metrics([-0.1, 0.05], [(0, 0)] * 2)["max_dd"] == pytest.approx(0.1)


def test_v1_threshold_is_strict_and_includes_today():
    c = [100.0] * 199 + [100.0]
    assert O.sig_v1(c)[199] == 0                               # equal to SMA: not above
    c[199] = 100.2                                             # SMA = (199*100+100.2)/200 = 100.001
    assert O.sig_v1(c)[199] == 1 and O.sig_v1(c)[198] is None  # first signal at the 200th close


def test_v2_cross():
    c = [100.0] * 150 + [110.0] * 50                           # last 50 closes = 110, last 200 mean = 102.5
    assert O.sig_v2(c)[199] == 1
    c = [110.0] * 150 + [100.0] * 50
    assert O.sig_v2(c)[199] == 0


def test_v3_donchian_state_machine():
    c = [100.0] * 100 + [101.0]                                # t=100: > max of closes 0..99
    s = O.sig_v3(c)
    assert s[99] is None and s[100] == 1
    c += [100.5] * 3                                           # inside the channel: keep state
    assert O.sig_v3(c)[-1] == 1
    c += [99.0]                                                # < min(closes t-50..t-1) = 100.5 -> exit
    assert O.sig_v3(c)[-1] == 0
    c += [99.5]                                                # not above the 100-day max -> stay in cash
    assert O.sig_v3(c)[-1] == 0


def test_v3_window_excludes_today():
    c = [100.0] * 100 + [100.0]                                # equal to max of prior closes: no entry
    assert O.sig_v3(c)[100] == 0


def test_positions_lag_signal_by_one_day():
    sig = [None, 0, 1, 1, 0]
    assert O.positions(sig) == [0, 0, 0, 1, 1]


def test_signals_use_no_future_bars():
    import random
    rnd = random.Random(7)
    c = [100.0]
    for _ in range(399):
        c.append(c[-1] * (1 + rnd.gauss(0, 0.01)))
    for name, f in O.SIGNALS.items():
        full = f(c)
        for cut in (120, 250, 333):
            tampered = c[:cut + 1] + [rnd.uniform(1, 1000) for _ in range(len(c) - cut - 1)]
            assert f(tampered)[:cut + 1] == full[:cut + 1], name


def test_strategy_series_first_day_is_a_buy_when_signal_on():
    o = [100.0] * 260; c = [100.0 + 0.1 * i for i in range(260)]
    sig = O.sig_v1(c)
    t0, rows = O.strategy_series(o, c, sig)
    assert t0 == 200 and rows[0][1:3] == (0, 1)                    # first position day: previous state is cash -> buy at open
    assert rows[0][3] == pytest.approx(c[200] / o[200] - 1 - O.cost(o[200]))
    assert rows[1][1:3] == (1, 1) and rows[1][3] == pytest.approx(c[201] / c[200] - 1)
