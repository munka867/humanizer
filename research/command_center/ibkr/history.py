"""Historical-bar downloader: chunked backwards paging, strict pacing, resumable, deduplicated, with provenance.

SAFETY: output goes ONLY to a NEW dated snapshot directory  data/processed_<YYYYMMDD>/  (or data/fake_<YYYYMMDD>/ for the simulated IB).
It NEVER writes to, or overwrites anything in, data/processed/ : that directory is the FROZEN dataset used by pre-registered runs (see
docs/DECISIONS.md). assert_safe_output() enforces this and tests prove it.

Pacing (official, 'Historical Data Limitations': https://interactivebrokers.github.io/tws-api/historical_limitations.html):
  - no identical request within 15 s
  - fewer than 6 requests for the same contract/exchange/tick type within 2 s
  - no more than 60 requests in any 10 minutes (BID_ASK counts double; we only request TRADES)
We stay below these (58 per 10 min, 5 per 2 s). The page states these apply to small bars; we apply them to every bar size to be safe.
"""
from __future__ import annotations

import asyncio
import collections
import datetime as dt
import hashlib
import json
import math
import re
import time
from pathlib import Path
from zoneinfo import ZoneInfo

from . import errors as E

NY = ZoneInfo("America/New_York")
# id -> (ib barSizeSetting, chunk durationStr, bar seconds, chunk span in days, file tag).  Chunk sizes follow the official 'Step Sizes' table
# (1 D: 1 min-1 day; 1 W: 3 mins-1 week; 1 M: 30 mins-1 month; 1 Y: 1 day-1 month). They are conservative, not a claim about IBKR's maxima.
BAR_SIZES = {
    "1d": ("1 day", "1 Y", 86400, 365, "1d"),
    "1h": ("1 hour", "1 M", 3600, 30, "1h"),
    "15m": ("15 mins", "1 W", 900, 7, "15m"),
    "5m": ("5 mins", "1 W", 300, 7, "5m"),
    "1m": ("1 min", "1 D", 60, 1, "1m"),
}
MAX_YEARS = {"1d": 30, "1h": 10, "15m": 10, "5m": 10, "1m": 5}   # app caps (not IBKR limits); the download also stops when IBKR has no older data
SNAP_RE = re.compile(r"^(processed|fake)_\d{8}$")
_DUR_RE = re.compile(r"^(\d{1,3}) ?([DWMY])$", re.I)


class HistoryError(RuntimeError):
    def __init__(self, info: dict):
        super().__init__(info.get("message") or info.get("title") or "history error")
        self.info = info


class Cancelled(RuntimeError):
    pass


def parse_span(years=None, duration=None, bar_size="1d") -> float:
    """-> span in days. Accepts years (float) or an IB-style duration like '30 D', '6 M', '2 Y'."""
    if bar_size not in BAR_SIZES:
        raise ValueError(f"bar_size must be one of {sorted(BAR_SIZES)}")
    if duration:
        m = _DUR_RE.match(str(duration).strip())
        if not m:
            raise ValueError("duration must look like '30 D', '6 M' or '2 Y'")
        n, u = int(m.group(1)), m.group(2).upper()
        days = n * {"D": 1, "W": 7, "M": 31, "Y": 366}[u]
    elif years is not None:
        if isinstance(years, bool) or not isinstance(years, (int, float)) or not (0 < years):
            raise ValueError("years must be a positive number")
        days = float(years) * 366
    else:
        raise ValueError("give years or duration")
    if days > MAX_YEARS[bar_size] * 366:
        raise ValueError(f"at most {MAX_YEARS[bar_size]} years for {bar_size} bars (app cap; IBKR may have less)")
    return days


# ------------------------------------------------------------------------------------------------ pacing
class Pacer:
    def __init__(self, clock=time.time, sleep=asyncio.sleep, per_window=58, window=600.0, per_contract=5, contract_window=2.0, identical_gap=15.0):
        self.clock, self.sleep = clock, sleep
        self.per_window, self.window, self.per_contract, self.contract_window, self.identical_gap = per_window, window, per_contract, contract_window, identical_gap
        self._all = collections.deque()          # (t, weight)
        self._by_contract: dict = collections.defaultdict(collections.deque)
        self._identical: dict = {}
        self.waited = 0.0
        self.requests = 0

    def _wait_needed(self, ckey, rkey, weight) -> float:
        now = self.clock()
        while self._all and now - self._all[0][0] >= self.window:
            self._all.popleft()
        w = 0.0
        last = self._identical.get(rkey)
        if last is not None and now - last < self.identical_gap:
            w = max(w, self.identical_gap - (now - last))
        dq = self._by_contract[ckey]
        while dq and now - dq[0] >= self.contract_window:
            dq.popleft()
        if len(dq) >= self.per_contract:
            w = max(w, dq[0] + self.contract_window - now)
        total = sum(x[1] for x in self._all)
        if total + weight > self.per_window:
            need, acc = total + weight - self.per_window, 0
            for t, wt in self._all:
                acc += wt
                if acc >= need:
                    w = max(w, t + self.window - now)
                    break
        return w

    async def acquire(self, ckey, rkey, weight=1):
        while True:
            w = self._wait_needed(ckey, rkey, weight)
            if w <= 1e-9:
                break
            self.waited += w
            await self.sleep(w + 0.001)
        now = self.clock()
        self._all.append((now, weight))
        self._by_contract[ckey].append(now)
        self._identical[rkey] = now
        self.requests += 1


