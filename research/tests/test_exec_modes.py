import json
from dataclasses import replace
from datetime import timedelta

import pytest
from exec_common import *  # noqa
from tradelab.execution import approve as AP
from tradelab.execution.broker import SimulatedBroker
from tradelab.execution.common import sha256_hex
from tradelab.execution.modes import KEY_ENV, Mode, ModeDenied, assert_may_submit, make_approval, verify_approval

KEY = b"test-key-held-only-by-the-user"
ENV = {KEY_ENV: KEY.decode()}


def approval_file(tmp_path, **over):
    kw = dict(account_id="DUSIM0001", instruments=[SPY, QQQ], strategy_id="stratA", strategy_version="v1", capital_allocation="5000",
              risk_policy_sha256=sha256_hex(POLICY.read_bytes()), issued_utc=T0 - timedelta(hours=1), expires_utc=T0 + timedelta(hours=8))
    kw.update(over)
    p = tmp_path / "paper_approval.json"
    p.write_text(json.dumps(make_approval(KEY, **kw)))
    return p


def paper_kwargs(tmp_path, **over):
    kw = dict(broker_is_simulated=True, registry_state=lambda s, v: "approved_paper", strategy_id="stratA", strategy_version="v1",
              instruments=[SPY], risk_policy_path=POLICY, approval_path=over.get("approval_path") or approval_file(tmp_path),
              broker_account_id="DUSIM0001", now_utc=T0, env=ENV)
    kw.update(over)
    return kw


def test_live_is_always_refused(tmp_path):
    with pytest.raises(ModeDenied):
        assert_may_submit(Mode.LIVE, **paper_kwargs(tmp_path))
    with pytest.raises(ModeDenied):
        make(tmp_path, Mode.LIVE)
    with pytest.raises(ModeDenied):
        assert_may_submit("LIVE", broker_is_simulated=True, env={KEY_ENV: "x"})


def test_paper_can_be_enabled_only_when_every_gate_holds(tmp_path):
    p = assert_may_submit(Mode.PAPER, **paper_kwargs(tmp_path))
    assert p["account_id"] == "DUSIM0001"


def test_research_agent_without_key_cannot_enable_paper(tmp_path):
    with pytest.raises(ModeDenied, match="CC_APPROVAL_KEY"):
        assert_may_submit(Mode.PAPER, **paper_kwargs(tmp_path, env={}))
    # an agent that forges an approval with its own key still fails against the real key
    forged = approval_file(tmp_path)
    with pytest.raises(ModeDenied, match="HMAC"):
        assert_may_submit(Mode.PAPER, **paper_kwargs(tmp_path, approval_path=forged, env={KEY_ENV: "agent-guess"}))


@pytest.mark.parametrize("field,value", [("account_id", "DUSIM9999"), ("instruments", [SPY, QQQ, 5]), ("strategy_id", "other"),
                                         ("strategy_version", "v2"), ("capital_allocation", "500000"),
                                         ("risk_policy_sha256", "0" * 64), ("issued_utc", "2020-01-01T00:00:00+00:00"),
                                         ("expires_utc", "2099-01-01T00:00:00+00:00")])
def test_tampering_with_any_approval_field_fails(tmp_path, field, value):
    f = approval_file(tmp_path)
    rec = json.loads(f.read_text())
    rec["payload"][field] = value
    f.write_text(json.dumps(rec))
    with pytest.raises(ModeDenied, match="HMAC"):
        assert_may_submit(Mode.PAPER, **paper_kwargs(tmp_path, approval_path=f))


def test_expired_and_future_approvals_fail(tmp_path):
    f = approval_file(tmp_path, expires_utc=T0 - timedelta(minutes=1))
    with pytest.raises(ModeDenied, match="expired"):
        assert_may_submit(Mode.PAPER, **paper_kwargs(tmp_path, approval_path=f))
    f = approval_file(tmp_path, issued_utc=T0 + timedelta(hours=1), expires_utc=T0 + timedelta(hours=9))
    with pytest.raises(ModeDenied, match="future"):
        assert_may_submit(Mode.PAPER, **paper_kwargs(tmp_path, approval_path=f))


