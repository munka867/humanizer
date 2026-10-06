"""ET session logic, streaming (causal). Converts UTC -> America/New_York at use only."""
from __future__ import annotations
import datetime as dt
from dataclasses import dataclass, field
import pandas as pd

NY = "America/New_York"
RTH_START = 9 * 60 + 30
RTH_END = 16 * 60
ON_START = 18 * 60
BAR_MIN = 5
RTH_BARS = (RTH_END - RTH_START) // BAR_MIN  # 78
MAX_ON_GAP_MIN = 60
MAX_PREV_AGE_DAYS = 4


def hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def is_half_day(d: dt.date) -> bool:
    """US equity early-close rule (no other holiday calendar is used)."""
    if d.weekday() >= 5:
        return False
    if d.month == 12 and d.day == 24:
        return True
    if d.month == 7 and d.day == 3 and d.weekday() <= 3:
        return True
    if d.month == 11 and d.weekday() == 4:  # Friday after Thanksgiving (4th Thursday)
        thursday = d - dt.timedelta(days=1)
        return thursday.weekday() == 3 and 22 <= thursday.day <= 28
    return False


@dataclass
class _Rth:
    date: dt.date
    hi: float
    lo: float
    n: int
    contiguous: bool
    expected_tod: int
    contracts: set
    roll_flag: bool = False


@dataclass
class _On:
    target: dt.date
    hi: float
    lo: float
    n: int
    first_tod: int
    last_ts: pd.Timestamp
    last_tod: int
    max_gap_min: float
    contracts: set


@dataclass(frozen=True)
class PrevSession:
    date: dt.date
    hi: float
    lo: float
    complete: bool
    contracts: frozenset
    roll_flag: bool


@dataclass(frozen=True)
class BarCtx:
    """What the tracker knows at the close of the current bar (all from past bars)."""
    et: pd.Timestamp
    date: dt.date
    tod: int                     # ET minutes since midnight of bar OPEN
    phase: str                   # 'RTH' | 'ON' | 'OTHER'
    rth_contiguous: bool         # all 5-min bars 09:30..current present (RTH bars only)
    roll_flag: bool
    prev: PrevSession | None
    on_hi: float | None
    on_lo: float | None
    on_valid: bool
    half_day: bool
    prev_age_days: int | None

    def skip_reason(self, need_prev: bool, need_on: bool) -> str | None:
        if self.half_day:
            return "half_day"
        if self.phase == "RTH" and not self.rth_contiguous:
            return "data_gap_today"
        if self.prev is None:
            return "no_prev_session"
        if self.roll_flag:
            return "roll_day"
        if self.prev.roll_flag:
            return "day_after_roll"
        if self.prev_age_days is not None and self.prev_age_days > MAX_PREV_AGE_DAYS:
            return "prev_stale"
        if need_prev and not self.prev.complete:
            return "prev_incomplete"
        if need_on and not self.on_valid:
            return "overnight_invalid"
        return None


class SessionTracker:
    """Feed every bar in order via update(); returns a BarCtx using ONLY bars seen so far."""

    def __init__(self, require_contract: bool = True):
        self.require_contract = require_contract
        self.reset()

    def reset(self) -> None:
        self._rth: _Rth | None = None
        self._on: _On | None = None
        self._prev: PrevSession | None = None

    @staticmethod
    def _tod_date(ts_utc: pd.Timestamp) -> tuple[pd.Timestamp, int, dt.date]:
        et = ts_utc.tz_convert(NY)
        return et, et.hour * 60 + et.minute, et.date()

    def update(self, ts_utc: pd.Timestamp, h: float, l: float, contract) -> BarCtx:
        et, tod, d = self._tod_date(ts_utc)
        if contract is None or (isinstance(contract, float) and contract != contract):
            if self.require_contract:
                raise ValueError("bar has no contract label; roll detection impossible "
                                 "(pass require_contract=False to accept at your own risk)")
            contract = "UNKNOWN"
        phase = "RTH" if RTH_START <= tod < RTH_END else ("ON" if (tod >= ON_START or tod < RTH_START) else "OTHER")

        if phase == "ON":
            target = d if tod < RTH_START else d + dt.timedelta(days=1)
            on = self._on
            if on is None or on.target != target:
                self._on = _On(target, h, l, 1, tod, ts_utc, tod, 0.0, {contract})
            else:
                gap = (ts_utc - on.last_ts).total_seconds() / 60.0
                on.max_gap_min = max(on.max_gap_min, gap)
                on.hi, on.lo = max(on.hi, h), min(on.lo, l)
                on.n += 1
                on.last_ts, on.last_tod = ts_utc, tod
                on.contracts.add(contract)
        elif phase == "RTH":
            if self._rth is None or self._rth.date != d:
                if self._rth is not None:
                    r = self._rth
                    self._prev = PrevSession(
                        r.date, r.hi, r.lo,
                        complete=(r.n == RTH_BARS and r.contiguous and len(r.contracts) == 1),
                        contracts=frozenset(r.contracts), roll_flag=r.roll_flag)
                contracts = set(self._on.contracts) if (self._on and self._on.target == d) else set()
                self._rth = _Rth(d, h, l, 0, tod == RTH_START, tod, contracts)
                self._rth.hi, self._rth.lo = float("-inf"), float("inf")
            r = self._rth
            if tod != r.expected_tod:
                r.contiguous = False
            r.expected_tod = tod + BAR_MIN
            r.n += 1
            r.hi, r.lo = max(r.hi, h), min(r.lo, l)
            r.contracts.add(contract)

        # day-level facts for date d (RTH or ON-of-target)
        day = d if phase != "ON" or tod < RTH_START else d + dt.timedelta(days=1)
        rth = self._rth if (self._rth is not None and self._rth.date == day) else None
        on = self._on if (self._on is not None and self._on.target == day) else None
        prev = self._prev if rth is not None else (self._prev_for_on(day))
        contracts_today = set()
        if on:
            contracts_today |= on.contracts
        if rth:
            contracts_today |= rth.contracts
        if prev is None:
            roll = len(contracts_today) > 1   # first day in data: skipped anyway via no_prev_session
        else:
            roll = len(contracts_today) != 1 or (len(contracts_today) == 1 and frozenset(contracts_today) != prev.contracts)
        if rth is not None:
            rth.roll_flag = roll
        on_valid = bool(on and on.first_tod == ON_START and on.max_gap_min <= MAX_ON_GAP_MIN
                        and len(on.contracts) == 1 and on.last_tod == RTH_START - BAR_MIN)
        return BarCtx(
            et=et, date=d, tod=tod, phase=phase,
            rth_contiguous=bool(rth.contiguous) if rth else True,
            roll_flag=roll, prev=prev,
            on_hi=on.hi if on else None, on_lo=on.lo if on else None, on_valid=on_valid,
            half_day=is_half_day(day),
            prev_age_days=(day - prev.date).days if prev else None)

    def _prev_for_on(self, day: dt.date) -> PrevSession | None:
        """During ON of `day` the last RTH accumulator is still the previous session; snapshot it."""
        r = self._rth
        if r is None or r.date >= day:
            return self._prev
        return PrevSession(r.date, r.hi, r.lo,
                           complete=(r.n == RTH_BARS and r.contiguous and len(r.contracts) == 1),
                           contracts=frozenset(r.contracts), roll_flag=r.roll_flag)
