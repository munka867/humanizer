import math
import numpy as np
import pandas as pd
import pytest
from tradelab.validation import stats as S
from test_validation_common import mk


def test_t_cdf_known_values():
    assert S.t_two_sided_p(2.228138852, 10) == pytest.approx(0.05, abs=1e-5)
    assert S.t_two_sided_p(0.0, 5) == pytest.approx(1.0)
    assert S.t_two_sided_p(1.959964, 1e6) == pytest.approx(0.05, abs=1e-4)
    assert S.t_two_sided_p(12.706, 1) == pytest.approx(0.05, abs=1e-3)


def test_hand_checked_metrics():
    # pnl: +10, -4, -6, +20, -5 ; costs 1 each; risk 10
    tr = mk([("2024-03-04 15:00", 30, 10), ("2024-03-04 16:00", 30, -4), ("2024-03-05 15:00", 30, -6),
             ("2024-03-06 15:00", 30, 20), ("2024-03-07 15:00", 30, -5)])
    m = S.metrics(tr, 100.0, n_variants_tried=4, n_boot=200, seed=1, n_session_days=5)
    assert m["n_trades"] == 5 and m["ev_usd"] == pytest.approx(3.0)
    assert m["ev_r"] == pytest.approx(0.3)
    assert m["std_usd"] == pytest.approx(np.std([10, -4, -6, 20, -5], ddof=1))
    assert m["hit_rate"] == pytest.approx(0.4)
    assert m["profit_factor"] == pytest.approx(30 / 15)
    # equity: 110, 106, 100, 120, 115 ; peak start 100 -> 110 -> dd worst = 110-100 = 10 ; pct = 10/110
    assert m["max_dd_usd"] == pytest.approx(10.0) and m["max_dd_pct"] == pytest.approx(10 / 110)
    assert m["longest_losing_streak"] == 2
    # exposure: 5 trades x 30 min = 150 min over 5 days x 6.5h
    assert m["exposure"] == pytest.approx(150 / (5 * 6.5 * 60))
    assert m["n_days"] == 4
    assert m["p_bonferroni_day"] >= m["p_day_two_sided"] - 1e-12
    assert len(m["periods"]["month"]) == 1 and m["periods"]["month"][0]["n"] == 5


def test_trade_tstat_matches_formula():
    x = np.array([10, -4, -6, 20, -5.0])
    r = S.mean_tstat(x)
    assert r["t"] == pytest.approx(x.mean() / (x.std(ddof=1) / math.sqrt(5)))
    assert 0 < r["p_two_sided"] < 1


def test_drawdown_pct_uses_running_peak():
    dd, pct = S.max_drawdown(np.array([100.0, -50.0, -50.0, 10.0]), 100.0)
    assert dd == pytest.approx(100.0) and pct == pytest.approx(100 / 200)


def test_edge_cases():
    assert S.metrics(mk([]), 600.0)["n_trades"] == 0
    one = S.metrics(mk([("2024-03-04 15:00", 30, 5)]), 600.0, n_boot=50)
    assert one["n_trades"] == 1 and math.isnan(one["std_usd"]) and one["profit_factor"] == float("inf")
    wins = S.metrics(mk([("2024-03-04 15:00", 30, 5), ("2024-03-05 15:00", 30, 7)]), 600.0, n_boot=50)
    assert wins["profit_factor"] == float("inf") and wins["max_dd_usd"] == 0 and wins["longest_losing_streak"] == 0
    assert wins["hit_rate"] == 1.0


def test_exposure_overlap_not_double_counted():
    tr = mk([("2024-03-04 15:00", 60, 1), ("2024-03-04 15:30", 60, 1)])  # union = 90 min
    assert S.exposure_fraction(tr, 1, 1.5) == pytest.approx(1.0)


def test_day_bootstrap_deterministic_and_covers_mean():
    rows = [(f"2024-03-{d:02d} 15:00", 10, v) for d, v in zip(range(1, 21), [5, -3] * 10)]
    tr = mk(rows)
    a = S.bootstrap_mean_ci_by_day(tr, 500, seed=7)
    b = S.bootstrap_mean_ci_by_day(tr, 500, seed=7)
    c = S.bootstrap_mean_ci_by_day(tr, 500, seed=8)
    assert a == b and a != c
    assert a["lo"] <= a["mean"] <= a["hi"] and a["mean"] == pytest.approx(1.0)


def test_day_bootstrap_respects_clustering():
    # day-level outcomes strongly clustered: all trades of a day share sign -> wider CI than naive
    rows = []
    for d in range(1, 21):
        v = 10 if d % 2 else -10
        rows += [(f"2024-03-{d:02d} {h}:00", 10, v) for h in (14, 15, 16, 17)]
    tr = mk(rows)
    ci = S.bootstrap_mean_ci_by_day(tr, 2000, seed=0)
    naive_half = 1.96 * tr.pnl_net.std(ddof=1) / math.sqrt(len(tr))
    assert (ci["hi"] - ci["lo"]) / 2 > 1.5 * naive_half


def test_period_breakdown_shares():
    tr = mk([("2024-01-10 15:00", 5, 10), ("2024-02-10 15:00", 5, 30), ("2024-02-11 15:00", 5, -10)])
    p = S.period_breakdown(tr, "month")
    assert list(p.period) == ["2024-01", "2024-02"] and list(p.n) == [1, 2]
    assert p.share_of_total.tolist() == pytest.approx([1 / 3, 2 / 3])
    q = S.period_breakdown(tr, "quarter")
    assert list(q.period) == ["2024Q1"]


def test_bonferroni():
    assert S.bonferroni(0.01, 24) == pytest.approx(0.24)
    assert S.bonferroni(0.2, 24) == 1.0


def test_deflated_sharpe_monotone_in_trials():
    rng = np.random.default_rng(0)
    r = rng.normal(0.2, 1.0, 250)
    d1 = S.deflated_sharpe(r, 1)["dsr"]
    d24 = S.deflated_sharpe(r, 24)["dsr"]
    d1000 = S.deflated_sharpe(r, 1000)["dsr"]
    assert d1 > d24 > d1000
    assert S.deflated_sharpe(r, 1)["sr0"] == 0.0
    # SR0 for N=24, V=1/(T-1): hand check
    sr0 = S.deflated_sharpe(r, 24)["sr0"]
    ref = math.sqrt(1 / 249) * ((1 - S.EULER_GAMMA) * S.norm_ppf(1 - 1 / 24) + S.EULER_GAMMA * S.norm_ppf(1 - 1 / (24 * math.e)))
    assert sr0 == pytest.approx(ref)
    assert math.isnan(S.deflated_sharpe(np.array([1.0, 2.0]), 5)["dsr"])


def test_required_trades_formula():
    # (1.6449+0.8416)*1/0.05 squared ~ 2473
    assert S.required_trades(0.05, 1.0) == pytest.approx(((S.norm_ppf(0.95) + S.norm_ppf(0.8)) / 0.05) ** 2)
    assert 2400 < S.required_trades(0.05, 1.0) < 2550
    assert S.required_trades(0.05, 1.0, alpha=0.05 / 24) > S.required_trades(0.05, 1.0)
