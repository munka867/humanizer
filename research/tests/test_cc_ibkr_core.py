"""IBKR read-only adapter: guard, no-order surface (static + runtime), quote/type mapping, pacing (fake clock), history (resume/dedupe/frozen
protection), error mapping, manager. Everything runs against the SIMULATED IB (fake_ib.py). Nothing here touches a real TWS/Gateway."""
import ast
import asyncio
import csv
import datetime as dt
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from command_center.ibkr import errors as E, guard, history as H  # noqa: E402
from command_center.ibkr.fake_ib import FakeIB, FakeStock  # noqa: E402
from command_center.ibkr.manager import IBKRManager, mask_account  # noqa: E402
from command_center.ibkr.readonly_client import ReadOnlyClient, NotStockError, LibraryMissing  # noqa: E402

PKG = ROOT / "command_center" / "ibkr"
FORBIDDEN = ["placeOrder", "cancelOrder", "modifyOrder", "whatIf", "reqGlobalCancel", "exerciseOptions", "placeorder", "cancelorder", "whatiforder", "submitOrder", "transmit"]


def run(coro):
    return asyncio.run(coro)


class FakeClock:
    def __init__(self, t=1_792_000_000.0):   # 2026-10-14 ~ 17:46 UTC
        self.t = t

    def now(self):
        return self.t

    async def sleep(self, d):
        self.t += d


def make_client(ib=None, kind="paper_tws"):
    return ReadOnlyClient(guard.resolve_target(kind), ib=ib or FakeIB(), stock_factory=FakeStock, source="fake")


# ------------------------------------------------------------------ guard
def test_guard_defaults_and_refusals():
    t = guard.resolve_target("paper_tws")
    assert (t.host, t.port, t.readonly, t.live_port) == ("127.0.0.1", 7497, True, False) and 9100 <= t.client_id <= 9109
    assert guard.resolve_target("paper_gateway").port == 4002
    for kind in ("live_tws_readonly", "live_gateway_readonly"):
        with pytest.raises(guard.GuardError, match="LIVE"):
            guard.resolve_target(kind, env={})
    live = guard.resolve_target("live_tws_readonly", env={guard.ENV_ALLOW_LIVE: "1"})
    assert live.port == 7496 and live.readonly and live.live_port
    with pytest.raises(guard.GuardError):
        guard.resolve_target("live_tws_readonly", env={guard.ENV_ALLOW_LIVE: "yes"})  # only the exact value 1
    for host in ("192.168.1.5", "example.com", "0.0.0.0", "127.0.0.1.evil.com", ""):
        with pytest.raises(guard.GuardError, match="not this computer"):
            guard.resolve_target("paper_tws", host=host)
    for host in ("localhost", "::1", "127.0.0.1"):
        assert guard.resolve_target("paper_tws", host=host).host == host
    for port in (7496, 4001, 80, 0, 7498, True, "7497"):
        with pytest.raises(guard.GuardError):
            guard.resolve_target("paper_tws", port=port, env={})
    with pytest.raises(guard.GuardError):
        guard.resolve_target("paper_tws", port=4002)         # port of another kind
    for cid in (0, 1, 9099, 9110, -1, True, "9101", 9101.0):
        with pytest.raises(guard.GuardError, match="clientId"):
            guard.resolve_target("paper_tws", client_id=cid)
    with pytest.raises(guard.GuardError):
        guard.resolve_target("bogus")
    assert guard.allowed_kinds(env={}) == ["paper_tws", "paper_gateway"]


def test_connect_passes_readonly_and_guarded_target():
    ib = FakeIB()
    c = make_client(ib)
    run(c.connect())
    assert ib.connect_args == {"host": "127.0.0.1", "port": 7497, "clientId": guard.CLIENT_ID_DEFAULT, "readonly": True}


