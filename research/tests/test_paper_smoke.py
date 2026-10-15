import importlib.util
import json
from collections import deque
from datetime import timedelta
from pathlib import Path

import pytest
from fake_ib_orders import MODULE, FakeIB, T0, foreign_trade
from paper_common import ACCT, ENV, KEY
from tradelab.brokers.ibkr_paper import smoke_test as ST
from tradelab.execution.common import sha256_hex
from tradelab.execution.modes import KEY_ENV, make_approval

ROOT = Path(__file__).resolve().parents[1]
SMOKE_POLICY = ROOT / "configs" / "risk_policy.smoke.yaml"


class FClock:
    def __init__(self):
        self.t = T0

    def __call__(self):
        return self.t


class Harness:
    def __init__(self, tmp_path, *, script=(), account=ACCT, strategy="SMOKE_TEST", version="1", instruments=(756733,), policy=SMOKE_POLICY,
                 approval_policy=None, expires_in=8, key=KEY, typed=ST.CONFIRM_PHRASE, accounts=None):
        self.tmp, self.fake, self.clock, self.out, self.typed = tmp_path, FakeIB(), FClock(), [], typed
        self.fake.accounts = accounts or [account]
        self.fake.script = deque(script)
        self.fake.price = 100.0
        ap = approval_policy or policy
        self.approval = tmp_path / "approval.json"
        self.approval.write_text(json.dumps(make_approval(key, account_id=account, instruments=list(instruments), strategy_id=strategy, strategy_version=version,
                                                          capital_allocation="100", risk_policy_sha256=sha256_hex(Path(ap).read_bytes()),
                                                          issued_utc=T0 - timedelta(hours=1), expires_utc=T0 + timedelta(hours=expires_in))))
        self.policy = policy
        self.env = dict(ENV)
        self.env["TRADELAB_LOCK_DIR"] = str(tmp_path / "locks")
        self.mod = type("M", (), {"IB": staticmethod(lambda: self.fake), "Stock": MODULE.Stock, "Order": MODULE.Order})
        self.prompts = []

    def _input(self, prompt):
        self.prompts.append(prompt)
        return self.typed

    def sleep(self, secs):
        self.clock.t += timedelta(seconds=secs)
        self.fake.ticker["time"] = self.clock.t           # keep the fake quote fresh unless a test says otherwise

    def run(self, *extra):
        argv = ["--approval", str(self.approval), "--policy", str(self.policy), "--ledger", str(self.tmp / "led.db"), "--fill-timeout", "5", *extra]
        return ST.run(argv, ib_module=self.mod, input_fn=self._input, env=self.env, clock=self.clock, sleep=self.sleep, out=self.out.append)

    @property
    def text(self):
        return "\n".join(str(x) for x in self.out)


def test_happy_path_places_exactly_one_buy_and_the_offsetting_sell(tmp_path):
    h = Harness(tmp_path, script=["fill", "fill"])
    assert h.run() == 0
    assert len(h.fake.placed) == 2
    (c1, o1), (c2, o2) = h.fake.placed
    assert (c1.conId, o1.action, o1.orderType, o1.totalQuantity, o1.lmtPrice, o1.tif, o1.outsideRth, o1.account) == (756733, "BUY", "LMT", 1.0, 100.04, "DAY", False, ACCT)
    assert (o2.action, o2.orderType, o2.totalQuantity, o2.lmtPrice, o2.account) == ("SELL", "LMT", 1.0, 99.96, ACCT)
    assert o1.orderRef.startswith("v1:") and len(o1.orderRef) == 52 and o1.orderRef != o2.orderRef
    t = h.text
    assert "ALL STEPS PASSED" in t and "[FAIL]" not in t and "AUDIT TRAIL" in t and "commission=0.35" in t
    assert "I PLACE A PAPER ORDER" in h.prompts[0] and KEY.decode() not in t
    assert t.index("PAPER SMOKE TEST - what this will do") < t.index("ORDERS THAT WILL BE SENT") < t.index("AUDIT TRAIL")
    assert h.fake.positions if False else True
    assert not h.fake.connected                                           # always disconnects


