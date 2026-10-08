// DEMO data: generated on the client, kept in a SEPARATE state, and never sent to or stored in the real event store.
// Every text is prefixed "DEMO:" and every event carries source:'demo' so the UI can label it. Mirrors the shape of seed_demo.py.

export const DEMO_RUN_ID = "demo-client";

export function demoEvents(base = Date.now() - 12 * 60000) {
  const out = [];
  let n = 0;
  const iso = (dt) => new Date(base + dt * 1000).toISOString();
  const ev = (dt, type, agent, payload) => { n += 1; out.push({ seq: n, event_id: `demo-client-${String(n).padStart(4, "0")}`, event_type: type, timestamp_utc: iso(dt), run_id: DEMO_RUN_ID,
    agent_id: agent || null, source: "demo", status: null, payload_version: 1, payload }); };
  ev(0, "mode.changed", null, { mode: "DEMO", note: "DEMO seed data - not real activity" });
  [["lead", "working", "DEMO: plan research slice"], ["data_ibkr", "queued", null], ["strategy_researcher", "queued", null], ["backtester", "queued", null],
    ["validator", "queued", null], ["monitor", "working", "DEMO: watch event feed"]].forEach(([a, s, t]) => ev(1, "agent.status", a, t ? { status: s, task: t } : { status: s }));
  ev(3, "task.created", "lead", { task_id: "D1", title: "DEMO: define data request", owner: "data_ibkr" });
  ev(3, "task.created", "lead", { task_id: "D2", title: "DEMO: draft strategy spec", owner: "strategy_researcher" });
  ev(3, "task.created", "lead", { task_id: "D3", title: "DEMO: run baseline backtest", owner: "backtester" });
  ev(3, "task.created", "lead", { task_id: "D4", title: "DEMO: validate split and costs", owner: "validator" });
  ev(4, "task.dependency", null, { task_id: "D3", depends_on: "D1" });
  ev(4, "task.dependency", null, { task_id: "D3", depends_on: "D2" });
  ev(4, "task.dependency", null, { task_id: "D4", depends_on: "D3" });
  ev(6, "message.sent", "lead", { sender: "lead", recipient: "data_ibkr", kind: "delegation", preview: "DEMO: please prepare the data request" });
  ev(7, "agent.status", "data_ibkr", { status: "working", task: "DEMO: prepare data request" });
  ev(7, "task.updated", "data_ibkr", { task_id: "D1", status: "working" });
  ev(8, "message.sent", "lead", { sender: "lead", recipient: "strategy_researcher", kind: "delegation", preview: "DEMO: draft the strategy spec" });
  ev(9, "agent.status", "strategy_researcher", { status: "working", task: "DEMO: draft strategy spec" });
  ev(9, "task.updated", "strategy_researcher", { task_id: "D2", status: "working" });
  ev(12, "tool.activity", "data_ibkr", { tool: "read_file", summary: "DEMO: read docs/DATA_REQUEST.md", sources: ["docs/DATA_REQUEST.md"] });
  ev(14, "agent.status", "backtester", { status: "awaiting_dependency", task: "DEMO: run baseline backtest", blockers: ["DEMO: waiting for data and spec"] });
  ev(14, "task.updated", "backtester", { task_id: "D3", status: "awaiting_dependency" });
  ev(18, "artifact.created", "data_ibkr", { path: "docs/DATA_REQUEST.md", title: "DEMO artifact link (existing doc, not produced by a demo agent)" });
  ev(20, "task.updated", "data_ibkr", { task_id: "D1", status: "complete" });
  ev(20, "agent.status", "data_ibkr", { status: "complete", task: "DEMO: define data request" });
  ev(22, "message.sent", "data_ibkr", { sender: "data_ibkr", recipient: "backtester", preview: "DEMO: data request is ready" });
  ev(24, "decision.summary", "strategy_researcher", { summary: "DEMO: keep the spec to one strategy, as per project rules", sources: ["research/CLAUDE.md"] });
  ev(26, "test.result", "validator", { name: "DEMO placeholder check", outcome: "skip", detail: "DEMO row only - no test was run" });
  ev(28, "approval.requested", "lead", { approval_id: "demo-approval-1", summary: "DEMO: approve moving D3 forward", requested_by: "lead" });
  ev(30, "incident", "monitor", { severity: "info", summary: "DEMO: example incident row - nothing happened" });
  ev(32, "heartbeat", "monitor", {});
  ev(34, "message.sent", "backtester", { sender: "backtester", recipient: "validator", preview: "DEMO: baseline run is ready for audit" });
  return out;
}
