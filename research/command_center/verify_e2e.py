#!/usr/bin/env python3
"""End-to-end browser check with Playwright + the preinstalled Chromium. Uses a TEMP db and port; kills its own server.

  python command_center/verify_e2e.py            # NEW shell UI (served at /): events flow end to end, reload, kill/restart
  python command_center/verify_e2e.py --legacy   # the preserved old UI at /static/legacy/index.html (tower graph etc.)
Shell-specific checks (layout, risk panel, modes, kit) live in verify_shell.py.
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
SHOTS = HERE / "screenshots"
PORT = int(os.environ.get("CC_E2E_PORT", 8791))
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "e2e-token"
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f" – {detail}" if detail else ""))


def start_server(db):
    env = dict(os.environ, CC_DB=db, CC_TOKEN=TOKEN, CC_PORT=str(PORT))
    p = subprocess.Popen([sys.executable, "-m", "command_center.server"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            urllib.request.urlopen(BASE + "/api/health", timeout=1).read()
            return p
        except Exception:
            time.sleep(0.2)
    p.kill()
    raise SystemExit("server did not start")


def emit(*args):
    return subprocess.run([sys.executable, str(HERE / "emit.py"), "--server", BASE, "--token", TOKEN, "--run-id", "demo-run-1", *args],
                          capture_output=True, text=True)


def legacy_main():
    from playwright.sync_api import sync_playwright
    SHOTS.mkdir(exist_ok=True)
    exe = next(iter(sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))), None) or "/opt/pw-browsers/chromium/chrome"
    db = os.path.join(tempfile.mkdtemp(), "e2e.sqlite3")
    srv = start_server(db)
    try:
        subprocess.run([sys.executable, str(HERE / "seed_demo.py"), "--server", BASE, "--token", TOKEN], check=True, capture_output=True)
        with sync_playwright() as pw:
            br = pw.chromium.launch(executable_path=exe, args=["--no-sandbox"])
            ctx = br.new_context(bypass_csp=True, viewport={"width": 1440, "height": 900})
            page = ctx.new_page()
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            ext = []
            page.on("request", lambda r: ext.append(r.url) if not r.url.startswith(BASE) and not r.url.startswith("data:") else None)
            page.goto(BASE + "/static/legacy/index.html")
            page.wait_for_selector("#conn.live", timeout=10000)
            check("connection badge LIVE", True)
            check("7 towers render", page.locator("g.tower").count() == 7, str(page.locator("g.tower").count()))
            check("DEMO banner visible", page.locator("#banner-demo").is_visible())
            check("mode badge present", "MODE: DEMO" in page.locator("#mode-badge").inner_text(), page.locator("#mode-badge").inner_text())
            check("PAPER/LIVE blocked buttons", page.locator("#modebar button", has_text="LIVE (blocked)").count() == 1)
            check("dependency edges dashed (present)", page.locator("path.edge-dep").count() >= 1, str(page.locator("path.edge-dep").count()))
            check("delegation message edges present", page.locator("path.edge-msg").count() >= 2)
            check("no edge pulsing on initial load", page.locator("path.edge-msg.pulse").count() == 0)
            page.wait_for_timeout(500)
            page.screenshot(path=str(SHOTS / "01-overview-demo.png"))
            page.locator("g.tower[data-agent=data_ibkr]").click()
            page.wait_for_selector("#panel h2")
            txt = page.locator("#panel").inner_text()
            check("side panel states reasoning not shown", "Private reasoning is not shown" in txt)
            check("side panel lists tool activity/artifact", "read_file" in txt and "docs/DATA_REQUEST.md" in txt)
            page.screenshot(path=str(SHOTS / "02-agent-panel.png"))
            # keyboard
            page.locator("g.tower[data-agent=lead]").focus()
            page.keyboard.press("ArrowDown")
            check("keyboard arrow moves focus", page.evaluate("document.activeElement.dataset.agent") == "backtester", page.evaluate("document.activeElement.dataset.agent"))
            page.keyboard.press("Enter")
            check("keyboard Enter opens panel", "Backtester" in page.locator("#panel h2").inner_text())
            for tab in ["Task board", "Dependencies", "Search", "Controls & approvals"]:
                page.get_by_role("tab", name=tab).click()
                page.wait_for_timeout(150)
                page.screenshot(path=str(SHOTS / f"03-tab-{tab.split()[0].lower()}.png"))
            # live message animates an edge
            page.get_by_role("tab", name="Activity feed").click()
            r = emit("--type", "message.sent", "--agent", "lead", "--recipient", "backtester", "--source", "emit", "--preview", "e2e live delegation check", "--kind", "delegation")
            check("emit.py accepted", r.returncode == 0, r.stdout.strip()[:120])
            try:
                page.wait_for_selector('path.edge-msg.pulse[data-edge="lead>backtester"]', timeout=4000)
                check("emitted message.sent pulses lead>backtester edge", True)
                page.screenshot(path=str(SHOTS / "04-edge-pulse.png"))
            except Exception as e:
                check("emitted message.sent pulses lead>backtester edge", False, str(e)[:80])
            page.wait_for_timeout(500)
            check("feed shows live event", "e2e live delegation check" in page.locator(".feed").first.inner_text())
            page.wait_for_timeout(3500)
            check("pulse ends after a few seconds", page.locator("path.edge-msg.pulse").count() == 0)
            # reload restores
            seq_before = page.evaluate("window.__cc.snap.seq")
            page.reload(); page.wait_for_selector("#conn.live", timeout=10000); page.wait_for_selector("g.tower")
            page.wait_for_function("window.__cc.snap && window.__cc.snap.seq >= %d" % seq_before)
            check("reload restores same seq + live event", page.evaluate("window.__cc.snap.seq") == seq_before and page.evaluate("window.__cc.events.some(e=>e.payload.preview==='e2e live delegation check')"))
            # replay
            page.get_by_role("button", name="Replay this run").click()
            page.wait_for_timeout(800)
            check("replay: mode badge REPLAY", "MODE: REPLAY" in page.locator("#mode-badge").inner_text())
            page.screenshot(path=str(SHOTS / "05-replay.png"))
            page.get_by_role("button", name="Exit replay").click()
            # other screens
            for nav in ["Research library", "Backtest lab", "Execution console", "Audit / settings"]:
                page.get_by_role("link", name=nav).click(); page.wait_for_timeout(500)
                page.screenshot(path=str(SHOTS / ("06-" + nav.split()[0].lower() + ".png")))
            page.get_by_role("link", name="Execution console").click(); page.wait_for_timeout(300)
            check("Execution console says not implemented + blocked", "Not implemented in this slice" in page.locator("#view").inner_text() and "blocked: requires approval & broker adapter" in page.locator("#view").inner_text())
            page.get_by_role("link", name="Research library").click(); page.wait_for_timeout(500)
            check("Research library lists docs", page.locator("#view table tbody tr").count() >= 1, str(page.locator("#view table tbody tr").count()))
            page.get_by_role("link", name="Overview").click(); page.wait_for_selector("g.tower")
            # kill server -> STALE
            srv.send_signal(signal.SIGTERM); srv.wait(5)
            page.wait_for_function("document.querySelector('#conn').classList.contains('reconnecting') || document.querySelector('#conn').classList.contains('stale')", timeout=8000)
            check("server killed -> UI leaves LIVE (reconnecting)", True, page.locator("#conn").inner_text())
            page.screenshot(path=str(SHOTS / "07-reconnecting.png"))
            page.wait_for_function("document.querySelector('#conn').classList.contains('stale')", timeout=30000)
            banner = page.locator("#banner-stale").inner_text()
            check("STALE banner text", banner.startswith("STALE: showing last known state as of ") and len(banner.split()[-1]) == 8, banner)
            check("STALE greys the UI", page.evaluate("document.body.classList.contains('stale')"))
            page.screenshot(path=str(SHOTS / "08-stale.png"))
            # restart -> recovers
            srv = start_server(db)
            page.wait_for_function("document.querySelector('#conn').classList.contains('live')", timeout=20000)
            check("server restart -> UI returns to LIVE without reload", True)
            check("no uncaught page errors", not [e for e in errors if "ERR_CONNECTION" not in e and "EventSource" not in e and "Failed to load resource" not in e], "; ".join(errors)[:200])
            check("no external network requests", not ext, str(ext[:3]))
            br.close()
    finally:
        srv.terminate()
    bad = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(bad)}/{len(results)} checks passed")
    (SHOTS / "e2e_results.json").write_text(json.dumps([{"check": n, "pass": ok, "detail": d} for n, ok, d in results], indent=1))
    return 1 if bad else 0


def main():
    """E2E for the new shell: seeded + emitted events reach the UI, survive reload, and the stream recovers after a server restart."""
    from playwright.sync_api import sync_playwright
    out = SHOTS / "after"
    out.mkdir(parents=True, exist_ok=True)
    exe = next(iter(sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))), None) or "/opt/pw-browsers/chromium/chrome"
    db = os.path.join(tempfile.mkdtemp(), "e2e.sqlite3")
    srv = start_server(db)
    try:
        subprocess.run([sys.executable, str(HERE / "seed_demo.py"), "--server", BASE, "--token", TOKEN], check=True, capture_output=True)
        with sync_playwright() as pw:
            br = pw.chromium.launch(executable_path=exe, args=["--no-sandbox"])
            page = br.new_context(bypass_csp=True, viewport={"width": 1440, "height": 900}).new_page()
            errors, ext = [], []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("request", lambda r: ext.append(r.url) if not r.url.startswith(BASE) and not r.url.startswith("data:") else None)
            page.goto(BASE + "/?cc_stale_after=4#/agents")
            page.wait_for_function("window.__cc && __cc.ctx.events.state().conn === 'live'", timeout=10000)
            page.wait_for_selector(".tbl-row", timeout=8000)
            check("stream LIVE and agents screen lists seeded events", page.locator(".tbl-row").count() >= 10, str(page.locator(".tbl-row").count()))
            check("mode label is DEMO for seeded-only events", page.locator("#mode-value").inner_text() == "DEMO")
            r = emit("--type", "message.sent", "--agent", "lead", "--recipient", "backtester", "--source", "emit", "--preview", "e2e live delegation check", "--kind", "delegation")
            check("emit.py accepted", r.returncode == 0, r.stdout.strip()[:120])
            try:
                page.wait_for_function("document.querySelector('#workspace').textContent.includes('e2e live delegation check')", timeout=5000)
                check("emitted event appears live in the UI", True)
            except Exception as e:
                check("emitted event appears live in the UI", False, str(e)[:80])
            page.screenshot(path=str(out / "e2e-agents-live.png"))
            seq_before = page.evaluate("__cc.ctx.events.state().lastSeq")
            page.reload()
            page.wait_for_function("window.__cc && __cc.ctx.events.state().conn === 'live' && __cc.ctx.events.state().lastSeq >= %d" % seq_before, timeout=10000)
            check("reload restores the same seq and the live event", page.evaluate("__cc.ctx.events.state().lastSeq") == seq_before and page.evaluate("__cc.ctx.events.recent(600).some(e => e.payload.preview === 'e2e live delegation check')"))
            srv.send_signal(signal.SIGTERM); srv.wait(5)
            page.wait_for_function("__cc.ctx.events.state().conn === 'stale'", timeout=20000)
            check("server killed -> STALE banner with last-known time", "Event stream STALE" in page.locator(".banner-stale").inner_text(), page.locator(".banner-stale").inner_text())
            page.screenshot(path=str(out / "e2e-stale.png"))
            srv = start_server(db)
            page.wait_for_function("__cc.ctx.events.state().conn === 'live'", timeout=20000)
            check("server restart -> back to LIVE without reload", not page.locator(".banner-stale").is_visible())
            check("no uncaught page errors", not [e for e in errors if not any(k in e for k in ("ERR_CONNECTION", "EventSource", "Failed to load resource", "net::"))], "; ".join(errors)[:200])
            check("no external network requests", not ext, str(ext[:3]))
            br.close()
    finally:
        srv.terminate()
    bad = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(bad)}/{len(results)} checks passed")
    (SHOTS / "e2e_shell_results.json").write_text(json.dumps([{"check": n, "pass": ok, "detail": d} for n, ok, d in results], indent=1))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(legacy_main() if "--legacy" in sys.argv else main())
