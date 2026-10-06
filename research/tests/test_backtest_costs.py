import pytest
from pathlib import Path
from dataclasses import replace
from tradelab.backtest import costs as C
from tradelab.contracts import CostModel, Instrument
from test_backtest_helpers import COST, MES

ROOT = Path(__file__).resolve().parents[1]


def test_fill_components_to_the_cent():
    # tick = 0.25*5 = $1.25. market: (0.5 spread + 0.5 slip) ticks = $1.25; stop adds 1 tick -> $2.50
    assert C.fill_slippage_usd(COST, MES, 1, C.ENTRY) == pytest.approx(1.25, abs=1e-9)
    assert C.fill_slippage_usd(COST, MES, 1, C.MARKET_EXIT) == pytest.approx(1.25, abs=1e-9)
    assert C.fill_slippage_usd(COST, MES, 1, C.STOP_EXIT) == pytest.approx(2.50, abs=1e-9)
    assert C.fill_slippage_usd(COST, MES, 1, C.LIMIT_EXIT) == 0.0
    assert C.commission_usd(COST, 1) == pytest.approx(0.35, abs=1e-9)


def test_round_trip_totals_and_qty_scaling():
    assert C.trade_costs_usd(COST, MES, 1, C.LIMIT_EXIT) == pytest.approx(0.70 + 1.25, abs=1e-9)
    assert C.trade_costs_usd(COST, MES, 1, C.STOP_EXIT) == pytest.approx(0.70 + 1.25 + 2.50, abs=1e-9)
    assert C.trade_costs_usd(COST, MES, 1, C.MARKET_EXIT) == pytest.approx(0.70 + 2.50, abs=1e-9)
    assert C.trade_costs_usd(COST, MES, 3, C.STOP_EXIT) == pytest.approx(3 * 4.45, abs=1e-9)


def test_multiplier_scales_everything():
    m2 = replace(COST, cost_multiplier=2.0)
    assert C.trade_costs_usd(m2, MES, 1, C.STOP_EXIT) == pytest.approx(2 * 4.45, abs=1e-9)
    assert C.trade_costs_usd(m2, MES, 1, C.LIMIT_EXIT) == pytest.approx(2 * 1.95, abs=1e-9)


def test_other_instrument_tick_value():
    nq = Instrument("NQ", 20.0, 0.25)  # tick = $5
    assert C.fill_slippage_usd(COST, nq, 1, C.ENTRY) == pytest.approx(5.0, abs=1e-9)


def test_unknown_kind_rejected():
    with pytest.raises(ValueError):
        C.fill_slippage_usd(COST, MES, 1, "bogus")


def test_yaml_scenarios():
    p = ROOT / "configs" / "costs_mes.yaml"
    x1, x15, x2 = (C.load_cost_model(p, s) for s in ("x1", "x1_5", "x2"))
    assert (x1.cost_multiplier, x15.cost_multiplier, x2.cost_multiplier) == (1.0, 1.5, 2.0)
    # base 0.85+0.35 per side, (0.5+0.25) ticks of $1.25 per market fill: limit exit round trip
    assert C.trade_costs_usd(x1, MES, 1, C.LIMIT_EXIT) == pytest.approx(2 * 1.2 + 0.75 * 1.25, abs=1e-9)
    assert C.trade_costs_usd(x15, MES, 1, C.LIMIT_EXIT) == pytest.approx(1.5 * (2 * 1.2 + 0.75 * 1.25), abs=1e-9)
    with pytest.raises(KeyError):
        C.load_cost_model(p, "x9")
