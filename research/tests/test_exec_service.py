import dataclasses
from decimal import Decimal as Dc
from types import SimpleNamespace

import pandas as pd
import pytest
from exec_common import *  # noqa
from tradelab.execution import orders as O
from tradelab.execution.broker import SimulatedBroker
from tradelab.execution.intents import Proposal
from tradelab.execution.modes import Mode
from tradelab.execution.replay import ReplayClock, ReplayRunner
from tradelab.execution.risk import RiskEngine
from tradelab.execution.service import ExecutionService, WriterLockHeld
from tradelab.execution.strategy_adapter import InstrumentSpec, SignalTranslator


def protective(svc, qty=10, stop="95", sid="p1", side=-1):
    return svc.propose(Proposal("stratA", "v1", sid, SPY, "SPY", side, Dc(qty), "STP", stop_px=Dc(stop), purpose="protective"))


def long_with_stop(svc, b):
    svc.start()
    svc.propose(entry())
    fill_all(svc, b)
    r = protective(svc)
    svc.process_events()
    assert svc.positions() == {SPY: Dc(10)} and r.status == "SUBMITTED"
    return r


def test_restart_reconciles_positions_and_orders(tmp_path):
    svc1, b, clock = make(tmp_path)
    svc1.start()
    svc1.propose(entry())                         # filled at broker...
    resting = svc1.propose(entry(signal_id="s2", qty=5, stop="99"))
    b.advance(SPY, [Dc(100)])                     # both fill at the broker; the process "crashes" before processing events
    svc1.propose(Proposal("stratA", "v1", "lim", SPY, "SPY", 1, Dc(3), "LMT", limit_px=Dc("99.95"), stop_px=Dc(98)))   # resting order
    svc1.close()
    b.restart()                                   # undelivered events are gone; broker keeps orders/fills
    svc2, _, _ = make(tmp_path, broker=b, clock=clock)
    rep = svc2.start()
    assert rep["ok"], rep
    assert svc2.positions() == {SPY: Dc(15)} == b.positions()
    assert svc2.orders.get(resting.client_order_id)["state"] == O.FILLED
    assert [r["state"] for r in svc2.orders.by_state(O.ACKED, O.SUBMITTING)] == [O.ACKED]   # the resting limit order, now ACKED
    assert svc2.propose(entry(signal_id="new")).status == "SUBMITTED"


def test_startup_reconciliation_runs_first_and_entries_wait_for_it(tmp_path):
    svc, b, _ = make(tmp_path)
    r = svc.propose(entry())
    assert r.status == "BLOCKED" and "RECONCILIATION_NOT_CLEAN" in r.reasons
    svc.start()
    assert svc.propose(entry(signal_id="x")).status == "SUBMITTED"


def test_unknown_broker_state_blocks_entries_but_keeps_protective_orders(tmp_path):
    svc1, b, clock = make(tmp_path)
    long_with_stop(svc1, b)
    svc1.close()
    b.inject_foreign_order(QQQ, -1, 5)
    b.inject_foreign_position(QQQ, 5)
    svc2, _, _ = make(tmp_path, broker=b, clock=clock)
    rep = svc2.start()
    assert not rep["ok"]
    kinds = {i["kind"] for i in svc2.incidents()}
    assert {"UNKNOWN_BROKER_ORDER", "POSITION_MISMATCH"} <= kinds
    blocked = svc2.propose(entry(signal_id="new"))
    assert blocked.status == "BLOCKED" and "RECONCILIATION_NOT_CLEAN" in blocked.reasons
    # our protective stop is still working at the broker (reconciliation never cancels), and a reducing exit is still allowed
    assert any(o.order_type == "STP" and o.con_id == SPY for o in b.open_orders())
    ex = svc2.propose(Proposal("stratA", "v1", "x1", SPY, "SPY", -1, Dc(10), "MKT", purpose="exit"))
    assert ex.status == "SUBMITTED"


def test_unknown_execution_at_broker_blocks_entries(tmp_path):
    svc, b, _ = make(tmp_path)
    b._fills.append(__import__("tradelab.execution.broker", fromlist=["BrokerEvent"]).BrokerEvent(
        "fill", "NOT-OURS", "SIM-9", "EX-77", SPY, 1, Dc(1), Dc(100), T0))
    b._pos[SPY] = Dc(1)
    rep = svc.start()
    assert not rep["ok"] and any(i["kind"] == "UNKNOWN_BROKER_EXECUTION" for i in svc.incidents())
    assert svc.propose(entry()).status == "BLOCKED"


def test_manual_adjustment_then_reconcile_clears_block(tmp_path):
    svc, b, _ = make(tmp_path)
    b.inject_foreign_position(SPY, 7)
    assert not svc.start()["ok"]
    svc.record_manual_adjustment(SPY, Dc(7), "operator", "pre-existing holding")
    assert svc.reconcile()["ok"]
    assert svc.propose(entry()).status == "SUBMITTED"


