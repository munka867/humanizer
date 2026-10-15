"""Phase-7 RE-AUDIT of the phase-6 fixes. FAKES ONLY (SimulatedBroker, tests/fake_ib_orders.FakeIB), throwaway keys, tmp_path and an explicit
lock_dir everywhere (nothing is written to ~/.tradelab_locks). Plain tests = an attack on the NEW code that FAILED; strict xfail = an open weakness."""
import json
import re
from datetime import timedelta
from decimal import Decimal as Dc
from pathlib import Path

import pytest
from exec_common import *  # noqa
from paper_common import *  # noqa
from fake_ib_orders import MODULE, FakeIB
from tradelab.brokers.ibkr_paper.adapter import ContractSpec, IbkrPaperBroker
from tradelab.execution import orders as O
from tradelab.execution.broker import BrokerDisconnected, OrderRequest, SimulatedBroker
from tradelab.execution.common import iso, sha256_hex
from tradelab.execution.intents import Proposal
from tradelab.execution.modes import Mode, ModeDenied, make_approval, verify_approval
from tradelab.execution.permit import PaperOrderPermit, PermitAuthority
from tradelab.execution.risk import RiskEngine, is_reducing
from tradelab.execution.service import ExecutionService, WriterLockHeld

ROOT = Path(__file__).resolve().parents[1]


def ex(sid, qty, side=-1, con=SPY, purpose="exit"):
    return Proposal("stratA", "v1", sid, con, "SPY", side, Dc(qty), "MKT", purpose=purpose)


def stop(sid, qty, px="95", con=SPY):
    return Proposal("stratA", "v1", sid, con, "SPY", -1, Dc(qty), "STP", stop_px=Dc(px), tif="GTC", purpose="protective")


def long_pos(tmp_path, qty=10, **kw):
    svc, b, clock = make(tmp_path, **kw)
    svc.start()
    assert svc.propose(entry(qty=qty)).status == "SUBMITTED"
    fill_all(svc, b)
    assert svc.positions() == {SPY: Dc(qty)}
    return svc, b, clock


def net(b, con=SPY):
    return b.positions().get(con, Dc(0))


# ======================================================== (1) reducing capacity: attacks that must FAIL
def test_capacity_arithmetic_never_goes_negative_and_never_exceeds_the_position():
    pos = {SPY: Dc(10)}
    for working in (Dc(0), Dc(3), Dc(10), Dc(25)):
        for qty in (Dc(1), Dc(7), Dc(10), Dc(11)):
            r = is_reducing(pos, SPY, -1, qty, working)
            assert r == (qty <= Dc(10) - working), (working, qty)
    assert not is_reducing(pos, SPY, +1, Dc(1)) and not is_reducing({}, SPY, -1, Dc(1)) and not is_reducing(pos, SPY, -1, Dc(1), mismatch=True)
    assert not is_reducing({SPY: Dc(-10)}, SPY, -1, Dc(1)) and is_reducing({SPY: Dc(-10)}, SPY, +1, Dc(10))


def test_partial_fills_release_exactly_the_filled_quantity(tmp_path):
    svc, b, _ = long_pos(tmp_path, 10)
    b.partial_fractions = [Dc("0.4"), Dc("0.6")]
    r = svc.propose(ex("x1", 10)); assert r.status == "SUBMITTED"
    b.script_submit()                                  # nothing scripted
    b.advance(SPY, [Dc(100)], now=None)                # fills 4 then 6 as two executions in one advance
    # deliver only the first execution
    first = b._events.pop(0) if b._events and b._events[0].kind == "ack" else None
    svc.process_events()
    assert svc.positions().get(SPY, Dc(0)) == 0 and svc.propose(ex("x2", 1)).status == "BLOCKED"   # flat: nothing left to reduce
    assert net(b) == 0


def test_cancel_pending_orders_still_hold_capacity_and_a_cancel_race_cannot_flip(tmp_path):
    svc, b, _ = long_pos(tmp_path, 10)
    r = svc.propose(stop("s1", 10)); svc.process_events()
    assert r.status == "SUBMITTED"
    b.cancel_races = True                              # the stop fills while we ask to cancel it
    svc._request_cancel(svc.orders.get(r.client_order_id), "test")
    # between the request and the events a second exit must not be 'reducing'
    assert svc.propose(ex("x1", 10)).status == "BLOCKED"
    svc.process_events()
    assert svc.positions().get(SPY, Dc(0)) == 0 and net(b) == 0


