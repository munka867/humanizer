#!/usr/bin/env python3
"""Browser verification for the MARKET workspaces (Command Center, Stocks & Charts, Portfolio & Orders).

Runs its OWN server in-process on port 8811 with a TEMP events DB and a TEMP data root. Default data = SYNTHETIC fixtures generated here
(deterministic random walk, tickers SYNA/SYNB/DUP; software test only, safe to screenshot/commit). `--real` serves the local
IBKR connector files instead (data/processed/*.csv, git-ignored); real screenshots then go to the scratch dir, never into the repo.

  python command_center/verify_market.py            # synthetic fixtures, screenshots -> command_center/screenshots/after/market-*.png
  python command_center/verify_market.py --real     # real local data (structure + screenshots outside the repo)
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import random
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
PORT = int(os.environ.get("CC_MARKET_PORT", 8811))
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "verify-token"
results: list = []
console_errors: list = []

SYN = [("SYNA", 910001, "ARCA", 410.0), ("SYNB", 910002, "NASDAQ", 120.0), ("DUP", 910003, "ARCA", 55.0), ("DUP", 910004, "NYSE", 87.0)]


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f" - {detail}" if detail else ""), flush=True)


def make_synthetic(root: Path):
    (root / "data/raw/ibkr_connector").mkdir(parents=True)
    (root / "data/processed").mkdir(parents=True)
    man = []
    for n, (sym, conid, ex, start) in enumerate(SYN):
        rnd = random.Random(1000 + n)
        d, px, lines = dt.date(2024, 10, 1), start, ["ts_open,open,high,low,close,volume,contract"]
        while d <= dt.date(2026, 10, 7):
            if d.weekday() < 5:
                o = px
                c = max(1.0, o * (1 + rnd.gauss(0.0004, 0.011)))
                hi, lo = max(o, c) * (1 + abs(rnd.gauss(0, 0.004))), min(o, c) * (1 - abs(rnd.gauss(0, 0.004)))
                lines.append(f"{d} 13:30:00+00:00,{o:.2f},{hi:.2f},{lo:.2f},{c:.2f},{float(int(rnd.uniform(0.6, 1.6) * 4_000_000))},{sym}")
                px = c
            d += dt.timedelta(days=1)
        f = root / f"data/processed/SYNTHETIC_{sym}_{ex}_1d.csv"
        f.write_text("\n".join(lines) + "\n")
        man.append({"symbol": sym, "contract_id": conid, "exchange": ex, "n_bars": len(lines) - 1, "first": "2024-10-01 13:30:00+00:00", "last": "2026-10-07 13:30:00+00:00",
                    "retrieved_on": "2026-10-08", "source_tag": "SYNTHETIC", "delayed_seconds": 900, "adjusted_for_dividends": False,
                    "processed_file": str(f.relative_to(root))})
    (root / "data/raw/ibkr_connector/MANIFEST.json").write_text(json.dumps(man))


def start_server(data_root: Path, db: Path):
    from command_center import api_market
    from command_center.server import make_server
    api_market.DATA_ROOT = data_root
    srv = make_server(db, port=PORT, token=TOKEN)
    srv.manifest_path = data_root / "data/raw/ibkr_connector/MANIFEST.json"
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, api_market


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true")
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright
    tmp = Path(tempfile.mkdtemp(prefix="mkverify-"))
    scratch = Path(os.environ.get("CC_SCRATCH", tmp))
    shots = scratch / "shots" if a.real else HERE / "screenshots" / "after"
    shots.mkdir(parents=True, exist_ok=True)
    if a.real:
        data_root = ROOT
        print("DATA: real local IBKR connector files (screenshots stay outside the repo)")
    else:
        data_root = tmp / "root"
        make_synthetic(data_root)
        print("DATA: SYNTHETIC fixtures (SYNA, SYNB, DUP x2)")
    srv, api_market = start_server(data_root, tmp / "ev" / "e.db")
    first = [i for i in api_market.instruments() if i["data_present"]]
    sym0, sym1 = first[0]["symbol"], first[1]["symbol"]
    exe = next(iter(sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))), None)
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=exe, args=["--no-sandbox"]) if exe else p.chromium.launch(args=["--no-sandbox"])
            ctx = b.new_context(viewport={"width": 1440, "height": 900})
            ctx.add_init_script(f"try{{localStorage.setItem('cc.token','{TOKEN}')}}catch(e){{}}")
            page = ctx.new_page()
            page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: console_errors.append(f"pageerror: {e}"))
            run(page, sym0, sym1, shots, api_market, a.real)
            b.close()
    finally:
        srv.stopping = True
        srv.shutdown()
        srv.server_close()
        shutil.rmtree(tmp, ignore_errors=True)
    check("no console errors", not [e for e in console_errors if "favicon" not in e], "; ".join(console_errors[:3]))
    bad = [r for r in results if not r[1]]
    print(f"\nverify_market: {len(results) - len(bad)}/{len(results)} checks passed" + ("" if not bad else f"; FAILED: {[r[0] for r in bad]}"))
    sys.exit(1 if bad else 0)


_nav = 0


def goto(page, ws, q="", ready=".mk-canvas[data-ready='1']"):
    global _nav
    _nav += 1
    page.goto(f"{BASE}/?n={_nav}#/{ws}{q}")  # unique query forces a full page load (hash-only navigation would not remount)
    if ready:
        page.wait_for_selector(ready, timeout=15000)
    page.wait_for_timeout(300)


def journey(name):
    def deco(fn):
        def run_(*a, **k):
            try:
                fn(*a, **k)
            except Exception as e:  # noqa: BLE001
                import traceback
                ln = [f for f in traceback.extract_tb(e.__traceback__) if f.filename == __file__]
                check(name, False, f"{type(e).__name__} at line {ln[-1].lineno if ln else '?'}: {str(e)[:200]}")
        return run_
    return deco


def run(page, sym0, sym1, shots, api_market, real):
    # ---------------------------------------------------------------- Command Center
    @journey("command center renders: summary, watchlist, chart, lower tabs")
    def cc():
        goto(page, "command_center")
        vals = page.eval_on_selector_all(".mk-metric", "els => els.map(e => [e.dataset.metric, e.dataset.state, e.querySelector('.mk-m-value').textContent.trim(), e.querySelector('.mk-m-reason').textContent])")
        check("five summary metrics, all em dash (unavailable), none zero", len(vals) == 5 and all(v[1] == "unavailable" and v[2].startswith("—") for v in vals), str([v[0] for v in vals]))
        check("each metric has a one-line reason", all(v[3].strip() for v in vals))
        check("broker strip says research mode / not connected", "no broker" in page.inner_text("[data-testid=broker-strip]").lower())
        check("watchlist rows present (conId+exchange keyed)", page.locator(".mk-wl-row").count() >= 3 and page.locator(".mk-wl-row[data-key]").count() >= 3)
        check("chart canvas rendered with bars", int(page.get_attribute(".mk-canvas", "data-bars")) > 100)
        check("attribution link/logo present", page.locator(".mk-canvas a[href*='tradingview']").count() >= 1)
        m = page.evaluate("""() => { const r = s => document.querySelector(s).getBoundingClientRect(); return {main: r('.mk-main'), lower: r('.mk-lower'), chart: r('.mk-chart'), metrics: r('.mk-metrics'), vh: innerHeight, sc: document.getElementById('workspace').scrollHeight, ch: document.getElementById('workspace').clientHeight}; }""")
        check("1440x900: metrics + 3 columns + lower fit without workspace scroll", m["sc"] <= m["ch"] + 1, f"scrollH {m['sc']} clientH {m['ch']}")
        check("chart gets more area than the metric row", m["chart"]["height"] * m["chart"]["width"] > 4 * m["metrics"]["height"] * m["metrics"]["width"] / 5)
        page.click("button[role=tab]:has-text('Working orders')")
        check("working orders tab shows honest empty state", "unavailable" in page.inner_text(".mk-lower").lower() and page.locator(".mk-lower >> text=Connect broker").count() >= 1)
        check("agent team summary has active/queued/idle + link", page.locator("[data-stat]").count() == 3 and page.locator(".mk-agents a[href='#/agents']").count() == 1)
    cc()

    @journey("select stock from watchlist")
    def sel():
        goto(page, "command_center")
        page.click(f".mk-wl-row[data-symbol='{sym1}']")
        page.wait_for_function(f"document.querySelector('.mk-sym')?.textContent === '{sym1}'")
        page.wait_for_selector(".mk-canvas[data-ready='1']")
        check("select stock updates chart title + selection", page.get_attribute(f".mk-wl-row[data-symbol='{sym1}']", "aria-selected") == "true")
    sel()

    @journey("panel resize persists")
    def rsz():
        goto(page, "command_center")
        h = page.locator("[aria-label^='Resize watchlist panel']").first
        w0 = page.evaluate("document.querySelector('.mk-side-left').getBoundingClientRect().width")
        h.focus(); page.keyboard.press("Shift+ArrowRight"); page.keyboard.press("Shift+ArrowRight")
        w1 = page.evaluate("document.querySelector('.mk-side-left').getBoundingClientRect().width")
        page.wait_for_timeout(1500)
        page.evaluate("window.__cc_dummy = 1")
        page.reload(); page.wait_for_selector(".mk-canvas[data-ready='1']")
        w2 = page.evaluate("document.querySelector('.mk-side-left').getBoundingClientRect().width")
        check("watchlist panel width changes with keyboard", w1 > w0 + 40, f"{w0}->{w1}")
        check("panel width persisted across reload", abs(w2 - w1) < 2, f"{w1} vs {w2}")
        page.locator("[aria-label^='Resize activity panel']").first.focus(); page.keyboard.press("Enter")
        check("activity panel collapses (Enter on handle)", page.evaluate("document.querySelector('.mk-side-right').getBoundingClientRect().width") < 4)
        page.keyboard.press("Enter")
        h.focus(); page.keyboard.press("Home")
    rsz()

    # ---------------------------------------------------------------- Stocks & Charts
    @journey("timeframe change 1D -> 1W -> 1M + range presets")
    def tf():
        goto(page, "stocks")
        n1 = int(page.get_attribute(".mk-canvas", "data-bars"))
        page.click("button[data-interval='1W']"); page.wait_for_selector(".mk-canvas[data-interval='1W'][data-ready='1']")
        nw = int(page.get_attribute(".mk-canvas", "data-bars"))
        page.click("button[data-interval='1M']"); page.wait_for_selector(".mk-canvas[data-interval='1M'][data-ready='1']")
        nm = int(page.get_attribute(".mk-canvas", "data-bars"))
        check("weekly has fewer bars than daily, monthly fewer than weekly", n1 > nw > nm > 3, f"{n1}/{nw}/{nm}")
        check("intraday button disabled with explanation", page.locator("button:disabled:has-text('Intraday')").count() == 1 and "intraday" in (page.get_attribute(".mk-disabled-wrap", "aria-label") or "").lower())
        check("VWAP disabled (unavailable on daily bars)", page.locator("button:disabled:has-text('VWAP')").count() == 1)
        chips = page.inner_text(".mk-chips")
        check("provenance chips: source, as-of, tz, unadjusted, session", all(s in chips for s in ["IBKR connector", "as of", "America/New_York", "Unadjusted for dividends", "Regular session"]), chips.replace("\n", " | ")[:200])
        check("partial last bar flagged on 1M (period in progress)", "Last bar partial" in chips)
        page.click("button[data-interval='1D']"); page.wait_for_selector(".mk-canvas[data-interval='1D'][data-ready='1']")
        page.click("button[data-range='3M']"); page.click("button[data-overlay='sma20']")
        check("overlay + range toggles reflect pressed state", page.get_attribute("button[data-overlay='sma20']", "aria-pressed") == "true" and page.get_attribute("button[data-range='3M']", "aria-pressed") == "true")
        page.click("button[data-overlay='sma20']"); page.click("button[data-range='1Y']")
        # keyboard
        r0 = page.evaluate("JSON.stringify(document.querySelector('.mk-canvas').__market.chart.timeScale().getVisibleLogicalRange())")
        page.focus(".mk-canvas"); page.keyboard.press("ArrowLeft"); page.keyboard.press("+"); page.wait_for_timeout(400)
        r1 = page.evaluate("JSON.stringify(document.querySelector('.mk-canvas').__market.chart.timeScale().getVisibleLogicalRange())")
        check("keyboard pan/zoom changes the visible range", r0 != r1)
    tf()

    @journey("crosshair readout")
    def cross():
        goto(page, "stocks")
        bx = page.locator(".mk-canvas").bounding_box()
        check("readout shows latest bar before hover", "(latest)" in page.inner_text("[data-testid=readout]"))
        page.mouse.move(bx["x"] + bx["width"] * 0.5, bx["y"] + bx["height"] * 0.4); page.mouse.move(bx["x"] + bx["width"] * 0.52, bx["y"] + bx["height"] * 0.4)
        page.wait_for_function("document.querySelector('[data-testid=readout]').dataset.hover === '1'", timeout=5000)
        t = page.inner_text("[data-testid=readout]")
        check("crosshair readout shows O H L C Vol Chg", all(k in t for k in ["O", "H", "L", "C", "Vol", "Chg"]) and "%" in t, t.replace("\n", " ")[:120])
    cross()

    @journey("watchlist create/add/reorder/remove persisted across reload")
    def wl():
        goto(page, "stocks")
        page.click("button:has-text('New list')"); page.fill("input[aria-label='Watchlist name']", "Verify list"); page.click("form.mk-nameform button[type=submit]")
        page.wait_for_function("[...document.querySelectorAll('.mk-wm select option')].some(o => o.textContent.startsWith('Verify list'))")
        for s in (sym0, sym1):
            page.fill("input[aria-label='Search instruments to add']", s)
            page.locator(f".mk-res:has-text('{s}') button:has-text('Add')").first.click()
            page.wait_for_function(f"[...document.querySelectorAll('.mk-wm tr.tbl-row')].some(r => r.textContent.includes('{s}'))")
        order = lambda: page.eval_on_selector_all(".mk-wm tr.tbl-row", "rs => rs.map(r => r.dataset.key)")  # noqa: E731
        o1 = order()
        check("two items added in order", len(o1) == 2, str(o1))
        page.wait_for_timeout(700)  # let the quote refresh finish re-rendering the table before clicking
        page.click(f"button[aria-label^='Move up {sym1}']")
        page.wait_for_function("(o) => document.querySelector('.mk-wm tr.tbl-row')?.dataset.key !== o", arg=o1[0])
        o2 = order()
        check("move up reorders", o2 == [o1[1], o1[0]], str(o2))
        page.wait_for_timeout(600)
        page.reload(); page.wait_for_selector(".mk-canvas[data-ready='1']")
        page.select_option(".mk-wm select[aria-label='Watchlist']", label=None, index=1) if page.locator(".mk-wm select option").count() > 1 else None
        page.wait_for_selector(".mk-wm tr.tbl-row")
        names = page.eval_on_selector_all(".mk-wm select option", "os => os.map(o => o.textContent)")
        check("list persisted after reload (server side)", any(n.startswith("Verify list") for n in names), str(names))
        sel = page.eval_on_selector(".mk-wm select[aria-label='Watchlist']", "s => s.selectedOptions[0].textContent")
        if not sel.startswith("Verify list"):
            page.select_option(".mk-wm select[aria-label='Watchlist']", label=[n for n in names if n.startswith("Verify list")][0]); page.wait_for_timeout(300)
        check("order persisted after reload", order() == o2, f"{order()} vs {o2}")
        page.click(f"button[aria-label^='Remove {sym1}']")
        page.wait_for_function("document.querySelectorAll('.mk-wm tr.tbl-row').length === 1")
        page.reload(); page.wait_for_selector(".mk-wm select")
        names = page.eval_on_selector_all(".mk-wm select option", "os => os.map(o => o.textContent)")
        page.select_option(".mk-wm select[aria-label='Watchlist']", label=[n for n in names if n.startswith("Verify list")][0]); page.wait_for_timeout(400)
        check("remove persisted after reload", len(order()) == 1)
        page.click("button:has-text('Delete')"); page.click(".modal button:has-text('Delete list')")
        page.wait_for_function("![...document.querySelectorAll('.mk-wm select option')].some(o => o.textContent.startsWith('Verify list'))")
    wl()

    @journey("duplicate tickers are distinguished by conId + exchange")
    def dup():
        goto(page, "stocks")
        page.fill("input[aria-label='Search instruments to add']", "DUP")
        page.wait_for_selector(".mk-res")
        t = page.inner_text(".mk-results")
        check("search lists both DUP listings with exchange and conId", "ARCA" in t and "NYSE" in t and "910003" in t and "910004" in t, t.replace("\n", " ")[:160]) if not page.locator(".mk-res:has-text('SYN')").count() else None
    if not real:
        dup()

    @journey("marker evidence drawer (SYNTHETIC marker fixture)")
    def marker():
        def fulfil(route):
            url = route.request.url
            bars = api_market._load(api_market.resolve(sym0))
            day = bars[-20]["date"].isoformat()
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"symbol": sym0, "markers": [
                {"id": "SYNTHETIC-1", "time": day, "state": "filled", "side": "buy", "price": bars[-20]["close"], "qty": 10, "strategy": "SYNTHETIC test strategy", "order_id": "SYN-ORD-1", "trade_id": "SYN-TRD-1", "source": "SYNTHETIC fixture in verify_market.py", "synthetic": True, "evidence": {"note": "SYNTHETIC marker used only to test the drawer", "rule": "none"}},
                {"id": "SYNTHETIC-2", "time": bars[-8]["date"].isoformat(), "state": "proposed", "side": "sell", "strategy": "SYNTHETIC test strategy", "source": "SYNTHETIC", "synthetic": True}]}))
        page.route("**/api/markers*", fulfil)
        goto(page, "stocks")
        check("legend distinguishes proposed/submitted/filled", all(s in page.inner_text(".mk-legend") for s in ["Proposed", "Submitted", "Filled"]))
        page.click(".mk-mbtn[data-marker-id='SYNTHETIC-1']")
        page.wait_for_selector(".drawer")
        t = page.inner_text(".drawer")
        check("drawer shows evidence + SYNTHETIC label", "SYNTHETIC fixture" in t and "SYN-ORD-1" in t and "Filled" in t, t.replace("\n", " ")[:100])
        page.keyboard.press("Escape"); page.wait_for_selector(".drawer", state="detached", timeout=3000)
        # click the marker on the chart itself
        page.click("button[data-range='3M']"); page.wait_for_timeout(600)
        pt = page.evaluate("""() => { const m = document.querySelector('.mk-canvas').__market; const rows = m.candles.data(); const t = rows[rows.length - 20].time;
            const x = m.chart.timeScale().timeToCoordinate(t); const y = m.candles.priceToCoordinate(rows[rows.length - 20].low); const r = document.querySelector('.mk-canvas').getBoundingClientRect(); return [r.left + x, r.top + y + 8]; }""")
        page.mouse.move(pt[0], pt[1]); page.mouse.click(pt[0], pt[1])
        try:
            page.wait_for_selector(".drawer", timeout=3000); ok = True
        except Exception:
            ok = False
        check("clicking the marker on the chart opens the drawer", ok)
        if ok:
            page.keyboard.press("Escape")
        page.unroute("**/api/markers*")
    marker()

    @journey("stale quote state is honest")
    def stale():
        real_now = api_market._now
        api_market._now = lambda: dt.datetime(2026, 12, 1, 15, 0, tzinfo=dt.timezone.utc)
        try:
            goto(page, "stocks")
            page.wait_for_selector(".mk-wm tr.tbl-row")
            page.wait_for_timeout(500)
            t = page.inner_text(".mk-wm") + page.inner_text(".mk-chips")
            check("stale badge shown with as-of date", "Stale" in t and "as of" in t.lower())
            check("stale reason available as tooltip", page.locator(".mk-chips [title*='calendar days']").count() >= 1)
        finally:
            api_market._now = real_now
    stale()

    # ---------------------------------------------------------------- Portfolio
    @journey("portfolio: empty tables with reasons, no order entry")
    def pf():
        goto(page, "portfolio", ready=".mk-pf .mk-entry")
        page.wait_for_selector("#pf-positions .empty")
        t = page.inner_text("#pf-positions")
        check("positions table empty state states reason", "unavailable" in t.lower() and "broker adapter" in t.lower(), t.replace("\n", " ")[:120])
        check("orders + fills empty too", page.locator("#pf-orders .empty").count() == 1 and page.locator("#pf-fills .empty").count() == 1)
        check("order entry card unavailable with prerequisites, no submit control", "Order entry unavailable" in page.inner_text(".mk-entry") and page.locator(".mk-entry button, .mk-entry input, .mk-entry form").count() == 0 and page.locator("button:has-text('Submit'), button:has-text('Place order'), button:has-text('Buy'), button:has-text('Sell')").count() == 0)
        check("metrics never zero", all(not v.strip().startswith(("$0", "0")) for v in page.eval_on_selector_all(".mk-m-value", "e => e.map(x => x.textContent)")))
        check("marks/FX/deposits note present", "Deposits and withdrawals" in page.inner_text(".mk-notes") and "FX" in page.inner_text(".mk-notes"))
    pf()

    @journey("portfolio table renders SYNTHETIC rows: status badges, expansion, CSV")
    def pfrows():
        rows = {"broker": {"state": "not_connected", "reason": "SYNTHETIC test"}, "positions": [{"id": "p1", "symbol": "SYNA", "exchange": "ARCA", "conid": 910001, "shares": 10, "avg_cost": 100, "mark": 101, "market_value": 1010, "unrealized": 10, "realized": None, "daily_pnl": None, "weight": None, "ownership": "manual", "currency": "USD"}],
                "working_orders": [{"id": f"o{i}", "symbol": "SYNA", "side": "buy", "qty": 10, "filled": 0, "remaining": 10, "type": "LMT", "limit": 99, "tif": "DAY", "status": s} for i, s in enumerate(["working", "partial", "pending_cancel", "rejected", "uncertain", "acknowledged"])],
                "fills": [], "metrics": {k: {"value": None, "reason": "SYNTHETIC", "currency": None, "as_of": None} for k in ("equity", "daily_pnl", "buying_power", "exposure", "risk_usage")}, "unavailable_reason": "SYNTHETIC", "note": "SYNTHETIC rows"}
        page.route("**/api/portfolio", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(rows)))
        goto(page, "portfolio", ready=".mk-pf .mk-entry")
        page.wait_for_selector("#pf-orders .badge")
        t = page.inner_text("#pf-orders")
        check("order statuses are distinct badges", all(s in t for s in ["Working", "Partially filled", "Pending cancel", "Rejected", "Uncertain", "Acknowledged"]))
        page.click("#pf-positions tr.tbl-row"); check("row expansion shows detail", page.locator("#pf-positions .tbl-expand").count() == 1)
        check("CSV export control present", page.locator("#pf-positions button:has-text('CSV')").count() == 1)
        page.unroute("**/api/portfolio")
    pfrows()

    # ---------------------------------------------------------------- screenshots
    for (w, h) in [(1920, 1080), (1440, 900), (1280, 720), (900, 700)]:
        page.set_viewport_size({"width": w, "height": h})
        for ws, ready in (("command_center", ".mk-canvas[data-ready='1']"), ("stocks", ".mk-canvas[data-ready='1']"), ("portfolio", ".mk-pf .mk-entry")):
            goto(page, ws, ready=ready)
            page.mouse.move(2, 2)
            page.wait_for_timeout(500)
            page.screenshot(path=str(shots / f"market-{ws}-{w}x{h}.png"))
    page.set_viewport_size({"width": 1440, "height": 900})


if __name__ == "__main__":
    main()
