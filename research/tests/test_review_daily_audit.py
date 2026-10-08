"""Phase-3 reviewer tests for the DAILY pipeline (docs/REVIEW.md, section 'Daily pipeline audit').
Synthetic data only, EXCEPT test_independent_reimplementation_matches_worker which reads the real
data/processed files (skipped if absent) and touches ONLY train+validation entries (never the sealed test)."""
import json
import pathlib
import numpy as np
import pandas as pd
import pytest
from tradelab.daily import baselines as B, engine as E, signals as S
from tradelab.validation.splits import classify_trades, make_split
from tradelab.validation.verdict import classify_development, REJECTED

ROOT = pathlib.Path(__file__).resolve().parents[1]
COSTS = E.DailyCosts()


def synth(n=150, seed=1, start="2024-01-02"):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n, tz="UTC") + pd.Timedelta(hours=14, minutes=30)
    c = 400 + np.cumsum(rng.normal(0, 3, n)); o = np.r_[400, c[:-1]] + rng.normal(0, 1, n)
    h = np.maximum(o, c) + rng.exponential(1.5, n); l = np.minimum(o, c) - rng.exponential(1.5, n)
    df = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": 1e6}, index=idx)
    df.index.name = "ts_open"
    return df


def test_look_ahead_prefix_invariance():
    """signal/H*/L*/ATR at row t from a prefix ending at t must equal the full-sample values (PASSES)."""
    b = synth(120)
    full = S.signal_table(b)
    for k in (30, 55, 90, 118):
        pre = S.signal_table(b.iloc[:k + 1])
        cols = ["hstar", "lstar", "atr", "raw_signal"]
        pd.testing.assert_frame_equal(pre[cols].iloc[:k + 1], full[cols].iloc[:k + 1])


def test_cost_arithmetic_by_hand():
    """2 x 0.35 + 2 x 0.005 x qty + 1bp x notional_open + 0.5bp x notional_close (PASSES); multiplier scales everything."""
    qty, ep, xp = 20.0, 500.0, 502.0
    exp = 0.70 + 2 * 0.005 * qty + qty * ep * 1e-4 + qty * xp * 0.5e-4
    assert float(COSTS.round_trip(qty, ep, xp)) == pytest.approx(exp)
    assert float(COSTS.scaled(2.0).round_trip(qty, ep, xp)) == pytest.approx(2 * exp)


def test_outside_day_and_equalities_are_not_signals():
    idx = pd.bdate_range("2024-01-02", periods=3, tz="UTC") + pd.Timedelta(hours=14, minutes=30)
    # prev range [10,20]; day2 equals prior high exactly (not > ) -> no bearish; closes inside
    b = pd.DataFrame({"open": [15, 15, 15], "high": [20, 20, 25], "low": [10, 12, 12], "close": [15, 15, 18], "volume": 1.0}, index=idx)
    assert S.raw_c2(S.repair_envelope(b)).iloc[1] == 0          # high == prior high: strict inequality required
    assert S.raw_c2(S.repair_envelope(b)).iloc[2] == -1         # sweep above 20, close 18 inside


# --- D-04: last bar's trade dropped by the split (reproduces on synthetic data)
@pytest.mark.xfail(strict=True, reason="D-04: make_split(first_ts, last_ts) is given the last bar's OPEN time, so the final "
                   "candidate trade (entry = last bar open, exit = 16:00 NY) is classified 'straddle' and silently dropped")
def test_last_day_trade_is_not_dropped_by_the_split():
    b = synth(200)
    c = E.candidates(b, "SYN")
    split = make_split(b.index[0], b.index[-1], (0.6, 0.2, 0.2), pd.Timedelta(days=3))
    lab = classify_trades(E.make_trades(c, +1, "x", COSTS), split)
    assert c.entry_ts.iloc[-1] == b.index[-1]
    assert lab.iloc[-1] == "test"


# --- D-05: strategy ids in trades are not the registered log ids
@pytest.mark.xfail(strict=True, reason="D-05: trades carry 'D3_V1_c2_both@universe=SPY|QQQ|IWM' but EXPERIMENT_LOG registers 'D3-V1'")
def test_strategy_id_matches_experiment_log():
    log = (ROOT / "docs" / "EXPERIMENT_LOG.md").read_text()
    assert f"| {E.strategy_id('D3_V1_c2_both', ['SPY', 'QQQ', 'IWM'])} |" in log