def test_uncertain_exit_keeps_capacity_and_blocks_a_second_exit(tmp_path):
    svc, b, _ = long_pos(tmp_path, 10)
    b.script_submit("lost_ack")
    assert svc.propose(ex("x1", 10)).status == "UNCERTAIN"
    r2 = svc.propose(ex("x2", 10))
    assert r2.status == "BLOCKED" and len(b._orders) == 2             # entry + the uncertain exit only
    b.advance(SPY, [Dc(100)]); svc.reconcile()
    assert net(b) == 0


def test_multiple_instruments_do_not_share_capacity(tmp_path):
    svc, b, _ = make(tmp_path, correlations={(SPY, QQQ): Dc("0.9")})   # pending orders in other instruments now need a correlation too (P7-04)
    svc.start()
    assert svc.propose(entry("a", qty=10)).status == "SUBMITTED" and svc.propose(entry("b", qty=10, con=QQQ)).status == "SUBMITTED"
    b.advance(SPY, [Dc(100)]); b.advance(QQQ, [Dc(100)]); svc.process_events()
    assert svc.positions() == {SPY: Dc(10), QQQ: Dc(10)}
    assert svc.propose(ex("xs", 10)).status == "SUBMITTED" and svc.propose(ex("xq", 10, con=QQQ)).status == "SUBMITTED"
    assert svc.propose(ex("xs2", 1)).status == "BLOCKED" and svc.propose(ex("xq2", 1, con=QQQ)).status == "BLOCKED"


def test_repeated_flatten_never_flips_even_with_delayed_fills(tmp_path):
    svc, b, _ = long_pos(tmp_path, 10)
    b.fill_delay_steps = 2
    for _ in range(4):
        svc.flatten_strategy_positions("stratA", "v1")
    for _ in range(5):
        b.advance(SPY, [Dc(100)]); svc.process_events()
    assert net(b) == 0 and svc.positions() == {}


def test_orders_carried_over_a_day_boundary_still_hold_capacity(tmp_path):
    svc, b, clock = long_pos(tmp_path, 10)
    assert svc.propose(stop("s1", 10)).status == "SUBMITTED"
    clock.t = clock.t + timedelta(days=1, hours=1); b.set_time(clock.t)
    assert svc.propose(ex("x-next-day", 10)).status == "BLOCKED"