# ------------------------------------------------------------------ no order surface
def test_static_no_order_api_names_in_package():
    files = sorted(PKG.glob("*.py")) + [ROOT / "command_center" / "api_ibkr.py"]
    assert len(files) >= 8
    for f in files:
        src = f.read_text()
        for name in FORBIDDEN:
            assert name.lower() not in src.lower(), f"{f.name} mentions {name}"
        tree = ast.parse(src)
        for n in ast.walk(tree):
            ident = getattr(n, "id", None) or getattr(n, "attr", None) or (n.name if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else None)
            if ident:
                assert not re.search(r"(?i)^(place|modify|cancel)_?order|^whatif|globalcancel|exercise", ident), f"{f.name}: {ident}"


def test_only_one_module_imports_the_third_party_library():
    for f in PKG.glob("*.py"):
        imports = [n for n in ast.walk(ast.parse(f.read_text())) if isinstance(n, (ast.Import, ast.ImportFrom))]
        mods = []
        for n in imports:
            mods += [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module or ""]
        has = any(m.split(".")[0] in ("ib_async", "ib_insync", "ibapi") for m in mods)
        assert has == (f.name == "readonly_client.py"), f.name
    # and it is a lazy (function-level) import, so the server starts without it
    src = (PKG / "readonly_client.py").read_text()
    top = ast.parse(src).body
    assert not any(isinstance(n, (ast.Import, ast.ImportFrom)) and any(getattr(a, "name", "").startswith("ib_async") for a in getattr(n, "names", [])) for n in top)


def test_runtime_wrapper_has_no_order_surface_and_never_returns_ib():
    ib = FakeIB()
    c = make_client(ib)
    run(c.connect())
    names = [n for n in dir(c)]
    for n in names:
        assert not re.search(r"(?i)order$|^place|^modify|^cancel|whatif|globalcancel|exercise|^transmit", n.replace("open_orders", "")), n
    public = [n for n in names if not n.startswith("_")]
    assert {"connect", "disconnect", "qualify_stock", "set_market_data_type", "subscribe_quote", "quote", "historical_bars", "account_summary", "positions", "open_orders", "executions", "server_time"} <= set(public)
    inst = run(c.qualify_stock("SPY", "ARCA", "USD"))
    c.subscribe_quote(inst)
    results = [c.managed_accounts(), c.positions(), c.open_orders(), c.executions(), c.quote(inst["conid"]), c.subscribed(), run(c.account_summary()), c.daily_pnl("X"), inst]
    for r in results:
        assert not isinstance(r, FakeIB)
    assert not any(isinstance(getattr(c, n), FakeIB) for n in public if not callable(getattr(c, n)))
    assert ib.order_surface_called is False
    assert not hasattr(c, "ib") and not hasattr(c, "_ib")


def test_non_stock_contracts_refused():
    class Opt(FakeStock):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.secType = "OPT"
    c = ReadOnlyClient(guard.resolve_target("paper_tws"), ib=FakeIB(), stock_factory=Opt, source="fake")
    run(c.connect())
    with pytest.raises(NotStockError):
        run(c.qualify_stock("SPY", "ARCA"))


def test_unqualifiable_or_ambiguous_contract_refused():
    ib = FakeIB()
    ib.unqualifiable.add("ZZZ")
    c = make_client(ib)
    run(c.connect())
    with pytest.raises(ValueError, match="not found or ambiguous"):
        run(c.qualify_stock("ZZZ", "ARCA"))


def test_conid_mismatch_refused():
    ib = FakeIB()

    async def q(*cs, **k):
        return [type("C", (), {"secType": "STK", "symbol": "SPY", "conId": 999, "primaryExchange": "ARCA", "currency": "USD"})()]
    ib.qualifyContractsAsync = q
    c = make_client(ib)
    run(c.connect())
    with pytest.raises(ValueError, match="expected 756733"):
        run(c.qualify_stock("SPY", "ARCA", conid=756733))