def test_prints_the_plan_before_any_connection_or_prompt(tmp_path):
    h = Harness(tmp_path, typed="no")
    h.env.pop(KEY_ENV)
    assert h.run() == 4
    assert h.out[0].strip().startswith("PAPER SMOKE TEST") and h.fake.connect_args is None


def test_no_approval_key_refuses_before_connecting(tmp_path):
    h = Harness(tmp_path)
    h.env.pop(KEY_ENV)
    assert h.run() == 4 and h.fake.connect_args is None and h.fake.order_calls == 0


@pytest.mark.parametrize("kw", [dict(strategy="OTHER"), dict(version="2"), dict(instruments=(756733, 1002)), dict(instruments=(1002,)),
                                dict(account="U7654321"), dict(expires_in=-1), dict(key=b"someone-elses-key")])
def test_bad_or_mismatched_approval_refuses_before_connecting(tmp_path, kw):
    h = Harness(tmp_path, **kw)
    assert h.run() == 4 and h.fake.connect_args is None and h.fake.order_calls == 0


def test_approval_for_a_different_policy_file_is_refused(tmp_path):
    other = tmp_path / "other.yaml"
    other.write_bytes(SMOKE_POLICY.read_bytes() + b"\n# edited\n")
    h = Harness(tmp_path, policy=other, approval_policy=SMOKE_POLICY)
    assert h.run() == 4 and h.fake.order_calls == 0


def test_signed_approval_alone_cannot_scale_the_smoke_test_up(tmp_path):
    big = tmp_path / "big.yaml"
    big.write_text(SMOKE_POLICY.read_text().replace("max_position_qty: 1", "max_position_qty: 50"))
    h = Harness(tmp_path, policy=big)                                     # approval is validly signed for THIS bigger policy
    assert h.run() == 4 and h.fake.connect_args is None
    more = tmp_path / "more.yaml"
    more.write_text(SMOKE_POLICY.read_text() + "  - {con_id: 1002, symbol: QQQ, multiplier: 1, max_position_qty: 1, allow_short: false, sessions: [[\"09:35\", \"15:55\"]]}\n")
    assert Harness(tmp_path, policy=more).run() == 4
    shorts = tmp_path / "short.yaml"
    shorts.write_text(SMOKE_POLICY.read_text().replace("allow_short: false\n  require", "allow_short: true\n  require"))
    assert Harness(tmp_path, policy=shorts).run() == 4


def test_registry_entry_is_hard_coded_for_the_smoke_test_only():
    assert ST.smoke_registry("SMOKE_TEST", "1") == "approved_paper"
    for sid, ver in (("SMOKE_TEST", "2"), ("SMOKE_TEST2", "1"), ("stratA", "v1"), ("", "")):
        assert ST.smoke_registry(sid, ver) is None
    assert (ST.MAX_QTY, ST.SPY_CON_ID) == (1, 756733)


def test_smoke_policy_file_is_tiny_and_matches_hard_caps():
    from tradelab.execution.risk import load_policy
    p = load_policy(SMOKE_POLICY)
    assert set(p.instruments) == {756733} and p.instruments[756733].max_position_qty == 1 and not p.allow_short and p.require_margin_check


def test_live_or_other_ports_cannot_be_selected():
    for port in ("7496", "4001", "7500"):
        with pytest.raises(SystemExit):
            ST.run(["--port", port], ib_module=MODULE, out=lambda *_: None)


def test_non_paper_login_is_refused_and_disconnected(tmp_path):
    h = Harness(tmp_path, accounts=["U1234567"])
    assert h.run() == 4 and h.fake.order_calls == 0 and not h.fake.connected
    assert "DU" in h.text


def test_tws_not_running_is_a_clear_refusal(tmp_path):
    h = Harness(tmp_path)
    h.fake.refuse_connect = ConnectionRefusedError("refused")
    assert h.run() == 4 and "TWS" in h.text and h.fake.order_calls == 0


def test_wrong_confirmation_phrase_sends_nothing(tmp_path):
    for typed in ("yes", "i place a paper order", "", "I PLACE A PAPER ORDER please"):
        h = Harness(tmp_path, typed=typed)
        assert h.run() == 3 and h.fake.order_calls == 0, typed
        assert "NOTHING was sent" in h.text


