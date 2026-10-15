"""Helpers for tests/test_exec_*.py (SIMULATED broker only)."""
from datetime import datetime, timezone
from decimal import Decimal as Dc
from pathlib import Path

from tradelab.execution.broker import SimulatedBroker
from tradelab.execution.intents import Proposal
from tradelab.execution.modes import Mode
from tradelab.execution.risk import Quote, RiskEngine, RiskInputs
from tradelab.execution.service import ExecutionService

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "configs" / "risk_policy.example.yaml"
T0 = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)      # 10:00 New York (EST): inside the 09:35-15:55 session
SPY, QQQ = 1001, 1002


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def good_quote(t=T0, px="100"):
    p = Dc(px)
    return Quote(p - Dc("0.01"), p + Dc("0.01"), p, t)


def base_inputs(now=T0, **kw):
    x = RiskInputs(now_utc=now, equity=Dc(10000), available_funds=Dc(9000), account_ts=now, peak_equity=Dc(10000),
                   day_start_equity=Dc(10000), positions={}, quotes={SPY: good_quote(now), QQQ: good_quote(now)}, shortable={},
                   correlations={(SPY, QQQ): Dc("0.9")}, recent_order_times=(), margin_ok=True)
    for k, v in kw.items():
        setattr(x, k, v)
    return x


def entry(signal_id="s1", qty=10, stop="99", con=SPY, side=1, **kw):
    return Proposal("stratA", "v1", signal_id, con, "SPY", side, Dc(qty), "MKT", stop_px=None if stop is None else Dc(stop), **kw)


def make(tmp_path, mode=Mode.DEMO, broker=None, name="led.db", clock=None, quote_px="100", margin=True, **kw):
    clock = clock or Clock()
    broker = broker or SimulatedBroker()
    broker.set_time(clock())
    px = {"v": quote_px}
    svc = ExecutionService(str(tmp_path / name), mode, broker, RiskEngine.from_file(POLICY),
                           lambda cid: good_quote(clock(), px["v"]), clock, margin_hook=(lambda p: margin), lock_dir=str(tmp_path / "locks"), **kw)
    svc.px = px
    return svc, broker, clock


def fill_all(svc, broker, con=SPY, price="100"):
    broker.advance(con, [Dc(price)])
    svc.process_events()
