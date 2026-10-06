"""Shared hand-made bar builders (no dependency on the data/ package). Contains no tests."""
from __future__ import annotations
import datetime as dt
from zoneinfo import ZoneInfo
import pandas as pd
from tradelab.contracts import CostModel, Instrument
from tradelab.backtest.engine import EngineConfig
from tradelab.backtest.signals import Signal
from tradelab.backtest.sessions import hhmm

NYZ = ZoneInfo("America/New_York")
MES = Instrument("MES", 5.0, 0.25)
# round numbers on purpose: tick = $1.25
COST = CostModel(commission_per_side_usd=0.25, exchange_fee_per_side_usd=0.10, spread_ticks=1.0,
                 slippage_ticks_per_side=0.5, stop_extra_slippage_ticks=1.0, cost_multiplier=1.0)
ECFG = EngineConfig(instrument=MES, cost=COST)


def to_utc(date: str | dt.date, hm: str) -> pd.Timestamp:
    """ET wall-clock -> UTC timestamp (DST-aware via zoneinfo)."""
    return pd.Timestamp(f"{date} {hm}", tz=NYZ).tz_convert("UTC")


def _grid(date, start: str, end: str):
    t = hhmm(start)
    while t <= hhmm(end):
        yield f"{t // 60:02d}:{t % 60:02d}"
        t += 5


def flat(px=5000.0, half=1.0):
    return (px, px + half, px - half, px)


def rth_bars(date, contract="MESM6", start="09:30", end="15:55", over=None, skip=(), base=5000.0):
    over = over or {}
    rows = []
    for hm in _grid(date, start, end):
        if hm in skip:
            continue
        o, h, l, c = over.get(hm, flat(base))
        rows.append((to_utc(date, hm), o, h, l, c, 100.0, contract))
    return rows


def on_bars(date, contract="MESM6", over=None, skip=(), base=5000.0, on_hi=5005.0, on_lo=4995.0):
    """Overnight bars for trading date `date`: previous calendar day 18:00 .. 09:25 ET."""
    d = pd.Timestamp(date).date()
    prev = d - dt.timedelta(days=1)
    over = dict(over or {})
    rows = []
    for dd, a, b in ((prev, "18:00", "23:55"), (d, "00:00", "09:25")):
        for hm in _grid(dd, a, b):
            key = f"{dd}T{hm}"
            if key in skip:
                continue
            if key == f"{d}T02:00":
                v = (base, on_hi, base - 1, base)
            elif key == f"{d}T03:00":
                v = (base, base + 1, on_lo, base)
            else:
                v = over.get(key, flat(base))
            rows.append((to_utc(dd, hm), *v[:4], 100.0, contract))
    return rows


def prev_day_rows(date, contract="MESM6", skip=(), hi=5010.0, lo=4990.0):
    over = {"12:00": (5000, hi, 4999, 5000), "13:00": (5000, 5001, lo, 5000)}
    return rth_bars(date, contract, over=over, skip=skip)


def to_df(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["ts_open", "open", "high", "low", "close", "volume", "contract"])
    df = df.sort_values("ts_open").set_index("ts_open")
    df.index = pd.DatetimeIndex(df.index).tz_convert("UTC")
    return df.astype({"open": "float64", "high": "float64", "low": "float64", "close": "float64", "volume": "float64"})


def history(trade_date, prev_date, over=None, contract="MESM6", prev_contract=None, prev_skip=(),
            on_skip=(), today_skip=(), end="15:55"):
    """prev RTH (hi 5010 / lo 4990) + overnight (hi 5005 / lo 4995) + today's RTH with overrides {'HH:MM': ohlc}."""
    rows = prev_day_rows(prev_date, prev_contract or contract, skip=prev_skip)
    rows += on_bars(trade_date, contract, skip=on_skip)
    rows += rth_bars(trade_date, contract, end=end, over=over, skip=today_skip)
    return to_df(rows)


SHORT_SWEEP = (5003.0, 5006.0, 5002.5, 5003.75)   # sweeps ON high 5005; stop 5006.25; D_sig 2.5
LONG_SWEEP = (4997.0, 4997.5, 4994.0, 4996.25)    # sweeps ON low 4995; stop 4993.75; D_sig 2.5


class ScriptedStrategy:
    """Engine-only tests: emits a prepared Signal when the bar with a given UTC open is shown."""
    name = "scripted"

    def __init__(self, script: dict):
        self.script = script   # {UTC ts_open: dict of Signal kwargs}
        self.diagnostics = {}

    def reset(self):
        pass

    def on_bar(self, ts, o, h, l, c, v, contract):
        kw = self.script.get(ts)
        if kw is None:
            return None
        base = dict(strategy_id="scripted", ref_px=c, target_r=2.0, min_risk_pts=2.0, max_risk_pts=12.0,
                    time_exit_min=hhmm("11:30"), signal_ts=ts + pd.Timedelta(minutes=5))
        base.update(kw)
        return Signal(**base)


def one_day(date, over=None, **kw):
    return to_df(rth_bars(date, over=over, **kw))
