// Safe DOM helpers. External text is ONLY ever inserted as text nodes / textContent / attributes. No innerHTML anywhere in core.
const SVG_NS = "http://www.w3.org/2000/svg";

const URL_ATTRS = new Set(["href", "src", "action", "formaction", "xlink:href"]);

function setAttr(node, k, v) {
  if (v === false || v == null) return;
  if (/^on/i.test(k)) return; // inline handlers are never allowed; use `on: {click: fn}`
  if (URL_ATTRS.has(k) && typeof v === "string" && /^\s*(javascript|data|vbscript):/i.test(v)) return;
  node.setAttribute(k, v === true ? "" : String(v));
}

/** el(tag, attrs?, ...children). attrs: class, text, dataset:{}, style:{}, on:{evt:fn}, aria-xxx and role as plain attributes.
 *  children: strings/numbers become TEXT nodes (never parsed as HTML); Nodes appended; arrays flattened; null/false skipped. */
export function el(tag, attrs, ...children) {
  const svg = tag.startsWith("svg:");
  const node = svg ? document.createElementNS(SVG_NS, tag.slice(4)) : document.createElement(tag);
  if (attrs && (typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs))) { children.unshift(attrs); attrs = null; }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") { if (v) node.setAttribute("class", Array.isArray(v) ? v.filter(Boolean).join(" ") : v); }
    else if (k === "text") node.textContent = v == null ? "" : String(v);
    else if (k === "dataset") for (const [dk, dv] of Object.entries(v || {})) { if (dv != null) node.dataset[dk] = String(dv); }
    else if (k === "style") { if (typeof v === "string") node.setAttribute("style", v); else for (const [sk, sv] of Object.entries(v || {})) node.style.setProperty(sk.startsWith("--") ? sk : sk.replace(/[A-Z]/g, c => "-" + c.toLowerCase()), sv); }
    else if (k === "on") for (const [ek, fn] of Object.entries(v || {})) node.addEventListener(ek, fn);
    else if (k === "html" || k === "innerHTML" || k === "outerHTML") throw new Error("el(): html attributes are not allowed; use text/children");
    else if (k === "value" && !svg) node.value = v;
    else if (k === "checked" || k === "disabled" || k === "hidden") { if (v) node.setAttribute(k, ""); }
    else setAttr(node, k, v);
  }
  append(node, children);
  return node;
}

export function append(node, children) {
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

/** text(node, str): replace content with a single text node. */
export function text(node, str) { node.textContent = str == null ? "" : String(str); return node; }

/** clear(node, ...children): remove all children, optionally append new ones. */
export function clear(node, ...children) { node.replaceChildren(); return append(node, children); }

export function svgEl(tag, attrs, ...children) { return el("svg:" + tag, attrs, ...children); }

let _uid = 0;
export const uid = (p = "cc") => `${p}-${++_uid}`;

export const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"]), [contenteditable=true]';

export function focusables(root) {
  return [...root.querySelectorAll(FOCUSABLE)].filter(n => !n.hidden && n.getClientRects().length && getComputedStyle(n).visibility !== "hidden");
}

/** Trap Tab inside `root`; returns release(). */
export function trapFocus(root) {
  const onKey = (e) => {
    if (e.key !== "Tab") return;
    const f = focusables(root);
    if (!f.length) { e.preventDefault(); root.focus(); return; }
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && (document.activeElement === first || document.activeElement === root)) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    else if (!root.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
  };
  document.addEventListener("keydown", onKey, true);
  return () => document.removeEventListener("keydown", onKey, true);
}

export function debounce(fn, ms) { let t; const d = (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; d.cancel = () => clearTimeout(t); d.flush = (...a) => { clearTimeout(t); fn(...a); }; return d; }

export function safeJSON(s, dflt = null) { try { return JSON.parse(s); } catch { return dflt; } }

/** localStorage wrappers: every read/write in try/catch (private windows, blocked storage). */
export const store = {
  get(k, d = null) { try { const v = localStorage.getItem(k); return v == null ? d : v; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); return true; } catch { return false; } },
  del(k) { try { localStorage.removeItem(k); } catch { /* ignore */ } },
};
