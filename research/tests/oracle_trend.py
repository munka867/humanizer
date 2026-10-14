"""Independent, loop-based oracle for DAILY_SPEC2 (trend filters). Written from the spec text only (phase-5 review).
Deliberately plain Python (lists, for-loops, csv module): no pandas/numpy arithmetic, nothing shared with src/tradelab/daily_trend.
Performance is only ever computed for bars strictly before the sealed-test start (see load())."""
from __future__ import annotations

import csv
import datetime as dt
import math

SIGNAL_FIRST = {"V1": 199, "V2": 199, "V3": 100}          # first index at which a signal exists (closes through t)
SLIP, COMM = 0.0001, 0.35 / 10000.0                       # 1 bp, $0.35 on $10,000


def cost(open_px: float, mult: float = 1.0) -> float:
    """per buy or sell, fraction of notional: 1 bp + half spread (0.5 cent / open) + commission 0.35 bp."""
    return mult * (SLIP + 0.005 / open_px + COMM)


# ----------------------------------------------------------------------------- signals (close_t and earlier only)
def sig_v1(c: list[float]) -> list[int | None]:
    out: list[int | None] = [None] * len(c)
    for t in range(199, len(c)):
        s = 0.0
        for k in range(t - 199, t + 1):
            s += c[k]
        out[t] = 1 if c[t] > s / 200.0 else 0
    return out


def sig_v2(c: list[float]) -> list[int | None]:
    out: list[int | None] = [None] * len(c)
    for t in range(199, len(c)):
        s50 = sum(c[k] for k in range(t - 49, t + 1)) / 50.0
        s200 = sum(c[k] for k in range(t - 199, t + 1)) / 200.0
        out[t] = 1 if s50 > s200 else 0
    return out


def sig_v3(c: list[float]) -> list[int | None]:
    out: list[int | None] = [None] * len(c)
    state = 0
    for t in range(100, len(c)):
        hi = max(c[k] for k in range(t - 100, t))          # closes t-100..t-1
        lo = min(c[k] for k in range(t - 50, t))           # closes t-50..t-1
        if c[t] > hi:
            state = 1
        elif c[t] < lo:
            state = 0
        out[t] = state
    return out


SIGNALS = {"V1": sig_v1, "V2": sig_v2, "V3": sig_v3}


# ----------------------------------------------------------------------------- four-case accounting
def day_return(p_prev: int, p: int, o: float, c: float, c_prev: float, mult: float = 1.0) -> float:
    if p_prev == 1 and p == 1:
        return c / c_prev - 1.0
    if p_prev == 0 and p == 1:
        return c / o - 1.0 - cost(o, mult)
    if p_prev == 1 and p == 0:
        return o / c_prev - 1.0 - cost(o, mult)
    return 0.0


def positions(sig: list[int | None]) -> list[int]:
    """p_t = signal at close t-1 (None -> 0)."""
    p = [0] * len(sig)
    for t in range(1, len(sig)):
        p[t] = sig[t - 1] if sig[t - 1] is not None else 0
    return p


def strategy_series(o, c, sig, mult=1.0):
    """list of (index, p_prev, p, return) for every index >= first position day."""
    p = positions(sig)
    t0 = next(t for t in range(len(sig)) if sig[t] is not None) + 1
    rows = []
    for t in range(t0, len(c)):
        rows.append((t, p[t - 1] if t > t0 else 0, p[t], day_return(p[t - 1] if t > t0 else 0, p[t], o[t], c[t], c[t - 1], mult)))
    return t0, rows


def bh_series(o, c, start, indices, mult=1.0):
    """buy and hold over `indices` (consecutive ints, first >= start): one buy at the first open."""
    rows = []
    for j, t in enumerate(indices):
        rows.append((t, 0 if j == 0 else 1, 1, day_return(0 if j == 0 else 1, 1, o[t], c[t], c[t - 1], mult)))
    return rows


# ----------------------------------------------------------------------------- metrics
def metrics(rets: list[float], pprev_p: list[tuple[int, int]]) -> dict:
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= 1.0 + r
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak)
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    return {"n": n, "total_return": eq - 1.0, "max_dd": mdd, "vol": math.sqrt(var) * math.sqrt(252.0),
            "time_in_market": sum(p for _, p in pprev_p) / n,
            "switches": sum(1 for a, b in pprev_p if a != b), "round_trips": sum(1 for a, b in pprev_p if a == 0 and b == 1)}


# ----------------------------------------------------------------------------- data and segments
def load(path: str, cutoff: dt.datetime) -> tuple[list[dt.datetime], list[float], list[float]]:
    """Rows with ts_open < cutoff only; later rows are skipped BEFORE their prices are parsed (sealed window never read as numbers)."""
    ts, o, c = [], [], []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            t = dt.datetime.fromisoformat(r["ts_open"])
            if t >= cutoff:
                break
            ts.append(t); o.append(float(r["open"])); c.append(float(r["close"]))
    return ts, o, c


def all_timestamps(path: str) -> list[dt.datetime]:
    with open(path, newline="") as fh:
        return [dt.datetime.fromisoformat(r["ts_open"]) for r in csv.DictReader(fh)]


def segment_bounds(data_dir: str, symbols=("SPY", "QQQ", "IWM")):
    """(first, b1, b2, end) exactly as validation.splits.make_split on dataset A (timestamps only)."""
    import pandas as pd
    from tradelab.validation.splits import make_split
    firsts, lasts = [], []
    for s in symbols:
        ts = all_timestamps(f"{data_dir}/{s}_1d.csv")
        firsts.append(ts[0]); lasts.append(ts[-1])
    sp = make_split(pd.Timestamp(min(firsts)), pd.Timestamp(max(lasts)), (0.6, 0.2, 0.2), pd.Timedelta(days=3))
    return sp, sp.test[0].to_pydatetime()
