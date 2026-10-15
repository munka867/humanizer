"""Harness for tests/test_paper_*.py: PAPER-mode ExecutionService + IbkrPaperBroker over FakeIB. No network, no ib_async."""
import json
from datetime import timedelta
from decimal import Decimal as Dc

from exec_common import POLICY, SPY, QQQ, T0, Clock, entry  # noqa
from fake_ib_orders import MODULE, FakeIB
from tradelab.brokers.ibkr_paper.adapter import ContractSpec, IbkrPaperBroker
from tradelab.execution.broker import AccountInfo
from tradelab.execution.common import sha256_hex
from tradelab.execution.modes import KEY_ENV, Mode, make_approval
from tradelab.execution.risk import RiskEngine
from tradelab.execution.service import ExecutionService

KEY = b"fake-approval-key-for-tests-only"
ENV = {KEY_ENV: KEY.decode()}
ACCT = "DUFAKE001"


def approval(tmp_path, account=ACCT, instruments=(SPY, QQQ), sid="stratA", ver="v1", policy=POLICY, clock_t=T0):
    p = tmp_path / "paper_approval.json"
    p.write_text(json.dumps(make_approval(KEY, account_id=account, instruments=list(instruments), strategy_id=sid, strategy_version=ver,
                                          capital_allocation="5000", risk_policy_sha256=sha256_hex(policy.read_bytes()),
                                          issued_utc=clock_t - timedelta(hours=1), expires_utc=clock_t + timedelta(hours=8))))
    return p


class Proxy:
    """Wraps an adapter. hold=True captures the OrderRequest without sending (for permit tests)."""
    is_simulated = False

    def __init__(self, inner, account_override=None):
        self.inner, self.account_override, self.hold, self.captured = inner, account_override, False, []

    def __getattr__(self, n):
        return getattr(self.inner, n)

    def account(self):
        a = self.inner.account()
        return AccountInfo(self.account_override, a.equity, a.available_funds, a.ts) if self.account_override else a

    def submit(self, req):
        self.captured.append(req)
        if self.hold:
            from tradelab.execution.broker import BrokerTimeout
            raise BrokerTimeout("held by test")
        return self.inner.submit(req)


def make_paper(tmp_path, *, name="p.db", fake=None, clock=None, account=ACCT, expected=ACCT, margin_hook="adapter", contracts=None,
               proxy=False, account_override=None, **adapter_kw):
    clock = clock or Clock()
    fake = fake or FakeIB()
    mod = type("M", (), {"IB": staticmethod(lambda f=fake: f), "Stock": MODULE.Stock, "Order": MODULE.Order})
    adapter = IbkrPaperBroker(contracts or {SPY: ContractSpec("SPY"), QQQ: ContractSpec("QQQ")}, ib_module=mod, expected_account=expected,
                              clock=clock, **adapter_kw)
    adapter.connect()
    port = Proxy(adapter, account_override) if proxy else adapter
    hook = adapter.margin_hook if margin_hook == "adapter" else margin_hook
    svc = ExecutionService(str(tmp_path / name), Mode.PAPER, port, RiskEngine.from_file(POLICY), lambda cid: adapter.quote(cid), clock,
                           registry_state=lambda s, v: "approved_paper", risk_policy_path=POLICY,
                           approval_path=approval(tmp_path, account=account_override or account, clock_t=clock()), env=ENV, margin_hook=hook,
                           correlations={(SPY, QQQ): Dc("0.9")})
    return svc, adapter, fake, clock, port