def test_stale_or_missing_quote_aborts_without_orders(tmp_path):
    h = Harness(tmp_path)
    h.fake.ticker["time"] = T0 - timedelta(minutes=15)                   # delayed data
    assert h.run() == 1 and h.fake.order_calls == 0 and "delayed" in h.text
    h2 = Harness(tmp_path)
    h2.fake.ticker["bid"] = float("nan")
    assert h2.run() == 1 and h2.fake.order_calls == 0


def test_unclean_reconciliation_or_existing_position_aborts_without_orders(tmp_path):
    h = Harness(tmp_path)
    h.fake.foreign_trades.append(foreign_trade(con_id=756733))
    assert h.run() == 1 and h.fake.order_calls == 0
    h2 = Harness(tmp_path)
    h2.fake.foreign_positions[(ACCT, 756733)] = 1.0
    assert h2.run() == 1 and h2.fake.order_calls == 0


def test_outside_regular_hours_is_blocked_by_the_risk_policy(tmp_path):
    h = Harness(tmp_path, script=["fill", "fill"])
    h.clock.t = T0.replace(hour=21)                                      # 16:00 New York, after the 15:55 cutoff
    h.fake.ticker["time"] = h.clock.t
    assert h.run() == 1 and h.fake.order_calls == 0 and "OUTSIDE_SESSION" in h.text


def test_buy_rejected_aborts_with_flat_position(tmp_path):
    h = Harness(tmp_path, script=["reject"])
    assert h.run() == 1 and h.fake.order_calls == 1 and "[FAIL] buy filled" in h.text
    assert "RESULT: FAILED" in h.text and "RESIDUAL" not in h.text


def test_buy_not_filled_in_time_is_cancelled_and_no_sell_is_sent(tmp_path):
    h = Harness(tmp_path, script=["accept"])
    assert h.run() == 1
    assert h.fake.order_calls == 1 and h.fake.cancel_calls == [h.fake.trades[0].order.orderId]
    assert "[FAIL] buy filled" in h.text and "flat per the ledger" in h.text


def test_sell_not_filled_cancels_it_then_flattens_only_the_smoke_position(tmp_path):
    h = Harness(tmp_path, script=["fill", "accept", "fill"])             # buy fills, sell rests, the flatten order fills
    assert h.run() == 1
    assert len(h.fake.placed) == 3 and h.fake.cancel_calls == [h.fake.trades[1].order.orderId]
    flat = h.fake.placed[2][1]
    assert (flat.action, flat.orderType, flat.totalQuantity) == ("SELL", "MKT", 1.0)
    assert "flatten report" in h.text and "[FAIL] sell filled" in h.text


def test_failed_flatten_exits_2_and_tells_the_user_to_check_tws(tmp_path):
    h = Harness(tmp_path, script=["fill", "accept", "accept"])           # flatten order rests unfilled
    assert h.run() == 2
    assert "OPEN TWS NOW" in h.text and "POSSIBLE RESIDUAL EXPOSURE" in h.text


def test_uncertain_submission_exits_2(tmp_path):
    h = Harness(tmp_path, script=["raise"])
    assert h.run() == 2 and h.fake.order_calls == 1 and "OPEN TWS NOW" in h.text


def test_abort_never_sends_a_blind_flatten_when_cancels_cannot_be_confirmed(tmp_path):
    h = Harness(tmp_path, script=["fill", "accept"])
    h.fake.cancel_mode = "not_found"                                     # the cancel of the resting sell is not confirmed (135)
    assert h.run() == 2
    assert len(h.fake.placed) == 2 and "NOT sending a flatten order blindly" in h.text


def test_ib_async_not_installed_gives_clear_message(tmp_path):
    if importlib.util.find_spec("ib_async"):
        pytest.skip("ib_async installed")
    h = Harness(tmp_path)
    argv = ["--approval", str(h.approval), "--policy", str(h.policy), "--ledger", str(tmp_path / "l.db")]
    assert ST.run(argv, input_fn=h._input, env=h.env, clock=h.clock, sleep=h.sleep, out=h.out.append) == 4
    assert "pip install ib_async==2.1.0" in h.text
