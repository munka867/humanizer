"""Bar-by-bar event simulation. See docs/STRATEGY_SPEC.md sections 3.3-3.7 and 4.

Order of events inside bar i (open time T_i):
  1. A pending market entry decided at the close of bar i-1 is filled at open_i (or cancelled).
  2. An open position is managed against bar i: gap-through-stop at the open, time/eod exit at the
     open, then intrabar stop/target (stop first when both).
  3. The strategy is shown bar i (completed) and may emit a signal for bar i+1's open.
Prices in the trades table are NOMINAL; costs are a separate positive USD amount.
"""
from __future__ import annotations
import math
from collections import Counter
from dataclasses import dataclass, field
import numpy as np
import pandas as pd
from tradelab.contracts import BAR_COLUMNS, BAR_INDEX_NAME, TRADE_COLUMNS, CostModel, Instrument
from tradelab.backtest import costs as C
from tradelab.backtest.sessions import BAR_MIN, NY, hhmm
from tradelab.backtest.signals import Signal, StreamingStrategy

EPS = 1e-9


@dataclass(frozen=True)
class EngineConfig:
    instrument: Instrument
    cost: CostModel
    bar_minutes: int = 5
    eod_flat: str = "15:55"
    limit_through_ticks: float = 1.0
    sizing_mode: str = "fixed"          # "fixed" | "risk"
    fixed_qty: int = 1
    risk_budget_usd: float = 100.0      # only used when sizing_mode == "risk"
    max_qty: int = 1
    max_trades_per_day: int = 1


@dataclass
class BacktestResult:
    trades: pd.DataFrame
    signals: list[Signal]
    diagnostics: Counter = field(default_factory=Counter)


@dataclass
class _Pos:
    sig: Signal
    side: int
    qty: int
    entry_i: int
    entry_ts: pd.Timestamp
    entry_px: float
    stop: float
    target: float
    D: float
    date: object
    contract: object
    exit_min: int
    exit_is_time: bool


def validate_bars(bars: pd.DataFrame) -> None:
    missing = [c for c in BAR_COLUMNS if c not in bars.columns]
    if missing:
        raise ValueError(f"bars missing columns {missing}")
    idx = bars.index
    if not isinstance(idx, pd.DatetimeIndex) or idx.tz is None or str(idx.tz) != "UTC":
        raise ValueError("bars index must be a tz-aware UTC DatetimeIndex")
    if not idx.is_monotonic_increasing or not idx.is_unique:
        raise ValueError("bars index must be sorted ascending and unique")
    if bars[["open", "high", "low", "close"]].isna().any().any():
        raise ValueError("bars contain NaN prices")


def _round_tick(x: float, tick: float, mode: str) -> float:
    q = x / tick
    q = math.floor(q + EPS) if mode == "down" else math.ceil(q - EPS)
    return q * tick


