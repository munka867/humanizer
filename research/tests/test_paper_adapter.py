import dataclasses
import importlib.util
from datetime import timedelta
from decimal import Decimal as Dc

import pytest
from paper_common import *  # noqa
from fake_ib_orders import MODULE, FakeIB, foreign_trade
from tradelab.brokers.ibkr_paper import errors as E
from tradelab.brokers.ibkr_paper import guard as G
from tradelab.brokers.ibkr_paper.adapter import AdapterUnavailable, ContractSpec, IbkrPaperBroker
from tradelab.execution import orders as O
from tradelab.execution.broker import BrokerRefused, OrderRequest
from tradelab.execution.intents import Proposal
from tradelab.execution.permit import PaperOrderPermit, PermitError, mint_permit


def rows(svc, q):
    return [dict(r) for r in svc.conn.execute(q)]


# ------------------------------------------------------------------ guard
@pytest.mark.parametrize("host,port,cid", [("192.168.1.5", 7497, 21), ("localhost", 7497, 21), ("0.0.0.0", 7497, 21), ("127.0.0.1", 7496, 21),
                                           ("127.0.0.1", 4001, 21), ("127.0.0.1", 7500, 21), ("127.0.0.1", 7497, 1), ("127.0.0.1", 7497, 15),
                                           ("127.0.0.1", "7497", 21), ("127.0.0.1", True, 21)])
def test_guard_refuses_bad_host_port_or_client_id(host, port, cid):
    with pytest.raises(G.GuardError):
        G.check_target(host, port, cid)
    if cid == 21:                                                          # the adapter has no clientId parameter at all: it is fixed
        with pytest.raises(G.GuardError):
            IbkrPaperBroker({}, ib_module=MODULE, host=host, port=port)


def test_guard_allows_only_the_two_paper_ports_on_loopback():
    assert G.check_target("127.0.0.1", 7497, 21) and G.check_target("::1", 4002, 21)


def test_connect_uses_fixed_client_id_paper_port_and_not_readonly(tmp_path):
    svc, ad, fake, *_ = make_paper(tmp_path)
    assert fake.connect_args == {"host": "127.0.0.1", "port": 7497, "clientId": 21, "readonly": False}
    assert not hasattr(ad, "host") or ad.host == "127.0.0.1"


def test_non_du_account_is_disconnected_and_refused():
    fake = FakeIB()
    fake.accounts = ["U1234567"]
    mod = type("M", (), {"IB": staticmethod(lambda: fake), "Stock": MODULE.Stock, "Order": MODULE.Order})
    ad = IbkrPaperBroker({SPY: ContractSpec("SPY")}, ib_module=mod)
    with pytest.raises(G.GuardError, match="DU"):
        ad.connect()
    assert not fake.connected and not ad.connected


def test_mixed_accounts_refused_and_expected_account_must_be_present():
    for accounts, expected in ((["DU1", "U2"], None), ([], None), (["DU1"], "DU2")):
        fake = FakeIB()
        fake.accounts = accounts
        mod = type("M", (), {"IB": staticmethod(lambda f=fake: f), "Stock": MODULE.Stock, "Order": MODULE.Order})
        with pytest.raises(G.GuardError):
            IbkrPaperBroker({}, ib_module=mod, expected_account=expected).connect()
        assert not fake.connected


def test_account_mismatch_between_order_and_connection_refuses_and_disconnects(tmp_path):
    # the service believes the broker's account is DUFAKE002 and approval binds DUFAKE002, but this login/adapter is DUFAKE001
    fake = FakeIB()
    fake.accounts = ["DUFAKE001", "DUFAKE002"]
    svc, ad, fake, *_ = make_paper(tmp_path, fake=fake, expected="DUFAKE001", proxy=True, account_override="DUFAKE002")
    svc.start()
    r = svc.propose(entry())
    assert r.status == "BLOCKED" and any("differs from the account bound" in x for x in r.reasons) and fake.order_calls == 0
    assert not fake.connected                                     # adapter disconnected itself


