// Settings & Audit workspace (RESEARCH worker): preferences, merged audit log, config history, health, redacted logs, usage, approvals, safety limits.
import { el } from "../core/dom.js";
import { useCss, errText, field, selectEl, kv, note, card, download } from "../research/kit.js";

const SECRET_RE = /token|secret|passw|api[_-]?key|authorization|cookie|account(_?id)?$/i;
const scrub = (v) => JSON.parse(JSON.stringify(v, (k, x) => (SECRET_RE.test(k) ? "[redacted]" : typeof x === "string" ? x.replace(/(?<![\w.:])\/(?:[\w.\-+@~=]+\/)+[\w.\-+@~=]+/g, "<path>") : x)));
const short = (v) => { const s = typeof v === "string" ? v : JSON.stringify(v); return s == null ? "" : s.length > 160 ? s.slice(0, 157) + "…" : s; };

export default {
  id: "settings", title: "Settings & Audit", icon: "settings",
  _off: [],
  async mount(host, ctx) {
    useCss();
    const root = el("div", { class: "rs" }); host.replaceChildren(root);
    root.append(el("div", { class: "rs-head" }, el("div", {}, el("h1", {}, "Settings & Audit"),
      el("p", {}, "Preferences, the audit trail, integration health and the safety limits that are enforced by the backend. Everything here is read from the server; nothing is simulated."))));
    const OFF = (this._off = []);
    const A = { audit: null };

    async function loadAudit() {
      const [a, r] = await Promise.allSettled([ctx.api.get("/api/audit?limit=500"), ctx.api.get("/api/research/audit?limit=500")]);
      const rows = []; const problems = [];
      if (a.status === "fulfilled") a.value.items.forEach((x) => rows.push({ id: "c" + x.seq, source: "config (events)", time: x.timestamp_utc, area: x.area, action: x.action, target: x.target, outcome: x.outcome || "applied", old: x.old, new: x.new, detail: scrub(x) }));
      else problems.push(`config audit: ${errText(a.reason)}`);
      if (r.status === "fulfilled") r.value.entries.forEach((x) => rows.push({ id: "r" + x.id, source: "research", time: x.ts, area: x.kind.split(".")[0], action: x.kind, target: x.subject, outcome: x.outcome, old: x.detail.from ?? null, new: x.detail.to ?? x.detail.requested ?? null, detail: scrub(x.detail) }));
      else problems.push(`research audit: ${errText(r.reason)}`);
      rows.sort((p, q) => String(q.time).localeCompare(String(p.time)));
      A.audit = { rows, problems }; return A.audit;
    }

    // ------------------------------------------------------------ preferences
    function prefs(panel) {
      const theme = () => document.documentElement.dataset.theme || "dark";
      const themeSel = selectEl([{ value: "dark", label: "Dark" }, { value: "light", label: "Light" }], theme(), { label: "Theme", on: { change: (e) => {
        const want = e.target.value; const b = document.getElementById("theme-btn"); if (theme() !== want) { if (b) b.click(); else document.documentElement.dataset.theme = want; }
        ctx.audit("settings.theme", { target: "ui.theme", old: theme() === want ? null : theme(), new: want, outcome: "applied" }); } } });
      const tz = selectEl([{ value: "America/New_York", label: "America/New_York (market time)" }, { value: "UTC", label: "UTC" }], ctx.prefs.get("ui.tz", "America/New_York"), { label: "Timezone", on: { change: (e) => {
        ctx.fmt.tz(e.target.value); ctx.prefs.set("ui.tz", e.target.value); ctx.audit("settings.timezone", { target: "ui.tz", new: e.target.value, outcome: "applied" }); } } });
      const status = el("p", { class: "hint", "aria-live": "polite" });
      const paintStatus = () => { const s = ctx.prefs.status(); status.textContent = s.error ? `Preferences not synced: ${s.error}` : s.pending ? "Saving preferences to the server…" : s.synced ? "Preferences are saved on the server." : "Preferences are held in this browser only until a token is set."; };
      paintStatus(); OFF.push(ctx.prefs.onChange(paintStatus));
      const reset = (label, prefix, what) => ctx.ui.btn(label, { icon: "x_circle", onClick: async () => {
        const keys = Object.keys(ctx.prefs.all()).filter((k) => k.startsWith(prefix));
        const ok = await ctx.ui.confirm({ title: label, scope: `${keys.length} saved ${what} setting(s) (${prefix}*). Nothing else changes.`, body: "They return to their defaults on the next visit to each screen.", confirmLabel: "Reset" });
        if (!ok) return; keys.forEach((k) => ctx.prefs.remove(k)); const r = await ctx.prefs.flush();
        if (r && r.ok === false) ctx.ui.toast(`Reset locally, but not saved on the server: ${r.error}`, { kind: "error" }); else ctx.ui.toast(`${keys.length} ${what} setting(s) reset`, { kind: "success" });
        ctx.audit("settings.reset", { target: prefix, new: keys.length, outcome: "applied" }); paintStatus(); } });
      panel.append(card("Preferences", el("div", { class: "rs-grid" }, field("Theme", themeSel), field("Timezone for displayed times", tz, "Market sessions are always evaluated in America/New_York.")),
        el("div", { class: "row" }, reset("Reset layout", "panel.", "panel size/collapse"), reset("Reset column settings", "table.", "table column"), reset("Reset remembered tabs", "tabs.", "tab")), status));
    }

    // ------------------------------------------------------------ audit
    function auditTab(panel, onlyConfig) {
      const slot = el("div", {}, ctx.ui.skeleton({ variant: "table", lines: 5 })); panel.append(slot);
      (async () => {
        const { rows, problems } = await loadAudit();
        const data = onlyConfig ? rows.filter((r) => r.source.startsWith("config") || r.action === "strategy.state") : rows;
        const srcSel = selectEl([{ value: "all", label: "All sources" }, { value: "config (events)", label: "config (events)" }, { value: "research", label: "research" }], "all", { label: "Source" });
        const outSel = selectEl([{ value: "all", label: "All outcomes" }, ...[...new Set(data.map((r) => r.outcome))].sort().map((v) => ({ value: v, label: v }))], "all", { label: "Outcome" });
        const apply = () => t.setRows(data.filter((r) => (srcSel.value === "all" || r.source === srcSel.value) && (outSel.value === "all" || r.outcome === outSel.value)));
        srcSel.addEventListener("change", apply); outSel.addEventListener("change", apply);
        const t = ctx.ui.table({ id: onlyConfig ? "settings.config" : "settings.audit", ariaLabel: "Audit log", caption: onlyConfig ? "Configuration change history" : "Audit log (config events + research API)", exportName: onlyConfig ? "config-history" : "audit", filter: true, rows: data, rowKey: (r) => r.id,
          expand: (r, td) => td.append(el("pre", { class: "tail" }, JSON.stringify(r.detail, null, 2))), emptyTitle: "No audit entries", emptyWhy: "Nothing has been recorded yet. Changing a preference or a strategy state creates an entry.",
          columns: [{ key: "time", label: "Time", width: 190, render: (r) => ctx.fmt.timeEl(r.time) }, { key: "source", label: "Source", width: 120 }, { key: "action", label: "Action", width: 190, value: (r) => (r.area ? `${r.area}.${r.action}`.replace(/^(\w+)\.\1\./, "$1.") : r.action) },
            { key: "target", label: "Target", width: 190, missing: "—" }, { key: "outcome", label: "Outcome", width: 140, render: (r) => ctx.ui.badge(/refused|failed/.test(r.outcome) ? "bad" : r.outcome === "changed" || r.outcome === "applied" || r.outcome === "stored" || r.outcome === "queued" ? "ok" : "neutral", r.outcome, /refused|failed/.test(r.outcome) ? "x_circle" : "check") },
            { key: "old", label: "From", width: 130, value: (r) => (r.old == null ? null : short(r.old)), missing: "—" }, { key: "new", label: "To", width: 130, value: (r) => (r.new == null ? null : short(r.new)), missing: "—" }] });
        slot.replaceChildren(el("div", { class: "rs-toolbar" }, field("Source", srcSel), field("Outcome", outSel), ctx.ui.btn("Export JSON", { icon: "download", onClick: () => download(`audit-${new Date().toISOString().slice(0, 10)}.json`, "application/json", JSON.stringify(data, null, 1)) })),
          problems.map((p) => note("warn", `Partial data: ${p}`)), t.el, el("p", { class: "hint" }, "Entries are append-only on the server. Secrets are redacted before display. CSV export of the filtered view is in the table toolbar."));
      })().catch((e) => ctx.ui.inlineError(slot, errText(e)));
    }

    // ------------------------------------------------------------ health
    function health(panel) {
      const slot = el("div", {}, ctx.ui.skeleton({ lines: 5 })); panel.append(slot);
      (async () => {
        const rows = []; const add = (name, state, detail, kind) => rows.push({ name, state, detail, kind });
        const get = async (p) => { try { return await ctx.api.get(p); } catch (e) { return { __err: errText(e) }; } };
        const [h, c, ex, strat, cov] = await Promise.all([get("/api/health"), get("/api/connections"), get("/api/experiments"), get("/api/strategies"), get("/api/data/coverage")]);
        h.__err ? add("Event store API", "unreachable", h.__err, "bad") : add("Event store API", "ok", `max seq ${h.max_seq}; server time ${h.server_time}`, "ok");
        const st = ctx.events.state(); add("Event stream (SSE)", st.conn, `last seq ${st.lastSeq}; stale after ${st.staleAfterS}s`, st.conn === "live" ? "ok" : st.conn === "stale" ? "warn" : "neutral");
        if (c.__err) add("Connections API", "unreachable", c.__err, "bad"); else { add("Broker", c.broker.state, c.broker.reason, "warn"); add("Market data", c.market_data.state, `${c.market_data.source}; last retrieved ${c.market_data.last_retrieved_on ?? "never"}`, c.market_data.state === "stale" ? "warn" : "info"); }
        if (ex.__err) add("Experiment queue", "unreachable", ex.__err, "bad"); else { const n = {}; ex.experiments.forEach((x) => { n[x.status] = (n[x.status] || 0) + 1; }); add("Experiment queue", "ok", Object.keys(n).length ? Object.entries(n).map(([k, v]) => `${v} ${k}`).join(", ") : "empty (single worker thread)", "ok"); }
        strat.__err ? add("Strategy registry", "unreachable", strat.__err, "bad") : add("Strategy registry", "ok", `${strat.strategies.length} strategies`, "ok");
        cov.__err ? add("Local data", "unreachable", cov.__err, "bad") : add("Local data", cov.symbols.some((s) => s.blocking_failures.length) ? "blocking failures" : "ok", `${cov.symbols.length} symbols, 1D bars`, cov.symbols.some((s) => s.blocking_failures.length) ? "bad" : "ok");
        const ps = ctx.prefs.status(); add("Preferences sync", ps.error ? "error" : ps.synced ? "synced" : "local only", ps.error || (ps.pending ? "pending" : "—"), ps.error ? "bad" : ps.synced ? "ok" : "warn");
        slot.replaceChildren(card("Integration health", ctx.ui.table({ id: "settings.health", ariaLabel: "Integration health", caption: "Integration health", rows, rowKey: (r) => r.name, exportName: "health", columnMenu: false,
          columns: [{ key: "name", label: "Component", width: 200 }, { key: "state", label: "State", width: 160, render: (r) => ctx.ui.badge(r.kind, r.state, r.kind === "ok" ? "check" : r.kind === "bad" ? "x_circle" : r.kind === "warn" ? "alert" : "info") }, { key: "detail", label: "Detail", width: 560 }] }).el,
          ctx.ui.btn("Re-check", { icon: "plug", onClick: () => { panel.replaceChildren(); health(panel); } })));
      })();
    }

    // ------------------------------------------------------------ logs
    function logs(panel) {
      const rows = () => ctx.events.recent(300).reverse().map((e) => ({ seq: e.seq, t: e.timestamp_utc, type: e.event_type, src: e.source, agent: e.agent_id, summary: short(scrub(e.payload)), payload: scrub(e.payload) }));
      const t = ctx.ui.table({ id: "settings.logs", ariaLabel: "Redacted event log", caption: "Recent events (redacted)", rows: rows(), rowKey: (r) => r.seq, filter: true, exportName: "events-redacted", pageSize: 100,
        expand: (r, td) => td.append(el("pre", { class: "tail" }, JSON.stringify(r.payload, null, 2))), emptyTitle: "No events received", emptyWhy: "The stream has delivered no events in this session.",
        columns: [{ key: "seq", label: "Seq", align: "num", width: 70 }, { key: "t", label: "Time", width: 190, render: (r) => ctx.fmt.timeEl(r.t) }, { key: "type", label: "Type", width: 150 }, { key: "src", label: "Source", width: 90 }, { key: "agent", label: "Agent", width: 140, missing: "—" }, { key: "summary", label: "Payload (redacted)", width: 480 }] });
      OFF.push(ctx.events.on("*", () => t.setRows(rows())));
      panel.append(card("Redacted logs", el("p", { class: "hint" }, "Showing the last 300 events this page has received. Secret-like keys, account ids and absolute paths are removed in the browser before display. Server process logs are not exposed."), t.el));
    }

    // ------------------------------------------------------------ usage + approvals
    function usage(panel) {
      const ev = ctx.events.recent(1000).filter((e) => e.payload && e.payload.usage && typeof e.payload.usage === "object");
      const tot = {}; ev.forEach((e) => Object.entries(e.payload.usage).forEach(([k, v]) => { if (typeof v === "number" && Number.isFinite(v)) tot[k] = (tot[k] || 0) + v; }));
      const uCard = card("Usage & cost", Object.keys(tot).length ? kv(Object.entries(tot).map(([k, v]) => [k, String(v)])) : note("info", el("strong", {}, "Not measured. "), "No event received in this session carries a measured usage object, so no token or cost figure is shown rather than an estimate."),
        ev.length ? el("p", { class: "hint" }, `Summed over ${ev.length} event(s) carrying a usage field in this session's buffer.`) : null);
      const slot = el("div", {}, ctx.ui.skeleton({ lines: 3 }));
      panel.append(uCard, card("Approvals (approval.requested / approval.resolved events)", slot));
      (async () => {
        try {
          const snap = await ctx.events.snapshot(); const rows = snap.approvals || [];
          slot.replaceChildren(ctx.ui.table({ id: "settings.approvals", ariaLabel: "Approvals", caption: "Approvals", rows, rowKey: (r) => r.approval_id, exportName: "approvals", columnMenu: false, emptyTitle: "No approval requests", emptyWhy: "No agent has requested an approval, and no approval can authorise paper or live trading in this build.",
            columns: [{ key: "approval_id", label: "Id", width: 180 }, { key: "summary", label: "Summary", width: 360 }, { key: "requested_by", label: "Requested by", width: 140, missing: "—" }, { key: "status", label: "Status", width: 120, value: (r) => r.status || r.resolution || "pending" }] }).el);
        } catch (e) { ctx.ui.inlineError(slot, errText(e)); }
      })();
    }

    // ------------------------------------------------------------ safety limits
    function safety(panel) {
      const slot = el("div", {}, ctx.ui.skeleton({ lines: 4 })); panel.append(slot);
      (async () => {
        let refusedTest = null, refusedApprove = null;
        try { const a = await ctx.api.get("/api/research/audit?limit=1000"); refusedTest = a.entries.filter((e) => e.outcome === "refused_test_segment").length; refusedApprove = a.entries.filter((e) => e.outcome === "refused_approval").length; } catch { /* shown as unknown */ }
        const items = [
          ["Live trading", "blocked", "Not allowed. Requires explicit recorded user approval, an approved risk policy and a broker adapter; none exists."],
          ["Paper trading", "blocked", "Allowed in principle for the new account, but only through an approved broker adapter and a deterministic risk engine, which do not exist yet."],
          ["Order submission", "no path", "No endpoint, module or button submits orders. The connector's order-instruction tool is never used."],
          ["Final test set", "sealed", `Evaluated at most once per frozen strategy via the final-test guard (results/test_access_log.jsonl). Dashboard attempts refused and logged: ${refusedTest ?? "unknown (audit unreadable)"}.`],
          ["Strategy states approved_paper / approved_live", "refused", `Server returns 403; attempts are recorded. Refused so far: ${refusedApprove ?? "unknown"}.`],
          ["Mode PAPER / LIVE / SHADOW", "refused", "PAPER and LIVE: blocked pending approval and adapter. SHADOW: no market-data feed or shadow-order recorder."],
          ["Account data", "read-only", `Display mode ${ctx.env.mode}; execution: ${ctx.env.execution}. The connected account is new and empty; nothing is shown as a zero balance.`]];
        slot.replaceChildren(card("Safety limits (read-only)", el("p", { class: "hint" }, "These are enforced in backend code and tests; this list explains them, it cannot change them."),
          ctx.ui.table({ id: "settings.safety", ariaLabel: "Safety limits", caption: "Safety limits", rows: items.map(([name, state, why]) => ({ name, state, why })), rowKey: (r) => r.name, exportable: false, columnMenu: false,
            columns: [{ key: "name", label: "Limit", width: 240 }, { key: "state", label: "State", width: 120, render: (r) => ctx.ui.badge("neutral", r.state, "lock") }, { key: "why", label: "Why", width: 620 }] }).el));
      })();
    }

    const tabs = ctx.ui.tabs({ id: "settings.tabs", label: "Settings sections", tabs: [
      { id: "prefs", label: "Preferences", render: prefs }, { id: "audit", label: "Audit log", render: (p) => auditTab(p, false) }, { id: "config", label: "Configuration history", render: (p) => auditTab(p, true) },
      { id: "health", label: "Integration health", render: health }, { id: "logs", label: "Logs", render: logs }, { id: "usage", label: "Usage & approvals", render: usage }, { id: "safety", label: "Safety limits", render: safety }] });
    root.append(tabs.el);
  },
  unmount() { this._off.forEach((f) => { try { f(); } catch { /* ignore */ } }); this._off = []; },
};
