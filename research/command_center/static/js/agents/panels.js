// Panels around the graph: run summary, synchronized timeline, task board, dependency list, accessible agent list.
// All text from events is inserted as text nodes (ctx.el / table cells); nothing here parses HTML.
import { AGENT_IDS, ROLE, STATUS_META, TASK_STATUSES, displayStatus, summarizeEvent, eventAgents, elapsedText, agentMatches, tms } from "./model.js";
import { statusBadge, timeLabel, heading, outcomeBadge, sevBadge, plural } from "./common.js";

const STATUS_ORDER = ["working", "awaiting_dependency", "awaiting_approval", "queued", "failed", "complete", "idle", "no_status", "not_started"];

/** Mark the selected row of a ui.table by data-key (rows re-render on setRows). */
function markRow(tbl, key) {
  for (const tr of tbl.el.querySelectorAll("tr.tbl-row")) { const on = key != null && tr.dataset.key === String(key); tr.classList.toggle("is-sel", on); if (on) tr.setAttribute("aria-selected", "true"); else tr.removeAttribute("aria-selected"); }
}

// ------------------------------------------------------------------------------------------------------------------ summary
export function createSummary(ctx, { onSelectEvent, onSelectAgent, onConcurrency, onApproval } = {}) {
  const { el, ui } = ctx;
  const counts = el("div", { class: "ag-chips", role: "list", "aria-label": "Agents by status" });
  const decisions = el("div", { class: "ag-mini-list", role: "list", "aria-label": "Latest decisions" });
  const incidents = el("div", { class: "ag-mini-list", role: "list", "aria-label": "Incidents" });
  const approvals = el("div", { class: "ag-mini-list", role: "list", "aria-label": "Approvals" });
  const conc = el("div", { class: "ag-conc" });
  const root = el("section", { class: "ag-card ag-summary", "aria-label": "Run summary" },
    heading(ctx, "Run summary"), counts,
    el("div", { class: "ag-sum-grid" },
      el("div", {}, el("h4", {}, "Latest decisions"), decisions),
      el("div", {}, el("h4", {}, "Incidents and approvals"), incidents, approvals)),
    conc);
  const row = (ev, kids) => el("div", { class: "ag-mini", role: "listitem" },
    el("button", { type: "button", class: "ag-mini-btn", on: { click: () => onSelectEvent?.(ev) } }, kids));
  return {
    el: root,
    update(v, u) {
      counts.replaceChildren(...STATUS_ORDER.filter((k) => v.counts[k]).map((k) => {
        const m = STATUS_META[k];
        return el("span", { role: "listitem" }, ui.badge(m.kind, `${v.counts[k]} ${m.label}`, m.icon));
      }));
      const evById = (id) => u.eventsById?.get(id);
      const ds = v.decisions.slice(-3).reverse();
      decisions.replaceChildren(...(ds.length ? ds.map((d) => row(evById(d.event_id), [
        el("span", { class: "ag-mini-k" }, `${ROLE[d.agent]?.short || "Run"} · ${timeLabel(ctx, d.ts, u.refMs, { seconds: false })}`), el("span", { class: "ag-mini-v" }, d.summary)]))
        : [el("p", { class: "muted ag-none" }, "No decision.summary events in this run.")]));
      const inc = v.incidents.slice(-2).reverse();
      incidents.replaceChildren(...(inc.length ? inc.map((i) => row(evById(i.event_id), [sevBadge(ctx, i.severity), el("span", { class: "ag-mini-v" }, i.summary)]))
        : [el("p", { class: "muted ag-none" }, "No incidents recorded.")]));
      const pend = v.approvals.filter((a) => !a.resolution);
      approvals.replaceChildren(...(pend.length ? pend.slice(0, 2).map((a) => el("div", { class: "ag-mini ag-appr", role: "listitem" },
        ui.badge("warn", "Approval pending", "flag"), el("span", { class: "ag-mini-v" }, a.summary),
        u.canAct ? el("span", { class: "ag-appr-btns" },
          ui.btn("Approve", { onClick: () => onApproval?.(a, "approved"), title: "Records the decision only; nothing is executed" }),
          ui.btn("Reject", { onClick: () => onApproval?.(a, "rejected"), title: "Records the decision only; nothing is executed" })) : null))
        : [el("p", { class: "muted ag-none" }, "No pending approvals.")]));
      // concurrency (set_concurrency): shows the RECORDED value; the server states it is not enforced
      const rec = v.concurrency;
      conc.replaceChildren(el("span", { class: "ag-conc-k" }, "Concurrency limit"),
        el("span", { class: "ag-seg", role: "group", "aria-label": "Concurrency limit 1 to 3" }, ...[1, 2, 3].map((n) =>
          el("button", { type: "button", class: ["ag-seg-b", rec === n ? "is-on" : ""], "aria-pressed": String(rec === n), disabled: !u.canAct, title: u.canAct ? `Record a request for concurrency ${n}` : u.actReason,
            on: { click: () => onConcurrency?.(n) } }, String(n)))),
        el("span", { class: "muted ag-conc-v" }, rec == null ? "none recorded" : `${rec} recorded, not enforced (no runtime attached)`));
    },
  };
}