# ======================================================== (2)/(10) flatten: unprotected positions and livelocks (open findings)
class SlowCancelBroker(SimulatedBroker):
    """cancel() is accepted but the ack arrives only when the test calls release() (real brokers are asynchronous)."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.deferred = []

    def cancel(self, coid):
        self.deferred.append(coid)

    def release(self):
        d, self.deferred = self.deferred, []
        for c in d:
            super().cancel(c)


def test_flatten_never_leaves_a_position_without_stop_or_exit(tmp_path):
    b = SlowCancelBroker()
    svc, _, clock = make(tmp_path, broker=b)
    svc.start(); assert svc.propose(entry(qty=10)).status == "SUBMITTED"; fill_all(svc, b)
    assert svc.propose(stop("s1", 10)).status == "SUBMITTED"; svc.process_events()
    rep = svc.flatten_strategy_positions("stratA", "v1")
    assert not rep["complete"]
    b.release(); svc.process_events()
    working = [r for r in svc.orders.by_state(O.ACKED, O.PARTIAL, O.SUBMITTING, O.CANCEL_PENDING) if r["con_id"] == SPY and r["side"] == -1]
    assert svc.positions() == {SPY: Dc(10)} and working, "position +10 with NO working stop or exit order"


def test_flatten_does_not_cancel_the_stop_when_the_exit_would_be_refused(tmp_path):
    svc, ad, fake, clock, port = make_paper(tmp_path)
    svc.start()
    fake.script.extend(["fill", "accept"])
    assert svc.propose(entry(qty=10)).status == "SUBMITTED"; svc.process_events()
    assert svc.propose(stop("s1", 10)).status == "SUBMITTED"; svc.process_events()
    clock.t = clock.t + timedelta(hours=9)                        # approval (valid 8 h) has expired
    fake.reqCurrentTime = lambda: clock.t
    rep = svc.flatten_strategy_positions("stratA", "v1")
    exit_sent = fake.order_calls >= 3
    stop_cancelled = bool(fake.cancel_calls)
    assert exit_sent or not stop_cancelled, f"stop cancelled={stop_cancelled}, exit sent={exit_sent}, report={rep['instruments']}"


def test_flatten_recovers_after_a_disconnect_during_the_cancel(tmp_path):
    svc, b, _ = long_pos(tmp_path, 10)
    assert svc.propose(stop("s1", 10)).status == "SUBMITTED"; svc.process_events()
    b.connected = False
    svc.flatten_strategy_positions("stratA", "v1")                # cancel raises BrokerDisconnected -> CANCEL_FAILED incident
    b.connected = True
    svc.reconcile(); svc.process_events()
    rep = svc.flatten_strategy_positions("stratA", "v1")
    b.advance(SPY, [Dc(100)]); svc.process_events()
    assert net(b) == 0 and svc.positions() == {}, f"stuck: {[ (r['purpose'], r['state']) for r in svc.orders.by_state(O.CANCEL_PENDING, O.ACKED)]}"


# ======================================================== exposure checks and pending orders
def test_pending_orders_in_other_instruments_count_toward_gross_exposure(tmp_path):
    svc, b, _ = make(tmp_path, quote_px="400", correlations={(SPY, QQQ): Dc("0.9")})
    svc.start()
    r1 = svc.propose(entry("a", qty=30, stop="399.5"))
    r2 = svc.propose(entry("b", qty=30, stop="399.5", con=QQQ))
    assert r1.status == "SUBMITTED"
    assert r2.status == "BLOCKED" and any(x in r2.reasons for x in ("MAX_GROSS_EXPOSURE", "MAX_CORRELATED_EXPOSURE")), f"gross would be 24,000 against a 20,000 policy cap (and correlated 24,000 against 15,000) ({r2.reasons})"


# ======================================================== (3) acknowledgements
def test_nothing_unattended_calls_the_acknowledge_methods_and_strategies_never_hold_the_service():
    callers = []
    for f in list((ROOT / "src").rglob("*.py")) + list((ROOT / "scripts").glob("*.py")) + list((ROOT / "command_center").glob("*.py")):
        t = f.read_text()
        for m in re.finditer(r"acknowledge_(position_mismatch|execution_corrections|corrections)\(", t):
            if f.name not in ("service.py", "adapter.py"):
                callers.append((str(f.relative_to(ROOT)), m.group(0)))
    assert not callers, callers
    from tradelab.execution import strategy_adapter, replay
    src = (Path(strategy_adapter.__file__).read_text())
    assert "ExecutionService" not in src and "self.service" not in src


def test_acknowledgement_lapses_when_the_mismatch_is_resolved_and_does_not_unblock_entries(tmp_path):
    svc, b, _ = long_pos(tmp_path, 10)
    b.inject_foreign_position(SPY, -10)
    assert not svc.reconcile()["ok"]
    assert svc.propose(ex("blocked", 10)).status == "BLOCKED"
    svc.acknowledge_position_mismatch(SPY, "op", "checked")
    assert "RECONCILIATION_NOT_CLEAN" in svc.propose(entry("e1", qty=1)).reasons          # entries stay blocked
    b.inject_foreign_position(SPY, +10)                                                   # mismatch resolved at the broker
    assert svc.reconcile()["ok"]
    assert svc._risk_inputs(ex("z", 1)).mismatch_con_ids == frozenset()


# ======================================================== (4) locks
def paper_service(tmp_path, adapter, name, lock_dir, clock=None, **kw):
    clock = clock or Clock()
    return ExecutionService(str(tmp_path / name), Mode.PAPER, adapter, RiskEngine.from_file(POLICY), lambda c: None, clock,
                            registry_state=lambda s, v: "approved_paper", risk_policy_path=POLICY,
                            approval_path=approval(tmp_path, clock_t=clock()), env=ENV, lock_dir=lock_dir, **kw)


def fresh_adapter(fake, clock=None):
    mod = type("M", (), {"IB": staticmethod(lambda f=fake: f), "Stock": MODULE.Stock, "Order": MODULE.Order})
    a = IbkrPaperBroker({SPY: ContractSpec("SPY")}, ib_module=mod, expected_account=ACCT, **({"clock": clock} if clock else {}))
    a.connect()
    return a


class ClaimsSimulated:
    """Wrapper that forwards to the real adapter but advertises is_simulated=True."""
    is_simulated = True
    def __init__(self, inner): self.inner = inner
    def __getattr__(self, n): return getattr(self.inner, n)


def test_paper_account_lock_cannot_be_dodged_by_claiming_to_be_simulated(tmp_path):
    fake = FakeIB()
    w1, w2 = ClaimsSimulated(fresh_adapter(fake)), ClaimsSimulated(fresh_adapter(fake))
    s1 = paper_service(tmp_path, w1, "a.db", str(tmp_path / "locks"))
    with pytest.raises(WriterLockHeld):
        paper_service(tmp_path, w2, "b.db", str(tmp_path / "locks"))
    s1.close()


@pytest.mark.xfail(strict=True, reason="P7-06 (design): the per-account lock directory is chosen by lock_dir/TRADELAB_LOCK_DIR/HOME, so a second process with a different value or HOME is not excluded; the lock is also advisory flock on a local file")
def test_account_lock_directory_cannot_be_redirected(tmp_path):
    fake = FakeIB()
    s1 = paper_service(tmp_path, fresh_adapter(fake), "a.db", str(tmp_path / "locks1"))
    with pytest.raises(WriterLockHeld):
        paper_service(tmp_path, fresh_adapter(fake), "b.db", str(tmp_path / "locks2"))
    s1.close()


def test_account_lock_is_released_on_close_and_stale_files_do_not_block(tmp_path):
    fake = FakeIB()
    locks = tmp_path / "locks"
    s1 = paper_service(tmp_path, fresh_adapter(fake), "a.db", str(locks))
    f = locks / "account_DUFAKE001.lock"
    assert f.exists()
    with pytest.raises(WriterLockHeld):
        paper_service(tmp_path, fresh_adapter(fake), "b.db", str(locks))
    s1.close()
    f.write_text("999999")                                    # stale PID text from a dead process: flock, not the PID, decides
    s2 = paper_service(tmp_path, fresh_adapter(fake), "c.db", str(locks)); s2.close()


# ======================================================== (5) orderRef v1:<intent>:<hmac16>
def refs(key=b"k1"):
    a = PermitAuthority(key)
    return a.make_ref, a.parse_ref


def test_order_ref_roundtrip_and_every_tamper_is_foreign():
    make, parse = refs()
    iid = "0123456789abcdef0123456789abcdef"
    ref = make(iid)
    assert re.fullmatch(r"v1:[0-9a-f]{32}:[0-9a-f]{16}", ref) and parse(ref) == iid
    bad = [ref[:-1] + ("0" if ref[-1] != "0" else "1"), ref.replace("v1:", "v2:"), ref + ":x", ref.upper(), "v1:" + iid + ":" + "0" * 16, iid,
           ref.replace(iid, iid[:-1] + "0"), "v1::", "", None, 5, ref.encode(), "v1:" + iid + ":é" * 16]
    for b in bad:
        assert parse(b) is None, b
    other_make, other_parse = refs(b"k2")
    assert other_parse(ref) is None and parse(other_make(iid)) is None     # key rotation: old refs become FOREIGN
    assert PermitAuthority(None).make_ref(iid) is None and PermitAuthority(None).parse_ref(ref) is None


def test_key_rotation_does_not_release_capacity_of_still_working_orders(tmp_path):
    svc1, ad1, fake, clock, _ = make_paper(tmp_path, name="one.db")
    svc1.start()
    fake.script.extend(["fill", "accept"])
    assert svc1.propose(entry(qty=10)).status == "SUBMITTED"; svc1.process_events()
    assert svc1.propose(stop("s1", 10)).status == "SUBMITTED"; svc1.process_events()
    svc1.close(); ad1.disconnect()
    K2 = b"rotated-key-for-tests-only"
    mod = type("M", (), {"IB": staticmethod(lambda f=fake: f), "Stock": MODULE.Stock, "Order": MODULE.Order})
    ad2 = IbkrPaperBroker({SPY: ContractSpec("SPY"), QQQ: ContractSpec("QQQ")}, ib_module=mod, expected_account=ACCT, clock=clock); ad2.connect()
    p = tmp_path / "a2.json"
    p.write_text(json.dumps(make_approval(K2, account_id=ACCT, instruments=[SPY, QQQ], strategy_id="stratA", strategy_version="v1", capital_allocation="5000",
                                          risk_policy_sha256=sha256_hex(POLICY.read_bytes()), issued_utc=clock() - timedelta(hours=1), expires_utc=clock() + timedelta(hours=8))))
    svc2 = ExecutionService(str(tmp_path / "one.db"), Mode.PAPER, ad2, RiskEngine.from_file(POLICY), lambda c: ad2.quote(c), clock,
                            registry_state=lambda s, v: "approved_paper", risk_policy_path=POLICY, approval_path=p, env={"CC_APPROVAL_KEY": K2.decode()},
                            margin_hook=ad2.margin_hook, lock_dir=str(tmp_path / "locks2"))
    svc2.reconcile()
    live_stops_at_broker = [t for t in fake.openTrades() if t.order.orderType == "STP"]
    ledger_working = [r for r in svc2.orders.by_state(O.ACKED, O.PARTIAL) if r["purpose"] == "protective"]
    assert live_stops_at_broker and ledger_working, "the live stop is no longer a working order in the ledger"


# ======================================================== (6)/(7)/(8) capital cap, policy latch, time, approve CLI
def test_capital_cap_is_zero_when_the_approval_cannot_be_verified_and_exits_are_not_blocked_by_it(tmp_path):
    svc, ad, fake, clock, _ = make_paper(tmp_path)
    svc.start()
    fake.script.extend(["fill"])
    assert svc.propose(entry(qty=10)).status == "SUBMITTED"; svc.process_events()
    svc._approval_path.write_text("{}")
    x = svc._risk_inputs(entry("z"))
    assert x.capital_cap == Dc(0)
    r = svc.propose(entry("more", qty=1))
    assert r.status == "BLOCKED" and fake.order_calls == 1


def test_policy_latch_at_construction_blocks_everything_until_restart(tmp_path):
    bad = tmp_path / "bad.yaml"; bad.write_text(POLICY.read_text() + "\n# different bytes\n")
    clock = Clock(); fake = FakeIB()
    mod = type("M", (), {"IB": staticmethod(lambda f=fake: f), "Stock": MODULE.Stock, "Order": MODULE.Order})
    ad = IbkrPaperBroker({SPY: ContractSpec("SPY")}, ib_module=mod, expected_account=ACCT, clock=clock); ad.connect()
    svc = ExecutionService(str(tmp_path / "x.db"), Mode.PAPER, ad, RiskEngine.from_file(bad), lambda c: ad.quote(c), clock,
                           registry_state=lambda s, v: "approved_paper", risk_policy_path=POLICY, approval_path=approval(tmp_path, clock_t=clock()), env=ENV,
                           lock_dir=str(tmp_path / "l"))
    assert svc._paper_block and "policy" in svc._paper_block
    svc.start()
    assert svc.propose(entry()).status == "BLOCKED" and fake.order_calls == 0
    svc._paper_block = None                                  # (in-process tamper) the per-submit re-check still refuses
    assert svc.propose(entry("again")).status == "BLOCKED" and fake.order_calls == 0


def test_expiry_judged_on_the_later_clock_and_skew_over_limit_refused(tmp_path):
    svc, ad, fake, clock, _ = make_paper(tmp_path)
    svc.start()
    fake.reqCurrentTime = lambda: T0 + timedelta(hours=8, seconds=1)          # broker says the approval just expired; local clock still inside
    r = svc.propose(entry("a"))
    assert r.status == "BLOCKED" and fake.order_calls == 0
    fake.reqCurrentTime = lambda: T0 - timedelta(seconds=200)
    assert svc.propose(entry("b")).status == "BLOCKED"


def test_approve_cli_limits_and_no_non_du_record_without_the_key(tmp_path, monkeypatch):
    from tradelab.execution import approve
    monkeypatch.setenv("CC_APPROVAL_KEY", "throwaway-test-key")
    common = ["--instruments", "1", "--strategy-id", "s", "--strategy-version", "1", "--capital", "100", "--risk-policy", str(POLICY)]
    for acct, hours in (("U1234567", "1"), ("DU1234567", "24.01"), ("DU1234567", "0"), ("DU1234567", "-1"), ("du1234567", "1"), ("DU1234567", "nan")):
        out = tmp_path / f"{acct}{hours}.json"
        assert approve.main(["--account", acct, *common, "--valid-hours", hours, "--out", str(out)]) != 0 and not out.exists(), (acct, hours)
    ok = tmp_path / "ok.json"
    assert approve.main(["--account", "DU1234567", *common, "--valid-hours", "24", "--out", str(ok)]) == 0
    # hand-editing a signed record to another account / longer validity cannot verify without the key
    rec = json.loads(ok.read_text()); now = T0
    for edit in ({"account_id": "U7654321"}, {"expires_utc": "2099-01-01T00:00:00+00:00"}, {"capital_allocation": "1000000"}):
        e = json.loads(json.dumps(rec)); e["payload"].update(edit); p = tmp_path / "e.json"; p.write_text(json.dumps(e))
        with pytest.raises(ModeDenied):
            verify_approval(p, b"throwaway-test-key", now_utc=datetime_now())
        with pytest.raises(ModeDenied):
            verify_approval(p, b"another-key", now_utc=datetime_now())


def datetime_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def test_verify_approval_enforces_the_same_limits_as_the_cli(tmp_path):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    key = b"throwaway-test-key"
    for acct, hours in (("U1234567", 1), ("DU1234567", 24 * 365 * 100)):
        p = tmp_path / f"{acct}.json"
        p.write_text(json.dumps(make_approval(key, account_id=acct, instruments=[1], strategy_id="s", strategy_version="1", capital_allocation="1",
                                              risk_policy_sha256="0" * 64, issued_utc=now - timedelta(minutes=1), expires_utc=now + timedelta(hours=hours))))
        with pytest.raises(ModeDenied):
            verify_approval(p, key, now_utc=now)


# ======================================================== P6-01 stays open: what the NEW capability design does and does not stop
def test_old_module_level_forging_surface_is_gone():
    import tradelab.execution.permit as P
    assert not any(hasattr(P, n) for n in ("_SECRET", "_MINT", "_sign", "mint_permit", "_CONSUMED"))
    svc, ad, fake, clock, port = make_paper(Path(__import__("tempfile").mkdtemp()), proxy=True)
    svc.start(); port.hold = True; svc.propose(entry())
    req = port.captured[-1]
    forged = PaperOrderPermit(**{**req.permit.body(), "permit_id": "forged", "sig": "0" * 64, "_consume": req.permit._consume})
    from tradelab.execution.broker import BrokerRefused
    with pytest.raises(BrokerRefused):
        ad.submit(OrderRequest(req.client_order_id, req.con_id, req.side, req.qty, req.order_type, intent_id=req.intent_id, permit=forged))
    unbound = IbkrPaperBroker({SPY: ContractSpec("SPY")}, ib_module=type("M", (), {"IB": staticmethod(lambda: FakeIB()), "Stock": MODULE.Stock, "Order": MODULE.Order}), expected_account=ACCT)
    unbound.connect()
    with pytest.raises(BrokerRefused):
        unbound.submit(req)
    with pytest.raises(RuntimeError):
        ad.bind_authority(lambda *a: None, lambda *a: None, lambda *a: None)          # one-shot


@pytest.mark.xfail(strict=True, reason="P6-01 (accepted limit, re-confirmed): the signing function sits in the permit's closure; any in-process code can call it, mint a valid permit and call adapter.submit without key/approval/risk")
def test_closure_extraction_cannot_forge_a_permit(tmp_path):
    svc, ad, fake, clock, port = make_paper(tmp_path, proxy=True)
    svc.start(); port.hold = True; svc.propose(entry())
    req = port.captured[-1]
    consume = req.permit._consume
    sign = next(c.cell_contents for c in consume.__closure__ if callable(c.cell_contents) and getattr(c.cell_contents, "__name__", "") == "sign")
    body = {**req.permit.body(), "permit_id": "mine", "qty": "7"}
    forged = PaperOrderPermit(**body, sig=sign(body), _consume=consume)
    import dataclasses
    from tradelab.execution.broker import BrokerRefused
    with pytest.raises(BrokerRefused):
        ad.submit(dataclasses.replace(req, qty=Dc(7), permit=forged))
    assert fake.order_calls == 0


# ======================================================== more regressions of the fixes
def test_protective_stop_is_not_starved_by_the_daily_order_limit(tmp_path):
    pol = tmp_path / "pol.yaml"
    pol.write_text(POLICY.read_text().replace("max_orders_per_day: 50", "max_orders_per_day: 3").replace("max_orders_per_minute: 5", "max_orders_per_minute: 100"))
    svc, b, _ = make(tmp_path, name="f.db")
    svc.risk = RiskEngine.from_file(pol)
    svc.start()
    for i in range(3):
        assert svc.propose(entry(f"e{i}", qty=1)).status == "SUBMITTED"
    fill_all(svc, b)
    assert svc.positions() == {SPY: Dc(3)}
    r = svc.propose(stop("s1", 3))
    assert r.status == "SUBMITTED", f"open +3 position cannot be protected: {r.reasons}"


def test_foreign_working_exit_counts_against_reducing_capacity(tmp_path):
    svc, b, _ = long_pos(tmp_path, 10)
    b.inject_foreign_order(SPY, -1, 10, "STP", "95")
    rep = svc.reconcile()
    assert any("unknown broker order" in f for f in rep["findings"])
    r = svc.propose(ex("x1", 10))
    assert r.status != "SUBMITTED"
