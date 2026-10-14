"""Signals s_t in {0,1} from closes through t ONLY (rolling windows ending at t). Position held during day t+1 is s_t
(executed at the open of t+1). `first_valid` = index of the first bar with a defined signal (warm-up); s_t = 0 before it."""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma_signal(close: pd.Series, window: int = 200) -> tuple[np.ndarray, int]:
    sma = close.rolling(window, min_periods=window).mean()
    valid = sma.notna().to_numpy()
    s = ((close > sma) & sma.notna()).to_numpy().astype(int)
    return s, int(np.argmax(valid)) if valid.any() else len(close)


def cross_signal(close: pd.Series, fast: int = 50, slow: int = 200) -> tuple[np.ndarray, int]:
    f = close.rolling(fast, min_periods=fast).mean()
    sl = close.rolling(slow, min_periods=slow).mean()
    ok = (f.notna() & sl.notna())
    s = ((f > sl) & ok).to_numpy().astype(int)
    return s, int(np.argmax(ok.to_numpy())) if ok.any() else len(close)


def donchian_signal(close: pd.Series, entry: int = 100, exit_: int = 50) -> tuple[np.ndarray, int]:
    """Enter if close_t > max(close_{t-entry..t-1}); exit if close_t < min(close_{t-exit..t-1}); else keep state. Starts in cash."""
    prev = close.shift(1)
    hi = prev.rolling(entry, min_periods=entry).max().to_numpy()
    lo = prev.rolling(exit_, min_periods=exit_).min().to_numpy()
    c = close.to_numpy()
    n = len(c)
    s = np.zeros(n, dtype=int)
    state = 0
    first = n
    for t in range(n):
        if np.isnan(hi[t]) or np.isnan(lo[t]):
            continue
        if first == n:
            first = t
        if c[t] > hi[t]:
            state = 1
        elif c[t] < lo[t]:
            state = 0
        s[t] = state
    return s, first


def make_signal(kind_cfg: dict, close: pd.Series) -> tuple[np.ndarray, int]:
    k = kind_cfg["kind"]
    if k == "sma200":
        return sma_signal(close, 200)
    if k == "cross":
        return cross_signal(close, kind_cfg["fast"], kind_cfg["slow"])
    if k == "donchian":
        return donchian_signal(close, kind_cfg["entry"], kind_cfg["exit"])
    raise ValueError(k)