def test_ib_async_missing_gives_friendly_message():
    if importlib.util.find_spec("ib_async"):
        pytest.skip("ib_async installed here")
    with pytest.raises(AdapterUnavailable, match="pip install ib_async==2.1.0"):
        IbkrPaperBroker({SPY: ContractSpec("SPY")}).connect()


# ------------------------------------------------------------------ permits
def test_adapter_refuses_orders_without_a_permit(tmp_path):
    svc, ad, fake, clock, _ = make_paper(tmp_path)
    req = OrderRequest("c1", SPY, 1, Dc(1), "MKT", intent_id="a" * 32)
    with pytest.raises(BrokerRefused, match="permit"):
        ad.submit(req)
    assert fake.order_calls == 0


def test_permit_cannot_be_constructed_or_minted_outside_the_service():
    with pytest.raises(PermitError):
        PaperOrderPermit("p", "i", "c", "DU1", 1, 1, "1", "MKT", None, None, "DAY", False, "a", "b", "sig")
    with pytest.raises(PermitError, match="only be minted by ExecutionService"):
        mint_permit({"account_id": "DU1"}, intent_id="i", client_order_id="c", con_id=1, side=1, qty=1, order_type="MKT", limit_px=None,
                    stop_px=None, tif="DAY", outside_rth=False, now=T0)


def held_request(tmp_path, **kw):
    svc, ad, fake, clock, port = make_paper(tmp_path, proxy=True, **kw)
    svc.start()
    port.hold = True
    svc.propose(entry())
    port.hold = False
    return svc, ad, fake, clock, port, port.captured[-1]


def test_permit_is_single_use_and_forged_or_edited_permits_fail(tmp_path):
    svc, ad, fake, clock, port, req = held_request(tmp_path)
    assert req.permit is not None and req.intent_id == req.permit.intent_id
    forged = object.__new__(PaperOrderPermit)
    for k, v in req.permit.__dict__.items():
        object.__setattr__(forged, k, v)
    object.__setattr__(forged, "qty", "500")
    with pytest.raises(BrokerRefused, match="signature"):
        ad.submit(dataclasses.replace(req, permit=forged, qty=Dc(500)))
    assert fake.order_calls == 0
    ad.submit(req)                                              # first use works
    assert fake.order_calls == 1
    with pytest.raises(BrokerRefused, match="already used"):
        ad.submit(req)
    assert fake.order_calls == 1


def test_expired_permit_is_refused(tmp_path):
    svc, ad, fake, clock, port, req = held_request(tmp_path)
    clock.t += timedelta(seconds=61)
    with pytest.raises(BrokerRefused, match="expired"):
        ad.submit(req)
    assert fake.order_calls == 0


def test_permit_bound_to_intent_and_order_fields(tmp_path):
    svc, ad, fake, clock, port, req = held_request(tmp_path)
    with pytest.raises(BrokerRefused, match="intent_id"):
        ad.submit(dataclasses.replace(req, intent_id="b" * 32))
    with pytest.raises(BrokerRefused, match="qty"):
        ad.submit(dataclasses.replace(req, qty=Dc(11)))
    with pytest.raises(BrokerRefused, match="side"):
        ad.submit(dataclasses.replace(req, side=-1))
    with pytest.raises(BrokerRefused, match="con_id"):
        ad.submit(dataclasses.replace(req, con_id=QQQ))
    assert fake.order_calls == 0


# ------------------------------------------------------------------ order shape
def test_order_sent_with_explicit_account_orderref_and_safe_flags(tmp_path):
    svc, ad, fake, clock, _ = make_paper(tmp_path)
    svc.start()
    r = svc.propose(entry())
    assert r.status == "SUBMITTED"
    contract, order = fake.placed[0]
    assert contract.secType == "STK" and contract.conId == SPY and contract.currency == "USD"
    assert order.account == ACCT and order.orderRef == r.intent_id and len(order.orderRef) == 32
    assert (order.action, order.orderType, order.tif, order.outsideRth, order.totalQuantity) == ("BUY", "MKT", "DAY", False, 10.0)