# ------------------------------------------------------------------------------------------------ paths / safety
def snapshot_dir(data_root: Path, source: str, now: dt.datetime) -> Path:
    prefix = "fake" if source == "fake" else "processed"
    return Path(data_root) / "data" / f"{prefix}_{now.astimezone(dt.timezone.utc):%Y%m%d}"


def assert_safe_output(path: Path, data_root: Path, source: str):
    """Refuse anything that is not a NEW dated snapshot directory directly under data/. data/processed (frozen) is never allowed."""
    p = Path(path).resolve()
    base = (Path(data_root) / "data").resolve()
    if p.parent != base:
        raise ValueError(f"refused: {p} is not directly under {base}")
    if p.name == "processed" or not SNAP_RE.match(p.name):
        raise ValueError(f"refused: output directory must be named processed_YYYYMMDD (or fake_YYYYMMDD), never 'processed' (frozen dataset): {p.name}")
    if (source == "fake") != p.name.startswith("fake_"):
        raise ValueError("refused: simulated data may only go to data/fake_*, real data only to data/processed_*")


def _atomic_write(path: Path, data: str):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8", newline="\n")
    tmp.replace(path)


def fmt_ts(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00")


def _to_ts(t, daily: bool) -> dt.datetime:
    if isinstance(t, dt.datetime):
        return t.astimezone(dt.timezone.utc)
    return dt.datetime.combine(t, dt.time(9, 30), NY).astimezone(dt.timezone.utc)   # daily: session OPEN, like the connector files


def csv_text(rows: dict, symbol: str) -> str:
    lines = ["ts_open,open,high,low,close,volume,contract"]
    for k in sorted(rows):
        r = rows[k]
        lines.append(f"{k},{r['open']},{r['high']},{r['low']},{r['close']},{float(r['volume'])},{symbol}")
    return "\n".join(lines) + "\n"


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    h.update(Path(p).read_bytes())
    return h.hexdigest()


# ------------------------------------------------------------------------------------------------ downloader
class HistoryDownloader:
    def __init__(self, client, data_root, source="ibkr", clock=time.time, sleep=asyncio.sleep, pacer=None, lib_version=None):
        self.client, self.data_root, self.source = client, Path(data_root), source
        self.clock, self.sleep = clock, sleep
        self.pacer = pacer or Pacer(clock, sleep)
        self.lib_version = lib_version

    def _now(self):
        return dt.datetime.fromtimestamp(self.clock(), dt.timezone.utc)

    async def download(self, inst: dict, bar_size: str, span_days: float, use_rth: bool = True, market_data_type: int | None = None,
                       progress=None, cancelled=lambda: False, out_dir: Path | None = None) -> dict:
        ib_bar, dur, bar_s, chunk_days, tag = BAR_SIZES[bar_size]
        daily = bar_size == "1d"
        now = self._now()
        out = Path(out_dir) if out_dir else snapshot_dir(self.data_root, self.source, now)
        assert_safe_output(out, self.data_root, self.source)
        out.mkdir(parents=True, exist_ok=True)
        sym = inst["symbol"]
        stem = f"{sym}_{tag}" + ("" if use_rth else "_eth")
        pcsv, pjson = out / f"{stem}.partial.csv", out / f"{stem}.partial.json"
        target_start = now - dt.timedelta(days=span_days)
        total_est = max(1, math.ceil(span_days / chunk_days))
        rows: dict[str, dict] = {}
        end = now
        chunks = requests = 0
        resumed = False
        # ---- resume from this snapshot's partial files (same symbol, bar size, session, same target span start within a day)
        if pjson.exists() and pcsv.exists():
            try:
                st = json.loads(pjson.read_text())
                if st.get("conid") == inst["conid"] and st.get("bar_size") == bar_size and st.get("use_rth") == use_rth:
                    for line in pcsv.read_text().splitlines()[1:]:
                        k, o, h, l, c, v, _ = line.split(",")
                        rows[k] = {"open": float(o), "high": float(h), "low": float(l), "close": float(c), "volume": float(v)}
                    end = dt.datetime.fromisoformat(st["next_end"])
                    chunks, resumed = st.get("chunks", 0), True
            except Exception:
                rows, end, chunks, resumed = {}, now, 0, False
        reason = "reached requested start"
        pace_retries = 0
        while True:
            if cancelled():
                raise Cancelled("cancelled by user; partial data kept for resume")
            ckey = (inst["conid"], inst.get("exchange"), "TRADES")
            rkey = (inst["conid"], end.isoformat(), dur, ib_bar, use_rth)
            await self.pacer.acquire(ckey, rkey, 1)
            requests += 1
            bars, errs = await self.client.historical_bars(inst, end, dur, ib_bar, use_rth, "TRADES")
            fatal = [(c, t) for c, t in errs if c in (200, 354, 10186, 504, 502, 507, 326, 503) ]
            if fatal and not bars:
                raise HistoryError(E.explain(*fatal[0]))
            pacing = [t for c, t in errs if c == 162 and "pacing" in t.lower()]
            if pacing and not bars:
                pace_retries += 1
                if pace_retries > 3:
                    raise HistoryError({**E.explain(162, pacing[0]), "message": "IBKR reported a pacing violation repeatedly. Try again in a few minutes."})
                await self.sleep(60.0 * pace_retries)   # back off; the identical-request rule needs >=15 s anyway
                continue
            pace_retries = 0
            if not bars:
                reason = "IBKR has no older data" if rows else "IBKR returned no data for this instrument/period"
                break
            before = len(rows)
            earliest = None
            for b in bars:
                ts = _to_ts(b["time"], daily)
                earliest = ts if earliest is None or ts < earliest else earliest
                rows[fmt_ts(ts)] = {k: b[k] for k in ("open", "high", "low", "close", "volume")}
            new = len(rows) - before
            chunks += 1
            end = earliest
            _atomic_write(pcsv, csv_text(rows, sym))
            _atomic_write(pjson, json.dumps({"conid": inst["conid"], "bar_size": bar_size, "use_rth": use_rth, "next_end": end.isoformat(), "chunks": chunks}))
            if progress:
                progress({"chunks_done": chunks, "chunks_total_estimate": max(total_est, chunks), "rows": len(rows), "oldest": fmt_ts(earliest), "requests": requests, "waited_s": round(self.pacer.waited, 1)})
            if earliest <= target_start:
                break
            if new == 0:
                reason = "no new rows in the last chunk (stopped to avoid looping)"
                break
        # ---- finalize: window, drop the still-forming bar, write, manifest
        lo, dropped = fmt_ts(target_start), 0
        final = {}
        for k, r in rows.items():
            if k < lo:
                continue
            t0 = dt.datetime.fromisoformat(k)
            bar_end = (t0.astimezone(NY).replace(hour=16, minute=0, second=0) if daily else t0 + dt.timedelta(seconds=bar_s))
            if bar_end > now:
                dropped += 1
                continue
            final[k] = r
        if not final:
            for p in (pcsv, pjson):
                p.unlink(missing_ok=True)
            raise HistoryError({"code": None, "severity": "error", "title": "No bars downloaded", "message": f"{sym}: {reason}.", "fix": "Check market-data permissions and the symbol/exchange; see docs/IBKR_SETUP.md.", "basis": "local"})
        name, n = f"{stem}.csv", 1
        while (out / name).exists():            # never overwrite an earlier file
            n += 1
            name = f"{stem}_r{n}.csv"
        path = out / name
        _atomic_write(path, csv_text(final, sym))
        ks = sorted(final)
        entry = {"symbol": sym, "conId": inst["conid"], "exchange": inst.get("exchange"), "currency": inst.get("currency", "USD"), "bar_size": bar_size, "ib_bar_size": ib_bar,
                 "what_to_show": "TRADES", "use_rth": use_rth, "session": "regular" if use_rth else "regular+extended", "market_data_type": market_data_type,
                 "delayed": market_data_type in (3, 4) if market_data_type else None, "retrieved_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "rows": len(final),
                 "first": ks[0], "last": ks[-1], "file": name, "sha256": sha256_file(path), "source": self.source, "library": self.lib_version, "requests": requests, "chunks": chunks,
                 "resumed": resumed, "dropped_incomplete_bars": dropped, "stop_reason": reason, "timestamps": "ts_open = bar OPEN time, UTC" + ("; daily bars use 09:30 America/New_York of the session date" if daily else ""),
                 "adjusted_for_dividends": False, "note": "SIMULATED fake IB data, not market data" if self.source == "fake" else "IBKR TWS/Gateway historical data (not yet verified against a real account)"}
        self._merge_manifest(out, entry)
        for p in (pcsv, pjson):
            p.unlink(missing_ok=True)
        return {**entry, "path": str(path.relative_to(self.data_root)) if path.is_relative_to(self.data_root) else str(path), "dir": str(out)}

    def _merge_manifest(self, out: Path, entry: dict):
        mp = out / "MANIFEST.json"
        cur = []
        if mp.exists():
            try:
                cur = json.loads(mp.read_text())
            except Exception:
                cur = []
        cur.append(entry)
        _atomic_write(mp, json.dumps(cur, indent=1))
