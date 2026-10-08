// Data & Connections workspace (RESEARCH worker): coverage + adequacy, account-export import, connection status.
import { el } from "../core/dom.js";
import { icon } from "../core/icons.js";
import { useCss, errText, field, selectEl, textInput, kv, note, card, poller } from "../research/kit.js";

const MAX_BYTES = 5 * 1024 * 1024;
const SECRET_RE = /token|secret|passw|api[_-]?key|authorization|cookie/i;
const redactObj = (o) => JSON.parse(JSON.stringify(o, (k, v) => (SECRET_RE.test(k) ? "[redacted]" : v)));

export default {
  id: "data", title: "Data & Connections", icon: "data",
  _off: [],
  async mount(host, ctx) {
    useCss();
    const root = el("div", { class: "rs" }); host.replaceChildren(root);
    root.append(el("div", { class: "rs-head" }, el("div", {}, el("h1", {}, "Data & Connections"),
      el("p", {}, "Local daily-bar coverage and quality, IBKR account-export import (stored raw, not normalised), and the honest state of every connection. Market data and account exports are kept apart."))));
    const connSlot = el("div", {}), covSlot = el("div", {}), impSlot = el("div", {}), regSlot = el("div", {});
    root.append(connSlot, covSlot, impSlot, regSlot);
    const timers = [];
    this._off = [() => timers.forEach((t) => t.stop())];

    // ---------------------------------------------------------------- connections
    let lastCheck = null;
    async function paintConn(manual) {
      const out = { checkedAt: new Date() };
      try {
        const [c, h] = await Promise.all([ctx.api.get("/api/connections"), ctx.api.get("/api/health")]);
        out.c = c; out.h = h; out.ok = true;
      } catch (e) { out.ok = false; out.err = errText(e); }
      lastCheck = out;
      const st = ctx.events.state();
      if (!out.ok) { connSlot.replaceChildren(card("Connections", note("bad", `Re-check failed: ${out.err}. The previous state is not trustworthy.`))); return; }
      const c = out.c; const b = c.broker, md = c.market_data, es = c.event_stream;
      const rows = [["Checked at", ctx.fmt.timeEl(out.checkedAt)]];
      connSlot.replaceChildren(card("Connections",
        el("div", { class: "rs-grid" },
          el("div", { class: "rs-card" }, el("h3", {}, "Broker"), el("div", { class: "row" }, ctx.ui.badge("warn", "not connected", "plug_off"), ctx.ui.badge("neutral", "execution: none", "shield")),
            el("p", {}, b.reason), el("p", { class: "hint" }, b.detail),
            note("lock", el("strong", {}, "To enable paper trading you need: "), "an approved broker adapter, a deterministic risk engine with enforced limits, and your explicit recorded approval. None exists. Live trading is not allowed. This app has no order path.")),
          el("div", { class: "rs-card" }, el("h3", {}, "Market data"), el("div", { class: "row" }, ctx.ui.badge(md.state === "stale" ? "warn" : md.state === "unavailable" ? "bad" : "info", md.state, md.state === "unavailable" ? "x_circle" : "clock")),
            kv([["Source", md.source], ["Interval", md.interval], ["Instruments", String(md.instruments)], ["Last bar", md.last_bar ? String(md.last_bar).slice(0, 10) : "—"], ["Last successful sync", md.last_retrieved_on ? `${md.last_retrieved_on} (${md.age_days} day(s) ago, from MANIFEST retrieval date)` : "never"],
              ["Source delay", Array.isArray(md.delayed_seconds) ? md.delayed_seconds.join(", ") + " s" : md.delayed_seconds == null ? "—" : `${md.delayed_seconds} s (~${Math.round(md.delayed_seconds / 60)} min delayed feed)`], ["Subscription", "Not reported by the connector files; entitlements are unverified"]]),
            el("p", { class: "hint" }, md.note), md.reason ? note("warn", md.reason) : null),
          el("div", { class: "rs-card" }, el("h3", {}, "Event stream"), el("div", { class: "row" }, ctx.ui.badge(st.conn === "live" ? "ok" : st.conn === "stale" ? "warn" : "neutral", st.conn, st.conn === "live" ? "signal" : "clock")),
            kv([["Transport", es.transport], ["Server max seq", String(es.max_seq)], ["Last seq seen", String(st.lastSeq)], ["Stale after", `${st.staleAfterS} s`], ["Runtime attached", es.runtime_attached ? "yes" : "no (events are recorded; no agent runtime is attached)"]]))),
        el("div", { class: "row" }, ctx.ui.btn("Re-check connections", { icon: "plug", onClick: async (ev) => { ev.currentTarget.disabled = true; await paintConn(true); ctx.ui.toast(lastCheck.ok ? `Re-checked at ${ctx.fmt.time(lastCheck.checkedAt)}: broker ${lastCheck.c.broker.state}, market data ${lastCheck.c.market_data.state}` : `Re-check failed: ${lastCheck.err}`, { kind: lastCheck.ok ? "info" : "error" }); } }),
          el("span", { class: "muted" }, "Fetches /api/connections and /api/health again; nothing is cached.")),
        el("details", {}, el("summary", {}, "Diagnostics (redacted)"), el("pre", { class: "tail" }, JSON.stringify(redactObj({ connections: c, health: out.h }), null, 2)), el("p", { class: "hint" }, "Keys that look like secrets are replaced; storage paths are redacted by the server."))));
    }

    // ---------------------------------------------------------------- coverage
    async function paintCov() {
      covSlot.replaceChildren(card("Market-data coverage", ctx.ui.skeleton({ lines: 4 })));
      try {
        const cov = await ctx.api.get("/api/data/coverage"); const syms = cov.symbols;
        const t = ctx.ui.table({ id: "data.coverage", ariaLabel: "Coverage", caption: "Daily-bar coverage per symbol", exportName: "coverage", rows: syms, rowKey: (r) => r.symbol,
          expand: (r, td) => td.append(covDetail(r)), emptyTitle: "No local market data", emptyWhy: "No *_1d.csv files in data/processed. Pull daily bars through the IBKR connector (read-only) and ingest them.",
          columns: [{ key: "symbol", label: "Symbol", width: 80 }, { key: "first", label: "First", width: 100, missing: "no data" }, { key: "last", label: "Last", width: 100, missing: "no data" },
            { key: "n_bars", label: "Bars", align: "num", width: 70, format: "qty" }, { key: "resolution", label: "Res.", width: 60 }, { key: "missing_sessions", label: "Missing", align: "num", width: 80, format: "qty", title: "Expected weekday sessions (minus holiday candidates) with no bar" },
            { key: "gaps", label: "Gaps >4d", align: "num", width: 90, value: (r) => r.gaps.length, format: "qty" },
            { key: "q", label: "Quality", width: 260, render: (r) => (r.blocking_failures.length ? ctx.ui.badge("bad", `${r.blocking_failures.length} blocking`, "x_circle") : r.quality_failures.length ? ctx.ui.badge("warn", r.quality_failures.map((f) => `${f.count}× ${f.code === "open_or_close_outside_high_low" ? "open/close outside high-low" : f.code.replace(/_/g, " ")}`).join(", "), "alert") : ctx.ui.badge("ok", "no failures", "check")) },
            { key: "adj", label: "Adjustment", width: 110, value: (r) => r.adjustment }, { key: "man", label: "Retrieved", width: 100, value: (r) => (r.manifest ? r.manifest.retrieved_on : null), missing: "no manifest entry" }] });
        // adequacy form
        const checks = syms.filter((s) => s.exists).map((s) => ({ s, cb: el("input", { type: "checkbox", value: s.symbol, checked: true, "aria-label": s.symbol }) }));
        const firsts = syms.map((s) => s.first).filter(Boolean).sort(), lasts = syms.map((s) => s.last).filter(Boolean).sort();
        const start = textInput({ type: "date", value: firsts[firsts.length - 1] || "", label: "Start" }), end = textInput({ type: "date", value: lasts[0] || "", label: "End" });
        const res = selectEl([{ value: "1D", label: "1D" }, { value: "5m", label: "5m (check will say unavailable)" }, { value: "1m", label: "1m (check will say unavailable)" }], "1D", { label: "Resolution" });
        const out = el("div", { "aria-live": "polite" });
        const run = async () => {
          const chosen = checks.filter((c) => c.cb.checked).map((c) => c.cb.value);
          if (!chosen.length || !start.value || !end.value) { out.replaceChildren(note("bad", "Choose at least one symbol and both dates.")); return; }
          try {
            const r = await ctx.api.get(`/api/data/coverage?symbols=${chosen.join(",")}&start=${start.value}&end=${end.value}&resolution=${res.value}`); const a = r.adequacy;
            out.replaceChildren(el("div", { class: "row" }, a.adequate ? ctx.ui.badge("ok", "adequate", "check") : ctx.ui.badge("bad", "not adequate", "x_circle")),
              a.reasons.length ? el("ul", { class: "list-plain" }, a.reasons.map((x) => el("li", {}, el("code", {}, x.code), ` ${x.symbol ? x.symbol + ": " : ""}${x.detail}`))) : el("p", { class: "muted" }, "No reasons against: coverage spans the request and missing sessions are within tolerance."));
          } catch (e) { out.replaceChildren(note("bad", errText(e))); }
        };
        covSlot.replaceChildren(card("Market-data coverage", el("p", { class: "hint" }, `Source: ${cov.market_data_dir}. Daily bars only, unadjusted for dividends. Session check = weekdays minus an approximate holiday list; early closes and special closures are not modelled. Click a row for details.`), t.el,
          el("h4", {}, "Adequacy check"), el("div", { class: "rs-toolbar" }, el("div", { class: "checks" }, checks.map((c) => el("label", { class: "chk" }, c.cb, c.s.symbol))), field("Start", start), field("End", end), field("Resolution", res), ctx.ui.btn("Check adequacy", { icon: "check", onClick: run })), out));
      } catch (e) { covSlot.replaceChildren(card("Market-data coverage", note("bad", `Could not load coverage: ${errText(e)}`))); }
    }
    function covDetail(r) {
      return el("div", { class: "rs-grid" }, kv([["File", r.file], ["Source", r.manifest ? `${r.manifest.exchange} · conId ${r.manifest.contract_id} · ${r.manifest.source_tag} prices` : "no manifest entry"],
        ["Manifest sha256", r.manifest ? el("span", { class: "sha" }, r.manifest.sha256) : "—"], ["Raw file verified", r.manifest ? (r.manifest.raw_file_sha256_verified === true ? "yes" : r.manifest.raw_file_sha256_verified === false ? "MISMATCH" : "raw file not present locally") : "—"],
        ["Expected sessions", String(r.expected_sessions)], ["Missing sessions", r.missing_sessions ? `${r.missing_sessions}: ${r.missing_sessions_first_20.join(", ")}` : "0"], ["Bars on holiday-candidate dates", String(r.extra_non_session_bars) + " (informational: e.g. half-days)"],
        ["Manifest mismatch", r.manifest_mismatch && r.manifest_mismatch.length ? r.manifest_mismatch.join("; ") : "none"]]),
        el("div", {}, el("h4", {}, "Quality counters"), kv(Object.entries(r.quality).map(([k, v]) => [k.replace(/_/g, " "), String(v)])), r.notes.map((n) => el("p", { class: "hint" }, n))));
    }

    // ---------------------------------------------------------------- import
    const imp = { preview: null, file: null, content: null };
    function paintImport() {
      const out = el("div", { class: "stack", "aria-live": "polite" }); const fileIn = el("input", { type: "file", accept: ".csv,.xml,.txt", class: "sr-only", id: "imp-file", "aria-label": "Choose an IBKR export file" });
      const dz = el("div", { class: "dropzone", role: "group", "aria-label": "Import drop zone" }, icon("download", 24), el("strong", {}, "Drop an IBKR export here (.csv, .xml, .txt)"),
        el("span", { class: "muted" }, "Flex Query or Trade Confirmation exports. Read in the browser as text (max 5 MB), previewed by the server, stored only if you commit."),
        el("label", { class: "btn", for: "imp-file" }, icon("list", 16), el("span", {}, "Choose file")), fileIn);
      const handle = async (f) => {
        if (!f) return;
        if (!/\.(csv|xml|txt)$/i.test(f.name)) { out.replaceChildren(note("bad", `Unsupported file type: ${f.name}. Accepted: .csv .xml .txt`)); return; }
        if (f.size > MAX_BYTES) { out.replaceChildren(note("bad", `File is ${(f.size / 1048576).toFixed(1)} MB; the limit is 5 MB.`)); return; }
        out.replaceChildren(ctx.ui.skeleton({ lines: 4 }));
        const content = await f.text(); imp.file = f.name; imp.content = content;
        try { imp.preview = await ctx.api.post("/api/import/preview", { filename: f.name, content }, { errorEl: out }); renderPreview(out); }
        catch (e) { if (!out.querySelector(".inline-error")) out.replaceChildren(note("bad", `Preview failed: ${errText(e)}`)); }
      };
      fileIn.addEventListener("change", async () => { const f = fileIn.files[0]; fileIn.value = ""; await handle(f); }); // reset so the same file can be chosen again
      ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("over"); }));
      ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("over"); }));
      dz.addEventListener("drop", (e) => handle(e.dataTransfer?.files?.[0]));
      impSlot.replaceChildren(card("Import IBKR account export",
        note("warn", el("strong", {}, "No real IBKR export sample exists yet. "), "Field names are from public documentation recollection and are UNVERIFIED; tests use SYNTHETIC fixtures. Imports are stored raw; nothing is normalised or reconciled. See docs/IMPORT_SUPPORT_MATRIX.md."),
        el("div", { class: "row" }, ctx.ui.btn("Support matrix", { icon: "list", onClick: async () => { const m = await import("../research/docviewer.js"); m.openDoc(ctx, "IMPORT_SUPPORT_MATRIX.md"); } })),
        dz, out));
    }
    function renderPreview(out) {
      const p = imp.preview; const rc = p.row_counts;
      const map = ctx.ui.table({ id: "data.map", ariaLabel: "Field mapping", caption: "Field mapping (support matrix)", rows: p.field_mapping, rowKey: (r) => r.normalized_field, exportable: false, columnMenu: false, compact: true,
        columns: [{ key: "normalized_field", label: "Normalized field", width: 140 }, { key: "required", label: "Required", width: 80, value: (r) => (r.required ? "yes" : "no") }, { key: "source_field", label: "Source field", width: 140, missing: "not in file" },
          { key: "status", label: "Status", width: 160, render: (r) => ctx.ui.badge(r.status === "mapped" ? "ok" : r.status.startsWith("MISSING") ? "bad" : "neutral", r.status, r.status === "mapped" ? "check" : r.status.startsWith("MISSING") ? "x_circle" : "dash") }] });
      const prevCols = p.header_fields.slice(0, 12).map((h) => ({ key: h, label: h, width: 120, value: (r) => r.values[h] }));
      const prev = ctx.ui.table({ id: "data.prev", ariaLabel: "Preview rows", caption: `First ${p.preview_rows.length} rows (account fields masked)`, rows: p.preview_rows, rowKey: (r) => r.line, exportable: false, compact: true,
        columns: [{ key: "line", label: "Line", align: "num", width: 60, format: "qty" }, ...prevCols], emptyTitle: "No rows", emptyWhy: "No data rows were found." });
      const q = ctx.ui.table({ id: "data.quar", ariaLabel: "Quarantined rows", caption: `Quarantined rows${p.quarantined_truncated ? " (first 50)" : ""}`, rows: p.quarantined, rowKey: (r) => r.line, exportable: false, columnMenu: false, compact: true,
        emptyTitle: "None", emptyWhy: "No row was quarantined.", columns: [{ key: "line", label: "Line", align: "num", width: 70, format: "qty" }, { key: "reason", label: "Reason", width: 420 }] });
      const commit = ctx.ui.btn("Commit (store raw file)", { kind: "primary", icon: "check", disabled: !p.would_commit, title: p.would_commit ? "" : p.duplicate_of ? "Duplicate of an existing import" : "Format not recognised or file malformed" });
      commit.addEventListener("click", async () => {
        commit.disabled = true;
        try {
          const r = await ctx.api.post("/api/import/commit", { filename: imp.file, content: imp.content }, { errorEl: res });
          if (r.duplicate) res.replaceChildren(note("info", el("strong", {}, "Duplicate: nothing was stored. "), `Identical bytes were already imported as #${r.existing.id} (${r.existing.filename}) at ${r.existing.imported_at}.`));
          else { ctx.ui.toast(`Stored ${r.entry.filename} (${r.entry.size_bytes} bytes), registry #${r.entry.id}. Not normalised; not reconciled.`, { kind: "success", title: "Import committed" });
            res.replaceChildren(note("info", `Committed as registry #${r.entry.id}; sha256 ${r.entry.sha256.slice(0, 16)}…`)); }
          await paintRegistry();
        } catch (e) { if (!res.querySelector(".inline-error")) res.replaceChildren(note("bad", `Not committed: ${errText(e)}`)); commit.disabled = false; }
      });
      const res = el("div", { "aria-live": "polite" });
      out.replaceChildren(
        el("div", { class: "row" }, ctx.ui.badge(p.format === "unknown" ? "bad" : "info", `format: ${p.format}`, p.format === "unknown" ? "x_circle" : "info"), ctx.ui.badge("neutral", p.container || "?", "list"),
          p.duplicate_of ? ctx.ui.badge("warn", `duplicate of #${p.duplicate_of.id}`, "alert") : ctx.ui.badge("ok", "not a duplicate", "check"), ctx.ui.badge("neutral", "reconciliation: not performed", "dash")),
        el("p", { class: "hint" }, p.format_confidence), p.parse_error ? note("bad", el("strong", {}, "Parse error: "), p.parse_error) : null,
        p.notes.map((n) => note("info", n)), p.missing_required_fields.length ? note("bad", `Missing required fields: ${p.missing_required_fields.join(", ")}`) : null,
        kv([["File", p.filename], ["Size", `${p.size_bytes} bytes`], ["sha256", el("span", { class: "sha" }, p.sha256)], ["Rows", `${rc.total} total · ${rc.valid} valid · ${rc.quarantined} quarantined`],
          ["Reconciliation", p.reconciliation], ["Support", p.support]]),
        el("h4", {}, "Preview"), prev.el, el("div", { class: "rs-grid-2" }, el("div", {}, el("h4", {}, "Field mapping"), map.el), el("div", {}, el("h4", {}, "Validation: quarantined rows"), q.el)),
        el("div", { class: "row" }, commit, el("span", { class: "muted" }, "Stores the raw text under data/raw/ibkr_exports/ (git-ignored) and registers its checksum.")), res);
    }
    async function paintRegistry() {
      try {
        const r = await ctx.api.get("/api/import/registry");
        regSlot.replaceChildren(card("Import registry", el("p", { class: "hint" }, r.note), ctx.ui.table({ id: "data.registry", ariaLabel: "Import registry", caption: "Committed imports", exportName: "imports", rows: r.entries, rowKey: (x) => x.id,
          emptyTitle: "No imports committed", emptyWhy: "Nothing has been imported. There is no real account data in this app yet.",
          columns: [{ key: "id", label: "#", align: "num", width: 44, format: "qty" }, { key: "filename", label: "File", width: 200 }, { key: "format", label: "Format", width: 130 }, { key: "size_bytes", label: "Bytes", align: "num", width: 70, format: "qty" },
            { key: "rows", label: "Valid / quarantined", width: 140, value: (x) => `${x.row_counts.valid} / ${x.row_counts.quarantined}` }, { key: "imported_at", label: "Imported", width: 150, render: (x) => ctx.fmt.timeEl(x.imported_at, { label: false }) },
            { key: "sha256", label: "sha256", width: 140, render: (x) => el("span", { class: "sha" }, x.sha256.slice(0, 14) + "…") }, { key: "status", label: "Status", width: 190 }] }).el));
      } catch (e) { regSlot.replaceChildren(card("Import registry", note("bad", `Could not load registry: ${errText(e)}`))); }
    }
    paintImport();
    await Promise.all([paintConn(false), paintCov(), paintRegistry()]);
  },
  unmount() { this._off.forEach((f) => { try { f(); } catch { /* ignore */ } }); this._off = []; },
};