@pytest.mark.parametrize("kw,fragment", [(dict(qty=Dc("1.5")), "whole number"), (dict(outside_rth=True), "outsideRth")])
def test_unsupported_order_shapes_are_refused_before_sending(tmp_path, kw, fragment):
    svc, ad, fake, *_ = make_paper(tmp_path, margin_hook=lambda p: True)
    svc.start()
    r = svc.propose(dataclasses.replace(entry(), **kw))
    assert r.status == "BLOCKED" and fragment in r.reasons[0] and fake.order_calls == 0
    assert svc.orders.get(r.client_order_id)["state"] == O.REJECTED


def test_currency_and_unqualifiable_contracts_refused(tmp_path):
    svc, ad, fake, *_ = make_paper(tmp_path, contracts={SPY: ContractSpec("SPY", "EUR")})
    with pytest.raises(BrokerRefused, match="currency"):
        ad._contract(SPY)
    assert svc.propose(entry()).status == "BLOCKED"                      # quote unavailable -> no entry
    svc2, ad2, fake2, *_ = make_paper(tmp_path, name="q.db")
    fake2.unqualifiable.add(SPY)
    with pytest.raises(BrokerRefused, match="security definition"):
        ad2._contract(SPY)
    assert fake2.order_calls == 0


def test_stop_and_limit_prices_map_to_the_right_fields(tmp_path):
    svc, ad, fake, *_ = make_paper(tmp_path)
    svc.start()
    svc.propose(entry())
    fake.status(fake.trades[0], "Submitted"); fake.fill(fake.trades[0], 10, 100); fake.sleep(); svc.process_events()
    r = svc.propose(Proposal("stratA", "v1", "stp", SPY, "SPY", -1, Dc(10), "STP", stop_px=Dc("95"), purpose="protective"))
    assert r.status == "SUBMITTED"
    o = fake.placed[-1][1]
    assert (o.orderType, o.auxPrice, o.action) == ("STP", 95.0, "SELL")


# ------------------------------------------------------------------ callbacks
def test_ack_is_not_a_fill_and_commission_is_recorded(tmp_path):
    svc, ad, fake, *_ = make_paper(tmp_path)
    svc.start()
    r = svc.propose(entry())
    svc.process_events()
    assert svc.orders.get(r.client_order_id)["state"] == O.ACKED and svc.positions() == {}
    fake.fill(fake.trades[0], 10, 100.0, exec_id="EXA", commission=0.35)
    fake.status(fake.trades[0], "Filled", 10)
    svc.process_events()
    assert svc.orders.get(r.client_order_id)["state"] == O.FILLED and svc.positions() == {SPY: Dc(10)}
    assert ad.commissions["EXA"] == (Dc("0.35"), "USD")


def test_duplicate_callbacks_are_idempotent(tmp_path):
    fake = FakeIB(); fake.script.append("duplicate")
    svc, ad, fake, *_ = make_paper(tmp_path, fake=fake)
    svc.start()
    svc.propose(entry())
    svc.process_events()
    assert svc.positions() == {SPY: Dc(10)}
    assert rows(svc, "SELECT COUNT(*) c FROM executions")[0]["c"] == 1 and rows(svc, "SELECT COUNT(*) c FROM intents")[0]["c"] == 1
    assert [e for e in svc.incidents()] == []


def test_fill_before_ack_out_of_order(tmp_path):
    fake = FakeIB(); fake.script.append("fill_before_ack")
    svc, ad, fake, *_ = make_paper(tmp_path, fake=fake)
    svc.start()
    r = svc.propose(entry())
    svc.process_events()
    assert svc.orders.get(r.client_order_id)["state"] == O.FILLED and svc.positions() == {SPY: Dc(10)}


