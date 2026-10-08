// Pure model for the Agent Team workspace: derive display state ONLY from backend events (no DOM, no ctx, no network).
// The same function serves live, recorded, replay (events.slice(0, n)) and demo (client-side seed) so they cannot diverge.
// It mirrors command_center/derive.py (single source of truth on the server) and adds per-agent views for the UI.

export const AGENT_IDS = ["lead", "data_ibkr", "strategy_researcher", "backtester", "validator", "risk_execution", "monitor"];
export const SPECIALISTS = AGENT_IDS.slice(1);

export const ROLE = {
  lead: { name: "Coordinator", short: "Lead", blurb: "Plans the research, delegates tasks, records decisions." },
  data_ibkr: { name: "Stock-data specialist", short: "Data", blurb: "Prepares read-only market-data requests (IBKR connector)." },
  strategy_researcher: { name: "Strategy researcher", short: "Strategy", blurb: "Drafts and refines the written strategy specification." },
  backtester: { name: "Backtester", short: "Backtester", blurb: "Runs the spec on historical data; train/validation only." },
  validator: { name: "Independent validator", short: "Validator", blurb: "Audits pipeline, splits and costs independently." },
  risk_execution: { name: "Risk / Execution engineer", short: "Risk", blurb: "Research-side risk checks. No order path exists." },
  monitor: { name: "Monitoring", short: "Monitor", blurb: "Watches the event feed and raises incidents." },
};

export const AGENT_STATUSES = ["queued", "working", "awaiting_dependency", "awaiting_approval", "idle", "failed", "complete"];
export const TASK_STATUSES = ["queued", "working", "awaiting_dependency", "awaiting_approval", "failed", "complete"];
export const EVENT_TYPES = ["agent.status", "task.created", "task.updated", "task.dependency", "message.sent", "tool.activity", "artifact.created",
  "test.result", "decision.summary", "incident", "mode.changed", "approval.requested", "approval.resolved", "heartbeat", "command.recorded", "audit.config"];

/** Status -> display. kind maps to ui.badge kinds; icon names exist in core/icons.js. Shape (icon) differs per status. */
export const STATUS_META = {
  queued: { label: "Queued", kind: "neutral", icon: "clock" },
  working: { label: "Working", kind: "info", icon: "radio" },
  awaiting_dependency: { label: "Waiting on dependency", kind: "warn", icon: "pause" },
  awaiting_approval: { label: "Awaiting approval", kind: "warn", icon: "flag" },
  idle: { label: "Idle", kind: "neutral", icon: "dash" },
  failed: { label: "Failed", kind: "bad", icon: "x_circle" },
  complete: { label: "Complete", kind: "ok", icon: "check" },
  no_status: { label: "No status reported", kind: "neutral", icon: "info" },
  not_started: { label: "Not started", kind: "neutral", icon: "dash" },
};

export const pairKey = (a, b) => (a < b ? `${a}|${b}` : `${b}|${a}`);
const str = (v) => (typeof v === "string" ? v : v == null ? "" : String(v));
const clip = (s, n) => { s = str(s).replace(/\s+/g, " ").trim(); return s.length > n ? s.slice(0, n - 1) + "…" : s; };
export const tms = (iso) => { const t = Date.parse(iso); return Number.isFinite(t) ? t : null; };

