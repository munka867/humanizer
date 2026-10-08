#!/usr/bin/env python3
"""Playwright journeys for the RESEARCH workspaces (research, backtest, data, settings). TEMP db, port 8813, real local data.
Runs the real pre-registered train experiment end-to-end; import uses the SYNTHETIC fixtures. Screenshots -> screenshots/after/research-*.png"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SHOTS = HERE / "screenshots" / "after"
PORT = int(os.environ.get("CC_RESEARCH_PORT", 8813))
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "research-token"
FIX = ROOT / "tests" / "fixtures"
results = []
VIEWS = [(1920, 1080), (1440, 900), (1280, 720), (900, 700)]


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f" - {detail}" if detail else ""), flush=True)


def start_server(db):
    env = dict(os.environ, CC_DB=db, CC_TOKEN=TOKEN, CC_PORT=str(PORT), CC_TEST_ACCESS_LOG=os.path.join(os.path.dirname(db), "test_access_log.jsonl"))
    p = subprocess.Popen([sys.executable, "-m", "command_center.server"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            urllib.request.urlopen(BASE + "/api/health", timeout=1).read()
            return p
        except Exception:
            time.sleep(0.2)
    p.kill()
    raise SystemExit("server did not start")


def shots(page, name):
    for w, h in VIEWS:
        page.set_viewport_size({"width": w, "height": h})
        page.evaluate("document.querySelector('.workspace').scrollTop = 0")
        page.wait_for_timeout(350)
        page.screenshot(path=str(SHOTS / f"research-{name}-{w}x{h}.png"))
    page.set_viewport_size({"width": 1440, "height": 900})


def scroll_shots(page, name, n=3, step=800):
    page.set_viewport_size({"width": 1440, "height": 900})
    for i in range(1, n + 1):
        page.evaluate(f"document.querySelector('.workspace').scrollTop = {i * step}")
        page.wait_for_timeout(400)
        page.screenshot(path=str(SHOTS / f"research-{name}-scroll{i}.png"))
    page.evaluate("document.querySelector('.workspace').scrollTop = 0")


def main():
    from playwright.sync_api import sync_playwright
    SHOTS.mkdir(parents=True, exist_ok=True)
    exe = next(iter(sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))), None) or "/opt/pw-browsers/chromium/chrome"
    db = os.path.join(tempfile.mkdtemp(), "research.sqlite3")
    # committed-run series (reproduced and VERIFIED equal to results/daily/*_summary.json)
    sys.path.insert(0, str(ROOT))
    from command_center import repro_committed
    rep = repro_committed.reproduce(ROOT)
    check("committed runs reproduce exactly (train, validation)", rep["all_equal"], str({k: v["equal"] for k, v in rep["segments"].items()}))
    srv = start_server(db)
    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch(executable_path=exe, args=["--no-sandbox"])
            bc = br.new_context(bypass_csp=True, viewport={"width": 1440, "height": 900})
            bc.add_init_script(f"localStorage.setItem('cc.token','{TOKEN}')")
            page = bc.new_page()
            errors, ext = [], []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "stale" not in m.text.lower() and "status of 403" not in m.text and "status of 422" not in m.text else None)
            page.on("request", lambda r: ext.append(r.url) if not r.url.startswith(BASE) and not r.url.startswith("data:") else None)
            journeys(page, errors)
            check("no external network requests", not ext, str(ext[:3]))
            check("no console/page errors", not errors, str(errors[:3]))
            br.close()
    finally:
        srv.terminate()
    bad = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(bad)}/{len(results)} checks passed")
    return 1 if bad else 0


def wait(page, sel, t=15000):
    page.wait_for_selector(sel, timeout=t)


def goto(page, ws, q=""):
    page.goto(f"{BASE}/#/{ws}{q}")
    page.wait_for_selector(".rs", timeout=15000)


def journeys(page, errors):
    # ---------------------------------------------------------------- research
    page.goto(BASE + "/")
    goto(page, "research")
    wait(page, "table.tbl-table tbody tr")
    page.wait_for_timeout(500)
    rows = page.locator('table:has(caption:text-is("Strategy registry")) tbody tr.tbl-row').count()
    check("strategy library lists 5 strategies incl. rejected", rows == 5, str(rows))
    txt = page.locator(".rs").first.inner_text()
    check("rejected + candidate + testing states visible as text badges", all(k in txt for k in ["rejected", "candidate", "testing"]))
    check("approved states shown (0) not hidden", "approved paper" in txt and "approved live" in txt)
    page.locator('select[aria-label="State filter"]').select_option("rejected")
    check("state filter works", page.locator('table:has(caption:text-is("Strategy registry")) tbody tr.tbl-row').count() == 2)
    page.locator('select[aria-label="State filter"]').select_option("all")
    ov = page.evaluate("(() => { const w = document.querySelector('.tbl-scroll'); return w.scrollWidth - w.clientWidth; })()")
    check("strategy table does not scroll horizontally at 1440x900", ov <= 0, str(ov))
    shots(page, "strategies")
    page.locator('table:has(caption:text-is("Strategy registry")) tbody tr.tbl-row', has_text="V3 short-only").click()
    wait(page, ".drawer .rs-card")
    d = page.locator(".drawer").inner_text()
    check("drawer shows rules, version, sources, dataset sha, experiments, validation, approval history",
          all(k in d for k in ["Rules & version", "DAILY_SPEC v1", "Source research", "Dataset", "ef0e598f114c", "D3-V3", "Validation links", "REVIEW.md", "Approval history"]))
    check("drawer: manifest match badge", "matches current manifest" in d)
    page.wait_for_timeout(600)
    page.screenshot(path=str(SHOTS / "research-strategy-drawer-1440x900.png"))
    # refusal path
    page.locator('.drawer select[aria-label="New state"]').select_option("approved_paper")
    page.locator('.drawer textarea[aria-label="Reason"]').fill("trying the forbidden transition")
    page.locator('.drawer button[type=submit]').click()
    wait(page, ".drawer .rs-note-bad")
    msg = page.locator(".drawer .rs-note-bad").inner_text()
    check("approved_paper refused with the server text verbatim", "requires explicit user approval recorded by the user plus an approved risk policy and a broker adapter; not available" in msg and "Refused" in msg, msg[:90])
    check("no success toast after refusal", page.locator(".toast-success").count() == 0)
    check("state unchanged after refusal", "testing" in page.locator(".drawer .badge").first.inner_text())
    page.screenshot(path=str(SHOTS / "research-state-refusal-1440x900.png"))
    # reason required (client side)
    page.locator('.drawer textarea[aria-label="Reason"]').fill("")
    page.locator('.drawer button[type=submit]').click()
    check("reason required message", "reason of at least 3" in page.locator(".drawer").inner_text())
    # allowed transition
    page.locator('.drawer select[aria-label="New state"]').select_option("candidate")
    page.locator('.drawer textarea[aria-label="Reason"]').fill("re-opened for review in UI journey")
    page.locator('.drawer button[type=submit]').click()
    wait(page, ".toast-success")
    check("allowed transition confirmed by server (toast)", "now candidate" in page.locator(".toast-success").first.inner_text())
    wait(page, ".drawer .badge")
    check("drawer reloaded with new state + history", "candidate" in page.locator(".drawer .badge").first.inner_text() and "re-opened for review" in page.locator(".drawer").inner_text())
    page.keyboard.press("Escape")
    # docs
    page.locator('button:has-text("Open research index")').click()
    wait(page, ".drawer .md")
    check("doc viewer renders markdown subset (table)", page.locator(".drawer .md table").count() >= 1 and page.locator(".drawer script").count() == 0)
    page.keyboard.press("Escape")

    # ---------------------------------------------------------------- backtest builder
    goto(page, "backtest")
    wait(page, "form.rs")
    bt = page.locator(".rs").first.inner_text()
    check("builder: H3 no parameters, final test locked, 1D only", "none (H3 has no free parameters)" in bt and "Sealed" in bt and "5m — unavailable" in bt)
    check("builder: final test radio disabled", page.locator('input[name=segment][value=test]').is_disabled())
    wait(page, "text=data adequate")
    check("builder: adequacy check passes for real data", True)
    shots(page, "builder")
    scroll_shots(page, "builder", 1)
    check("builder: universe and seed locked with reason", page.locator('.checks input[type=checkbox]:not([disabled])').count() == 0 and "Pre-registered seed" in page.locator("form").inner_text())
    r = page.evaluate("""async () => { const r = await fetch('/api/experiments',{method:'POST',headers:{'Content-Type':'application/json','X-CC-Token':'research-token'},body:JSON.stringify({strategy_id:'D3_V1_c2_both',segment:'validation',seed:5,symbols:['SPY']})}); return [r.status, (await r.json()).code]; }""")
    check("API refuses unregistered deviating run (422 not_preregistered)", r == [422, "not_preregistered"], str(r))
    page.locator('select[aria-label="Strategy"]').select_option("D3_V1_c2_both")
    page.locator('button:has-text("Queue experiment")').click()
    wait(page, ".toast-success:has-text('Queued exp_')")
    check("experiment accepted by server (toast from response)", True)
    wait(page, "text=Run exp_")
    # real status through completion
    t0 = time.time()
    seen_steps = False
    while time.time() - t0 < 120:
        body = page.locator(".rs").first.inner_text()
        if "pipeline steps finished" in body:
            seen_steps = True
        if "completed" in body and "Open results" in body:
            break
        page.wait_for_timeout(300)
    check("queue shows real steps and completion", seen_steps and "Open results" in page.locator(".rs").first.inner_text())
    body = page.locator(".rs").first.inner_text()
    check("no invented percentage text", "%" not in page.locator(".steps").inner_text() and "steps finished (count reported by the process" in body)
    check("stdout tail visible", "Segment: train" in page.locator("pre.tail").first.inner_text())
    shots(page, "queue")
    # ---------------------------------------------------------------- results
    page.locator('button:has-text("Open results")').click()
    wait(page, ".seg-panel .chart canvas", 30000)
    page.wait_for_timeout(800)
    res = page.locator(".rs").first.inner_text()
    check("results: training shown, validation + sealed + forward separate", "Training segment" in res and "Validation segment" in res and "Out-of-sample (final test)" in res and "sealed" in res and "Forward paper" in res)
    check("results: KPIs net/gross/costs/expectancy/CI present", all(k in res for k in ["Net P&L", "Gross P&L", "Costs", "Expectancy", "95% CI of EV", "Trades", "Exposure", "Max drawdown"]))
    check("results: equity + drawdown charts (2 canvases per chart)", page.locator(".seg-panel .chart").count() == 2 and page.locator(".seg-panel .chart canvas").count() >= 2)
    check("results: attribution logo", page.locator(".seg-panel .chart a[href*='tradingview']").count() >= 1)
    check("results: walk-forward/sensitivity honest", "Walk-forward windows: not computed" in res and "Parameter sensitivity: not applicable" in res)
    check("results: baselines present", "B_D0" in res and "B_D1" in res)
    check("results: trade log rows", page.locator('table:has(caption:text-is("Trade log (this page)")) tbody tr.tbl-row').count() > 10)
    low = res.lower()
    check("no profitable / ready-for-live claim", "profitable" not in low.replace("no profitability", "") and "ready for live" not in low and "live-ready" not in low)
    page.locator(".kpi .def").first.hover()
    page.wait_for_timeout(500)
    check("definition tooltip on hover", page.locator(".tooltip").count() >= 1 and len(page.locator(".tooltip").first.inner_text()) > 10)
    shots(page, "results-train")
    scroll_shots(page, "results-train", 4)
    # committed validation run with verdict
    page.locator('select[aria-label="Run"]').select_option("committed:validation:D3_V3_c2_short")
    wait(page, "text=INCONCLUSIVE", 30000)
    page.wait_for_selector(".seg-panel .chart canvas", timeout=30000)
    page.wait_for_timeout(800)
    res = page.locator(".rs").first.inner_text()
    check("committed validation run: failed gates verbatim", "not met: n_val >= 100" in res and "not met: Bonferroni p <= 0.1" in res)
    check("reproduction verified note", "reproduction verified equal" in res)
    shots(page, "results-validation")
    scroll_shots(page, "results-validation", 3)
    # ---------------------------------------------------------------- compare
    page.get_by_role("tab", name="Compare").click()
    wait(page, ".tabpanel:not([hidden]) label.chk")
    labels = page.locator(".tabpanel:not([hidden]) label.chk")
    for i in range(labels.count()):
        t = labels.nth(i).inner_text()
        if "committed · train · D3_V1" in t or "committed · validation · D3_V1" in t:
            labels.nth(i).locator("input").check()
    page.locator('button:has-text("Compare selected")').click()
    wait(page, "table.cmp")
    ct = page.locator(".rs").first.inner_text()
    check("compare: separate training/validation tables incl. committed runs", "Training segment (separate)" in ct and "Validation segment (separate)" in ct and "committed:train:D3_V1_c2_both" in ct and "not in this run" in ct)
    shots(page, "compare")
    # ---------------------------------------------------------------- monte carlo
    page.get_by_role("tab", name="Monte Carlo").click()
    wait(page, "svg.qplot")
    mt = page.locator(".rs").first.inner_text()
    check("MC: conditional simulation label, method, seed, paths, horizon, thresholds", "Conditional simulation" in mt and "not a probability the strategy works" in mt and "day_block" in mt and "5000" in mt.replace(",", "") and "Horizon" in mt and "Thresholds" in mt)
    check("MC: honest about quantile plots instead of histograms", "not the full simulated distribution" in mt)
    shots(page, "montecarlo")
    scroll_shots(page, "montecarlo", 2)

    # ---------------------------------------------------------------- data
    goto(page, "data")
    wait(page, 'table:has(caption:text-is("Daily-bar coverage per symbol")) tbody tr.tbl-row')
    dt = page.locator(".rs").first.inner_text()
    check("data: coverage rows + not connected + stale/historical data states", page.locator('table:has(caption:text-is("Daily-bar coverage per symbol")) tbody tr.tbl-row').count() == 3 and "not connected" in dt and "No real IBKR export sample exists yet" in dt)
    check("data: quality failure surfaced (SPY 15 envelope)", "15× open/close outside high-low" in dt)
    page.locator('button:has-text("Check adequacy")').click()
    wait(page, "text=adequate")
    page.locator('select[aria-label="Resolution"]').last.select_option("5m")
    page.locator('button:has-text("Check adequacy")').click()
    wait(page, "text=resolution_unavailable")
    check("adequacy: intraday reported unavailable with reason", True)
    # re-check connections really re-fetches
    n0 = len(page.context.pages)
    with page.expect_response(lambda r: r.url.endswith("/api/connections")):
        page.locator('button:has-text("Re-check connections")').click()
    wait(page, ".toast-info")
    check("re-check connections re-fetches and reports", "Re-checked at" in page.locator(".toast-info").first.inner_text())
    # import valid
    page.set_input_files("#imp-file", str(FIX / "SYNTHETIC_flex_trades.csv"))
    wait(page, "text=format: ibkr_flex_csv")
    it = page.locator(".rs").first.inner_text()
    check("import preview: format, mapping, reconciliation not performed", "reconciliation: not performed" in it and "TradePrice" in it and "3 total · 3 valid · 0 quarantined" in it)
    shots(page, "import-preview")
    scroll_shots(page, "import-preview", 2)
    page.locator('button:has-text("Commit (store raw file)")').click()
    wait(page, ".toast-success")
    check("commit success toast from persisted response", "Import committed" in page.locator(".toast-success").first.inner_text())
    wait(page, 'table:has(caption:text-is("Committed imports")) tbody tr.tbl-row')
    check("registry lists the import", page.locator('table:has(caption:text-is("Committed imports")) tbody tr.tbl-row').count() == 1)
    # duplicate
    page.set_input_files("#imp-file", str(FIX / "SYNTHETIC_flex_trades.csv"))
    wait(page, "text=duplicate of #1")
    check("duplicate detected in preview; commit disabled", page.locator('button:has-text("Commit (store raw file)")').is_disabled())
    # invalid
    page.set_input_files("#imp-file", str(FIX / "SYNTHETIC_flex_invalid.csv"))
    wait(page, "text=1 valid · 4 quarantined".replace("1 valid · 4 quarantined", "4 quarantined"))
    it = page.locator(".rs").first.inner_text()
    check("invalid file: quarantined rows with line numbers and reasons", "non-numeric value in Quantity" in it and "column count 9 != header 15" in it)
    page.set_input_files("#imp-file", str(FIX / "SYNTHETIC_flex_trades.xml"))
    wait(page, "text=format: ibkr_flex_xml")
    check("xml preview with quarantined rows", "2 quarantined" in page.locator(".rs").first.inner_text())
    # bad type
    page.set_input_files("#imp-file", files=[{"name": "x.exe", "mimeType": "application/octet-stream", "buffer": b"MZ"}])
    wait(page, "text=Unsupported file type")
    check("unsupported file type rejected client-side", True)
    shots(page, "data")
    scroll_shots(page, "data", 2)

    # ---------------------------------------------------------------- settings
    goto(page, "settings")
    page.get_by_role("tab", name="Audit log").click()
    wait(page, 'table:has(caption:text-is("Audit log (config events + research API)")) tbody tr.tbl-row')
    at = page.locator(".rs").first.inner_text()
    check("audit log merges research entries incl. refused approval + import commit", "refused_approval" in at and "import.commit" in at and "experiment.create" in at)
    shots(page, "audit")
    page.get_by_role("tab", name="Safety limits").click()
    wait(page, 'table:has(caption:text-is("Safety limits"))')
    stx = page.locator(".rs").first.inner_text()
    check("safety limits listed with reasons", "Live trading" in stx and "Final test set" in stx and "Paper trading" in stx)
    page.get_by_role("tab", name="Usage & approvals").click()
    check("usage honestly 'not measured'", "Not measured" in page.locator(".rs").first.inner_text())
    page.get_by_role("tab", name="Integration health").click()
    wait(page, 'table:has(caption:text-is("Integration health"))')
    page.get_by_role("tab", name="Preferences").click()
    shots(page, "settings")
    # ---------------------------------------------------------------- test segment refusal via API
    r = page.evaluate("""async () => (await fetch('/api/experiments',{method:'POST',headers:{'Content-Type':'application/json','X-CC-Token':'research-token'},body:JSON.stringify({strategy_id:'D3_V1_c2_both',segment:'test'})})).status""")
    check("API refuses the sealed test segment (403)", r == 403)
    # ---------------------------------------------------------------- stale stream
    page.goto(BASE + "/?cc_stale_after=3#/data")
    page.wait_for_selector(".rs", timeout=15000)
    try:
        page.wait_for_selector(".workspace.is-stale", timeout=20000)
        check("stale stream: shell dims workspace, research screen still renders", page.locator(".rs table").count() > 0)
    except Exception:
        check("stale stream: shell marks workspace stale", False)
    page.screenshot(path=str(SHOTS / "research-stale-1440x900.png"))


if __name__ == "__main__":
    sys.exit(main())
