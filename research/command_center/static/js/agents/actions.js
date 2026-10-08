// Actions: every command goes through POST /api/commands and the SERVER's reply is shown verbatim as the outcome
// ("recorded, no runtime attached ..."). There is no optimistic success, no order path and no git call anywhere in this module.
import { ROLE, AGENT_IDS } from "./model.js";

export const CANCEL_SCOPE = "This only records a request to stop RESEARCH work. It never closes positions, cancels orders or touches execution. No agent runtime is attached, so nothing is stopped either.";

export function createActions(ctx, { getRunId, canAct, actReason, onOutcome } = {}) {
  const { el, ui } = ctx;

  /** send(command, args) -> {ok, message, raw}. Shows the server message as the outcome; failures show the real error. */
  async function send(command, args, { errorEl } = {}) {
    if (!canAct()) { const m = actReason(); onOutcome?.({ kind: "error", command, message: m }); return { ok: false, message: m }; }
    try {
      const body = { command, args }; const rid = getRunId(); if (rid) body.run_id = rid;
      const r = await ctx.api.post("/api/commands", body, { errorEl });
      const msg = r.message || (r.accepted ? "recorded" : "not accepted");
      onOutcome?.({ kind: r.accepted ? "ok" : "error", command, message: msg, runtime_attached: !!r.runtime_attached, recorded: !!r.recorded, note: r.note || null });
      return { ok: !!r.accepted, message: msg, raw: r };
    } catch (e) {
      const msg = e.body?.message || e.message || "request failed";
      onOutcome?.({ kind: "error", command, message: e.code === "no_token" ? `Not recorded. ${msg}` : `Not recorded by the server: ${msg}`, code: e.code });
      return { ok: false, message: msg, error: e };
    }
  }

  return {
    send,
    requestUpdate: (agent) => send("request_update", { agent_id: agent }),
    setConcurrency: (n) => send("set_concurrency", { value: n }),
    approve: async (a, resolution) => {
      const ok = await ui.confirm({ title: `${resolution === "approved" ? "Approve" : "Reject"} this proposal?`, body: a.summary,
        scope: "Records a human decision as an approval.resolved event. Nothing is executed and no order is placed.", confirmLabel: resolution === "approved" ? "Record approval" : "Record rejection" });
      if (ok) return send("approve_proposal", { approval_id: a.approval_id, resolution });
      return null;
    },
    cancel: async (agent, taskId) => {
      const ok = await ui.confirm({ title: "Record a request to cancel research?", danger: true, confirmLabel: "Record cancel request", cancelLabel: "Keep as is",
        body: agent ? `Target: ${ROLE[agent]?.name || agent}${taskId ? `, task ${taskId}` : ""}.` : "Target: the current research run.", scope: CANCEL_SCOPE });
      if (!ok) return null;
      const args = {}; if (agent) args.agent_id = agent; if (taskId) args.task_id = taskId;
      return send("cancel_research", args);
    },
    /** Start research task: form drawer. The honest pre-statement is shown BEFORE submit; the server reply after. */
    startTask(defaultAgent) {
      const errorEl = el("div", { class: "ag-form-err" });
      const result = el("div", { class: "ag-form-result", role: "status", "aria-live": "polite" });
      const sel = el("select", { class: "input", "aria-label": "Assign to agent", id: "ag-task-agent" }, ...AGENT_IDS.map((a) => el("option", { value: a, selected: a === (defaultAgent || "strategy_researcher") }, `${ROLE[a].name} (${a})`)));
      const title = el("input", { class: "input", type: "text", maxlength: 300, required: true, id: "ag-task-title", placeholder: "e.g. Re-run the baseline with the updated cost model", autocomplete: "off" });
      const submit = ui.btn("Record task request", { kind: "primary", type: "submit" });
      const form = el("form", { class: "ag-form", novalidate: true, on: { submit: async (e) => {
        e.preventDefault(); errorEl.replaceChildren(); result.replaceChildren();
        if (!title.value.trim()) { errorEl.replaceChildren(el("div", { class: "inline-error", role: "alert" }, "Enter a task title.")); title.focus(); return; }
        submit.disabled = true;
        const r = await send("assign_task", { agent_id: sel.value, title: title.value.trim() }, { errorEl });
        submit.disabled = false;
        if (r.error) return;                                   // error already shown inline (errorEl) and as outcome
        result.replaceChildren(el("div", { class: ["ag-result", r.ok ? "is-ok" : "is-bad"] }, el("strong", {}, "Server response: "), el("span", {}, r.message)));
        if (r.ok) { title.value = ""; ui.toast(r.message, { kind: "info", title: "Task request recorded" }); }
      } } },
        el("p", { class: "ag-form-note" }, "This sends a request to the command centre, which records it as an event. ", el("strong", {}, "No agent runtime is attached"), ", so nothing will execute the task. The server's reply is shown below."),
        el("label", { class: "field" }, el("span", {}, "Agent"), sel),
        el("label", { class: "field" }, el("span", {}, "Task title"), title),
        errorEl, el("div", { class: "row" }, submit), result);
      ui.drawer({ title: "Start research task", width: 460, label: "Start research task", content: form });
    },
  };
}
