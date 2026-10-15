"""Phase-6 adversarial review of the execution core and the PAPER adapter. FAKES ONLY: SimulatedBroker and tests/fake_ib_orders.FakeIB.
Never connects to anything, never uses a real key (throwaway test key from paper_common), approval files live in tmp_path.
Plain tests show a current guard HOLDS; strict xfail tests reproduce an OPEN weakness (remove the marker when fixed)."""
import json
import threading
from datetime import timedelta
from decimal import Decimal as Dc
from types import SimpleNamespace

import pytest
from exec_common import *  # noqa
from paper_common import *  # noqa
from fake_ib_orders import MODULE, FakeIB
from tradelab.brokers.ibkr_paper import guard as G
from tradelab.brokers.ibkr_paper.adapter import ContractSpec, IbkrPaperBroker
from tradelab.execution import orders as O
from tradelab.execution import permit as P
from tradelab.execution.broker import BrokerRefused, OrderRequest, SimulatedBroker
from tradelab.execution.common import iso, sha256_hex
from tradelab.execution.intents import Proposal
from tradelab.execution.modes import Mode
from tradelab.execution.risk import RiskEngine
from tradelab.execution.service import ExecutionService


def long_position(tmp_path, qty=10, **kw):
    svc, b, clock = make(tmp_path, **kw)
    svc.start()
    assert svc.propose(entry(qty=qty)).status == "SUBMITTED"
    fill_all(svc, b)
    assert svc.positions() == {SPY: Dc(qty)}
    return svc, b, clock


def exit_order(sid, qty, side=-1):
    return Proposal("stratA", "v1", sid, SPY, "SPY", side, Dc(qty), "MKT", purpose="exit")


# ============================================================ 1. risk engine: the reducing-order exemption
def test_two_reducing_exits_cannot_flip_the_position_to_short(tmp_path):
    svc, b, _ = long_position(tmp_path, 10)
    r1, r2 = svc.propose(exit_order("x1", 10)), svc.propose(exit_order("x2", 10))        # no fill processed in between (e.g. closed market, slow fill)
    b.advance(SPY, [Dc(100)])
    svc.process_events()
    assert b.positions().get(SPY, Dc(0)) >= 0, f"broker is now {b.positions()} after {r1.status}/{r2.status}"


def test_reducing_order_flood_is_rate_limited_and_cannot_exceed_the_position(tmp_path):
    svc, b, _ = long_position(tmp_path, 10)
    sent = [svc.propose(exit_order(f"f{i}", 1)).status for i in range(20)]
    b.advance(SPY, [Dc(100)])
    svc.process_events()
    assert sent.count("SUBMITTED") <= 5 and b.positions().get(SPY, Dc(0)) >= 0


def test_double_flatten_does_not_flip(tmp_path):
    svc, b, _ = long_position(tmp_path, 10)
    svc.flatten_strategy_positions("stratA", "v1")
    svc.flatten_strategy_positions("stratA", "v1")
    b.advance(SPY, [Dc(100)])
    svc.process_events()
    assert b.positions().get(SPY, Dc(0)) >= 0


def test_reducing_exemption_not_used_when_ledger_and_broker_disagree(tmp_path):
    svc, b, _ = long_position(tmp_path, 10)
    b.inject_foreign_position(SPY, -10)                       # broker is flat; ledger still believes +10
    rep = svc.reconcile()
    assert not rep["ok"] and any("position mismatch" in f for f in rep["findings"])
    r = svc.propose(exit_order("after-mismatch", 10))
    assert r.status == "BLOCKED"


def test_a_reducing_order_can_never_open_from_flat_or_flip_in_a_single_order(tmp_path):
    """HOLDS: from flat a SELL is a new (blocked) short; a single SELL larger than the position is not 'reducing'."""
    svc, b, _ = long_position(tmp_path, 10)
    assert svc.propose(exit_order("big", 11)).status == "BLOCKED"
    svc2, b2, _ = make(tmp_path, name="flat.db")
    svc2.start()
    assert "SHORTING_NOT_ALLOWED" in svc2.propose(Proposal("stratA", "v1", "s", SPY, "SPY", -1, Dc(1), "MKT", stop_px=Dc(101))).reasons


