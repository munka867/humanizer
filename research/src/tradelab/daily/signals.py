"""H3 daily C2 signals. Every quantity at row t uses rows <= t only (shifts are backward-looking).
The ONLY use of row t+1 is `next_day_ok`, which looks at its DATE (not prices), as the spec requires."""
from __future__ import annotations

import numpy as np
import pandas as pd

NY = "America/New_York"


def repair_envelope(bars: pd.DataFrame) -> pd.DataFrame:
    """H* = max(high, open, close), L* = min(low, open, close) (fixed rule, DAILY_SPEC)."""
    out = bars.copy()
    out["hstar"] = bars[["high", "open", "close"]].max(axis=1)
    out["lstar"] = bars[["low", "open", "close"]].min(axis=1)
    return out


def true_range(env: pd.DataFrame) -> pd.Series:
    pc = env["close"].shift(1)
    tr = pd.concat([env["hstar"] - env["lstar"], (env["hstar"] - pc).abs(), (env["lstar"] - pc).abs()], axis=1).max(axis=1, skipna=False)
    return tr  # NaN on the first row (no previous close)


def atr(env: pd.DataFrame, n: int = 14) -> pd.Series:
    """Simple mean of the last n true ranges, ending at t (inclusive). NaN until n TRs exist."""
    return true_range(env).rolling(n, min_periods=n).mean()


def bar_dates(bars: pd.DataFrame) -> pd.Series:
    """NY calendar date of each bar's open."""
    return pd.Series(bars.index.tz_convert(NY).tz_localize(None).normalize(), index=bars.index)


def raw_c2(env: pd.DataFrame) -> pd.Series:
    """+1 bullish C2, -1 bearish C2, 0 none/outside day. Uses t and t-1 only."""
    hp, lp = env["hstar"].shift(1), env["lstar"].shift(1)
    c = env["close"]
    bear = (env["hstar"] > hp) & (c < hp) & (c > lp)
    bull = (env["lstar"] < lp) & (c > lp) & (c < hp)
    sig = np.where(bear & ~bull, -1, np.where(bull & ~bear, 1, 0))
    return pd.Series(sig, index=env.index, dtype=int)


def next_day_ok(bars: pd.DataFrame, max_gap_days: int = 4) -> pd.Series:
    """True if a bar t+1 exists and its date is <= max_gap_days calendar days after day t."""
    d = bar_dates(bars)
    gap = (d.shift(-1) - d).dt.days
    return (gap.notna() & (gap >= 1) & (gap <= max_gap_days))


def signal_table(bars: pd.DataFrame, atr_n: int = 14, max_gap_days: int = 4) -> pd.DataFrame:
    """Per-day table: hstar, lstar, atr, raw signal, eligible (ATR valid, t-1 exists, t+1 within gap),
    signal (raw signal where eligible, else 0)."""
    env = repair_envelope(bars)
    out = env[["hstar", "lstar"]].copy()
    out["atr"] = atr(env, atr_n)
    out["raw_signal"] = raw_c2(env)
    out["next_ok"] = next_day_ok(bars, max_gap_days)
    out["eligible"] = out["next_ok"] & out["atr"].notna() & (out["atr"] > 0)
    out["signal"] = np.where(out["eligible"], out["raw_signal"], 0)
    return out
