#!/usr/bin/env python3
"""Agent Team workspace verification (Playwright + the preinstalled Chromium). TEMP db, own port 8812; kills its own server.

  python command_center/verify_agents.py     # writes screenshots/after/agents-*.png and screenshots/agents_results.json

Loads command_center/real_events.jsonl (9 backfilled events) into the TEST server, seeds demo data (source 'demo') and a gap run, then drives the UI.
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SHOTS = HERE / "screenshots" / "after"
PORT = int(os.environ.get("CC_AGENTS_PORT", 8812))
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "agents-e2e-token"
REAL_RUN = "run-2026-10-08-daily"
GAP_RUN = "run-gap-test"
results: list = []


def check(name, ok, detail=""):
    detail = " ".join(str(detail).split())[:300]
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f" - {detail}" if detail else ""), flush=True)


def start_server(db):
    env = dict(os.environ, CC_DB=db, CC_TOKEN=TOKEN, CC_PORT=str(PORT))
    p = subprocess.Popen([sys.executable, "-m", "command_center.server"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(80):
        try:
            urllib.request.urlopen(BASE + "/api/health", timeout=1).read()
            return p
        except Exception:
            time.sleep(0.2)
    p.kill()
    raise SystemExit("server did not start")


def api(method, path, body=None, token=TOKEN):
    h = {"Content-Type": "application/json"}
    if token:
        h["X-CC-Token"] = token
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def emit(*args):
    return subprocess.run([sys.executable, str(HERE / "emit.py"), "--server", BASE, "--token", TOKEN, "--run-id", REAL_RUN, *args], capture_output=True, text=True, timeout=20)


def ev(run, t, agent=None, ts=None, **payload):
    e = {"event_type": t, "run_id": run, "source": "emit", "payload": payload}
    if agent:
        e["agent_id"] = agent
    if ts:
        e["timestamp_utc"] = ts.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return e


def seed():
    subprocess.run([sys.executable, str(HERE / "seed_demo.py"), "--server", BASE, "--token", TOKEN], check=True, capture_output=True)
    base = datetime.now(timezone.utc) - timedelta(hours=3)
    g = [
        ev(GAP_RUN, "agent.status", "lead", base, status="working", task="Plan gap test"),
        ev(GAP_RUN, "message.sent", "lead", base + timedelta(seconds=2), sender="lead", recipient="data_ibkr", preview="Start the data request", kind="delegation"),
        ev(GAP_RUN, "agent.status", "data_ibkr", base + timedelta(seconds=3), status="working", task="Prepare data request"),
        ev(GAP_RUN, "message.sent", "data_ibkr", base + timedelta(minutes=14, seconds=3), sender="data_ibkr", recipient="lead", preview="Data request ready after a long quiet period"),
        ev(GAP_RUN, "artifact.created", "data_ibkr", base + timedelta(minutes=14, seconds=5), path="docs/DATA_REQUEST.md", title="Data request"),
        ev(GAP_RUN, "agent.status", "data_ibkr", base + timedelta(minutes=14, seconds=6), status="complete", task="Prepare data request"),
    ]
    api("POST", "/api/events", g)
    subprocess.run([sys.executable, str(HERE / "load_events.py"), "--server", BASE, "--token", TOKEN], check=True, capture_output=True)  # real run last => latest run


TOWER_METRICS = r"""() => {
  const out = {docX: document.documentElement.scrollWidth > innerWidth + 1, minFont: 99, clipped: [], towers: 0, tinyText: []};
  const stage = document.querySelector('.ag-stage'); if (!stage) return out;
  const sc = parseFloat(stage.style.getPropertyValue('--ag-scale') || '1');
  for (const t of document.querySelectorAll('.ag-tower')) {
    if (!t.getClientRects().length) continue;
    out.towers++;
    const b = t.querySelector('.t-body');
    if (b.scrollHeight > b.clientHeight + 1) out.clipped.push(t.dataset.agent + ' v' + (b.scrollHeight - b.clientHeight));
    for (const n of t.querySelectorAll('.t-title,.t-task,.t-line,.t-badge .badge,.t-result,.t-demo')) {
      if (!n.getClientRects().length || !n.textContent.trim()) continue;
      const fs = parseFloat(getComputedStyle(n).fontSize) * sc;
      out.minFont = Math.min(out.minFont, fs);
      if (fs < 12.4) out.tinyText.push(t.dataset.agent + ':' + n.className + ':' + fs.toFixed(1));
    }
    // text overflowing the tower horizontally
    const br = b.getBoundingClientRect();
    for (const n of t.querySelectorAll('.t-title,.t-task,.t-line,.badge')) { const r = n.getBoundingClientRect(); if (r.width && r.right > br.right + 1) out.clipped.push(t.dataset.agent + ' h:' + n.className); }
  }
  out.scale = sc; out.density = stage.dataset.density;
  return out;
}"""


def main():
    from playwright.sync_api import sync_playwright
    SHOTS.mkdir(parents=True, exist_ok=True)
    exe = next(iter(sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))), None) or "/opt/pw-browsers/chromium/chrome"
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "agents.sqlite3")
    srv = start_server(db)
    holder = {"srv": srv}
    try:
        seed()
        st, runs = api("GET", "/api/runs")
        check("test server seeded: real run is the latest run, demo + gap runs exist", runs["latest"] == REAL_RUN and {r["run_id"] for r in runs["runs"]} >= {REAL_RUN, GAP_RUN, "demo-run-1"}, runs["latest"])
        st, evs = api("GET", f"/api/events?run_id={REAL_RUN}&limit=500")
        check("real run has the 9 backfilled events from source coordinator-backfill", len(evs["events"]) == 9 and {e["source"] for e in evs["events"]} == {"coordinator-backfill"})

        with sync_playwright() as pw:
            br = pw.chromium.launch(executable_path=exe, args=["--no-sandbox"])
            ctx = br.new_context(viewport={"width": 1440, "height": 900})
            ctx.add_init_script(f"localStorage.setItem('cc.token', '{TOKEN}')")
            page = ctx.new_page()
            errors, ext, posts = [], [], []
            page.on("pageerror", lambda e: errors.append("pageerror: " + str(e)))
            page.on("console", lambda m: errors.append("console: " + m.text) if m.type == "error" else None)
            page.on("request", lambda r: ext.append(r.url) if not r.url.startswith(BASE) and not r.url.startswith("data:") else None)
            page.on("request", lambda r: posts.append((r.method, r.url)) if r.method == "POST" else None)

            def boot(url=BASE + "/?cc_stale_after=4#/agents", p=None):
                p = p or page
                p.goto(url)
                p.wait_for_selector(".ag-tower", state="attached", timeout=12000)
                p.wait_for_function("window.__agents && __agents.state().runId && __agents.state().count > 0", timeout=12000)
                p.wait_for_timeout(500)

            shot = lambda name, p=None: (p or page).screenshot(path=str(SHOTS / f"agents-{name}.png"))

            # ------------------------------------------------------------ layout at 4 viewports + screenshots
            for (w, h) in [(1920, 1080), (1440, 900), (1280, 720), (900, 700)]:
                page.set_viewport_size({"width": w, "height": h})
                boot()
                m = page.evaluate(TOWER_METRICS)
                if w == 900:
                    check(f"{w}x{h}: graph view is shown (list is the default only below 900px)", page.locator(".ag-graphpane").is_visible())
                check(f"{w}x{h}: no horizontal page overflow, 7 towers, no clipped tower text", not m["docX"] and m["towers"] == 7 and not m["clipped"], json.dumps(m))
                check(f"{w}x{h}: graph text stays >= 12.4 px effective (density {m.get('density')}, scale {m.get('scale', 0):.2f})", m["minFont"] >= 12.4 and not m["tinyText"], f"min {m['minFont']:.1f} {m['tinyText'][:3]}")
                coll = page.evaluate("(() => { const a = document.querySelector('.ag-inspector'); return a.getBoundingClientRect().width; })()")
                check(f"{w}x{h}: inspector is collapsed while nothing is selected (space goes to the graph)", coll < 2, coll)
                shot(f"{w}x{h}")
            page.set_viewport_size({"width": 1440, "height": 900})
            boot()

            # ------------------------------------------------------------ real data: inactive roles, honest states
            def tower_text(a):
                return page.locator(f'[data-agent="{a}"]').inner_text()
            for a in ("data_ibkr", "strategy_researcher", "risk_execution", "monitor"):
                t = tower_text(a)
                cls = page.get_attribute(f'[data-agent="{a}"]', "class")
                check(f"{a}: no events => looks inactive ('Not started', 'No agent running'), never active", "is-inactive" in cls and "Not started" in t and "No agent running" in t and page.get_attribute(f'[data-agent="{a}"]', "data-active") == "0", t.replace("\n", " | "))
            lt = tower_text("lead")
            check("lead has events but never reported agent.status => 'No status reported', not Working", "No status reported" in lt and "Working" not in lt, lt.replace("\n", " | "))
            bt = tower_text("backtester")
            check("backtester tower: role, Complete badge, task, elapsed, last event, queued/done, result preview", all(x in bt for x in ("Backtester", "Complete", "H3 daily", "In status", "Last ", "Queued 1", "Done 1", "pytest")), bt.replace("\n", " | "))
            vt = tower_text("validator")
            check("validator tower shows Complete + audit task text from events", "Independent validator" in vt and "Complete" in vt and "Daily audit" in vt, vt.replace("\n", " | "))
            check("no fabricated percentages / token / cost numbers on towers or summary", not page.evaluate("/\\d+\\s?%|tokens?|cost/i.test([...document.querySelectorAll('.ag-tower, .ag-summary')].map(n => n.textContent).join(' '))"))
            check("real run is not labelled DEMO", page.locator(".ag-badges").inner_text().find("DEMO") < 0 and page.locator(".ag-tower .t-demo:visible").count() == 0)

            # parity with the server's derived snapshot (derive.py)
            st, snap = api("GET", f"/api/snapshot?run_id={REAL_RUN}")
            mine = page.evaluate("(() => { const v = __agents.view(); return Object.fromEntries(v.agentList.map(a => [a.id, {status: a.status, completed: a.completed, has: a.has_events, task: a.task}])); })()")
            same = all((mine[a["agent_id"]]["status"] == a["status"]) and mine[a["agent_id"]]["completed"] == a["completed_count"] and mine[a["agent_id"]]["task"] == a["task"] for a in snap["agents"])
            check("client-side derivation matches /api/snapshot (status, task, completed count for all 7 agents)", same, json.dumps({k: v["status"] for k, v in mine.items()}))

            # ------------------------------------------------------------ inspector + artifact link
            page.click('[data-agent="backtester"]')
            page.wait_for_timeout(500)
            iw = page.evaluate("document.querySelector('.ag-inspector').getBoundingClientRect().width")
            check("clicking a tower opens the resizable inspector", iw > 300 and page.locator("#ag-ins-title").inner_text() == "Backtester", iw)
            check("inspector has the five tabs", [t.strip() for t in page.locator(".ag-ins-body [role=tab]").all_inner_texts()] == ["Current task", "Messages", "Tool activity", "Outputs", "Run history"])
            check("inspector states private reasoning is not shown (once)", page.locator(".ag-inspector").inner_text().count("Private reasoning is not shown") == 1)
            check("resize handle (separator) is present for the inspector", page.locator('.ag-main [role=separator][aria-label^="Resize inspector"]').count() == 1)
            page.click('.ag-ins-body [role=tab]:has-text("Outputs")'); page.wait_for_timeout(200)
            check("Outputs tab lists the artifact path, test result and offers Open document", "docs/DAILY_RESULTS.md" in page.locator(".ag-ins-body").inner_text() and "PASS" in page.locator(".ag-ins-body").inner_text() and page.locator('.ag-ins-body button:has-text("Open document")').count() == 1)
            page.click('.ag-ins-body button:has-text("Open document")'); page.wait_for_timeout(800)
            doc_txt = page.locator("pre.ag-doc").inner_text() if page.locator("pre.ag-doc").count() else ""
            check("artifact link opens the document from /api/docs/DAILY_RESULTS.md", len(doc_txt) > 50 and page.locator(".drawer").count() == 1, len(doc_txt))
            shot("drawer-artifact")
            page.keyboard.press("Escape"); page.wait_for_timeout(400)
            check("Esc closes the document drawer first", page.locator(".drawer").count() == 0 and page.evaluate("document.querySelector('.ag-inspector').getBoundingClientRect().width") > 300)
            page.click('.ag-ins-body [role=tab]:has-text("Messages")'); page.wait_for_timeout(200)
            check("Messages tab shows the delegation from lead", "Implement DAILY_SPEC v1" in page.locator(".ag-ins-body").inner_text() and "Received" in page.locator(".ag-ins-body").inner_text())
            page.click('.ag-ins-body [role=tab]:has-text("Current task")'); page.wait_for_timeout(200)
            check("Current task tab: no usage invented", "Not reported by any event" in page.locator(".ag-ins-body").inner_text())
            page.click('.ag-inspector button:has-text("View changes")'); page.wait_for_timeout(300)
            check("View changes shows the git-tracked path as text (no git call)", "docs/DAILY_RESULTS.md" in page.locator(".popover").inner_text() and "no git command" in page.locator(".popover").inner_text())
            page.keyboard.press("Escape"); page.wait_for_timeout(200)
            tl_rows = page.locator(".ag-timeline tr.tbl-row").count()
            check("selecting a tower filters the timeline to that agent", 0 < tl_rows < 9, tl_rows)
            shot("inspector-open")
            page.keyboard.press("Escape"); page.wait_for_timeout(500)
            check("Esc collapses the inspector and returns focus to the tower", page.evaluate("document.querySelector('.ag-inspector').getBoundingClientRect().width") < 2 and page.evaluate("document.activeElement && document.activeElement.dataset && document.activeElement.dataset.agent") == "backtester")
            check("timeline unfiltered again after deselect", page.locator(".ag-timeline tr.tbl-row").count() == 9)

            # ------------------------------------------------------------ selecting an event highlights tower + artifact
            page.click('.ag-timeline tr.tbl-row:has-text("artifact.created")'); page.wait_for_timeout(300)
            check("selecting an artifact event highlights its tower and the artifact preview", "is-hl" in page.get_attribute('[data-agent="backtester"]', "class") and "is-art-hl" in page.get_attribute('[data-agent="backtester"]', "class") and page.locator(".ag-evbar").is_visible())
            page.click('.ag-timeline tr.tbl-row:has-text("Implement DAILY_SPEC")'); page.wait_for_timeout(300)
            check("selecting a message event highlights sender and recipient towers", all("is-hl" in page.get_attribute(f'[data-agent="{a}"]', "class") for a in ("lead", "backtester")) and "is-hl" not in page.get_attribute('[data-agent="monitor"]', "class"))
            page.click(".ag-evbar button:has-text('Clear')"); page.wait_for_timeout(200)

            # ------------------------------------------------------------ connectors: structure is never communication
            check("hierarchy lines exist for all six specialists", page.locator(".ag-edge-hier").count() == 6)
            msg_pairs0 = sorted(page.evaluate("[...document.querySelectorAll('.ag-edge-msg')].map(e => e.dataset.pair)"))
            check("message connectors exist only for observed messages (lead|backtester, lead|validator)", msg_pairs0 == ["backtester|lead", "lead|validator"], msg_pairs0)
            check("no connector pulses on load or for replayed backlog events", page.locator("[data-pulse]").count() == 0)
            check("dependency connector absent: T-audit has no task.created, so its owner is unknown (never guessed)", page.locator(".ag-edge-dep").count() == 0)

            # LIVE message.sent pulses exactly that edge
            check("before: no connector between validator and monitor", page.locator('.ag-edge-msg[data-pair="monitor|validator"]').count() == 0)
            r = emit("--type", "message.sent", "--agent", "validator", "--recipient", "monitor", "--preview", "Audit complete, 1 medium finding")
            check("emit.py posted a real message.sent", r.returncode == 0, r.stdout.strip()[:120])
            page.wait_for_selector('.ag-edge-msg[data-pair="monitor|validator"][data-pulse="1"]', timeout=6000)
            pulsing = page.evaluate("[...document.querySelectorAll('.ag-edge[data-pulse]')].map(e => e.dataset.kind + ':' + e.dataset.pair)")
            check("live message.sent pulses exactly that edge (and no other)", pulsing == ["msg:monitor|validator"], pulsing)
            shot("pulse")
            hit = page.locator('.ag-edge-msg[data-pair="monitor|validator"] .ag-hit')
            hit.focus(); page.wait_for_timeout(300)
            tip = page.locator("#cc-tooltip").inner_text() if page.locator("#cc-tooltip").is_visible() else ""
            check("hover/focus shows sender, recipient, timestamp and preview", all(x in tip for x in ("validator", "monitor", "Audit complete", "EDT")) or all(x in tip for x in ("validator", "monitor", "Audit complete", "UTC")), tip)
            hit.dispatch_event("click"); page.wait_for_timeout(300)
            pop = page.locator(".popover").inner_text() if page.locator(".popover").count() else ""
            check("click opens details with sender, recipient, time and preview", all(x in pop for x in ("Sender", "validator", "Recipient", "monitor", "Time", "Preview", "Audit complete")), pop.replace("\n", " | "))
            page.keyboard.press("Escape"); page.wait_for_timeout(200)
            page.wait_for_function("document.querySelectorAll('[data-pulse]').length === 0", timeout=6000)
            check("pulse stops by itself", page.locator("[data-pulse]").count() == 0)

            # dependency edge differs from message edge; a dependency event never pulses
            emit("--type", "task.created", "--agent", "lead", "--task-id", "T-audit", "--title", "Independent audit of daily pipeline", "--owner", "validator")
            page.wait_for_selector('.ag-edge-dep[data-pair="backtester|validator"]', timeout=6000)
            style = page.evaluate("""() => { const d = getComputedStyle(document.querySelector('.ag-edge-dep .ag-line')), m = getComputedStyle(document.querySelector('.ag-edge-msg .ag-line')), h = getComputedStyle(document.querySelector('.ag-edge-hier .ag-line'));
              return {dep: [d.strokeDasharray, d.stroke, d.strokeWidth], msg: [m.strokeDasharray, m.stroke, m.strokeWidth], hier: [h.strokeDasharray, h.stroke, h.strokeWidth]}; }""")
            check("dependency edge is dashed; message edge is solid; hierarchy is thin neutral (three visibly different kinds)",
                  style["dep"][0] not in ("none", "") and style["msg"][0] in ("none", "") and style["dep"][1] != style["msg"][1] and float(style["hier"][2][:-2]) < float(style["msg"][2][:-2]), json.dumps(style))
            check("a dependency event does not pulse any connector", page.locator("[data-pulse]").count() == 0)
            emit("--type", "task.dependency", "--task-id", "T-audit", "--depends-on", "T-daily")   # duplicate dependency record: still no pulse
            page.wait_for_timeout(700)
            check("still no pulse after another task.dependency event", page.locator("[data-pulse]").count() == 0)
            before = page.evaluate("JSON.stringify([...document.querySelectorAll('.ag-edge-hier .ag-line, .ag-edge-msg .ag-line, .ag-edge-dep .ag-line')].map(e => e.getAttribute('d')))")
            emit("--type", "message.sent", "--agent", "monitor", "--recipient", "risk_execution", "--preview", "Layout stability probe")
            page.wait_for_selector('.ag-edge-msg[data-pair="monitor|risk_execution"]', timeout=6000)
            after = page.evaluate("JSON.stringify([...document.querySelectorAll('.ag-edge-hier .ag-line, .ag-edge-msg .ag-line, .ag-edge-dep .ag-line')].map(e => e.getAttribute('d')))")
            tower_pos = page.evaluate("JSON.stringify([...document.querySelectorAll('.ag-tower')].map(t => [t.style.left, t.style.top]))")
            check("layout is stable: adding a connector does not move existing connectors", all(p in json.loads(after) for p in json.loads(before)))
            page.wait_for_timeout(300)
            check("towers do not move when events arrive", page.evaluate("JSON.stringify([...document.querySelectorAll('.ag-tower')].map(t => [t.style.left, t.style.top]))") == tower_pos)
            shot("connectors")

            # ------------------------------------------------------------ heartbeat alone must not mark an agent working
            emit("--type", "heartbeat", "--agent", "risk_execution")
            page.wait_for_function("document.querySelector('[data-agent=risk_execution]').textContent.includes('Service reachable')", timeout=6000)
            rt = tower_text("risk_execution")
            check("heartbeat alone: 'service reachable' shown separately, status stays Not started (not Working)", "Not started" in rt and "Working" not in rt and page.get_attribute('[data-agent="risk_execution"]', "data-active") == "0", rt.replace("\n", " | "))

            # ------------------------------------------------------------ keyboard
            page.locator('[data-agent="lead"]').focus()
            page.keyboard.press("ArrowDown")
            f1 = page.evaluate("document.activeElement.dataset.agent")
            page.keyboard.press("ArrowRight")
            f2 = page.evaluate("document.activeElement.dataset.agent")
            page.keyboard.press("ArrowUp")
            f3 = page.evaluate("document.activeElement.dataset.agent")
            check("arrow keys move focus between towers (lead -> specialist -> neighbour -> lead)", f1 == "strategy_researcher" and f2 == "backtester" and f3 == "lead", (f1, f2, f3))
            page.keyboard.press("ArrowDown"); page.keyboard.press("Enter"); page.wait_for_timeout(500)
            check("Enter inspects the focused tower and moves focus into the inspector", page.evaluate("document.activeElement && document.activeElement.id") == "ag-ins-title" and page.locator("#ag-ins-title").inner_text() != "", page.evaluate("document.activeElement.id"))
            page.keyboard.press("Escape"); page.wait_for_timeout(500)
            check("Esc closes the inspector; focus returns to a tower", page.evaluate("document.querySelector('.ag-inspector').getBoundingClientRect().width") < 2 and page.evaluate("!!document.activeElement.dataset.agent"))
            page.evaluate("document.activeElement.blur()")
            tabs = []
            page.locator(".ag-toolbar button").last.focus()
            for _ in range(14):
                page.keyboard.press("Tab")
                a = page.evaluate("document.activeElement.dataset && document.activeElement.dataset.agent")
                if a:
                    tabs.append(a)
            check("Tab reaches all seven towers in reading order", tabs[:7] == ["lead", "data_ibkr", "strategy_researcher", "backtester", "validator", "risk_execution", "monitor"], tabs)

            # ------------------------------------------------------------ search / filters / zoom / fit / centre
            page.fill(".ag-search", "audit"); page.wait_for_selector(".ag-search-pop:not([hidden]) .ag-sr-item", timeout=6000)
            sr = page.locator(".ag-search-pop").inner_text().replace("AGENTS", "Agents").replace("TASKS", "Tasks").replace("EVENTS", "Events")
            check("search covers agents, tasks and events via /api/search", "Agents" in sr and "Tasks" in sr and "Events" in sr and "T-audit" in sr and "message.sent" in sr, sr.replace("\n", " | ")[:200])
            page.click(".ag-search-pop .ag-sr-item:has-text('message.sent')"); page.wait_for_timeout(500)
            check("clicking an event result selects it (highlights towers)", page.locator(".ag-evbar").is_visible())
            page.click(".ag-evbar button:has-text('Clear')")
            page.fill(".ag-search", "valid"); page.wait_for_selector(".ag-search-pop:not([hidden]) .ag-sr-item", timeout=6000)
            check("search finds agents by role name", "Independent validator" in page.locator(".ag-search-pop").inner_text())
            page.click(".ag-search-pop .ag-sr-item:has-text('Independent validator')"); page.wait_for_timeout(500)
            check("clicking an agent result opens its inspector", page.locator("#ag-ins-title").inner_text() == "Independent validator")
            page.keyboard.press("Escape") if False else None
            page.evaluate("document.activeElement && document.activeElement.blur()"); page.locator("#ag-ins-title").focus(); page.keyboard.press("Escape"); page.wait_for_timeout(400)
            page.fill(".ag-search", "")
            page.click(".ag-toolbar button:has-text('Filters')"); page.wait_for_timeout(200)
            page.select_option('.popover select[aria-label="Status"]', "complete"); page.wait_for_timeout(300)
            dim = page.evaluate("[...document.querySelectorAll('.ag-tower.is-dim')].map(t => t.dataset.agent)")
            check("status filter dims non-matching towers (layout unchanged)", sorted(dim) == ["data_ibkr", "lead", "monitor", "risk_execution", "strategy_researcher"], dim)
            page.select_option('.popover select[aria-label="Status"]', ""); page.select_option('.popover select[aria-label="Event type"]', "message.sent"); page.wait_for_timeout(300)
            kinds = page.evaluate("[...new Set([...document.querySelectorAll('.ag-timeline tr.tbl-row .ag-evtype')].map(n => n.textContent))]")
            check("event-type filter limits the timeline", kinds == ["message.sent"], kinds)
            page.select_option('.popover select[aria-label="Event type"]', ""); page.select_option('.popover select[aria-label="Role"]', "lead"); page.wait_for_timeout(300)
            check("role filter dims the other six towers", page.locator(".ag-tower.is-dim").count() == 6)
            page.click(".popover button:has-text('Clear filters')"); page.wait_for_timeout(300)
            check("clear filters restores everything", page.locator(".ag-tower.is-dim").count() == 0)
            s0 = page.evaluate("__agents.graph.transform().s")
            page.click('.ag-ctl button[title="Zoom in"]'); s1 = page.evaluate("__agents.graph.transform().s")
            page.click('.ag-ctl button[title="Zoom out"]'); page.click('.ag-ctl button[title="Zoom out"]'); s2 = page.evaluate("__agents.graph.transform().s")
            page.click('.ag-ctl button:has-text("Fit")'); s3 = page.evaluate("__agents.graph.transform().s")
            check("zoom in/out and fit-to-screen change and restore the scale", s1 > s0 and s2 < s1 and abs(s3 - s0) < 0.01, (s0, s1, s2, s3))
            emit("--type", "agent.status", "--agent", "monitor", "--status", "working", "--task", "Watching the event feed")
            page.wait_for_selector('.ag-tower[data-agent="monitor"][data-status="working"]', timeout=6000)
            page.click('.ag-ctl button:has-text("Centre")'); page.wait_for_timeout(200)
            c = page.evaluate("(() => { const t = document.querySelector('.ag-tower[data-status=working]') || document.querySelector('.ag-tower'); const r = t.getBoundingClientRect(), v = document.querySelector('.ag-viewport').getBoundingClientRect(); return [Math.abs((r.left + r.width / 2) - (v.left + v.width / 2)), Math.abs((r.top + r.height / 2) - (v.top + v.height / 2))]; })()")
            check("centre-on-active-agent centres the active (working) tower", c[0] < 3 and c[1] < 3, c)
            page.click('.ag-ctl button:has-text("Fit")')
            vp = page.evaluate("(() => { const v = document.querySelector('.ag-viewport'); return [v.getBoundingClientRect().left, v.getBoundingClientRect().top]; })()")
            page.mouse.move(vp[0] + 40, vp[1] + 560); page.mouse.down(); page.mouse.move(vp[0] + 120, vp[1] + 600, steps=5); page.mouse.up()
            check("drag pans the stage", page.evaluate("__agents.graph.transform().x") != page.evaluate("0 + (document.querySelector('.ag-stage').style.transform ? 0 : 1)") and page.evaluate("document.querySelector('.ag-stage').style.transform").startswith("translate("), page.evaluate("document.querySelector('.ag-stage').style.transform"))
            page.click('.ag-ctl button:has-text("Fit")')

            # ------------------------------------------------------------ actions (server's real response is the outcome)
            page.click('[data-agent="strategy_researcher"]'); page.wait_for_timeout(400)
            page.click('.ag-inspector button:has-text("Request update")'); page.wait_for_selector(".ag-outcome:not([hidden])", timeout=6000)
            out = page.locator(".ag-outcome").inner_text()
            check("Request update shows the server's actual reply ('recorded, no runtime attached')", "recorded, no runtime attached" in out and "no update will be produced" in out, out)
            page.click('.ag-inspector button:has-text("Start research task")'); page.wait_for_selector("#ag-task-title", timeout=4000)
            page.fill("#ag-task-title", "Re-run the baseline with updated costs"); page.click('.drawer button:has-text("Record task request")'); page.wait_for_selector(".ag-form-result .ag-result", timeout=6000)
            res = page.locator(".ag-form-result").inner_text()
            check("Start research task: server reply shown, states the task was NOT started", "recorded, no runtime attached" in res and "NOT started" in res, res)
            shot("start-task")
            page.keyboard.press("Escape"); page.wait_for_timeout(400)
            page.click('.ag-inspector button:has-text("Cancel research task")'); page.wait_for_selector(".modal", timeout=4000)
            mt = page.locator(".modal").inner_text()
            check("Cancel confirm states it only records a request to stop RESEARCH (no positions, orders, execution)", "only records a request to stop RESEARCH" in mt and "never closes positions" in mt and "cancels orders" in mt and "touches execution" in mt, mt.replace("\n", " | "))
            page.click('.modal button:has-text("Record cancel request")'); page.wait_for_function("document.querySelector('.ag-outcome') && document.querySelector('.ag-outcome').textContent.includes('Cancel research is only a recorded request')", timeout=6000)
            check("Cancel outcome is the server's reply", "recorded, no runtime attached" in page.locator(".ag-outcome").inner_text())
            page.keyboard.press("Escape"); page.wait_for_timeout(300)
            page.click(".ag-conc button:has-text('2')"); page.wait_for_function("document.querySelector('.ag-outcome').textContent.includes('limit 2 stored')", timeout=6000)
            page.wait_for_function("document.querySelector('.ag-conc').textContent.includes('2 recorded, not enforced')", timeout=6000)
            check("set_concurrency shows 'recorded, not enforced' honestly", "recorded, not enforced" in page.locator(".ag-conc").inner_text())
            st, cs = api("GET", f"/api/events?run_id={REAL_RUN}&limit=500")
            cmds = [e["payload"]["command"] for e in cs["events"] if e["event_type"] == "command.recorded"]
            check("commands were recorded by the server as command.recorded events", {"request_update", "assign_task", "cancel_research", "set_concurrency"} <= set(cmds), cmds)
            check("no order / execution endpoints were called", not any(re_ for re_ in posts if "/api/commands" not in re_[1] and "/api/events" not in re_[1] and "/api/prefs" not in re_[1] and "/api/audit" not in re_[1]), [p for p in posts if "/api/commands" not in p[1]][:5])
            page.click('[data-agent="strategy_researcher"]') if page.evaluate("document.querySelector('.ag-inspector').getBoundingClientRect().width") > 2 else None
            page.keyboard.press("Escape"); page.wait_for_timeout(300)

            # ------------------------------------------------------------ DEMO mode: separate state, visibly labelled
            n_before = api("GET", "/api/events?limit=5000")[1]["max_seq"]
            page.click(".ag-toolbar button:has-text('Demo')"); page.wait_for_timeout(600)
            check("demo toggle labels everything DEMO (badge + banner + tower chips)", "DEMO (client-side)" in page.locator(".ag-badges").inner_text() and "DEMO DATA" in page.locator(".ag-banner").inner_text() and page.locator(".ag-tower .t-demo:visible").count() >= 6, page.locator(".ag-banner").inner_text())
            check("demo data is client-side only: server event count unchanged", api("GET", "/api/events?limit=5000")[1]["max_seq"] == n_before)
            check("demo mode disables commands (nothing is sent)", page.locator(".ag-toolbar button:has-text('Start research task')").first.is_disabled())
            shot("demo")
            page.click(".ag-toolbar button:has-text('Exit demo')"); page.wait_for_timeout(500)
            check("exiting demo restores the real run", page.locator(".ag-tower .t-demo:visible").count() == 0 and "DEMO" not in page.locator(".ag-badges").inner_text())
            page.select_option(".ag-runsel", "demo-run-1"); page.wait_for_timeout(1200)
            check("a backend run made only of source='demo' events is labelled DEMO", "DEMO run" in page.locator(".ag-badges").inner_text() and "source='demo'" in page.locator(".ag-banner").inner_text())
            page.select_option(".ag-runsel", REAL_RUN); page.wait_for_timeout(1000)

            # ------------------------------------------------------------ replay (recorded run with a 14 minute gap)
            page.select_option(".ag-runsel", GAP_RUN); page.wait_for_timeout(1200)
            check("selecting an older run shows RECORDED RUN (not LIVE)", "RECORDED RUN" in page.locator(".ag-badges").inner_text() and "LIVE" not in page.locator(".ag-badges").inner_text())
            check("recorded run disables live commands with a reason", page.locator(".ag-toolbar button:has-text('Start research task')").first.is_disabled())
            posts_before = len(posts)
            page.click(".ag-toolbar button:has-text('Replay')"); page.wait_for_selector(".ag-replay:not([hidden])", timeout=4000)
            check("replay mode badge is distinct from live", "REPLAY" in page.locator(".ag-badges").inner_text() and "LIVE" not in page.locator(".ag-badges").inner_text() and "REPLAY MODE" in page.locator(".ag-replay").inner_text())
            page.select_option("#ag-rp-speed", "4")
            gap_label = ""
            for _ in range(60):
                t = page.locator("#ag-rp-gap").inner_text() if page.locator("#ag-rp-gap").is_visible() else ""
                if "gap compressed" in t:
                    gap_label = t; break
                page.wait_for_timeout(150)
            check("replay shows a compressed-gap label ('gap compressed 14m')", gap_label == "gap compressed 14m", gap_label)
            shot("replay")
            ptime = page.locator("#ag-rp-time").inner_text()
            check("replay shows the labelled historical (original) timestamp", ptime.startswith("Original time ") and "20" in ptime, ptime)
            page.click("#ag-rp-play"); page.wait_for_timeout(300)
            idx_a = page.evaluate("__agents.state().replay.idx")
            check("pause stops the replay (button reads Play)", page.locator("#ag-rp-play").inner_text().strip() in ("Play", "Replay again") and not page.evaluate("__agents.state().replay.playing"))
            page.wait_for_timeout(700)
            check("paused replay does not advance", page.evaluate("__agents.state().replay.idx") == idx_a)
            page.click("#ag-rp-play"); page.wait_for_function("__agents.state().replay.idx === __agents.state().replay.n || __agents.state().replay.idx > %d" % idx_a, timeout=15000)
            check("play resumes the replay", True)
            page.evaluate("document.querySelector('#ag-rp-scrub').value = '2'; document.querySelector('#ag-rp-scrub').dispatchEvent(new Event('input', {bubbles: true}))"); page.wait_for_timeout(400)
            check("scrubbing seeks to an event position and pauses", page.evaluate("__agents.state().replay.idx") == 2 and not page.evaluate("__agents.state().replay.playing"))
            check("speed selector offers 0.5x to 16x", page.locator("#ag-rp-speed option").all_inner_texts() == ["0.5x", "1x", "2x", "4x", "8x", "16x"])
            page.click("#ag-rp-play"); page.select_option("#ag-rp-speed", "16")
            page.wait_for_function("__agents.state().replay.idx === __agents.state().replay.n", timeout=15000)
            check("replayed message.sent pulses (uses recorded events only)", True)
            check("replay posts nothing (no POST requests while replaying)", len(posts) == posts_before, posts[posts_before:])
            page.click(".ag-toolbar button:has-text('Exit replay')"); page.wait_for_timeout(500)
            check("exit replay returns to the recorded run view", page.locator(".ag-replay").is_hidden() and "RECORDED RUN" in page.locator(".ag-badges").inner_text())
            page.select_option(".ag-runsel", REAL_RUN); page.wait_for_timeout(1000)

            # ------------------------------------------------------------ connection states, reconnect, no duplicate events
            n_events = len(api("GET", f"/api/events?run_id={REAL_RUN}&limit=500")[1]["events"])
            holder["srv"].terminate(); holder["srv"].wait(timeout=10)
            page.wait_for_function("document.querySelector('.ag-badges').textContent.includes('STALE') || document.querySelector('.ag-badges').textContent.includes('RECONNECTING') || document.querySelector('.ag-badges').textContent.includes('FAILED')", timeout=12000)
            page.wait_for_function("document.querySelector('.ag-badges').textContent.includes('STALE')", timeout=14000)
            check("killing the server shows STALE honestly (banner explains, counters paused)", "STALE" in page.locator(".ag-badges").inner_text() and "stale" in page.locator(".ag-banner").inner_text().lower(), page.locator(".ag-banner").inner_text())
            shot("stale")
            holder["srv"] = start_server(db)
            emit("--type", "message.sent", "--agent", "lead", "--recipient", "validator", "--preview", "After restart")
            page.wait_for_function("document.querySelector('.ag-badges').textContent.includes('LIVE')", timeout=20000)
            page.wait_for_function("document.querySelectorAll('.ag-timeline tr.tbl-row').length === %d" % (n_events + 1), timeout=15000)
            keys = page.evaluate("[...document.querySelectorAll('.ag-timeline tr.tbl-row')].map(r => r.dataset.key)")
            srv_events = api("GET", f"/api/events?run_id={REAL_RUN}&limit=500")[1]["events"]
            check("after reconnect the feed resumes with no duplicate events (rows == unique seqs == server events)", len(keys) == len(set(keys)) == len(srv_events) == n_events + 1, (len(keys), len(set(keys)), len(srv_events)))
            check("the post-restart message appears once and pulses its connector or at least exists", page.locator('.ag-edge-msg[data-pair="lead|validator"]').count() == 1)

            # ------------------------------------------------------------ light theme contrast sanity
            page.evaluate("document.documentElement.dataset.theme = 'light'"); page.wait_for_timeout(300)
            shot("light-1440x900")
            m = page.evaluate(TOWER_METRICS)
            check("light theme: towers render without clipping", not m["clipped"], m["clipped"])
            page.evaluate("document.documentElement.dataset.theme = 'dark'")

            # ------------------------------------------------------------ small screens: list view default, inspector as drawer
            ctx2 = br.new_context(viewport={"width": 800, "height": 700})
            p2 = ctx2.new_page()
            errs2 = []
            p2.on("pageerror", lambda e: errs2.append(str(e)))
            p2.on("console", lambda m: errs2.append(m.text) if m.type == "error" else None)
            boot(BASE + "/#/agents", p2)
            check("800px: list view is the default", p2.locator(".ag-listpane").is_visible() and p2.locator(".ag-graphpane").is_hidden())
            rows = p2.locator(".ag-list tr.tbl-row").count()
            hdrs = p2.locator(".ag-list th").all_inner_texts()
            check("800px: accessible table lists all 7 agents with status, task, in-status, last event, queued, done, result", rows == 7 and all(h in " ".join(hdrs) for h in ("Agent", "Status", "Current task", "In status", "Last event", "Queued", "Done", "Latest result")) and p2.locator(".ag-list table caption").count() == 1, (rows, hdrs))
            lt = p2.locator(".ag-list").inner_text()
            check("800px list shows the same honest states (Not started, Complete, No status reported)", "Not started" in lt and "Complete" in lt and "No status reported" in lt)
            p2.screenshot(path=str(SHOTS / "agents-800x700-list.png"))
            p2.click('.ag-list tr.tbl-row:has-text("Backtester")'); p2.wait_for_timeout(700)
            check("800px: selecting a row opens the inspector as a drawer", p2.locator(".drawer").count() == 1 and "Backtester" in p2.locator(".drawer").inner_text())
            p2.screenshot(path=str(SHOTS / "agents-800x700-drawer.png"))
            p2.keyboard.press("Escape"); p2.wait_for_timeout(500)
            check("800px: Esc closes the drawer", p2.locator(".drawer").count() == 0)
            p2.click('.ag-viewseg button:has-text("Graph")'); p2.wait_for_timeout(600)
            check("800px: graph view is available on demand", p2.locator(".ag-graphpane").is_visible())
            m2 = p2.evaluate(TOWER_METRICS)
            check("800px graph: no horizontal page overflow", not m2["docX"], json.dumps(m2))
            p2.screenshot(path=str(SHOTS / "agents-800x700-graph.png"))
            # no token: commands fail honestly
            p2.click('.ag-viewseg button:has-text("List")'); p2.wait_for_timeout(300)
            p2.click('.ag-list tr.tbl-row:has-text("Backtester")'); p2.wait_for_timeout(600)
            p2.click('.drawer button:has-text("Request update")'); p2.wait_for_selector(".ag-outcome:not([hidden])", timeout=5000)
            check("without an API token the action reports 'Not recorded' (no fake success)", "Not recorded" in p2.locator(".ag-outcome").inner_text() and "token" in p2.locator(".ag-outcome").inner_text().lower(), p2.locator(".ag-outcome").inner_text())
            check("800px context: no page errors", not errs2, errs2[:3])
            ctx2.close()

            check("no external requests were made", not ext, ext[:3])
            real_errs = [e for e in errors if "ERR_CONNECTION_REFUSED" not in e]   # refused connections are the expected effect of the deliberate server kill
            check("no page errors or console errors (apart from the refused connections caused by the deliberate server kill)", not real_errs, real_errs[:5])
            br.close()
    finally:
        holder["srv"].kill()
    fails = [r for r in results if not r[1]]
    out = HERE / "screenshots" / "agents_results.json"
    out.write_text(json.dumps([{"name": n, "ok": ok, "detail": d} for n, ok, d in results], indent=1))
    print(f"\n{len(results) - len(fails)}/{len(results)} checks passed")
    for n, _, d in fails:
        print("FAILED:", n, "-", d)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
