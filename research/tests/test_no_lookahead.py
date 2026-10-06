"""Results at time t must not depend on any bar after t (truncated or replaced by garbage)."""
import numpy as np
import pandas as pd
import pytest
from tradelab.backtest.baselines import OpeningRangeBreakout, RandomEntry, RandomParams
from tradelab.backtest.engine import run_backtest
from tradelab.backtest.sweep_reversal import SweepParams, SweepReversal
from test_backtest_helpers import ECFG, MES, to_df, to_utc

import datetime as dt

STEP = pd.Timedelta(minutes=5)


def synthetic_walk(seed=11):
    """Test-only random walk (labelled SYNTHETIC by construction; tests software, not markets)."""
    rng = np.random.default_rng(seed)
    days = [d for d in pd.date_range("2026-03-02", "2026-03-20") if d.weekday() < 5]  # spans DST start Mar 8
    px, rows = 5000.0, []
    for d in days:
        prev = (d - pd.Timedelta(days=1)).date()
        stamps = [(prev, h * 60 + m) for h in range(18, 24) for m in range(0, 60, 5)]
        stamps += [(d.date(), t) for t in range(0, 9 * 60 + 30, 5)]
        stamps += [(d.date(), t) for t in range(9 * 60 + 30, 16 * 60, 5)]
        for dd, t in stamps:
            o = px
            c = round((o + 0.04 * (5000.0 - o) + rng.normal(0, 1.1)) * 4) / 4   # mean-reverting so levels get revisited
            h = max(o, c) + abs(round(rng.normal(0, 0.5) * 4) / 4)
            l = min(o, c) - abs(round(rng.normal(0, 0.5) * 4) / 4)
            rows.append((to_utc(dd, f"{t // 60:02d}:{t % 60:02d}"), o, h, l, c, 100.0, "MESH6"))
            px = c
    return to_df(rows)


BARS = synthetic_walk()


def make(kind):
    if kind == "h1_both":
        return SweepReversal(MES, SweepParams(levels="both"))
    if kind == "h1_prev":
        return SweepReversal(MES, SweepParams(levels="prev_rth", target_r=3.0))
    if kind == "b1":
        return OpeningRangeBreakout(MES)
    return RandomEntry(MES, RandomParams(seed=3, rep=1))


KINDS = ["h1_both", "h1_prev", "b1", "b0"]


def garbage_after(bars, k, seed=5):
    rng = np.random.default_rng(seed)
    g = bars.copy()
    n = len(g) - k - 1
    px = rng.uniform(3000, 7000, size=(n, 4))
    g.iloc[k + 1:, g.columns.get_loc("open")] = px[:, 0]
    g.iloc[k + 1:, g.columns.get_loc("high")] = px.max(axis=1) + 1
    g.iloc[k + 1:, g.columns.get_loc("low")] = px.min(axis=1) - 1
    g.iloc[k + 1:, g.columns.get_loc("close")] = px[:, 3]
    g.iloc[k + 1:, g.columns.get_loc("contract")] = rng.choice(["X", "Y", "MESH6"], size=n)
    return g


@pytest.mark.parametrize("kind", KINDS)
def test_synthetic_run_produces_activity(kind):
    res = run_backtest(BARS, make(kind), ECFG)
    assert len(res.signals) >= 2 and len(res.trades) >= 1   # otherwise the leakage test would be vacuous


@pytest.mark.parametrize("kind", KINDS)
def test_truncation_and_garbage_do_not_change_past(kind):
    full = run_backtest(BARS, make(kind), ECFG)
    n = len(BARS)
    cuts = set(range(60, n - 1, 331))                      # spread through the sample
    for sg in full.signals:                                  # plus cuts right at/around every decision
        j = BARS.index.get_loc(sg.signal_ts - STEP)
        cuts |= {j - 1, j, j + 1, j + 3}
    for tr in full.trades.itertuples():                      # and inside each trade
        cuts.add(BARS.index.get_loc(tr.entry_ts) + 1)
    cuts = sorted(c for c in cuts if 10 < c < n - 1)
    assert len(cuts) > 12
    for k in cuts:
        tk = BARS.index[k]
        want_sig = [s for s in full.signals if s.signal_ts <= tk + STEP]
        want_tr = full.trades[full.trades.exit_ts <= tk].reset_index(drop=True)
        for variant in (BARS.iloc[: k + 1], garbage_after(BARS, k)):
            r = run_backtest(variant, make(kind), ECFG)
            got_sig = [s for s in r.signals if s.signal_ts <= tk + STEP]
            got_tr = r.trades[r.trades.exit_ts <= tk].reset_index(drop=True)
            assert got_sig == want_sig, f"signals differ at cut {k}"
            assert len(got_tr) == len(want_tr)
            if len(want_tr):
                pd.testing.assert_frame_equal(got_tr, want_tr)


def test_strategy_object_only_receives_completed_bars_one_at_a_time():
    seen = []

    class Spy:
        name = "spy"
        def reset(self): pass
        def on_bar(self, ts, o, h, l, c, v, contract):
            seen.append(ts)
            return None

    run_backtest(BARS, Spy(), ECFG)
    assert seen == list(BARS.index)       # in order, once each, no lookahead handle


class Leaky:
    """Deliberately peeks one bar ahead (via the global frame): the harness must catch it."""
    name = "leaky"
    def __init__(self, frame): self.frame = frame
    def reset(self): self.i = -1
    def on_bar(self, ts, o, h, l, c, v, contract):
        from tradelab.backtest.signals import Signal
        self.i += 1
        j = self.frame.index.get_loc(ts)
        et = ts.tz_convert("America/New_York")
        if j + 1 < len(self.frame) and et.hour == 10 and et.minute == 0 and self.frame["close"].iloc[j + 1] > c:
            return Signal("leaky", 1, ts + STEP, c - 5, c, 2.0, 2.0, 12.0, 11 * 60 + 30)
        return None


def test_harness_detects_a_leaky_strategy():
    full = run_backtest(BARS, Leaky(BARS), ECFG)
    assert len(full.signals) >= 2
    found = False
    for sg in full.signals:
        k = BARS.index.get_loc(sg.signal_ts - STEP)          # cut exactly at the decision bar
        tk = BARS.index[k]
        r = run_backtest(BARS.iloc[: k + 1], Leaky(BARS.iloc[: k + 1]), ECFG)
        if [x for x in r.signals if x.signal_ts <= tk + STEP] != [x for x in full.signals if x.signal_ts <= tk + STEP]:
            found = True
            break
    assert found