# ------------------------------------------------------------------ quote / data-type mapping
@pytest.mark.parametrize("n,label", [(1, "live"), (2, "frozen"), (3, "delayed"), (4, "delayed_frozen")])
def test_quote_data_type_per_tick(n, label):
    ib = FakeIB()
    c = make_client(ib)
    run(c.connect())
    c.set_market_data_type(n)
    assert ib.market_data_type_calls == [n]
    inst = run(c.qualify_stock("SPY", "ARCA"))
    c.subscribe_quote(inst)
    q = c.quote(inst["conid"])
    assert q.data_type == n and q.data_type_label == label and q.source == "fake"
    assert q.last and q.bid and q.ask and q.volume and q.prior_close and q.time.endswith("+00:00")
    t = dt.datetime.fromisoformat(q.time)
    lag = (dt.datetime.now(dt.timezone.utc) - t).total_seconds()
    assert (lag > 600) == (n in (3, 4))      # delayed ticks carry their own (older) timestamp


def test_invalid_market_data_type_rejected_and_missing_values_are_none():
    c = make_client()
    run(c.connect())
    with pytest.raises(ValueError):
        c.set_market_data_type(5)
    ib = c._ReadOnlyClient__ib
    inst = run(c.qualify_stock("SPY", "ARCA"))
    c.subscribe_quote(inst)
    tk = ib._tickers[inst["conid"]]
    tk.last = tk.bid = tk.ask = float("nan")
    tk.volume = -1
    q = c.quote(inst["conid"])
    assert q.last is None and q.bid is None and q.ask is None            # nan/-1 never become zero
    assert c.quote(123456) is None


# ------------------------------------------------------------------ error mapping
@pytest.mark.parametrize("code", [162, 200, 354, 10167, 504, 502, 1100, 1101, 1102, 2104, 2105, 2106, 2107, 2108, 321, 326, 10186, 10197])
def test_error_codes_are_plain_language_with_a_fix(code):
    d = E.explain(code, "raw text")
    assert d["title"] and d["message"] and d["fix"] and d["code"] == code and d["basis"]
    assert d["severity"] in ("info", "warn", "error")


def test_error_severity_and_unmapped_passthrough():
    assert all(E.explain(c)["severity"] == "info" for c in (2104, 2106, 2107, 2108, 1102))
    assert E.explain(2103)["severity"] == "warn" and E.explain(502)["severity"] == "error"
    d = E.explain(99999, "weird text")
    assert d["basis"] == "unmapped code" and d["message"] == "weird text"
    assert "UNVERIFIED" in E.explain(10167)["basis"] and "UNVERIFIED" in E.explain(321)["basis"]
    assert E.explain("x")["code"] is None


def test_exception_mapping():
    assert "listening" in E.explain_exception(ConnectionRefusedError(), 7497)["message"]
    assert "7497" in E.explain_exception(ConnectionRefusedError(), 7497)["message"]
    assert "did not" in E.explain_exception(asyncio.TimeoutError())["title"].lower()
    assert "ib_async" in E.explain_exception(LibraryMissing("x"))["title"]
    assert E.explain_exception(guard.GuardError("nope"))["title"].startswith("Connection refused")
    assert E.explain_exception(RuntimeError("boom"))["message"].startswith("RuntimeError")


# ------------------------------------------------------------------ pacing with a fake clock
def test_pacer_identical_request_15s():
    clk = FakeClock()
    p = H.Pacer(clk.now, clk.sleep)
    run(p.acquire("c", "r"))
    t0 = clk.t
    run(p.acquire("c", "r"))
    assert clk.t - t0 >= 15.0
    t1 = clk.t
    run(p.acquire("c", "other"))
    assert clk.t - t1 < 15.0


def test_pacer_six_per_two_seconds_same_contract():
    clk = FakeClock()
    p = H.Pacer(clk.now, clk.sleep)
    t0 = clk.t
    for i in range(5):
        run(p.acquire("c", f"r{i}"))
    assert clk.t == t0                        # five are fine
    run(p.acquire("c", "r5"))
    assert clk.t - t0 >= 2.0                   # the sixth would be a violation: it waits
    run(p.acquire("another", "x"))             # other contracts are not held back by this rule


