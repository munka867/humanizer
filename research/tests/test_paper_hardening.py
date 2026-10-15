"""Fixes for the phase-6 audit (P6-02..P6-13) that the review reproducers do not cover directly. FAKES ONLY."""
from datetime import timedelta
from decimal import Decimal as Dc

import pytest
from paper_common import *  # noqa
from exec_common import base_inputs
from tradelab.execution import orders as O
from tradelab.execution.intents import Proposal
from tradelab.execution.permit import PermitAuthority
from tradelab.execution.risk import RiskEngine, is_reducing
from tradelab.execution.service import WriterLockHeld


def test_reducing_capacity_shrinks_with_every_live_exit_so_twenty_sell_one_stop_at_the_position():
    eng = RiskEngine.from_file(POLICY)
    working, allowed = Dc(0), 0
    for i in range(20):
        x = base_inputs(positions={SPY: Dc(5)}, working_same_side={(SPY, -1): working}, frequency_exempt=True)
        d = eng.evaluate(Proposal("s", "v", f"e{i}", SPY, "SPY", -1, Dc(1), "MKT", purpose="exit"), x)
        if d.reducing:
            allowed += 1
            working += 1
    assert allowed == 5
    assert not is_reducing({SPY: Dc(10)}, SPY, -1, Dc(10), working=Dc(10))
    assert is_reducing({SPY: Dc(10)}, SPY, -1, Dc(6), working=Dc(4))
    assert not is_reducing({SPY: Dc(10)}, SPY, -1, Dc(1), mismatch=True)


def test_reducing_orders_are_subject_to_the_frequency_limit_but_operator_flatten_is_not(tmp_path):
    from datetime import timedelta as td
    eng = RiskEngine.from_file(POLICY)
    recent = tuple(T0 - td(seconds=s) for s in range(5))
    sell = Proposal("s", "v", "x", SPY, "SPY", -1, Dc(1), "MKT", purpose="exit")
    assert "ORDER_FREQUENCY_PER_MINUTE" in eng.evaluate(sell, base_inputs(positions={SPY: Dc(5)}, recent_order_times=recent)).reasons
    assert eng.evaluate(sell, base_inputs(positions={SPY: Dc(5)}, recent_order_times=recent, frequency_exempt=True)).allowed


def test_acknowledged_mismatch_lets_an_explicit_exit_through_but_not_entries(tmp_path):
    svc, ad, fake, *_ = make_paper(tmp_path)
    svc.start()
    fake.script.append("fill")
    svc.propose(entry(qty=10)); svc.process_events()
    assert svc.positions() == {SPY: Dc(10)}
    fake.fills.clear()                                         # broker "forgets": now flat there, ledger still +10
    assert not svc.reconcile()["ok"]
    sell = Proposal("stratA", "v1", "x", SPY, "SPY", -1, Dc(10), "MKT", purpose="exit")
    assert svc.propose(sell).status == "BLOCKED"               # exemption withdrawn while the mismatch is unresolved
    svc.acknowledge_position_mismatch(SPY, "operator", "checked in TWS")
    assert svc.propose(Proposal("stratA", "v1", "x2", SPY, "SPY", -1, Dc(10), "MKT", purpose="exit")).status == "SUBMITTED"
    assert svc.propose(entry(signal_id="new")).status == "BLOCKED"


def test_order_ref_is_hmac_tagged_and_foreign_or_untagged_refs_do_not_map():
    a = PermitAuthority(b"key-one")
    iid = "ab" * 16
    ref = a.make_ref(iid)
    assert ref.startswith("v1:") and a.parse_ref(ref) == iid
    assert a.parse_ref(ref[:-1] + ("0" if ref[-1] != "0" else "1")) is None                  # wrong tag
    assert a.parse_ref(f"v1:{iid}:{'0' * 16}") is None and a.parse_ref(iid) is None and a.parse_ref("") is None and a.parse_ref(None) is None
    assert PermitAuthority(b"key-two").parse_ref(ref) is None                                 # another key cannot claim our orders
    assert PermitAuthority(None).make_ref(iid) is None


def test_foreign_order_with_our_prefix_is_not_ours_in_reconciliation(tmp_path):
    from fake_ib_orders import foreign_trade
    svc, ad, fake, *_ = make_paper(tmp_path)
    iid = "ab" * 16
    fake.foreign_trades.append(foreign_trade(ref=f"v1:{iid}:{'0' * 16}"))
    assert not svc.start()["ok"] and any(i["kind"] == "UNKNOWN_BROKER_ORDER" for i in svc.incidents())


