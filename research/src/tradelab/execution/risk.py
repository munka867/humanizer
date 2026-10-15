"""Deterministic risk engine. Policy = YAML file, validated, versioned, hashed (sha256 of the file bytes).

Rules
* MISSING or STALE inputs BLOCK new exposure. Orders that strictly REDUCE an existing position (never flipping it) stay allowed.
* Every decision records reasons + policy hash. Daily loss uses the trading date in the policy timezone (America/New_York).
* Limits change ONLY by loading a different policy file into a NEW RiskEngine. RiskPolicy is a frozen dataclass and the engine has
  no setter / raise-limit API; strategies never receive a reference to the engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping
from zoneinfo import ZoneInfo

import yaml

from tradelab.execution.common import D, canon, iso, sha256_hex
from tradelab.execution.intents import ENTRY, PROTECTIVE, Proposal

DEC_KEYS = ("max_risk_per_trade", "max_daily_loss", "max_drawdown", "max_gross_exposure", "max_net_exposure",
            "max_correlated_exposure", "correlation_threshold", "max_spread_bps", "max_slippage_bps", "min_account_headroom")


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class InstrumentRule:
    con_id: int
    symbol: str
    multiplier: Decimal
    max_position_qty: Decimal
    allow_short: bool
    sessions: tuple  # ((time_start, time_end), ...) local policy timezone, half-open


@dataclass(frozen=True)
class RiskPolicy:
    version: str
    timezone: str
    max_risk_per_trade: Decimal
    max_daily_loss: Decimal
    max_drawdown: Decimal
    max_gross_exposure: Decimal
    max_net_exposure: Decimal
    max_correlated_exposure: Decimal
    correlation_threshold: Decimal
    max_orders_per_minute: int
    max_orders_per_day: int
    max_spread_bps: Decimal
    max_slippage_bps: Decimal
    max_data_age_seconds: int
    min_account_headroom: Decimal       # fraction of equity that must remain as available funds
    allow_short: bool
    require_margin_check: bool
    instruments: Mapping[int, InstrumentRule]
    sha256: str = ""

    @property
    def hash(self) -> str:
        return self.sha256


def _t(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def load_policy(path) -> RiskPolicy:
    raw_bytes = Path(path).read_bytes()
    try:
        y = yaml.safe_load(raw_bytes)
    except yaml.YAMLError as e:
        raise PolicyError(f"unparseable policy: {e}") from e
    if not isinstance(y, dict):
        raise PolicyError("policy must be a mapping")
    need = ("schema", "version", "timezone", "limits", "instruments")
    for k in need:
        if k not in y:
            raise PolicyError(f"missing key {k}")
    if y["schema"] != "risk_policy.v1":
        raise PolicyError("unsupported schema")
    try:
        ZoneInfo(y["timezone"])
    except Exception as e:
        raise PolicyError(f"bad timezone: {e}") from e
    L = y["limits"]
    need_l = list(DEC_KEYS) + ["max_orders_per_minute", "max_orders_per_day", "max_data_age_seconds", "allow_short", "require_margin_check"]
    missing = [k for k in need_l if k not in L]
    if missing:
        raise PolicyError(f"missing limits: {missing}")
    vals = {k: D(L[k]) for k in DEC_KEYS}
    if any(v < 0 for v in vals.values()):
        raise PolicyError("limits must be non-negative")
    if not (Decimal(0) <= vals["min_account_headroom"] <= 1):
        raise PolicyError("min_account_headroom is a fraction in [0,1]")
    if not y["instruments"]:
        raise PolicyError("allowed instrument list is empty")
    inst = {}
    for r in y["instruments"]:
        sess = tuple((_t(a), _t(b)) for a, b in r["sessions"])
        rule = InstrumentRule(int(r["con_id"]), str(r["symbol"]), D(r["multiplier"]), D(r["max_position_qty"]),
                              bool(r.get("allow_short", False)), sess)
        if rule.multiplier <= 0 or rule.max_position_qty <= 0 or not sess:
            raise PolicyError(f"bad instrument rule {rule.symbol}")
        inst[rule.con_id] = rule
    return RiskPolicy(version=str(y["version"]), timezone=y["timezone"], max_orders_per_minute=int(L["max_orders_per_minute"]),
                      max_orders_per_day=int(L["max_orders_per_day"]), max_data_age_seconds=int(L["max_data_age_seconds"]),
                      allow_short=bool(L["allow_short"]), require_margin_check=bool(L["require_margin_check"]),
                      instruments=MappingProxyType(inst), sha256=sha256_hex(raw_bytes), **vals)


@dataclass(frozen=True)
class Quote:
    bid: Decimal | None
    ask: Decimal | None
    last: Decimal | None
    ts: datetime | None

    @property
    def mid(self):
        return (self.bid + self.ask) / 2 if self.bid is not None and self.ask is not None else self.last


@dataclass
class RiskInputs:
    """Snapshot assembled by the service. None / absent => 'missing' => block new exposure."""
    now_utc: datetime
    equity: Decimal | None = None
    available_funds: Decimal | None = None
    account_ts: datetime | None = None
    peak_equity: Decimal | None = None
    day_start_equity: Decimal | None = None
    positions: Mapping[int, Decimal] | None = None          # from FILLS
    quotes: Mapping[int, Quote] | None = None
    shortable: Mapping[int, bool | None] | None = None
    correlations: Mapping[tuple, Decimal] | None = None     # {(a,b): rho}, symmetric lookup
    recent_order_times: tuple = ()                           # UTC datetimes of submissions today
    margin_ok: bool | None = None                            # result of the margin hook for THIS proposal
    working_same_side: Mapping[tuple, Decimal] | None = None  # {(con_id, side): remaining qty of LIVE orders (submitting/working/cancel-pending/uncertain)}
    mismatch_con_ids: frozenset = frozenset()                # instruments with an unacknowledged ledger/broker position mismatch
    capital_cap: Decimal | None = None                       # approval's capital_allocation (hard cap on gross exposure), None = no approval cap
    frequency_exempt: bool = False                           # service-internal (operator flatten); strategies cannot set it


@dataclass(frozen=True)
class RiskDecision:
    decision_id: str
    allowed: bool
    reducing: bool
    reasons: tuple
    policy_hash: str
    policy_version: str
    ts: str
    details: dict = field(default_factory=dict)


def is_reducing(positions: Mapping[int, Decimal] | None, con_id: int, side: int, qty: Decimal, working: Decimal = Decimal(0),
                mismatch: bool = False) -> bool:
    """True only if the order is opposite to the FILLED position and fits in the remaining reducing capacity
    |position| - (remaining qty of live same-side orders). Stacked exits therefore cannot flip a position. The exemption is
    withdrawn on an instrument with an unresolved ledger/broker mismatch."""
    if not positions or mismatch:
        return False
    cur = positions.get(con_id, Decimal(0))
    return cur != 0 and (cur > 0) == (side < 0) and D(qty) <= abs(cur) - D(working)


class RiskEngine:
    def __init__(self, policy: RiskPolicy):
        self._policy = policy

    @classmethod
    def from_file(cls, path) -> "RiskEngine":
        return cls(load_policy(path))

    @property
    def policy(self) -> RiskPolicy:
        return self._policy

    def trading_date(self, now_utc: datetime):
        return now_utc.astimezone(ZoneInfo(self._policy.timezone)).date()

    def evaluate(self, p: Proposal, x: RiskInputs, intent_id: str | None = None) -> RiskDecision:
        pol = self._policy
        qty, reasons = D(p.qty), []
        working = (x.working_same_side or {}).get((p.con_id, p.side), Decimal(0))
        reducing = is_reducing(x.positions, p.con_id, p.side, qty, working, p.con_id in x.mismatch_con_ids)
        details: dict = {"qty": str(qty), "con_id": p.con_id, "side": p.side, "purpose": p.purpose, "working_same_side": str(working)}
        if not reducing:
            if p.purpose != ENTRY:       # a label never exempts: exit/protective orders that are not truly reducing get every entry check
                reasons.append("NOT_REDUCING:exit/protective order exceeds the position, working exits, or the position is unreconciled")
            sub: list = []
            self._check_new_exposure(p, x, qty, sub, details)
            reasons.extend(sub)
        # P7-09: a strictly reducing PROTECTIVE order can only shrink exposure (bounded by the position), so the order-frequency limits
        # never starve a stop. Entries, plain exits (the operator flatten is exempt separately) and anything not truly reducing stay limited.
        protective_reducing = reducing and p.purpose == PROTECTIVE
        if x.now_utc is not None and not x.frequency_exempt and not protective_reducing:
            self._check_frequency(x, reasons)
        return RiskDecision(decision_id=sha256_hex(canon([intent_id, iso(x.now_utc), reasons, details]))[:24],
                            allowed=not reasons, reducing=reducing, reasons=tuple(reasons), policy_hash=pol.sha256,
                            policy_version=pol.version, ts=iso(x.now_utc), details=details)

    # ------------------------------------------------------------------
    def _check_new_exposure(self, p, x: RiskInputs, qty, R: list, det: dict):
        pol = self._policy
        rule = pol.instruments.get(p.con_id)
        if rule is None:
            R.append("INSTRUMENT_NOT_ALLOWED")
            return
        # ---- missing / stale inputs
        for name in ("equity", "available_funds", "account_ts", "peak_equity", "day_start_equity", "positions", "quotes"):
            if getattr(x, name) is None:
                R.append(f"MISSING_INPUT:{name}")
        if R:
            return
        age = lambda ts: (x.now_utc - ts).total_seconds()
        if age(x.account_ts) > pol.max_data_age_seconds or age(x.account_ts) < -5:
            R.append("STALE_ACCOUNT")
        q = x.quotes.get(p.con_id)
        if q is None or q.ts is None or q.mid is None:
            R.append("MISSING_INPUT:quote")
            return
        if age(q.ts) > pol.max_data_age_seconds or age(q.ts) < -5:
            R.append("STALE_QUOTE")
        if R:
            return
        mid = D(q.mid)
        # ---- session (policy timezone)
        local = x.now_utc.astimezone(ZoneInfo(pol.timezone)).time()
        if not any(a <= local < b for a, b in rule.sessions):
            R.append("OUTSIDE_SESSION")
        # ---- spread / slippage
        if q.bid is None or q.ask is None:
            R.append("MISSING_INPUT:bid_ask")
        else:
            spread_bps = (D(q.ask) - D(q.bid)) / mid * 10000
            det["spread_bps"] = str(spread_bps)
            if spread_bps > pol.max_spread_bps:
                R.append("SPREAD_TOO_WIDE")
            if p.order_type == "LMT":
                slip = abs(D(p.limit_px) - mid) / mid * 10000
            else:
                slip = spread_bps / 2
            det["expected_slippage_bps"] = str(slip)
            if slip > pol.max_slippage_bps:
                R.append("SLIPPAGE_TOO_LARGE")
        # ---- position / short
        cur = x.positions.get(p.con_id, Decimal(0))
        pending = (x.working_same_side or {}).get((p.con_id, p.side), Decimal(0))      # live same-side orders will add to the position if filled
        post = cur + p.side * (pending + qty)
        det["post_position_incl_pending"] = str(post)
        if abs(post) > rule.max_position_qty:
            R.append("MAX_POSITION")
        if post < 0:
            if not (pol.allow_short and rule.allow_short):
                R.append("SHORTING_NOT_ALLOWED")
            elif (x.shortable or {}).get(p.con_id) is not True:
                R.append("NOT_CONFIRMED_SHORTABLE")
        # ---- per-trade risk (needs a planned stop)
        if p.stop_px is None:                  # every non-reducing order, whatever its purpose label says
            R.append("NO_STOP_FOR_RISK")
        else:
            ref = D(p.limit_px) if p.order_type == "LMT" else mid
            risk = abs(ref - D(p.stop_px)) * qty * rule.multiplier
            det["trade_risk"] = str(risk)
            if risk > pol.max_risk_per_trade:
                R.append("MAX_RISK_PER_TRADE")
        # ---- account level
        daily_loss = x.day_start_equity - x.equity
        det["daily_loss"] = str(daily_loss)
        if daily_loss >= pol.max_daily_loss:
            R.append("DAILY_LOSS_LIMIT")
        dd = x.peak_equity - x.equity
        det["drawdown"] = str(dd)
        if dd >= pol.max_drawdown:
            R.append("MAX_DRAWDOWN")
        if x.equity <= 0 or x.available_funds / x.equity < pol.min_account_headroom:
            R.append("ACCOUNT_HEADROOM")
        if pol.require_margin_check and x.margin_ok is not True:
            R.append("MARGIN_CHECK_FAILED_OR_MISSING")
        # ---- exposure (notional = |qty| * mid * multiplier, using quotes for every held instrument)
        pos_after = dict(x.positions)
        pos_after[p.con_id] = post
        # P7-04: unfilled orders in OTHER instruments count too (worst case per instrument: whichever fill direction grows |position|)
        for ocid in {c for (c, _s) in (x.working_same_side or {}) if c != p.con_id}:
            base = x.positions.get(ocid, Decimal(0))
            up = base + (x.working_same_side or {}).get((ocid, 1), Decimal(0))
            dn = base - (x.working_same_side or {}).get((ocid, -1), Decimal(0))
            pos_after[ocid] = up if abs(up) >= abs(dn) else dn
        notion, missing = {}, []
        for cid, qv in pos_after.items():
            if qv == 0:
                continue
            qq = x.quotes.get(cid)
            r = pol.instruments.get(cid)
            if qq is None or qq.mid is None or r is None:
                missing.append(cid)
                continue
            notion[cid] = qv * D(qq.mid) * r.multiplier
        if missing:
            R.append("MISSING_INPUT:exposure_quote")
        else:
            gross, net = sum((abs(v) for v in notion.values()), Decimal(0)), sum(notion.values(), Decimal(0))
            det.update(gross=str(gross), net=str(net))
            cap = pol.max_gross_exposure if x.capital_cap is None else min(pol.max_gross_exposure, x.capital_cap)
            det["gross_cap"] = str(cap)
            if gross > cap:
                R.append("MAX_GROSS_EXPOSURE" if cap == pol.max_gross_exposure else "CAPITAL_ALLOCATION_EXCEEDED")
            if abs(net) > pol.max_net_exposure:
                R.append("MAX_NET_EXPOSURE")
            corr_sum = abs(notion.get(p.con_id, Decimal(0)))
            for cid, v in notion.items():
                if cid == p.con_id:
                    continue
                rho = (x.correlations or {}).get((p.con_id, cid), (x.correlations or {}).get((cid, p.con_id)))
                if rho is None:
                    R.append(f"MISSING_INPUT:correlation:{p.con_id}-{cid}")
                elif abs(D(rho)) >= pol.correlation_threshold:
                    corr_sum += abs(v)
            if corr_sum > pol.max_correlated_exposure:
                R.append("MAX_CORRELATED_EXPOSURE")

    def _check_frequency(self, x: RiskInputs, R: list):
        pol = self._policy
        recent = list(x.recent_order_times)
        last_min = sum(1 for t in recent if 0 <= (x.now_utc - t).total_seconds() < 60)
        if last_min >= pol.max_orders_per_minute:
            R.append("ORDER_FREQUENCY_PER_MINUTE")
        if len(recent) >= pol.max_orders_per_day:
            R.append("ORDER_FREQUENCY_PER_DAY")
