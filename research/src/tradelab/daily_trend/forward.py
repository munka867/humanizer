"""Forward observation: the 5 days after dataset A's end, from dataset B. Position STATE is computed over the full
combined series (Donchian is path dependent) but returns are computed ONLY for the new days."""
from __future__ import annotations

import numpy as np
import pandas as pd

from tradelab.daily_trend import accounting as A
from tradelab.daily_trend.analysis import symbol_frame


def combine(a: pd.DataFrame, b: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    ov = a.index.intersection(b.index)
    cols = ["open", "high", "low", "close", "volume"]
    pd.testing.assert_frame_equal(a.loc[ov, cols], b.loc[ov, cols], check_exact=True)   # 0 revisions on the overlap
    new = b[b.index > a.index.max()]
    return pd.concat([a, new[a.columns]]).sort_index(), len(new)


def forward_observation(bars_a: dict, bars_b: dict, variants: dict, costs: A.TrendCosts) -> dict:
    out = {}
    for name, vcfg in variants.items():
        per = {}
        for sym in bars_a:
            full, k = combine(bars_a[sym], bars_b[sym])
            f = symbol_frame(full, vcfg).iloc[-k:] if k else None
            r = A.daily_returns(f["o"], f["c"], f["pc"], f["p"], f["pp"], costs)
            ones = np.ones(k, dtype=int)
            rbh = A.daily_returns(f["o"], f["c"], f["pc"], ones, np.concatenate([[1], ones[:-1]]), costs)  # held since before: no buy cost
            per[sym] = pd.DataFrame({"pos": f["p"].to_numpy(), "ret": r, "ret_bh": rbh},
                                    index=f.index.tz_convert("America/New_York").strftime("%Y-%m-%d"))
        out[name] = per
    return out
