"""SYNTHETIC MES-like bars for testing software ONLY. Random walk: no market information whatsoever.

Every output carries df.attrs['SYNTHETIC']=True, contract labels starting 'SYNTH', and (when written to CSV)
a SYNTHETIC column and a SYNTHETIC_ filename. Deterministic given `seed`.
"""
from __future__ import annotations
import argparse
from dataclasses import dataclass
from pathlib import Path
import numpy as np
import pandas as pd
from tradelab.contracts import BAR_COLUMNS, BAR_INDEX_NAME
from tradelab.data import calendar as cal

TICK = 0.25


@dataclass(frozen=True)
class Defects:
    duplicates: int = 0
    gaps: int = 0               # runs of 5-40 missing bars inside open hours
    bad_ohlc: int = 0           # high set below low
    zero_volume: int = 0
    spikes: int = 0             # one-bar +-3% close spikes
    stale_run: int = 0          # length of a flat run (0 = none)
    out_of_order: int = 0       # adjacent row swaps
    roll_jump_points: float = 0.0   # contract changes at the first session break after `roll_after`
    roll_after: str | None = None
    drop_contract_col: bool = False  # test the heuristic roll detector
    dst_bug: bool = False       # shift timestamps +1h after the first DST change (fixed-offset export bug)


def make_synthetic_bars(seed: int, start: str = "2024-03-04", days: int = 10, freq: str = "1min",
                        start_price: float = 4800.0, defects: Defects | None = None) -> pd.DataFrame:
    """Bars for `days` UTC days from `start` (UTC midnight), only inside nominal Globex hours (holidays NOT modelled)."""
    d = defects or Defects()
    rng = np.random.default_rng(seed)
    step = pd.Timedelta(freq)
    idx = pd.date_range(pd.Timestamp(start, tz="UTC"), periods=int(days * pd.Timedelta("1D") // step), freq=step)
    idx = idx[cal.globex_open_mask(idx)]
    n = len(idx)
    mins = np.asarray(idx.tz_convert(cal.NY).hour * 60 + idx.tz_convert(cal.NY).minute)
    rth = cal.rth_mask(idx)
    k = step / pd.Timedelta("1min")
    sigma = np.where(rth, 0.00030, 0.00015) * np.sqrt(k)
    logp = np.log(start_price) + np.cumsum(rng.normal(0, sigma))
    close = np.round(np.exp(logp) / TICK) * TICK
    open_ = np.r_[start_price, close[:-1]]
    wick = lambda: np.round(rng.exponential(1.0, n) * (1 + rth)) * TICK
    high = np.maximum(open_, close) + wick()
    low = np.minimum(open_, close) - wick()
    vol = rng.poisson(np.where(rth, 900, 200) * k).astype(float)
    df = pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=idx)
    df["contract"] = "SYNTH-MES-A"
    df.index.name = BAR_INDEX_NAME
    if d.roll_jump_points:
        after = pd.Timestamp(d.roll_after, tz="UTC") if d.roll_after else idx[n // 2]
        brk = np.nonzero((idx[1:] - idx[:-1] > step) & (idx[1:] > after))[0]
        if len(brk):
            i = brk[0] + 1
            df.iloc[i:, :4] += d.roll_jump_points
            df.iloc[i:, df.columns.get_loc("contract")] = "SYNTH-MES-B"
    pick = lambda m: rng.choice(np.arange(10, n - 60), size=m, replace=False)
    for i in pick(d.bad_ohlc):
        df.iloc[i, 1] = df.iloc[i, 2] - 1.0
    for i in pick(d.zero_volume):
        df.iloc[i, 4] = 0.0
    for i in pick(d.spikes):
        df.iloc[i, 3] = round(df.iloc[i, 3] * 1.03 / TICK) * TICK
        df.iloc[i, 1] = max(df.iloc[i, 1], df.iloc[i, 3])
    if d.stale_run:
        i = int(rng.integers(10, n - d.stale_run - 10))
        df.iloc[i:i + d.stale_run, :4] = df.iloc[i - 1, 3]
    df = df.drop(index=[t for i in pick(d.gaps) for t in df.index[i:i + int(rng.integers(5, 41))]])
    if d.duplicates:
        dup = df.iloc[rng.choice(len(df), d.duplicates, replace=False)]
        df = pd.concat([df, dup]).sort_index(kind="stable")
    if d.dst_bug:
        off = idx.tz_convert(cal.NY).map(lambda t: t.utcoffset())
        change = [i for i in range(1, len(off)) if off[i] != off[i - 1]]
        if change:
            df.index = df.index.where(df.index < idx[change[0]], df.index + pd.Timedelta(hours=1))
            df.index.name = BAR_INDEX_NAME
    if d.out_of_order:  # swap the timestamps of adjacent rows so time runs backwards there
        perm = np.arange(len(df))
        for i in rng.choice(np.arange(10, len(df) - 10, 10), size=d.out_of_order, replace=False):
            perm[[i, i + 1]] = perm[[i + 1, i]]
        df.index = df.index[perm]
    if d.drop_contract_col:
        df = df.drop(columns="contract")
    df[BAR_COLUMNS] = df[BAR_COLUMNS].astype("float64")
    df.attrs["SYNTHETIC"] = True
    return df


def write_csv(df: pd.DataFrame, path: Path) -> None:
    out = df.copy()
    out["SYNTHETIC"] = 1
    out.to_csv(path, index_label=BAR_INDEX_NAME)


def write_fixtures(outdir: str | Path = "data/fixtures", seed: int = 20240101) -> list[Path]:
    """Deterministic fixture files; filenames start with SYNTHETIC_."""
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    spec = {
        "SYNTHETIC_MES_1m_clean_mar2024.csv": dict(start="2024-03-06", days=8),
        "SYNTHETIC_MES_1m_clean_fall_dst.csv": dict(start="2024-10-31", days=6),
        "SYNTHETIC_MES_1m_defects_mar2024.csv": dict(start="2024-03-06", days=8, defects=Defects(
            duplicates=5, gaps=3, bad_ohlc=4, zero_volume=6, spikes=2, stale_run=40, roll_jump_points=55.0,
            roll_after="2024-03-07")),
    }
    paths = []
    for name, kw in spec.items():
        p = out / name
        write_csv(make_synthetic_bars(seed, **kw), p)
        paths.append(p)
    return paths


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Write SYNTHETIC test fixtures (not market data).")
    ap.add_argument("--outdir", default="data/fixtures")
    ap.add_argument("--seed", type=int, default=20240101)
    for p in write_fixtures(**vars(ap.parse_args())):
        print(p)
