from decimal import Decimal as Dc

import pytest
from exec_common import *  # noqa
from tradelab.execution import orders as O
from tradelab.execution.broker import BrokerEvent, SimulatedBroker
from tradelab.execution.intents import make_intent_id
from tradelab.execution.modes import Mode


def count(svc, table):
    return svc.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_intent_id_is_stable_and_depends_on_identity_fields():
    a = make_intent_id("s", "v1", "sig", 1001, 1)
    assert a == make_intent_id("s", "v1", "sig", 1001, 1)
    assert len({a, make_intent_id("s", "v2", "sig", 1001, 1), make_intent_id("s", "v1", "sig2", 1001, 1),
                make_intent_id("s", "v1", "sig", 1002, 1), make_intent_id("s", "v1", "sig", 1001, -1)}) == 5


def test_duplicate_signal_is_idempotent(tmp_path):
    svc, b, _ = make(tmp_path)
    svc.start()
    r1, r2, r3 = svc.propose(entry()), svc.propose(entry()), svc.propose(entry())
    assert r1.status == "SUBMITTED" and r2.duplicate and r3.duplicate and r2.intent_id == r1.intent_id
    assert count(svc, "intents") == 1 and count(svc, "orders") == 1 and len(b.open_orders()) == 1


def test_duplicate_callback_cannot_create_a_second_intent_or_position(tmp_path):
    b = SimulatedBroker()
    b.duplicate_events = True
    svc, b, _ = make(tmp_path, broker=b)
    svc.start()
    svc.propose(entry())
    fill_all(svc, b)
    assert count(svc, "intents") == 1 and count(svc, "orders") == 1
    assert count(svc, "executions") == 1                       # duplicate fill callback deduped on exec_id
    assert svc.positions() == {SPY: Dc(10)}
    svc.process_events()
    assert svc.positions() == {SPY: Dc(10)}


def test_ack_is_not_a_fill_and_positions_come_only_from_fills(tmp_path):
    svc, b, _ = make(tmp_path)
    svc.start()
    r = svc.propose(entry())
    svc.process_events()
    assert svc.orders.get(r.client_order_id)["state"] == O.ACKED
    assert svc.positions() == {} and count(svc, "executions") == 0
    fill_all(svc, b)
    assert svc.orders.get(r.client_order_id)["state"] == O.FILLED
    assert svc.positions() == {SPY: Dc(10)}


def test_position_quantities_only_from_fills_partials_sum(tmp_path):
    b = SimulatedBroker()
    b.partial_fractions = [Dc("0.3"), Dc("0.7")]
    svc, b, _ = make(tmp_path, broker=b)
    svc.start()
    r = svc.propose(entry(qty=10))
    fill_all(svc, b)
    assert svc.positions() == {SPY: Dc(10)}
    assert [Dc(x["qty"]) for x in svc.conn.execute("SELECT qty FROM executions ORDER BY exec_id")] == [Dc(3), Dc(7)]
    # manual DB tampering of filled_qty does not change position (derived from executions)
    svc.conn.execute("UPDATE orders SET filled_qty='999'")
    assert svc.positions() == {SPY: Dc(10)}


def test_partial_fill_state_then_cancel_remainder(tmp_path):
    svc, b, _ = make(tmp_path)
    svc.start()
    r = svc.propose(entry(qty=10))
    coid = r.client_order_id
    svc.process_events()
    svc.orders.apply_execution({"exec_id": "E1", "client_order_id": coid, "con_id": SPY, "side": 1, "qty": 4, "price": 100, "ts": T0}, T0)
    assert svc.orders.get(coid)["state"] == O.PARTIAL
    svc.cancel_entry_orders("stratA")
    assert svc.orders.get(coid)["state"] == O.CANCEL_PENDING
    svc.process_events()
    assert svc.orders.get(coid)["state"] == O.CANCELED and svc.positions() == {SPY: Dc(4)}


def test_out_of_order_fill_before_ack_is_handled(tmp_path):
    b = SimulatedBroker()
    b.reverse_events = True
    svc, b, _ = make(tmp_path, broker=b)
    svc.start()
    r = svc.propose(entry())
    b.advance(SPY, [Dc(100)])
    svc.process_events()                              # events arrive as [fill, ack]
    row = svc.orders.get(r.client_order_id)
    assert row["state"] == O.FILLED and svc.positions() == {SPY: Dc(10)}
    assert not svc.incidents() or all(i["kind"] != "ILLEGAL_TRANSITION" for i in svc.incidents())