# --- D-02: independent-per-instrument null is too narrow when signals cluster by date
def test_independent_null_is_narrower_than_date_matched_null_when_signals_cluster():
    """Three perfectly correlated instruments (identical bars), signals on the same dates (the extreme of the clustering
    seen in real data, where ~25% fewer distinct dates occur than independence implies). The worker's null draws days
    independently per instrument; a date-shuffle null (one permutation of dates shared by all instruments) has a larger
    sd, i.e. the worker's p-values are anti-conservative. Documents the size of the effect (PASSES)."""
    b = synth(260, seed=3)
    cands = {s: E.candidates(b, s) for s in ("A", "B", "C")}
    c = cands["A"]
    n = 40
    counts = {s: (0, n) for s in cands}
    ind = B.matched_permutation_null(cands, counts, COSTS, seed=1, n_draws=4000)["usd"]
    short = E.make_trades(c, -1, "x", COSTS)["pnl_net"].to_numpy()
    rng = np.random.default_rng(1)
    perms = rng.random((4000, len(c))).argsort(axis=1)[:, :n]
    shared = short[perms].mean(axis=1)                     # same dates for all three instruments => mean of one instrument
    assert shared.std() > 1.4 * ind.std()                  # ~sqrt(3) in the extreme


def test_v1_rejection_follows_pre_registered_rule_literally():
    """Reconstruct classify_development from the stored (train+validation only) inputs. PASSES."""
    p = ROOT / "results" / "daily" / "validation_summary.json"
    if not p.exists():
        pytest.skip("results not present")
    e = json.loads(p.read_text())["verdict"]["D3_V1_c2_both"]["inputs"]
    assert e["n_train"] >= 30 and e["ev_train_1x"] <= 0
    assert classify_development(e)[0] == REJECTED


def test_reject_rule_is_a_point_estimate_screen_not_evidence_of_negative_ev():
    """Documents (Info): a train EV of -$0.01 with n=30 is REJECTED even though nothing is statistically distinguishable."""
    e = dict(n_train=30, n_val=500, ev_train_1x=-0.01, ev_val_1x=50.0, val_ci_hi_1x=100.0)
    assert classify_development(e)[0] == REJECTED


@pytest.mark.skipif(not (ROOT / "data" / "processed" / "SPY_1d.csv").exists(), reason="real daily data not present")
def test_independent_reimplementation_matches_worker():
    """Loop re-implementation of the C2 rule, ATR14, fills, costs and the split on TRAIN+VALIDATION only; compares trade
    counts and net totals with the worker's stored summaries. (Aggregates only; no raw prices printed or asserted.)"""
    syms = ["SPY", "QQQ", "IWM"]
    raw = {s: pd.read_csv(ROOT / "data" / "processed" / f"{s}_1d.csv") for s in syms}
    for d in raw.values():
        d["ts"] = pd.to_datetime(d.ts_open, utc=True)
    first = min(d.ts.iloc[0] for d in raw.values()); last = max(d.ts.iloc[-1] for d in raw.values())
    span = (last + pd.Timedelta(1, "ns")) - first
    b1 = first + pd.Timedelta(int(span.value * 0.6), "ns"); b2 = first + pd.Timedelta(int(span.value * 0.8), "ns")
    purge = pd.Timedelta(days=3)

    def seg(en, ex):
        if first <= en < b1:
            return "train" if ex < b1 else None
        if b1 <= en < b2 and en >= b1 + purge:
            return "validation" if ex < b2 else None
        return None                                     # sealed test / outside: never evaluated

    rows = []
    for s, d in raw.items():
        o, h, l, c = (d[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        H = np.maximum.reduce([h, o, c]); L = np.minimum.reduce([l, o, c]); n = len(d)
        dates = d.ts.dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize()
        tr = [np.nan] + [max(H[i] - L[i], abs(H[i] - c[i - 1]), abs(L[i] - c[i - 1])) for i in range(1, n)]
        for t in range(14, n - 1):
            atr = np.mean(tr[t - 13:t + 1])
            if not atr > 0 or not 1 <= (dates[t + 1] - dates[t]).days <= 4:
                continue
            bear = H[t] > H[t - 1] and c[t] < H[t - 1] and c[t] > L[t - 1]
            bull = L[t] < L[t - 1] and c[t] > L[t - 1] and c[t] < H[t - 1]
            if bear == bull:
                continue
            side = -1 if bear else 1
            en = d.ts.iloc[t + 1]
            ex = (dates[t + 1] + pd.Timedelta(hours=16)).tz_localize("America/New_York").tz_convert("UTC")
            sg = seg(en, ex)
            if sg is None:
                continue
            q = 10000 / o[t + 1]
            gross = side * (c[t + 1] - o[t + 1]) * q
            cost = 0.70 + 2 * 0.005 * q + q * o[t + 1] * 1e-4 + q * c[t + 1] * 0.5e-4
            rows.append((sg, side, gross - cost))
    r = pd.DataFrame(rows, columns=["seg", "side", "net"])
    for segname in ("train", "validation"):
        j = json.loads((ROOT / "results" / "daily" / f"{segname}_summary.json").read_text())["variants"]
        for v, f in (("D3_V1_c2_both", lambda x: x.side != 0), ("D3_V2_c2_long", lambda x: x.side == 1),
                     ("D3_V3_c2_short", lambda x: x.side == -1)):
            x = r[(r.seg == segname) & f(r)]
            assert len(x) == j[v]["pooled"]["n"]
            assert x.net.sum() == pytest.approx(j[v]["pooled"]["total_usd"], abs=1e-3)