def test_pacer_60_per_10_minutes_and_bid_ask_double():
    clk = FakeClock()
    p = H.Pacer(clk.now, clk.sleep)
    t0 = clk.t
    for i in range(58):
        run(p.acquire(f"c{i}", f"r{i}"))
    assert clk.t == t0
    run(p.acquire("cx", "rx"))
    assert clk.t - t0 >= 600.0 - 1e-6          # the 59th waits for the window to roll
    clk2 = FakeClock()
    p2 = H.Pacer(clk2.now, clk2.sleep)
    for i in range(29):
        run(p2.acquire(f"c{i}", f"r{i}", weight=2))   # 29 BID_ASK requests = 58 units
    t = clk2.t
    run(p2.acquire("cz", "rz", weight=2))
    assert clk2.t - t >= 600.0 - 40            # counted twice


def test_pacer_never_exceeds_limits_over_many_requests():
    clk = FakeClock()
    p = H.Pacer(clk.now, clk.sleep)
    stamps = []
    for i in range(130):
        run(p.acquire("c", f"r{i}"))
        stamps.append(clk.t)
    for i, t in enumerate(stamps):
        assert sum(1 for s in stamps if t - 600 < s <= t) <= 60
        assert sum(1 for s in stamps if t - 2 < s <= t) <= 5


# ------------------------------------------------------------------ history
def dl(tmp_path, ib=None, clk=None, source="fake"):
    ib = ib or FakeIB()
    clk = clk or FakeClock()
    c = make_client(ib)
    run(c.connect())
    inst = run(c.qualify_stock("SPY", "ARCA", conid=756733))
    d = H.HistoryDownloader(c, tmp_path, source, clock=clk.now, sleep=clk.sleep)
    return ib, clk, c, inst, d


def read_rows(p):
    with open(p, newline="") as f:
        return list(csv.DictReader(f))