def test_missing_and_stale_inputs_block_new_exposure(tmp_path):
    """HOLDS: no account / stale quote / no quote -> entry blocked (fail closed)."""
    svc, b, clock = make(tmp_path)
    svc.start()
    b.account_stale = True
    assert any(r.startswith("STALE_ACCOUNT") for r in svc.propose(entry("a")).reasons)
    b.account_stale = False
    svc.quote_source = lambda cid: None
    assert any("MISSING_INPUT" in r for r in svc.propose(entry("b")).reasons)
    def boom(cid): raise RuntimeError("quote source down")
    svc.quote_source = boom
    assert svc.propose(entry("c")).status == "BLOCKED"


# ============================================================ 2. PAPER chain
def paper(tmp_path, **kw):
    return make_paper(tmp_path, proxy=True, **kw)


def captured_request(tmp_path):
    svc, adapter, fake, clock, port = paper(tmp_path)
    svc.start()
    port.hold = True
    svc.propose(entry())
    return svc, adapter, fake, clock, port.captured[-1]


def forge_permit(req, account, now):
    body = dict(permit_id="forged", intent_id=req.intent_id, client_order_id=req.client_order_id, account_id=account, con_id=req.con_id,
                side=req.side, qty=str(req.qty), order_type=req.order_type, limit_px=None, stop_px=None, tif=req.tif,
                outside_rth=bool(req.outside_rth), issued_utc=iso(now), expires_utc=iso(now + timedelta(seconds=60)))
    return P.PaperOrderPermit(sig=P._sign(body), _mint=P._MINT, **body)


@pytest.mark.xfail(strict=True, reason="P6-01: PaperOrderPermit's guard objects (_MINT, _SECRET, _sign) are plain module globals; any code in the process can forge a valid permit and call IbkrPaperBroker.submit directly, with no key, approval, strategy state or risk check")
def test_adapter_refuses_a_permit_forged_from_module_globals(tmp_path):
    svc, adapter, fake, clock, req = captured_request(tmp_path)
    assert fake.order_calls == 0
    forged = forge_permit(OrderRequest(req.client_order_id, SPY, 1, Dc(1), "MKT", intent_id=req.intent_id), ACCT, clock())
    req2 = OrderRequest(req.client_order_id, SPY, 1, Dc(1), "MKT", intent_id=req.intent_id, permit=forged)
    with pytest.raises(BrokerRefused):
        adapter.submit(req2)
    assert fake.order_calls == 0


@pytest.mark.xfail(strict=True, reason="P6-01b: mint_permit's caller check reads the caller's globals['__name__'], which code can set (exec with {'__name__': 'tradelab.execution.service'})")
def test_mint_permit_caller_check_cannot_be_spoofed():
    ns = {"__name__": "tradelab.execution.service", "mint_permit": P.mint_permit, "now": T0}
    with pytest.raises(P.PermitError):
        exec("p = mint_permit({'account_id': 'DUFAKE001'}, intent_id='a'*32, client_order_id='c', con_id=1, side=1, qty=1, order_type='MKT', "
             "limit_px=None, stop_px=None, tif='DAY', outside_rth=False, now=now)", ns)


