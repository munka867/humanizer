// Backtesting & Monte Carlo workspace (RESEARCH worker). Builder, queue, results, compare, Monte Carlo.
// Everything shown comes from the research API; progress is the pipeline's own finished-step count, never a percentage guess.
import { el } from "../core/dom.js";
import { icon } from "../core/icons.js";
import { useCss, clearTips, errText, field, selectEl, textInput, kv, note, card, lockedControl, stateBadge, statusBadge, verdictBadge, poller, defLabel } from "../research/kit.js";
import { runPath, segmentPanel, sealedPanels, verdictPanel, runHeader, mcPanel } from "../research/results_view.js";

const SEED0 = 20261008;
const COSTS = [["x1", "x1 — modelled costs as pre-registered"], ["x1_5", "x1.5 — costs ×1.5"], ["x2", "x2 — costs ×2"]];

export default {
  id: "backtest", title: "Backtesting & Monte Carlo", icon: "backtest",
  _cleanup: [],
  async mount(host, ctx, params) {
    useCss();
    const S = { strategies: [], coverage: null, exps: [], committed: [], selected: null, cache: new Map(), charts: [], timers: [], tabs: null, detailId: null };
    this._cleanup = [() => S.timers.forEach((t) => t.stop()), () => S.charts.forEach((c) => { try { c.destroy(); } catch { /* already gone */ } })];
    const root = el("div", { class: "rs" });
    host.replaceChildren(root);
    root.append(el("div", { class: "rs-head" }, el("div", {}, el("h1", {}, "Backtesting & Monte Carlo"),
      el("p", {}, "Run registered daily strategies through the pre-registered pipeline on stocks only. Training and validation are separate; the final test is sealed. Monte Carlo resamples the same evidence."))));
    const idx = async () => {
      const [e, c] = await Promise.all([ctx.api.get("/api/experiments"), ctx.api.get("/api/runs/committed")]);
      S.exps = e.experiments; S.committed = c.runs;
    };
    try {
      const [st, cov] = await Promise.all([ctx.api.get("/api/strategies"), ctx.api.get("/api/data/coverage"), idx()]);
      S.strategies = st.strategies; S.coverage = cov;
    } catch (e) { root.append(ctx.ui.empty("Research API unavailable", errText(e))); return; }

    const runOptions = () => [...S.exps.filter((x) => x.status === "completed").map((x) => ({ id: x.id, label: `${x.id.slice(4, 19)} · ${x.strategy_id} · ${x.segment} · ${(x.symbols || []).join("+")}` })),
      ...S.committed.map((x) => ({ id: x.id, label: `committed · ${x.segment} · ${x.variant}` }))];

    // ============================================================ builder
    function builder(panel) {
      const runnable = S.strategies.filter((s) => s.runnable);
      const want = params?.strategy && runnable.some((s) => s.id === params.strategy) ? params.strategy : runnable[0]?.id;
      const stratSel = selectEl(S.strategies.map((s) => ({ value: s.id, label: `${s.name} [${s.state}]${s.runnable ? "" : " — not runnable"}`, disabled: !s.runnable })), want, { label: "Strategy", on: { change: () => { paintStrat(); precheck(); } } });
      const stratInfo = el("div", { "aria-live": "polite" });
      const covSyms = (S.coverage.symbols || []).filter((s) => s.exists && s.n_bars > 0);
      const checks = covSyms.map((s) => { const cb = el("input", { type: "checkbox", value: s.symbol, checked: true, "aria-label": s.symbol, on: { change: () => precheck() } });
        return { cb, node: el("label", { class: "chk" }, cb, el("strong", {}, s.symbol), el("span", { class: "muted" }, `${s.first} → ${s.last} · ${s.n_bars} bars`)) }; });
      const first = covSyms.map((s) => s.first).sort().pop(), last = covSyms.map((s) => s.last).sort()[0];
      const adequacy = el("div", { "aria-live": "polite" });
      const segs = [["train", "Training", "Chronological first 60% of the common span. Used for descriptive statistics; nothing is fitted."],
        ["validation", "Validation", "Next 20% (plus training for the verdict inputs). Purge of 3 calendar days at each boundary."]];
      const segRadios = segs.map(([v, l, d], i) => ({ v, input: el("input", { type: "radio", name: "segment", value: v, checked: i === 0 }), l, d }));
      const seed = textInput({ type: "number", value: SEED0, label: "Seed", min: 0, step: 1 });
      const cost = selectEl(COSTS.map(([value, label]) => ({ value, label })), "x1", { label: "Cost scenario" });
      const notes = el("textarea", { class: "input", "aria-label": "Notes", maxlength: 2000, placeholder: "Why are you running this? (optional)" });
      const msgs = el("div", { "aria-live": "polite" }); const submit = ctx.ui.btn("Queue experiment", { kind: "primary", icon: "flask", type: "submit" });
      function paintStrat() {
        const s = S.strategies.find((x) => x.id === stratSel.value); if (!s) return;
        stratInfo.replaceChildren(el("div", { class: "row" }, stateBadge(ctx, s.state), verdictBadge(ctx, s.verdict), el("span", { class: "muted" }, s.version)),
          el("p", { class: "hint" }, s.status_note), s.state === "rejected" ? note("warn", "This strategy is rejected by its pre-registered rule. Re-running reproduces evidence; it does not re-open the strategy.") : null);
      }
      let seq = 0;
      async function precheck() {
        const chosen = checks.filter((c) => c.cb.checked).map((c) => c.cb.value); const my = ++seq;
        if (!chosen.length) { adequacy.replaceChildren(note("bad", "Select at least one symbol.")); return; }
        adequacy.replaceChildren(ctx.ui.skeleton({ lines: 1 }));
        try {
          const r = await ctx.api.get(`/api/data/coverage?symbols=${chosen.join(",")}&start=${first}&end=${last}&resolution=1D`); if (my !== seq) return;
          const a = r.adequacy;
          adequacy.replaceChildren(a.adequate ? el("div", { class: "row" }, ctx.ui.badge("ok", "data adequate for 1D over the common span", "check"), el("span", { class: "muted" }, `${first} → ${last}`))
            : note("bad", el("strong", {}, "Data inadequate. "), el("ul", {}, a.reasons.map((x) => el("li", {}, (x.symbol ? x.symbol + ": " : "") + x.detail)))));
        } catch (e) { adequacy.replaceChildren(note("warn", `Could not check adequacy: ${errText(e)}`)); }
      }
      const form = el("form", { class: "rs", novalidate: true, on: { submit: async (e) => {
        e.preventDefault(); msgs.replaceChildren();
        const symbols = checks.filter((c) => c.cb.checked).map((c) => c.cb.value); const segment = segRadios.find((r) => r.input.checked).v; const sd = Number(seed.value);
        const errs = [];
        if (!stratSel.value) errs.push("Choose a runnable strategy."); if (!symbols.length) errs.push("Select at least one symbol.");
        if (!Number.isInteger(sd) || sd < 0 || sd >= 2 ** 31) errs.push("Seed must be an integer from 0 to 2147483647.");
        if (errs.length) { msgs.replaceChildren(note("bad", el("ul", {}, errs.map((x) => el("li", {}, x))))); return; }
        submit.disabled = true;
        try {
          const r = await ctx.api.post("/api/experiments", { strategy_id: stratSel.value, symbols, segment, cost_scenario: cost.value, seed: sd, notes: notes.value.trim() });
          ctx.ui.toast(`Queued ${r.experiment.id}`, { kind: "success", title: "Experiment accepted by the server" });
          await refresh(); S.detailId = r.experiment.id; S.tabs.select("queue");
        } catch (err) {
          const items = err.body && err.body.inputs_inadequate;
          if (items) msgs.replaceChildren(note("bad", el("strong", {}, "Not queued: inputs_inadequate. "), el("ul", {}, items.map((x) => el("li", {}, `${x.symbol ? x.symbol + " — " : ""}${x.code}: ${x.detail}`)))));
          else msgs.replaceChildren(note("bad", el("strong", {}, err.status === 403 ? "Refused (403). " : "Not queued. "), errText(err), err.body?.reason ? ` ${err.body.reason}` : ""));
        } finally { submit.disabled = false; }
      } } },
        el("div", { class: "rs-grid-2 stretch" },
          card("Strategy", field("Strategy", stratSel), stratInfo, lockedControl("Strategy parameters", "none (H3 has no free parameters)", "DAILY_SPEC v1 fixes every rule; nothing is tuned. Changing a rule would be a new strategy that must be pre-registered first.")),
          card("Universe & data", el("div", { class: "field" }, el("span", {}, "Stocks / ETFs with local daily bars"), el("div", { class: "checks" }, checks.map((c) => c.node))),
            el("div", { class: "rs-grid-2" }, field("Resolution", selectEl([{ value: "1D", label: "1D (daily)" }, { value: "5m", label: "5m — unavailable", disabled: true }, { value: "1m", label: "1m — unavailable", disabled: true }], "1D", { label: "Resolution" }), "Only daily bars exist locally; no intraday data."),
              lockedControl("Data range", `${first ?? "—"} → ${last ?? "—"}`, "Pre-registered: the whole available span, split 60/20/20 with a 3-day purge. A custom range is not supported.")), adequacy),
          card("Capital & costs", lockedControl("Notional per trade", "$10,000 (fractional shares)", "Pre-registered sizing assumption (DAILY_SPEC). Monte Carlo account size is $10,000."),
            field("Cost scenario (headline row)", cost, "The pipeline always prices x1, x1.5 and x2; this chooses which row is shown first. Cost parameters are unverified assumptions."),
            lockedControl("Session", "regular session only", "Daily bars: entry at the next day's open, exit at its close. No pre/post-market or intraday settings exist.")),
          card("Validation method", el("div", { class: "radio-row", role: "radiogroup", "aria-label": "Segment" },
            ...segRadios.map((r) => el("label", {}, r.input, el("span", {}, el("strong", {}, r.l), el("span", { class: "hint" }, r.d)))),
            el("label", { class: "off", "aria-disabled": "true" }, el("input", { type: "radio", name: "segment", value: "test", disabled: true }), el("span", {}, el("strong", {}, "Final test "), icon("lock", 12), el("span", { class: "hint" },
              "Sealed. Evaluated at most once per frozen strategy through the final-test guard by the research coordinator; this screen can never run it, and a request for it is refused and logged.")))),
            field("Seed", seed, "Pre-registered seed is 20261008. A different seed or symbol subset is recorded as a deviation."), field("Notes", notes))),
        msgs, el("div", { class: "row" }, submit, el("span", { class: "muted" }, "Runs scripts/run_daily.py in a subprocess (one job at a time). Outputs stay on this machine.")));
      panel.append(form); paintStrat(); precheck();
    }

    // ============================================================ queue
    function queue(panel) {
      const detail = el("div", {}); let tbl;
      tbl = ctx.ui.table({ id: "bt.queue", ariaLabel: "Experiments", caption: "Experiment queue and history", exportName: "experiments", rows: S.exps, rowKey: (x) => x.id,
        onRowClick: (x) => { S.detailId = x.id; paintDetail(); }, onRowActivate: (x) => { S.detailId = x.id; paintDetail(); },
        emptyTitle: "No experiments yet", emptyWhy: "Use the Experiment builder to queue one.", emptyAction: { label: "Open builder", onClick: () => S.tabs.select("builder") },
        columns: [{ key: "id", label: "Id", width: 190 }, { key: "strategy_id", label: "Strategy", width: 130 }, { key: "segment", label: "Segment", width: 80 },
          { key: "symbols", label: "Symbols", width: 120, value: (x) => (x.symbols || []).join("+") }, { key: "seed", label: "Seed", align: "num", width: 100, render: (x) => String(x.seed) },
          { key: "status", label: "Status", width: 125, render: (x) => statusBadge(ctx, x.status) },
          { key: "prog", label: "Steps", width: 60, value: (x) => (x.progress ? `${x.progress.steps_done}/${x.progress.steps_total}` : ""), missing: "not started" },
          { key: "started_at", label: "Started", width: 150, render: (x) => ctx.fmt.timeEl(x.started_at, { label: false }), missing: "not started" },
          { key: "finished_at", label: "Finished", width: 150, render: (x) => ctx.fmt.timeEl(x.finished_at, { label: false }), missing: "not finished" }, { key: "exit_code", label: "Exit", align: "num", width: 50, missing: "—" }] });
      panel.append(card("Queue & runs", el("p", { class: "hint" }, "One worker runs jobs in submission order. Progress counts steps the pipeline process has actually finished."), tbl.el), detail);
      S.queueTbl = tbl; S.detailEl = detail; paintDetail();
    }
    async function paintDetail() {
      const d = S.detailEl; if (!d) return;
      if (!S.detailId) { d.replaceChildren(el("p", { class: "muted" }, "Select a run to see its status, steps and process output.")); return; }
      try {
        const j = await ctx.api.get(`/api/experiments/${S.detailId}`);
        const pr = j.progress; const total = pr ? pr.steps_total : 0;
        d.replaceChildren(card(`Run ${j.id}`, el("div", { class: "row" }, statusBadge(ctx, j.status), j.exit_code != null ? el("span", { class: "muted" }, `exit code ${j.exit_code}`) : null),
          kv([["Strategy", j.strategy_id], ["Segment", j.segment], ["Symbols", (j.symbols || []).join(", ")], ["Seed", String(j.seed)], ["Cost headline", j.cost_scenario],
            ["Created", ctx.fmt.timeEl(j.created_at)], ["Started", j.started_at ? ctx.fmt.timeEl(j.started_at) : "not started"], ["Finished", j.finished_at ? ctx.fmt.timeEl(j.finished_at) : "not finished"]]),
          j.params.deviation_note ? note("warn", j.params.deviation_note) : null,
          pr ? el("div", {}, el("div", { class: "prog", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": total, "aria-valuenow": pr.steps_done, "aria-label": "Pipeline steps finished" }, el("i", { style: { width: `${(100 * pr.steps_done) / total}%` } })),
            el("p", { class: "hint" }, `${pr.steps_done} of ${total} pipeline steps finished (count reported by the process; not a time estimate).`),
            el("ul", { class: "steps" }, pr.steps_finished.map((s) => el("li", {}, icon("check", 14), s)))) : el("p", { class: "muted" }, j.status === "queued" ? "Waiting for the worker. No steps have run." : "The process has not reported a step yet."),
          j.error ? note("bad", el("strong", {}, "Error: "), j.error) : null,
          el("h4", {}, "stdout (tail)"), el("pre", { class: "tail" }, j.stdout_tail || "(empty)"), el("h4", {}, "stderr (tail)"), el("pre", { class: "tail" }, j.stderr_tail || "(empty)"),
          el("p", { class: "hint" }, "Output is redacted: paths outside the repository are replaced with <path>."),
          j.status === "completed" ? ctx.ui.btn("Open results", { kind: "primary", icon: "gauge", onClick: () => { S.selected = j.id; S.tabs.select("results"); } }) : null));
      } catch (e) { ctx.ui.inlineError(d, errText(e)); }
    }
    async function refresh() {
      try { await idx(); } catch { return 6000; }
      if (S.queueTbl) S.queueTbl.setRows(S.exps);
      const active = S.exps.some((x) => x.status === "queued" || x.status === "running");
      if (S.detailId && S.detailEl) await paintDetail();
      return active ? 1200 : 6000;
    }
    S.timers.push(poller(refresh, 1500));

    // ============================================================ results
    function runPicker(onPick, current) {
      const opts = runOptions(); const sel = selectEl(opts.map((o) => ({ value: o.id, label: o.label })), current || opts[0]?.id, { label: "Run", on: { change: (e) => onPick(e.target.value) } });
      return { sel, opts };
    }
    async function loadRun(id) {
      if (!S.cache.has(id)) S.cache.set(id, await ctx.api.get(runPath(id) + (id.startsWith("committed:") ? "" : "/result")));
      return S.cache.get(id);
    }
    function results(panel) {
      const out = el("div", { class: "rs" }); const { sel, opts } = runPicker((id) => show(id), S.selected);
      panel.append(el("div", { class: "rs-toolbar" }, field("Run", sel, "Completed experiments and the committed pre-registered runs.")), out);
      async function show(id) {
        clearTips(); S.selected = id; S.charts.splice(0).forEach((c) => { try { c.destroy(); } catch { /* ignore */ } });
        out.replaceChildren(ctx.ui.skeleton({ lines: 6 }));
        try {
          const res = await loadRun(id); if (S.selected !== id) return;
          const nodes = [runHeader(ctx, res), verdictPanel(ctx, res)];
          for (const k of ["training", "validation"]) if (res.evidence[k]) nodes.push(await segmentPanel(ctx, res, k, id, S));
          if (!res.evidence.training) nodes.push(el("section", { class: "seg-panel" }, el("header", {}, el("h3", {}, "Training segment")), el("p", { class: "muted" }, "Not part of this run. Training results are a separate run (compare tab); they are never merged into validation numbers.")));
          if (!res.evidence.validation) nodes.push(el("section", { class: "seg-panel" }, el("header", {}, el("h3", {}, "Validation segment")), el("p", { class: "muted" }, "Not part of this run. Run the validation segment to see it; it is kept separate from training.")));
          nodes.push(...sealedPanels(ctx, res));
          out.replaceChildren(...nodes);
        } catch (e) { out.replaceChildren(note("bad", e.body?.job_status === "failed" ? "This run failed; there is no result. See Queue & runs." : errText(e))); }
      }
      if (opts.length) show(S.selected || sel.value); else out.append(ctx.ui.empty("No runs yet", "Queue an experiment, or check the committed runs once the API lists them."));
    }

    // ============================================================ compare
    function compare(panel) {
      const opts = runOptions(); const checks = opts.map((o) => ({ o, cb: el("input", { type: "checkbox", value: o.id, "aria-label": o.label }) }));
      const out = el("div", {}); const msg = el("div", { "aria-live": "polite" });
      const go = ctx.ui.btn("Compare selected", { kind: "primary", icon: "columns", onClick: async () => {
        const ids = checks.filter((c) => c.cb.checked).map((c) => c.o.id); msg.replaceChildren();
        if (ids.length < 2 || ids.length > 6) { msg.replaceChildren(note("bad", "Select between 2 and 6 runs.")); return; }
        out.replaceChildren(ctx.ui.skeleton({ lines: 5 }));
        try { const r = await ctx.api.get(`/api/experiments/compare?ids=${encodeURIComponent(ids.join(","))}`); out.replaceChildren(...compareView(r)); }
        catch (e) { out.replaceChildren(note("bad", errText(e))); }
      } });
      panel.append(card("Choose runs", el("div", { class: "checks" }, checks.map((c) => el("label", { class: "chk" }, c.cb, el("span", {}, c.o.label)))), el("div", { class: "row" }, go), msg), out);
    }
    function compareView(r) {
      const cols = r.table; const rowDefs = [["n", "Trades", (b) => ctx.fmt.qty(b.n)], ["ev_usd", "EV $/trade", (b) => ctx.fmt.money(b.ev_usd)], ["ci", "95% CI of EV", (b) => (b.ci_usd ? `${ctx.fmt.money(b.ci_usd[0], { dp: 1 })} … ${ctx.fmt.money(b.ci_usd[1], { dp: 1 })}` : "—")],
        ["hit", "Hit rate", (b) => ctx.fmt.pct(b.hit, { ratio: true, sign: false, dp: 1 })], ["dd", "Max drawdown", (b) => ctx.fmt.money(b.maxdd_usd)], ["cost", "EV at headline cost", (b) => (b.selected_cost_row ? ctx.fmt.money(b.selected_cost_row.ev_usd) : "—")]];
      const mk = (key, title) => {
        const t = el("table", { class: "cmp" }, el("caption", { class: "sr-only" }, title), el("thead", {}, el("tr", {}, el("th", {}, "Metric"), cols.map((c) => el("th", {}, c.id.startsWith("committed") ? c.id : c.id.slice(4, 19), el("div", { class: "muted" }, `${c.strategy_id || ""} · ${(c.run.symbols || []).join("+")} · seed ${c.run.seed ?? "?"}`))))),
          el("tbody", {}, rowDefs.map(([, label, f]) => el("tr", {}, el("td", {}, label), cols.map((c) => el("td", {}, c[key] ? f(c[key]) : el("span", { class: "muted" }, "not in this run")))))));
        return card(title, el("div", { class: "cmp-wrap" }, t));
      };
      return [...r.warnings.map((w) => note("warn", w)), mk("training", "Training segment (separate)"), mk("validation", "Validation segment (separate)"),
        card("Verdicts (as produced by the pipeline)", el("div", { class: "cmp-wrap" }, el("table", { class: "cmp" }, el("tbody", {}, cols.map((c) => el("tr", {}, el("td", {}, c.id.startsWith("committed") ? c.id : c.id.slice(4, 19)),
          el("td", {}, c.verdict ? el("div", { class: "row" }, verdictBadge(ctx, c.verdict.value), el("span", { class: "muted" }, (c.verdict.reasons || []).join("; "))) : el("span", { class: "muted" }, "no verdict in this run")))))))),
        el("p", { class: "hint" }, r.note)];
    }

    // ============================================================ monte carlo
    function monte(panel) {
      const out = el("div", { class: "rs" }); const { sel, opts } = runPicker((id) => show(id), S.selected);
      panel.append(note("warn", el("strong", {}, "Conditional simulation — resamples the same evidence, not a probability the strategy works. "), "It adds no independent market evidence."),
        el("div", { class: "rs-toolbar" }, field("Run", sel)), out);
      async function show(id) {
        S.selected = id; out.replaceChildren(ctx.ui.skeleton({ lines: 5 }));
        try { const res = await loadRun(id); out.replaceChildren(runHeader(ctx, res), ...mcPanel(ctx, res)); } catch (e) { out.replaceChildren(note("bad", errText(e))); }
      }
      if (opts.length) show(S.selected || sel.value); else out.append(ctx.ui.empty("No runs yet", "A Monte Carlo block exists for each run that had trades."));
    }

    S.redo = {}; S.fresh = {};
    const wrap = (id, fn) => (p) => { S.fresh[id] = true; S.redo[id] = () => { clearTips(); S.charts.splice(0).forEach((c) => { try { c.destroy(); } catch { /* ignore */ } }); p.replaceChildren(); fn(p); }; fn(p); };
    S.tabs = ctx.ui.tabs({ id: "backtest.tabs", label: "Backtesting sections", active: params?.tab || (params?.strategy ? "builder" : undefined),
      onChange: (tid) => { if (S.fresh[tid]) { S.fresh[tid] = false; return; } if (["results", "compare", "mc"].includes(tid) && S.redo[tid]) S.redo[tid](); }, tabs: [
      { id: "builder", label: "Experiment builder", render: wrap("builder", builder) }, { id: "queue", label: "Queue & runs", render: wrap("queue", queue) },
      { id: "results", label: "Results", render: wrap("results", results) }, { id: "compare", label: "Compare", render: wrap("compare", compare) }, { id: "mc", label: "Monte Carlo", render: wrap("mc", monte) }] });
    root.append(S.tabs.el);
    if (params?.strategy) S.tabs.select("builder");
  },
  unmount() { clearTips(); this._cleanup.forEach((f) => { try { f(); } catch { /* ignore */ } }); this._cleanup = []; },
};
