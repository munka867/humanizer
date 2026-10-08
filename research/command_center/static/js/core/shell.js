// Shell: top bar, navigation, status chips, Risk panel, notifications, token box, banners, keyboard. Pure DOM via el(); no innerHTML.
import { el, clear, debounce, store, uid } from "./dom.js";
import { icon } from "./icons.js";
import { fmt, DASH } from "./format.js";
import { WORKSPACES, byId } from "./registry.js";
import { nyseSession } from "./session.js";
import { MODES } from "./env.js";

const NAV_BREAK_ICONS = 1100, NAV_BREAK_DRAWER = 800;

export function createShell(ctx) {
  const { ui, prefs, env, events, freshness, api, risk } = ctx;
  const $ = (id) => document.getElementById(id);
  const app = $("app"), topbar = $("topbar"), nav = $("nav"), outlet = $("workspace"), banners = $("banners");
  const tone = (t) => ({ ok: "ok", warn: "warn", bad: "bad", info: "info", neutral: "neutral", demo: "demo" }[t] || "neutral");

  let fitQueued = false;
  // ---------------------------------------------------------------- chips
  /** chip({id, pri, icon, label, sub, tone, onClick, aria}) -> {el, set({icon,label,sub,tone,aria,title})} */
  function chip(o) {
    const node = el("button", { type: "button", class: "chip", id: o.id, "aria-haspopup": o.popup === false ? null : "dialog", "aria-expanded": o.popup === false ? null : "false", dataset: { pri: o.pri ?? 5 } });
    const ic = el("span", { class: "chip-ic" }), lbl = el("span", { class: "chip-label" }), sub = el("span", { class: "chip-sub" });
    node.append(ic, lbl, sub);
    if (o.onClick) node.addEventListener("click", () => o.onClick(node));
    const c = { el: node, set(p) {
      if (p.icon !== undefined) ic.replaceChildren(icon(p.icon, 16));
      if (p.label !== undefined) lbl.textContent = p.label;
      if (p.sub !== undefined) { sub.textContent = p.sub || ""; sub.hidden = !p.sub; }
      if (p.tone) node.dataset.tone = tone(p.tone);
      if (p.aria !== undefined) node.setAttribute("aria-label", p.aria);
      if (p.title !== undefined) node.title = p.title;
      scheduleFit();
    } };
    c.set(o);
    return c;
  }

  // ---------------------------------------------------------------- top bar construction
  const navToggle = el("button", { type: "button", class: "icon-btn nav-toggle", "aria-label": "Open navigation", "aria-controls": "nav", "aria-expanded": "false" }, icon("menu", 20));
  const brand = el("a", { class: "brand", href: "#/command_center", "aria-label": "tradelab research workstation, home" }, el("span", { class: "brand-mark", "aria-hidden": "true" }, icon("gauge", 20)), el("span", { class: "brand-name" }, "tradelab", el("span", { class: "brand-sub" }, "research workstation")));
  const titleEl = el("h1", { class: "ws-title", id: "ws-title" }, "");

  // search
  const searchId = uid("search");
  const searchIn = el("input", { type: "search", class: "search-input", id: searchId, placeholder: "Symbol", role: "combobox", "aria-expanded": "false", "aria-controls": `${searchId}-list`, "aria-autocomplete": "list", "aria-label": "Search symbols (press / to focus)", autocomplete: "off", spellcheck: "false" });
  const searchList = el("ul", { class: "search-list", role: "listbox", id: `${searchId}-list`, "aria-label": "Symbol results", hidden: true });
  const searchBox = el("div", { class: "search", role: "search" }, el("span", { class: "search-ic" }, icon("search", 16)), searchIn, el("kbd", { class: "kbd search-kbd", "aria-hidden": "true" }, "/"), searchList);
  searchBox.dataset.pri = 4;

  // environment badge: the TRUE execution environment (there is none)
  const envBadge = el("div", { class: "env-badge", role: "status", id: "env-badge", "aria-label": "Environment: research only. Execution: none, broker adapter not built.", title: "Execution: none — broker adapter not built", dataset: { pri: 10 } },
    icon("shield", 16), el("span", { class: "env-label" }, "ENV", el("span", { class: "env-long" }, "IRONMENT")), el("strong", { class: "env-value" }, "RESEARCH ONLY"), el("span", { class: "env-sub" }, "no execution"));

  // mode selector (separate from navigation)
  const modeBtn = el("button", { type: "button", class: "chip mode-btn", id: "mode-btn", "aria-haspopup": "dialog", "aria-expanded": "false", dataset: { pri: 9, tone: "info" } },
    el("span", { class: "chip-ic" }, icon("flag", 16)), el("span", { class: "mode-k" }, "Mode"), el("strong", { class: "mode-v", id: "mode-value" }, ""), icon("chevron_down", 14));

  const brokerChip = chip({ id: "chip-broker", pri: 7, icon: "plug_off", label: "Broker not connected", tone: "warn", onClick: (a) => brokerPanel(a) });
  const dataChip = chip({ id: "chip-data", pri: 6, icon: "clock", label: "Data", tone: "neutral", onClick: (a) => dataPanel(a) });
  const agentsChip = chip({ id: "chip-agents", pri: 5, icon: "radio", label: "Agents", tone: "neutral", onClick: (a) => agentsPanel(a) });
  const sessionChip = chip({ id: "chip-session", pri: 3, icon: "building", label: "NYSE", tone: "neutral", onClick: (a) => sessionPanel(a) });
  const riskChip = chip({ id: "chip-risk", pri: 8, icon: "shield_off", label: "No risk policy", tone: "warn", onClick: () => openRisk() });
  riskChip.el.setAttribute("aria-haspopup", "dialog");
  const demoChip = chip({ id: "chip-demo", pri: 4, icon: "flask", label: "DEMO events", tone: "demo", popup: false, onClick: () => ctx.nav("agents") });
  demoChip.el.hidden = true; demoChip.el.title = "Events with source='demo' are present: seeded examples, not real activity.";

  const bellCount = el("span", { class: "bell-count", hidden: true, "aria-hidden": "true" });
  const bellBtn = el("button", { type: "button", class: "icon-btn bell", id: "bell", "aria-haspopup": "dialog", "aria-expanded": "false", "aria-label": "Notifications", dataset: { pri: 7 } }, icon("bell", 18), bellCount);
  const tokenBtn = el("button", { type: "button", class: "icon-btn", id: "token-btn", "aria-haspopup": "dialog", "aria-expanded": "false", dataset: { pri: 2 } });
  const themeBtn = el("button", { type: "button", class: "icon-btn", id: "theme-btn", dataset: { pri: 2 } });
  const ovfBtn = el("button", { type: "button", class: "icon-btn ovf-btn", id: "ovf-btn", "aria-label": "More status items", "aria-haspopup": "true", "aria-expanded": "false", "aria-controls": "tb-ovf", hidden: true }, icon("more", 18));
  const ovfPanel = el("div", { class: "tb-ovf", id: "tb-ovf", hidden: true, role: "group", "aria-label": "More status items" });
  const right = el("div", { class: "tb-right", id: "tb-right" });

  const items = [envBadge, modeBtn, riskChip.el, brokerChip.el, dataChip.el, demoChip.el, agentsChip.el, sessionChip.el, bellBtn, tokenBtn, themeBtn];
  right.append(...items, ovfBtn);
  topbar.append(navToggle, brand, titleEl, searchBox, right, ovfPanel);
  searchBox.classList.add("tb-search");

  // ---------------------------------------------------------------- top bar overflow ("fit")
  function scheduleFit() { if (fitQueued) return; fitQueued = true; requestAnimationFrame(() => { fitQueued = false; fit(); }); }
  function fit() {
    for (const n of items) if (n.parentNode !== right) right.insertBefore(n, ovfBtn);
    ovfBtn.hidden = true;
    const order = [...items].filter(n => !n.hidden).sort((a, b) => (+a.dataset.pri) - (+b.dataset.pri));
    let moved = false;
    while (topbar.scrollWidth > topbar.clientWidth + 1 && order.length) {
      const n = order.shift(); ovfPanel.append(n); moved = true; ovfBtn.hidden = false;
    }
    if (!moved) { ovfPanel.hidden = true; ovfBtn.setAttribute("aria-expanded", "false"); }
    else { ovfBtn.dataset.count = ovfPanel.children.length; }
  }
  ovfBtn.addEventListener("click", () => {
    const open = ovfPanel.hidden; ovfPanel.hidden = !open; ovfBtn.setAttribute("aria-expanded", String(open));
    if (open) { const r = ovfBtn.getBoundingClientRect(); ovfPanel.style.top = r.bottom + 6 + "px"; ovfPanel.style.right = Math.max(8, innerWidth - r.right) + "px"; ovfPanel.querySelector("button, input")?.focus(); }
  });
  document.addEventListener("pointerdown", (e) => { if (!ovfPanel.hidden && !ovfPanel.contains(e.target) && !ovfBtn.contains(e.target) && !e.target.closest?.(".popover")) { ovfPanel.hidden = true; ovfBtn.setAttribute("aria-expanded", "false"); } }, true);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !ovfPanel.hidden && !document.querySelector(".popover")) { ovfPanel.hidden = true; ovfBtn.setAttribute("aria-expanded", "false"); ovfBtn.focus(); } });
  new ResizeObserver(() => scheduleFit()).observe(topbar);
  document.fonts?.ready?.then(scheduleFit);

  // ---------------------------------------------------------------- navigation
  const navList = el("ul", { class: "nav-list", role: "list" });
  const navItems = {};
  for (const w of WORKSPACES) {
    const cap = el("span", { class: "nav-cap" }), capIc = el("span", { class: "nav-cap-ic" });
    const a = el("a", { class: "nav-item", href: `#/${w.id}`, dataset: { ws: w.id }, "aria-label": w.title },
      el("span", { class: "nav-ic" }, icon(w.icon, 20)), el("span", { class: "nav-text" }, el("span", { class: "nav-label" }, w.title), el("span", { class: "nav-cap-row" }, capIc, cap)));
    navItems[w.id] = { a, cap, capIc };
    navList.append(el("li", {}, a));
  }
  const collapseBtn = el("button", { type: "button", class: "nav-collapse", id: "nav-collapse", "aria-expanded": "true", "aria-controls": "nav" }, icon("panel_left", 18), el("span", { class: "nav-collapse-label" }, "Collapse"));
  const K = (t) => el("kbd", { class: "kbd" }, t);
  nav.append(navList, el("div", { class: "nav-foot" }, collapseBtn, el("p", { class: "nav-hint" }, el("span", {}, K("Alt"), "+", K("1"), "\u2013", K("8"), " switch"), el("span", {}, K("/"), " search \u00b7 ", K("["), " collapse"))));
  const scrim = $("nav-scrim");

  function navMode() { return innerWidth < NAV_BREAK_DRAWER ? "drawer" : innerWidth < NAV_BREAK_ICONS ? "icons" : (prefs.get("shell.nav_collapsed", false) ? "icons" : "full"); }
  let navOpen = false, lastNavFocus = null, releaseTrap = null;
  function applyNav() {
    const m = navMode(); app.dataset.nav = m;
    const drawerOpen = m === "drawer" && navOpen;
    app.classList.toggle("nav-open", drawerOpen);
    navToggle.setAttribute("aria-expanded", String(drawerOpen)); navToggle.setAttribute("aria-label", drawerOpen ? "Close navigation" : "Open navigation");
    collapseBtn.hidden = m === "drawer" || (m === "icons" && innerWidth < NAV_BREAK_ICONS);
    collapseBtn.setAttribute("aria-expanded", String(m !== "icons"));
    collapseBtn.querySelector(".nav-collapse-label").textContent = m === "icons" ? "Expand" : "Collapse";
    collapseBtn.querySelector("svg").style.transform = m === "icons" ? "scaleX(-1)" : "";
    nav.toggleAttribute("inert", m === "drawer" && !navOpen);
    scrim.hidden = !drawerOpen;
    document.body.dataset.navmode = m;
    scheduleFit();
  }
  function setDrawer(open) {
    navOpen = open; applyNav();
    if (open) { lastNavFocus = document.activeElement; (nav.querySelector("[aria-current=page]") || nav.querySelector("a"))?.focus(); }
    else if (lastNavFocus?.isConnected && document.activeElement && nav.contains(document.activeElement)) lastNavFocus.focus();
  }
  navToggle.addEventListener("click", () => setDrawer(!navOpen));
  scrim.addEventListener("click", () => setDrawer(false));
  collapseBtn.addEventListener("click", () => { prefs.set("shell.nav_collapsed", !prefs.get("shell.nav_collapsed", false)); applyNav(); });
  nav.addEventListener("click", (e) => { if (e.target.closest("a.nav-item") && navMode() === "drawer") setDrawer(false); });
  nav.addEventListener("keydown", (e) => { if (e.key === "Escape" && navMode() === "drawer" && navOpen) { e.stopPropagation(); setDrawer(false); navToggle.focus(); } });
  addEventListener("resize", debounce(applyNav, 50));

  function paintNav() {
    const caps = env.capabilities();
    for (const w of WORKSPACES) {
      const c = caps[w.id], n = navItems[w.id];
      n.cap.textContent = c.state; n.a.dataset.tone = tone(c.tone);
      n.capIc.replaceChildren(icon(c.tone === "ok" ? "check" : c.tone === "warn" ? "alert" : "dash", 12));
      n.a.title = `${w.title}: ${c.state}. ${c.detail}`;
      n.a.setAttribute("aria-label", `${w.title}, ${c.state}`);
    }
  }

  // ---------------------------------------------------------------- theme + token
  function applyTheme(t) {
    document.documentElement.dataset.theme = t; store.set("cc.theme", t);
    themeBtn.replaceChildren(icon(t === "dark" ? "sun" : "moon", 18));
    themeBtn.setAttribute("aria-label", t === "dark" ? "Switch to light theme" : "Switch to dark theme"); themeBtn.title = themeBtn.getAttribute("aria-label");
    scheduleFit();
  }
  themeBtn.addEventListener("click", () => { const t = document.documentElement.dataset.theme === "light" ? "dark" : "light"; applyTheme(t); prefs.set("ui.theme", t); });
  const t0 = prefs.get("ui.theme", null) || store.get("cc.theme", "dark"); applyTheme(t0 === "light" ? "light" : "dark");

  function paintToken() {
    const has = api.token.has(), st = prefs.status();
    tokenBtn.replaceChildren(...[icon(has ? "lock" : "unlock", 18), st.pending && !st.synced ? el("span", { class: "dot-warn", "aria-hidden": "true" }) : null].filter(Boolean));
    tokenBtn.setAttribute("aria-label", has ? `API token set${st.pending ? `; ${st.pending} preference change(s) not yet saved` : ""}` : "API token not set: changes cannot be saved to the server");
    tokenBtn.title = tokenBtn.getAttribute("aria-label");
  }
  tokenBtn.addEventListener("click", () => ui.popover(tokenBtn, (box) => {
    box.classList.add("token-pop");
    const input = el("input", { type: "password", class: "input", autocomplete: "off", spellcheck: "false", "aria-label": "X-CC-Token", placeholder: "X-CC-Token", value: "" });
    const msg = el("div", { class: "pop-note", "aria-live": "polite" });
    const status = () => { const st = prefs.status(); return api.token.has() ? (st.pending ? `Token entered (not verified). ${st.pending} preference change(s) waiting to save.` : "Token entered (not verified yet). Use Save token to check it with the server.") : "No token. Browsing works; saving preferences, audit entries and commands is refused."; };
    msg.textContent = status();
    box.append(el("div", { class: "pop-title" }, "API token"), el("p", { class: "pop-note" }, "Required for every write (preferences, audit, commands). Printed by the server at start-up; stored in this browser's localStorage only."), input,
      el("div", { class: "row" }, ui.btn("Save token", { kind: "primary", onClick: async () => { api.token.set(input.value); input.value = ""; let chk = null; try { chk = await api.get("/api/auth/check"); } catch (e) { chk = null; } if (chk && chk.token_valid) { const r = await prefs.flush(); msg.textContent = "Token accepted by the server. Changes can be saved." + (r.ok ? "" : ` (${r.error})`); } else { msg.textContent = chk ? "Token rejected by the server. Changes cannot be saved." : "Could not verify the token (server unreachable)."; } paintToken(); } }),
        ui.btn("Clear", { onClick: () => { api.token.clear(); msg.textContent = status(); paintToken(); } })), msg);
  }, { label: "API token", width: 320 }));
  api.token.onChange(() => { paintToken(); prefs.flush(); });
  prefs.onChange(() => paintToken());

  // ---------------------------------------------------------------- panels (popovers)
  const row = (k, v) => el("div", { class: "kv-row" }, el("dt", {}, k), el("dd", {}, v));
  function panel(title, build, anchor, width = 340) {
    ui.popover(anchor, (box, p) => { box.classList.add("info-pop"); box.append(el("div", { class: "pop-title" }, title)); build(box, p); }, { label: title, width });
  }
  function brokerPanel(a) {
    const b = env.broker;
    panel("Broker connection", (box, p) => box.append(
      ui.badge("warn", "Broker not connected", "plug_off"),
      el("p", { class: "pop-note" }, el("strong", {}, "Trading setup incomplete. "), `${b.reason}.`),
      el("dl", { class: "kv" }, row("Execution", "none — broker adapter not built"), row("Account data", "Read-only IBKR access is authorised but not wired into this app"), row("Risk engine", "not built")),
      ui.btn("Set up in Data & Connections", { kind: "primary", icon: "data", onClick: () => { p.close(false); ctx.nav("data"); } })), a);
  }
  function dataPanel(a) {
    const f = freshness.get("quotes"), md = env.connections?.market_data;
    panel("Market data", (box) => box.append(ui.freshBadge("quotes"), el("p", { class: "pop-note" }, f.detail || "No detail"),
      el("dl", { class: "kv" }, row("Source", md?.source || DASH), row("Interval", md ? "daily bars only" : DASH), row("Last bar", md?.last_bar ? String(md.last_bar).slice(0, 10) : DASH), row("Retrieved", md?.last_retrieved_on || DASH), row("Instruments", md ? String(md.instruments) : DASH))), a);
  }
  function agentsPanel(a) {
    const s = events.state();
    panel("Agent event stream", (box) => box.append(ui.freshBadge("agents"),
      el("p", { class: "pop-note" }, freshness.get("agents").detail),
      el("dl", { class: "kv" }, row("Connection", s.conn), row("Last event seq", String(s.lastSeq)), row("Last heartbeat", s.lastBeat ? fmt.time(s.lastBeat) : DASH), row("Stale after", `${s.staleAfterS} s without heartbeat`), row("Resume", "Last-Event-ID replay, deduplicated by seq"))), a);
  }
  function sessionPanel(a) {
    const s = nyseSession();
    panel("Exchange session", (box) => box.append(ui.badge(s.state === "open" ? "ok" : "neutral", s.label, s.state === "open" ? "check" : "clock"),
      el("p", { class: "pop-note" }, s.detail),
      el("dl", { class: "kv" }, row("Clock", `${fmt.time(new Date(), { tz: "America/New_York" })}`), row("Hours", "09:30–16:00 ET regular session")),
      el("p", { class: "pop-note warn-note" }, el("strong", {}, "Schedule unverified. "), s.note)), a);
  }

  // mode selector
  modeBtn.addEventListener("click", () => ui.popover(modeBtn, (box) => {
    box.classList.add("mode-pop");
    const msg = el("div", { class: "mode-msg", role: "status", "aria-live": "polite" });
    box.append(el("div", { class: "pop-title" }, "Display mode"), el("p", { class: "pop-note" }, "A display label for what you are looking at. It never changes execution; there is none."));
    const list = el("div", { class: "mode-list", role: "group", "aria-label": "Modes" });
    for (const m of MODES) {
      const cur = env.mode === m.id;
      const b = el("button", { type: "button", class: ["mode-item", cur ? "current" : "", m.selectable ? "" : "refused"], "aria-pressed": String(cur), "aria-disabled": m.selectable ? null : "true" },
        el("span", { class: "mode-item-name" }, m.id, cur ? el("span", { class: "sr-only" }, " (current)") : null), el("span", { class: "mode-item-desc" }, m.selectable ? m.desc : (m.id === "SHADOW" ? "Unavailable" : "Refused")), cur ? icon("check", 14) : (m.selectable ? null : icon("lock", 14)));
      b.addEventListener("click", async () => {
        const r = await env.setMode(m.id);
        if (r.ok) { paintMode(); box.querySelectorAll(".mode-item").forEach(x => { const on = x === b; x.classList.toggle("current", on); x.setAttribute("aria-pressed", String(on)); }); msg.className = "mode-msg ok"; msg.replaceChildren(icon("check", 14), el("span", {}, ` Display label set to ${m.id}. Execution unchanged: none.`, r.audit && !r.audit.ok ? el("span", { class: "mode-audit-warn" }, ` Audit entry NOT recorded: ${r.audit.error}`) : null)); }
        else { msg.className = "mode-msg bad"; msg.replaceChildren(icon("x_circle", 14), el("span", {}, " " + r.reason, r.audit && !r.audit.ok ? el("span", { class: "mode-audit-warn" }, ` (refusal not audited: ${r.audit.error})`) : null)); }
      });
      list.append(b);
    }
    box.append(list, msg, el("p", { class: "pop-foot" }, icon("shield", 14), " Execution: none — broker adapter not built"));
    const bk = env.broker.state !== "connected";
    if (bk) box.append(el("div", { class: "pop-setup" }, el("span", {}, "Trading setup incomplete — broker not connected."), ui.btn("Set up", { onClick: () => { ui.closePopover(); ctx.nav("data"); } })));
  }, { label: "Display mode", width: 360, placement: "bottom-end" }));
  function paintMode() {
    $("mode-value").textContent = env.mode;
    modeBtn.setAttribute("aria-label", `Display mode ${env.mode}. Changing it does not change execution; there is none.`);
    scheduleFit();
  }

  // ---------------------------------------------------------------- chips painting
  function paintChips() {
    const b = env.broker;
    brokerChip.set({ label: "Broker not connected", icon: "plug_off", tone: "warn", aria: `Broker not connected. ${b.reason}. Trading setup incomplete. Open details.`, title: "Trading setup incomplete" });
    const q = freshness.get("quotes"), qn = { realtime: "Realtime", delayed: "Delayed", historical: "Historical", stale: "Stale", unavailable: "Unavailable", unknown: "Unknown" }[q.state];
    dataChip.set({ label: `Data ${qn}`, sub: q.asOf ? (/^\d{4}/.test(q.asOf) && q.asOf.length === 10 ? q.asOf.slice(5) : fmt.time(q.asOf, { date: false, seconds: false })) : "", icon: q.state === "unavailable" ? "plug_off" : q.state === "stale" ? "alert" : "clock", tone: q.state === "historical" || q.state === "realtime" ? (q.state === "realtime" ? "ok" : "info") : q.state === "unknown" ? "neutral" : q.state === "unavailable" ? "bad" : "warn", aria: `Market data ${qn}${q.asOf ? ", as of " + q.asOf : ""}. ${q.detail || ""}`, title: q.detail });
    const e = events.state();
    const lab = { live: "Event stream live", reconnecting: "Event stream reconnecting", stale: "Event stream STALE", connecting: "Event stream connecting" }[e.conn];
    agentsChip.set({ label: lab, sub: e.conn === "stale" && e.lastBeat ? fmt.time(e.lastBeat, { date: false }) : "", icon: e.conn === "live" ? "radio" : e.conn === "stale" ? "alert" : "clock", tone: e.conn === "live" ? "ok" : e.conn === "connecting" ? "neutral" : "warn", aria: `Agent event stream ${e.conn}`, title: freshness.get("agents").detail });
    agentsChip.el.dataset.pri = e.conn === "live" || e.conn === "connecting" ? 5 : 9.5;  // a degraded stream stays visible in the bar
    demoChip.el.hidden = !env.hasDemo; scheduleFit();
    paintRisk();
  }
  function paintSession() {
    const s = nyseSession();
    sessionChip.set({ label: s.label, sub: "unverified", icon: s.state === "open" ? "building" : "clock", tone: s.state === "open" ? "ok" : "neutral", aria: `${s.label}. ${s.detail}. Schedule unverified.`, title: `${s.detail}. Schedule unverified.` });
  }
  function paintRisk() {
    const r = risk.get();
    if (!r.policy) riskChip.set({ label: "No risk policy", icon: "shield_off", tone: "warn", aria: "Risk: no risk policy configured. Open risk panel.", title: "No risk policy configured" });
    else {
      const breach = Object.values(r.flags).some(f => f?.level === "breach"), warn = Object.values(r.flags).some(f => f?.level === "warn") || ["stale", "unavailable", "unknown"].includes(freshness.get("quotes").state);
      riskChip.set({ label: breach ? "Risk: limit breached" : warn ? "Risk: attention" : "Risk: within limits", icon: breach ? "x_circle" : warn ? "alert" : "shield", tone: breach ? "bad" : warn ? "warn" : "ok", aria: "Risk summary. Open risk panel." });
    }
  }
  freshness.on(() => paintChips());
  env.on(() => { paintChips(); paintNav(); paintMode(); paintNotes(); });
  events.onState(() => { paintChips(); paintBanners(); });
  risk.on(() => paintRisk());
  prefs.onChange((k) => { if (k === "ui.mode") paintMode(); if (k === "shell.nav_collapsed" || k === "*") applyNav(); });

  // ---------------------------------------------------------------- banners (stale stream)
  const staleBanner = el("div", { class: "banner banner-stale", role: "alert", hidden: true });
  banners.append(staleBanner);
  function paintBanners() {
    const e = events.state(), stale = e.conn === "stale";
    staleBanner.hidden = !stale; outlet.classList.toggle("is-stale", stale); document.body.classList.toggle("stale", stale);
    if (stale) { staleBanner.replaceChildren(icon("alert", 16), el("strong", {}, "Event stream STALE"), ` — showing last known state as of ${fmt.time(e.lastBeat || Date.now(), { date: false })}. No heartbeat for over ${e.staleAfterS} s; reconnecting automatically.`); }
  }

  // ---------------------------------------------------------------- notifications
  const notes = new Map(); // key -> {key, kind, sev, text, ts, seq, source, approval_id, resolved}
  const seen = () => prefs.get("shell.notif_seen", 0);
  events.on("incident", (e) => { notes.set("i" + e.seq, { key: "i" + e.seq, kind: "incident", sev: e.payload.severity, text: e.payload.summary, ts: e.timestamp_utc, seq: e.seq, source: e.source, run: e.run_id }); paintNotes(); });
  events.on("approval.requested", (e) => { notes.set("a" + e.payload.approval_id, { key: "a" + e.payload.approval_id, kind: "approval", sev: "warning", text: e.payload.summary, ts: e.timestamp_utc, seq: e.seq, source: e.source, run: e.run_id, approval_id: e.payload.approval_id, resolved: false }); paintNotes(); });
  events.on("approval.resolved", (e) => { const n = notes.get("a" + e.payload.approval_id); if (n) { n.resolved = true; paintNotes(); } });
  const unread = () => [...notes.values()].filter(n => (n.kind === "approval" ? !n.resolved : n.seq > seen()));
  function paintNotes() {
    const n = unread().length;
    bellCount.hidden = n === 0; bellCount.textContent = n > 99 ? "99+" : String(n);
    bellBtn.setAttribute("aria-label", n ? `Notifications: ${n} unread incidents or pending approvals` : "Notifications: none unread");
  }
  bellBtn.addEventListener("click", () => ui.popover(bellBtn, (box) => {
    box.classList.add("notes-pop");
    const list = [...notes.values()].sort((a, b) => b.seq - a.seq);
    box.append(el("div", { class: "pop-title" }, "Notifications"));
    if (!list.length) box.append(ui.empty("Nothing to report", "No incidents or pending approvals in the recent event history (last ~500 events). Only real events from the stream appear here."));
    else {
      const ul = el("ul", { class: "notes-list" });
      for (const n of list.slice(0, 40)) {
        const isUnread = n.kind === "approval" ? !n.resolved : n.seq > seen();
        ul.append(el("li", { class: ["note", isUnread ? "unread" : ""] },
          n.kind === "approval" ? ui.badge(n.resolved ? "neutral" : "warn", n.resolved ? "Approval resolved" : "Approval pending", n.resolved ? "check" : "clock") : ui.badge(n.sev === "critical" ? "bad" : n.sev === "warning" ? "warn" : "info", `Incident · ${n.sev}`, n.sev === "critical" ? "x_circle" : n.sev === "warning" ? "alert" : "info"),
          el("p", { class: "note-text" }, n.text), el("div", { class: "note-meta" }, fmt.time(n.ts), " · run ", n.run, n.source === "demo" ? ui.badge("demo", "DEMO", "flask") : null),
          n.kind === "approval" && !n.resolved ? ui.btn("Review", { onClick: () => { ui.closePopover(); ctx.nav("agents", { approval: n.approval_id }); } }) : null));
      }
      box.append(ul);
    }
    const top = Math.max(0, ...[...notes.values()].filter(n => n.kind === "incident").map(n => n.seq)); if (top > seen()) { prefs.set("shell.notif_seen", top); paintNotes(); }
  }, { label: "Notifications", width: 380 }));

  // ---------------------------------------------------------------- risk panel
  function openRisk() {
    ui.drawer({ title: "Risk", label: "Risk panel", width: 520, content: (body, d) => {
      const render = () => {
        const r = risk.get(), q = freshness.get("quotes"), has = !!r.policy;
        const noPol = "No risk policy configured", noBroker = "No broker account data (broker not connected)";
        const lim = (key, label, f) => [label, has && r.policy.limits?.[key] != null ? f(r.policy.limits[key]) : fmt.missing(noPol)];
        const use = (key, label, f) => [label, r.usage?.[key] != null ? f(r.usage[key]) : fmt.missing(has ? "Not measured: no broker account data" : noBroker)];
        const money = (v) => fmt.money(v, { currency: r.policy?.currency || "USD" });
        const flag = (label, level, reason) => [label, level === "ok" ? ui.badge("ok", "OK", "check") : level === "warn" ? ui.badge("warn", "Attention", "alert") : level === "breach" ? ui.badge("bad", "Breach", "x_circle") : ui.badge("neutral", "Unknown", "dash"), reason];
        const f = r.flags || {};
        const staleLevel = ["stale", "unavailable", "unknown"].includes(q.state) ? "warn" : q.state === "historical" ? "warn" : null;
        const kvTable = (rows) => el("table", { class: "kv-table" }, el("tbody", {}, rows.map(([k, v, extra]) => el("tr", {}, el("th", { scope: "row" }, k), el("td", {}, v), extra !== undefined ? el("td", { class: "kv-note" }, extra) : null))));
        const sect = (title, ...kids) => el("section", { class: "risk-sect", "aria-labelledby": null }, el("h3", {}, title), ...kids);
        const ctl = (label, ic, scope, missing) => { const wid = uid("ctl-why"); return el("div", { class: "risk-ctl" }, el("button", { type: "button", class: "btn btn-danger-outline", disabled: true, "aria-describedby": wid }, icon(ic, 16), el("span", {}, label)),
          el("div", { id: wid }, el("p", { class: "ctl-why" }, el("strong", {}, "Scope: "), scope), el("p", { class: "ctl-why" }, el("strong", {}, "Unavailable because: "), missing))); };
        clear(body,
          el("div", { class: "risk-head" }, has ? ui.badge("ok", `Policy: ${r.policy.name || "configured"}`, "shield") : ui.badge("warn", "No risk policy configured", "shield_off"),
            el("p", {}, has ? "Limits below are those the policy declares." : "Until a policy exists nothing enforces any limit. This app also has no execution path, so there is nothing to limit yet. Unknown values show — with the reason on hover.")),
          sect("Limits", kvTable([lim("max_daily_loss", "Max daily loss", money), lim("max_position_value", "Max position value", money), lim("max_order_value", "Max order value", money), lim("max_concentration_pct", "Max concentration", (v) => fmt.pct(v, { sign: false })), lim("max_spread_bps", "Max spread", (v) => `${fmt.num(v, 1)} bps`)])),
          sect("Usage", kvTable([use("daily_loss", "Daily loss used", money), use("position_value", "Position value", money), use("buying_power", "Buying power", money), use("concentration_pct", "Largest position", (v) => fmt.pct(v, { sign: false }))])),
          sect("Flags", kvTable([
            flag("Stale market data", f.stale_data?.level ?? staleLevel, f.stale_data?.reason || (q.state === "historical" ? "Quotes are historical daily bars, not live prices" : q.detail || "No market-data state")),
            flag("Spread", f.spread?.level ?? null, f.spread?.reason || "No quote feed: spreads unknown"),
            flag("Buying power", f.buying_power?.level ?? null, f.buying_power?.reason || "No broker connection: buying power unknown"),
            flag("Concentration", f.concentration?.level ?? null, f.concentration?.reason || "No positions data"),
            flag("Reconciliation", f.reconciliation?.level ?? null, f.reconciliation?.reason || "No broker to reconcile against")])),
          sect("Controls",
            el("p", { class: "ctl-lead" }, "Disabled, not simulated. They record nothing and change nothing."),
            ctl("Pause new entries", "pause", "Stops the strategy runtime from submitting NEW entry orders for all strategies. Existing positions and protective orders are untouched.", "no strategy runtime and no broker adapter exist, so there is nothing to pause."),
            ctl("Cancel entry orders", "x_circle", "Cancels resting ENTRY orders at the broker. Protective stop and target orders are not cancelled.", "no broker adapter exists and there is no order state to cancel."),
            ctl("Flatten strategy positions", "stop", "Closes the open positions of ONE selected strategy at market. Will require a typed confirmation naming the strategy and the position list.", "no broker adapter exists and there are no positions.")),
          el("p", { class: "risk-asof" }, r.asOf ? `Risk state as of ${fmt.time(r.asOf)}` : "Risk state has never been computed."));
      };
      render(); const off = risk.on(render), off2 = freshness.on(render); const orig = d.close; d.close = () => { off(); off2(); orig(); };
    } });
  }
  document.addEventListener("click", (e) => { const t = e.target.closest?.("[data-open-risk]"); if (t) openRisk(); });

  // ---------------------------------------------------------------- symbol search
  let searchSeq = 0, activeIdx = -1, results = [];
  const closeSearch = () => { searchList.hidden = true; searchIn.setAttribute("aria-expanded", "false"); searchIn.removeAttribute("aria-activedescendant"); activeIdx = -1; };
  function paintResults(msg) {
    searchList.replaceChildren();
    if (msg) searchList.append(el("li", { class: "search-msg", role: "presentation" }, msg));
    results.forEach((r, i) => {
      const li = el("li", { class: "search-item", role: "option", id: `${searchId}-o${i}`, "aria-selected": "false" },
        el("strong", { class: "sym" }, r.symbol), el("span", { class: "sym-name" }, r.name || "(name not in data manifest)"), el("span", { class: "sym-meta" }, `${r.exchange || DASH} · conId ${r.con_id ?? DASH}`));
      li.addEventListener("mousedown", (e) => { e.preventDefault(); choose(r); }); searchList.append(li);
    });
    searchList.hidden = false; searchIn.setAttribute("aria-expanded", "true");
  }
  function choose(r) { closeSearch(); searchIn.value = ""; searchIn.blur(); ctx.nav("stocks", { symbol: r.symbol, conid: r.con_id, ex: r.exchange }); }
  async function runSearch() {
    const mine = ++searchSeq, q = searchIn.value.trim();
    try {
      const r = await api.get("/api/instruments?q=" + encodeURIComponent(q) + "&limit=12");
      if (mine !== searchSeq) return;
      results = r.instruments || []; activeIdx = results.length ? 0 : -1;
      paintResults(!r.available ? `Instrument list unavailable: ${r.reason}` : results.length ? `Instruments with local daily bars (${r.total}). Not a full symbol directory.` : `No local instrument matches “${q}”. Only symbols with downloaded daily bars are searchable.`);
      setActive();
    } catch (e) {
      if (mine !== searchSeq) return; results = [];
      paintResults(e.code === "not_found" ? "Instrument search unavailable: /api/instruments is not provided by this server." : `Instrument search failed: ${e.message}`);
    }
  }
  function setActive() { searchList.querySelectorAll(".search-item").forEach((li, i) => { li.setAttribute("aria-selected", String(i === activeIdx)); if (i === activeIdx) searchIn.setAttribute("aria-activedescendant", li.id); }); }
  searchIn.addEventListener("input", debounce(runSearch, 150));
  searchIn.addEventListener("focus", () => runSearch());
  searchIn.addEventListener("blur", () => setTimeout(closeSearch, 120));
  searchIn.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); if (searchList.hidden) runSearch(); else { activeIdx = Math.min(results.length - 1, activeIdx + 1); setActive(); } }
    else if (e.key === "ArrowUp") { e.preventDefault(); activeIdx = Math.max(0, activeIdx - 1); setActive(); }
    else if (e.key === "Enter") { if (results[activeIdx]) { e.preventDefault(); choose(results[activeIdx]); } }
    else if (e.key === "Escape") { if (!searchList.hidden) { e.stopPropagation(); closeSearch(); } else searchIn.blur(); }
  });

  // ---------------------------------------------------------------- keyboard
  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || e.target.isContentEditable;
    if (e.altKey && !e.ctrlKey && !e.metaKey && /^Digit[1-8]$/.test(e.code)) { e.preventDefault(); ctx.nav(WORKSPACES[+e.code.slice(5) - 1].id); outlet.focus({ preventScroll: true }); return; }
    if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === "/") { e.preventDefault(); if (ovfPanel.contains(searchBox)) ovfBtn.click(); searchIn.focus(); searchIn.select(); }
    else if (e.key === "[" && navMode() !== "drawer") { prefs.set("shell.nav_collapsed", !prefs.get("shell.nav_collapsed", false)); applyNav(); }
  });

  document.querySelector(".skip")?.addEventListener("click", (e) => { e.preventDefault(); outlet.focus(); }); // keep the hash route intact
  // ---------------------------------------------------------------- route hooks
  function onRoute(id) {
    const w = byId(id);
    titleEl.textContent = w.title; document.title = `${w.title} · tradelab`;
    for (const [k, n] of Object.entries(navItems)) { if (k === id) n.a.setAttribute("aria-current", "page"); else n.a.removeAttribute("aria-current"); }
    ui.live(`${w.title} workspace`);
    if (navMode() === "drawer") setDrawer(false);
    outlet.scrollTop = 0;
  }

  // ---------------------------------------------------------------- init
  applyNav(); paintNav(); paintMode(); paintChips(); paintSession(); paintToken(); paintNotes(); paintBanners();
  setInterval(paintSession, 15000);
  scheduleFit();
  return { outlet, onRoute, openRisk, applyNav, fit, paintAll() { paintNav(); paintChips(); paintMode(); } };
}