def test_partial_fills_accumulate(tmp_path):
    fake = FakeIB(); fake.script.append("partial")
    svc, ad, fake, *_ = make_paper(tmp_path, fake=fake)
    svc.start()
    r = svc.propose(entry())
    svc.process_events()
    assert svc.orders.get(r.client_order_id)["state"] == O.PARTIAL and svc.positions() == {SPY: Dc(5)}
    fake.fill(fake.trades[0], 5, 100.1); fake.status(fake.trades[0], "Filled", 10)
    svc.process_events()
    assert svc.orders.get(r.client_order_id)["state"] == O.FILLED and svc.positions() == {SPY: Dc(10)}


def test_status_filled_without_exec_details_forces_reconciliation_not_a_guess(tmp_path):
    fake = FakeIB(); fake.script.append("status_only_fill")
    svc, ad, fake, *_ = make_paper(tmp_path, fake=fake)
    svc.start()
    svc.propose(entry())
    svc.process_events()
    assert svc.positions() == {} and any(i["kind"] == "BROKER_RESYNC_REQUIRED" for i in svc.incidents())
    assert svc.propose(entry(signal_id="n")).status == "BLOCKED"


def test_reject_maps_to_rejected_with_plain_reason(tmp_path):
    fake = FakeIB(); fake.script.append("reject")
    svc, ad, fake, *_ = make_paper(tmp_path, fake=fake)
    svc.start()
    r = svc.propose(entry())
    svc.process_events()
    assert svc.orders.get(r.client_order_id)["state"] == O.REJECTED and svc.positions() == {}
    reason = rows(svc, "SELECT reason FROM order_events WHERE to_state='REJECTED'")[0]["reason"]
    assert "rejected by IBKR" in reason


def test_lost_ack_becomes_uncertain_and_blocks_blind_retry(tmp_path):
    fake = FakeIB(); fake.script.append("silent")
    svc, ad, fake, clock, _ = make_paper(tmp_path, fake=fake)
    svc.start()
    r = svc.propose(entry())
    svc.process_events()
    clock.t += timedelta(seconds=11)
    assert svc.check_timeouts() == [r.client_order_id]
    again = svc.propose(entry())
    assert again.status == "UNCERTAIN" and again.duplicate and fake.order_calls == 1
    other = svc.propose(entry(signal_id="x"))
    assert other.status == "BLOCKED" and fake.order_calls == 1


def test_place_order_exception_is_uncertain_not_rejected(tmp_path):
    fake = FakeIB(); fake.script.append("raise")
    svc, ad, fake, *_ = make_paper(tmp_path, fake=fake)
    svc.start()
    r = svc.propose(entry())
    assert r.status == "UNCERTAIN" and svc.orders.get(r.client_order_id)["state"] == O.UNCERTAIN


def test_unknown_order_id_callback_is_an_incident_and_blocks_entries(tmp_path):
    svc, ad, fake, *_ = make_paper(tmp_path)
    svc.start()
    fake.error(9999, 201, "who is this")
    svc.process_events()
    assert any(i["kind"] == "EVENT_UNKNOWN_ORDER" for i in svc.incidents())
    assert svc.propose(entry()).status == "BLOCKED"


@pytest.mark.parametrize("code,action", [(103, "uncertain"), (104, "uncertain"), (110, "reject"), (135, "uncertain"), (161, "cancel_fail"),
                                         (200, "reject"), (201, "reject"), (202, "cancelled"), (354, "info"), (399, "info"), (504, "disconnect"),
                                         (1100, "resync"), (1101, "resync"), (1102, "resync"), (2104, "info")])
def test_error_code_mapping_table(code, action):
    a, text = E.classify(code, order_related=True)
    assert a == action and len(text) > 15


def test_unmapped_order_error_is_uncertain_but_unmapped_general_error_is_info():
    assert E.classify(4321, True)[0] == "uncertain" and E.classify(4321, False)[0] == "info"


