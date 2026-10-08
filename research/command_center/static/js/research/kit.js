// RESEARCH worker helpers. Only el()/text nodes for external text; tokens.css variables for colour; no innerHTML anywhere.
import { el } from "../core/dom.js";
import { icon } from "../core/icons.js";

const loaded = new Map();
export function loadCss(href) {
  if (document.querySelector(`link[data-rs="${href}"]`)) return;
  const l = document.createElement("link"); l.rel = "stylesheet"; l.href = href; l.dataset.rs = href; document.head.append(l);
}
export function loadScript(src) {
  if (loaded.has(src)) return loaded.get(src);
  const p = new Promise((res, rej) => { const s = document.createElement("script"); s.src = src; s.onload = () => res(); s.onerror = () => rej(new Error("could not load " + src)); document.head.append(s); });
  loaded.set(src, p); return p;
}
export const useCss = () => loadCss("/static/css/research.css");

/** Human message for an ApiError: prefers the server's own error text (verbatim). */
export function errText(e) {
  const b = e && e.body;
  if (b && typeof b === "object" && b.error) return String(b.error);
  return (e && e.message) || String(e);
}

export function field(label, control, hint, id) {
  const lab = el("label", { class: "field" }, el("span", {}, label), control);
  if (hint) lab.append(el("span", { class: "hint" }, hint));
  return lab;
}
export function textInput(o = {}) { return el("input", { class: "input", type: o.type || "text", value: o.value ?? "", placeholder: o.placeholder, min: o.min, max: o.max, step: o.step, readonly: o.readonly, "aria-label": o.label, name: o.name, inputmode: o.inputmode, on: o.on }); }
export function selectEl(options, value, o = {}) {
  const s = el("select", { class: "input", "aria-label": o.label, name: o.name, on: o.on },
    options.map((x) => { const op = el("option", { value: x.value }, x.label); if (x.disabled) op.disabled = true; if (x.value === value) op.selected = true; return op; }));
  return s;
}

/** Label with a definition tooltip (hover/focus). */
export function defLabel(ctx, label, def) {
  const b = el("span", { class: "def", tabindex: "0", role: "note", "aria-label": `${label}: ${def || "no definition available"}` }, label, icon("info", 12));
  if (def) ctx.ui.tooltip(b, def);
  return b;
}
export function kv(rows) {
  return el("dl", { class: "kv" }, rows.filter(Boolean).map(([k, v]) => el("div", { class: "kv-row" }, el("dt", {}, k), el("dd", {}, v ?? "—"))));
}
export function card(title, ...kids) { return el("section", { class: "rs-card" }, title ? el("h3", {}, title) : null, ...kids); }
export function note(kind, ...kids) {
  const ic = { warn: "alert", info: "info", bad: "x_circle", lock: "lock" }[kind] || "info";
  return el("div", { class: ["rs-note", `rs-note-${kind}`], role: kind === "bad" ? "alert" : "note" }, icon(ic, 16), el("div", {}, ...kids));
}
export function lockedControl(label, valueText, reason) {
  return el("div", { class: "field locked" }, el("span", {}, label), el("div", { class: "locked-val" }, icon("lock", 14), el("span", {}, valueText)), el("span", { class: "hint" }, reason));
}

/** Download text as a file (CSV/JSON export). */
export function download(name, mime, body) {
  const a = el("a", { href: URL.createObjectURL(new Blob([body], { type: mime })), download: name });
  document.body.append(a); a.click(); a.remove();
}
export const STATE_BADGE = {
  candidate: ["info", "flask"], testing: ["warn", "clock"], rejected: ["bad", "x_circle"],
  approved_paper: ["neutral", "lock"], approved_live: ["neutral", "lock"],
};
export function stateBadge(ctx, s) { const [k, i] = STATE_BADGE[s] || ["neutral", "dash"]; return ctx.ui.badge(k, s.replace("_", " "), i); }
export function verdictBadge(ctx, v) {
  if (!v) return ctx.ui.badge("neutral", "no verdict yet", "dash");
  const k = v === "REJECTED" ? "bad" : v === "INCONCLUSIVE" ? "warn" : "info";
  return ctx.ui.badge(k, v, v === "REJECTED" ? "x_circle" : v === "INCONCLUSIVE" ? "alert" : "info");
}
export function statusBadge(ctx, s) {
  const m = { queued: ["neutral", "clock"], running: ["info", "gauge"], completed: ["ok", "check"], failed: ["bad", "x_circle"] }[s] || ["neutral", "dash"];
  return ctx.ui.badge(m[0], s, m[1]);
}
/** Poller that stops on unmount. */
export function poller(fn, ms) {
  let t = null, dead = false;
  const tick = async () => { if (dead) return; let next = ms; try { const r = await fn(); if (typeof r === "number") next = r; } catch { /* keep polling */ } if (!dead) t = setTimeout(tick, next); };
  t = setTimeout(tick, ms);
  return { stop() { dead = true; clearTimeout(t); }, now() { clearTimeout(t); return tick(); } };
}

/** Remove tooltip bubbles left behind when their target element was re-rendered away. */
export function clearTips() { document.querySelectorAll(".tooltip").forEach((n) => n.remove()); }
