// Shared small helpers for the Agent Team workspace (DOM through ctx.el only; external text is always a text node).
import { STATUS_META, tms } from "./model.js";

/** Smart time label: time only when on the same (display-zone) date as `refMs`, otherwise date + time. Always carries the zone label. */
export function timeLabel(ctx, iso, refMs, { seconds = true } = {}) {
  const t = tms(iso); if (t == null) return ctx.fmt.DASH;
  const day = (ms) => ctx.fmt.time(ms, { date: true, seconds: false, label: false }).slice(0, 10);
  const same = refMs != null && day(t) === day(refMs);
  return ctx.fmt.time(t, { date: !same, seconds: same ? seconds : false, label: true });
}

export function statusBadge(ctx, key) {
  const m = STATUS_META[key] || STATUS_META.no_status;
  return ctx.ui.badge(m.kind, m.label, m.icon);
}

export function plural(n, one, many) { return `${n} ${n === 1 ? one : many || one + "s"}`; }

/** Section heading used in panels. */
export function heading(ctx, label, extra) {
  return ctx.el("div", { class: "ag-h" }, ctx.el("h3", {}, label), extra || null);
}

/** Outcome badge for test.result */
export function outcomeBadge(ctx, outcome) {
  const k = { pass: ["ok", "check"], fail: ["bad", "x_circle"], error: ["bad", "alert"], skip: ["neutral", "dash"] }[outcome] || ["neutral", "dash"];
  return ctx.ui.badge(k[0], String(outcome || "unknown").toUpperCase(), k[1]);
}

export function sevBadge(ctx, sev) {
  const k = { critical: ["bad", "alert"], warning: ["warn", "alert"], info: ["info", "info"] }[sev] || ["neutral", "info"];
  return ctx.ui.badge(k[0], sev || "unknown", k[1]);
}

/** Fetch a doc as TEXT (never HTML). Only same-origin /api/docs|results paths produced by model.docLinkFor/resultLinkFor are passed in. */
export async function fetchText(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText || "not found"}`);
  return r.text();
}
