"""Tolerant CSV loader producing exactly the contracts.py bar format.

Timestamp semantics are explicit: `timestamp_label` ('open' default | 'close') and, for naive local
timestamps, an explicit `tz_hint`. Ambiguity raises ValueError; nothing is guessed.
"""
from __future__ import annotations
import re
from pathlib import Path
import numpy as np
import pandas as pd
from tradelab.contracts import BAR_COLUMNS, BAR_INDEX_NAME

_EPOCH = pd.Timestamp(0, tz="UTC")
_NUM_ALIASES = {
    "open": ["open", "o", "openprice"], "high": ["high", "h", "highprice"],
    "low": ["low", "l", "lowprice"], "close": ["close", "c", "closeprice", "last"],
    "volume": ["volume", "vol", "v", "totalvolume"],
}
_TS_ALIASES = ["tsopen", "timestamp", "datetime", "datetimeutc", "dateandtime", "ts", "time", "date",
               "opentime", "timeopen", "closetime", "timeclose", "barstart", "starttime"]
_TZ_SUFFIX = re.compile(r"\s+([A-Za-z]+(?:/[A-Za-z_]+)+|UTC|GMT)$")
_TZ_ALIAS = {"US/Eastern": "America/New_York", "US/Central": "America/Chicago", "US/Pacific": "America/Los_Angeles",
             "GMT": "UTC"}
_OFFSET = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})$")


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _find(cols: dict[str, str], aliases: list[str]) -> str | None:
    return next((cols[a] for a in aliases if a in cols), None)


def _parse_numeric_epoch(v: pd.Series) -> pd.DatetimeIndex:
    mx = float(v.abs().max())
    if v.between(19000101, 21001231).all() and (v % 1 == 0).all():
        raise ValueError("timestamp column looks like YYYYMMDD dates without a time of day; intraday bars need a time")
    unit = "s" if mx < 1e11 else "ms" if mx < 1e14 else "us" if mx < 1e17 else "ns"
    return pd.DatetimeIndex(pd.to_datetime(v.astype("int64"), unit=unit, utc=True))


def _parse_strings(s: pd.Series, tz_hint: str | None) -> pd.DatetimeIndex:
    s = s.astype(str).str.strip()
    suffix = s.str.extract(_TZ_SUFFIX)[0]
    if suffix.notna().any():
        names = set(suffix.dropna())
        if len(names) > 1 or suffix.isna().any():
            raise ValueError(f"inconsistent timezone suffixes in timestamps: {sorted(names)}")
        name = names.pop()
        name = _TZ_ALIAS.get(name, name)
        if tz_hint and tz_hint != name:
            raise ValueError(f"file says timezone {name!r} but tz_hint={tz_hint!r}")
        tz_hint, s = name, s.str.replace(_TZ_SUFFIX, "", regex=True)
    has_off = s.str.contains(_OFFSET)
    if has_off.any() and not has_off.all():
        raise ValueError("some timestamps carry a UTC offset and some do not; refusing to guess")
    if has_off.all():
        return pd.DatetimeIndex(pd.to_datetime(s, utc=True, format="mixed"))
    if not tz_hint:
        raise ValueError("timestamps are naive (no offset); pass tz_hint, e.g. 'America/New_York' or 'UTC'")
    try:
        naive = pd.DatetimeIndex(pd.to_datetime(s))
    except (ValueError, TypeError):
        naive = pd.DatetimeIndex(pd.to_datetime(s, format="mixed"))
    try:
        return naive.tz_localize(tz_hint, ambiguous="raise", nonexistent="raise").tz_convert("UTC")
    except Exception as e:  # AmbiguousTimeError / NonExistentTimeError
        raise ValueError(f"local timestamps ambiguous or non-existent in {tz_hint} (DST): {e}. "
                         "Re-export in UTC or with UTC offsets.") from e


