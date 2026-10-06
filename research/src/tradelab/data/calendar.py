"""CME Globex nominal calendar helpers (equity-index futures). All times America/New_York.

Nominal = weekly pattern only: Sun 18:00 ET -> Fri 17:00 ET, with a daily break 17:00-18:00 ET Mon-Thu.
Holidays and early closes are NOT assumed: `holiday_candidates` only lists dates a human should
verify against the official CME holiday calendar.
"""
from __future__ import annotations
import datetime as dt
import numpy as np
import pandas as pd

NY = "America/New_York"
RTH_START_MIN = 9 * 60 + 30
RTH_END_MIN = 16 * 60
_OPEN_MIN, _CLOSE_MIN = 18 * 60, 17 * 60


def _wall(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Naive America/New_York wall-clock times (DST handled by tz_convert)."""
    return idx.tz_convert(NY).tz_localize(None)


def globex_open_mask(idx: pd.DatetimeIndex) -> np.ndarray:
    """True where a bar OPENING at idx is inside nominal Globex trading hours (holidays ignored)."""
    w = _wall(idx)
    wd, m = np.asarray(w.dayofweek), np.asarray(w.hour * 60 + w.minute)
    closed = (wd == 5) | ((wd == 4) & (m >= _CLOSE_MIN)) | ((wd == 6) & (m < _OPEN_MIN)) \
        | ((wd <= 3) & (m >= _CLOSE_MIN) & (m < _OPEN_MIN))
    return ~closed


def rth_mask(idx: pd.DatetimeIndex) -> np.ndarray:
    """Bar opens within Mon-Fri 09:30-16:00 ET (a 1m bar at 15:59 is the last RTH bar)."""
    w = _wall(idx)
    m = np.asarray(w.hour * 60 + w.minute)
    return (np.asarray(w.dayofweek) < 5) & (m >= RTH_START_MIN) & (m < RTH_END_MIN)


def session_date(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """CME trading date (naive, midnight): the session opening Sun/Mon-Thu 18:00 ET belongs to the next date."""
    return (_wall(idx) + pd.Timedelta(hours=6)).normalize()


def et_date(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return _wall(idx).normalize()


def _nth(y: int, m: int, wd: int, n: int) -> dt.date:
    d = dt.date(y, m, 1)
    d += dt.timedelta(days=(wd - d.weekday()) % 7 + 7 * (n - 1))
    return d


def _last(y: int, m: int, wd: int) -> dt.date:
    d = dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - wd) % 7)


def _easter(y: int) -> dt.date:
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    j, k = c // 4, c % 4
    m = (a + 11 * h) // 319
    r = (2 * e + 2 * j - k - h + m + 32) % 7
    mo = (h - m + r + 90) // 25
    return dt.date(y, mo, (h - m + r + mo + 19) % 32)


def holiday_candidates(year: int) -> set[dt.date]:
    """US-holiday-ish dates (plus observed shifts, Thanksgiving Friday, Dec 24) where Globex may be
    closed or close early. A CANDIDATE list for flagging only; verify against the CME calendar."""
    fixed = [dt.date(year, 1, 1), dt.date(year, 6, 19), dt.date(year, 7, 4), dt.date(year, 12, 25),
             dt.date(year, 12, 24)]
    out = set(fixed)
    for d in fixed:  # observed: Sat -> Fri, Sun -> Mon
        if d.weekday() == 5:
            out.add(d - dt.timedelta(days=1))
        elif d.weekday() == 6:
            out.add(d + dt.timedelta(days=1))
    tg = _nth(year, 11, 3, 4)
    out |= {_nth(year, 1, 0, 3), _nth(year, 2, 0, 3), _easter(year) - dt.timedelta(days=2),
            _last(year, 5, 0), _nth(year, 9, 0, 1), tg, tg + dt.timedelta(days=1)}
    return out


def is_holiday_candidate(d: dt.date) -> bool:
    return d in holiday_candidates(d.year)


def third_friday(year: int, month: int) -> dt.date:
    return _nth(year, month, 4, 3)


def dst_transition_dates(start: pd.Timestamp, end: pd.Timestamp) -> list[dt.date]:
    """ET calendar dates (Sundays) on which the UTC offset changes, within [start, end] (UTC)."""
    days = pd.date_range(start.normalize() - pd.Timedelta(days=1), end.normalize() + pd.Timedelta(days=1), freq="D", tz="UTC")
    off = days.tz_convert(NY).map(lambda t: t.utcoffset())
    return [days[i].tz_convert(NY).date() for i in range(1, len(days)) if off[i] != off[i - 1]]
