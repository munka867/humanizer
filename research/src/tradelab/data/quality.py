"""Data-quality checks for bar data. quality_report() -> dict; render_markdown() -> str.

Nothing here "fixes" data. Holidays/early closes are only FLAGGED as candidates, never assumed.
Contract rolls: use roll_flags() / drop_roll_sessions() so backtests never trade across a roll
or count the roll gap as P&L.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from tradelab.contracts import Instrument
from tradelab.data import calendar as cal

OHLC = ["open", "high", "low", "close"]
ROLL_MONTHS = (3, 6, 9, 12)


def _iso(t) -> str:
    return pd.Timestamp(t).isoformat()


def _gaps(b: pd.DataFrame, freq: pd.Timedelta) -> list[dict]:
    ts = b.index
    out = []
    for i in np.nonzero((ts[1:] - ts[:-1]) > freq)[0]:
        slots = pd.date_range(ts[i] + freq, ts[i + 1], freq=freq, inclusive="left")
        slots = slots[cal.globex_open_mask(slots)]
        if len(slots):
            dates = set(cal.et_date(slots).date)
            out.append({"start": _iso(slots[0]), "end": _iso(slots[-1] + freq), "missing_bars": len(slots),
                        "holiday_candidate": any(cal.is_holiday_candidate(d) for d in dates)})
    return out


def _jumps(b: pd.DataFrame, freq: pd.Timedelta) -> pd.DataFrame:
    """Open-vs-previous-close log jump for every pair of bars that are NOT consecutive (session breaks/gaps)."""
    brk = (b.index[1:] - b.index[:-1]) > freq
    j = np.log(b["open"].to_numpy()[1:] / b["close"].to_numpy()[:-1])
    d = pd.DataFrame({"jump": j[brk], "ts": b.index[1:][brk]})
    if "contract" in b:
        d["new_contract"] = b["contract"].to_numpy()[1:][brk]
        d["old_contract"] = b["contract"].to_numpy()[:-1][brk]
    return d


def _near_expiry(ts: pd.Timestamp, before: int = 12, after: int = 2) -> bool:
    d = cal.session_date(pd.DatetimeIndex([ts]))[0]
    for m in ROLL_MONTHS:
        e = pd.Timestamp(cal.third_friday(d.year, m))
        if e - pd.Timedelta(days=before) <= d <= e + pd.Timedelta(days=after):
            return True
    return False


def roll_flags(bars: pd.DataFrame, expected_freq: str, min_jump: float = 0.003) -> pd.Series:
    """Bool Series (aligned to bars.index): True on the first bar AFTER a suspected contract roll,
    i.e. a bar whose open is not comparable with the previous close.

    With a 'contract' column: True where the contract label changes. Without: True where a gap between bars
    has |log jump| > max(min_jump, 6 robust sigma of all gap jumps) within ~12d before / 2d after a quarterly
    (H/M/U/Z) third-Friday expiry. The heuristic can miss rolls or flag real news gaps; it only SUSPECTS.
    """
    freq = pd.Timedelta(expected_freq)
    flags = pd.Series(False, index=bars.index)
    if len(bars) < 2:
        return flags
    if "contract" in bars and bars["contract"].notna().any():
        c = bars["contract"].to_numpy()
        flags.iloc[1:] = (c[1:] != c[:-1])
        return flags
    d = _jumps(bars, freq)
    if d.empty:
        return flags
    sig = 1.4826 * float(np.median(np.abs(d["jump"] - np.median(d["jump"]))))
    thr = max(min_jump, 6 * sig)
    hit = [t for j, t in zip(d["jump"], d["ts"]) if abs(j) > thr and _near_expiry(t)]
    flags.loc[hit] = True
    return flags


def drop_roll_sessions(bars: pd.DataFrame, flags: pd.Series) -> pd.DataFrame:
    """Remove every bar of each CME trading session (18:00 ET -> 17:00 ET) that contains a roll flag, and the
    previous session too (so no entry can be held across the roll boundary or use pre-roll levels)."""
    sd = cal.session_date(bars.index)
    bad = set(sd[flags.to_numpy()])
    bad |= {d - pd.Timedelta(days=1) for d in list(bad)}
    return bars.loc[~np.isin(sd, list(bad))]


def quality_report(bars: pd.DataFrame, expected_freq: str, instrument: Instrument, *, spike_k: float = 10.0,
                   stale_run: int = 30, min_roll_jump: float = 0.003) -> dict:
    if not isinstance(bars.index, pd.DatetimeIndex) or bars.index.tz is None or str(bars.index.tz) != "UTC":
        raise ValueError("bars must have a tz-aware UTC DatetimeIndex")
    freq = pd.Timedelta(expected_freq)
    idx = bars.index
    issues: list[dict] = []

    def add(sev, code, msg):
        issues.append({"severity": sev, "code": code, "message": msg})

    rep: dict = {"instrument": instrument.symbol, "expected_freq": expected_freq, "n_bars": len(bars),
                 "synthetic": bool(bars.attrs.get("SYNTHETIC", False)), "issues": issues}
    if len(bars) == 0:
        add("error", "empty", "no bars")
        return rep
    rep["first_ts"], rep["last_ts"] = _iso(idx.min()), _iso(idx.max())
    n_dup = int(idx.duplicated().sum())
    n_back = int((idx[1:] < idx[:-1]).sum())
    rep["duplicates"], rep["non_monotonic_steps"] = n_dup, n_back
    if n_dup:
        add("error", "duplicates", f"{n_dup} duplicate timestamps")
    if n_back:
        add("error", "non_monotonic", f"{n_back} steps go backwards in time")
    b = bars[~idx.duplicated()].sort_index(kind="stable")
    ts = b.index
    # step sanity
    step = pd.Series(ts[1:] - ts[:-1]).mode()
    rep["modal_step"] = str(step.iloc[0]) if len(step) else None
    if len(step) and step.iloc[0] != freq:
        add("error", "freq_mismatch", f"modal step {step.iloc[0]} != expected {freq}")
    # OHLC
    o, h, l, c = (b[k] for k in OHLC)
    bad = (h < l) | (h < o) | (h < c) | (l > o) | (l > c)
    nan = b[OHLC + ["volume"]].isna().any(axis=1)
    nonpos = (b[OHLC] <= 0).any(axis=1)
    x = b[OHLC] / instrument.tick_size
    off_tick = ((x - x.round()).abs() > 1e-6).any(axis=1)
    zero_vol, neg_vol = int((b["volume"] == 0).sum()), int((b["volume"] < 0).sum())
    rep["ohlc"] = {"inconsistent": int(bad.sum()), "nan_rows": int(nan.sum()), "nonpositive_price": int(nonpos.sum()),
                   "off_tick": int(off_tick.sum()), "zero_volume": zero_vol, "negative_volume": neg_vol,
                   "examples": [_iso(t) for t in ts[(bad | nan | nonpos).to_numpy()][:5]]}
    for n, code, msg, sev in [(bad.sum(), "bad_ohlc", "bars violate high>=max(o,c,l) / low<=min(o,c,h)", "error"),
                              (nan.sum(), "nan", "bars with NaN OHLCV", "error"),
                              (nonpos.sum(), "nonpositive_price", "bars with price <= 0", "error"),
                              (neg_vol, "negative_volume", "bars with negative volume", "error"),
                              (off_tick.sum(), "off_tick", f"bars with prices not on the {instrument.tick_size} tick grid "
                               "(back-adjusted series are expected to show this)", "warn"),
                              (zero_vol, "zero_volume", "zero-volume bars", "warn")]:
        if n:
            add(sev, code, f"{int(n)} {msg}")
    # calendar: bars in nominal closed hours, gaps
    closed = ~cal.globex_open_mask(ts)
    rep["bars_in_closed_hours"] = int(closed.sum())
    if closed.any():
        add("warn", "closed_hours_bars", f"{int(closed.sum())} bars inside the nominal Globex closed windows "
            "(wrong timezone / close-vs-open labelling, or special sessions); first: " + _iso(ts[closed][0]))
    gaps = _gaps(b, freq)
    rep["gaps"] = {"n": len(gaps), "missing_bars": sum(g["missing_bars"] for g in gaps),
                   "n_holiday_candidate": sum(g["holiday_candidate"] for g in gaps),
                   "largest": sorted(gaps, key=lambda g: -g["missing_bars"])[:10]}
    if gaps:
        add("warn", "gaps", f"{len(gaps)} gaps in nominal open hours, {rep['gaps']['missing_bars']} bars missing "
            f"({rep['gaps']['n_holiday_candidate']} on holiday-candidate dates: verify, not assumed)")
    # DST / week open
    w = ts.tz_convert(cal.NY).tz_localize(None)
    sun = pd.Series(w[w.dayofweek == 6])
    first_sun = sun.groupby(sun.dt.normalize()).min()
    bad_open = first_sun[(first_sun.dt.hour * 60 + first_sun.dt.minute) > 18 * 60]
    trans = cal.dst_transition_dates(ts.min(), ts.max())
    rep["dst"] = {"transition_dates": [str(d) for d in trans],
                  "late_week_opens": [str(t) for t in bad_open.tolist()][:10],
                  "bars_on_transition_dates": {str(d): int((w.date == d).sum()) for d in trans}}
    if len(bad_open):
        add("warn", "late_week_open", f"{len(bad_open)} Sundays whose first bar is after 18:00 ET "
            "(holiday, missing data, or DST/timezone error)")
    # RTH coverage (first/last ET dates excluded: file edges)
    rth = ts[cal.rth_mask(ts)]
    per_day = pd.Series(1, index=cal.et_date(rth)).groupby(level=0).sum()
    expected = int(pd.Timedelta("6h30m") // freq)
    d0, d1 = cal.et_date(ts[:1])[0], cal.et_date(ts[-1:])[0]
    wk = pd.bdate_range(d0 + pd.Timedelta(days=1), d1 - pd.Timedelta(days=1))
    per_day = per_day.reindex(wk, fill_value=0)
    partial = per_day[(per_day > 0) & (per_day < expected)]
    missing = per_day[per_day == 0]
    rep["rth"] = {"expected_bars_per_day": expected, "days": len(per_day), "full_days": int((per_day >= expected).sum()),
                  "partial_days": [{"date": str(d.date()), "bars": int(n), "holiday_candidate": cal.is_holiday_candidate(d.date())}
                                   for d, n in partial.items()][:20],
                  "n_partial_days": len(partial),
                  "missing_weekdays": [{"date": str(d.date()), "holiday_candidate": cal.is_holiday_candidate(d.date())}
                                       for d in missing.index][:20], "n_missing_weekdays": len(missing)}
    if len(partial) or len(missing):
        add("warn", "rth_coverage", f"{len(partial)} partial and {len(missing)} empty RTH weekdays "
            "(early closes/holidays possible: verify against the CME calendar)")
    # spikes (consecutive bars only)
    cons = (ts[1:] - ts[:-1]) == freq
    with np.errstate(all="ignore"):
        lr = np.log(c.to_numpy()[1:] / c.to_numpy()[:-1])
    r = pd.Series(lr, index=ts[1:])[cons & np.isfinite(lr)]
    sig = 1.4826 * float(np.median(np.abs(r - r.median()))) if len(r) else 0.0
    sig = sig or float(r.std() or 0.0)
    spikes = r[np.abs(r) > spike_k * sig] if sig > 0 else r.iloc[:0]
    rep["spikes"] = {"n": len(spikes), "robust_sigma": sig,
                     "largest": [{"ts": _iso(t), "log_ret": float(v)} for t, v in spikes.abs().nlargest(5).items()]}
    if len(spikes):
        add("warn", "spikes", f"{len(spikes)} one-bar returns beyond {spike_k} robust sigma (review: bad ticks or real events)")
    # stale
    flat = ((o == h) & (h == l) & (l == c) & (c == c.shift())).to_numpy()
    runs, cur = [], 0
    for v in flat:
        cur = cur + 1 if v else 0
        runs.append(cur)
    runs = np.array(runs)
    ends = [i for i in range(len(runs)) if runs[i] >= stale_run and (i + 1 == len(runs) or runs[i + 1] == 0)]
    rep["stale"] = {"longest_run": int(runs.max()), "n_runs_ge_threshold": len(ends), "threshold": stale_run,
                    "examples": [_iso(ts[i - int(runs[i]) + 1]) for i in ends[:5]]}
    if ends:
        add("warn", "stale", f"{len(ends)} runs of >= {stale_run} identical flat bars (stale feed?)")
    # rolls
    flags = roll_flags(b, expected_freq, min_roll_jump)
    jumps = _jumps(b, freq)
    rolls = []
    for t in b.index[flags.to_numpy()]:
        j = jumps[jumps["ts"] == t]
        rolls.append({"ts": _iso(t), "log_jump": float(j["jump"].iloc[0]) if len(j) else None,
                      "points": float(b.loc[t, "open"] - b["close"].shift().loc[t]),
                      **({"from": str(j["old_contract"].iloc[0]), "to": str(j["new_contract"].iloc[0])} if len(j) and "contract" in b else {})})
    have_contract = "contract" in b and b["contract"].notna().any()
    unexplained = 0
    if have_contract and len(jumps):
        sg = 1.4826 * float(np.median(np.abs(jumps["jump"] - jumps["jump"].median())))
        big = jumps[(jumps["jump"].abs() > max(min_roll_jump, 6 * sg)) & ~flags.reindex(jumps["ts"]).to_numpy()]
        unexplained = len(big)
    rep["rolls"] = {"method": "contract_column" if have_contract else "heuristic_near_quarterly_expiry",
                    "n": len(rolls), "events": rolls[:20], "large_gaps_without_contract_change": unexplained,
                    "flagged_sessions": int(len(set(cal.session_date(b.index[flags.to_numpy()]))))}
    if rolls:
        add("warn", "roll", f"{len(rolls)} contract rolls ({rep['rolls']['method']}); use roll_flags()/drop_roll_sessions(): "
            "never trade across a roll or count the gap as P&L")
    if unexplained:
        add("warn", "unexplained_jump", f"{unexplained} large gaps with no contract change")
    rep["ok"] = not any(i["severity"] == "error" for i in issues)
    return rep


def render_markdown(rep: dict) -> str:
    L = [f"# Data quality: {rep['instrument']} {rep['expected_freq']}"]
    if rep.get("synthetic"):
        L.append("\n**SYNTHETIC DATA. For software tests only; says nothing about any market.**")
    L.append(f"\nBars: {rep['n_bars']}  ({rep.get('first_ts')} to {rep.get('last_ts')})")
    L.append(f"\nResult: **{'no errors' if rep.get('ok') else 'ERRORS'}**, {len(rep['issues'])} issue types\n")
    L += ["| severity | code | detail |", "|---|---|---|"]
    L += [f"| {i['severity']} | {i['code']} | {i['message']} |" for i in rep["issues"]] or ["| - | none | - |"]
    if "gaps" in rep:
        g, r, s, d, ro = rep["gaps"], rep["rth"], rep["spikes"], rep["dst"], rep["rolls"]
        L += ["", "## Detail",
              f"- duplicates {rep['duplicates']}, backwards steps {rep['non_monotonic_steps']}, modal step {rep['modal_step']}",
              f"- OHLC: {rep['ohlc']}",
              f"- bars in closed hours: {rep['bars_in_closed_hours']}",
              f"- gaps: {g['n']} ({g['missing_bars']} bars); largest: {g['largest'][:3]}",
              f"- DST transition dates in range: {d['transition_dates']}; bars on them: {d['bars_on_transition_dates']}; late week opens: {d['late_week_opens']}",
              f"- RTH ({r['expected_bars_per_day']} bars/day expected): {r['full_days']}/{r['days']} full; partial {r['n_partial_days']}; empty weekdays {r['n_missing_weekdays']}",
              f"- spikes: {s['n']} (robust sigma {s['robust_sigma']:.2e}); stale: {rep['stale']}",
              f"- rolls ({ro['method']}): {ro['n']}; events {ro['events'][:5]}; large gaps w/o contract change: {ro['large_gaps_without_contract_change']}"]
    return "\n".join(L) + "\n"
