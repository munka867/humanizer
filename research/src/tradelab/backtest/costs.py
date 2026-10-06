"""Cost arithmetic. All inputs are ASSUMPTIONS (see configs/costs_mes.yaml)."""
from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import yaml
from tradelab.contracts import CostModel, Instrument

# fill kinds
ENTRY = "entry"            # market entry
MARKET_EXIT = "market_exit"  # time / eod / discontinuity exits
STOP_EXIT = "stop_exit"    # stop triggered -> market order, extra slippage
LIMIT_EXIT = "limit_exit"  # resting target; no spread/slippage
_KINDS = {ENTRY, MARKET_EXIT, STOP_EXIT, LIMIT_EXIT}


def commission_usd(m: CostModel, qty: int) -> float:
    """Commission + exchange/regulatory fees for ONE side."""
    return (m.commission_per_side_usd + m.exchange_fee_per_side_usd) * qty * m.cost_multiplier


def fill_slippage_usd(m: CostModel, inst: Instrument, qty: int, kind: str) -> float:
    """Half-spread + slippage (+ stop extra) in USD for one fill, excluding commission."""
    if kind not in _KINDS:
        raise ValueError(f"unknown fill kind {kind!r}")
    if kind == LIMIT_EXIT:
        return 0.0
    ticks = m.spread_ticks / 2.0 + m.slippage_ticks_per_side
    if kind == STOP_EXIT:
        ticks += m.stop_extra_slippage_ticks
    return ticks * inst.tick_size * inst.point_value * qty * m.cost_multiplier


def trade_costs_usd(m: CostModel, inst: Instrument, qty: int, exit_kind: str) -> float:
    """Total positive cost of a round trip: market entry + exit of `exit_kind`, 2 commission sides."""
    return (2 * commission_usd(m, qty)
            + fill_slippage_usd(m, inst, qty, ENTRY)
            + fill_slippage_usd(m, inst, qty, exit_kind))


def load_cost_model(path: str | Path, scenario: str = "x1") -> CostModel:
    """Load base assumptions from YAML; `scenario` selects the stress multiplier."""
    cfg = yaml.safe_load(Path(path).read_text())
    base = cfg["base"]
    scen = cfg["scenarios"]
    if scenario not in scen:
        raise KeyError(f"scenario {scenario!r} not in {sorted(scen)}")
    m = CostModel(
        commission_per_side_usd=float(base["commission_per_side_usd"]),
        exchange_fee_per_side_usd=float(base["exchange_fee_per_side_usd"]),
        spread_ticks=float(base["spread_ticks"]),
        slippage_ticks_per_side=float(base["slippage_ticks_per_side"]),
        stop_extra_slippage_ticks=float(base["stop_extra_slippage_ticks"]),
    )
    return replace(m, cost_multiplier=float(scen[scenario]))