def test_permit_is_single_use_across_threads_and_replay_fails(tmp_path):
    """HOLDS: 16 racing consumers of one valid permit -> exactly one wins; a later replay fails."""
    svc, adapter, fake, clock, req = captured_request(tmp_path)
    wins, errs = [], []
    def go():
        try:
            P.consume_permit(req.permit, req, clock()); wins.append(1)
        except P.PermitError as e:
            errs.append(str(e))
    ts = [threading.Thread(target=go) for _ in range(16)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(wins) == 1 and len(errs) == 15
    with pytest.raises(BrokerRefused):
        adapter.submit(req)
    assert fake.order_calls == 0


def test_permit_expiry_and_field_binding_hold(tmp_path):
    svc, adapter, fake, clock, req = captured_request(tmp_path)
    for field, val in (("qty", Dc(2)), ("side", -1), ("con_id", QQQ), ("order_type", "LMT")):
        bad = OrderRequest(req.client_order_id, req.con_id, req.side, req.qty, req.order_type, req.limit_px, req.stop_px, req.tif, req.outside_rth,
                           req.intent_id, req.permit)
        bad = OrderRequest(**{**bad.__dict__, field: val, **({"limit_px": Dc(1)} if field == "order_type" else {})})
        with pytest.raises(BrokerRefused):
            adapter.submit(bad)
    clock.t = clock.t + timedelta(seconds=61)
    with pytest.raises(BrokerRefused):
        adapter.submit(req)
    assert fake.order_calls == 0


def test_no_order_without_key_state_policy_hash_account_or_expiry(tmp_path):
    """HOLDS: each missing proof alone blocks the PAPER submit (the broker is never called)."""
    # missing key
    svc, adapter, fake, clock, port = make_paper(tmp_path, name="a.db"); svc._env = {}
    svc.start(); assert svc.propose(entry()).status == "BLOCKED" and fake.order_calls == 0
    # wrong key
    svc, adapter, fake, clock, port = make_paper(tmp_path, name="b.db"); svc._env = {"CC_APPROVAL_KEY": "not-the-key"}
    svc.start(); assert svc.propose(entry()).status == "BLOCKED" and fake.order_calls == 0
    # strategy not approved_paper
    svc, adapter, fake, clock, port = make_paper(tmp_path, name="c.db"); svc._registry_state = lambda s, v: "testing"
    svc.start(); assert svc.propose(entry()).status == "BLOCKED" and fake.order_calls == 0
    # policy edited after approval
    pol = tmp_path / "pol.yaml"; pol.write_text(POLICY.read_text())
    svc, adapter, fake, clock, port = make_paper(tmp_path, name="d.db")
    svc._policy_path = pol                                         # file bytes differ from the approved sha
    pol.write_text(POLICY.read_text() + "\n# edited\n")
    svc.start(); assert svc.propose(entry()).status == "BLOCKED" and fake.order_calls == 0
    # approval for another account
    svc, adapter, fake, clock, port = make_paper(tmp_path, name="e.db", account="DUFAKE001", expected="DUFAKE001", proxy=True, account_override="DUOTHER999")
    svc.start(); assert svc.propose(entry()).status == "BLOCKED" and fake.order_calls == 0
    # expired
    svc, adapter, fake, clock, port = make_paper(tmp_path, name="f.db")
    svc.start(); clock.t = clock.t + timedelta(hours=9)
    assert svc.propose(entry()).status == "BLOCKED" and fake.order_calls == 0
    # tampered approval file (field edit keeps old mac)
    svc, adapter, fake, clock, port = make_paper(tmp_path, name="g.db")
    rec = json.loads(svc._approval_path.read_text()); rec["payload"]["account_id"] = "DUATTACKER"; svc._approval_path.write_text(json.dumps(rec))
    svc.start(); assert svc.propose(entry()).status == "BLOCKED" and fake.order_calls == 0


def test_risk_engine_policy_must_be_the_approved_policy(tmp_path):
    strict = tmp_path / "strict.yaml"; loose = tmp_path / "loose.yaml"
    txt = POLICY.read_text()
    strict.write_text(txt.replace("max_position_qty: 50", "max_position_qty: 5"))
    loose.write_text(txt)                                          # 50
    clock = Clock(); fake = FakeIB()
    mod = type("M", (), {"IB": staticmethod(lambda f=fake: f), "Stock": MODULE.Stock, "Order": MODULE.Order})
    adapter = IbkrPaperBroker({SPY: ContractSpec("SPY")}, ib_module=mod, expected_account=ACCT, clock=clock); adapter.connect()
    svc = ExecutionService(str(tmp_path / "x.db"), Mode.PAPER, adapter, RiskEngine.from_file(loose), lambda c: adapter.quote(c), clock,
                           registry_state=lambda s, v: "approved_paper", risk_policy_path=strict,
                           approval_path=approval(tmp_path, policy=strict, instruments=(SPY,), clock_t=clock()), env=ENV, margin_hook=adapter.margin_hook)
    svc.start()
    r = svc.propose(entry(qty=20, stop="99.9"))
    assert r.status != "SUBMITTED", "20 shares were sent although the APPROVED policy caps SPY at 5"


def test_paper_mode_requires_a_persistent_locked_ledger():
    fake = FakeIB()
    mod = type("M", (), {"IB": staticmethod(lambda f=fake: f), "Stock": MODULE.Stock, "Order": MODULE.Order})
    adapter = IbkrPaperBroker({SPY: ContractSpec("SPY")}, ib_module=mod, expected_account=ACCT); adapter.connect()
    with pytest.raises(Exception):
        ExecutionService(":memory:", Mode.PAPER, adapter, RiskEngine.from_file(POLICY), lambda c: None, Clock(), registry_state=lambda s, v: "approved_paper")


def test_single_writer_lock_blocks_second_service_on_same_ledger(tmp_path):
    """HOLDS for the same ledger file (the lock is per ledger, not per broker account; see P6-05)."""
    from tradelab.execution.service import WriterLockHeld
    svc, b, clock = make(tmp_path)
    with pytest.raises(WriterLockHeld):
        make(tmp_path)


def test_corrupt_approval_mac_is_a_clean_block(tmp_path):
    svc, adapter, fake, clock, port = make_paper(tmp_path)
    rec = json.loads(svc._approval_path.read_text()); rec["hmac_sha256"] = "é" * 64; svc._approval_path.write_text(json.dumps(rec))
    svc.start()
    assert svc.propose(entry()).status == "BLOCKED"


def test_live_mode_and_simulated_gate_cannot_reach_the_real_adapter(tmp_path):
    """HOLDS: LIVE cannot be constructed or re-assigned; DEMO/BACKTEST/REPLAY refuse a non-simulated broker; the adapter also needs a permit."""
    fake = FakeIB()
    mod = type("M", (), {"IB": staticmethod(lambda f=fake: f), "Stock": MODULE.Stock, "Order": MODULE.Order})
    adapter = IbkrPaperBroker({SPY: ContractSpec("SPY")}, ib_module=mod, expected_account=ACCT); adapter.connect()
    with pytest.raises(Exception):
        ExecutionService(str(tmp_path / "l.db"), Mode.LIVE, adapter, RiskEngine.from_file(POLICY), lambda c: None, Clock())
    for m in (Mode.DEMO, Mode.BACKTEST, Mode.REPLAY):
        from tradelab.execution.modes import ModeDenied, assert_may_submit
        with pytest.raises(ModeDenied, match="simulated"):
            assert_may_submit(m, broker_is_simulated=getattr(adapter, "is_simulated", False))
    svc = ExecutionService(str(tmp_path / "m.db"), Mode.DEMO, SimulatedBroker(), RiskEngine.from_file(POLICY), lambda c: None, Clock())
    with pytest.raises(AttributeError):
        svc.mode = Mode.PAPER
    with pytest.raises(Exception):
        from tradelab.execution.modes import assert_may_submit
        assert_may_submit(Mode.LIVE, broker_is_simulated=True)


# ============================================================ 3. guards: host / port / account
@pytest.mark.parametrize("port", [7496, 4001, 7497.0, "7497", None, True, 0, 4003, 7498])
def test_ports_other_than_the_two_paper_ports_refused(port):
    with pytest.raises(G.GuardError):
        IbkrPaperBroker({SPY: ContractSpec("SPY")}, ib_module=MODULE, port=port)


def test_no_environment_variable_changes_host_port_or_account_guard(monkeypatch):
    for k in ("IB_PORT", "TWS_PORT", "IBKR_PORT", "CC_IBKR_PORT", "CC_ALLOW_LIVE", "CC_LIVE", "TWS_HOST", "IB_HOST", "CC_PAPER_PORT", "CC_ALLOW_NON_PAPER"):
        monkeypatch.setenv(k, "7496" if "PORT" in k else "1")
    a = IbkrPaperBroker({SPY: ContractSpec("SPY")}, ib_module=MODULE)
    assert (a.host, a.port) == ("127.0.0.1", 7497)
    fake = FakeIB(); fake.accounts = ["U1234567"]
    mod = type("M", (), {"IB": staticmethod(lambda f=fake: f), "Stock": MODULE.Stock, "Order": MODULE.Order})
    with pytest.raises(G.GuardError):
        IbkrPaperBroker({SPY: ContractSpec("SPY")}, ib_module=mod).connect()
    assert fake.connected is False                                    # disconnected after refusing


def test_fake_that_lies_about_accounts_midsession_cannot_redirect_orders(tmp_path):
    """HOLDS: the account used for an order comes from the signed approval and must be in the list captured at connect; a later change of
    managedAccounts() on the fake is ignored (fixed list) and an order for a non-DU account is refused by the adapter."""
    svc, adapter, fake, clock, port = paper(tmp_path)
    fake.accounts = ["U1234567"]                                      # fake now claims a live login
    req = OrderRequest("c" * 20 + "-1", SPY, 1, Dc(1), "MKT", intent_id="a" * 32)
    with pytest.raises(BrokerRefused):
        adapter.submit(req)                                           # no permit
    assert fake.order_calls == 0
    with pytest.raises(G.GuardError):
        G.check_order_account("U1234567", ["U1234567"])


# ============================================================ 4. idempotency / fills / foreign orders
def test_duplicate_intent_and_blind_retry_after_uncertain_send_nothing_twice(tmp_path):
    svc, b, clock = make(tmp_path)
    svc.start()
    b.script_submit("lost_ack")
    r1 = svc.propose(entry("same"))
    assert r1.status == "UNCERTAIN"
    r2 = svc.propose(entry("same"))
    assert r2.status == "UNCERTAIN" and len(b._orders) == 1
    r3 = svc.propose(entry("different"))
    assert r3.status == "BLOCKED" and "UNCERTAIN_ORDER_BLOCKS_ENTRIES" in r3.reasons and len(b._orders) == 1


def test_positions_come_from_executions_not_acks_or_order_rows(tmp_path):
    svc, b, _ = make(tmp_path)
    svc.start(); svc.propose(entry())
    svc.process_events()                                              # ack only
    assert svc.positions() == {}
    svc.conn.execute("UPDATE orders SET filled_qty='10', state='FILLED'")   # tampered row
    assert svc.positions() == {}


def fill_event(adapter, order_ref, exec_id, shares=1, perm=1):
    ex = SimpleNamespace(execId=exec_id, time=T0, acctNumber=ACCT, side="BOT", shares=float(shares), price=100.0, permId=perm, orderId=7, orderRef=order_ref, clientId=21)
    trade = SimpleNamespace(order=SimpleNamespace(orderRef=order_ref, orderId=7, account=ACCT), orderStatus=SimpleNamespace(permId=perm, filled=0.0, status="Submitted"))
    adapter._on_exec(trade, SimpleNamespace(execution=ex, contract=SimpleNamespace(conId=SPY), time=T0))


def test_execution_correction_is_not_double_counted(tmp_path):
    svc, adapter, fake, clock, port = paper(tmp_path)
    ref = "a" * 32
    fill_event(adapter, ref, "0000e0d5.65f1a2b3.01.01")
    fill_event(adapter, ref, "0000e0d5.65f1a2b3.01.02")              # same execution, corrected
    fills = [e for e in adapter.poll_events() if e.kind == "fill"] if False else [e for e in adapter._events if e.kind == "fill"]
    assert len(fills) == 1


def test_foreign_orderref_with_same_prefix_is_foreign():
    ours = "0123456789abcdef0123" + "0" * 12
    spoof = "0123456789abcdef0123" + "f" * 12
    assert IbkrPaperBroker._coid(spoof) != IbkrPaperBroker._coid(ours) or IbkrPaperBroker._coid(spoof).startswith("FOREIGN")


def test_foreign_open_order_blocks_entries_and_is_never_cancelled(tmp_path):
    svc, adapter, fake, clock, port = paper(tmp_path)
    from fake_ib_orders import foreign_trade
    fake.foreign_trades.append(foreign_trade() if callable(foreign_trade) else foreign_trade)
    rep = svc.start()
    assert not rep["ok"] and svc.propose(entry()).status == "BLOCKED"
    assert fake.cancel_calls == [] and fake.order_calls == 0


def test_paper_chain_end_to_end_stacked_exits_flip_short(tmp_path):
    svc, adapter, fake, clock, port = make_paper(tmp_path)
    svc.start()
    fake.script.extend(["fill", "accept", "accept"])
    assert svc.propose(entry(qty=10)).status == "SUBMITTED"
    svc.process_events()
    assert svc.positions() == {SPY: Dc(10)}
    r1 = svc.propose(Proposal("stratA", "v1", "x1", SPY, "SPY", -1, Dc(10), "MKT", purpose="exit"))
    r2 = svc.propose(Proposal("stratA", "v1", "x2", SPY, "SPY", -1, Dc(10), "MKT", purpose="exit"))
    for t in fake.trades[-2:]:
        fake.fill(t, 10, 100.0); fake.status(t, "Filled", 10)
    fake.sleep(); svc.process_events()
    net = sum(f.execution.shares * (1 if f.execution.side == "BOT" else -1) for f in fake.fills)
    assert net >= 0, f"net {net} after {r1.status}/{r2.status}"


# ============================================================ 5. further bypasses of the risk engine / approval content
def test_purpose_label_cannot_skip_the_per_trade_risk_check(tmp_path):
    svc, b, _ = make(tmp_path)
    svc.start()
    assert svc.propose(entry("e1", qty=40, stop=None)).status == "BLOCKED"            # as an entry: NO_STOP_FOR_RISK
    for purpose in ("exit", "protective"):
        r = svc.propose(Proposal("stratA", "v1", f"sneaky-{purpose}", SPY, "SPY", 1, Dc(40), "MKT", purpose=purpose))
        assert r.status == "BLOCKED", f"opened a long with purpose={purpose!r}: {r.status}"


def test_signed_capital_allocation_is_enforced(tmp_path):
    from tradelab.execution.modes import make_approval
    svc, adapter, fake, clock, port = make_paper(tmp_path)
    svc._approval_path.write_text(json.dumps(make_approval(
        KEY, account_id=ACCT, instruments=[SPY, QQQ], strategy_id="stratA", strategy_version="v1", capital_allocation="500",
        risk_policy_sha256=sha256_hex(POLICY.read_bytes()), issued_utc=clock() - timedelta(hours=1), expires_utc=clock() + timedelta(hours=8))))
    svc.start()
    r = svc.propose(entry(qty=10, stop="99"))                         # 10 x ~100 = ~1,000 notional
    assert r.status != "SUBMITTED"


def test_expired_approval_cannot_be_revived_by_a_wrong_local_clock(tmp_path):
    svc, adapter, fake, clock, port = make_paper(tmp_path)           # approval valid until T0 + 8h
    svc.start()
    fake.reqCurrentTime = lambda: T0 + timedelta(hours=9)             # the broker says it is 9 h later: approval is expired in real time
    r = svc.propose(entry())                                          # local clock still says T0
    assert r.status != "SUBMITTED"


def test_approve_cli_refuses_non_paper_accounts_and_unbounded_validity(tmp_path, monkeypatch, capsys):
    from tradelab.execution import approve
    monkeypatch.setenv("CC_APPROVAL_KEY", "throwaway-test-key")
    out = tmp_path / "a.json"
    rc = approve.main(["--account", "U1234567", "--instruments", "1", "--strategy-id", "s", "--strategy-version", "1", "--capital", "100",
                       "--risk-policy", str(POLICY), "--valid-hours", "876000", "--out", str(out)])
    assert rc != 0 and not out.exists()


def test_approve_cli_refuses_without_the_key_and_writes_nothing(tmp_path, monkeypatch):
    from tradelab.execution import approve
    monkeypatch.delenv("CC_APPROVAL_KEY", raising=False)
    out = tmp_path / "a.json"
    rc = approve.main(["--account", "DU1234567", "--instruments", "1", "--strategy-id", "s", "--strategy-version", "1", "--capital", "100",
                       "--risk-policy", str(POLICY), "--out", str(out)])
    assert rc == 2 and not out.exists()


def test_smoke_registry_and_caps_are_narrow():
    """HOLDS: the SMOKE_TEST registry entry answers approved_paper for exactly one (id, version); the caps live in smoke_test.run, not the registry."""
    from tradelab.brokers.ibkr_paper import smoke_test as S
    assert S.smoke_registry("SMOKE_TEST", "1") == "approved_paper"
    for sid, ver in (("SMOKE_TEST", "2"), ("smoke_test", "1"), ("SMOKE_TEST ", "1"), ("stratA", "v1"), ("", "")):
        assert S.smoke_registry(sid, ver) is None
    assert S.MAX_QTY == Dc(1) and S.SPY_CON_ID == 756733


def test_two_services_cannot_drive_the_same_broker_account(tmp_path):
    b = SimulatedBroker()
    make(tmp_path, name="one.db", broker=b)
    with pytest.raises(Exception):
        make(tmp_path, name="two.db", broker=b)