/** Plain-text one-line summary of an event (safe: callers must still insert it as TEXT). */
export function summarizeEvent(e, max = 260) {
  const p = e.payload || {};
  switch (e.event_type) {
    case "agent.status": return clip(`${p.status}${p.task ? ": " + p.task : ""}`, max);
    case "task.created": return clip(`Task ${p.task_id} created: ${p.title}${p.owner ? ` (${p.owner})` : ""}`, max);
    case "task.updated": return clip(`Task ${p.task_id}${p.status ? " is now " + p.status : " updated"}${p.owner ? ` (${p.owner})` : ""}`, max);
    case "task.dependency": return clip(`${p.task_id} waits on ${p.depends_on}`, max);
    case "message.sent": return clip(`${p.sender} to ${p.recipient}: ${p.preview}`, max);
    case "tool.activity": return clip(`${p.tool}: ${p.summary}`, max);
    case "artifact.created": return clip(`Artifact ${p.path}${p.title ? " (" + p.title + ")" : ""}`, max);
    case "test.result": return clip(`${str(p.outcome).toUpperCase()}: ${p.name}${p.detail ? " - " + p.detail : ""}`, max);
    case "decision.summary": return clip(p.summary, max);
    case "incident": return clip(`[${p.severity}] ${p.summary}`, max);
    case "mode.changed": return clip(`Mode label ${p.mode}${p.note ? " - " + p.note : ""}`, max);
    case "approval.requested": return clip(`Approval ${p.approval_id} requested: ${p.summary}`, max);
    case "approval.resolved": return clip(`Approval ${p.approval_id} ${p.resolution}${p.note ? " - " + p.note : ""}`, max);
    case "heartbeat": return "Heartbeat (service reachable; not a work status)";
    case "command.recorded": return clip(`${p.command}${p.accepted ? "" : " (rejected)"}: ${p.message}`, max);
    case "audit.config": return clip(`${p.area}.${p.action}${p.target ? " " + p.target : ""}`, max);
    default: return e.event_type;
  }
}

/** Which agents does an event concern (timeline filter by tower). `tasks` is a Map(task_id -> task). */
export function eventAgents(e, tasks) {
  const p = e.payload || {}, out = new Set();
  if (AGENT_IDS.includes(e.agent_id)) out.add(e.agent_id);
  if (e.event_type === "message.sent") { if (AGENT_IDS.includes(p.sender)) out.add(p.sender); if (AGENT_IDS.includes(p.recipient)) out.add(p.recipient); }
  if ((e.event_type === "task.created" || e.event_type === "task.updated") && AGENT_IDS.includes(p.owner)) out.add(p.owner);
  if (e.event_type === "task.dependency" && tasks) for (const id of [p.task_id, p.depends_on]) { const o = tasks.get(id)?.owner; if (AGENT_IDS.includes(o)) out.add(o); }
  if (e.event_type === "approval.requested" && AGENT_IDS.includes(p.requested_by)) out.add(p.requested_by);
  return [...out];
}

function blankAgent(id) {
  return { id, name: ROLE[id].name, short: ROLE[id].short, has_events: false, has_status: false, status: null, task: null, status_since: null,
    last_activity: null, last_activity_seq: null, last_heartbeat: null, queued: 0, completed: 0, working_tasks: 0, blockers: [], usage: null, usage_event: null,
    latest: null, demo: false, real: false, message_count: 0, event_count: 0 };
}

