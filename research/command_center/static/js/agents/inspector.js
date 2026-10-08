// Inspector: tabs Current task / Messages / Tool activity / Outputs / Run history for one agent.
// Shows concise OBSERVABLE summaries only (events). Private reasoning is not shown, and this is stated once, briefly.
import { ROLE, STATUS_META, displayStatus, summarizeEvent, usageRows, elapsedText, docLinkFor, resultLinkFor, pairKey } from "./model.js";
import { statusBadge, timeLabel, outcomeBadge, fetchText, plural } from "./common.js";

export function createInspector(ctx, { onClose, actions, openDoc } = {}) {
  const { el, ui } = ctx;
  let agentId = null, V = null, U = { refMs: Date.now() }, tabsApi = null;
  const rendered = new Set();
  const head = el("div", { class: "ag-ins-head" });
  const acts = el("div", { class: "ag-ins-acts", role: "toolbar", "aria-label": "Agent actions" });
  const note = el("p", { class: "ag-ins-note" }, ui.icon("shield", 14), el("span", {}, "Shows observable events only. Private reasoning is not shown."));
  const body = el("div", { class: "ag-ins-body" });
  const root = el("div", { class: "ag-inspector-in" }, head, note, acts, body);

  const kv = (rows) => el("dl", { class: "kv ag-kv" }, ...rows.filter(Boolean).map(([k, v]) => el("div", { class: "kv-row" }, el("dt", {}, k), el("dd", {}, v))));
  const none = (t) => el("p", { class: "muted ag-none" }, t);
  const ag = () => V?.agents[agentId];
  const list = (items) => el("ul", { class: "ag-ul" }, ...items);

  function artifactRow(a) {
    const doc = docLinkFor(a.path) || resultLinkFor(a.path);
    return el("li", { class: ["ag-art", U.hl?.eventId === a.event_id ? "is-hl" : ""], dataset: { eventId: a.event_id } },
      el("div", { class: "ag-art-p mono" }, a.path), a.title ? el("div", { class: "muted" }, a.title) : null,
      el("div", { class: "ag-art-m" }, el("span", { class: "muted" }, timeLabel(ctx, a.ts, U.refMs, { seconds: false })),
        doc ? ui.btn("Open document", { icon: "external", onClick: () => openDoc?.(a.path, doc) }) : el("span", { class: "muted" }, "Not served by this page (path shown as text)")));
  }

  const fills = {
    task(p) {
      const a = ag(), key = displayStatus(a), tasks = V.tasks.filter((t) => t.owner === agentId);
      const usage = usageRows(a.usage);
      p.replaceChildren(kv([
        ["Status", statusBadge(ctx, key)],
        ["Current task", !a.has_events ? "No agent running" : !a.status ? "No task reported" : a.task || "No task text reported"],
        ["In status", a.has_events && a.status && a.status_since ? `${elapsedText(a.status_since, U.refMs)} (since ${timeLabel(ctx, a.status_since, U.refMs)})` : "—"],
        ["Last event", a.has_events ? timeLabel(ctx, a.last_activity, U.refMs) : "No events from this agent"],
        ["Tasks", `${a.queued} queued, ${a.completed} completed, ${tasks.length} owned`],
        ["Blockers", a.blockers.length ? list(a.blockers.map((b) => el("li", {}, b))) : "None reported"],
        ["Service", a.last_heartbeat ? `Reachable: heartbeat ${timeLabel(ctx, a.last_heartbeat, U.refMs)}. A heartbeat is not a work status.` : "No heartbeat seen"],
        usage ? ["Usage", list(usage.map((r) => el("li", {}, `${r.key}: ${typeof r.value === "number" ? ctx.fmt.num(r.value, Number.isInteger(r.value) ? 0 : 2) : r.value}${r.estimate ? " (estimate)" : ""}`))) ] : null,
        usage ? ["Usage source", `Reported by event ${a.usage_event}. Not computed here.`] : ["Usage", "Not reported by any event"],
        ["Role", ROLE[agentId].blurb],
      ]), el("h4", { class: "ag-sub" }, "Owned tasks"),
        tasks.length ? list(tasks.map((t) => el("li", {}, el("span", { class: "mono" }, t.task_id), " ", t.title, " ", ui.badge(STATUS_META[t.status].kind, STATUS_META[t.status].label, STATUS_META[t.status].icon)))) : none("No tasks owned by this agent in this run."),
        a.latest ? el("div", {}, el("h4", { class: "ag-sub" }, "Latest result"), el("p", {}, a.latest.text)) : null);
    },
    messages(p) {
      const ms = V.messages.filter((m) => m.sender === agentId || m.recipient === agentId).slice().reverse();
      p.replaceChildren(ms.length ? list(ms.map((m) => el("li", { class: ["ag-msg", U.hl?.eventId === m.event_id ? "is-hl" : ""] },
        el("div", { class: "ag-msg-h" }, ui.badge(m.sender === agentId ? "info" : "neutral", m.sender === agentId ? "Sent" : "Received", m.sender === agentId ? "chevron_right" : "chevron_left"),
          el("span", { class: "ag-msg-d" }, `${m.sender} to ${m.recipient}`), m.kind === "delegation" ? ui.badge("neutral", "delegation", "list") : null),
        el("div", { class: "muted" }, timeLabel(ctx, m.ts, U.refMs)), el("div", { class: "ag-msg-p" }, m.preview)))) : none("No message.sent events involve this agent."));
    },
    tools(p) {
      const ts = V.tools.filter((t) => t.agent === agentId).slice().reverse();
      p.replaceChildren(ts.length ? list(ts.map((t) => el("li", { class: "ag-tool" }, el("div", { class: "ag-tool-h" }, el("span", { class: "mono ag-tool-n" }, t.tool), el("span", { class: "muted" }, timeLabel(ctx, t.ts, U.refMs))),
        el("div", {}, t.summary), t.sources.length ? el("div", { class: "muted" }, `Sources: ${t.sources.join(", ")}`) : null))) : none("No tool.activity events from this agent."));
    },
    outputs(p) {
      const arts = V.artifacts.filter((a) => a.agent === agentId).slice().reverse(), tests = V.tests.filter((t) => t.agent === agentId).slice().reverse(), decs = V.decisions.filter((d) => d.agent === agentId).slice().reverse();
      p.replaceChildren(
        el("h4", { class: "ag-sub" }, "Artifacts"), arts.length ? list(arts.map(artifactRow)) : none("No artifact.created events from this agent."),
        el("h4", { class: "ag-sub" }, "Test results"), tests.length ? list(tests.map((t) => el("li", { class: ["ag-test", U.hl?.eventId === t.event_id ? "is-hl" : ""] }, outcomeBadge(ctx, t.outcome), " ", el("span", {}, t.name), t.detail ? el("div", { class: "muted" }, t.detail) : null, t.command ? el("div", { class: "mono muted" }, t.command) : null))) : none("No test.result events from this agent."),
        el("h4", { class: "ag-sub" }, "Decisions"), decs.length ? list(decs.map((d) => el("li", { class: ["ag-dec", U.hl?.eventId === d.event_id ? "is-hl" : ""] }, el("div", {}, d.summary), el("div", { class: "muted" }, timeLabel(ctx, d.ts, U.refMs, { seconds: false }), d.sources.length ? ` · sources: ${d.sources.join(", ")}` : "")))) : none("No decision.summary events from this agent."));
    },
    history(p) {
      const evs = (V.byAgent[agentId] || []).slice().reverse();
      p.replaceChildren(evs.length ? list(evs.map((e) => el("li", { class: ["ag-hist", U.hl?.eventId === e.event_id ? "is-hl" : ""] },
        el("div", { class: "ag-hist-h" }, el("span", { class: "mono ag-hist-t" }, e.event_type), el("span", { class: "muted" }, timeLabel(ctx, e.timestamp_utc, U.refMs))), el("div", {}, summarizeEvent(e))))) : none("No events recorded for this agent in this run."));
    },
  };

  function mountTabs() {
    rendered.clear();
    tabsApi = ui.tabs({ id: "agents.inspector", label: "Inspector sections", active: "task", tabs: [
      ["task", "Current task"], ["messages", "Messages"], ["tools", "Tool activity"], ["outputs", "Outputs"], ["history", "Run history"]].map(([id, label]) => ({ id, label, render: (p) => { rendered.add(id); if (V && agentId) fills[id](p); } })) });
    body.replaceChildren(tabsApi.el);
  }

  function renderHead() {
    const a = ag(); if (!a) return;
    head.replaceChildren(
      el("div", { class: "ag-ins-title" }, el("h2", { class: "ag-ins-h", id: "ag-ins-title", tabindex: -1 }, ROLE[agentId].name), el("span", { class: "mono muted" }, agentId),
        a.demo && !a.real ? el("span", { class: "t-demo" }, "DEMO") : null),
      onClose ? el("button", { class: "icon-btn", type: "button", "aria-label": "Close inspector", title: "Close inspector (Esc)", on: { click: () => onClose() } }, ui.icon("x", 16)) : null);
  }

  function renderActions() {
    const a = ag(); if (!a) return;
    const can = U.canAct, why = U.actReason;
    const lastArt = [...V.artifacts].reverse().find((x) => x.agent === agentId);
    const lastTest = [...V.tests].reverse().find((x) => x.agent === agentId);
    const reviewTarget = a.latest?.kind === "artifact" ? lastArt : a.latest?.kind === "test" ? lastTest : (lastArt || lastTest);
    const changes = [...V.artifacts].filter((x) => x.agent === agentId);
    acts.replaceChildren(
      ui.btn("Request update", { disabled: !can, title: can ? "Records a request_update command; no runtime is attached" : why, onClick: () => actions.requestUpdate(agentId) }),
      ui.btn("Start research task", { kind: "primary", disabled: !can, title: can ? "Record a task request for this agent" : why, onClick: () => actions.startTask(agentId) }),
      ui.btn("Review result", { disabled: !reviewTarget, title: reviewTarget ? "Open the latest artifact or test result" : "This agent has no result yet", onClick: () => reviewResult(reviewTarget) }),
      ui.btn("View changes", { disabled: !changes.length, title: changes.length ? "Show the git-tracked path(s); no git command is run" : "No artifact paths reported", onClick: (ev) => showChanges(ev.currentTarget, changes) }),
      ui.btn("Cancel research task", { disabled: !can, title: can ? "Record a request to stop research (nothing else)" : why, onClick: () => actions.cancel(agentId, a.task && V.tasks.find((t) => t.owner === agentId && t.status === "working")?.task_id) }));
    acts.lastChild.classList.add("btn-danger-outline");
  }

  function reviewResult(t) {
    if (!t) return;
    if (t.path) { const doc = docLinkFor(t.path) || resultLinkFor(t.path); if (doc) { openDoc?.(t.path, doc); return; } }
    ui.drawer({ title: t.path ? "Artifact" : "Test result", width: 460, content: (b) => b.append(
      t.path ? el("div", {}, el("p", { class: "mono" }, t.path), el("p", { class: "muted" }, "This path is not served by the command centre. It is shown as text only.")) :
        el("div", {}, outcomeBadge(ctx, t.outcome), el("p", {}, t.name), t.detail ? el("p", { class: "muted" }, t.detail) : null, t.command ? el("p", { class: "mono" }, t.command) : null)) });
  }
  function showChanges(anchor, arts) {
    ui.popover(anchor, (box) => box.append(el("div", { class: "pop-title" }, "Git-tracked artifact paths"),
      list(arts.map((a) => el("li", { class: "mono" }, a.path))),
      el("p", { class: "pop-note" }, "Shown as text. This page runs no git command; use `git log -p -- <path>` in a terminal to see the changes.")), { label: "Artifact paths", width: 380 });
  }

  const api = {
    el: root,
    get agent() { return agentId; },
    show(id) { if (id !== agentId) { agentId = id; mountTabs(); } },
    update(v, u) {
      V = v; U = u; if (!agentId) return;
      renderHead(); renderActions();
      for (const id of rendered) { const p = tabsApi.panel(id); if (p) fills[id](p); }
    },
    focusHeading() { head.querySelector("#ag-ins-title")?.focus(); },
    clear() { agentId = null; head.replaceChildren(); acts.replaceChildren(); body.replaceChildren(); },
  };
  void pairKey; void plural;
  return api;
}

/** Doc viewer drawer (text only). path = display path, url = same-origin /api/docs|results URL. */
export function openDocDrawer(ctx, path, url) {
  const { el, ui } = ctx;
  ui.drawer({ title: path, width: 640, label: "Artifact document", content: (b) => {
    b.append(el("p", { class: "muted" }, "Plain-text view. ", el("a", { href: url, target: "_blank", rel: "noopener" }, "Open raw file in a new tab")), ui.skeleton({ lines: 6 }));
    fetchText(url).then((t) => { b.replaceChildren(el("p", { class: "muted" }, el("a", { href: url, target: "_blank", rel: "noopener" }, "Open raw file in a new tab")), el("pre", { class: "ag-doc" }, t)); })
      .catch((e) => b.replaceChildren(el("div", { class: "inline-error", role: "alert" }, `Could not load ${path}: ${e.message}`)));
  } });
}
