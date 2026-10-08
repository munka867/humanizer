// Research & Strategies workspace (RESEARCH worker). Registry, state changes, read-only research docs.
import { el } from "../core/dom.js";
import { useCss, errText, field, selectEl, textInput, kv, note, stateBadge, verdictBadge, card } from "../research/kit.js";
import { openDoc } from "../research/docviewer.js";

const REFUSAL_HINT = "approved_paper and approved_live are shown so the refusal is visible: the server will refuse and record the attempt.";

export default {
  id: "research", title: "Research & Strategies", icon: "research",
  _off: [],
  async mount(host, ctx) {
    useCss();
    const S = { list: [], filters: { state: "all", family: "all", q: "" } };
    const root = el("div", { class: "rs" });
    host.replaceChildren(root);
    const head = el("div", { class: "rs-head" }, el("div", {}, el("h1", {}, "Research & Strategies"),
      el("p", {}, "Strategy registry with lifecycle state, pre-registered rules and evidence links. Rejected and inconclusive candidates stay visible. Nothing here places orders; approved_paper / approved_live cannot be reached.")));
    const tableSlot = el("div", {}, ctx.ui.skeleton({ variant: "table", lines: 5 }));
    const toolbar = el("div", { class: "rs-toolbar", role: "group", "aria-label": "Strategy filters" });
    const docsSlot = el("div", {}, ctx.ui.skeleton({ lines: 3 }));
    const summary = el("div", { class: "row", "aria-live": "polite" });
    root.append(head, card("Strategy library", toolbar, summary, tableSlot), card("Hypotheses & research documents (read-only)", docsSlot));

    let tbl = null;
    const visible = () => S.list.filter((s) => (S.filters.state === "all" || s.state === S.filters.state) && (S.filters.family === "all" || s.family === S.filters.family)
      && (!S.filters.q || (s.name + " " + s.id).toLowerCase().includes(S.filters.q.toLowerCase())));
    const paint = () => {
      const rows = visible();
      const counts = {}; S.list.forEach((s) => { counts[s.state] = (counts[s.state] || 0) + 1; });
      summary.replaceChildren(...["candidate", "testing", "rejected", "approved_paper", "approved_live"].map((k) => el("span", { class: "row" }, stateBadge(ctx, k), el("span", { class: "muted" }, String(counts[k] || 0)))),
        el("span", { class: "muted" }, `${rows.length} of ${S.list.length} shown`));
      if (tbl) tbl.setRows(rows);
    };
    const buildTable = () => {
      tbl = ctx.ui.table({ id: "research.strategies", ariaLabel: "Strategies", caption: "Strategy registry", exportName: "strategies", rows: visible(), rowKey: (s) => s.id,
        onRowActivate: (s) => openDetail(s.id), onRowClick: (s) => openDetail(s.id),
        emptyTitle: "No strategies match", emptyWhy: "Change the filters. The registry itself is configs/strategies.json.",
        columns: [
          { key: "name", label: "Strategy", width: 250, value: (s) => s.name },
          { key: "family", label: "Family", width: 70 },
          { key: "state", label: "State", width: 140, render: (s) => stateBadge(ctx, s.state) },
          { key: "verdict", label: "Pipeline verdict", width: 170, value: (s) => s.verdict, render: (s) => verdictBadge(ctx, s.verdict), missing: "No run has produced a verdict" },
          { key: "version", label: "Version", width: 170 },
          { key: "n_exp", label: "Experiments", align: "num", width: 110, value: (s) => s.experiments.length, format: "qty" },
          { key: "note", label: "Status", width: 270, value: (s) => (s.runnable ? "Runnable. " : "Not runnable. ") + s.status_note },
        ] });
      tableSlot.replaceChildren(tbl.el);
    };

    // ---- filters
    const fState = selectEl([{ value: "all", label: "All states" }, ...["candidate", "testing", "rejected", "approved_paper", "approved_live"].map((v) => ({ value: v, label: v }))], "all",
      { label: "State filter", on: { change: (e) => { S.filters.state = e.target.value; paint(); } } });
    const fFam = selectEl([{ value: "all", label: "All families" }, { value: "H1", label: "H1 intraday sweep" }, { value: "H2", label: "H2 TTrades" }, { value: "H3", label: "H3 daily" }], "all",
      { label: "Family filter", on: { change: (e) => { S.filters.family = e.target.value; paint(); } } });
    const fQ = textInput({ placeholder: "name or id", label: "Search strategies", on: { input: (e) => { S.filters.q = e.target.value; paint(); } } });
    toolbar.append(field("State", fState), field("Family", fFam), field("Search", fQ));

    async function load() {
      try {
        const r = await ctx.api.get("/api/strategies");
        S.list = r.strategies; if (!tbl) buildTable(); paint();
      } catch (e) { ctx.ui.inlineError(tableSlot, `Could not load strategies: ${errText(e)}`); }
    }

    // ---- detail drawer
    async function openDetail(id) {
      const body = el("div", { class: "rs" }, ctx.ui.skeleton({ lines: 6 }));
      const d = ctx.ui.drawer({ title: id, width: 640, label: `Strategy ${id}`, content: body });
      try {
        const s = await ctx.api.get(`/api/strategies/${encodeURIComponent(id)}`);
        d.setTitle(s.name);
        body.replaceChildren(...detailNodes(s, d));
      } catch (e) { ctx.ui.inlineError(body, `Could not load: ${errText(e)}`); }
    }
    function docLink(doc, section) {
      return el("button", { type: "button", class: "link-btn", on: { click: () => openDoc(ctx, doc, section) } }, doc + (section ? ` — ${section}` : ""));
    }
    function detailNodes(s, drawer) {
      const nodes = [];
      nodes.push(el("div", { class: "row" }, stateBadge(ctx, s.state), verdictBadge(ctx, s.verdict), s.runnable ? ctx.ui.badge("ok", "runnable in builder", "check") : ctx.ui.badge("neutral", "not runnable", "lock")));
      nodes.push(el("p", {}, s.status_note));
      if (!s.runnable) nodes.push(note("lock", s.not_runnable_reason || "Not runnable."));
      nodes.push(card("Rules & version", kv([["Rules", docLink(s.rules.doc, s.rules.section)], ["Version", s.version], ["Id", el("code", {}, s.id)]])));
      nodes.push(card("Source research (docs/SOURCES.md)", el("ul", { class: "list-plain" }, s.source_research.map((x) => el("li", {}, docLink(x.doc), el("div", { class: "muted" }, x.item))))));
      if (s.dataset) {
        const sh = s.dataset.manifest_sha256 || {}; const match = s.dataset_matches_current_manifest || {};
        nodes.push(card("Dataset", kv([["Symbols", s.dataset.symbols.join(", ")], ["Period", s.dataset.period], ["Resolution", s.dataset.resolution], ["Adjustment", s.dataset.adjustment], ["Manifest", el("code", {}, s.dataset.manifest)]]),
          el("ul", { class: "list-plain" }, Object.entries(sh).map(([k, v]) => el("li", {}, el("strong", {}, k + " "), el("span", { class: "sha" }, v), " ",
            match[k] === true ? ctx.ui.badge("ok", "matches current manifest", "check") : match[k] === false ? ctx.ui.badge("bad", "differs from current manifest", "alert") : null)))));
      } else nodes.push(card("Dataset", el("p", { class: "muted" }, s.dataset_note || "No dataset.")));
      nodes.push(card("Experiments (docs/EXPERIMENT_LOG.md ids)", s.experiments.length ? el("ul", { class: "list-plain" }, s.experiments.map((x) => el("li", {}, el("code", {}, x)))) : el("p", { class: "muted" }, "None registered."),
        s.runnable ? ctx.ui.btn("Open in experiment builder", { icon: "backtest", onClick: () => { drawer.close(); ctx.nav("backtest", { strategy: s.id }); } }) : null));
      nodes.push(card("Validation links", el("ul", { class: "list-plain" }, s.validation.map((x) => el("li", {}, docLink(x.doc, x.section))))));
      nodes.push(card("Approval history", el("p", { class: "muted" }, s.approval_history.length ? "" : "No approvals have been recorded, and none can be: " + s.approved_states_note + "."),
        s.refused_approval_attempts ? el("p", {}, `${s.refused_approval_attempts} refused attempt(s) are recorded in the audit log.`) : null));
      nodes.push(card("State history", s.state_history && s.state_history.length ? el("ul", { class: "list-plain" }, s.state_history.map((h) => el("li", {}, ctx.fmt.timeEl(h.ts), ` ${h.from_state} → ${h.to_state}: `, h.reason))) : el("p", { class: "muted" }, `No state change recorded; registered state is ${s.registered_state}.`)));
      nodes.push(stateForm(s, drawer));
      return nodes;
    }
    function stateForm(s, drawer) {
      const opts = [...s.allowed_state_transitions.map((v) => ({ value: v, label: v })), { value: "approved_paper", label: "approved_paper (not available)" }, { value: "approved_live", label: "approved_live (not available)" }];
      const sel = selectEl(opts, opts[0].value, { label: "New state" });
      const reason = el("textarea", { class: "input", "aria-label": "Reason", placeholder: "Why? (required, 3-2000 characters)", maxlength: 2000 });
      const out = el("div", { "aria-live": "polite" });
      const btn = ctx.ui.btn("Change state", { kind: "primary", icon: "check", type: "submit" });
      const form = el("form", { class: "rs-card", on: { submit: async (e) => {
        e.preventDefault(); out.replaceChildren();
        if (reason.value.trim().length < 3) { out.append(note("bad", "A reason of at least 3 characters is required.")); reason.focus(); return; }
        btn.disabled = true;
        try {
          const r = await ctx.api.post(`/api/strategies/${encodeURIComponent(s.id)}/state`, { state: sel.value, reason: reason.value.trim() });
          ctx.ui.toast(`${s.id}: state is now ${r.state}`, { kind: "success", title: "State change recorded" });
          drawer.close(); await load(); openDetail(s.id);
        } catch (err) {
          const refused = err.status === 403;
          out.replaceChildren(note("bad", el("strong", {}, refused ? "Refused (HTTP 403). Nothing changed. " : "Not saved. "), el("span", {}, errText(err)),
            refused ? el("div", { class: "hint" }, "The attempt was recorded in the audit log.") : null));
          await load();
        } finally { btn.disabled = false; }
      } } },
        el("h3", {}, "Change state"), field("New state", sel, REFUSAL_HINT), field("Reason (required)", reason), out, el("div", { class: "row" }, btn));
      return form;
    }

    // ---- documents
    async function loadDocs() {
      try {
        const r = await ctx.api.get("/api/docs");
        const feat = ["RESEARCH_INDEX.md", "HYPOTHESES_TTRADES.md", "STRATEGY_SPEC.md", "DAILY_SPEC.md", "DAILY_RESULTS.md", "EXPERIMENT_LOG.md", "VALIDATION_PLAN.md", "REVIEW.md", "SOURCES.md", "IMPORT_SUPPORT_MATRIX.md"];
        const t = ctx.ui.table({ id: "research.docs", ariaLabel: "Research documents", caption: "Research documents", exportName: "docs", filter: true, rows: r.items, rowKey: (x) => x.name,
          onRowActivate: (x) => openDoc(ctx, x.name), onRowClick: (x) => openDoc(ctx, x.name), emptyTitle: "No documents", emptyWhy: "research/docs is empty or unreadable.",
          columns: [{ key: "title", label: "Title", width: 380, render: (x) => el("span", {}, x.title) }, { key: "name", label: "File", width: 240 }, { key: "bytes", label: "Bytes", align: "num", width: 90, format: "qty" },
            { key: "modified", label: "Modified (UTC)", width: 190 }, { key: "feat", label: "Key doc", width: 80, value: (x) => (feat.includes(x.name) ? "yes" : "no") }] });
        docsSlot.replaceChildren(el("div", { class: "row" }, ctx.ui.btn("Open research index", { icon: "list", onClick: () => openDoc(ctx, "RESEARCH_INDEX.md") }),
          ctx.ui.btn("Experiment log", { icon: "flask", onClick: () => openDoc(ctx, "EXPERIMENT_LOG.md") })), t.el);
      } catch (e) { ctx.ui.inlineError(docsSlot, `Could not load documents: ${errText(e)}`); }
    }
    await Promise.all([load(), loadDocs()]);
  },
  unmount() { /* no timers or subscriptions are held */ },
};