def test_pause_new_entries_keeps_protective_exits(tmp_path):
    svc, b, _ = make(tmp_path)
    long_with_stop(svc, b)
    svc.pause_new_entries("operator test")
    assert any(o.order_type == "STP" for o in b.open_orders())                   # not cancelled
    blocked = svc.propose(entry(signal_id="e2"))
    assert blocked.status == "BLOCKED" and "PAUSED_NEW_ENTRIES" in blocked.reasons
    # a new protective/reducing order is still allowed while paused
    r = protective(svc, qty=5, stop="94", sid="p2")
    assert r.status == "SUBMITTED"
    exit_ = svc.propose(Proposal("stratA", "v1", "x", SPY, "SPY", -1, Dc(5), "MKT", purpose="exit"))
    assert exit_.status == "SUBMITTED"
    fill_all(svc, b)
    assert svc.positions() == {SPY: Dc(5)}


def test_cancel_entry_orders_only_touches_strategy_owned_entry_orders(tmp_path):
    svc, b, _ = make(tmp_path)
    svc.start()
    svc.propose(entry())
    fill_all(svc, b)
    stop = protective(svc)
    lim = svc.propose(Proposal("stratA", "v1", "lim1", SPY, "SPY", 1, Dc(2), "LMT", limit_px=Dc("99.95"), stop_px=Dc(98)))
    other = svc.propose(dataclasses.replace(entry(signal_id="o1", qty=2), strategy_id="stratB", order_type="LMT", limit_px=Dc("99.95")))
    b.inject_foreign_order(QQQ, 1, 3, "LMT")
    svc.process_events()
    done = svc.cancel_entry_orders("stratA")
    svc.process_events()
    assert done == [lim.client_order_id]
    assert svc.orders.get(lim.client_order_id)["state"] == O.CANCELED
    assert svc.orders.get(stop.client_order_id)["state"] == O.ACKED               # protective untouched
    assert svc.orders.get(other.client_order_id)["state"] == O.ACKED             # other strategy's entry untouched
    assert any(o.client_order_id.startswith("FOREIGN") for o in b.open_orders())  # foreign order untouched


def test_flatten_reports_completion_and_cancels_orphan_protective(tmp_path):
    svc, b, _ = make(tmp_path)
    stop = long_with_stop(svc, b)
    b.auto_fill_market = True
    rep = svc.flatten_strategy_positions("stratA")
    assert rep["complete"] and rep["residual_exposure"] == {} and rep["instruments"][SPY]["residual_position"] == "0"
    svc.process_events()
    assert svc.orders.get(stop.client_order_id)["state"] == O.CANCELED          # orphan protective cleaned once flat
    assert svc.positions() == {} == b.positions()


def test_flatten_reports_residual_exposure_on_failure(tmp_path):
    svc, b, _ = make(tmp_path)
    long_with_stop(svc, b)
    b.script_submit("reject")
    rep = svc.flatten_strategy_positions("stratA")
    assert not rep["complete"] and rep["residual_exposure"] == {str(SPY): "10"}
    assert rep["instruments"][SPY]["order_state"] == O.REJECTED
    b.script_submit("lost_ack")
    rep2 = svc.flatten_strategy_positions("stratA")
    assert not rep2["complete"] and rep2["instruments"][SPY]["result"] == "UNCERTAIN"
    rep3 = svc.flatten_strategy_positions("stratA")                               # uncertain order on the same instrument: no blind double-close
    assert rep3["instruments"][SPY]["result"] == "BLOCKED" and "UNCERTAIN_ORDER_SAME_INSTRUMENT" in rep3["instruments"][SPY]["reasons"]


def test_flatten_with_delayed_fill_reports_pending_not_success(tmp_path):
    b = SimulatedBroker()
    svc, b, _ = make(tmp_path, broker=b)
    long_with_stop(svc, b)
    b.fill_delay_steps = 3
    rep = svc.flatten_strategy_positions("stratA")
    assert not rep["complete"] and rep["instruments"][SPY]["filled"] == "0" and rep["instruments"][SPY]["residual_position"] == "10"


def test_emergency_stop_state_persists_across_restart(tmp_path):
    svc, b, clock = make(tmp_path)
    svc.start()
    svc.propose(entry(qty=2, stop="99"))
    fill_all(svc, b)
    svc.propose(Proposal("stratA", "v1", "l", SPY, "SPY", 1, Dc(1), "LMT", limit_px=Dc("99.95"), stop_px=Dc(98)))
    svc.process_events()
    cancelled = svc.emergency_stop("test", ["stratA"])
    assert len(cancelled) == 1 and svc.emergency
    svc.close()
    svc2, _, _ = make(tmp_path, broker=b, clock=clock)
    assert svc2.emergency
    svc2.start()
    r = svc2.propose(entry(signal_id="after"))
    assert r.status == "BLOCKED" and "EMERGENCY_STOP" in r.reasons
    assert svc2.propose(Proposal("stratA", "v1", "xx", SPY, "SPY", -1, Dc(2), "MKT", purpose="exit")).status == "SUBMITTED"   # reducing still allowed
    with pytest.raises(RuntimeError):
        svc2.resume_new_entries("op")
    svc2.clear_emergency("op", "ok")
    assert not svc2.emergency


