import dataclasses
import inspect
from datetime import timedelta
from decimal import Decimal as Dc

import pytest
from exec_common import *  # noqa
from tradelab.execution import risk as R
from tradelab.execution.risk import Quote, RiskEngine, RiskInputs

ENG = RiskEngine.from_file(POLICY)


def ev(p=None, **kw):
    return ENG.evaluate(p or entry(), base_inputs(**kw))


def test_baseline_entry_is_allowed():
    d = ev()
    assert d.allowed and not d.reasons and d.policy_hash == ENG.policy.sha256 and len(d.policy_hash) == 64


Q = lambda px, t=T0, spread="0.02": Quote(Dc(px) - Dc(spread) / 2, Dc(px) + Dc(spread) / 2, Dc(px), t)

BREACHES = {
    "MAX_RISK_PER_TRADE": dict(p=entry(stop="90")),                                   # 10 * ~10 = 100 > 50
    "DAILY_LOSS_LIMIT": dict(kw=dict(day_start_equity=Dc(10100))),                    # lost 100 today
    "MAX_DRAWDOWN": dict(kw=dict(peak_equity=Dc(10200))),                             # 200 below peak
    "MAX_POSITION": dict(p=entry(qty=51)),
    "MAX_GROSS_EXPOSURE": dict(kw=dict(positions={QQQ: Dc(40)}, quotes={SPY: Q(100), QQQ: Q(520)})),   # 20800
    "MAX_NET_EXPOSURE": dict(kw=dict(positions={QQQ: Dc(40)}, quotes={SPY: Q(100), QQQ: Q(520)})),
    "MAX_CORRELATED_EXPOSURE": dict(p=entry(qty=40), kw=dict(positions={QQQ: Dc(50)}, quotes={SPY: Q(100), QQQ: Q(250)})),
    "ORDER_FREQUENCY_PER_MINUTE": dict(kw=dict(recent_order_times=tuple(T0 - timedelta(seconds=s) for s in range(5)))),
    "ORDER_FREQUENCY_PER_DAY": dict(kw=dict(recent_order_times=tuple(T0 - timedelta(minutes=10 + i) for i in range(50)))),
    "INSTRUMENT_NOT_ALLOWED": dict(p=entry(con=9999)),
    "OUTSIDE_SESSION": dict(kw=dict(now_utc=T0.replace(hour=23), account_ts=T0.replace(hour=23), quotes={SPY: Q(100, T0.replace(hour=23)), QQQ: Q(100, T0.replace(hour=23))})),
    "SPREAD_TOO_WIDE": dict(kw=dict(quotes={SPY: Q(100, spread="2"), QQQ: Q(100)})),
    "SLIPPAGE_TOO_LARGE": dict(p=dataclasses.replace(entry(), order_type="LMT", limit_px=Dc("101"))),   # 100 bps from mid
    "ACCOUNT_HEADROOM": dict(kw=dict(available_funds=Dc(1000))),
    "SHORTING_NOT_ALLOWED": dict(p=entry(side=-1)),
    "MARGIN_CHECK_FAILED_OR_MISSING": dict(kw=dict(margin_ok=False)),
    "NO_STOP_FOR_RISK": dict(p=entry(stop=None)),
}


@pytest.mark.parametrize("reason", sorted(BREACHES))
def test_breached_limit_prevents_entry__each_limit_separately(reason):
    spec = BREACHES[reason]
    d = ev(spec.get("p"), **spec.get("kw", {}))
    assert not d.allowed, reason
    assert any(r == reason or r.startswith(reason) for r in d.reasons), (reason, d.reasons)


def test_short_needs_policy_and_broker_confirmation():
    pol = dataclasses.replace(ENG.policy, allow_short=True,
                              instruments={k: dataclasses.replace(v, allow_short=True) for k, v in ENG.policy.instruments.items()})
    e = RiskEngine(pol)
    p = entry(side=-1)
    assert "NOT_CONFIRMED_SHORTABLE" in e.evaluate(p, base_inputs(shortable={SPY: None})).reasons
    assert "NOT_CONFIRMED_SHORTABLE" in e.evaluate(p, base_inputs(shortable={SPY: False})).reasons
    assert e.evaluate(p, base_inputs(shortable={SPY: True})).allowed