def test_cancel_not_found_135_makes_the_order_uncertain(tmp_path):
    svc, ad, fake, *_ = make_paper(tmp_path)
    svc.start()
    r = svc.propose(dataclasses.replace(entry(), order_type="LMT", limit_px=Dc("99.95")))
    svc.process_events()
    fake.cancel_mode = "not_found"
    svc.cancel_entry_orders("stratA")
    svc.process_events()
    assert svc.orders.get(r.client_order_id)["state"] == O.UNCERTAIN
    assert any(d["code"] == 135 for d in ad.diagnostics if d["kind"] == "ibkr_error")


def test_cancel_too_late_161_keeps_order_alive_and_fill_wins(tmp_path):
    svc, ad, fake, *_ = make_paper(tmp_path)
    svc.start()
    r = svc.propose(dataclasses.replace(entry(), order_type="LMT", limit_px=Dc("99.95")))
    svc.process_events()
    fake.cancel_mode = "too_late"
    svc.cancel_entry_orders("stratA")
    fake.fill(fake.trades[0], 10, 99.95); fake.status(fake.trades[0], "Filled", 10)
    svc.process_events()
    assert svc.orders.get(r.client_order_id)["state"] == O.FILLED and svc.positions() == {SPY: Dc(10)}


def test_cancel_ack_and_error_202_both_end_canceled_once(tmp_path):
    svc, ad, fake, *_ = make_paper(tmp_path)
    svc.start()
    r = svc.propose(dataclasses.replace(entry(), order_type="LMT", limit_px=Dc("99.95")))
    svc.process_events()
    svc.cancel_entry_orders("stratA")
    fake.error(fake.trades[0].order.orderId, 202, "cancelled")
    svc.process_events()
    assert svc.orders.get(r.client_order_id)["state"] == O.CANCELED


def test_connectivity_loss_1100_blocks_entries_until_reconciled(tmp_path):
    svc, ad, fake, *_ = make_paper(tmp_path)
    svc.start()
    fake.error(-1, 1100, "Connectivity lost")
    svc.process_events()
    assert any(i["kind"] == "BROKER_RESYNC_REQUIRED" for i in svc.incidents())
    assert svc.propose(entry()).status == "BLOCKED"
    assert svc.reconcile()["ok"]
    assert svc.propose(entry(signal_id="after-reconcile")).status == "SUBMITTED"


def test_error_504_with_unacked_order_makes_it_uncertain(tmp_path):
    fake = FakeIB(); fake.script.append("silent")
    svc, ad, fake, *_ = make_paper(tmp_path, fake=fake)
    svc.start()
    r = svc.propose(entry())
    fake.error(-1, 504, "Not connected")
    svc.process_events()
    assert svc.orders.get(r.client_order_id)["state"] == O.UNCERTAIN and not ad.connected


def test_socket_disconnect_marks_adapter_disconnected_and_poll_raises(tmp_path):
    from tradelab.execution.broker import BrokerDisconnected
    svc, ad, fake, *_ = make_paper(tmp_path)
    svc.start()
    fake.disconnect()
    with pytest.raises(BrokerDisconnected):
        ad.poll_events()
    assert svc.process_events() == 0 and any(i["kind"] == "POLL_FAILED" for i in svc.incidents())


# ------------------------------------------------------------------ reconnect / reconciliation
def test_reconnect_reconciles_open_orders_and_missed_fills(tmp_path):
    fake = FakeIB()
    svc1, ad1, fake, clock, _ = make_paper(tmp_path, fake=fake)
    svc1.start()
    r1 = svc1.propose(entry(signal_id="a"))                      # will fill while we are "down"
    rest = svc1.propose(dataclasses.replace(entry(signal_id="b", qty=3), order_type="LMT", limit_px=Dc("99.95")))
    svc1.process_events()
    for ev in (fake.orderStatusEvent, fake.execDetailsEvent, fake.commissionReportEvent, fake.errorEvent, fake.disconnectedEvent):
        ev._subs.clear()                                         # the process died: callbacks are lost
    svc1.close()
    fake.fill(fake.trades[0], 10, 100.0); fake.status(fake.trades[0], "Filled", 10)
    fake.sleep()
    svc2, ad2, fake, *_ = make_paper(tmp_path, fake=fake, clock=clock)
    rep = svc2.start()
    assert rep["ok"], rep
    assert svc2.orders.get(r1.client_order_id)["state"] == O.FILLED and svc2.positions() == {SPY: Dc(10)} == ad2.positions()
    assert svc2.orders.get(rest.client_order_id)["state"] == O.ACKED         # recomputed via orderRef -> client order id
    assert [o.client_order_id for o in ad2.open_orders()] == [rest.client_order_id]


