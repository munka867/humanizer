"""Data coverage and account-export import endpoints (RESEARCH worker). Stdlib only; no broker imports; no order routes.

* GET  /api/data/coverage[?symbols=SPY,QQQ&start=YYYY-MM-DD&end=YYYY-MM-DD&resolution=1D]
* POST /api/import/preview   {filename, content}      -> format detection + validation, nothing stored
* POST /api/import/commit    {filename, content}      -> stores RAW bytes under data/raw/ibkr_exports/ (git-ignored)
* GET  /api/import/registry

Import parsing is built against PUBLIC-DOCUMENTATION RECOLLECTION of IBKR Flex field names. NO real IBKR export sample exists
yet, so every field name below is UNVERIFIED and all test fixtures are SYNTHETIC (docs/IMPORT_SUPPORT_MATRIX.md).
Market data (data/processed, data/raw/ibkr_connector) and account exports (data/raw/ibkr_exports) are never mixed.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import math
import os
import re
import tempfile
import threading
from pathlib import Path
from urllib.parse import unquote
from xml.parsers import expat
from zoneinfo import ZoneInfo

from .jobs import ResearchDB, utc_now
from .router import route

ROOT = Path(__file__).resolve().parents[1]
NY = ZoneInfo("America/New_York")
MAX_IMPORT_BYTES = 5 * 1024 * 1024
MIN_SEGMENT_BARS = 40          # software minimum so the daily pipeline yields non-empty tables; NOT a statistical sufficiency test
MAX_MISSING_PCT = 2.0          # adequacy: more than this share of expected sessions missing inside the window
GAP_CALENDAR_DAYS = 4          # same threshold as DAILY_SPEC max_gap_calendar_days
SYM_RE = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}\Z")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ApiError(Exception):
    def __init__(self, status: int, message: str, **extra):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


# ------------------------------------------------------------------ shared context (one per server instance)
_CTX_LOCK = threading.Lock()


class Ctx:
    def __init__(self, server):
        db_path = Path(getattr(getattr(server, "store", None), "path", None) or (ROOT / "command_center" / "data" / "events.sqlite3"))
        p = {
            "root": ROOT, "data_dir": ROOT / "data" / "processed",
            "manifest": ROOT / "data" / "raw" / "ibkr_connector" / "MANIFEST.json",
            "imports_dir": ROOT / "data" / "raw" / "ibkr_exports", "docs_dir": ROOT / "docs", "configs_dir": ROOT / "configs",
            "results_daily": ROOT / "results" / "daily", "experiments_dir": ROOT / "results" / "experiments",
            "test_log": Path(os.environ.get("CC_TEST_ACCESS_LOG") or ROOT / "results" / "test_access_log.jsonl"), "db": db_path.parent / "research.sqlite3",
            "runner": None, "timeout_s": None,
        }
        p.update({k: (Path(v) if k not in ("runner", "timeout_s") and v is not None else v)
                  for k, v in (getattr(server, "research_paths", None) or {}).items()})
        self.paths = p
        self.rdb = ResearchDB(p["db"])
        self.rdb.script("""CREATE TABLE IF NOT EXISTS import_registry(
            id INTEGER PRIMARY KEY AUTOINCREMENT, sha256 TEXT UNIQUE NOT NULL, filename TEXT NOT NULL, stored_path TEXT NOT NULL,
            size_bytes INTEGER NOT NULL, imported_at TEXT NOT NULL, format TEXT NOT NULL, row_counts TEXT NOT NULL,
            quarantined INTEGER NOT NULL, status TEXT NOT NULL);""")
        self.queue = None
        self.store = getattr(server, "store", None)


def ctx(server) -> Ctx:
    c = server.__dict__.get("_research_ctx")
    if c is None:
        with _CTX_LOCK:
            c = server.__dict__.get("_research_ctx")
            if c is None:
                c = Ctx(server)
                server.__dict__["_research_ctx"] = c
    return c


# ------------------------------------------------------------------ response helpers
def clean(o):
    """JSON-safe copy: NaN/Inf -> None (browsers cannot parse NaN)."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    return o


