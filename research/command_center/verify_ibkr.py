#!/usr/bin/env python3
"""Browser verification of the IBKR (read-only) feature against the SIMULATED IB (fake mode). NOTHING here touches a real TWS/Gateway.

Own server in-process on port 8812, temp DB, temp data roots, SYNTHETIC market fixtures. Screenshots -> command_center/screenshots/after/ibkr-*.png
    python command_center/verify_ibkr.py
"""
from __future__ import annotations

import glob
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

os.environ.setdefault("CC_MARKET_PORT", "8812")
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
import verify_market as VM  # noqa: E402  (synthetic fixtures + helpers)

VM.PORT = int(os.environ["CC_MARKET_PORT"])
VM.BASE = f"http://127.0.0.1:{VM.PORT}"
BASE, TOKEN, check = VM.BASE, VM.TOKEN, VM.check


def main():
    from playwright.sync_api import sync_playwright
    from command_center.ibkr import manager as MM
    from command_center.ibkr.fake_ib import FakeIB, FakeStock
    tmp = Path(tempfile.mkdtemp(prefix="ibverify-"))
    shots = HERE / "screenshots" / "after"
    shots.mkdir(parents=True, exist_ok=True)
    data_root = tmp / "root"
    VM.make_synthetic(data_root)
    ib = FakeIB()
    ibroot = tmp / "ibroot"                     # downloads go here (never into the synthetic 'processed' folder)
    mgr = MM.IBKRManager(ib_factory=lambda: ib, stock_factory=FakeStock, source="fake", data_root=ibroot, env={}, backoff=(1, 2), connect_timeout=3)
    MM.set_manager(mgr)
    srv, api_market = VM.start_server(data_root, tmp / "ev" / "e.db")
    exe = next(iter(sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))), None)
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=exe, args=["--no-sandbox"]) if exe else p.chromium.launch(args=["--no-sandbox"])
            ctx = b.new_context(viewport={"width": 1440, "height": 900}, bypass_csp=True)  # the server now sends a CSP; wait_for_function needs eval
            ctx.add_init_script(f"try{{localStorage.setItem('cc.token','{TOKEN}')}}catch(e){{}}")
            page = ctx.new_page()
            page.on("console", lambda m: VM.console_errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: VM.console_errors.append(f"pageerror: {e}"))
            run(page, ib, mgr, shots, ibroot, data_root)
            b.close()
    finally:
        mgr.shutdown()
        MM.set_manager(None)
        srv.stopping = True
        srv.shutdown()
        srv.server_close()
        shutil.rmtree(tmp, ignore_errors=True)
    errs = [e for e in VM.console_errors if "favicon" not in e and "refused" not in e.lower() and "400" not in e and "409" not in e]
    check("no unexpected console errors", not errs, "; ".join(errs[:3]))
    bad = [r for r in VM.results if not r[1]]
    print(f"\nverify_ibkr: {len(VM.results) - len(bad)}/{len(VM.results)} checks passed" + ("" if not bad else f"; FAILED: {[r[0] for r in bad]}"))
    sys.exit(1 if bad else 0)


