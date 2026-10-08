#!/usr/bin/env python3
"""Shell verification with Playwright + the preinstalled Chromium (/opt/pw-browsers). TEMP db, own port; kills its own server.

  python command_center/verify_shell.py          # writes screenshots/after/shell-*.png and screenshots/shell_results.json
"""
from __future__ import annotations

import glob
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SHOTS = HERE / "screenshots" / "after"
PORT = int(os.environ.get("CC_SHELL_PORT", 8793))
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "shell-e2e-token"
results = []
sys.path.insert(0, str(ROOT))


def check(name, ok, detail=""):
    detail = " ".join(str(detail).split())
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f" - {detail}" if detail else ""), flush=True)


def start_server(db):
    env = dict(os.environ, CC_DB=db, CC_TOKEN=TOKEN, CC_PORT=str(PORT))
    p = subprocess.Popen([sys.executable, "-m", "command_center.server"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            urllib.request.urlopen(BASE + "/api/health", timeout=1).read()
            return p
        except Exception:
            time.sleep(0.2)
    p.kill()
    raise SystemExit("server did not start")


def api(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "X-CC-Token": TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


LAYOUT_JS = r"""() => {
  const out = {docOverflowX: document.documentElement.scrollWidth > innerWidth + 1, docOverflowY: document.documentElement.scrollHeight > innerHeight + 1,
               bodyScroll: document.scrollingElement.scrollTop, topbarOverflow: 0, offscreen: [], overlaps: [], clipped: [], nested: []};
  const tb = document.getElementById('topbar'); out.topbarOverflow = tb.scrollWidth - tb.clientWidth;
  const vis = [...tb.querySelectorAll(':scope > *, #tb-right > *')].filter(n => n.getClientRects().length && !n.matches('#tb-ovf'));
  const rects = vis.map(n => [n, n.getBoundingClientRect()]);
  for (const [n, r] of rects) { if (r.right > innerWidth + 0.5 || r.left < -0.5) out.offscreen.push(n.id || n.className); }
  for (let i = 0; i < rects.length; i++) for (let j = i + 1; j < rects.length; j++) {
    const a = rects[i][1], b = rects[j][1];
    if (a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1 && !rects[i][0].contains(rects[j][0]) && !rects[j][0].contains(rects[i][0])) out.overlaps.push([rects[i][0].id || rects[i][0].className, rects[j][0].id || rects[j][0].className]);
  }
  // text clipped inside chips / badges / buttons (ellipsis not intended there)
  for (const n of document.querySelectorAll('#topbar .chip-label, #topbar .chip, #topbar .env-badge, .nav-item .nav-label, .nav-cap, .tbl-table th .th-label')) {
    if (!n.getClientRects().length) continue;
    if (n.scrollWidth > n.clientWidth + 1 && getComputedStyle(n).overflow !== 'visible' && !n.closest('th')) out.clipped.push((n.id || n.className) + ':' + n.textContent.trim().slice(0, 30));
  }
  // scrollable containers: only #workspace, #nav, .tbl-scroll and popover/drawer internals may scroll
  const allowed = new Set(['workspace', 'nav']);
  for (const n of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(n);
    if (!/(auto|scroll)/.test(cs.overflowY + cs.overflowX) || !n.getClientRects().length) continue;
    const sh = n.scrollHeight > n.clientHeight + 1, sw = n.scrollWidth > n.clientWidth + 1;
    if (!(sh || sw)) continue;
    if (allowed.has(n.id) || n.classList.contains('tbl-scroll') || n.closest('.popover, .drawer, .tb-ovf, .search-list, .notes-list')) continue;
    out.nested.push((n.id || n.className) + (sh ? ' V' : '') + (sw ? ' H' : ''));
  }
  const ws = document.getElementById('workspace');
  out.wsNestedScrollers = [...ws.querySelectorAll('*')].filter(n => { const cs = getComputedStyle(n); return /(auto|scroll)/.test(cs.overflowY) && n.scrollHeight > n.clientHeight + 1 && !n.classList.contains('tbl-scroll') && !n.closest('.popover'); }).map(n => n.className);
  return out;
}"""


def main():
    from playwright.sync_api import sync_playwright
    SHOTS.mkdir(parents=True, exist_ok=True)
    exe = next(iter(sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))), None) or "/opt/pw-browsers/chromium/chrome"
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "shell.sqlite3")
    srv = start_server(db)
    shot = lambda page, name: page.screenshot(path=str(SHOTS / f"shell-{name}.png"))
    try:
        subprocess.run([sys.executable, str(HERE / "seed_demo.py"), "--server", BASE, "--token", TOKEN], check=True, capture_output=True)
        api("POST", "/api/events", {"event_id": "inc-1", "event_type": "incident", "source": "emit", "run_id": "demo-run-1", "payload": {"severity": "warning", "summary": "Validator found a high-severity finding"}})
        with sync_playwright() as pw:
            br = pw.chromium.launch(executable_path=exe, args=["--no-sandbox"])
            ctx = br.new_context(viewport={"width": 1440, "height": 900})
            page = ctx.new_page()
            errors, ext = [], []
            page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
            page.on("console", lambda m: errors.append("console: " + m.text) if m.type == "error" else None)
            page.on("request", lambda r: ext.append(r.url) if not r.url.startswith(BASE) and not r.url.startswith("data:") else None)

            def reveal(sel):
                """Click a top-bar control; if the fit algorithm moved it into the overflow menu, open that first."""
                if not page.locator(sel).first.is_visible():
                    page.click("#ovf-btn"); page.wait_for_timeout(150)
                page.click(sel)

            def boot(url=BASE + "/", wait_live=True):
                page.goto(url)
                page.wait_for_selector("#chip-agents", state="attached")
                if wait_live:
                    page.wait_for_function("window.__cc && __cc.ctx.events.state().conn === 'live'", timeout=10000)
                page.wait_for_function("document.querySelector('#workspace .ws-host')", timeout=8000)

            # ---------------------------------------------------------------- layout at 4 viewports
            for (w, h) in [(1920, 1080), (1440, 900), (1280, 720), (900, 700)]:
                page.set_viewport_size({"width": w, "height": h})
                boot()
                page.wait_for_timeout(500)
                lay = page.evaluate(LAYOUT_JS)
                ok = not lay["docOverflowX"] and not lay["docOverflowY"] and lay["topbarOverflow"] <= 1 and not lay["offscreen"] and not lay["overlaps"]
                check(f"{w}x{h}: shell fits (no page scroll, top bar does not overflow, no overlap/offscreen)", ok, json.dumps({k: lay[k] for k in ("docOverflowX", "docOverflowY", "topbarOverflow", "offscreen", "overlaps")}))
                check(f"{w}x{h}: no clipped chip/nav text", not lay["clipped"], lay["clipped"])
                check(f"{w}x{h}: no nested scrollbars (only workspace/nav/table scroll)", not lay["nested"] and not lay["wsNestedScrollers"], f"{lay['nested']} {lay['wsNestedScrollers']}")
                vis_risk = page.locator("#chip-risk, #tb-ovf #chip-risk").first.is_visible() or page.locator("#chip-risk").count() == 1
                check(f"{w}x{h}: ENV badge, mode, risk chip reachable", page.locator("#env-badge").is_visible() and page.locator("#mode-btn").is_visible() and vis_risk)
                shot(page, f"{w}x{h}")
                if w == 900:
                    check("900x700: nav collapses to icons", page.evaluate("document.getElementById('app').dataset.nav") == "icons" and page.evaluate("document.getElementById('nav').getBoundingClientRect().width") <= 70)
                    check("900x700: overflow menu holds the lowest-priority items", page.locator("#ovf-btn").is_visible())
                    page.click("#ovf-btn"); page.wait_for_timeout(200)
                    check("overflow menu opens with status items", page.locator("#tb-ovf").is_visible() and page.locator("#tb-ovf #chip-broker").count() == 1)
                    shot(page, "900x700-overflow")
                    page.keyboard.press("Escape")
                    check("overflow menu closes with Esc", not page.locator("#tb-ovf").is_visible())
            # 700px wide: drawer navigation
            page.set_viewport_size({"width": 700, "height": 800}); boot(); page.wait_for_timeout(300)
            check("700px: nav becomes a drawer (hidden until opened)", page.evaluate("document.getElementById('app').dataset.nav") == "drawer" and page.locator(".nav-toggle").is_visible() and page.evaluate("document.getElementById('nav').getBoundingClientRect().right") <= 0)
            lay = page.evaluate(LAYOUT_JS)
            check("700px: shell fits, no overflow", not lay["docOverflowX"] and lay["topbarOverflow"] <= 1 and not lay["offscreen"], json.dumps({k: lay[k] for k in ("docOverflowX", "topbarOverflow", "offscreen")}))
            page.click(".nav-toggle"); page.wait_for_timeout(300)
            check("700px: nav drawer opens, focus moves into it", page.evaluate("document.getElementById('nav').getBoundingClientRect().left") >= 0 and page.evaluate("document.getElementById('nav').contains(document.activeElement)"))
            shot(page, "700x800-nav-drawer")
            page.keyboard.press("Escape"); page.wait_for_timeout(250)
            check("700px: Esc closes nav drawer and focus returns to the toggle", page.evaluate("document.getElementById('nav').getBoundingClientRect().right") <= 0 and page.evaluate("document.activeElement.classList.contains('nav-toggle')"))

            # ---------------------------------------------------------------- back to 1440x900 for functional checks
            page.set_viewport_size({"width": 1440, "height": 900}); boot()
            # environment / execution honesty
            env_txt = page.locator("#env-badge").inner_text()
            check("ENVIRONMENT badge states research only / no execution", "RESEARCH ONLY" in env_txt and "Execution: none" in (page.get_attribute("#env-badge", "title") or ""), env_txt)
            check("broker chip says 'Broker not connected'", "Broker not connected" in page.locator("#chip-broker").inner_text())
            check("market-data chip is Historical (daily bars), never Realtime", "Historical" in page.locator("#chip-data").inner_text() and "Realtime" not in page.locator("#chip-data").inner_text(), page.locator("#chip-data").inner_text())
            sess = page.locator("#chip-session")
            sess_txt = sess.inner_text() if sess.is_visible() else page.evaluate("document.getElementById('chip-session').textContent")
            check("exchange-session chip is NYSE open/closed and marked unverified", ("NYSE open" in sess_txt or "NYSE closed" in sess_txt) and "unverified" in sess_txt, sess_txt)
            check("risk chip reads 'No risk policy'", "No risk policy" in page.locator("#chip-risk").inner_text())
            check("footer credit line + safe link", "Charts: TradingView Lightweight Charts" in page.locator("#footer").inner_text() and page.get_attribute("#footer a", "href") == "https://www.tradingview.com/" and "noopener" in page.get_attribute("#footer a", "rel"))
            names = page.evaluate("[...document.querySelectorAll('#nav .nav-label')].map(n => n.textContent)")
            check("nav lists exactly the 8 workspaces in order", names == ["Command Center", "Stocks & Charts", "Portfolio & Orders", "Agent Team", "Research & Strategies", "Backtesting & Monte Carlo", "Data & Connections", "Settings & Audit"], names)
            caps = page.evaluate("[...document.querySelectorAll('#nav .nav-cap')].map(n => n.textContent)")
            check("nav shows honest capability states (no 'not implemented')", all(c and "not implemented" not in c.lower() for c in caps) and "no broker" in caps, caps)
            check("no emoji in the chrome", not page.evaluate("/[\\u{1F300}-\\u{1FAFF}\\u{2600}-\\u{27BF}]/u.test(document.getElementById('app').textContent)"))
            check("landmarks: banner, navigation, main, contentinfo", page.locator("header[role=banner]").count() == 1 and page.locator("nav[aria-label=Workspaces]").count() == 1 and page.locator("main#workspace").count() == 1 and page.locator("footer[role=contentinfo]").count() == 1)
            shot(page, "overview-1440")

            # ---------------------------------------------------------------- token + persistence (server-backed)
            reveal("#token-btn"); page.fill(".token-pop input", TOKEN); page.click("text=Save token"); page.wait_for_timeout(500)
            check("token saved in localStorage", page.evaluate("localStorage.getItem('cc.token')") == TOKEN)
            page.keyboard.press("Escape")
            # nav collapse + persistence
            page.click("#nav-collapse"); page.wait_for_timeout(500)
            check("nav collapses to icons", page.evaluate("document.getElementById('app').dataset.nav") == "icons")
            shot(page, "nav-collapsed-1440")
            page.reload(); page.wait_for_selector("#chip-agents", state="attached"); page.wait_for_timeout(500)
            check("collapsed nav persists across reload", page.evaluate("document.getElementById('app').dataset.nav") == "icons")
            st, pr = api("GET", "/api/prefs")
            check("nav state persisted on the SERVER (/api/prefs)", st == 200 and pr["prefs"].get("shell.nav_collapsed") is True, pr["prefs"])
            page.evaluate("() => { localStorage.removeItem('cc.prefs.cache'); localStorage.removeItem('cc.prefs.dirty'); }")
            page.reload(); page.wait_for_selector("#chip-agents", state="attached"); page.wait_for_timeout(600)
            check("with the UI cache wiped the server prefs still restore the nav state", page.evaluate("document.getElementById('app').dataset.nav") == "icons")
            page.click("#nav-collapse"); page.wait_for_timeout(300)

            # ---------------------------------------------------------------- mode selector accuracy
            page.click("#mode-btn"); page.wait_for_timeout(200)
            check("mode menu lists DEMO BACKTEST REPLAY SHADOW PAPER LIVE", page.evaluate("[...document.querySelectorAll('.mode-item .mode-item-name')].map(n => n.textContent.replace(/\\(current\\)/, '').trim())") == ["DEMO", "BACKTEST", "REPLAY", "SHADOW", "PAPER", "LIVE"])
            check("mode menu states execution none / broker adapter not built", "Execution: none" in page.locator(".mode-pop").inner_text() and "broker adapter not built" in page.locator(".mode-pop").inner_text())
            cur = page.locator("#mode-value").inner_text()
            check("default mode badge matches the event data (seeded demo => DEMO)", cur == "DEMO", cur)
            shot(page, "mode-menu")
            page.click(".mode-item:has-text('BACKTEST')"); page.wait_for_timeout(600)
            check("selecting BACKTEST updates the badge", page.locator("#mode-value").inner_text() == "BACKTEST")
            audit = api("GET", "/api/audit")[1]["items"]
            check("mode change recorded as audit.config (old DEMO -> new BACKTEST)", any(a["action"] == "mode" and a["old"] == "DEMO" and a["new"] == "BACKTEST" and a["outcome"] == "applied" for a in audit), audit[:1])
            for m, word in (("PAPER", "Refused"), ("LIVE", "Refused"), ("SHADOW", "Unavailable")):
                page.click(f".mode-item:has-text('{m}')", force=True); page.wait_for_timeout(450)
                msg = page.locator(".mode-msg").inner_text()
                check(f"{m} is refused with a concise reason and mode stays BACKTEST", word in msg and page.locator("#mode-value").inner_text() == "BACKTEST", msg)
            check("refusals are audited", sum(1 for a in api("GET", "/api/audit")[1]["items"] if a["outcome"] == "refused") >= 3)
            check("server never recorded a PAPER/LIVE mode event", not any(e["event_type"] == "mode.changed" and e["payload"]["mode"] in ("PAPER", "LIVE") for e in api("GET", "/api/events?limit=5000")[1]["events"]))
            page.keyboard.press("Escape")
            check("Esc closes the mode menu and focus returns to the button", page.evaluate("document.activeElement.id") == "mode-btn")
            reveal("#chip-broker"); page.wait_for_timeout(200)
            check("broker popover: 'Trading setup incomplete' + Set up action", "Trading setup incomplete" in page.locator(".info-pop").inner_text() and page.get_by_role("button", name="Set up in Data & Connections").is_visible())
            shot(page, "broker-popover")
            page.get_by_role("button", name="Set up in Data & Connections").click(); page.wait_for_timeout(500)
            check("Set up opens Data & Connections", page.evaluate("location.hash") == "#/data" and "Data & Connections" in page.locator("#ws-title").inner_text())
            check("Data placeholder shows real connection state", "not_connected: no broker adapter implemented" in page.locator("#workspace").inner_text())
            shot(page, "data-placeholder")

            # ---------------------------------------------------------------- risk drawer
            page.focus("#chip-risk"); page.keyboard.press("Enter"); page.wait_for_selector(".drawer.open, .layer.open .drawer"); page.wait_for_timeout(300)
            drawer_txt = page.locator(".drawer").inner_text()
            check("risk drawer opens as a modal dialog", page.get_attribute(".drawer", "role") == "dialog" and page.get_attribute(".drawer", "aria-modal") == "true")
            check("risk drawer: 'No risk policy configured' and em dashes with reasons", "No risk policy configured" in drawer_txt and page.locator(".drawer .missing").count() >= 8)
            check("risk drawer: all 3 controls DISABLED with scope + prerequisite", page.locator(".drawer .risk-ctl button:disabled").count() == 3 and drawer_txt.count("Unavailable because") == 3 and drawer_txt.count("Scope:") == 3)
            check("risk drawer: disabled controls reference their explanation (aria-describedby)", page.evaluate("[...document.querySelectorAll('.drawer .risk-ctl button')].every(b => document.getElementById(b.getAttribute('aria-describedby')) && document.getElementById(b.getAttribute('aria-describedby')).textContent.includes('Scope'))"))
            check("risk drawer: flags listed (stale data, spread, buying power, concentration, reconciliation)", all(k in drawer_txt for k in ["Stale market data", "Spread", "Buying power", "Concentration", "Reconciliation"]))
            check("focus is trapped inside the drawer and background is inert", page.evaluate("document.querySelector('.drawer').contains(document.activeElement) && document.getElementById('app').inert === true"))
            for _ in range(12):
                page.keyboard.press("Tab")
            check("Tab stays inside the drawer", page.evaluate("document.querySelector('.drawer').contains(document.activeElement)"))
            shot(page, "risk-drawer")
            page.keyboard.press("Escape"); page.wait_for_timeout(400)
            check("Esc closes the risk drawer", page.locator(".drawer").count() == 0)
            check("focus returns to the risk chip; background no longer inert", page.evaluate("document.activeElement.id") == "chip-risk" and page.evaluate("document.getElementById('app').inert") is False)

            # ---------------------------------------------------------------- bell / notifications
            bc = page.locator("#bell .bell-count").inner_text() if page.locator("#bell .bell-count").is_visible() else ""
            check("bell shows real unread incidents/approvals", bc.isdigit() and int(bc) >= 1, bc)
            reveal("#bell"); page.wait_for_timeout(250)
            ntxt = page.locator(".notes-pop").inner_text()
            check("notifications list the real incident text", "Validator found a high-severity finding" in ntxt, ntxt[:120])
            shot(page, "notifications")
            page.keyboard.press("Escape")

            # ---------------------------------------------------------------- keyboard: shortcuts, search, skip link, focus ring, table
            page.keyboard.press("Alt+3"); page.wait_for_timeout(500)
            check("Alt+3 opens Portfolio & Orders", page.evaluate("location.hash") == "#/portfolio" and "Portfolio" in page.locator("#ws-title").inner_text())
            check("portfolio placeholder says broker not connected (not zeros)", "Broker not connected" in page.locator("#workspace").inner_text() and "$0" not in page.locator("#workspace").inner_text())
            page.keyboard.press("Alt+1"); page.wait_for_timeout(400)
            page.evaluate("document.activeElement.blur()")
            page.keyboard.press("/")
            check("'/' focuses symbol search", page.evaluate("document.activeElement.type") == "search")
            page.keyboard.type("sp"); page.wait_for_selector(".search-item"); page.wait_for_timeout(200)
            check("symbol search lists SPY with conId + exchange from the manifest", "SPY" in page.locator(".search-list").inner_text() and "756733" in page.locator(".search-list").inner_text() and "ARCA" in page.locator(".search-list").inner_text())
            check("search result without manifest name says so (nothing invented)", "name not in data manifest" in page.locator(".search-list").inner_text())
            shot(page, "search")
            page.keyboard.press("Enter"); page.wait_for_timeout(600)
            check("choosing a result routes to Stocks with symbol/conid/exchange", page.evaluate("location.hash") == "#/stocks?symbol=SPY&conid=756733&ex=ARCA", page.evaluate("location.hash"))
            # search degrades honestly when endpoint is absent
            page.route("**/api/instruments*", lambda r: r.fulfill(status=404, content_type="application/json", body='{"ok":false,"error":"not found"}'))
            page.click(".search-input"); page.wait_for_timeout(600)
            check("search degrades honestly when /api/instruments is absent", "unavailable" in page.locator(".search-list").inner_text().lower(), page.locator(".search-list").inner_text()[:100])
            page.unroute("**/api/instruments*"); page.keyboard.press("Escape"); page.evaluate("document.activeElement.blur()")
            # skip link + focus ring
            page.keyboard.press("Alt+1"); page.wait_for_timeout(300)
            page.reload(); page.wait_for_selector("#chip-agents", state="attached"); page.wait_for_timeout(500)
            page.keyboard.press("Tab")
            check("first Tab stop is the skip link", page.evaluate("document.activeElement.classList.contains('skip')"))
            page.keyboard.press("Enter"); page.wait_for_timeout(200)
            check("skip link moves focus to the workspace without breaking the route", page.evaluate("document.activeElement.id") == "workspace" and page.evaluate("location.hash") == "#/command_center")
            page.focus("#nav a.nav-item >> nth=1")
            page.keyboard.press("Shift+Tab"); page.keyboard.press("Tab")
            ring = page.evaluate("(() => { const cs = getComputedStyle(document.activeElement); return [cs.outlineStyle, cs.outlineWidth, cs.outlineColor]; })()")
            check("visible focus ring on keyboard focus (2px solid focus colour)", ring[0] == "solid" and ring[1] == "2px", ring)
            # table: sort, keyboard rows, expansion, hide column, CSV
            page.goto(BASE + "/#/command_center"); page.wait_for_selector(".tbl-row")
            page.click("th:has-text('Symbol') .th-btn"); page.wait_for_timeout(150)
            first = page.locator(".tbl-row td").first.inner_text()
            page.click("th:has-text('Symbol') .th-btn"); page.wait_for_timeout(150)
            check("table sorts asc then desc (aria-sort)", first == "IWM" and page.locator(".tbl-row td").first.inner_text() == "SPY" and page.get_attribute("th:has-text('Symbol')", "aria-sort") == "descending", f"{first}")
            page.focus(".tbl-row >> nth=0"); page.keyboard.press("ArrowDown")
            check("table: ArrowDown moves row focus", page.evaluate("document.activeElement.classList.contains('tbl-row') && document.activeElement.parentNode.querySelectorAll('.tbl-row')[1] === document.activeElement"))
            col = page.locator("th:has-text('Ccy')")
            box0 = page.locator("th:has-text('Bars')").bounding_box()["x"]
            page.click("text=Columns"); page.click(".menu-pop label:has-text('Ccy') input"); page.keyboard.press("Escape"); page.wait_for_timeout(200)
            check("table: column can be hidden and the state is persisted in prefs", col.count() == 0 and "currency" in (page.evaluate("__cc.ctx.prefs.get('table.ph.instruments')") or {}).get("hide", []))
            with page.expect_download() as dl:
                page.click("button:has-text('CSV')")
            csv_text = Path(dl.value.path()).read_text(encoding="utf-8-sig")
            check("CSV export contains the visible columns and rows only", csv_text.splitlines()[0].startswith("Symbol,Name,Exchange") and "Ccy" not in csv_text.splitlines()[0] and "SPY" in csv_text and len(csv_text.strip().splitlines()) == 4, csv_text.splitlines()[:2])
            # resizable column via keyboard on the grip
            w0 = page.locator("th:has-text('Name')").bounding_box()["width"]
            page.focus("th:has-text('Name') .th-grip"); page.keyboard.press("ArrowRight"); page.keyboard.press("ArrowRight")
            check("table: column resize (keyboard) widens the column", page.locator("th:has-text('Name')").bounding_box()["width"] > w0 + 20)

            # ---------------------------------------------------------------- router: error boundary + unknown route
            page.route("**/static/js/workspaces/research.js", lambda r: r.fulfill(status=500, body="boom"))
            page.goto(BASE + "/#/research"); page.wait_for_timeout(800)
            check("workspace load failure is contained (error view + Retry), shell alive", page.locator(".ws-error").is_visible() and page.locator("#nav").is_visible() and "failed to load" in page.locator(".ws-error").inner_text())
            shot(page, "workspace-error")
            page.unroute("**/static/js/workspaces/research.js")
            page.click("#nav a[data-ws=stocks]"); page.wait_for_timeout(500)
            check("navigation works after a failed workspace", "Stocks" in page.locator("#ws-title").inner_text() and page.locator(".ws-error").count() == 0)
            page.goto(BASE + "/#/nonsense"); page.wait_for_timeout(500)
            check("unknown route falls back to Command Center", page.evaluate("location.hash") == "#/command_center")
            page.goto(BASE + "/#/agents"); page.wait_for_selector(".tbl-row", timeout=8000)
            check("agents placeholder lists real events from the stream", page.locator(".tbl-row").count() >= 5)
            page.locator(".tbl-row >> nth=0").click(); page.wait_for_timeout(200)
            check("table row expansion works (click) and shows payload as text", page.locator(".tbl-expand").count() == 1)
            shot(page, "agents-placeholder")

            # ---------------------------------------------------------------- theme, reduced motion
            page.goto(BASE + "/#/settings"); page.wait_for_selector("#theme-btn", state="attached"); page.wait_for_timeout(400)
            reveal("#theme-btn")
            page.wait_for_timeout(300)
            check("theme toggle switches to light", page.evaluate("document.documentElement.dataset.theme") == "light")
            shot(page, "light-settings")
            page.reload(); page.wait_for_selector("#chip-agents", state="attached")
            check("theme persists across reload", page.evaluate("document.documentElement.dataset.theme") == "light")
            page.evaluate("() => { __cc.ctx.prefs.set('ui.theme','dark'); document.documentElement.dataset.theme='dark'; localStorage.setItem('cc.theme','dark'); }")
            ctx2 = br.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
            p2 = ctx2.new_page(); p2.goto(BASE + "/"); p2.wait_for_selector("#chip-risk"); p2.click("#chip-risk"); p2.wait_for_selector(".drawer")
            td = p2.evaluate("getComputedStyle(document.querySelector('.drawer')).transitionDuration")
            check("prefers-reduced-motion respected (drawer transition ~0)", float(td.split(",")[0].replace("s", "")) <= 0.01, td)
            ctx2.close()

            # ---------------------------------------------------------------- SSE: STALE, reconnect, replay
            boot(BASE + "/?cc_stale_after=4")
            page.evaluate("() => { window.__seen = []; __cc.ctx.events.on('*', (e) => window.__seen.push(e.seq)); }")
            seq0 = page.evaluate("__cc.ctx.events.state().lastSeq")
            srv.send_signal(signal.SIGTERM); srv.wait(5)
            page.wait_for_function("['reconnecting','stale'].includes(__cc.ctx.events.state().conn)", timeout=8000)
            check("server killed -> agents chip leaves LIVE", "live" not in page.evaluate("document.getElementById('chip-agents').textContent.toLowerCase()"), page.evaluate("document.getElementById('chip-agents').textContent"))
            page.wait_for_function("__cc.ctx.events.state().conn === 'stale'", timeout=15000)
            banner = page.locator(".banner-stale").inner_text()
            check("STALE banner: 'Event stream STALE - showing last known state as of <time>'", "Event stream STALE" in banner and "last known state as of" in banner, banner)
            check("freshness model: agents feed is 'stale', quotes/broker feeds independent", page.evaluate("__cc.ctx.freshness.get('agents').state") == "stale" and page.evaluate("__cc.ctx.freshness.get('quotes').state") == "historical" and page.evaluate("__cc.ctx.freshness.get('broker').state") == "unavailable")
            check("workspace greyed while stale", page.evaluate("document.getElementById('workspace').classList.contains('is-stale')"))
            shot(page, "stale")
            # events written while the server is down (straight into the DB file) must replay after restart, once, in order
            from command_center.store import Store
            st = Store(db)
            res = st.insert([{"event_id": f"offline-{i}", "event_type": "incident", "source": "emit", "run_id": "demo-run-1", "payload": {"severity": "critical" if i == 2 else "info", "summary": f"offline event {i}"}} for i in (1, 2, 3)])
            st.close()
            srv = start_server(db)
            page.wait_for_function("__cc.ctx.events.state().conn === 'live'", timeout=20000)
            page.wait_for_function("window.__seen.length >= 3", timeout=10000)
            seen = page.evaluate("window.__seen")
            expect = [a["seq"] for a in res["accepted"]]
            check("reconnect: UI returns to LIVE without reload; stale banner gone", not page.locator(".banner-stale").is_visible())
            check("replay after restart: missed events delivered once, in order, no gaps", seen == sorted(set(seen)) and seen[-3:] == expect and (not seen[:-3] or seen[0] > seq0), f"seen={seen} expect={expect}")
            check("replayed incident reached the bell", "offline event 2" in (page.evaluate("(async()=>{document.getElementById('bell').click(); await new Promise(r=>setTimeout(r,200)); return document.querySelector('.notes-pop').textContent})()")))
            page.keyboard.press("Escape")
            shot(page, "after-reconnect")
            # a raw SSE client resuming with Last-Event-ID gets exactly the missed events
            import http.client
            c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=5)
            c.request("GET", "/api/stream", headers={"Last-Event-ID": str(expect[0] - 1)}); r = c.getresponse()
            got, buf = [], ""
            t0 = time.time()
            while len(got) < 3 and time.time() - t0 < 5:
                line = r.fp.readline().decode()
                if line.startswith("id:"):
                    got.append(int(line[3:]))
            c.close()
            check("Last-Event-ID resume returns exactly the missed seqs", got == expect, got)

            # ---------------------------------------------------------------- UI kit via the dev harness (stub ctx)
            kp = ctx.new_page(); kerr = []
            kp.on("pageerror", lambda e: kerr.append(str(e))); kp.on("console", lambda m: kerr.append(m.text) if m.type == "error" else None)
            kp.goto(BASE + "/static/dev.html?ws=command_center"); kp.wait_for_function("window.__dev", timeout=8000); kp.wait_for_selector(".tbl-row", timeout=8000)
            check("dev harness mounts a workspace with a stub ctx (no backend writes)", kp.locator(".ph").count() == 1 and kp.evaluate("__dev.ctx.stub") is True)
            kp.evaluate("""() => { const {ui, el} = __dev.ctx; const host = document.getElementById('workspace'); host.replaceChildren();
              window.__rows = Array.from({length: 5000}, (_, i) => ({id: i, sym: 'S' + String(i).padStart(4, '0'), px: 100 + (i % 97) * 1.37, chg: ((i % 13) - 6) / 4, q: i % 7 === 0 ? null : i}));
              window.__t = ui.table({id: 'kit.big', caption: 'Kit table', rows: window.__rows, rowKey: r => r.id, filter: true, maxHeight: '420px', expand: (r, td) => td.append(el('span', {}, 'detail for ' + r.sym)),
                columns: [{key: 'sym', label: 'Symbol', width: 110}, {key: 'px', label: 'Price', align: 'num', format: 'price'}, {key: 'chg', label: 'Chg %', align: 'num', format: 'pct', colour: true}, {key: 'q', label: 'Qty', align: 'num', format: 'qty', missing: 'No position: broker not connected'}]});
              host.append(window.__t.el); }""")
            kp.wait_for_timeout(300)
            check("table >2000 rows is paged (500 rows in the DOM) with a pager", kp.locator(".tbl-row").count() == 500 and kp.locator(".tbl-pager").is_visible() and "of 5000" in kp.locator(".tbl-pager").inner_text(), kp.locator(".tbl-pager").inner_text())
            check("table: numeric cells right-aligned, tabular numerals, missing = em dash with reason tooltip", kp.evaluate("(() => { const td = document.querySelectorAll('.tbl-row')[1].querySelectorAll('td')[2]; const cs = getComputedStyle(td); const m = document.querySelector('.tbl-row .missing'); return cs.textAlign === 'right' && getComputedStyle(td.firstChild).fontVariantNumeric.includes('tabular') && m.textContent.startsWith('\u2014') && m.title.includes('broker not connected'); })()"))
            kp.click(".tbl-pager button:has-text('Next')"); kp.wait_for_timeout(200)
            check("pager Next shows rows 501-1000", "rows 501" in kp.locator(".tbl-pager").inner_text())
            check("CSV of a paged table exports ALL rows (5000) and keeps the em-dash cells empty", kp.evaluate("window.__t.toCSV().trim().split('\\r\\n').length") == 5001)
            kp.fill(".tbl-bar input[type=search]", "S0042"); kp.wait_for_timeout(300)
            check("table filter narrows rows and updates the count", kp.locator(".tbl-row").count() == 1 and "1 of 5000" in kp.locator(".tbl-count").inner_text())
            kp.locator(".tbl-row").first.click(); kp.wait_for_timeout(100)
            check("table row expansion renders caller content as text", "detail for S0042" in kp.locator(".tbl-expand").inner_text())
            check("pct cells carry an explicit sign (colour is not the only cue)", kp.evaluate("window.__t.setFilter(''); window.__t.setRows(window.__rows.slice(0, 40)); [...document.querySelectorAll('.tbl-row td:nth-child(4) .cell')].every(c => /^[+\\u2212]?\\d/.test(c.textContent) && (c.textContent.startsWith('+') || c.textContent.startsWith('\\u2212') || c.textContent.startsWith('0.00')))"))
            res_ = kp.evaluate("""async () => { const {ui} = __dev.ctx; const out = {}; const p = ui.confirm({title: 'Flatten strategy', scope: 'All 3 open positions of strategy ORB-1 (SPY x 10, QQQ x 5)', typed: 'ORB-1', danger: true, confirmLabel: 'Flatten'});
              await new Promise(r => setTimeout(r, 50)); const ok = document.querySelector('.modal .btn-danger'); out.disabledAtStart = ok.disabled; out.scope = document.querySelector('.scope-box').textContent;
              out.focusOnInput = document.activeElement.tagName === 'INPUT'; const inp = document.querySelector('.modal input'); inp.value = 'ORB-1'; inp.dispatchEvent(new Event('input')); out.enabledAfterTyping = !ok.disabled;
              document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true})); out.escResult = await p; out.gone = !document.querySelector('.modal'); return out; }""")
            check("confirm(): scope shown, typed-confirm gates the button, Esc resolves false", res_["disabledAtStart"] and "ORB-1" in res_["scope"] and res_["focusOnInput"] and res_["enabledAfterTyping"] and res_["escResult"] is False and res_["gone"], res_)
            kp.evaluate("() => __dev.ctx.ui.toast('Preference saved on the server', {title: 'Saved'})"); kp.wait_for_timeout(100)
            check("toast renders in a polite live region", kp.locator(".toast-region[aria-live=polite] .toast").count() == 1)
            kp.evaluate("""() => { const {ui, el} = __dev.ctx; const host = document.getElementById('workspace'); host.replaceChildren();
              window.__tabs = ui.tabs({id: 'kit', label: 'Kit tabs', tabs: [{id: 'a', label: 'Alpha', render: p => p.append('panel A')}, {id: 'b', label: 'Beta', render: p => p.append('panel B')}, {id: 'c', label: 'Gamma', render: p => p.append('panel C')}]});
              const row = el('div', {style: {display: 'flex', height: '200px', marginTop: '16px'}}); const main = el('div', {style: {flex: '1'}}, 'main'); const side = el('div', {class: 'side', style: {background: 'var(--surface)'}}, 'side panel');
              row.append(main, side); host.append(window.__tabs.el, row); window.__rz = ui.resizer({panel: side, side: 'left', id: 'kit.side', width: 240, min: 160, max: 400});
              const b = el('button', {class: 'btn', id: 'tipbtn'}, 'Hover me'); host.append(b); ui.tooltip(b, 'Tooltip text', {delay: 10}); }""")
            kp.focus("#workspace [role=tab] >> nth=0"); kp.keyboard.press("ArrowRight"); kp.keyboard.press("ArrowRight")
            check("tabs: arrow keys move + activate, lazy render, aria-selected", "panel C" in kp.locator("[role=tabpanel]:visible").inner_text() and kp.get_attribute("[role=tab]:has-text('Gamma')", "aria-selected") == "true" and kp.locator("[role=tabpanel]:visible").count() == 1)
            kp.focus(".resizer"); kp.keyboard.press("ArrowLeft"); kp.keyboard.press("ArrowLeft")
            w_ = kp.evaluate("window.__rz.width()")
            check("resizer: keyboard resizes (left-edge handle, ArrowLeft grows), clamps and persists in prefs", w_ == 272 and kp.evaluate("__dev.ctx.prefs.get('panel.kit.side').w") == 272, w_)
            kp.keyboard.press("Enter")
            check("resizer: Enter collapses the panel (inert) and persists", kp.evaluate("window.__rz.isCollapsed()") and kp.evaluate("document.querySelector('.side').inert") and kp.evaluate("__dev.ctx.prefs.get('panel.kit.side').collapsed") is True)
            kp.focus("#tipbtn"); kp.wait_for_timeout(100)
            check("tooltip appears on keyboard focus with aria-describedby", kp.locator(".tooltip").is_visible() and kp.get_attribute("#tipbtn", "aria-describedby") == "cc-tooltip")
            kp.evaluate("() => { __dev.ctx.risk.set({policy: {name: 'kit policy', currency: 'USD', limits: {max_daily_loss: 500}}, usage: {daily_loss: 120.5}}); }")
            check("fmt: money keeps currency, negative uses a true minus, missing is an em dash", kp.evaluate("[__dev.ctx.fmt.money(-3.2), __dev.ctx.fmt.money(1234.5, {currency: 'CAD'}), __dev.ctx.fmt.pct(1.5), __dev.ctx.fmt.pct(-0.25), __dev.ctx.fmt.pct(0.015, {ratio: true}), __dev.ctx.fmt.money(null), __dev.ctx.fmt.qty(12.5)]") == ["\u2212$3.20", "CAD\u00a01,234.50", "+1.50%", "\u22120.25%", "+1.50%", "\u2014", "12.5"])
            check("fmt.time carries an explicit timezone label (EDT/EST and UTC)", kp.evaluate("[__dev.ctx.fmt.time('2026-10-08T18:03:22Z'), __dev.ctx.fmt.time('2026-01-08T18:03:22Z'), __dev.ctx.fmt.time('2026-10-08T18:03:22Z', {tz: 'UTC'})]") == ["2026-10-08 14:03:22 EDT", "2026-01-08 13:03:22 EST", "2026-10-08 18:03:22 UTC"])
            check("NYSE clock: holidays/half-days computed (Thanksgiving 2026-11-26 closed, 2026-11-27 early close, Good Friday 2026-04-03 closed)", kp.evaluate("""async () => { const m = await import('/static/js/core/session.js'); const f = (iso) => m.nyseSession(new Date(iso)); const a = f('2026-11-26T16:00:00Z'), b = f('2026-11-27T17:30:00Z'), c = f('2026-04-03T15:00:00Z'), d = f('2026-10-08T14:00:00Z'), e = f('2026-10-10T15:00:00Z'); return [a.state, a.reason, b.state, b.early, c.state, d.state, e.state, d.verified]; }""") == ["closed", "Thanksgiving Day", "open", True, "closed", "open", "closed", False])
            shot(kp, "kit-harness")
            check("kit harness: no page errors", not kerr, kerr[:3])
            kp.close()

            check("no external network requests (everything same-origin)", not ext, ext[:3])
            real_err = [e for e in errors if not any(k in e for k in ("ERR_CONNECTION", "EventSource", "Failed to load resource", "net::", "500", "workspace research failed", "workspace command_center failed"))]
            check("no page errors / console errors", not real_err, "; ".join(real_err)[:300])
            br.close()
    finally:
        try:
            srv.terminate()
        except Exception:
            pass
    bad = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(bad)}/{len(results)} checks passed")
    (HERE / "screenshots" / "shell_results.json").write_text(json.dumps([{"check": n, "pass": ok, "detail": d} for n, ok, d in results], indent=1))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