def test_execution_correction_blocks_entries_until_a_human_acknowledges(tmp_path):
    from test_review_broker_exec import fill_event
    svc, ad, fake, *_ = make_paper(tmp_path)
    svc.start()
    fill_event(ad, "a" * 32, "0000e0d5.65f1a2b3.01.01")
    fill_event(ad, "a" * 32, "0000e0d5.65f1a2b3.01.02")
    assert ad.unresolved_corrections == ["0000e0d5.65f1a2b3.01.02"]
    svc.process_events()
    assert svc.propose(entry()).status == "BLOCKED"
    assert not svc.reconcile()["ok"] and any(i["kind"] == "EXECUTION_CORRECTION_REVIEW" for i in svc.incidents())
    svc.acknowledge_execution_corrections("operator", "reviewed in TWS")
    # the earlier unknown 'a'*32 fill is foreign, so reconcile still reports it; the correction finding itself is gone
    assert ad.unresolved_corrections == []


def test_distinct_partial_fills_of_one_order_are_not_corrections(tmp_path):
    from test_review_broker_exec import fill_event
    svc, ad, *_ = make_paper(tmp_path)
    fill_event(ad, "a" * 32, "0000e0d5.65f1a2b3.01.01")
    fill_event(ad, "a" * 32, "0000e0d5.65f1a2b3.02.01")
    assert ad.unresolved_corrections == [] and len([e for e in ad._events if e.kind == "fill"]) == 2


def test_clock_skew_against_broker_time_blocks_paper_orders(tmp_path):
    svc, ad, fake, clock, _ = make_paper(tmp_path)
    svc.start()
    fake.reqCurrentTime = lambda: T0 + timedelta(seconds=100)
    assert svc.propose(entry("ok")).status == "SUBMITTED"
    fake.reqCurrentTime = lambda: T0 + timedelta(seconds=130)
    r = svc.propose(entry("late"))
    assert r.status == "BLOCKED" and "differ by more than" in r.reasons[0]
    def down():
        raise RuntimeError("no time")
    fake.reqCurrentTime = down
    assert "broker time unavailable" in svc.propose(entry("down")).reasons[0] or svc.propose(entry("down2")).status == "BLOCKED"


def test_capital_allocation_caps_gross_exposure_at_min_of_policy_and_capital(tmp_path):
    svc, ad, fake, clock, _ = make_paper(tmp_path)          # approval capital is 5000, policy gross cap 20000
    svc.start()
    r = svc.propose(entry(qty=40, stop="99.9"))             # ~4000 notional <= 5000
    assert r.status == "SUBMITTED"
    r2 = svc.propose(entry(signal_id="more", qty=20, stop="99.9"))   # would reach ~6000 > 5000
    assert r2.status == "BLOCKED" and "CAPITAL_ALLOCATION_EXCEEDED" in r2.reasons


def test_policy_engine_must_match_approval_at_construction_and_on_every_submit(tmp_path):
    svc, ad, fake, *_ = make_paper(tmp_path)
    assert svc._paper_block is None
    svc.start()
    svc.risk = RiskEngine.from_file(__import__("pathlib").Path(tmp_path / "other.yaml")) if False else svc.risk
    from dataclasses import replace
    svc.risk = RiskEngine(replace(svc.risk.policy, sha256="0" * 64))          # engine swapped after construction
    r = svc.propose(entry())
    assert r.status == "BLOCKED" and any("risk engine" in x or "sha256" in x for x in r.reasons) and fake.order_calls == 0


def test_account_lock_is_named_by_the_approved_account_and_blocks_a_second_process(tmp_path):
    svc, ad, fake, clock, _ = make_paper(tmp_path, name="one.db")
    files = list((tmp_path / "locks_one.db").glob("*.lock"))
    assert any(f.name == "account_DUFAKE001.lock" for f in files)
    with pytest.raises(WriterLockHeld, match="DUFAKE001"):
        make_paper(tmp_path, name="two.db", fake=fake) if False else __import__("tradelab.execution.service", fromlist=["x"]).ExecutionService(
            str(tmp_path / "two.db"), svc.mode, ad, svc.risk, lambda c: None, clock, registry_state=lambda s, v: "approved_paper",
            risk_policy_path=POLICY, approval_path=svc._approval_path, env=ENV, lock_dir=str(tmp_path / "locks_one.db"))
    svc.close()


def test_label_cannot_exempt_a_non_reducing_order(tmp_path):
    svc, ad, fake, *_ = make_paper(tmp_path)
    svc.start()
    for purpose in ("exit", "protective"):
        r = svc.propose(Proposal("stratA", "v1", f"s-{purpose}", SPY, "SPY", 1, Dc(40), "MKT", purpose=purpose))
        assert r.status == "BLOCKED" and any(x.startswith("NOT_REDUCING") for x in r.reasons) and "NO_STOP_FOR_RISK" in r.reasons
    assert fake.order_calls == 0
