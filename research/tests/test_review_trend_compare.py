"""Phase-5 reviewer: the worker's library (tradelab.daily_trend) against the independent loop oracle on TRAIN and VALIDATION only
(dataset A truncated strictly before the sealed start; nothing at/after 2025-12-20 is ever evaluated). Tolerance 1e-9."""
import datetime as dt
import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests")); sys.path.insert(0, str(ROOT / "src"))
import oracle_trend as O                                   # noqa: E402
import oracle_trend_run as R                               # noqa: E402

pytest.importorskip("tradelab.daily_trend.analysis")
from tradelab.daily_trend import accounting as A, analysis as AN      # noqa: E402

DATA = ROOT / "data" / "processed"
pytestmark = pytest.mark.skipif(not (DATA / "SPY_1d.csv").exists(), reason="dataset A not present")
KINDS = {"V1": {"kind": "sma200"}, "V2": {"kind": "cross", "fast": 50, "slow": 200}, "V3": {"kind": "donchian", "entry": 100, "exit": 50}}


@pytest.fixture(scope="module")
def world():
    sp, cutoff = O.segment_bounds(str(DATA))
    bars = {}
    for s in R.SYMS:
        df = pd.read_csv(DATA / f"{s}_1d.csv")
        df["ts_open"] = pd.to_datetime(df["ts_open"], utc=True)
        df = df.set_index("ts_open")
        bars[s] = AN.truncate_before(df, pd.Timestamp(cutoff))
    return sp, bars


def worker_numbers(world, v, seg, purge):
    sp, bars = world
    start, end = (sp.train[0], sp.train[1]) if seg == "train" else (sp.validation[0] + (sp.purge if purge else pd.Timedelta(0)), sp.validation[1])
    frames = {s: AN.symbol_frame(bars[s], KINDS[v]) for s in R.SYMS}
    dates = AN.common_dates(frames, start, end)
    win = AN.window(frames, dates, A.TrendCosts())
    out = {}
    for s in R.SYMS:
        w = win[s]
        out[s] = AN.segment_metrics(w["r"], w["r_bh"], w["p"], w["pp"][0])
    r, rbh = AN.pooled(win, "r"), AN.pooled(win, "r_bh")
    out["POOLED"] = AN.segment_metrics(r, rbh, np.mean([win[s]["p"] for s in R.SYMS], axis=0), 0)
    return out


@pytest.mark.parametrize("purge", [True, False])
@pytest.mark.parametrize("v", ["V1", "V2", "V3"])
@pytest.mark.parametrize("seg", ["train", "validation"])
def test_worker_matches_oracle(world, v, seg, purge):
    mine = R.run(purge_validation=purge, common_only=True)[f"{v}/{seg}"]
    theirs = worker_numbers(world, v, seg, purge)
    for s in ("SPY", "QQQ", "IWM", "POOLED"):
        m, t = mine[s], theirs[s]
        assert t["n_days"] == m["n"], (s, "n")
        for a, b in (("total_return", "total"), ("bh_total_return", "total_bh"), ("max_dd", "mdd"), ("bh_max_dd", "mdd_bh"),
                     ("dd_reduction", "dd_reduction"), ("vol", "vol"), ("time_in_market", "time_in_market")):
            assert t[b] == pytest.approx(m[a], abs=1e-9), (s, a, m[a], t[b])
        if s != "POOLED":
            assert t["switches"] == m["switches"], (s, "switches")
            assert t["buys"] == m["round_trips"], (s, "buys")
