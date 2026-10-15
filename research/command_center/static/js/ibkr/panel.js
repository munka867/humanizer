// IBKR panel for Data & Connections (MARKET worker). Read-only: connect to the user's own TWS/IB Gateway, see status, download history.
// Nothing here can place, change or cancel an order. Not yet verified against a real TWS/Gateway.
import { el } from "../core/dom.js";
import { fmt } from "../core/format.js";
import { ibkrStore, chipText } from "../market/ibkr.js";

const KIND_LABEL = { paper_tws: "TWS, paper account (port 7497)", paper_gateway: "IB Gateway, paper account (port 4002)" };
const BARS = [["1d", "Daily"], ["1h", "1 hour"], ["15m", "15 minutes"], ["5m", "5 minutes"], ["1m", "1 minute"]];
const loadedCss = (() => { let p; return () => (p ||= new Promise((res) => { if (document.getElementById("ib-css")) return res(); const l = el("link", { rel: "stylesheet", href: "/static/css/ibkr.css", id: "ib-css" }); l.onload = l.onerror = () => res(); document.head.append(l); setTimeout(res, 1500); })); })();

/** mountIbkrPanel(ctx, slot) -> dispose(). */
export function mountIbkrPanel(ctx, slot) {
  const ui = ctx.ui, store = ibkrStore(ctx);
  let alive = true, jobTimer = null, lastJobs = [];
  const root = el("section", { class: "ib-panel", "aria-labelledby": "ib-h", "data-testid": "ibkr-panel" });
  slot.replaceChildren(root);
  const errBox = el("div", { "aria-live": "polite" });

  function errorView(le) {
    if (!le) return null;
    return el("div", { class: "ib-err", role: "alert", dataset: { testid: "ibkr-error" } }, el("b", {}, le.title || "Error"), el("span", {}, le.message), el("span", { class: "ib-fix" }, el("strong", {}, "What to do: "), le.fix || "See docs/IBKR_SETUP.md."),
      el("span", { class: "muted mk-fine" }, `${le.at ? fmt.time(le.at) : ""}${le.code ? ` · IBKR code ${le.code}` : ""}${le.basis ? ` · basis: ${le.basis}` : ""}`), le.raw ? el("details", {}, el("summary", {}, "Raw message"), le.raw) : null);
  }

  function statusBox(st) {
    const ch = chipText(st), tone = { ok: "ok", warn: "warn", bad: "bad", demo: "demo", neutral: "neutral" }[ch.tone] || "neutral";
    const f = st.feeds || {};
    const asof = (x) => (x?.as_of ? fmt.time(x.as_of) : "—");
    return el("div", { class: "ib-box", dataset: { testid: "ibkr-status" } }, el("h4", {}, "Status"),
      el("div", { class: "row" }, ui.badge(tone, ch.label, ch.icon), ui.badge("neutral", "Execution: none", "shield"), ui.badge("info", "Read-only", "lock")),
      st.state === "error" && st.next_retry_at ? el("p", { class: "muted mk-fine" }, `Retrying automatically (attempt ${st.attempts}); next try ${fmt.time(st.next_retry_at, { date: false })}.`) : null,
      el("dl", { class: "ib-feeds" }, el("dt", {}, "Connection"), el("dd", {}, st.state === "connected" ? `${st.label}, server time ${fmt.time(st.server_time)}` : st.state),
        el("dt", {}, "Quotes feed"), el("dd", {}, `as of ${asof(f.quotes)}${f.quotes?.data_type ? " · tick type " + f.quotes.data_type + " (1 live, 3 delayed)" : ""}`),
        el("dt", {}, "Account feed"), el("dd", {}, `as of ${asof(f.account)}${st.account_masked ? " · account " + st.account_masked : ""}`),
        el("dt", {}, "History"), el("dd", {}, `last download ${asof(f.history)}`)),
      errorView(st.last_error));
  }

  function steps(st) {
    const lib = st.library || {};
    return el("div", { class: "ib-box" }, el("h4", {}, "Setup checklist"),
      el("ol", { class: "ib-steps" },
        el("li", {}, "Install TWS or IB Gateway from IBKR and log in to your PAPER account (IBKR says a paper account needs an approved and funded live account)."),
        el("li", {}, "In TWS/Gateway open Configure > Settings > API > Settings: tick \"Enable ActiveX and Socket Clients\", leave \"Read-Only API\" ON, set the socket port (7497 TWS paper / 4002 Gateway paper), add 127.0.0.1 to Trusted IPs."),
        el("li", {}, "Install the Python library: ", el("code", {}, "python -m pip install ib_async==2.1.0"), "  ", lib.version === "fake" ? ui.badge("demo", "simulated mode: library not used", "flask") : lib.installed ? ui.badge("ok", `ib_async ${lib.version || ""} installed`, "check") : ui.badge("bad", "ib_async not installed", "x_circle")),
        lib.installed ? null : el("li", { class: "muted" }, lib.message || ""),
        el("li", {}, "Click Connect below, then run ", el("code", {}, "python -m command_center.ibkr.doctor"), " in a terminal if anything fails.")),
      el("a", { class: "btn", href: "/api/docs/IBKR_SETUP.md", target: "_blank", rel: "noopener" }, "Open the setup guide"));
  }

  function controls(st) {
    const kinds = st.allowed_kinds || ["paper_tws", "paper_gateway"];
    const sel = el("select", { class: "input", "aria-label": "Connection kind", id: "ib-kind" }, kinds.map((k) => el("option", { value: k }, KIND_LABEL[k] || k)));
    const busy = st.state === "connecting";
    const connected = st.state === "connected";
    const c = ui.btn(busy ? "Connecting…" : "Connect (read-only)", { kind: "primary", icon: "plug", disabled: connected || busy, onClick: async () => {
      errBox.replaceChildren();
      try { await ctx.api.post("/api/ibkr/connect", { kind: sel.value }, { errorEl: errBox }); store.refresh(); } catch { /* inline */ } } });
    c.dataset.testid = "ibkr-connect";
    const d = ui.btn("Disconnect", { icon: "plug_off", disabled: !connected && st.state !== "error" && !busy, onClick: async () => {
      errBox.replaceChildren(); try { await ctx.api.post("/api/ibkr/disconnect", {}, { errorEl: errBox }); store.refresh(); } catch { /* inline */ } } });
    d.dataset.testid = "ibkr-disconnect";
    return el("div", { class: "ib-box" }, el("h4", {}, "Connection"), el("label", { class: "field" }, el("span", {}, "Connect to"), sel), el("div", { class: "row" }, c, d), errBox,
      el("p", { class: "muted mk-fine" }, "Only this computer is allowed (127.0.0.1) and only PAPER ports (7497, 4002). Live ports (7496, 4001) are always refused, with no override, and after connecting every account id must start with DU (paper) or the connection is refused."));
  }

  // ---------------------------------------------------------------- history download
  const symIn = el("input", { class: "input", id: "ib-syms", value: "SPY, QQQ, IWM", "aria-label": "Symbols", maxlength: 120 });
  const barSel = el("select", { class: "input", id: "ib-bar", "aria-label": "Bar size" }, BARS.map(([v, l]) => el("option", { value: v }, l)));
  const spanIn = el("input", { class: "input", id: "ib-years", type: "number", min: "0.1", max: "30", step: "0.5", value: "2", "aria-label": "Years of history" });
  const sessSel = el("select", { class: "input", id: "ib-sess", "aria-label": "Session" }, el("option", { value: "rth" }, "Regular hours only"), el("option", { value: "eth" }, "Include extended hours"));
  const formErr = el("div", { "aria-live": "polite" });
  const jobsBox = el("div", { class: "ib-jobs", dataset: { testid: "ibkr-jobs" } });
  function historyForm(st) {
    const connected = st.state === "connected";
    const go = ui.btn("Download history", { kind: "primary", icon: "download", disabled: !connected, title: connected ? "" : "Connect first", onClick: async () => {
      formErr.replaceChildren();
      const symbols = symIn.value.split(/[ ,]+/).map((x) => x.trim().toUpperCase()).filter(Boolean);
      try { await ctx.api.post("/api/ibkr/history", { symbols, bar_size: barSel.value, years: Number(spanIn.value), use_rth: sessSel.value === "rth" }, { errorEl: formErr }); pollJobs(); } catch { /* inline */ } } });
    go.dataset.testid = "ibkr-download";
    return el("div", { class: "ib-box" }, el("h4", {}, "Download history (daily and intraday)"),
      el("div", { class: "ib-form" }, el("label", {}, "Symbols (US stocks / ETFs)", symIn), el("label", {}, "Bar size", barSel), el("label", {}, "Years back (max 30 daily, 10 intraday, 5 for 1 minute)", spanIn), el("label", {}, "Session", sessSel)),
      el("div", { class: "row" }, go), formErr,
      el("p", { class: "muted mk-fine" }, "Files go to a NEW folder data/processed_<date>/ (git-ignored) with a provenance manifest. The frozen data/processed/ used by pre-registered runs is never touched or overwritten. IBKR paces history requests (60 per 10 minutes), so long downloads take time; progress below counts real completed requests."),
      jobsBox);
  }
  function drawJobs(jobs) {
    lastJobs = jobs;
    jobsBox.replaceChildren(...jobs.slice(0, 4).map((j) => {
      const p = j.progress || {}, pct = j.status === "done" ? 100 : p.chunks_total_estimate ? Math.min(99, Math.round((p.chunks_done / p.chunks_total_estimate) * 100)) : 0;
      const kind = { done: "ok", running: "info", queued: "neutral", error: "bad", cancelled: "warn" }[j.status] || "neutral";
      const stop = (j.status === "running" || j.status === "queued") ? ui.btn("Stop", { onClick: async () => { try { await ctx.api.post("/api/ibkr/history/stop", { id: j.id }); pollJobs(); } catch { /* ignore */ } } }) : null;
      return el("div", { class: "ib-job", dataset: { jobId: j.id, status: j.status } },
        el("div", { class: "row" }, ui.badge(kind, j.status, j.status === "done" ? "check" : j.status === "error" ? "x_circle" : "clock"), el("span", {}, `${j.symbols.join(", ")} · ${j.bar_size} · ${j.session}`), el("span", { class: "spacer" }), stop),
        j.status === "running" || j.status === "queued" ? el("div", { class: "ib-prog", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": pct, "aria-label": "Download progress (estimate)" }, el("i", { style: { width: pct + "%" } })) : null,
        el("span", { class: "muted" }, `${p.message || ""}${p.chunks_total_estimate && j.status === "running" ? ` · ${p.chunks_done}/${p.chunks_total_estimate} requests (total is an estimate)` : ""}${p.waited_s ? ` · waited ${p.waited_s}s for IBKR pacing` : ""}`),
        j.error ? el("span", { class: "ib-err" }, j.error) : null,
        ...(j.outputs || []).map((o) => el("div", { class: "ib-out" }, `${o.path} · ${o.rows} rows · ${o.first.slice(0, 10)} to ${o.last.slice(0, 10)} · sha256 ${o.sha256.slice(0, 12)}…`)),
        j.status === "done" ? el("span", { class: "muted mk-fine" }, `Saved in ${j.dir}/ with MANIFEST.json (source ${j.source}).`) : null);
    }));
  }
  async function pollJobs() {
    if (!alive) return;
    try { const r = await ctx.api.get("/api/ibkr/history/jobs"); drawJobs(r.jobs || []); } catch { /* keep last */ }
    clearTimeout(jobTimer);
    if (alive && lastJobs.some((j) => j.status === "running" || j.status === "queued")) jobTimer = setTimeout(pollJobs, 1500);
  }

  let lastKey = null, lastStable = null;
  function draw(st, err) {
    if (!alive) return;
    const key = st ? JSON.stringify([st.state, st.fake, st.last_error, st.library, st.allowed_kinds, st.attempts, st.account_masked, st.subscribed > 0, st.feeds]) : String(err);
    const stable = st ? JSON.stringify([st.state, st.fake, st.last_error, st.library, st.allowed_kinds, st.attempts]) : String(err);
    if (key === lastKey) return;
    if (root.contains(document.activeElement) && document.activeElement !== document.body && stable === lastStable) { return; }   // do not steal focus while the user is typing
    lastKey = key; lastStable = stable;
    const head = el("div", { class: "ib-head" }, el("h3", { id: "ib-h" }, "IBKR live data (read-only)"), ui.badge("warn", "Not yet verified against a real TWS/Gateway", "alert"));
    if (!st) { root.replaceChildren(head, err ? el("div", { class: "ib-err", role: "alert" }, `Could not read /api/ibkr/status: ${err}`) : ui.skeleton({ lines: 3 })); return; }
    const keepErr = errBox.childNodes.length ? [...errBox.childNodes] : null;
    root.replaceChildren(head,
      st.fake ? el("div", { class: "ib-sim", role: "status", dataset: { testid: "fake-banner" } }, ui.icon("flask", 18), el("span", {}, "SIMULATED IBKR (test fixture): not real market data and not a real account. Every record is labelled source=fake and downloads go to data/fake_*.")) : null,
      el("div", { class: "ib-cols" }, el("div", { class: "ib-col" }, steps(st), controls(st)), el("div", { class: "ib-col" }, statusBox(st))), historyForm(st));
    if (keepErr) errBox.replaceChildren(...keepErr);
    if (!jobTimer) pollJobs();
  }
  loadedCss().then(() => { if (!alive) return; const off = store.on(draw); stopStore = off; });
  let stopStore = () => {};
  pollJobs();
  return () => { alive = false; clearTimeout(jobTimer); stopStore(); };
}
