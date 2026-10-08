// Read-only markdown SUBSET renderer. Builds DOM with el() only: no HTML is ever parsed, so document text cannot inject markup.
// Supports: # headings, paragraphs, - / 1. lists, | tables |, ``` fences, > quotes, **bold**, `code`, [text](http(s) or docs/X.md).
import { el } from "../core/dom.js";

function inline(s, onDoc) {
  const out = []; const re = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)\s]+\))/g; let last = 0, m;
  while ((m = re.exec(s))) {
    if (m.index > last) out.push(s.slice(last, m.index));
    const t = m[0];
    if (t.startsWith("**")) out.push(el("strong", {}, t.slice(2, -2)));
    else if (t.startsWith("`")) out.push(el("code", {}, t.slice(1, -1)));
    else {
      const mm = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(t); const label = mm[1], url = mm[2];
      const doc = /^(?:\.\/)?(?:docs\/)?([A-Za-z0-9_.\-]+\.md)(?:#.*)?$/.exec(url);
      if (/^https?:\/\//i.test(url)) out.push(el("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, label));
      else if (doc && onDoc) out.push(el("a", { href: "#", on: { click: (e) => { e.preventDefault(); onDoc(doc[1]); } } }, label));
      else out.push(label);
    }
    last = m.index + t.length;
  }
  if (last < s.length) out.push(s.slice(last));
  return out;
}
const cells = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());

export function renderMarkdown(src, { onDoc, maxLines = 4000 } = {}) {
  const root = el("div", { class: "md" }); const lines = String(src).split(/\r?\n/).slice(0, maxLines); let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (!l.trim()) { i++; continue; }
    if (/^```/.test(l)) { const buf = []; i++; while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]); i++; root.append(el("pre", { class: "md-pre" }, buf.join("\n"))); continue; }
    const h = /^(#{1,6})\s+(.*)$/.exec(l);
    if (h) { const lvl = Math.min(4, h[1].length + 1); root.append(el("h" + lvl, { class: "md-h", id: undefined }, inline(h[2], onDoc))); i++; continue; }
    if (/^\s*\|/.test(l) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      const head = cells(l); i += 2; const body = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) body.push(cells(lines[i++]));
      root.append(el("div", { class: "md-tbl" }, el("table", {}, el("thead", {}, el("tr", {}, head.map((c) => el("th", {}, inline(c, onDoc))))),
        el("tbody", {}, body.map((r) => el("tr", {}, r.map((c) => el("td", {}, inline(c, onDoc)))))))));
      continue;
    }
    if (/^\s*([-*]|\d+\.)\s+/.test(l)) {
      const ordered = /^\s*\d+\./.test(l); const items = [];
      while (i < lines.length && (/^\s*([-*]|\d+\.)\s+/.test(lines[i]) || (/^\s{2,}\S/.test(lines[i]) && items.length))) {
        if (/^\s*([-*]|\d+\.)\s+/.test(lines[i])) items.push(lines[i].replace(/^\s*([-*]|\d+\.)\s+/, "")); else items[items.length - 1] += " " + lines[i].trim();
        i++;
      }
      root.append(el(ordered ? "ol" : "ul", {}, items.map((t) => el("li", {}, inline(t, onDoc))))); continue;
    }
    if (/^>\s?/.test(l)) { const buf = []; while (i < lines.length && /^>\s?/.test(lines[i])) buf.push(lines[i++].replace(/^>\s?/, "")); root.append(el("blockquote", {}, inline(buf.join(" "), onDoc))); continue; }
    const buf = [l.trim()]; i++;
    while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|```|\s*\||\s*([-*]|\d+\.)\s|>)/.test(lines[i])) buf.push(lines[i++].trim());
    root.append(el("p", {}, inline(buf.join(" "), onDoc)));
  }
  if (lines.length === maxLines) root.append(el("p", { class: "muted" }, "(document truncated in the viewer)"));
  return root;
}