// ------------------------------------------------------------------------------------------------------------------ timeline
export function createTimeline(ctx, { onSelectEvent } = {}) {
  const { el, ui } = ctx;
  let rows = [], refMs = Date.now(), selKey = null;
  const tbl = ui.table({ id: "agents.timeline", ariaLabel: "Event timeline, newest first", caption: "Event timeline, newest first", compact: true, maxHeight: "232px", exportable: false, columnMenu: false, filter: false,
    rowKey: (r) => r.e.seq, emptyTitle: "No events to show", emptyWhy: "This run has no events matching the current filters.",
    onRowClick: (r) => onSelectEvent?.(r.e), onRowActivate: (r) => onSelectEvent?.(r.e),
    columns: [
      { key: "time", label: "Time", width: 170, sortable: false, render: (r) => el("time", { datetime: r.e.timestamp_utc, title: r.e.timestamp_utc }, timeLabel(ctx, r.e.timestamp_utc, refMs)) },
      { key: "agent", label: "Agent", width: 150, sortable: false, render: (r) => el("span", {}, r.agents.length ? r.agents.map((a) => ROLE[a].short).join(", ") : "Run") },
      { key: "type", label: "Event", width: 140, sortable: false, render: (r) => el("span", { class: "ag-evtype mono" }, r.e.event_type) },
      { key: "src", label: "Source", width: 110, sortable: false, render: (r) => el("span", { class: r.e.source === "demo" ? "ag-src is-demo" : "ag-src" }, r.e.source === "demo" ? "DEMO" : r.e.source) },
      { key: "sum", label: "Summary", width: 520, sortable: false, render: (r) => el("span", { title: r.text }, r.text) },
    ] });
  const root = el("div", { class: "ag-timeline" }, tbl.el);
  return {
    el: root,
    update(v, u) {
      refMs = u.refMs; selKey = u.selectedEvent?.seq ?? null;
      const f = u.filters || {};
      const evs = u.events || [];
      rows = [];
      for (let i = evs.length - 1; i >= 0; i--) {
        const e = evs[i];
        if (f.type && e.event_type !== f.type) continue;
        const ags = eventAgents(e, v.taskMap);
        if (u.selected && !ags.includes(u.selected)) continue;
        if (f.role && !ags.includes(f.role)) continue;
        if (f.status) { const hit = ags.some((a) => displayStatus(v.agents[a]) === f.status); if (!hit) continue; }
        rows.push({ e, agents: ags, text: summarizeEvent(e) });
      }
      tbl.setRows(rows); markRow(tbl, selKey);
    },
    mark(seq) { selKey = seq; markRow(tbl, seq); },
    table: tbl,
  };
}

// ------------------------------------------------------------------------------------------------------------------ task board
export function createBoard(ctx, { onSelectAgent } = {}) {
  const { el, ui } = ctx;
  const root = el("div", { class: "ag-board", role: "list", "aria-label": "Task board, columns by status" });
  return {
    el: root,
    update(v, u) {
      const f = u.filters || {};
      let tasks = v.tasks;
      if (u.selected) tasks = tasks.filter((t) => t.owner === u.selected);
      if (f.role) tasks = tasks.filter((t) => t.owner === f.role);
      if (f.status) tasks = tasks.filter((t) => t.status === f.status);
      if (!v.tasks.length) { root.replaceChildren(ui.empty("No tasks in this run", "task.created events create cards here. Nothing is invented.")); return; }
      root.replaceChildren(...TASK_STATUSES.map((st) => {
        const m = STATUS_META[st], list = tasks.filter((t) => t.status === st);
        return el("section", { class: ["ag-col", `ag-col-${st}`], role: "listitem", "aria-label": `${m.label}: ${plural(list.length, "task")}` },
          el("header", { class: "ag-col-h" }, ui.badge(m.kind, m.label, m.icon), el("span", { class: "ag-col-n" }, String(list.length))),
          ...(list.length ? list.map((t) => el("div", { class: ["ag-task", t.demo ? "is-demo" : ""], tabindex: 0, role: "button",
            on: { click: () => t.owner && onSelectAgent?.(t.owner), keydown: (e) => { if (e.key === "Enter" && t.owner) onSelectAgent?.(t.owner); } },
            "aria-label": `Task ${t.task_id}: ${t.title}. Owner ${t.owner ? ROLE[t.owner]?.name : "unknown"}.` },
            el("div", { class: "ag-task-id mono" }, t.task_id), el("div", { class: "ag-task-t" }, t.title),
            el("div", { class: "ag-task-o" }, t.owner ? ROLE[t.owner]?.name || t.owner : "Owner not reported"),
            t.unmet?.length ? el("div", { class: "ag-task-w" }, `Waits on ${t.unmet.join(", ")}`) : null)) : [el("p", { class: "muted ag-none" }, "None")]));
      }));
    },
  };
}