@pytest.mark.parametrize("name", ["equity", "available_funds", "account_ts", "peak_equity", "day_start_equity", "positions", "quotes"])
def test_missing_risk_input_blocks_entries(name):
    d = ev(**{name: None})
    assert not d.allowed and f"MISSING_INPUT:{name}" in d.reasons


def test_stale_inputs_block_entries_and_missing_quote_or_correlation_blocks():
    old = T0 - timedelta(seconds=31)
    assert "STALE_QUOTE" in ev(quotes={SPY: Q(100, old), QQQ: Q(100)}).reasons
    assert "STALE_ACCOUNT" in ev(account_ts=old).reasons
    assert "MISSING_INPUT:quote" in ev(quotes={QQQ: Q(100)}).reasons
    d = ev(positions={QQQ: Dc(5)}, correlations={})
    assert any(r.startswith("MISSING_INPUT:correlation") for r in d.reasons)


def test_missing_or_stale_inputs_block_entries_but_allow_reducing_exits():
    pos = {SPY: Dc(10)}
    sell = entry(signal_id="x", qty=10, side=-1, stop=None, purpose="exit")
    for kw in (dict(equity=None), dict(quotes={}), dict(account_ts=T0 - timedelta(hours=1)), dict(quotes=None),
               dict(day_start_equity=Dc(99999)), dict(peak_equity=Dc(99999))):
        d = ev(sell, positions=pos, **kw)
        assert d.allowed and d.reducing, kw
    # entry under the same conditions is blocked, and a flip (sell more than held) is NOT reducing
    assert not ev(entry(), positions=pos, equity=None).allowed
    flip = entry(signal_id="y", qty=15, side=-1, stop=None, purpose="exit")
    d = ev(flip, positions=pos, equity=None)
    assert not d.allowed and not d.reducing


def test_decisions_carry_policy_hash_and_version():
    d = ev()
    assert d.policy_version == "example-1" and d.policy_hash == ENG.policy.sha256


def test_limits_cannot_be_raised_through_any_api():
    with pytest.raises(dataclasses.FrozenInstanceError):
        ENG.policy.max_daily_loss = Dc(10**9)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ENG.policy.instruments[SPY].max_position_qty = Dc(10**9)  # frozen dataclass: attribute assignment refused
    with pytest.raises(AttributeError):
        ENG.policy = ENG.policy
    public = [n for n, _ in inspect.getmembers(RiskEngine, inspect.isfunction) if not n.startswith("_")]
    assert set(public) <= {"evaluate", "from_file", "trading_date"}
    with pytest.raises(TypeError):
        ENG.policy.instruments[4242] = ENG.policy.instruments[SPY]
    assert not any(w in n for n in public for w in ("set", "raise", "update", "limit", "override"))


def test_policy_file_validation_and_hash(tmp_path):
    bad = tmp_path / "bad.yaml"
    for txt in ("not: a policy", "schema: risk_policy.v1\nversion: x\ntimezone: Nowhere/Land\nlimits: {}\ninstruments: []"):
        bad.write_text(txt)
        with pytest.raises(R.PolicyError):
            R.load_policy(bad)
    p2 = tmp_path / "p.yaml"
    p2.write_bytes(POLICY.read_bytes().replace(b"max_daily_loss: 100", b"max_daily_loss: 9999"))
    assert R.load_policy(p2).sha256 != ENG.policy.sha256 and R.load_policy(p2).max_daily_loss == Dc(9999)


def test_daily_loss_trading_date_uses_new_york():
    from datetime import datetime, timezone
    # 2026-01-06 03:00 UTC is still 2026-01-05 in New York
    assert str(ENG.trading_date(datetime(2026, 1, 6, 3, 0, tzinfo=timezone.utc))) == "2026-01-05"