def test_pause_state_persists_across_restart(tmp_path):
    svc, b, clock = make(tmp_path)
    svc.pause_new_entries("x")
    svc.close()
    svc2, _, _ = make(tmp_path, broker=b, clock=clock)
    svc2.start()
    assert svc2.paused and svc2.propose(entry()).status == "BLOCKED"


def test_second_writer_is_refused(tmp_path):
    svc, b, clock = make(tmp_path)
    with pytest.raises(WriterLockHeld):
        make(tmp_path, broker=b, clock=clock)
    svc.close()
    svc3, _, _ = make(tmp_path, broker=b, clock=clock)          # released on close
    svc3.close()


def test_missing_broker_account_data_blocks_entries(tmp_path):
    class NoAccount(SimulatedBroker):
        def account(self):
            from tradelab.execution.broker import BrokerDisconnected
            raise BrokerDisconnected("down")
    svc, b, _ = make(tmp_path, broker=NoAccount())
    svc.start()
    r = svc.propose(entry())
    assert r.status == "BLOCKED" and any(x.startswith("MISSING_INPUT") for x in r.reasons)


def test_stale_account_snapshot_blocks_entries(tmp_path):
    b = SimulatedBroker()
    b.account_stale = True
    svc, b, _ = make(tmp_path, broker=b)
    svc.start()
    assert "STALE_ACCOUNT" in svc.propose(entry()).reasons


def test_stale_quotes_block_entries(tmp_path):
    from datetime import timedelta
    svc, b, clock = make(tmp_path)
    svc.start()
    svc.quote_source = lambda cid: good_quote(clock() - timedelta(minutes=5))
    assert "STALE_QUOTE" in svc.propose(entry()).reasons


def test_risk_decision_is_logged_with_policy_hash(tmp_path):
    svc, b, _ = make(tmp_path)
    svc.start()
    r = svc.propose(entry())
    row = svc.conn.execute("SELECT * FROM risk_decisions WHERE intent_id=?", (r.intent_id,)).fetchone()
    assert row["allowed"] == 1 and row["policy_hash"] == svc.risk.policy.sha256
    blocked = svc.propose(entry(signal_id="big", qty=500))
    row = svc.conn.execute("SELECT * FROM risk_decisions WHERE intent_id=?", (blocked.intent_id,)).fetchone()
    assert row["allowed"] == 0 and "MAX_POSITION" in row["reasons"]


# ------------------------------------------------------------------ replay
class Scripted:
    name = "scripted"

    def reset(self):
        self.i = 0

    def on_bar(self, bar):
        self.i += 1
        if self.i % 15 == 3:
            return SimpleNamespace(strategy_id="stratA", side=1, signal_ts=bar.name + pd.Timedelta(minutes=5), stop_px=float(bar["close"]) - 1.5)
        return None


def bars():
    idx = pd.date_range("2026-01-05 14:35", periods=60, freq="5min", tz="UTC")
    base = [100 + 0.1 * i + (0.8 if i % 7 == 0 else -0.3) for i in range(60)]
    return pd.DataFrame({"open": base, "high": [x + 0.4 for x in base], "low": [x - 0.9 for x in base], "close": [x + 0.1 for x in base]}, index=idx)


def run_replay(tmp_path, name):
    clock = ReplayClock(T0)
    b = SimulatedBroker(seed=7)
    svc = ExecutionService(str(tmp_path / name), Mode.REPLAY, b, RiskEngine.from_file(POLICY), lambda c: None, clock, margin_hook=lambda p: True)
    runner = ReplayRunner(svc, b, clock, Scripted(), SignalTranslator(InstrumentSpec(SPY, "SPY"), "v1", lambda s: Dc(10)))
    res = runner.run(bars())
    ledger = [tuple(r) for r in svc.conn.execute("SELECT strategy_id,signal_id,con_id,side,qty,purpose,status FROM intents ORDER BY intent_id")]
    states = [tuple(r) for r in svc.conn.execute("SELECT client_order_id,state,filled_qty FROM orders ORDER BY client_order_id")]
    return res, ledger, states


def test_fixed_seed_replay_is_reproducible(tmp_path):
    r1, l1, s1 = run_replay(tmp_path, "a.db")
    r2, l2, s2 = run_replay(tmp_path, "b.db")
    assert r1.n_signals == 4 and len(r1.executions) > 0
    assert (r1.executions, l1, s1, r1.positions) == (r2.executions, l2, s2, r2.positions)
    assert any(x[5] == "protective" for x in l1)                 # protective stops were placed after entry fills
    assert all(x[0] in ("SUBMITTED", "BLOCKED", "DUPLICATE") for x in [(r.status,) for r in r1.results])