// ------------------------------------------------------------------------------------------------------------------ dependency list
export function createDeps(ctx, { onSelectAgent } = {}) {
  const { el, ui } = ctx;
  const tbl = ui.table({ id: "agents.deps", ariaLabel: "Task dependencies", caption: "Task dependencies", compact: true, maxHeight: "232px", exportable: false, columnMenu: false,
    emptyTitle: "No task dependencies", emptyWhy: "task.dependency events list which task waits on which. Nothing is inferred.", rowKey: (r) => r.seq,
    columns: [
      { key: "task", label: "Task", width: 220, sortable: false, render: (r) => el("span", { title: r.taskTitle }, `${r.task_id}${r.taskTitle ? " · " + r.taskTitle : ""}`) },
      { key: "owner", label: "Owner", width: 150, sortable: false, render: (r) => el("span", {}, r.to_agent ? ROLE[r.to_agent].short : "Owner unknown") },
      { key: "dep", label: "Waits on", width: 220, sortable: false, render: (r) => el("span", { title: r.depTitle }, `${r.depends_on}${r.depTitle ? " · " + r.depTitle : ""}`) },
      { key: "prov", label: "Provider", width: 150, sortable: false, render: (r) => el("span", {}, r.from_agent ? ROLE[r.from_agent].short : "Owner unknown") },
      { key: "state", label: "State", width: 190, sortable: false, render: (r) => r.from_known ? (r.from_status === "complete" ? ui.badge("ok", "Met", "check") : ui.badge("warn", `Unmet (${r.from_status})`, "pause")) : ui.badge("neutral", "Provider task not in events", "info") },
      { key: "edge", label: "Connector", width: 220, sortable: false, render: (r) => el("span", { class: "muted" }, r.from_agent && r.to_agent && r.from_agent !== r.to_agent ? "Dashed connector drawn" : "Not drawn (owners not both known)") },
    ] });
  return {
    el: el("div", { class: "ag-deps" }, tbl.el),
    update(v, u) {
      const f = u.filters || {};
      let rows = v.deps.map((d) => ({ ...d, taskTitle: v.taskMap.get(d.task_id)?.title || "", depTitle: v.taskMap.get(d.depends_on)?.title || "" }));
      if (u.selected) rows = rows.filter((d) => d.from_agent === u.selected || d.to_agent === u.selected);
      if (f.role) rows = rows.filter((d) => d.from_agent === f.role || d.to_agent === f.role);
      tbl.setRows(rows);
    },
  };
}

// ------------------------------------------------------------------------------------------------------------------ list view (accessible)
export function createList(ctx, { onSelectAgent } = {}) {
  const { el, ui } = ctx;
  let refMs = Date.now(), sel = null;
  const tbl = ui.table({ id: "agents.list", ariaLabel: "Agents", caption: "Agents with status, current task and latest result", exportable: false, columnMenu: false,
    rowKey: (r) => r.id, onRowClick: (r) => onSelectAgent?.(r.id), onRowActivate: (r) => onSelectAgent?.(r.id),
    emptyTitle: "No agents", emptyWhy: "",
    columns: [
      { key: "agent", label: "Agent", width: 200, sortable: false, render: (r) => el("span", {}, el("strong", {}, ROLE[r.id].name), " ", el("span", { class: "mono muted" }, r.id)) },
      { key: "status", label: "Status", width: 190, sortable: false, render: (r) => statusBadge(ctx, displayStatus(r)) },
      { key: "task", label: "Current task", width: 280, sortable: false, render: (r) => el("span", { title: r.task || "" }, !r.has_events ? "No agent running" : !r.status ? "No task reported" : r.task || "No task text reported") },
      { key: "since", label: "In status", width: 100, sortable: false, render: (r) => el("span", {}, r.has_events && r.status && r.status_since ? elapsedText(r.status_since, refMs) : "—") },
      { key: "last", label: "Last event", width: 190, sortable: false, render: (r) => el("span", {}, r.has_events ? timeLabel(ctx, r.last_activity, refMs) : "—") },
      { key: "q", label: "Queued", width: 80, sortable: false, align: "num", render: (r) => el("span", {}, String(r.queued)) },
      { key: "d", label: "Done", width: 80, sortable: false, align: "num", render: (r) => el("span", {}, String(r.completed)) },
      { key: "res", label: "Latest result", width: 320, sortable: false, render: (r) => el("span", { title: r.latest?.text || "" }, r.latest ? `${r.latest.kind}: ${r.latest.text}` : "—") },
      { key: "svc", label: "Service", width: 150, sortable: false, render: (r) => el("span", { class: "muted" }, r.last_heartbeat ? "Reachable (heartbeat)" : "—") },
    ] });
  return {
    el: el("div", { class: "ag-list" }, tbl.el),
    update(v, u) {
      refMs = u.refMs; sel = u.selected;
      const f = u.filters || {};
      tbl.setRows(v.agentList.filter((a) => agentMatches(a, f))); markRow(tbl, sel);
    },
  };
}

export { markRow, tms };
