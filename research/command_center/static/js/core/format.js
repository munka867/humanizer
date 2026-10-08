// Formatting helpers. Plain functions return STRINGS ("—" when missing). Use fmt.cell()/fmt.missing() for DOM with a tooltip reason.
import { el } from "./dom.js";

export const DASH = "—";
const MINUS = "−";
const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const toNum = (v) => (v === null || v === undefined || v === "" || typeof v === "boolean" ? NaN : Number(v));

const nfCache = new Map();
function nf(opts) {
  const k = JSON.stringify(opts);
  let f = nfCache.get(k);
  if (!f) { f = new Intl.NumberFormat("en-US", opts); nfCache.set(k, f); }
  return f;
}
const signed = (s, v, show) => (v < 0 ? MINUS + s.replace("-", "") : show && v > 0 ? "+" + s : s);

let TZ = "America/New_York";
export const TZ_LABELS = { "America/New_York": "ET", UTC: "UTC" };

export const fmt = {
  DASH,
  /** Set/get the display timezone ('America/New_York' | 'UTC'). Default America/New_York. */
  tz(v) { if (v === "UTC" || v === "America/New_York") TZ = v; return TZ; },

  /** num(v, dp=2): grouped decimal. */
  num(v, dp = 2) { if (dp && typeof dp === 'object') dp = Number.isInteger(dp.dp) ? dp.dp : 2; v = toNum(v); return isNum(v) ? signed(nf({ minimumFractionDigits: dp, maximumFractionDigits: dp }).format(v), v, false) : DASH; },
  /** money(v, {currency='USD', dp=2, sign=false, compact=false}): "$1,234.50", "−$3.20"; currency is ALWAYS shown (symbol or code). */
  money(v, { currency = "USD", dp = 2, sign = false, compact = false } = {}) {
    v = toNum(v); if (!isNum(v)) return DASH;
    const o = { style: "currency", currency, currencyDisplay: currency === "USD" ? "narrowSymbol" : "code" };
    if (compact) { o.notation = "compact"; o.maximumFractionDigits = 1; } else { o.minimumFractionDigits = dp; o.maximumFractionDigits = dp; }
    const s = nf(o).format(Math.abs(v));
    return (v < 0 ? MINUS : sign && v > 0 ? "+" : "") + s;
  },
  /** price(v, {dp}): quote price; 2dp (4dp when |v|<1) unless dp given. No currency symbol (column header carries it). */
  price(v, { dp } = {}) { v = toNum(v); if (!isNum(v)) return DASH; const d = dp ?? (Math.abs(v) < 1 && v !== 0 ? 4 : 2); return fmt.num(v, d); },
  /** qty(v, {dp=0, maxDp=4}): share quantity; integers show no decimals, fractional shares up to maxDp. */
  qty(v, { dp = 0, maxDp = 4 } = {}) { v = toNum(v); if (!isNum(v)) return DASH; return signed(nf({ minimumFractionDigits: dp, maximumFractionDigits: Math.max(dp, maxDp) }).format(v), v, false); },
  /** pct(v, {dp=2, sign=true, ratio=false}): v is in PERCENT POINTS (1.5 -> "+1.50%") unless ratio:true (0.015 -> "+1.50%"). */
  pct(v, { dp = 2, sign = true, ratio = false } = {}) {
    v = toNum(v); if (!isNum(v)) return DASH; if (ratio) v *= 100;
    return signed(nf({ minimumFractionDigits: dp, maximumFractionDigits: dp }).format(v), v, sign) + "%";
  },
  compact(v, dp = 1) { v = toNum(v); return isNum(v) ? nf({ notation: "compact", maximumFractionDigits: dp }).format(v) : DASH; },
  /** dir(v): 'pos' | 'neg' | '' (class names from tokens.css). Always pair with a signed string; colour is never the only cue. */
  dir(v) { v = toNum(v); return isNum(v) ? (v > 0 ? "pos" : v < 0 ? "neg" : "") : ""; },

  /** time(v, {tz, date=true, seconds=true, label=true}): v = Date | ISO string | epoch ms. Always carries a tz label (EDT/EST/UTC). */
  time(v, { tz = TZ, date = true, seconds = true, label = true } = {}) {
    const d = v instanceof Date ? v : new Date(v);
    if (v == null || v === "" || Number.isNaN(d.getTime())) return DASH;
    const parts = new Intl.DateTimeFormat("en-CA", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit", timeZoneName: "short" }).formatToParts(d);
    const g = (t) => parts.find(p => p.type === t)?.value || "";
    const tzn = tz === "UTC" ? "UTC" : g("timeZoneName");
    const t = `${g("hour")}:${g("minute")}${seconds ? ":" + g("second") : ""}`;
    return `${date ? `${g("year")}-${g("month")}-${g("day")} ` : ""}${t}${label ? " " + tzn : ""}`;
  },
  /** dateOnly(v, {tz}): YYYY-MM-DD in tz (strings already YYYY-MM-DD, e.g. bar dates, are returned as is). */
  dateOnly(v, { tz = TZ } = {}) { if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v)) return v; const s = fmt.time(v, { tz, date: true, seconds: false, label: false }); return s === DASH ? DASH : s.slice(0, 10); },
  /** ago(v): "12s ago", "3m ago", "2h ago", "5d ago". */
  ago(v, now = Date.now()) {
    const t = v instanceof Date ? v.getTime() : new Date(v).getTime(); if (v == null || Number.isNaN(t)) return DASH;
    const s = Math.max(0, Math.round((now - t) / 1000));
    return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.floor(s / 60)}m ago` : s < 86400 ? `${Math.floor(s / 3600)}h ago` : `${Math.floor(s / 86400)}d ago`;
  },
  duration(s) { s = toNum(s); if (!isNum(s)) return DASH; return s < 60 ? `${Math.round(s)} s` : s < 3600 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`; },

  /** missing(reason): <span class="missing" title=reason>—</span> with an sr-only reason. Use wherever a value is unknown. */
  missing(reason = "No data available") {
    return el("span", { class: "missing", title: reason }, DASH, el("span", { class: "sr-only" }, ` (${reason})`));
  },
  /** cell(value, kind|fn, {reason, opts, colour}): kind in num|money|price|qty|pct|time|text. Returns <span class="cell num"> or missing(reason). */
  cell(value, kind = "text", { reason, opts, colour = false } = {}) {
    const miss = value === null || value === undefined || value === "" || (typeof value === "number" && !Number.isFinite(value));
    if (miss) return fmt.missing(reason);
    const f = typeof kind === "function" ? kind : fmt[kind] || String;
    const out = f(value, opts);
    if (out === DASH) return fmt.missing(reason);
    return el("span", { class: ["cell", kind !== "text" && kind !== "time" ? "num" : "", colour ? fmt.dir(value) : ""] }, out);
  },
  /** timeEl(v, opts): <time datetime=iso title="ISO · zone"> */
  timeEl(v, opts = {}) {
    const d = v instanceof Date ? v : new Date(v);
    if (v == null || Number.isNaN(d.getTime())) return fmt.missing("No timestamp");
    const tz = opts.tz || TZ;
    return el("time", { datetime: d.toISOString(), title: `${d.toISOString()} · shown in ${tz}`, class: "cell" }, fmt.time(d, opts));
  },
};

export default fmt;