def run(page, ib, mgr, shots, ibroot, data_root):
    goto = VM.goto
    chip = lambda: page.inner_text("#chip-broker")  # noqa: E731

    @VM.journey("disconnected state is honest, simulated banner unmissable")
    def j1():
        goto(page, "data", ready="[data-testid=ibkr-panel] [data-testid=ibkr-connect]")
        page.wait_for_function("document.querySelector('#chip-broker')?.textContent.includes('SIMULATED')")
        check("simulated banner + panel banner", page.locator("#sim-banner:visible").count() == 1 and page.locator("[data-testid=fake-banner]").count() == 1)
        check("panel says not yet verified + execution none + read-only", all(s in page.inner_text("[data-testid=ibkr-panel]") for s in ["Not yet verified against a real TWS/Gateway", "Execution: none", "Read-only"]))
        check("chip shows disconnected simulated state", "SIMULATED IBKR" in chip() and "disconnected" in chip())
        check("disconnect disabled when not connected", page.is_disabled("[data-testid=ibkr-disconnect]"))
        page.screenshot(path=str(shots / "ibkr-data-disconnected-1440x900.png"))
    j1()

    @VM.journey("connect, chip + status")
    def j2():
        page.click("[data-testid=ibkr-connect]")
        page.wait_for_function("document.querySelector('[data-testid=ibkr-status]')?.textContent.includes('connected')", timeout=15000)
        page.wait_for_function("document.querySelector('#chip-broker')?.textContent.includes('SIMULATED IBKR (test fixture)')", timeout=10000)
        check("connected via read-only flag + loopback", ib.connect_args == {"host": "127.0.0.1", "port": 7497, "clientId": 9101, "readonly": True}, str(ib.connect_args))
        t = page.inner_text("[data-testid=ibkr-status]")
        check("status shows execution none, read-only, feeds", "Execution: none" in t and "Read-only" in t and "Quotes feed" in t and "Account feed" in t)
    j2()

    @VM.journey("command center: simulated account data, delayed quotes labelled")
    def j3():
        goto(page, "command_center")
        page.wait_for_function("document.querySelector('[data-testid=broker-strip]')?.textContent.includes('SIMULATED')", timeout=15000)
        vals = page.eval_on_selector_all(".mk-metric", "els => Object.fromEntries(els.map(e => [e.dataset.metric, e.querySelector('.mk-m-value').textContent.trim()]))")
        check("equity shows the broker value with currency", vals["equity"].startswith("$98,765.43"), str(vals))
        check("exposure zero is shown as 0.00% (reported zero) not a dash", vals["exposure"].startswith("0.00%"), vals["exposure"])
        check("daily P&L unknown stays a dash", vals["daily_pnl"].startswith("—") and vals["risk_usage"].startswith("—"))
        asof = page.inner_text(".mk-metric[data-metric=equity] .mk-m-asof")
        check("as-of time shown for the account value", "—" not in asof.replace("as of", ""), asof)
        page.wait_for_selector(".mk-wl-row .mk-dot.live, .mk-wl-row[title*='Delayed']", timeout=15000)
        check("watchlist rows labelled delayed (tick type)", page.locator(".mk-wl-row[title*='Delayed']").count() >= 1)
        page.wait_for_selector("[data-testid=live-price]:visible", timeout=15000)
        check("chart header shows labelled live/delayed price", "Delayed" in page.inner_text("[data-testid=live-price]"), page.inner_text("[data-testid=live-price]"))
        check("no order controls", page.locator("button:has-text('Buy'), button:has-text('Sell'), button:has-text('Place')").count() == 0)
        page.mouse.move(2, 2)
        page.screenshot(path=str(shots / "ibkr-command-center-connected-1440x900.png"))
    j3()

    @VM.journey("portfolio shows read-only account data; orders unknown not none")
    def j4():
        goto(page, "portfolio", ready=".mk-pf .mk-entry")
        page.wait_for_selector("#pf-positions .empty")
        check("positions: broker reported none", "reported" in page.inner_text("#pf-positions") and "no stock positions" in page.inner_text("#pf-positions").lower())
        check("orders and fills: unknown because of Read-Only API", "unknown" in page.inner_text("#pf-orders").lower() and "Read-Only API" in page.inner_text("#pf-orders"))
        check("order entry still absent", "Order entry unavailable" in page.inner_text(".mk-entry") and page.locator(".mk-entry button").count() == 0)
        check("account masked, simulated labelled", "DU*****01" in page.inner_text("[data-testid=broker-strip]") and "SIMULATED" in page.inner_text("[data-testid=broker-strip]"))
        page.screenshot(path=str(shots / "ibkr-portfolio-connected-1440x900.png"))
    j4()

    @VM.journey("stocks: live labelled chips")
    def j5():
        goto(page, "stocks")
        page.wait_for_function("document.querySelector('.mk-chips')?.textContent.includes('Quote: Delayed')", timeout=15000)
        check("quote chip says Delayed", True)
        page.mouse.move(2, 2)
        page.screenshot(path=str(shots / "ibkr-stocks-live-1440x900.png"))
    j5()

    @VM.journey("download history with real progress")
    def j6():
        goto(page, "data", ready="[data-testid=ibkr-download]")
        page.wait_for_function("!document.querySelector('[data-testid=ibkr-download]').disabled", timeout=10000)
        page.fill("#ib-syms", "SYNA, SYNB")
        page.select_option("#ib-bar", "5m")
        page.fill("#ib-years", "0.1")
        page.click("[data-testid=ibkr-download]")
        page.wait_for_selector("[data-testid=ibkr-jobs] .ib-job", timeout=10000)
        seen = set()
        for _ in range(80):
            t = page.inner_text("[data-testid=ibkr-jobs]")
            st = page.get_attribute("[data-testid=ibkr-jobs] .ib-job", "data-status")
            seen.add(st)
            if st in ("done", "error"):
                break
            page.wait_for_timeout(150)
        check("job finished done", st == "done", f"{st}: {page.inner_text('[data-testid=ibkr-jobs]')[:200]}")
        t = page.inner_text("[data-testid=ibkr-jobs]")
        check("outputs listed with path, rows and sha256 under data/fake_", "data/fake_" in t and "sha256" in t and "rows" in t, t.replace("\n", " ")[:200])
        check("manifest mention + frozen data note", "MANIFEST.json" in t and "never touched or overwritten" in page.inner_text("[data-testid=ibkr-panel]"))
        files = sorted(p.name for d in (ibroot / "data").glob("fake_*") for p in d.glob("*"))
        check("files really on disk (CSV + manifest)", any(f.endswith("_5m.csv") for f in files) and "MANIFEST.json" in files, str(files))
        check("no data/processed written by the download", not (ibroot / "data" / "processed").exists() and not list((ibroot / "data").glob("processed_*")))
        page.mouse.move(2, 2)
        page.screenshot(path=str(shots / "ibkr-data-connected-download-1440x900.png"))
    j6()

    @VM.journey("disconnect, then error display with plain-language fix")
    def j7():
        page.click("[data-testid=ibkr-disconnect]")
        page.wait_for_function("document.querySelector('#chip-broker')?.textContent.includes('disconnected')", timeout=10000)
        check("chip back to disconnected", "disconnected" in chip())
        ib.refuse_connect = ConnectionRefusedError(111, "refused")
        page.click("[data-testid=ibkr-connect]")
        page.wait_for_selector("[data-testid=ibkr-error]", timeout=15000)
        t = page.inner_text("[data-testid=ibkr-error]")
        check("error shows title, message and 'What to do'", "Could not connect" in t and "What to do" in t and "7497" in t and "Trusted IPs" in t, t.replace("\n", " ")[:200])
        check("chip shows error", "error" in chip())
        page.mouse.move(2, 2)
        page.screenshot(path=str(shots / "ibkr-data-error-1440x900.png"))
        ib.refuse_connect = None
        page.click("[data-testid=ibkr-disconnect]")
    j7()

    for w, h in ((1920, 1080), (1280, 720), (900, 700)):
        page.set_viewport_size({"width": w, "height": h})
        goto(page, "data", ready="[data-testid=ibkr-panel] [data-testid=ibkr-connect]")
        page.mouse.move(2, 2)
        page.wait_for_timeout(400)
        page.screenshot(path=str(shots / f"ibkr-data-{w}x{h}.png"))
    page.set_viewport_size({"width": 1440, "height": 900})


if __name__ == "__main__":
    main()