def handler(fn):
    """Wrap a route fn: JSON errors, NaN-safe output, never leak a traceback or an absolute path."""
    def wrapped(h, m, q, body):
        try:
            out = fn(h, m, q, body)
            code, obj = out if isinstance(out, tuple) else (200, out)
            return h._json(code, clean(obj))
        except ApiError as e:
            return h._json(e.status, clean({"ok": False, "error": e.message, **e.extra}))
        except (ValueError, KeyError, TypeError) as e:
            return h._json(400, {"ok": False, "error": f"invalid request: {type(e).__name__}"})
        except Exception as e:  # pragma: no cover - last resort
            if os.environ.get("CC_DEBUG"):
                import traceback
                traceback.print_exc()
            return h._json(500, {"ok": False, "error": f"internal error: {type(e).__name__}"})
    wrapped.__name__ = fn.__name__
    return wrapped


def need_obj(body) -> dict:
    if not isinstance(body, dict):
        raise ApiError(400, "body must be a JSON object")
    return body


def rel(c: Ctx, p: Path) -> str:
    try:
        return str(Path(p).resolve().relative_to(c.paths["root"].resolve()))
    except ValueError:
        return "<outside-repo>"


# ------------------------------------------------------------------ market-data coverage
def holiday_dates(year: int) -> set[dt.date]:
    """Reuses tradelab.data.calendar.holiday_candidates (a CANDIDATE list, not an exchange calendar)."""
    import sys
    src = str(ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from tradelab.data.calendar import holiday_candidates
    return holiday_candidates(year)


def expected_sessions(first: dt.date, last: dt.date) -> list[dt.date]:
    out, d = [], first
    hol: dict[int, set] = {}
    while d <= last:
        if d.weekday() < 5:
            if d.year not in hol:
                hol[d.year] = holiday_dates(d.year)
            # Dec 24 is in the candidate list because Globex closes early; for daily equity bars it is a normal session.
            if d not in hol[d.year] or (d.month == 12 and d.day == 24):
                out.append(d)
        d += dt.timedelta(days=1)
    return out


def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def read_daily_csv(path: Path) -> dict:
    rows, malformed = [], 0
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        rd = csv.DictReader(fh)
        cols = rd.fieldnames or []
        for r in rd:
            try:
                ts = dt.datetime.fromisoformat((r.get("ts_open") or "").replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=dt.timezone.utc)
            except ValueError:
                malformed += 1
                continue
            rows.append((ts, [_f(r.get(k)) for k in ("open", "high", "low", "close", "volume")]))
    return {"columns": cols, "rows": rows, "malformed_timestamps": malformed}


def manifest_entries(c: Ctx) -> dict:
    try:
        data = json.loads(Path(c.paths["manifest"]).read_text())
        return {e["symbol"]: e for e in data if isinstance(e, dict) and "symbol" in e}
    except (OSError, ValueError, KeyError):
        return {}


def coverage_for(c: Ctx, symbol: str) -> dict:
    f = Path(c.paths["data_dir"]) / f"{symbol}_1d.csv"
    man = manifest_entries(c).get(symbol)
    out = {"symbol": symbol, "resolution": "1D", "file": rel(c, f), "exists": f.is_file(),
           "synthetic": "SYNTHETIC" in str(f).upper(), "adjustment": "unadjusted" if man and man.get("adjusted_for_dividends") is False else "unknown (no manifest entry)",
           "manifest": None, "n_bars": 0, "first": None, "last": None, "expected_sessions": 0, "missing_sessions": 0,
           "missing_sessions_first_20": [], "gaps": [], "extra_non_session_bars": 0, "quality": {}, "quality_failures": [],
           "blocking_failures": [], "notes": [], "session_check": "weekdays minus tradelab.data.calendar.holiday_candidates (approximate: early closes and special closures not modelled)"}
    if man:
        out["manifest"] = {k: man.get(k) for k in ("contract_id", "exchange", "sha256", "n_bars", "first", "last", "retrieved_on",
                                                       "source_tag", "delayed_seconds", "adjusted_for_dividends")}
        raw = Path(c.paths["root"]) / str(man.get("raw_file", ""))
        if raw.is_file():
            ok = hashlib.sha256(raw.read_bytes()).hexdigest() == man.get("sha256")
            out["manifest"]["raw_file_sha256_verified"] = ok
            if not ok:
                out["quality_failures"].append({"code": "raw_sha_mismatch", "severity": "error", "count": 1})
        else:
            out["manifest"]["raw_file_sha256_verified"] = None
            out["notes"].append("raw connector file not present locally (git-ignored/ephemeral); sha256 not re-verified")
    if not f.is_file():
        out["blocking_failures"].append({"code": "no_data_file", "detail": f"{symbol}_1d.csv not found"})
        return out
    d = read_daily_csv(f)
    need = {"ts_open", "open", "high", "low", "close", "volume"}
    if not need <= set(d["columns"]):
        out["blocking_failures"].append({"code": "missing_columns", "detail": sorted(need - set(d["columns"]))})
        return out
    rows = d["rows"]
    out["n_bars"] = len(rows)
    q = {"malformed_timestamps": d["malformed_timestamps"], "duplicates": 0, "non_monotonic_steps": 0, "nan_rows": 0,
         "nonpositive_price": 0, "high_below_low": 0, "open_or_close_outside_high_low": 0, "zero_volume": 0, "negative_volume": 0}
    seen, prev = set(), None
    dates = []
    for ts, (o, h, l, cl, v) in rows:
        if ts in seen:
            q["duplicates"] += 1
        seen.add(ts)
        if prev is not None and ts < prev:
            q["non_monotonic_steps"] += 1
        prev = ts
        dates.append(ts.astimezone(NY).date())
        vals = (o, h, l, cl)
        if any(x is None for x in vals) or v is None:
            q["nan_rows"] += 1
            continue
        if any(x <= 0 for x in vals):
            q["nonpositive_price"] += 1
        if h < l:
            q["high_below_low"] += 1
        if o > h or o < l or cl > h or cl < l:
            q["open_or_close_outside_high_low"] += 1
        if v == 0:
            q["zero_volume"] += 1
        if v < 0:
            q["negative_volume"] += 1
    out["quality"] = q
    severities = {"malformed_timestamps": "error", "duplicates": "error", "non_monotonic_steps": "error", "nan_rows": "error",
                  "nonpositive_price": "error", "high_below_low": "error", "negative_volume": "error",
                  "open_or_close_outside_high_low": "warning", "zero_volume": "warning"}
    for k, n in q.items():
        if n:
            out["quality_failures"].append({"code": k, "severity": severities[k], "count": n})
            if severities[k] == "error":
                out["blocking_failures"].append({"code": k, "detail": f"{n} rows"})
    if not dates:
        out["blocking_failures"].append({"code": "empty", "detail": "no usable bars"})
        return out
    uniq = sorted(set(dates))
    out["first"], out["last"] = uniq[0].isoformat(), uniq[-1].isoformat()
    exp = expected_sessions(uniq[0], uniq[-1])
    have = set(uniq)
    missing = [x for x in exp if x not in have]
    out["expected_sessions"], out["missing_sessions"] = len(exp), len(missing)
    out["missing_sessions_first_20"] = [x.isoformat() for x in missing[:20]]
    out["extra_non_session_bars"] = len([x for x in uniq if x not in set(exp)])
    out["gaps"] = [{"from": a.isoformat(), "to": b.isoformat(), "calendar_days": (b - a).days}
                   for a, b in zip(uniq, uniq[1:]) if (b - a).days > GAP_CALENDAR_DAYS][:50]
    if man:
        mm = []
        if man.get("n_bars") != len(rows):
            mm.append(f"manifest n_bars {man.get('n_bars')} != file {len(rows)}")
        if str(man.get("first", ""))[:10] != out["first"]:
            mm.append(f"manifest first {str(man.get('first'))[:10]} != file {out['first']}")
        if str(man.get("last", ""))[:10] != out["last"]:
            mm.append(f"manifest last {str(man.get('last'))[:10]} != file {out['last']}")
        out["manifest_mismatch"] = mm
    return out


def available_symbols(c: Ctx) -> list[str]:
    syms = {p.name[:-len("_1d.csv")] for p in Path(c.paths["data_dir"]).glob("*_1d.csv")} if Path(c.paths["data_dir"]).is_dir() else set()
    return sorted(s for s in syms if SYM_RE.match(s))


def parse_date(s, name) -> dt.date:
    if not isinstance(s, str) or not DATE_RE.match(s):
        raise ApiError(400, f"{name} must be YYYY-MM-DD")
    try:
        return dt.date.fromisoformat(s)
    except ValueError:
        raise ApiError(400, f"{name} is not a valid date") from None


def check_request(c: Ctx, symbols, start, end, resolution="1D") -> dict:
    """Adequacy of the local data for {symbols,start,end,resolution}. Returns {adequate, reasons, per_symbol}."""
    reasons, per = [], {}
    if resolution != "1D":
        reasons.append({"code": "resolution_unavailable", "detail": f"only 1D bars exist locally; requested {resolution!r} (no intraday data)"})
    if not symbols:
        reasons.append({"code": "no_symbols", "detail": "no symbols requested"})
    s_d, e_d = parse_date(start, "start"), parse_date(end, "end")
    if e_d < s_d:
        raise ApiError(400, "end is before start")
    for s in symbols:
        cov = coverage_for(c, s)
        per[s] = {"first": cov["first"], "last": cov["last"], "n_bars": cov["n_bars"]}
        if not cov["exists"]:
            reasons.append({"symbol": s, "code": "no_data", "detail": f"no daily data file for {s}"})
            continue
        for b in cov["blocking_failures"]:
            reasons.append({"symbol": s, "code": b["code"], "detail": b["detail"]})
        if cov["first"] and (cov["first"] > start or cov["last"] < end):
            reasons.append({"symbol": s, "code": "range_not_covered",
                            "detail": f"{s} data covers {cov['first']}..{cov['last']}; requested {start}..{end}"})
        if cov["first"]:
            lo, hi = max(s_d, dt.date.fromisoformat(cov["first"])), min(e_d, dt.date.fromisoformat(cov["last"]))
            if hi >= lo:
                exp = expected_sessions(lo, hi)
                miss = _missing_in_window(c, s, exp)
                per[s]["expected_sessions_in_window"], per[s]["missing_sessions_in_window"] = len(exp), miss
                if exp and 100.0 * miss / len(exp) > MAX_MISSING_PCT:
                    reasons.append({"symbol": s, "code": "too_many_missing_sessions",
                                    "detail": f"{miss} of {len(exp)} expected sessions missing in window (> {MAX_MISSING_PCT}%)"})
    return {"adequate": not reasons, "reasons": reasons, "per_symbol": per,
            "request": {"symbols": list(symbols), "start": start, "end": end, "resolution": resolution}}


def _session_dates(c: Ctx, symbol: str) -> set[str]:
    f = Path(c.paths["data_dir"]) / f"{symbol}_1d.csv"
    return {ts.astimezone(NY).date().isoformat() for ts, _ in read_daily_csv(f)["rows"]}


def _missing_in_window(c: Ctx, symbol: str, exp: list[dt.date]) -> int:
    have = _session_dates(c, symbol)
    return sum(1 for x in exp if x.isoformat() not in have)


def segment_problems(c: Ctx, symbols: list[str], segment: str, split_cfg: dict) -> list[dict]:
    """Inputs that make an experiment on `segment` meaningless. validation also needs train (verdict inputs use both)."""
    probs = []
    covs = {}
    for s in symbols:
        cov = coverage_for(c, s)
        covs[s] = cov
        if not cov["exists"]:
            probs.append({"symbol": s, "code": "no_data", "detail": f"no daily data file for {s} in the processed data directory"})
        for b in cov["blocking_failures"]:
            if b["code"] != "no_data_file":
                probs.append({"symbol": s, "code": b["code"], "detail": b["detail"]})
    if probs:
        return probs
    import sys
    src = str(ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    import pandas as pd
    from tradelab.validation.splits import make_split
    idx = {s: [ts for ts, _ in read_daily_csv(Path(c.paths["data_dir"]) / f"{s}_1d.csv")["rows"]] for s in symbols}
    first = min(min(v) for v in idx.values())
    last = max(max(v) for v in idx.values())
    try:
        split = make_split(pd.Timestamp(first), pd.Timestamp(last), tuple(split_cfg["fractions"]), pd.Timedelta(days=split_cfg["purge_days"]))
    except ValueError as e:
        return [{"symbol": None, "code": "span_too_short", "detail": str(e)}]
    segs = {"train": split.train, "validation": split.validation}
    need = ["train", "validation"] if segment == "validation" else ["train"]
    for name in need:
        a, b = segs[name]
        a, b = a.to_pydatetime(), b.to_pydatetime()
        for s in symbols:
            n = sum(1 for ts in idx[s] if a <= ts < b)
            if n < MIN_SEGMENT_BARS:
                probs.append({"symbol": s, "code": "segment_too_short", "segment": name,
                              "detail": f"{s} has {n} bars in the {name} segment ({a.date()}..{b.date()}); software minimum is {MIN_SEGMENT_BARS} "
                                        "(ATR14 warm-up and non-empty tables; this is NOT a statistical sufficiency threshold)"})
    return probs


@route("GET", r"/api/data/coverage")
@handler
def api_coverage(h, m, q, body):
    c = ctx(h.server)
    manifest = manifest_entries(c)
    syms = sorted(set(available_symbols(c)) | set(manifest))
    out = {"as_of": utc_now(), "market_data_dir": rel(c, c.paths["data_dir"]), "resolution": "1D",
           "note": "Daily bars only. Market data is kept separate from account exports (data/raw/ibkr_exports).",
           "symbols": [coverage_for(c, s) for s in syms]}
    if q.get("symbols"):
        req = [s.strip().upper() for s in q["symbols"].split(",") if s.strip()]
        bad = [s for s in req if not SYM_RE.match(s)]
        if bad:
            raise ApiError(400, f"invalid symbols: {bad}")
        if not q.get("start") or not q.get("end"):
            raise ApiError(400, "start and end (YYYY-MM-DD) are required with symbols")
        out["adequacy"] = check_request(c, req, q["start"], q["end"], q.get("resolution", "1D"))
    return out


# ------------------------------------------------------------------ account-export import
# All names below are from recollection of PUBLIC IBKR documentation and are UNVERIFIED against a real export.
NORMALIZED = {   # normalized field -> (required, candidate source names per container)
    "account_id": (False, ["ClientAccountID", "AccountID", "Account", "accountId"]),
    "symbol": (True, ["Symbol", "symbol"]),
    "con_id": (False, ["ConID", "conid", "Conid"]),
    "asset_class": (False, ["AssetClass", "assetCategory"]),
    "trade_datetime": (True, ["DateTime", "TradeDateTime", "dateTime", "TradeDate", "tradeDate", "ExecutionTime", "Date/Time"]),
    "side": (True, ["Buy/Sell", "BuySell", "buySell", "Side", "Action"]),
    "quantity": (True, ["Quantity", "quantity", "Shares"]),
    "price": (True, ["TradePrice", "tradePrice", "Price", "price"]),
    "currency": (False, ["CurrencyPrimary", "Currency", "currency"]),
    "commission": (False, ["IBCommission", "ibCommission", "Commission", "commission"]),
    "proceeds": (False, ["Proceeds", "proceeds", "Amount", "NetCash"]),
    "trade_id": (False, ["TradeID", "tradeID", "ExecID", "IBExecID"]),
    "order_id": (False, ["IBOrderID", "OrderID", "ibOrderID"]),
    "exchange": (False, ["Exchange", "exchange"]),
}
NUMERIC = {"quantity", "price", "commission", "proceeds"}
DATEISH = {"trade_datetime"}
FLEX_SIGNATURE = {"ClientAccountID", "AccountID", "ConID", "TradeDate", "DateTime", "Buy/Sell", "Quantity", "TradePrice", "TradeMoney",
                  "IBCommission", "Proceeds", "CurrencyPrimary", "AssetClass", "TradeID", "IBOrderID", "OpenCloseIndicator", "NetCash", "Multiplier"}
CONFIRM_SIGNATURE = {"Account", "ConID", "TradeDate", "SettleDate", "Buy/Sell", "Quantity", "Price", "Amount", "Commission", "ExecID", "Shares", "Side"}
XML_CONTAINERS = {"FlexQueryResponse", "FlexStatements", "FlexStatement"}
XML_ROW_TAGS = ("Trade", "TradeConfirm", "Order", "Lot", "OpenPosition", "CashTransaction")
MASK_RE = re.compile(r"account|acct", re.I)
DATE_FORMATS = ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%d;%H%M%S", "%Y%m%d;%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y%m%d;%H%M%S", "%Y-%m-%d;%H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                "%Y%m%d %H%M%S", "%m/%d/%Y")


def mask(field: str, value):
    if isinstance(value, str) and value and MASK_RE.search(field):
        return value[:1] + "*" * max(0, len(value) - 4) + value[-3:] if len(value) > 4 else "***"
    return value


def parse_dateish(v: str) -> bool:
    v = v.strip()
    for f in DATE_FORMATS:
        try:
            dt.datetime.strptime(v, f)
            return True
        except ValueError:
            continue
    return False


class _XmlRefused(Exception):
    pass


def parse_xml(text: str) -> dict:
    """Parse with expat directly so each element has a line number. DTD/entity declarations are refused (no XXE / entity bombs)."""
    low = text[:200000].lower()
    if "<!doctype" in low or "<!entity" in low:
        return {"parse_error": "DTD/entity declarations are not allowed (refused for safety)", "rows": [], "root": None}
    p = expat.ParserCreate()
    stack, rows, counts = [], [], {}
    state = {"root": None}

    def start(name, attrs):
        if state["root"] is None:
            state["root"] = name
        if attrs and name not in XML_CONTAINERS:
            counts[name] = counts.get(name, 0) + 1
            rows.append({"line": p.CurrentLineNumber, "tag": name, "attrs": dict(attrs), "parent": stack[-1] if stack else None})
        elif name in XML_CONTAINERS and name == "FlexStatement":
            state["statement_attrs"] = sorted(attrs)
        stack.append(name)

    def end(name):
        stack.pop()
    p.StartElementHandler, p.EndElementHandler = start, end
    try:
        p.Parse(text.encode("utf-8"), True)
    except expat.ExpatError as e:
        return {"parse_error": f"XML not well-formed at line {e.lineno}, column {e.offset}", "rows": rows, "root": state["root"], "counts": counts}
    return {"parse_error": None, "rows": rows, "root": state["root"], "counts": counts, "statement_attrs": state.get("statement_attrs", [])}


def best_candidate(header: list[str], cands: list[str]):
    for cnd in cands:
        if cnd in header:
            return cnd
    low = {h.lower(): h for h in header}
    for cnd in cands:
        if cnd.lower() in low:
            return low[cnd.lower()]
    return None


def detect_csv(text: str):
    """returns (format, header, data_rows[(line, [cells])], structural_notes)"""
    rd = csv.reader(io.StringIO(text))
    recs = []
    try:
        for r in rd:
            recs.append((rd.line_num, r))
    except csv.Error as e:
        return "unknown", [], [], [f"CSV parse error near line {rd.line_num}: {e}"]
    recs = [(ln, r) for ln, r in recs if any(c.strip() for c in r)]
    if not recs:
        return "unknown", [], [], ["file has no non-empty lines"]
    notes = []
    first = recs[0][1]
    if first and first[0].strip('"') in ("BOF", "BOA", "HEADER", "EOF"):
        # UNVERIFIED recollection of the sectioned Flex CSV layout: a HEADER row names the fields, DATA rows hold the values.
        hdr_i = next((i for i, (_, r) in enumerate(recs) if r and r[0] == "HEADER"), None)
        if hdr_i is None:
            return "ibkr_flex_csv", [], [], ["sectioned Flex CSV (BOF/BOA) with no HEADER row found; layout is UNVERIFIED"]
        header = recs[hdr_i][1][2:] if len(recs[hdr_i][1]) > 2 else recs[hdr_i][1][1:]
        data = [(ln, r[2:] if len(r) > 2 else r[1:]) for ln, r in recs[hdr_i + 1:] if r and r[0] == "DATA"]
        notes.append("sectioned layout (BOF/HEADER/DATA/EOF) handled from recollection of the public docs; UNVERIFIED against a real file")
        return "ibkr_flex_csv", header, data, notes
    header = [c.strip() for c in first]
    data = recs[1:]
    hs = set(header)
    fscore, cscore = len(hs & FLEX_SIGNATURE), len(hs & CONFIRM_SIGNATURE)
    if "SettleDate" in hs and "TradePrice" not in hs and cscore >= 3:
        fmt = "trade_confirmation_like"
    elif fscore >= 3 and fscore >= cscore:
        fmt = "ibkr_flex_csv"
    elif cscore >= 3:
        fmt = "trade_confirmation_like"
    else:
        fmt = "unknown"
    return fmt, header, data, notes


def analyse_upload(filename: str, content: str) -> dict:
    """Pure function: detect, map, validate. Returns the preview structure (no storage, no logging of contents)."""
    raw = content.encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    res = {"filename": os.path.basename(filename)[:200], "size_bytes": len(raw), "sha256": sha, "container": None, "format": "unknown",
           "format_confidence": "heuristic: header-name matching against UNVERIFIED recollection of IBKR field names",
           "header_fields": [], "preview_rows": [], "row_counts": {"total": 0, "valid": 0, "quarantined": 0}, "quarantined": [],
           "quarantined_truncated": False, "field_mapping": [], "missing_required_fields": [], "notes": [], "parse_error": None,
           "reconciliation": "not performed (no normalization implemented)",
           "support": "NO real IBKR export sample exists yet; field names are UNVERIFIED; fixtures are SYNTHETIC (docs/IMPORT_SUPPORT_MATRIX.md)"}
    stripped = content.lstrip("﻿ \t\r\n")
    rows: list[tuple[int, dict]] = []   # (line, {field: value}) validated later
    if stripped.startswith("<"):
        res["container"] = "xml"
        px = parse_xml(content)
        if px["parse_error"]:
            res["parse_error"] = px["parse_error"]
            return res
        if px["root"] != "FlexQueryResponse":
            res["notes"].append(f"XML root element is {px['root']!r}, not FlexQueryResponse")
            return res
        counts = px["counts"]
        row_tag = next((t for t in XML_ROW_TAGS if counts.get(t)), None)
        res["row_types"] = counts
        if row_tag is None:
            res["notes"].append("no Trade/TradeConfirm/Order/... elements with attributes found")
            res["format"] = "ibkr_flex_xml"
            return res
        res["format"] = "trade_confirmation_like" if row_tag == "TradeConfirm" else "ibkr_flex_xml"
        sel = [r for r in px["rows"] if r["tag"] == row_tag]
        header: list[str] = []
        for r in sel:
            for k in r["attrs"]:
                if k not in header:
                    header.append(k)
        res["header_fields"] = header
        res["notes"].append(f"rows taken from <{row_tag}> elements ({len(sel)}); other element types are counted but not validated")
        rows = [(r["line"], r["attrs"]) for r in sel]
    else:
        res["container"] = "csv"
        fmt, header, data, notes = detect_csv(content)
        res["format"], res["header_fields"], res["notes"] = fmt, header, notes
        if fmt == "unknown" and not header:
            return res
        for ln, cells in data:
            if len(cells) != len(header):
                rows.append((ln, {"__bad__": f"column count {len(cells)} != header {len(header)}"}))
            else:
                rows.append((ln, dict(zip(header, [c.strip() for c in cells]))))
    # mapping
    hdr = res["header_fields"]
    mapping = {}
    for nf, (req, cands) in NORMALIZED.items():
        src = best_candidate(hdr, cands)
        mapping[nf] = src
        res["field_mapping"].append({"normalized_field": nf, "required": req, "source_field": src,
                                     "status": "mapped" if src else ("MISSING (required)" if req else "absent (optional)")})
        if req and not src:
            res["missing_required_fields"].append(nf)
    known = {s for s in mapping.values() if s}
    res["unmapped_source_fields"] = [h_ for h_ in hdr if h_ not in known]
    # validation
    quarantined, valid = [], 0
    for ln, rec in rows:
        reason = None
        if "__bad__" in rec:
            reason = rec["__bad__"]
        else:
            for nf, src in mapping.items():
                if not src:
                    continue
                v = rec.get(src, "")
                if NORMALIZED[nf][0] and (v is None or str(v).strip() == ""):
                    reason = f"missing value for required field {src}"
                    break
                if nf in NUMERIC and str(v).strip() != "":
                    try:
                        if not math.isfinite(float(str(v).replace(",", ""))):
                            raise ValueError
                    except ValueError:
                        reason = f"non-numeric value in {src}"
                        break
                if nf in DATEISH and str(v).strip() != "" and not parse_dateish(str(v)):
                    reason = f"unparseable date/time in {src}"
                    break
        if reason:
            quarantined.append({"line": ln, "reason": reason})
        else:
            valid += 1
    res["row_counts"] = {"total": len(rows), "valid": valid, "quarantined": len(quarantined)}
    res["quarantined"] = quarantined[:50]
    res["quarantined_truncated"] = len(quarantined) > 50
    prev = []
    for ln, rec in rows[:20]:
        prev.append({"line": ln, "values": {k: mask(k, v) for k, v in rec.items() if k != "__bad__"} if "__bad__" not in rec else {"__unparsed__": rec["__bad__"]}})
    res["preview_rows"] = prev
    res["account_fields_masked"] = True
    return res


def check_upload_body(body) -> tuple[str, str]:
    b = need_obj(body)
    fn, content = b.get("filename"), b.get("content")
    if not isinstance(fn, str) or not fn.strip() or len(fn) > 200:
        raise ApiError(400, "filename must be a non-empty string (<=200 chars)")
    if not isinstance(content, str) or not content:
        raise ApiError(400, "content must be a non-empty string (file text)")
    if len(content.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise ApiError(413, f"content exceeds {MAX_IMPORT_BYTES} bytes")
    return fn, content


def safe_name(fn: str) -> str:
    base = os.path.basename(fn.replace("\\", "/")) or "upload"
    base = re.sub(r"[^A-Za-z0-9._-]", "_", base).lstrip(".")[:80] or "upload"
    return base


def find_dup(c: Ctx, sha: str):
    r = c.rdb.one("SELECT id, filename, imported_at, size_bytes FROM import_registry WHERE sha256=?", (sha,))
    return r


@route("POST", r"/api/import/preview")
@handler
def api_preview(h, m, q, body):
    c = ctx(h.server)
    fn, content = check_upload_body(body)
    res = analyse_upload(fn, content)
    res["duplicate_of"] = find_dup(c, res["sha256"])
    res["would_commit"] = (res["format"] != "unknown" and not res["parse_error"]) and not res["duplicate_of"]
    return res


@route("POST", r"/api/import/commit")
@handler
def api_commit(h, m, q, body):
    c = ctx(h.server)
    fn, content = check_upload_body(body)
    res = analyse_upload(fn, content)
    dup = find_dup(c, res["sha256"])
    if dup:
        c.rdb.audit("import.commit", res["sha256"][:16], "duplicate", {"existing_id": dup["id"]})
        return 200, {"ok": True, "duplicate": True, "created": False, "existing": dup, "sha256": res["sha256"]}
    if res["parse_error"] or res["format"] == "unknown":
        c.rdb.audit("import.commit", res["sha256"][:16], "refused", {"reason": res["parse_error"] or "unknown format"})
        raise ApiError(422, "file refused: " + (res["parse_error"] or "format not recognised (see /api/import/preview)"),
                       format=res["format"], sha256=res["sha256"])
    d = Path(c.paths["imports_dir"])
    d.mkdir(parents=True, exist_ok=True)
    target = d / f"{res['sha256'][:16]}_{safe_name(fn)}"
    raw = content.encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=str(d), prefix=".incoming_")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(raw)
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    try:
        cur = c.rdb.run("INSERT INTO import_registry(sha256,filename,stored_path,size_bytes,imported_at,format,row_counts,quarantined,status) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (res["sha256"], res["filename"], rel(c, target), len(raw), utc_now(), res["format"],
                         json.dumps(res["row_counts"]), res["row_counts"]["quarantined"], "raw stored; NOT normalized; NOT reconciled"))
    except Exception:  # UNIQUE race: someone committed identical bytes between our check and insert
        dup = find_dup(c, res["sha256"])
        if dup:
            return 200, {"ok": True, "duplicate": True, "created": False, "existing": dup, "sha256": res["sha256"]}
        raise
    c.rdb.audit("import.commit", res["sha256"][:16], "stored", {"id": cur.lastrowid, "format": res["format"], "size": len(raw)})
    return 201, {"ok": True, "duplicate": False, "created": True, "entry": registry_row(c, cur.lastrowid),
                 "reconciliation": res["reconciliation"]}


def registry_row(c: Ctx, rid: int):
    r = c.rdb.one("SELECT * FROM import_registry WHERE id=?", (rid,))
    return _reg_public(r) if r else None


def _reg_public(r: dict) -> dict:
    return {"id": r["id"], "sha256": r["sha256"], "filename": r["filename"], "stored_path": r["stored_path"], "size_bytes": r["size_bytes"],
            "imported_at": r["imported_at"], "format": r["format"], "row_counts": json.loads(r["row_counts"]),
            "quarantined": r["quarantined"], "status": r["status"]}


@route("GET", r"/api/import/registry")
@handler
def api_registry(h, m, q, body):
    c = ctx(h.server)
    rows = c.rdb.all("SELECT * FROM import_registry ORDER BY id DESC")
    return {"entries": [_reg_public(r) for r in rows], "directory": rel(c, c.paths["imports_dir"]),
            "note": "Account exports only. Raw files are git-ignored; nothing here is normalized or reconciled."}
