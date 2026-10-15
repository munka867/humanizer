"""Deterministic ReplayRunner: recorded bars -> strategy.on_bar -> translator -> ExecutionService (risk, ledger) -> SimulatedBroker.
Fills happen at the NEXT bar's open (market) / along an O,adverse,favourable,C path (stops) - the same timing contract as the backtester.
No randomness is used except the broker's seeded RNG (kept for scripted-behaviour extensions); same inputs + seed => identical ledger."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import pandas as pd

from tradelab.execution.broker import SimulatedBroker
from tradelab.execution.common import D
from tradelab.execution.intents import PROTECTIVE, Proposal, make_intent_id
from tradelab.execution.risk import Quote
from tradelab.execution.service import ExecutionService
from tradelab.execution.strategy_adapter import InstrumentSpec, SignalTranslator


class ReplayClock:
    def __init__(self, t: datetime):
        self.t = t

    def __call__(self) -> datetime:
        return self.t


@dataclass
class ReplayResult:
    n_signals: int
    results: list
    positions: dict
    executions: list


class ReplayRunner:
    def __init__(self, service: ExecutionService, broker: SimulatedBroker, clock: ReplayClock, strategy, translator: SignalTranslator,
                 bar_seconds: int = 300, spread_bps: Decimal = Decimal("2"), protect: bool = True):
        self.svc, self.broker, self.clock, self.strategy, self.tr = service, broker, clock, strategy, translator
        self.bar_seconds, self.spread, self.protect = bar_seconds, D(spread_bps), protect
        self._last_quote: Quote | None = None
        service.quote_source = self._quote
        self._pending_stops: list[tuple] = []

    def _quote(self, con_id):
        return self._last_quote if con_id == self.tr.inst.con_id else None

    def run(self, bars: pd.DataFrame) -> ReplayResult:
        cid = self.tr.inst.con_id
        self.strategy.reset()
        self.svc.start()
        results, nsig = [], 0
        for ts, bar in bars.iterrows():
            t_open = ts.to_pydatetime()
            self.clock.t = t_open
            self.broker.set_time(t_open)
            pos = self.svc.positions().get(cid, Decimal(0))
            o, h, l, c = (D(bar[k]) for k in ("open", "high", "low", "close"))
            path = [o, l, h, c] if pos >= 0 else [o, h, l, c]
            self.broker.advance(cid, path, t_open)
            close_t = t_open + pd.Timedelta(seconds=self.bar_seconds)
            self.clock.t = close_t
            self.broker.set_time(self.clock.t)
            self.svc.process_events()
            self._place_protectives()
            half = c * self.spread / Decimal(20000)
            self._last_quote = Quote(c - half, c + half, c, self.clock.t)
            bar_s = bar.copy()
            bar_s.name = ts
            sig = self.strategy.on_bar(bar_s)
            if sig is not None:
                nsig += 1
                results.append(self.svc.propose(self.tr.entry(sig)))
                self._remember_stop(sig)
        self.broker.advance(cid, [D(bars.iloc[-1]["close"])])
        self.svc.process_events()
        ex = [dict(r) for r in self.svc.conn.execute("SELECT exec_id,con_id,side,qty,price FROM executions ORDER BY exec_id")]
        return ReplayResult(nsig, results, self.svc.positions(), ex)

    def _remember_stop(self, sig):
        if self.protect and getattr(sig, "stop_px", None) is not None:
            self._pending_stops.append((self.tr.entry(sig), D(sig.stop_px)))

    def _place_protectives(self):
        keep = []
        for prop, stop in self._pending_stops:
            iid = make_intent_id(prop.strategy_id, prop.strategy_version, prop.signal_id, prop.con_id, prop.side, prop.purpose)
            rows = self.svc.orders.for_intent(iid)
            if not rows:
                continue                      # entry was blocked/shadowed: nothing to protect
            st = rows[-1]["state"]
            if st == "FILLED":
                self.svc.propose(Proposal(prop.strategy_id, prop.strategy_version, prop.signal_id + ":stop", prop.con_id, prop.symbol,
                                          -prop.side, D(rows[-1]["filled_qty"]), "STP", stop_px=stop, purpose=PROTECTIVE))
            elif st in ("SUBMITTING", "ACKED", "PARTIALLY_FILLED"):
                keep.append((prop, stop))
        self._pending_stops = keep
