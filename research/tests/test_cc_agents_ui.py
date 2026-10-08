"""Agent Team workspace: static contract (no innerHTML with data, no external URLs, tokens only, no order paths) and pure-function
unit tests (derive / layout / replay / navigation) executed with node when it is available."""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STATIC = ROOT / "command_center" / "static"
AGENT_JS = sorted((STATIC / "js" / "agents").glob("*.js")) + [STATIC / "js" / "workspaces" / "agents.js"]
CSS = STATIC / "css" / "agents.css"
NODE = shutil.which("node") or ("/opt/node22/bin/node" if Path("/opt/node22/bin/node").exists() else None)
need_node = pytest.mark.skipif(NODE is None, reason="node not available")


def _src(p):
    return p.read_text(encoding="utf-8")


def _code_only(text):
    """Strip // line comments and /* */ blocks so prose in comments cannot trip the contract checks."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(re.sub(r"(^|[^:\"'`])//.*$", r"\1", ln) for ln in text.splitlines())


def test_agents_files_exist():
    names = {p.name for p in AGENT_JS}
    assert {"agents.js", "model.js", "layout.js", "graph.js", "panels.js", "inspector.js", "actions.js", "replay.js", "demo.js", "common.js"} <= names
    assert CSS.exists()


def test_workspace_module_contract():
    src = _src(STATIC / "js" / "workspaces" / "agents.js")
    assert re.search(r'id:\s*"agents"', src) and "mount(" in src and "unmount()" in src and "export default" in src
    assert "/static/css/agents.css" in src, "CSS must be loaded dynamically from the module"
    assert "_placeholder" not in src


@pytest.mark.parametrize("path", AGENT_JS, ids=lambda p: p.name)
def test_no_html_injection_apis(path):
    code = _code_only(_src(path))
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "createContextualFragment", "DOMParser", "eval(", "new Function", "srcdoc"):
        assert bad not in code, f"{path.name}: {bad} is forbidden (event text is untrusted)"
    assert not re.search(r"""setAttribute\(\s*["']on""", code)
    assert not re.search(r"\.\s*on(click|error|load)\s*=", code)


@pytest.mark.parametrize("path", AGENT_JS + [CSS], ids=lambda p: p.name)
def test_no_external_urls_or_hardcoded_colours(path):
    code = _code_only(_src(path))
    assert not re.search(r"https?://", code), f"{path.name}: external URL"
    assert "//cdn" not in code and "@import" not in code
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", code), f"{path.name}: hard-coded hex colour (use tokens.css variables)"
    assert not re.search(r"\brgba?\(|\bhsla?\(", code), f"{path.name}: hard-coded colour function (use tokens)"


def test_css_uses_only_token_variables():
    css = _src(CSS)
    tokens = _src(STATIC / "css" / "tokens.css") + _src(STATIC / "css" / "base.css")
    own = set(re.findall(r"(--ag-[a-z0-9-]+|--tc)\s*:", css)) | {"--ag-avail"}   # --ag-avail is set from JS (visible workspace height)
    for v in set(re.findall(r"var\((--[a-z0-9-]+)", css)):
        assert v in own or f"{v}:" in tokens or f"{v} :" in tokens, f"{v} is not defined in tokens.css/base.css"


@pytest.mark.parametrize("path", AGENT_JS, ids=lambda p: p.name)
def test_no_order_path_or_broker(path):
    code = _code_only(_src(path)).lower()
    for bad in ("place_order", "submit_order", "createorder", "create_order", "/api/orders", "ib_insync", "ibapi", "websocket(", "xmlhttprequest"):
        assert bad not in code, f"{path.name}: {bad}"
    # the only mutating endpoint this workspace may call
    for m in re.finditer(r"""api\.post\(\s*["'`]([^"'`]+)""", code):
        assert m.group(1) == "/api/commands", m.group(1)


def test_commands_used_are_research_only():
    src = _src(STATIC / "js" / "agents" / "actions.js")
    used = set(re.findall(r'send\("([a-z_]+)"', src)) | set(re.findall(r'send\(\s*"([a-z_]+)"', src))
    assert used <= {"assign_task", "request_update", "cancel_research", "approve_proposal", "set_concurrency"}, used
    assert "never closes positions" in src and "cancels orders" in src and "touches execution" in src


def test_replay_module_posts_nothing():
    code = _code_only(_src(STATIC / "js" / "agents" / "replay.js"))
    assert "fetch(" not in code and "/api/" not in code and "ctx" not in code and "post(" not in code.lower() and "import" in code and code.count("import ") == 1  # imports only ./model.js


def test_unmount_cleans_up_listeners():
    src = _src(STATIC / "js" / "workspaces" / "agents.js")
    for needle in ("removeEventListener", "clearTimeout", "disconnect()", "dispose()", "cancelAnimationFrame", "document.querySelector(\"link[data-agents-css]\")?.remove()"):
        assert needle in src, needle


# --------------------------------------------------------------------------------------------------------------------------- node unit tests
def run_node(script, payload=None):
    wrapper = f"""
import {{ pathToFileURL }} from 'node:url';
const A = (n) => import(pathToFileURL('{STATIC}/js/agents/' + n + '.js').href);
const input = JSON.parse(process.env.PAYLOAD || 'null');
const out = await (async () => {{ {script} }})();
console.log(JSON.stringify(out));
"""
    import os
    env = dict(os.environ, PAYLOAD=json.dumps(payload))
    r = subprocess.run([NODE, "--input-type=module", "-e", wrapper], capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@need_node
def test_layout_is_orthogonal_and_has_no_shared_segments_between_kinds():
    out = run_node("""
      const { makeLayout, createLaneBook, sharedSegmentLength } = await A('layout');
      const res = {};
      for (const d of ['full', 'compact']) {
        const L = makeLayout(d); const book = createLaneBook(L); const routes = [];
        for (const k of ['msg', 'dep']) for (const [key, r] of Object.entries(L.routes[k])) routes.push([k + ':' + key, r.pts]);
        for (const [a, b] of L.pairs) for (const k of ['m', 'd']) routes.push([k + 'bot:' + a + b, book.assign(k, a, b).pts]);
        let nonOrtho = 0, shared = 0;
        for (const [, pts] of routes) for (let i = 0; i < pts.length - 1; i++) if (pts[i][0] !== pts[i+1][0] && pts[i][1] !== pts[i+1][1]) nonOrtho++;
        for (let i = 0; i < routes.length; i++) for (let j = i + 1; j < routes.length; j++) if (sharedSegmentLength(routes[i][1], routes[j][1]) > 0.5) shared++;
        const hierVsOther = [];
        for (const h of Object.values(L.routes.hier)) for (const [n, pts] of routes) if (sharedSegmentLength(h.pts, pts) > 0.5) hierVsOther.push(n);
        res[d] = { nonOrtho, shared, hierVsOther, n: routes.length, size: L.size };
      }
      return res;""")
    for d in ("full", "compact"):
        assert out[d]["nonOrtho"] == 0, "routes must be orthogonal"
        assert out[d]["shared"] == 0, "message/dependency connectors must never share a collinear segment"
        assert out[d]["hierVsOther"] == [], "hierarchy lines must not overlap message/dependency lines"
        assert out[d]["n"] == 6 * 2 + 1 + 15 * 2


@need_node
def test_layout_is_static_and_lanes_are_sticky():
    out = run_node("""
      const { makeLayout, createLaneBook } = await A('layout');
      const a = JSON.stringify(makeLayout('full').routes), b = JSON.stringify(makeLayout('full').routes);
      const L = makeLayout('full');
      const book1 = createLaneBook(L), book2 = createLaneBook(L);
      const first = book1.assign('m', 'backtester', 'validator').d;
      book1.assign('m', 'data_ibkr', 'monitor'); book1.assign('d', 'strategy_researcher', 'risk_execution'); book1.assign('m', 'validator', 'monitor');
      const again = book1.assign('m', 'validator', 'backtester').d;
      const alone = book2.assign('m', 'backtester', 'validator').d;
      return { deterministic: a === b, sticky: first === again, independentOfLaterEdges: first === alone, lanesUsed: book1.lanesUsed(), size: L.size };""")
    assert out["deterministic"] and out["sticky"] and out["independentOfLaterEdges"]
    assert out["lanesUsed"] >= 2


@need_node
def test_fit_center_zoom_and_keyboard_neighbours():
    out = run_node("""
      const { makeLayout, fitTransform, centerOnTransform, zoomAt, neighbor } = await A('layout');
      const L = makeLayout('full');
      const fit = fitTransform(L.size, 1160, 620), small = fitTransform(L.size, 500, 300);
      const c = centerOnTransform(L.nodes.backtester, fit, 1160, 620);
      const z = zoomAt(fit, 1.2, 580, 310);
      return { fit, small, centred: [Math.round(L.nodes.backtester.cx * c.s + c.x), Math.round(L.nodes.backtester.cy * c.s + c.y)],
        zoomKeepsPoint: [Math.round((580 - fit.x) / fit.s), Math.round((580 - z.x) / z.s)],
        nav: [neighbor('lead', 'down'), neighbor('backtester', 'left'), neighbor('backtester', 'right'), neighbor('backtester', 'up'), neighbor('data_ibkr', 'left'), neighbor('monitor', 'right')] };""")
    assert out["fit"]["s"] <= 1 and out["small"]["s"] >= 0.8, "text never shrinks below the readable floor"
    assert out["centred"] == [580, 310]
    assert abs(out["zoomKeepsPoint"][0] - out["zoomKeepsPoint"][1]) <= 1
    assert out["nav"] == ["strategy_researcher", "strategy_researcher", "validator", "lead", "data_ibkr", "monitor"]


REAL = [json.loads(line) for line in (ROOT / "command_center" / "real_events.jsonl").read_text().splitlines() if line.strip()]


def _with_seq(events):
    return [dict(e, seq=i + 1) for i, e in enumerate(events)]


@need_node
def test_derive_matches_server_derive_on_real_events():
    from command_center.derive import snapshot
    evs = _with_seq(REAL)
    snap = snapshot(evs, "run-2026-10-08-daily", len(evs))
    out = run_node("const { derive } = await A('model'); const v = derive(input); return { agents: Object.fromEntries(v.agentList.map(a => [a.id, { status: a.status, task: a.task, completed: a.completed, has: a.has_events }])), messages: v.messages.length, tasks: v.tasks.map(t => [t.task_id, t.owner, t.status]), deps: v.deps.length };", evs)
    for a in snap["agents"]:
        mine = out["agents"][a["agent_id"]]
        assert mine["status"] == a["status"] and mine["task"] == a["task"] and mine["completed"] == a["completed_count"], a["agent_id"]
    assert out["messages"] == len(snap["messages"])
    assert sorted(out["tasks"]) == sorted([t["task_id"], t["owner"], t["status"]] for t in snap["tasks"])
    assert out["deps"] == len(snap["dependencies"])


@need_node
def test_roles_without_events_are_inactive_and_heartbeat_is_not_work():
    evs = _with_seq(REAL)
    base = max(e["seq"] for e in evs)
    evs.append({"seq": base + 1, "event_id": "hb1", "event_type": "heartbeat", "timestamp_utc": "2026-10-08T05:00:00.000Z", "run_id": "r", "agent_id": "risk_execution", "source": "emit", "payload": {}})
    out = run_node("""const { derive, displayStatus } = await A('model'); const v = derive(input);
      return { ds: Object.fromEntries(v.agentList.map(a => [a.id, displayStatus(a)])), hb: v.agents.risk_execution.last_heartbeat, usage: v.agents.backtester.usage, counts: v.counts };""", evs)
    assert out["ds"]["data_ibkr"] == "not_started" and out["ds"]["monitor"] == "not_started"
    assert out["ds"]["risk_execution"] == "not_started", "a heartbeat alone must never mark an agent as working or started"
    assert out["hb"] == "2026-10-08T05:00:00.000Z"
    assert out["ds"]["lead"] == "no_status", "lead has events but never reported agent.status"
    assert out["ds"]["backtester"] == "complete" and out["ds"]["validator"] == "complete"
    assert out["usage"] is None, "no usage unless an event supplies it"


@need_node
def test_usage_only_when_supplied_and_estimates_are_labelled():
    ev = {"seq": 1, "event_id": "u1", "event_type": "agent.status", "timestamp_utc": "2026-10-08T05:00:00.000Z", "run_id": "r", "agent_id": "backtester", "source": "emit",
          "payload": {"status": "working", "task": "t", "usage": {"input_tokens": 1200, "estimated": True}}}
    out = run_node("const { derive, usageRows } = await A('model'); const v = derive(input); return { rows: usageRows(v.agents.backtester.usage), none: usageRows(null), lead: usageRows(v.agents.lead.usage) };", [ev])
    assert out["rows"] == [{"key": "input_tokens", "value": 1200, "estimate": True}] and out["none"] is None and out["lead"] is None


@need_node
def test_edge_kinds_are_separate_and_structure_is_not_communication():
    base = {"run_id": "r", "source": "emit", "payload_version": 1}
    evs = [
        dict(base, seq=1, event_id="a", event_type="task.created", timestamp_utc="2026-10-08T05:00:00.000Z", agent_id="lead", payload={"task_id": "T1", "title": "one", "owner": "backtester"}),
        dict(base, seq=2, event_id="b", event_type="task.created", timestamp_utc="2026-10-08T05:00:01.000Z", agent_id="lead", payload={"task_id": "T2", "title": "two", "owner": "validator"}),
        dict(base, seq=3, event_id="c", event_type="task.dependency", timestamp_utc="2026-10-08T05:00:02.000Z", agent_id=None, payload={"task_id": "T2", "depends_on": "T1"}),
        dict(base, seq=4, event_id="d", event_type="task.dependency", timestamp_utc="2026-10-08T05:00:03.000Z", agent_id=None, payload={"task_id": "T3", "depends_on": "T1"}),   # T3 unknown
        dict(base, seq=5, event_id="e", event_type="message.sent", timestamp_utc="2026-10-08T05:00:04.000Z", agent_id="lead", payload={"sender": "lead", "recipient": "monitor", "preview": "hi"}),
    ]
    out = run_node("""const { derive, messagePairs, dependencyPairs } = await A('model'); const v = derive(input);
      const m = [...messagePairs(v.messages).keys()], d = [...dependencyPairs(v.deps).values()].map(g => [g.key, g.dirs.size, [...g.dirs][0]]);
      return { m, d, depRows: v.deps.length };""", evs)
    assert out["m"] == ["lead|monitor"], "only the message produces a message connector"
    assert out["d"] == [["backtester|validator", 1, "ab"]], "the dependency with an unknown owner is listed but never drawn"
    assert out["depRows"] == 2


@need_node
def test_replay_schedule_compresses_long_gaps_and_labels_them():
    evs = [{"timestamp_utc": t} for t in ("2026-10-08T05:00:00.000Z", "2026-10-08T05:00:02.000Z", "2026-10-08T05:14:02.000Z", "2026-10-08T05:14:03.000Z", "2026-10-08T05:14:03.000Z")]
    out = run_node("""const { buildSchedule, gapLabel, createPlayer } = await A('replay'); const s = buildSchedule(input);
      let now = 0, t = null, fired = [];
      const p = createPlayer({ events: input, onChange: (st, rev) => fired.push([st.idx, rev.length, st.gapMs]), now: () => now, setTimer: (f) => { t = f; return 1; }, clearTimer: () => { t = null; } });
      p.play(); for (let i = 0; i < 400 && t; i++) { now += 60; const f = t; t = null; f(); }
      return { at: s.at, gap: s.gap, total: s.total, label: gapLabel(840000), done: p.state().idx, playing: p.state().playing, sawGap: fired.some(f => f[2] === 840000) };""", evs)
    assert out["at"][:2] == [0, 2000] and out["gap"][2] == 840000 and out["at"][2] == 3200, "14 minutes compress to 1.2 s of playback"
    assert out["label"] == "gap compressed 14m"
    assert out["at"][4] == out["at"][3], "events with equal timestamps reveal together"
    assert out["done"] == 5 and out["playing"] is False and out["sawGap"]


@need_node
def test_demo_events_are_labelled_and_client_side():
    out = run_node("const { demoEvents, DEMO_RUN_ID } = await A('demo'); const e = demoEvents(0); return { n: e.length, sources: [...new Set(e.map(x => x.source))], runs: [...new Set(e.map(x => x.run_id))], run: DEMO_RUN_ID, labelled: e.filter(x => x.event_type !== 'heartbeat' && x.event_type !== 'mode.changed' && JSON.stringify(x.payload).includes('DEMO')).length };")
    assert out["sources"] == ["demo"] and out["runs"] == [out["run"]] and out["n"] > 20 and out["labelled"] > 15


@need_node
def test_doc_link_only_for_served_docs():
    out = run_node("const m = await A('model'); return [m.docLinkFor('docs/DAILY_RESULTS.md'), m.docLinkFor('research/docs/X.md'), m.docLinkFor('results/run.csv'), m.docLinkFor('https://evil.example/a.md'), m.docLinkFor('docs/../../etc/passwd.md'), m.resultLinkFor('results/daily/2026.md')];")
    assert out == ["/api/docs/DAILY_RESULTS.md", "/api/docs/X.md", None, None, None, "/api/results/2026.md"]
