import { el } from "../core/dom.js";
import { renderMarkdown } from "./md.js";
import { errText } from "./kit.js";

/** Read-only viewer for /api/docs/<name>.md in a drawer. `section` (optional) scrolls to the first heading containing that text. */
export function openDoc(ctx, name, section) {
  const base = String(name).split("/").pop();
  if (!/^[A-Za-z0-9_.\-]+\.md$/.test(base)) return ctx.ui.toast("Invalid document name", { kind: "error" });
  const body = el("div", {}, ctx.ui.skeleton({ lines: 6 }));
  const d = ctx.ui.drawer({ title: base, width: 760, label: `Document ${base}`, content: body });
  (async () => {
    try {
      const txt = await fetchText(`/api/docs/${encodeURIComponent(base)}`);
      const md = renderMarkdown(txt, { onDoc: (n) => openDoc(ctx, n) });
      body.replaceChildren(el("p", { class: "muted" }, `Read-only view of research/docs/${base} (rendered as a safe markdown subset; no HTML is interpreted).`), md);
      if (section) {
        const want = section.toLowerCase().replace(/^\d+\.\s*/, "");
        const h = [...md.querySelectorAll(".md-h")].find((x) => x.textContent.toLowerCase().includes(want));
        if (h) { h.scrollIntoView({ block: "start" }); h.classList.add("md-hit"); h.prepend("→ "); }
        else md.prepend(el("p", { class: "muted" }, `Section "${section}" not found by heading text; showing the whole document.`));
      }
    } catch (e) { ctx.ui.inlineError(body, `Could not load ${base}: ${errText(e)}`); }
  })();
  return d;
}
async function fetchText(path) {
  const r = await fetch(path, { headers: { Accept: "text/plain" } });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.text();
}
