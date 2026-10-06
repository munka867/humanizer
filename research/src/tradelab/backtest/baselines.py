"""Baselines B0 (random entry) and B1 (30-min opening-range breakout). Streaming/causal."""
from __future__ import annotations
import datetime as dt
from collections import Counter
from dataclasses import dataclass
import numpy as np
import pandas as pd
from tradelab.contracts import Instrument
from tradelab.backtest.sessions import BAR_MIN, RTH_START, SessionTracker, hhmm
from tradelab.backtest.signals import Signal


@dataclass(frozen=True)
class RandomParams:
    target_r: float = 2.0
    seed: int = 20261006
    rep: int = 0
    first_signal_bar: str = "09:35"
    last_signal_bar: str = "10:55"
    time_exit: str = "11:30"
    min_risk_pts: float = 2.0
    max_risk_pts: float = 12.0
    stop_distances_pts: tuple[float, ...] | None = None  # empirical H1 distances; None -> uniform ticks


class RandomEntry:
    """B0. Per day: random direction, random signal bar, stop distance drawn from a supplied distribution.
    The RNG is seeded by (seed, rep, date) only, so it never depends on market data."""

    def __init__(self, instrument: Instrument, params: RandomParams = RandomParams(),
                 strategy_id: str = "B0_random_entry", require_contract: bool = True):
        self.inst, self.p, self.name = instrument, params, strategy_id
        self._first, self._last = hhmm(params.first_signal_bar), hhmm(params.last_signal_bar)
        self._exit = hhmm(params.time_exit)
        self._tracker = SessionTracker(require_contract=require_contract)
        self.reset()

    def reset(self) -> None:
        self._tracker.reset()
        self._plan_day: dt.date | None = None
        self._plan = None
        self.diagnostics: Counter = Counter()

    def _draw(self, d: dt.date):
        rng = np.random.default_rng([self.p.seed, self.p.rep, d.toordinal()])
        n_bars = (self._last - self._first) // BAR_MIN + 1
        bar_tod = self._first + BAR_MIN * int(rng.integers(0, n_bars))
        side = 1 if rng.random() < 0.5 else -1
        if self.p.stop_distances_pts:
            dist = float(rng.choice(np.asarray(self.p.stop_distances_pts, dtype=float)))
        else:
            t = self.inst.tick_size
            lo, hi = round(self.p.min_risk_pts / t), round(self.p.max_risk_pts / t)
            dist = float(rng.integers(lo, hi + 1)) * t
        return bar_tod, side, dist

    def on_bar(self, ts_open, o, h, l, c, v, contract) -> Signal | None:
        ctx = self._tracker.update(ts_open, h, l, contract)
        if ctx.phase != "RTH" or ctx.tod < self._first or ctx.tod > self._last:
            return None
        if self._plan_day != ctx.date:
            self._plan_day, self._plan = ctx.date, self._draw(ctx.date)
        bar_tod, side, dist = self._plan
        if ctx.tod != bar_tod:
            return None
        reason = ctx.skip_reason(need_prev=True, need_on=True)  # same eligible days as H1(levels=both)
        if reason is not None:
            self.diagnostics["skip_" + reason] += 1
            return None
        self.diagnostics["signals"] += 1
        return Signal(
            strategy_id=self.name, side=side, signal_ts=ts_open + pd.Timedelta(minutes=BAR_MIN),
            stop_px=c - side * dist, ref_px=c, target_r=self.p.target_r,
            min_risk_pts=self.p.min_risk_pts, max_risk_pts=self.p.max_risk_pts,
            time_exit_min=self._exit, meta={"dist": dist})


@dataclass(frozen=True)
class OrbParams:
    target_r: float = 2.0
    or_end: str = "10:00"                # OR = bars 09:30..09:55
    last_signal_bar: str = "10:55"
    stop_buffer_ticks: int = 1
    time_exit: str = "11:30"
    min_risk_pts: float = 2.0
    max_risk_pts: float = 12.0


class OpeningRangeBreakout:
    """B1. 30-minute OR; first bar CLOSING outside the range from 10:00 to 10:55 is the signal."""

    def __init__(self, instrument: Instrument, params: OrbParams = OrbParams(),
                 strategy_id: str = "B1_orb30", require_contract: bool = True):
        self.inst, self.p, self.name = instrument, params, strategy_id
        self._or_end, self._last = hhmm(params.or_end), hhmm(params.last_signal_bar)
        self._exit = hhmm(params.time_exit)
        self._tracker = SessionTracker(require_contract=require_contract)
        self.reset()

    def reset(self) -> None:
        self._tracker.reset()
        self._day: dt.date | None = None
        self._or_hi = self._or_lo = None
        self._or_n = 0
        self._used: dt.date | None = None
        self.diagnostics: Counter = Counter()

    def on_bar(self, ts_open, o, h, l, c, v, contract) -> Signal | None:
        ctx = self._tracker.update(ts_open, h, l, contract)
        if ctx.phase != "RTH":
            return None
        if self._day != ctx.date:
            self._day, self._or_hi, self._or_lo, self._or_n = ctx.date, None, None, 0
        if ctx.tod < self._or_end:
            self._or_hi = h if self._or_hi is None else max(self._or_hi, h)
            self._or_lo = l if self._or_lo is None else min(self._or_lo, l)
            self._or_n += 1
            return None
        if ctx.tod > self._last or self._used == ctx.date:
            return None
        need = (self._or_end - RTH_START) // BAR_MIN
        reason = ctx.skip_reason(need_prev=False, need_on=False)
        if reason is None and self._or_n != need:
            reason = "or_incomplete"
        if reason is not None:
            self.diagnostics["skip_" + reason] += 1
            return None
        if c > self._or_hi:
            side = 1
        elif c < self._or_lo:
            side = -1
        else:
            return None
        self._used = ctx.date
        tick = self.inst.tick_size
        stop = self._or_lo - self.p.stop_buffer_ticks * tick if side == 1 else self._or_hi + self.p.stop_buffer_ticks * tick
        d_sig = abs(c - stop)
        if d_sig < self.p.min_risk_pts - 1e-9 or d_sig > self.p.max_risk_pts + 1e-9:
            self.diagnostics["rejected_risk_filter"] += 1
            return None
        self.diagnostics["signals"] += 1
        return Signal(
            strategy_id=self.name, side=side, signal_ts=ts_open + pd.Timedelta(minutes=BAR_MIN),
            stop_px=stop, ref_px=c, target_r=self.p.target_r,
            min_risk_pts=self.p.min_risk_pts, max_risk_pts=self.p.max_risk_pts,
            time_exit_min=self._exit, meta={"or_hi": self._or_hi, "or_lo": self._or_lo})