def load_bars(path, tz_hint: str | None = None, timestamp_label: str = "open", bar_freq: str | None = None,
              contract: str | None = None, validate: str = "raise", sort: bool = False) -> pd.DataFrame:
    """Load an OHLCV CSV into the contracts.py bar format.

    timestamp_label: 'open' (default) or 'close'. 'close' REQUIRES bar_freq (e.g. '1min'); ts is shifted back by it.
    tz_hint: IANA zone for naive timestamps (ignored for epoch or offset-bearing timestamps).
    validate: 'raise' -> duplicates / non-increasing index raise; 'keep' -> return rows as parsed (for diagnostics).
    sort: sort ascending first (for newest-first exports) before validating.
    """
    if timestamp_label not in ("open", "close"):
        raise ValueError("timestamp_label must be 'open' or 'close'")
    if validate not in ("raise", "keep"):
        raise ValueError("validate must be 'raise' or 'keep'")
    raw = pd.read_csv(path, sep=None, engine="python", dtype=str, skipinitialspace=True)
    cols = {_norm(c): c for c in raw.columns}
    # --- timestamp
    tcol = _find(cols, _TS_ALIASES)
    if tcol is None:
        raise ValueError(f"no timestamp column found in {list(raw.columns)}")
    dcol, tmcol = cols.get("date"), cols.get("time")
    if tcol in (dcol, tmcol) and dcol and tmcol and not (set(cols) & {"tsopen", "timestamp", "datetime"}):
        raw["_ts"] = raw[dcol].str.strip() + " " + raw[tmcol].str.strip()
        tcol = "_ts"
    if "close" in _norm(tcol) and "timestamp" not in _norm(tcol) and timestamp_label == "open":
        raise ValueError(f"timestamp column {tcol!r} looks like a bar CLOSE time but timestamp_label='open'; "
                         "pass timestamp_label='close' and bar_freq")
    tsv = raw[tcol]
    num = pd.to_numeric(tsv, errors="coerce")
    if num.notna().all():
        ts = _parse_numeric_epoch(num)
    elif num.notna().any():
        raise ValueError("timestamp column mixes numeric and text values")
    else:
        ts = _parse_strings(tsv, tz_hint)
    # --- values
    out = {}
    for k, aliases in _NUM_ALIASES.items():
        c = _find(cols, aliases)
        if c is None:
            raise ValueError(f"missing column for {k!r}; columns are {list(raw.columns)}")
        v = pd.to_numeric(raw[c].str.replace(",", ""), errors="coerce")
        if v.isna().any() and raw[c].notna().sum() != v.notna().sum():
            raise ValueError(f"non-numeric values in column {c!r}")
        out[k] = v.astype("float64").to_numpy()
    df = pd.DataFrame(out, index=ts)
    ccol = _find(cols, ["contract", "localsymbol"])
    if ccol is None and "symbol" in cols:
        # R-06: a root symbol ("MES") is not a contract label; accept 'symbol' only if EVERY value carries a
        # month/year code (e.g. MESZ4, ESH25), otherwise roll detection would be silently disabled.
        sym = raw[cols["symbol"]].astype(str).str.strip()
        if sym.str.fullmatch(r"[A-Z0-9]{1,4}[FGHJKMNQUVXZ]\d{1,2}").all():
            ccol = cols["symbol"]
    if ccol is not None:
        df["contract"] = raw[ccol].to_numpy()
    elif contract:
        df["contract"] = contract
    if timestamp_label == "close":
        if not bar_freq:
            raise ValueError("timestamp_label='close' requires bar_freq (e.g. '1min')")
        df.index = df.index - pd.Timedelta(bar_freq)
    df.index.name = BAR_INDEX_NAME
    if df.index.isna().any():
        raise ValueError("unparseable timestamps")
    if sort:
        df = df.sort_index(kind="stable")
    if validate == "raise":
        if df.index.duplicated().any():
            raise ValueError(f"{int(df.index.duplicated().sum())} duplicate timestamps; use validate='keep' to inspect")
        if not df.index.is_monotonic_increasing:
            raise ValueError("timestamps not ascending; pass sort=True if the file is newest-first")
    df.attrs.update(source=str(path), timestamp_label=timestamp_label)
    if "synthetic" in cols or "SYNTHETIC" in Path(str(path)).name.upper():
        df.attrs["SYNTHETIC"] = True
    return df[BAR_COLUMNS + (["contract"] if "contract" in df else [])]


def _infer_step(idx: pd.DatetimeIndex) -> pd.Timedelta:
    d = pd.Series(idx[1:] - idx[:-1])
    if d.empty:
        raise ValueError("cannot infer bar size from fewer than 2 bars; pass src_freq")
    return d.mode().iloc[0]


def resample_bars(df: pd.DataFrame, freq: str, src_freq: str | None = None) -> pd.DataFrame:
    """Aggregate bars to `freq`, labelled by bar OPEN, bins [T, T+freq) aligned to UTC epoch.

    A target bar is emitted only if ALL its source bars are present (complete, no NaNs, one contract),
    so the output bar at T uses only data up to T+freq and is never partially filled. Dropped
    bin counts are stored in out.attrs['resample_dropped'].
    """
    f = pd.Timedelta(freq)
    src = pd.Timedelta(src_freq) if src_freq else _infer_step(df.index)
    if f % src or pd.Timedelta("1D") % f:
        raise ValueError(f"{freq} must be a multiple of the source step {src} and divide one day")
    if not df.index.is_unique or not df.index.is_monotonic_increasing:
        raise ValueError("resample requires a unique ascending index")
    off = df.index - _EPOCH
    if ((off % src) != pd.Timedelta(0)).any():
        raise ValueError(f"timestamps are not aligned to the {src} grid")
    n = int(f // src)
    key = _EPOCH + (off // f) * f
    g = df.assign(_ok=df[BAR_COLUMNS].notna().all(axis=1)).groupby(key, sort=True)
    agg = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                volume=("volume", "sum"), n=("_ok", "sum"))
    ncon = g["contract"].nunique() if "contract" in df else None
    keep = agg["n"] == n
    dropped = {"incomplete": int((~keep).sum()), "multi_contract": 0}
    if ncon is not None:
        multi = keep & (ncon > 1)
        dropped["multi_contract"] = int(multi.sum())
        keep &= ~multi
        agg["contract"] = g["contract"].last()
    out = agg.loc[keep].drop(columns="n")
    out.index = pd.DatetimeIndex(out.index, name=BAR_INDEX_NAME)
    out[BAR_COLUMNS] = out[BAR_COLUMNS].astype("float64")
    out.attrs.update(df.attrs, resample_dropped=dropped)
    return out