def test_unknown_open_order_and_position_at_broker_block_entries_but_never_cancel(tmp_path):
    svc, ad, fake, *_ = make_paper(tmp_path)
    fake.foreign_trades.append(foreign_trade())
    fake.foreign_positions[(ACCT, SPY)] = 4.0
    rep = svc.start()
    assert not rep["ok"]
    kinds = {i["kind"] for i in svc.incidents()}
    assert {"UNKNOWN_BROKER_ORDER", "POSITION_MISMATCH"} <= kinds
    assert svc.propose(entry()).status == "BLOCKED" and fake.cancel_calls == []


def test_orders_and_positions_of_other_accounts_are_ignored(tmp_path):
    fake = FakeIB()
    fake.accounts = [ACCT, "DUFAKE777"]
    svc, ad, fake, *_ = make_paper(tmp_path, fake=fake)
    fake.foreign_trades.append(foreign_trade(account="DUOTHER9"))
    fake.foreign_positions[("DUOTHER9", SPY)] = 9.0
    assert svc.start()["ok"]


# ------------------------------------------------------------------ whatIf
def test_whatif_warning_blocks_through_margin_hook_and_before_submit(tmp_path):
    fake = FakeIB(); fake.whatif_result = "warn"
    svc, ad, fake, *_ = make_paper(tmp_path, fake=fake)
    svc.start()
    r = svc.propose(entry())
    assert r.status == "BLOCKED" and "MARGIN_CHECK_FAILED_OR_MISSING" in r.reasons and fake.order_calls == 0
    svc2, ad2, fake2, *_ = make_paper(tmp_path, name="w2.db", fake=fake, margin_hook=lambda p: True)     # even if the risk hook were bypassed
    svc2.start()
    r2 = svc2.propose(entry())
    assert r2.status == "BLOCKED" and "what-if" in r2.reasons[0] and fake.order_calls == 0


def test_whatif_unavailable_blocks_when_policy_requires_it_and_ok_result_allows(tmp_path):
    fake = FakeIB(); fake.whatif_result = "raise"
    svc, *_ = make_paper(tmp_path, fake=fake)
    svc.start()
    assert "MARGIN_CHECK_FAILED_OR_MISSING" in svc.propose(entry()).reasons
    fake2 = FakeIB()
    svc2, ad2, fake2, *_ = make_paper(tmp_path, name="ok.db", fake=fake2)
    svc2.start()
    assert svc2.propose(entry()).status == "SUBMITTED"
    assert any(d["kind"] == "whatif" for d in ad2.diagnostics)


def test_max_init_margin_change_is_enforced(tmp_path):
    svc, ad, fake, *_ = make_paper(tmp_path, max_init_margin_change=Dc(50))
    svc.start()
    assert svc.propose(entry()).status == "BLOCKED"            # fake what-if reports a 100 margin change


def test_quote_and_account_come_from_fake_ib(tmp_path):
    svc, ad, fake, *_ = make_paper(tmp_path)
    q = ad.quote(SPY)
    assert (q.bid, q.ask) == (Dc("99.99"), Dc("100.01"))
    assert ad.account().account_id == ACCT and ad.account().equity == Dc(100000)
    fake.ticker["bid"] = float("nan")
    assert ad.quote(SPY) is None