/** derive(events) -> view. `events` must be seq-ascending (as the store returns them). */
export function derive(events) {
  const agents = Object.fromEntries(AGENT_IDS.map((a) => [a, blankAgent(a)]));
  const tasks = new Map(), deps = [], messages = [], artifacts = [], tests = [], decisions = [], incidents = [], commands = [], tools = [];
  const approvals = new Map(), sources = new Set(), statusDone = [], statusChanges = [];
  let mode = null, concurrency = null, firstTs = null, lastTs = null, lastSeq = 0, demoEvents = 0;

  const touch = (a, e) => {
    const ag = agents[a]; if (!ag) return;
    ag.has_events = true; ag.last_activity = e.timestamp_utc; ag.last_activity_seq = e.seq; ag.event_count += 1;
    if (e.source === "demo") ag.demo = true; else ag.real = true;
  };

  for (const e of events) {
    const p = e.payload || {}, t = e.event_type, ts = e.timestamp_utc, aid = e.agent_id;
    sources.add(e.source); if (e.source === "demo") demoEvents += 1;
    if (!firstTs || ts < firstTs) firstTs = ts;
    if (!lastTs || ts > lastTs) lastTs = ts;
    lastSeq = Math.max(lastSeq, e.seq || 0);
    if (t === "agent.status" && agents[aid]) {
      touch(aid, e);
      const ag = agents[aid];
      if (ag.status !== p.status) { ag.status_since = ts; if (p.status === "complete") statusDone.push([aid, p.task, e.seq]); }
      statusChanges.push({ agent: aid, status: p.status, task: p.task || null, ts, seq: e.seq, event_id: e.event_id });
      ag.status = p.status; ag.has_status = true; ag.task = p.task || null; ag.blockers = Array.isArray(p.blockers) ? p.blockers.map(str) : [];
      if (p.usage && typeof p.usage === "object") { ag.usage = p.usage; ag.usage_event = e.event_id; }
    } else if (t === "heartbeat") {
      if (agents[aid]) { agents[aid].last_heartbeat = ts; }
    } else if (t === "task.created") {
      tasks.set(p.task_id, { task_id: p.task_id, title: str(p.title), owner: p.owner || aid || null, status: p.status || "queued", created: ts, updated: ts, demo: e.source === "demo", seq: e.seq, event_id: e.event_id });
      touch(aid, e);
    } else if (t === "task.updated") {
      let tk = tasks.get(p.task_id);
      if (!tk) { tk = { task_id: p.task_id, title: str(p.title) || p.task_id, owner: aid || null, status: "queued", created: ts, updated: ts, demo: e.source === "demo", seq: e.seq, event_id: e.event_id, implicit: true }; tasks.set(p.task_id, tk); }
      for (const k of ["status", "owner", "title"]) if (p[k]) tk[k] = p[k];
      tk.updated = ts; tk.demo = tk.demo || e.source === "demo"; touch(aid, e);
    } else if (t === "task.dependency") {
      deps.push({ task_id: p.task_id, depends_on: p.depends_on, seq: e.seq, ts, demo: e.source === "demo", event_id: e.event_id });
    } else if (t === "message.sent") {
      const m = { seq: e.seq, ts, sender: p.sender, recipient: p.recipient, preview: str(p.preview), kind: p.kind || "message", demo: e.source === "demo", event_id: e.event_id };
      messages.push(m); touch(p.sender, e);
      if (agents[p.sender]) agents[p.sender].message_count += 1;
    } else if (t === "tool.activity") {
      touch(aid, e); tools.push({ seq: e.seq, ts, agent: aid, tool: p.tool, summary: str(p.summary), sources: Array.isArray(p.sources) ? p.sources.map(str) : [], target: p.target || null, event_id: e.event_id, demo: e.source === "demo" });
    } else if (t === "artifact.created") {
      touch(aid, e);
      const a = { seq: e.seq, ts, agent: aid, path: str(p.path), title: p.title || null, url: p.url || null, kind: p.kind || null, event_id: e.event_id, demo: e.source === "demo" };
      artifacts.push(a); if (agents[aid]) agents[aid].latest = { kind: "artifact", text: a.path, sub: a.title, ts, seq: e.seq, event_id: e.event_id };
    } else if (t === "test.result") {
      touch(aid, e);
      const r = { seq: e.seq, ts, agent: aid, name: str(p.name), outcome: p.outcome, detail: p.detail || null, command: p.command || null, event_id: e.event_id, demo: e.source === "demo" };
      tests.push(r); if (agents[aid]) agents[aid].latest = { kind: "test", text: `${str(p.outcome).toUpperCase()}: ${r.name}`, outcome: p.outcome, ts, seq: e.seq, event_id: e.event_id };
    } else if (t === "decision.summary") {
      touch(aid, e);
      const d = { seq: e.seq, ts, agent: aid, summary: str(p.summary), sources: Array.isArray(p.sources) ? p.sources.map(str) : [], event_id: e.event_id, demo: e.source === "demo" };
      decisions.push(d); if (agents[aid]) agents[aid].latest = { kind: "decision", text: d.summary, ts, seq: e.seq, event_id: e.event_id };
    } else if (t === "incident") {
      incidents.push({ seq: e.seq, ts, agent: aid, severity: p.severity, summary: str(p.summary), event_id: e.event_id, demo: e.source === "demo" });
    } else if (t === "mode.changed") {
      mode = p.mode;
    } else if (t === "approval.requested") {
      approvals.set(p.approval_id, { approval_id: p.approval_id, summary: str(p.summary), requested_by: p.requested_by || aid || null, requested_at: ts, resolution: null, demo: e.source === "demo", event_id: e.event_id });
    } else if (t === "approval.resolved") {
      const ap = approvals.get(p.approval_id) || { approval_id: p.approval_id, summary: "(request event not in this run)", requested_by: null, requested_at: null, demo: e.source === "demo" };
      ap.resolution = p.resolution; ap.resolved_at = ts; approvals.set(p.approval_id, ap);
    } else if (t === "command.recorded") {
      commands.push({ seq: e.seq, ts, command: p.command, accepted: !!p.accepted, runtime_attached: !!p.runtime_attached, message: str(p.message), args: p.args || {}, event_id: e.event_id });
      if (p.command === "set_concurrency" && p.accepted) concurrency = p.args?.value ?? concurrency;
    }
  }

  // Completed-count rule identical to derive.py (completed tasks owned + status transitions into complete, de-duplicated by task text).
  const doneTitles = Object.fromEntries(AGENT_IDS.map((a) => [a, new Set()])), counted = Object.fromEntries(AGENT_IDS.map((a) => [a, new Set()]));
  for (const tk of tasks.values()) {
    if (agents[tk.owner] && tk.status === "complete") { agents[tk.owner].completed += 1; doneTitles[tk.owner].add(tk.title); }
  }
  for (const [a, text, seq] of statusDone) {
    const key = text || `#seq${seq}`;
    if (doneTitles[a].has(key) || counted[a].has(key)) continue;
    counted[a].add(key); agents[a].completed += 1;
  }
  for (const tk of tasks.values()) {
    tk.depends_on = [...new Set(deps.filter((d) => d.task_id === tk.task_id).map((d) => d.depends_on))];
    tk.unmet = tk.depends_on.filter((d) => tasks.get(d)?.status !== "complete");
    const ag = agents[tk.owner]; if (!ag) continue;
    if (tk.status === "queued") ag.queued += 1;
    if (tk.status === "working") ag.working_tasks += 1;
    if (tk.status === "awaiting_dependency" && tk.unmet.length) ag.blockers.push(`task ${tk.task_id} waits on: ${tk.unmet.join(", ")}`);
    if (tk.status === "awaiting_approval") ag.blockers.push(`task ${tk.task_id} awaits approval`);
  }
  for (const ag of Object.values(agents)) if (ag.status === "queued" && ag.queued === 0) ag.queued = 1; // the agent itself reports it is queued
  const depEdges = deps.map((d) => ({ ...d, from_agent: tasks.get(d.depends_on)?.owner || null, to_agent: tasks.get(d.task_id)?.owner || null,
    from_status: tasks.get(d.depends_on)?.status || null, to_status: tasks.get(d.task_id)?.status || null,
    from_known: tasks.has(d.depends_on), to_known: tasks.has(d.task_id) }));
  const byAgent = Object.fromEntries(AGENT_IDS.map((a) => [a, []]));
  for (const e of events) for (const a of eventAgents(e, tasks)) byAgent[a].push(e);

  const counts = {};
  for (const ag of Object.values(agents)) {
    const k = !ag.has_events ? "not_started" : ag.status || "no_status";
    counts[k] = (counts[k] || 0) + 1;
  }
  const onlyDemo = events.length > 0 && demoEvents === events.length;
  return { agents, agentList: AGENT_IDS.map((a) => agents[a]), tasks: [...tasks.values()], taskMap: tasks, deps: depEdges, messages, artifacts, tests, decisions, incidents, tools,
    approvals: [...approvals.values()], commands, statusChanges, concurrency, mode, sources: [...sources].sort(), hasDemo: demoEvents > 0, onlyDemo,
    firstTs, lastTs, lastSeq, count: events.length, counts, byAgent };
}

