// Inline SVG icons (24x24 grid, stroke-based, currentColor). Built with createElementNS from constant data: no HTML parsing, no emoji.
import { svgEl } from "./dom.js";

const P = (d) => ["path", { d }];
const C = (cx, cy, r) => ["circle", { cx, cy, r }];
const R = (x, y, w, h, rx = 2) => ["rect", { x, y, width: w, height: h, rx }];
const L = (x1, y1, x2, y2) => ["line", { x1, y1, x2, y2 }];

export const ICONS = {
  // workspaces
  command_center: [R(3, 3, 7, 9), R(14, 3, 7, 5), R(14, 12, 7, 9), R(3, 16, 7, 5)],
  stocks: [P("M3 3v18h18"), P("M7 15l4-5 3 3 5-7")],
  portfolio: [R(3, 7, 18, 13), P("M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"), P("M3 13h18")],
  agents: [C(12, 8, 3), P("M5 20v-1a4 4 0 0 1 4-4h6a4 4 0 0 1 4 4v1"), C(5, 9, 2), C(19, 9, 2)],
  research: [P("M9 3h6"), P("M10 3v6L4.5 19a1.5 1.5 0 0 0 1.3 2.2h12.4a1.5 1.5 0 0 0 1.3-2.2L14 9V3"), P("M7.5 15h9")],
  backtest: [P("M3 12a9 9 0 1 0 3-6.7"), P("M3 4v5h5"), P("M12 7v5l3 2")],
  data: [["ellipse", { cx: 12, cy: 5, rx: 8, ry: 3 }], P("M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5"), P("M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6")],
  settings: [L(4, 7, 20, 7), L(4, 12, 20, 12), L(4, 17, 20, 17), C(9, 7, 2), C(15, 12, 2), C(8, 17, 2)],
  // chrome
  menu: [L(4, 6, 20, 6), L(4, 12, 20, 12), L(4, 18, 20, 18)],
  panel_left: [R(3, 4, 18, 16), L(9, 4, 9, 20)],
  chevron_left: [P("M15 6l-6 6 6 6")],
  chevron_right: [P("M9 6l6 6-6 6")],
  chevron_down: [P("M6 9l6 6 6-6")],
  chevron_up: [P("M6 15l6-6 6 6")],
  search: [C(11, 11, 6), L(20, 20, 15.5, 15.5)],
  bell: [P("M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"), P("M10 21h4")],
  lock: [R(5, 11, 14, 10), P("M8 11V8a4 4 0 0 1 8 0v3")],
  unlock: [R(5, 11, 14, 10), P("M8 11V8a4 4 0 0 1 7.5-2")],
  sun: [C(12, 12, 4), L(12, 2, 12, 5), L(12, 19, 12, 22), L(2, 12, 5, 12), L(19, 12, 22, 12), L(4.9, 4.9, 7, 7), L(17, 17, 19.1, 19.1), L(4.9, 19.1, 7, 17), L(17, 7, 19.1, 4.9)],
  moon: [P("M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z")],
  more: [C(5, 12, 1.2), C(12, 12, 1.2), C(19, 12, 1.2)],
  external: [P("M14 4h6v6"), L(20, 4, 11, 13), P("M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5")],
  download: [P("M12 4v11"), P("M7 11l5 5 5-5"), P("M4 20h16")],
  columns: [R(3, 4, 18, 16), L(9, 4, 9, 20), L(15, 4, 15, 20)],
  // status (shape differs per status so colour is never the only signal)
  check: [P("M4 12.5l5 5L20 6.5")],
  alert: [P("M12 3l10 18H2z"), L(12, 10, 12, 14), L(12, 17.5, 12, 17.6)],
  x: [L(6, 6, 18, 18), L(18, 6, 6, 18)],
  x_circle: [C(12, 12, 9), L(9, 9, 15, 15), L(15, 9, 9, 15)],
  info: [C(12, 12, 9), L(12, 11, 12, 16), L(12, 7.5, 12, 7.6)],
  clock: [C(12, 12, 9), P("M12 7v5l3 2")],
  dash: [L(6, 12, 18, 12)],
  pause: [L(9, 6, 9, 18), L(15, 6, 15, 18)],
  shield: [P("M12 3l8 3v6c0 4.5-3.2 8-8 9-4.8-1-8-4.5-8-9V6z")],
  shield_off: [P("M12 3l8 3v6c0 4.5-3.2 8-8 9-4.8-1-8-4.5-8-9V6z"), L(4, 4, 20, 20)],
  plug_off: [P("M9 3v4"), P("M15 3v4"), P("M7 7h10v4a5 5 0 0 1-10 0z"), P("M12 16v5"), L(3, 3, 21, 21)],
  plug: [P("M9 3v4"), P("M15 3v4"), P("M7 7h10v4a5 5 0 0 1-10 0z"), P("M12 16v5")],
  signal: [L(5, 19, 5, 15), L(10, 19, 10, 11), L(15, 19, 15, 7), L(20, 19, 20, 3)],
  radio: [C(12, 12, 2), P("M7.8 7.8a6 6 0 0 0 0 8.4"), P("M16.2 7.8a6 6 0 0 1 0 8.4")],
  building: [P("M3 21h18"), P("M5 21V9l7-5 7 5v12"), L(9, 21, 9, 13), L(15, 21, 15, 13)],
  flask: [P("M9 3h6"), P("M10 3v6L4.5 19a1.5 1.5 0 0 0 1.3 2.2h12.4a1.5 1.5 0 0 0 1.3-2.2L14 9V3")],
  gauge: [P("M4 17a8 8 0 1 1 16 0"), L(12, 17, 16, 11)],
  stop: [R(5, 5, 14, 14, 2)],
  flag: [P("M5 21V4"), P("M5 4h12l-2 4 2 4H5")],
  list: [L(8, 6, 20, 6), L(8, 12, 20, 12), L(8, 18, 20, 18), L(4, 6, 4.1, 6), L(4, 12, 4.1, 12), L(4, 18, 4.1, 18)],
};

/** icon(name, size=16): returns a new <svg aria-hidden> element. Unknown names render a neutral dash. */
export function icon(name, size = 16) {
  const parts = ICONS[name] || ICONS.dash;
  const s = svgEl("svg", { viewBox: "0 0 24 24", width: size, height: size, fill: "none", stroke: "currentColor", "stroke-width": 1.8,
    "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", focusable: "false", class: "icon" });
  for (const [tag, attrs] of parts) s.append(svgEl(tag, attrs));
  return s;
}
