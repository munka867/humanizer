"""Daily return accounting (DAILY_SPEC2). p = position held during day t (decided at close t-1, executed at open t);
pp = position during day t-1. Arrays broadcast, so p/pp may be (draws, n) matrices."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml


@dataclass(frozen=True)
class TrendCosts:
    slippage_bps: float = 1.0
    half_spread_usd: float = 0.005
    commission_usd: float = 0.35
    position_usd: float = 10000.0
    mult: float = 1.0

    def frac(self, open_px):
        """Cost of ONE buy or ONE sell as a fraction of notional."""
        return self.mult * (self.slippage_bps * 1e-4 + self.half_spread_usd / np.asarray(open_px, dtype=float)
                            + self.commission_usd / self.position_usd)

    def scaled(self, m: float) -> "TrendCosts":
        return TrendCosts(self.slippage_bps, self.half_spread_usd, self.commission_usd, self.position_usd, m)


def load_costs(path) -> tuple[TrendCosts, dict]:
    cfg = yaml.safe_load(Path(path).read_text())
    return TrendCosts(**cfg["base"]), cfg["scenarios"]


def daily_returns(o, c, pc, p, pp, costs: TrendCosts):
    o, c, pc = (np.asarray(x, dtype=float) for x in (o, c, pc))
    p, pp = np.asarray(p), np.asarray(pp)
    cf = costs.frac(o)
    hold = (pp == 1) & (p == 1)
    buy = (pp == 0) & (p == 1)
    sell = (pp == 1) & (p == 0)
    return np.where(hold, c / pc - 1.0, np.where(buy, c / o - 1.0 - cf, np.where(sell, o / pc - 1.0 - cf, 0.0)))


def total_return(r):
    return np.prod(1.0 + np.asarray(r), axis=-1) - 1.0


def max_drawdown(r):
    """Peak-to-trough of the cumulative-return curve (start equity 1), positive fraction."""
    r = np.asarray(r, dtype=float)
    eq = np.cumprod(1.0 + r, axis=-1)
    eq = np.concatenate([np.ones(eq.shape[:-1] + (1,)), eq], axis=-1)
    peak = np.maximum.accumulate(eq, axis=-1)
    return (1.0 - eq / peak).max(axis=-1)


def ann_vol(r):
    return np.std(np.asarray(r, dtype=float), axis=-1, ddof=1) * np.sqrt(252.0)