/** Aggregate message events per unordered pair. Direction of the latest message decides the arrow. */
export function messagePairs(messages) {
  const out = new Map();
  for (const m of messages) {
    if (!m.sender || !m.recipient || m.sender === m.recipient) continue;
    const k = pairKey(m.sender, m.recipient);
    let g = out.get(k); if (!g) { g = { key: k, a: m.sender < m.recipient ? m.sender : m.recipient, b: m.sender < m.recipient ? m.recipient : m.sender, items: [] }; out.set(k, g); }
    g.items.push(m);
  }
  for (const g of out.values()) { const last = g.items[g.items.length - 1]; g.last = last; g.count = g.items.length; g.dir = last.sender === g.a ? "ab" : "ba"; }
  return out;
}

/** Dependency edges between AGENTS (derived from task.dependency + task owners). Edges without two known, different owners are NOT drawn:
 *  a dependency is listed in the dependency table but never guessed onto a connector. Direction: provider (depends_on owner) -> dependent. */
export function dependencyPairs(depEdges) {
  const out = new Map();
  for (const d of depEdges) {
    if (!d.from_agent || !d.to_agent || d.from_agent === d.to_agent) continue;
    const k = pairKey(d.from_agent, d.to_agent);
    let g = out.get(k); if (!g) { g = { key: k, a: d.from_agent < d.to_agent ? d.from_agent : d.to_agent, b: d.from_agent < d.to_agent ? d.to_agent : d.from_agent, items: [], dirs: new Set() }; out.set(k, g); }
    g.items.push(d); g.dirs.add(d.from_agent === g.a ? "ab" : "ba");
  }
  return out;
}