def test_daily_download_format_manifest_and_incomplete_bar_dropped(tmp_path):
    ib, clk, c, inst, d = dl(tmp_path)
    res = run(d.download(inst, "1d", H.parse_span(years=2, bar_size="1d"), True, 3))
    out = Path(res["dir"])
    assert out.name.startswith("fake_") and out.parent == tmp_path / "data"
    rows = read_rows(out / "SPY_1d.csv")
    assert list(rows[0]) == ["ts_open", "open", "high", "low", "close", "volume", "contract"] and rows[0]["contract"] == "SPY"
    ts = [r["ts_open"] for r in rows]
    assert ts == sorted(ts) and len(ts) == len(set(ts)) and len(ts) > 450
    assert all(re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:30:00\+00:00", t) and t[11:13] in ("13", "14") for t in ts)   # 09:30 New York as UTC
    now = dt.datetime.fromtimestamp(clk.t, dt.timezone.utc)
    last_open = dt.datetime.fromisoformat(rows[-1]["ts_open"])
    assert last_open.astimezone(dt.timezone(dt.timedelta(hours=-4))).replace(hour=16, minute=0) <= now      # no still-forming daily bar is stored
    man = json.loads((out / "MANIFEST.json").read_text())[0]
    for k in ("symbol", "conId", "exchange", "bar_size", "what_to_show", "use_rth", "market_data_type", "retrieved_utc", "rows", "sha256", "delayed", "source", "adjusted_for_dividends"):
        assert k in man
    assert man["conId"] == 756733 and man["use_rth"] is True and man["delayed"] is True and man["source"] == "fake" and man["rows"] == len(rows)
    assert man["sha256"] == hashlib.sha256((out / "SPY_1d.csv").read_bytes()).hexdigest()
    assert "SIMULATED" in man["note"]
    assert not list(out.glob("*.partial.*"))                     # partials cleaned up


def test_chunked_paging_dedupe_and_progress(tmp_path):
    ib, clk, c, inst, d = dl(tmp_path)
    seen = []
    res = run(d.download(inst, "1d", H.parse_span(years=3, bar_size="1d"), True, None, progress=seen.append))
    assert res["chunks"] == len(seen) == len(ib.history_calls) >= 3
    assert [s["chunks_done"] for s in seen] == list(range(1, len(seen) + 1))              # real chunks, in order
    assert all(s["chunks_total_estimate"] >= s["chunks_done"] for s in seen)
    ends = [call[1] for call in ib.history_calls]
    assert ends == sorted(ends, reverse=True)                                              # pages backwards in time
    rows = read_rows(Path(res["dir"]) / "SPY_1d.csv")
    assert len({r["ts_open"] for r in rows}) == len(rows)                                   # overlap deduped


def test_resume_after_cancel_does_not_repeat_chunks_and_matches_single_run(tmp_path):
    ib, clk, c, inst, d = dl(tmp_path / "a")
    span = H.parse_span(years=3, bar_size="1d")
    full = run(d.download(inst, "1d", span, True, 3))
    full_rows = read_rows(Path(full["dir"]) / "SPY_1d.csv")

    ib2, clk2, c2, inst2, d2 = dl(tmp_path / "b")
    n = {"k": 0}
    with pytest.raises(H.Cancelled):
        run(d2.download(inst2, "1d", span, True, 3, progress=lambda p: n.__setitem__("k", p["chunks_done"]), cancelled=lambda: n["k"] >= 1))
    out = tmp_path / "b" / "data" / next(p.name for p in (tmp_path / "b" / "data").iterdir())
    assert (out / "SPY_1d.partial.csv").exists() and (out / "SPY_1d.partial.json").exists()
    calls_before = len(ib2.history_calls)
    res = run(d2.download(inst2, "1d", span, True, 3))
    assert res["resumed"] is True
    assert len(ib2.history_calls) + 0 == calls_before + (full["chunks"] - 1)           # only the missing chunks were requested
    assert read_rows(Path(res["dir"]) / "SPY_1d.csv") == full_rows


def test_pacing_error_backs_off_and_retries(tmp_path):
    ib, clk, c, inst, d = dl(tmp_path)
    ib.history_errors = [(162, "Historical Market Data Service error message:Historical data request pacing violation")]
    t0 = clk.t
    res = run(d.download(inst, "1d", 200, True, 3))
    assert clk.t - t0 >= 60 and res["rows"] > 100 and len(ib.history_calls) >= 2


def test_persistent_pacing_error_gives_up_with_message(tmp_path):
    ib, clk, c, inst, d = dl(tmp_path)
    ib.history_errors = [(162, "pacing violation")] * 10
    with pytest.raises(H.HistoryError) as ei:
        run(d.download(inst, "1d", 200, True, 3))
    assert "pacing" in ei.value.info["message"].lower()


def test_fatal_error_surfaces_plain_language(tmp_path):
    ib, clk, c, inst, d = dl(tmp_path)
    ib.history_errors = [(200, "No security definition has been found for the request")]
    with pytest.raises(H.HistoryError) as ei:
        run(d.download(inst, "1d", 200, True, 3))
    assert ei.value.info["code"] == 200 and ei.value.info["fix"]


def test_intraday_regular_vs_extended_sessions(tmp_path):
    ib, clk, c, inst, d = dl(tmp_path)
    reg = run(d.download(inst, "5m", H.parse_span(duration="10 D", bar_size="5m"), True, 3))
    eth = run(d.download(inst, "5m", H.parse_span(duration="10 D", bar_size="5m"), False, 3))
    rr, re_ = read_rows(Path(reg["dir"]) / "SPY_5m.csv"), read_rows(Path(eth["dir"]) / "SPY_5m_eth.csv")
    ny = dt.timezone(dt.timedelta(hours=-4))

    def hm(r):
        t = dt.datetime.fromisoformat(r["ts_open"]).astimezone(ny)
        return t.hour * 60 + t.minute
    assert min(map(hm, rr)) >= 9 * 60 + 30 - 60 and max(map(hm, rr)) <= 16 * 60                # regular hours (allow DST slop of 1 h in this check)
    assert len(re_) > len(rr) * 1.5 and min(map(hm, re_)) < 9 * 60 + 30 - 60
    m = json.loads((Path(reg["dir"]) / "MANIFEST.json").read_text())
    assert {e["session"] for e in m} == {"regular", "regular+extended"}
    assert {e["use_rth"] for e in m} == {True, False}
    assert all(r["ts_open"].endswith("+00:00") for r in rr)                                      # UTC bar OPEN


@pytest.mark.parametrize("bar,ib_bar", [("1m", "1 min"), ("5m", "5 mins"), ("15m", "15 mins"), ("1h", "1 hour"), ("1d", "1 day")])
def test_all_bar_sizes_supported(tmp_path, bar, ib_bar):
    ib, clk, c, inst, d = dl(tmp_path)
    res = run(d.download(inst, bar, 3 if bar in ("1m", "5m", "15m") else 40, True, 3))
    assert res["rows"] > 0 and ib.history_calls[0][3] == ib_bar and (Path(res["dir"]) / f"SPY_{bar}.csv").exists()


def test_incomplete_current_bar_dropped_intraday(tmp_path):
    ib, clk, c, inst, d = dl(tmp_path, clk=FakeClock(1_792_000_000.0 + 5 * 3600 - 17 * 60))   # a time during a New York session
    res = run(d.download(inst, "5m", 2, True, 3))
    now = dt.datetime.fromtimestamp(clk.t, dt.timezone.utc)
    last = dt.datetime.fromisoformat(read_rows(Path(res["dir"]) / "SPY_5m.csv")[-1]["ts_open"])
    assert last + dt.timedelta(minutes=5) <= now


def test_spans_and_validation():
    assert H.parse_span(years=1, bar_size="1d") == 366
    assert H.parse_span(duration="30 D", bar_size="5m") == 30 and H.parse_span(duration="6 M", bar_size="1d") == 186
    for bad in (dict(years=0), dict(years=-1), dict(years=True), dict(years="2"), dict(duration="abc"), dict(duration="1 Q"), dict(), dict(years=999), dict(duration="99 Y")):
        with pytest.raises(ValueError):
            H.parse_span(bar_size="1d", **bad)
    with pytest.raises(ValueError):
        H.parse_span(years=1, bar_size="1s")
    with pytest.raises(ValueError):
        H.parse_span(years=6, bar_size="1m")        # app cap


# ------------------------------------------------------------------ frozen data protection
def test_frozen_processed_directory_is_never_writable(tmp_path):
    ib, clk, c, inst, d = dl(tmp_path)
    frozen = tmp_path / "data" / "processed"
    frozen.mkdir(parents=True)
    (frozen / "SPY_1d.csv").write_text("FROZEN\n")
    before = (frozen / "SPY_1d.csv").read_bytes()
    for bad in (frozen, tmp_path / "data", tmp_path / "data" / "processed_2026", tmp_path / "elsewhere" / "processed_20261014", tmp_path / "data" / "x" / "processed_20261014", tmp_path / "data" / "processed_20261014" / "sub"):
        with pytest.raises(ValueError, match="refused"):
            H.assert_safe_output(bad, tmp_path, "ibkr")
        with pytest.raises(ValueError, match="refused"):
            run(d.download(inst, "1d", 100, True, 3, out_dir=bad))
    assert (frozen / "SPY_1d.csv").read_bytes() == before and sorted(p.name for p in frozen.iterdir()) == ["SPY_1d.csv"]
    H.assert_safe_output(tmp_path / "data" / "processed_20261014", tmp_path, "ibkr")
    H.assert_safe_output(tmp_path / "data" / "fake_20261014", tmp_path, "fake")
    with pytest.raises(ValueError):
        H.assert_safe_output(tmp_path / "data" / "processed_20261014", tmp_path, "fake")     # simulated data never goes to a real snapshot name
    with pytest.raises(ValueError):
        H.assert_safe_output(tmp_path / "data" / "fake_20261014", tmp_path, "ibkr")
    assert H.snapshot_dir(tmp_path, "ibkr", dt.datetime(2026, 10, 14, tzinfo=dt.timezone.utc)).name == "processed_20261014"
    assert H.snapshot_dir(tmp_path, "fake", dt.datetime(2026, 10, 14, tzinfo=dt.timezone.utc)).name == "fake_20261014"


def test_real_snapshot_dir_name_for_real_source_and_gitignored():
    gi = (ROOT / ".gitignore").read_text()
    assert "data/processed_*/" in gi and "data/processed/" in gi and "data/fake_" in gi
    assert H.SNAP_RE.match("processed_20261014") and not H.SNAP_RE.match("processed")


def test_existing_files_are_never_overwritten(tmp_path):
    ib, clk, c, inst, d = dl(tmp_path)
    r1 = run(d.download(inst, "1d", 100, True, 3))
    p1 = Path(r1["dir"]) / "SPY_1d.csv"
    b1 = p1.read_bytes()
    clk.t += 3600
    r2 = run(d.download(inst, "1d", 100, True, 3))
    assert r2["file"] == "SPY_1d_r2.csv" and p1.read_bytes() == b1
    assert len(json.loads((Path(r1["dir"]) / "MANIFEST.json").read_text())) == 2


def test_real_frozen_dataset_untouched_by_the_test_suite():
    p = ROOT / "data" / "processed"
    names = sorted(x.name for x in p.glob("*")) if p.exists() else []
    assert all(n.endswith("_1d.csv") for n in names)     # only the original daily files; nothing added by downloads


# ------------------------------------------------------------------ manager (background thread, own loop)
def wait_for(fn, timeout=6.0):
    t = time.time() + timeout
    while time.time() < t:
        v = fn()
        if v:
            return v
        time.sleep(0.03)
    raise AssertionError("timed out")


@pytest.fixture()
def mgr(tmp_path):
    ib = FakeIB()
    m = IBKRManager(ib_factory=lambda: ib, stock_factory=FakeStock, source="fake", data_root=tmp_path, env={}, backoff=(0.05, 0.1), connect_timeout=2)
    m.fake_ib = ib
    yield m
    m.shutdown()


def test_manager_connect_status_quotes_account_disconnect(mgr, tmp_path):
    assert mgr.status()["state"] == "disconnected" and mgr.status()["fake"] is True and "SIMULATED" in mgr.status()["banner"]
    mgr.connect("paper_tws")
    wait_for(lambda: mgr.status()["state"] == "connected")
    st = mgr.status()
    assert st["execution"] == "none" and st["readonly"] is True and st["port"] == 7497 and st["server_time"]
    q = mgr.live_quotes([{"symbol": "SPY", "exchange": "ARCA", "conid": 756733, "currency": "USD"}])
    (tick,) = q.values()
    assert tick.data_type_label == "delayed" and tick.source == "fake"
    assert mgr.status()["feeds"]["quotes"]["as_of"] and mgr.status()["subscribed"] == 1
    snap = mgr.account_snapshot()
    assert snap["values"]["NetLiquidation"] == ("98765.43", "USD") and mgr.status()["feeds"]["account"]["as_of"]
    assert mgr.status()["account_masked"] == "F*****01" and "FAKE0001" not in json.dumps(mgr.status())
    mgr.disconnect()
    assert mgr.status()["state"] == "disconnected" and mgr.live_quotes([{"symbol": "SPY", "exchange": "ARCA", "conid": 756733}]) == {}


def test_manager_refuses_live_port_without_env_and_never_starts_thread(mgr):
    with pytest.raises(guard.GuardError):
        mgr.connect("live_tws_readonly")
    assert mgr.state == "disconnected" and mgr._loop is None


def test_manager_error_state_with_plain_language_and_backoff_then_recovers(mgr):
    mgr.fake_ib.refuse_connect = ConnectionRefusedError(111, "refused")
    mgr.connect("paper_tws")
    wait_for(lambda: mgr.status()["state"] == "error" and mgr.status()["attempts"] >= 2)
    e = mgr.status()["last_error"]
    assert e["title"].startswith("Could not connect") and "7497" in e["message"] and e["fix"] and e["at"]
    assert mgr.status()["next_retry_at"]
    mgr.fake_ib.refuse_connect = None
    wait_for(lambda: mgr.status()["state"] == "connected")
    assert mgr.status()["last_error"] is None


def test_manager_reconnects_after_drop_and_resubscribes(mgr):
    mgr.connect("paper_tws")
    wait_for(lambda: mgr.status()["state"] == "connected")
    inst = {"symbol": "SPY", "exchange": "ARCA", "conid": 756733}
    mgr.live_quotes([inst])
    mgr.fake_ib.drop()
    wait_for(lambda: mgr.status()["state"] in ("error", "connecting") or mgr.status()["attempts"] >= 2)
    wait_for(lambda: mgr.status()["state"] == "connected" and mgr.status()["attempts"] >= 2)
    wait_for(lambda: mgr.status()["subscribed"] == 1)
    assert mgr.status()["last_error"] is None


def test_manager_library_missing_is_friendly_and_server_side_safe(monkeypatch, tmp_path):
    from command_center.ibkr import manager as MM

    def boom():
        raise LibraryMissing("ib_async could not be imported (ModuleNotFoundError). Install it with: python -m pip install ib_async==2.1.0")
    monkeypatch.setattr(MM, "library_info", lambda: {"installed": False, "version": None, "message": str(LibraryMissing("ib_async could not be imported. Install it with: python -m pip install ib_async==2.1.0"))})
    m = IBKRManager(data_root=tmp_path, env={})
    st = m.status()
    assert st["library"]["installed"] is False and "pip install ib_async==2.1.0" in st["library"]["message"] and st["state"] == "disconnected"
    with pytest.raises(LibraryMissing):
        m.connect("paper_tws")
    assert m.status()["last_error"]["title"].startswith("ib_async is not installed")


def test_manager_history_job_end_to_end(mgr, tmp_path):
    mgr.connect("paper_tws")
    wait_for(lambda: mgr.status()["state"] == "connected")
    job = mgr.submit_history([{"symbol": "SPY", "exchange": "ARCA", "conid": 756733, "currency": "USD"}, {"symbol": "QQQ", "exchange": "NASDAQ", "conid": None, "currency": "USD"}], "1d", years=2)
    wait_for(lambda: mgr.list_jobs()[0]["status"] in ("done", "error"), 20)
    j = mgr.list_jobs()[0]
    assert j["status"] == "done" and len(j["outputs"]) == 2 and j["dir"].startswith("data/fake_") and j["progress"]["chunks_done"] >= 1
    assert all(o["rows"] > 400 and (tmp_path / o["path"]).exists() for o in j["outputs"])
    assert not (tmp_path / "data" / "processed").exists() and not list((tmp_path / "data").glob("processed_*"))
    assert mgr.status()["feeds"]["history"]["as_of"]
    with pytest.raises(ValueError):
        mgr.submit_history([], "1d", years=1)
    with pytest.raises(ValueError):
        mgr.submit_history([{"symbol": "SPY"}], "2h", years=1)


def test_manager_history_requires_connection(mgr):
    with pytest.raises(RuntimeError, match="not connected"):
        mgr.submit_history([{"symbol": "SPY"}], "1d", years=1)


def test_manager_history_error_is_reported_in_job(mgr):
    mgr.connect("paper_tws")
    wait_for(lambda: mgr.status()["state"] == "connected")
    mgr.fake_ib.history_errors = [(200, "No security definition")]
    mgr.submit_history([{"symbol": "SPY", "exchange": "ARCA", "conid": 756733}], "1d", years=1)
    wait_for(lambda: mgr.list_jobs()[0]["status"] == "error")
    j = mgr.list_jobs()[0]
    assert j["error"] and j["error_info"]["code"] == 200


def test_mask_account():
    assert mask_account("U1234567") == "U*****67" and mask_account(None) is None and "1234" not in mask_account("DU1234567")
