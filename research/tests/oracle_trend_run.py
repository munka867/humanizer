"""Driver for tests/oracle_trend.py: per-segment, per-ETF and pooled metrics for V1..V3 (train/validation only)."""
from __future__ import annotations
import json, sys
sys.path.insert(0, "tests"); sys.path.insert(0, "src")
import oracle_trend as O

SYMS = ("SPY", "QQQ", "IWM")


def run(data_dir="data/processed", purge_validation=True, mult=1.0, common_only=False):
    sp, cutoff = O.segment_bounds(data_dir)
    b1, b2 = sp.validation[0].to_pydatetime(), sp.validation[1].to_pydatetime()
    first = sp.train[0].to_pydatetime()
    import datetime as dt
    segs = {"train": (first, b1), "validation": (b1 + (dt.timedelta(days=3) if purge_validation else dt.timedelta(0)), b2)}
    data = {s: O.load(f"{data_dir}/{s}_1d.csv", cutoff) for s in SYMS}
    out = {}
    for v, f in O.SIGNALS.items():
        sig = {s: f(data[s][2]) for s in SYMS}
        pos = {s: O.positions(sig[s]) for s in SYMS}
        for segname, (lo, hi) in segs.items():
            per = {}
            by_date = {}
            for s in SYMS:
                ts, o, c = data[s]
                t0, rows = O.strategy_series(o, c, sig[s], mult)
                sel = [r for r in rows if lo <= ts[r[0]] < hi]
                per[s] = sel
                by_date[s] = {ts[r[0]]: r for r in sel}
            common = sorted(set.intersection(*[set(by_date[s]) for s in SYMS]))
            res = {}
            for s in SYMS:
                sel = per[s] if not common_only else [by_date[s][d] for d in sorted(set.intersection(*[set(by_date[x]) for x in SYMS]))]
                m = O.metrics([r[3] for r in sel], [(r[1], r[2]) for r in sel])
                ts, o, c = data[s]
                idx = [r[0] for r in sel]
                bh = O.bh_series(o, c, idx[0], idx, mult)
                mb = O.metrics([r[3] for r in bh], [(r[1], r[2]) for r in bh])
                m["bh_total_return"], m["bh_max_dd"] = mb["total_return"], mb["max_dd"]
                m["dd_reduction"] = 1 - m["max_dd"] / mb["max_dd"]
                res[s] = m
            # pooled equal weight on common dates
            prets, bhrets, pp = [], [], []
            idxs = {s: [by_date[s][d][0] for d in common] for s in SYMS}
            bhrows = {s: O.bh_series(data[s][1], data[s][2], idxs[s][0], idxs[s], mult) for s in SYMS}
            for k, d in enumerate(common):
                prets.append(sum(by_date[s][d][3] for s in SYMS) / 3.0)
                bhrets.append(sum(bhrows[s][k][3] for s in SYMS) / 3.0)
            # pooled in-market = mean p; switches = sum over ETFs
            m = O.metrics(prets, [(0, 0)] * len(prets))
            m["time_in_market"] = sum(by_date[s][d][2] for s in SYMS for d in common) / (3.0 * len(common))
            m["switches"] = sum(1 for s in SYMS for d in common if by_date[s][d][1] != by_date[s][d][2])
            m["round_trips"] = sum(1 for s in SYMS for d in common if by_date[s][d][1] == 0 and by_date[s][d][2] == 1)
            mb = O.metrics(bhrets, [(0, 0)] * len(bhrets))
            m["bh_total_return"], m["bh_max_dd"] = mb["total_return"], mb["max_dd"]
            m["dd_reduction"] = 1 - m["max_dd"] / mb["max_dd"]
            res["POOLED"] = m
            out[f"{v}/{segname}"] = res
    return out


if __name__ == "__main__":
    purge = (sys.argv[1] != "nopurge") if len(sys.argv) > 1 else True
    r = run(purge_validation=purge)
    json.dump(r, open(sys.argv[2] if len(sys.argv) > 2 else "/dev/stdout", "w"), indent=1)
