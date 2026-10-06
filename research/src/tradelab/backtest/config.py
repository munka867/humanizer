"""Config loading and strategy construction from YAML."""
from __future__ import annotations
from pathlib import Path
from typing import Any
import yaml
from tradelab.contracts import Instrument
from tradelab.backtest.baselines import (OpeningRangeBreakout, OrbParams, RandomEntry, RandomParams)
from tradelab.backtest.costs import load_cost_model
from tradelab.backtest.engine import EngineConfig
from tradelab.backtest.sweep_reversal import SweepParams, SweepReversal

INSTRUMENTS = {
    "MES": Instrument("MES", 5.0, 0.25), "ES": Instrument("ES", 50.0, 0.25),
    "MNQ": Instrument("MNQ", 2.0, 0.25), "NQ": Instrument("NQ", 20.0, 0.25),
}
STRATEGIES = {
    "sweep_reversal": (SweepReversal, SweepParams),
    "random_entry": (RandomEntry, RandomParams),
    "orb": (OpeningRangeBreakout, OrbParams),
}


def load_yaml(path: str | Path) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text())


def variant_id(cfg: dict, overrides: dict | None) -> str:
    vid = cfg["id"]
    if overrides:
        vid += "@" + ",".join(f"{k}={overrides[k]}" for k in sorted(overrides))
    return vid


def build(cfg: dict, cost_path: str | Path, scenario: str = "x1",
          overrides: dict | None = None, **extra_params):
    """Return (strategy, EngineConfig, params_dict). `overrides` must be keys of the grid (logged variants)."""
    inst = INSTRUMENTS[cfg.get("instrument", "MES")]
    params = dict(cfg.get("params", {}))
    if overrides:
        bad = [k for k in overrides if k not in cfg.get("grid", {})]
        if bad:
            raise ValueError(f"overrides {bad} are not grid parameters; changing them is a new "
                             "variant that must be registered in docs/EXPERIMENT_LOG.md and the config")
        params.update(overrides)
    params.update(extra_params)
    if "stop_distances_pts" in params and params["stop_distances_pts"] is not None:
        params["stop_distances_pts"] = tuple(params["stop_distances_pts"])
    cls, pcls = STRATEGIES[cfg["strategy"]]
    strat = cls(inst, pcls(**params), strategy_id=variant_id(cfg, overrides))
    e = cfg.get("engine", {})
    ecfg = EngineConfig(instrument=inst, cost=load_cost_model(cost_path, scenario), **e)
    return strat, ecfg, params