def run_backtest(bars: pd.DataFrame, strategy: StreamingStrategy, cfg: EngineConfig) -> BacktestResult:
    validate_bars(bars)
    inst, cm = cfg.instrument, cfg.cost
    tick, pv = inst.tick_size, inst.point_value
    step = pd.Timedelta(minutes=cfg.bar_minutes)
    eod_min = hhmm(cfg.eod_flat)
    idx = bars.index
    O, H, L, Cl = (bars[c].to_numpy(float) for c in ("open", "high", "low", "close"))
    V = bars["volume"].to_numpy(float)
    contracts = bars["contract"].to_numpy(object) if "contract" in bars.columns else [None] * len(bars)
    n = len(bars)

    strategy.reset()
    diag: Counter = Counter()
    signals: list[Signal] = []
    rows: list[dict] = []
    trades_by_date: Counter = Counter()
    pending: Signal | None = None
    pos: _Pos | None = None

    def close_pos(exit_i: int, exit_px: float, exit_ts: pd.Timestamp, reason: str, kind: str, bars_held: int):
        nonlocal pos
        p = pos
        gross = p.side * (exit_px - p.entry_px) * p.qty * pv
        cost = C.trade_costs_usd(cm, inst, p.qty, kind)
        risk = p.qty * p.D * pv
        net = gross - cost
        rows.append({
            "trade_id": len(rows) + 1, "strategy": p.sig.strategy_id, "side": p.side, "qty": p.qty,
            "signal_ts": p.sig.signal_ts, "entry_ts": p.entry_ts, "exit_ts": exit_ts,
            "entry_px": p.entry_px, "exit_px": exit_px, "exit_reason": reason,
            "risk_usd": risk, "pnl_gross": gross, "costs": cost, "pnl_net": net,
            "r_multiple": net / risk, "bars_held": bars_held})
        pos = None

    for i in range(n):
        ts = idx[i]
        et = ts.tz_convert(NY)
        tod, date = et.hour * 60 + et.minute, et.date()
        o, h, l, c = O[i], H[i], L[i], Cl[i]
        ctr = contracts[i]
        entry_bar = False

        # -- 1. pending entry fills at this open
        if pending is not None:
            sig, pending = pending, None
            prev_ctr = contracts[i - 1] if i > 0 else None
            reason = None
            if ts != sig.signal_ts:
                reason = "cancel_gap"
            elif ctr != prev_ctr:
                reason = "cancel_contract"
            elif tod >= min(sig.time_exit_min, eod_min):
                reason = "cancel_after_exit_time"
            elif trades_by_date[date] >= cfg.max_trades_per_day or pos is not None:
                reason = "cancel_daily_limit"
            else:
                D = (sig.stop_px - o) if sig.side == -1 else (o - sig.stop_px)
                if D <= EPS:
                    reason = "cancel_open_through_stop"
                elif D < sig.min_risk_pts - EPS or D > sig.max_risk_pts + EPS:
                    reason = "cancel_risk_filter_at_fill"
            if reason is not None:
                diag[reason] += 1
            else:
                if cfg.sizing_mode == "fixed":
                    qty = cfg.fixed_qty
                elif cfg.sizing_mode == "risk":
                    qty = int(min(max(math.floor(cfg.risk_budget_usd / (D * pv)), 1), cfg.max_qty))
                else:
                    raise ValueError(f"sizing_mode {cfg.sizing_mode!r}")
                tgt_raw = o + sig.side * sig.target_r * D
                target = _round_tick(tgt_raw, tick, "down" if sig.side == -1 else "up")
                pos = _Pos(sig, sig.side, qty, i, ts, o, sig.stop_px, target, D, date, ctr,
                           min(sig.time_exit_min, eod_min), sig.time_exit_min <= eod_min)
                trades_by_date[date] += 1
                entry_bar = True

        # -- 2. manage position
        if pos is not None:
            p = pos
            if not entry_bar and (date != p.date or ctr != p.contract or ts != idx[i - 1] + step):
                diag["exit_discontinuity"] += 1
                close_pos(i - 1, Cl[i - 1], idx[i - 1] + step, "eod", C.MARKET_EXIT, i - 1 - p.entry_i + 1)
            else:
                s = p.side
                gap_stop = (o >= p.stop) if s == -1 else (o <= p.stop)
                if not entry_bar and gap_stop:
                    close_pos(i, o, ts, "stop", C.STOP_EXIT, i - p.entry_i)
                elif not entry_bar and tod >= p.exit_min:
                    close_pos(i, o, ts, "time" if p.exit_is_time else "eod", C.MARKET_EXIT, i - p.entry_i)
                else:
                    through = cfg.limit_through_ticks * tick
                    stop_hit = (h >= p.stop - EPS) if s == -1 else (l <= p.stop + EPS)
                    tgt_hit = (l <= p.target - through + EPS) if s == -1 else (h >= p.target + through - EPS)
                    held = i - p.entry_i + 1
                    if stop_hit and tgt_hit:
                        close_pos(i, p.stop, ts + step, "ambiguous_stop", C.STOP_EXIT, held)
                    elif stop_hit:
                        close_pos(i, p.stop, ts + step, "stop", C.STOP_EXIT, held)
                    elif tgt_hit:
                        close_pos(i, p.target, ts + step, "target", C.LIMIT_EXIT, held)
                    elif i == n - 1:
                        diag["exit_end_of_data"] += 1
                        close_pos(i, c, ts + step, "eod", C.MARKET_EXIT, held)

        # -- 3. strategy decides at the close of bar i
        sig = strategy.on_bar(ts, o, h, l, c, V[i], ctr)
        if sig is not None:
            signals.append(sig)
            if sig.signal_ts != ts + step:
                raise ValueError("signal_ts must equal the signal bar close time")
            if i < n - 1 and pos is None:
                pending = sig
            else:
                diag["signal_dropped_in_position_or_end"] += 1

    trades = pd.DataFrame(rows, columns=TRADE_COLUMNS)
    trades = trades.astype({"trade_id": "int64", "side": "int64", "qty": "int64", "bars_held": "int64"})
    return BacktestResult(trades=trades, signals=signals, diagnostics=diag + Counter(
        {("strategy_" + k): v for k, v in getattr(strategy, "diagnostics", Counter()).items()}))