def test_paper_refused_for_wrong_state_policy_account_instrument_or_missing_record(tmp_path):
    with pytest.raises(ModeDenied, match="state"):
        assert_may_submit(Mode.PAPER, **paper_kwargs(tmp_path, registry_state=lambda s, v: "candidate"))
    with pytest.raises(ModeDenied, match="state"):
        assert_may_submit(Mode.PAPER, **paper_kwargs(tmp_path, registry_state=lambda s, v: None))
    other = tmp_path / "other_policy.yaml"
    other.write_bytes(POLICY.read_bytes() + b"\n# changed\n")
    with pytest.raises(ModeDenied, match="sha256"):
        assert_may_submit(Mode.PAPER, **paper_kwargs(tmp_path, risk_policy_path=other))
    with pytest.raises(ModeDenied, match="policy file missing"):
        assert_may_submit(Mode.PAPER, **paper_kwargs(tmp_path, risk_policy_path=tmp_path / "nope.yaml"))
    with pytest.raises(ModeDenied, match="account"):
        assert_may_submit(Mode.PAPER, **paper_kwargs(tmp_path, broker_account_id="U1234567"))
    with pytest.raises(ModeDenied, match="instrument"):
        assert_may_submit(Mode.PAPER, **paper_kwargs(tmp_path, instruments=[777]))
    with pytest.raises(ModeDenied, match="missing"):
        assert_may_submit(Mode.PAPER, **paper_kwargs(tmp_path, approval_path=tmp_path / "none.json"))


def test_non_paper_modes_need_simulated_broker_and_shadow_never_submits(tmp_path):
    for m in (Mode.DEMO, Mode.BACKTEST, Mode.REPLAY):
        assert_may_submit(m, broker_is_simulated=True)
        with pytest.raises(ModeDenied):
            assert_may_submit(m, broker_is_simulated=False)
    with pytest.raises(ModeDenied):
        assert_may_submit(Mode.SHADOW, broker_is_simulated=True)


def test_shadow_mode_records_would_be_orders_and_never_calls_broker_port(tmp_path):
    class Spy(SimulatedBroker):
        def submit(self, req):
            raise AssertionError("SHADOW must not call submit")
        def cancel(self, c):
            raise AssertionError("no cancel")
    svc, b, _ = make(tmp_path, Mode.SHADOW, broker=Spy())
    svc.start()
    r = svc.propose(entry())
    assert r.status == "SHADOWED"
    assert svc.conn.execute("SELECT COUNT(*) FROM shadow_orders").fetchone()[0] == 1
    assert svc.conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0


def test_mode_cannot_be_changed_by_a_strategy_object(tmp_path):
    svc, b, _ = make(tmp_path, Mode.DEMO)
    with pytest.raises(AttributeError):
        svc.mode = Mode.PAPER

    class Strat:
        name = "s"
        def reset(self): ...
        def on_bar(self, bar):
            return None
    s = Strat()
    # the only call a strategy receives from the runners is on_bar(bar); no handle to the service/mode is passed
    import inspect
    from tradelab.execution import replay
    assert "self.strategy.on_bar(bar_s)" in inspect.getsource(replay.ReplayRunner.run)
    assert not hasattr(s, "svc") and not hasattr(s, "mode")


def test_service_in_paper_mode_blocks_orders_without_valid_approval(tmp_path):
    svc, b, _ = make(tmp_path, Mode.PAPER, registry_state=lambda s, v: "approved_paper", risk_policy_path=POLICY,
                     approval_path=tmp_path / "missing.json", env=ENV)
    svc.start()
    r = svc.propose(entry())
    assert r.status == "BLOCKED" and svc._paper_block and "approval" in svc._paper_block      # construction-time check latched the refusal
    assert b.open_orders() == []


def test_service_in_paper_mode_with_all_gates_reaches_simulated_broker(tmp_path):
    from tradelab.execution.broker import SimulatedBroker
    svc, b, _ = make(tmp_path, Mode.PAPER, broker=SimulatedBroker(account_id="DUSIM0001"), registry_state=lambda s, v: "approved_paper",
                     risk_policy_path=POLICY, approval_path=approval_file(tmp_path), env=ENV)
    svc.start()
    assert svc.propose(entry()).status == "SUBMITTED"


def test_approve_cli_exists_refuses_without_key_and_signs_with_key(tmp_path, monkeypatch, capsys):
    with pytest.raises(SystemExit) as e:
        AP.main(["--help"])
    assert e.value.code == 0
    monkeypatch.delenv(KEY_ENV, raising=False)
    out = tmp_path / "a.json"
    argv = ["--account", "DUSIM0001", "--instruments", "1001", "--strategy-id", "stratA", "--strategy-version", "v1", "--capital", "5000",
            "--risk-policy", str(POLICY), "--out", str(out)]
    assert AP.main(argv) == 2 and not out.exists()
    monkeypatch.setenv(KEY_ENV, "k")
    assert AP.main(argv) == 0
    import datetime as dt
    payload = verify_approval(out, b"k", now_utc=dt.datetime.now(dt.timezone.utc))
    assert payload["account_id"] == "DUSIM0001"
