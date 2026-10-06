import math
import numpy as np
import pandas as pd
import pytest
from tradelab.contracts import Instrument
from tradelab.validation import montecarlo as M
from tradelab.validation.stats import day_codes
from test_validation_common import mk

MES = Instrument("MES", 5.0, 0.25)


def sample(n_days=12):
    rows = []
    for d in range(1, n_days + 1):
        for h, v in ((14, 4.0), (15, -2.0), (16, 3.0 if d % 3 else -6.0)):
            rows.append((f"2024-03-{d:02d} {h}:00", 20, v, 1.0, 10.0))
    t = mk(rows)
    t["qty"] = 2
    return t


def test_deterministic_given_seed_and_differs_across_seeds():
    t = sample()
    a = M.run_montecarlo(t, M.MCConfig(seed=1, n_sims=100))
    b = M.run_montecarlo(t, M.MCConfig(seed=1, n_sims=100))
    c = M.run_montecarlo(t, M.MCConfig(seed=2, n_sims=100))
    assert a == b and a != c


@pytest.mark.parametrize("method", M.METHODS)
def test_all_methods_run_and_carry_caveat(method):
    r = M.run_montecarlo(sample(), M.MCConfig(seed=3, n_sims=50, method=method, daily_loss_limit=5, max_dd_limit=8))
    assert "no independent market evidence" in r["caveat"]
    assert 0 <= r["probabilities"]["p_ruin"] <= 1
    assert r["probabilities"]["p_breach_max_dd_limit"] is not None
    if method == "day_block":
        assert r["probabilities"]["p_breach_daily_loss_limit"] is not None
    else:
        assert r["probabilities"]["p_breach_daily_loss_limit"] is None


def test_day_block_preserves_day_grouping():
    t = M._prep(sample())
    codes = day_codes(t)
    rng = np.random.default_rng(0)
    paths = M.draw_paths(t, "day_block", 20, None, 5, rng)
    for idx, seg in paths:
        assert len(np.unique(seg)) == 12
        for s in np.unique(seg):
            days = np.unique(codes[idx[seg == s]])
            assert len(days) == 1                       # one sampled day -> one source day
            assert (seg == s).sum() == (codes == days[0]).sum()   # ALL its trades, none missing
            # within-day order retained
            sub = idx[seg == s]
            assert (np.diff(sub) > 0).all()


def test_trade_block_contiguity():
    t = M._prep(sample())
    paths = M.draw_paths(t, "trade_block", 10, 20, 4, np.random.default_rng(0))
    for idx, _ in paths:
        assert len(idx) == 20
        for k in range(0, 20, 4):
            assert (np.diff(idx[k:k + 4]) == 1).all()


def test_edge_cases():
    r0 = M.run_montecarlo(mk([]), M.MCConfig(seed=0))
    assert r0["n_trades_input"] == 0 and "note" in r0 and "caveat" in r0
    r1 = M.run_montecarlo(mk([("2024-03-04 15:00", 10, 5)]), M.MCConfig(seed=0, n_sims=20, method="trade_block"))
    assert r1["distributions"]["total_pnl_usd"]["mean"] == pytest.approx(5.0)
    allw = mk([(f"2024-03-{d:02d} 15:00", 10, 3) for d in range(1, 6)])
    for m in M.METHODS:
        r = M.run_montecarlo(allw, M.MCConfig(seed=0, n_sims=30, method=m, max_dd_limit=1))
        assert r["probabilities"]["p_ruin"] == 0 and r["probabilities"]["p_ev_le_0"] == 0
        assert r["distributions"]["max_drawdown_usd"]["mean"] == 0
        assert r["probabilities"]["p_breach_max_dd_limit"] == 0


def test_ruin_probability_certain_loss():
    allL = mk([(f"2024-03-{d:02d} 15:00", 10, -100) for d in range(1, 8)])
    r = M.run_montecarlo(allL, M.MCConfig(seed=0, n_sims=20, account_size=600, ruin_equity=0))
    assert r["probabilities"]["p_ruin"] == 1.0   # 7 x -100 = -700 < -600


def test_fill_miss_drops_only_winners():
    t = sample()
    base = M.run_montecarlo(t, M.MCConfig(seed=5, n_sims=200, method="iid"))
    miss = M.run_montecarlo(t, M.MCConfig(seed=5, n_sims=200, method="iid", fill_miss_frac=0.5))
    assert miss["distributions"]["total_pnl_usd"]["mean"] < base["distributions"]["total_pnl_usd"]["mean"]
    assert miss["distributions"]["trades_per_path"]["mean"] < base["distributions"]["trades_per_path"]["mean"]
    full = M.run_montecarlo(t, M.MCConfig(seed=5, n_sims=50, method="iid", fill_miss_frac=1.0))
    assert full["distributions"]["total_pnl_usd"]["p95"] < 0      # only losers remain


def test_worse_stop_is_exact():
    t = M._prep(sample())
    t2 = M.apply_worse_stop(t, 2, MES)
    stops = t.exit_reason == "stop"
    # 2 ticks * 0.25 * $5 * qty 2 = $5.00
    assert (t.pnl_net - t2.pnl_net)[stops].tolist() == pytest.approx([5.0] * stops.sum())
    assert (t.pnl_net - t2.pnl_net)[~stops].abs().sum() == 0
    with pytest.raises(ValueError):
        M.apply_worse_stop(t, 1, None)


def test_cost_stress_hand_checked():
    t = mk([("2024-03-04 15:00", 10, 5, 2.0), ("2024-03-05 15:00", 10, -3, 2.0)])  # gross 7 and -1, costs 2
    t["qty"] = 2
    df = M.cost_stress(t, MES, multipliers=(1.0, 2.0), extra_ticks=(0.0, 1.0), n_boot=50)
    row = lambda m, k: df[(df.cost_mult == m) & (df.extra_ticks == k)].iloc[0]
    assert row(1.0, 0.0).ev_usd == pytest.approx(1.0)               # (5-3)/2
    assert row(2.0, 0.0).ev_usd == pytest.approx(1.0 - 2.0)         # costs doubled: -2 per trade
    # 1 extra tick per side: 1*0.25*5*2qty*2sides = 5.0 per trade
    assert row(1.0, 1.0).ev_usd == pytest.approx(1.0 - 5.0)
    assert "no independent market evidence" in df.attrs["caveat"]


def test_inconsistent_trade_table_rejected():
    t = sample()
    t.loc[0, "pnl_net"] += 5
    with pytest.raises(ValueError):
        M.run_montecarlo(t, M.MCConfig(seed=0))


def test_feasibility_documented_constraint():
    t = sample()
    f = M.account_feasibility(t, 600.0, None)
    assert f["margin_ok"] is None and any("not supplied" in n for n in f["notes"])
    f2 = M.account_feasibility(t, 600.0, 1500.0)
    assert f2["margin_ok"] is False