/** Elapsed helpers. ref = epoch ms the elapsed time is measured to. */
export function elapsedText(sinceIso, refMs) {
  const t = tms(sinceIso); if (t == null || refMs == null) return null;
  const s = Math.max(0, Math.round((refMs - t) / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
  return `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
}

export function gapText(ms) {
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) { const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60); return m ? `${h}h ${m}m` : `${h}h`; }
  return `${Math.round(s / 86400)}d`;
}

/** Usage is shown ONLY when a payload supplied it. Returns [{key, value, estimate}] (numbers/strings only) or null. */
export function usageRows(usage) {
  if (!usage || typeof usage !== "object") return null;
  const est = !!(usage.estimate || usage.estimated || usage.is_estimate);
  const rows = [];
  for (const [k, v] of Object.entries(usage)) {
    if (/^(estimate|estimated|is_estimate)$/i.test(k)) continue;
    if (typeof v === "number" || typeof v === "string") rows.push({ key: k, value: v, estimate: est || /estimate|approx/i.test(k) });
  }
  return rows.length ? rows : null;
}

/** Display state of an agent for badges: has_events/status -> STATUS_META key. A heartbeat NEVER yields a work status. */
export function displayStatus(ag) {
  if (!ag.has_events) return "not_started";
  return ag.status || "no_status";
}

/** Filters (status, role, event type) used by graph dimming, list, timeline and board. All fields optional ('' / null = any). */
export function agentMatches(ag, f) {
  if (f.role && ag.id !== f.role) return false;
  if (f.status && displayStatus(ag) !== f.status) return false;
  return true;
}

/** Internal path used by artifact links: only docs/<name>.md maps to /api/docs/<name>.md. Everything else is shown as text. */
export function docLinkFor(path) {
  const m = /^(?:research\/)?docs\/([A-Za-z0-9_.\-]+\.md)$/.exec(str(path).trim());
  return m ? `/api/docs/${m[1]}` : null;
}
export function resultLinkFor(path) {
  const m = /^(?:research\/)?results\/daily\/([A-Za-z0-9_.\-]+\.md)$/.exec(str(path).trim());
  return m ? `/api/results/${m[1]}` : null;
}