def test_illegal_transitions_are_rejected(tmp_path):
    svc, b, clock = make(tmp_path)
    svc.start()
    r = svc.propose(entry())
    fill_all(svc, b)
    with pytest.raises(O.IllegalTransition):
        svc.orders.transition(r.client_order_id, O.ACKED, T0)        # FILLED is terminal
    assert set(O.TRANSITIONS[O.FILLED]) == set() and O.NEW in O.TRANSITIONS
    assert O.FILLED not in O.TRANSITIONS[O.NEW]


def test_reject_event_moves_order_to_rejected_and_never_to_position(tmp_path):
    svc, b, _ = make(tmp_path)
    svc.start()
    b.script_submit("reject")
    r = svc.propose(entry())
    svc.process_events()
    assert svc.orders.get(r.client_order_id)["state"] == O.REJECTED and svc.positions() == {}


def test_fill_after_cancel_is_recorded_and_raises_incident(tmp_path):
    svc, b, _ = make(tmp_path)
    svc.start()
    r = svc.propose(entry())
    svc.process_events()
    svc.cancel_entry_orders("stratA")
    svc.process_events()
    assert svc.orders.get(r.client_order_id)["state"] == O.CANCELED
    svc.orders.apply_execution({"exec_id": "LATE", "client_order_id": r.client_order_id, "con_id": SPY, "side": 1, "qty": 10, "price": 100, "ts": T0}, T0)
    assert svc.positions() == {SPY: Dc(10)}                            # fills are truth
    assert any(i["kind"] == "FILL_AFTER_TERMINAL" for i in svc.incidents())


def test_cancel_race_where_fill_wins_ends_filled(tmp_path):
    b = SimulatedBroker()
    b.cancel_races = True
    svc, b, _ = make(tmp_path, broker=b)
    svc.start()
    r = svc.propose(entry())
    svc.process_events()
    svc.cancel_entry_orders("stratA")
    svc.process_events()
    assert svc.orders.get(r.client_order_id)["state"] == O.FILLED and svc.positions() == {SPY: Dc(10)}


def test_uncertain_submission_blocks_blind_retry(tmp_path):
    svc, b, _ = make(tmp_path)
    svc.start()
    b.script_submit("lost_ack")
    r1 = svc.propose(entry())
    assert r1.status == "UNCERTAIN"
    assert svc.orders.get(r1.client_order_id)["state"] == O.UNCERTAIN
    r2 = svc.propose(entry())                                         # same signal retried blindly
    assert r2.status == "UNCERTAIN" and r2.duplicate
    r3 = svc.propose(entry(signal_id="other"))                        # a different entry while uncertainty exists
    assert r3.status == "BLOCKED" and "UNCERTAIN_ORDER_BLOCKS_ENTRIES" in r3.reasons
    assert count(svc, "orders") == 1 and len(b.open_orders()) == 1    # exactly one order ever reached the broker


def test_uncertain_resolves_only_via_reconciliation(tmp_path):
    svc, b, _ = make(tmp_path)
    svc.start()
    b.script_submit("lost_ack")
    r = svc.propose(entry())
    rep = svc.reconcile()
    assert svc.orders.get(r.client_order_id)["state"] == O.ACKED and rep["ok"]
    assert svc.propose(entry(signal_id="n2")).status == "SUBMITTED"


def test_uncertain_not_at_broker_resolves_to_canceled_with_incident(tmp_path):
    svc, b, _ = make(tmp_path)
    svc.start()
    b.script_submit("timeout_not_sent")
    r = svc.propose(entry())
    assert r.status == "UNCERTAIN"
    svc.reconcile()
    assert svc.orders.get(r.client_order_id)["state"] == O.CANCELED
    assert any(i["kind"] == "ORDER_NOT_AT_BROKER" for i in svc.incidents())


def test_disconnect_during_submit_is_uncertain_not_rejected(tmp_path):
    svc, b, _ = make(tmp_path)
    svc.start()
    b.script_submit("disconnect")
    assert svc.propose(entry()).status == "UNCERTAIN"


def test_ack_timeout_moves_submitting_to_uncertain(tmp_path):
    svc, b, clock = make(tmp_path)
    svc.start()
    b.script_submit("ok")
    r = svc.propose(entry())              # ack event is queued but we never process it
    b._events.clear()                     # ack lost
    from datetime import timedelta
    clock.t += timedelta(seconds=11)
    assert svc.check_timeouts() == [r.client_order_id]
    assert svc.orders.get(r.client_order_id)["state"] == O.UNCERTAIN
