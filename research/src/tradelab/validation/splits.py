"""Chronological splits with purging. No shuffling, ever.

Semantics (see contracts.Split): segments are half-open [start, end) in UTC.
A trade belongs to a segment by its ENTRY time. It is kept only if
  * entry_ts >= segment_start + purge   (for the 2nd and 3rd segments; the first
    segment has no leading purge), and
  * exit_ts  <  segment_end             (nothing may straddle a boundary).
Everything else is dropped and counted, with a reason:
  "purge_gap"  entry lies in [boundary, boundary + purge)
  "straddle"   entry is inside a segment's eligible area but exit_ts >= segment end
  "outside"    entry before the first boundary or at/after the overall end
Choose purge >= the longest possible trade duration (plus any feature lookback the
strategy uses) so that features/indicators warmed up before a boundary cannot carry
information across it either. Purging only removes trades; it cannot fix a strategy
that itself uses data across a boundary (indicator warm-up is the caller's concern).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

import pandas as pd

from tradelab.contracts import Split

SEGMENTS = ("train", "validation", "test")
_NS = pd.Timedelta(1, "ns")


def make_split(first_ts, last_ts, fractions=(0.6, 0.2, 0.2), purge=pd.Timedelta(days=1)) -> Split:
    """Split [first_ts, last_ts] (inclusive of last_ts) by TIME span, not trade count."""
    if len(fractions) != 3 or any(f <= 0 for f in fractions) or abs(sum(fractions) - 1.0) > 1e-9:
        raise ValueError("fractions must be 3 positive numbers summing to 1")
    first, last = pd.Timestamp(first_ts), pd.Timestamp(last_ts)
    if first.tzinfo is None or last.tzinfo is None:
        raise ValueError("timestamps must be tz-aware (UTC)")
    first, last = first.tz_convert("UTC"), last.tz_convert("UTC")
    if last <= first:
        raise ValueError("last_ts must be after first_ts")
    purge = pd.Timedelta(purge)
    if purge < pd.Timedelta(0):
        raise ValueError("purge must be >= 0")
    end = last + _NS
    span = end - first
    b1 = first + (span * fractions[0]).round("ns")
    b2 = first + (span * (fractions[0] + fractions[1])).round("ns")
    if purge >= (b2 - b1) or purge >= (end - b2):
        raise ValueError("purge is as long as a validation/test segment")
    return Split(train=(first, b1), validation=(b1, b2), test=(b2, end), purge=purge)


@dataclass
class SplitResult:
    train: pd.DataFrame
    validation: pd.DataFrame
    test: pd.DataFrame
    dropped: dict = field(default_factory=dict)  # {"purge_gap": n, "straddle": n, "outside": n}
    dropped_trades: pd.DataFrame | None = None   # with a 'drop_reason' column

    def segment(self, name: str) -> pd.DataFrame:
        return getattr(self, name)

    @property
    def n_dropped(self) -> int:
        return int(sum(self.dropped.values()))


def _check(trades: pd.DataFrame) -> None:
    for c in ("entry_ts", "exit_ts"):
        if c not in trades.columns:
            raise ValueError(f"trades missing column {c}")
    if len(trades) and (trades["exit_ts"] < trades["entry_ts"]).any():
        raise ValueError("trade with exit_ts < entry_ts")


def classify_trades(trades: pd.DataFrame, split: Split) -> pd.Series:
    """Label each trade: 'train'|'validation'|'test' or a drop reason
    ('purge_gap'|'straddle'|'outside'). Index aligned with `trades`."""
    _check(trades)
    labels = pd.Series("outside", index=trades.index, dtype=object)
    if len(trades) == 0:
        return labels
    e, x = trades["entry_ts"], trades["exit_ts"]
    for i, name in enumerate(SEGMENTS):
        start, end = getattr(split, name)
        in_seg = (e >= start) & (e < end)
        lead = split.purge if i > 0 else pd.Timedelta(0)
        in_purge = in_seg & (e < start + lead)
        eligible = in_seg & ~in_purge
        straddle = eligible & (x >= end)
        labels[in_purge] = "purge_gap"
        labels[straddle] = "straddle"
        labels[eligible & ~straddle] = name
    return labels


def apply_split(trades: pd.DataFrame, split: Split) -> SplitResult:
    labels = classify_trades(trades, split)
    parts = {n: trades[labels == n].sort_values("entry_ts", kind="stable") for n in SEGMENTS}
    drop_mask = ~labels.isin(SEGMENTS)
    dropped = {r: int((labels == r).sum()) for r in ("purge_gap", "straddle", "outside")}
    dropped_df = trades[drop_mask].assign(drop_reason=labels[drop_mask])
    return SplitResult(dropped=dropped, dropped_trades=dropped_df, **parts)


@dataclass(frozen=True)
class Window:
    train: tuple[pd.Timestamp, pd.Timestamp]
    test: tuple[pd.Timestamp, pd.Timestamp]
    purge: pd.Timedelta


def walk_forward_windows(first_ts, last_ts, train_span, test_span, step=None,
                         purge=pd.Timedelta(days=1), anchored=False) -> list[Window]:
    """Rolling (or anchored-expanding) walk-forward windows. Test window k starts
    `purge` after the train window ends; windows never extend past last_ts and
    consecutive TEST windows do not overlap when step >= test_span (default)."""
    first = pd.Timestamp(first_ts).tz_convert("UTC")
    end = pd.Timestamp(last_ts).tz_convert("UTC") + _NS
    train_span, test_span = pd.Timedelta(train_span), pd.Timedelta(test_span)
    step = pd.Timedelta(step) if step is not None else test_span
    purge = pd.Timedelta(purge)
    if min(train_span, test_span, step) <= pd.Timedelta(0):
        raise ValueError("spans and step must be positive")
    out: list[Window] = []
    t0 = first
    while True:
        tr_end = t0 + train_span if not anchored else first + train_span + len(out) * step
        tr_start = t0 if not anchored else first
        te_start = tr_end + purge
        te_end = te_start + test_span
        if te_end > end:
            break
        out.append(Window((tr_start, tr_end), (te_start, te_end), purge))
        t0 = t0 + step
    return out


def select_window(trades: pd.DataFrame, window: tuple, lead_purge=pd.Timedelta(0)):
    """Trades entering in [start+lead_purge, end) and exiting before end.
    Returns (kept, n_dropped_straddle_or_purge)."""
    _check(trades)
    start, end = window
    e, x = trades["entry_ts"], trades["exit_ts"]
    in_win = (e >= start) & (e < end)
    keep = in_win & (e >= start + lead_purge) & (x < end)
    return trades[keep], int((in_win & ~keep).sum())


def apply_walk_forward(trades: pd.DataFrame, windows: list[Window]) -> list[dict]:
    res = []
    for w in windows:
        tr, d1 = select_window(trades, w.train)
        te, d2 = select_window(trades, w.test)
        res.append({"window": w, "train": tr, "test": te, "dropped_train": d1, "dropped_test": d2})
    return res
