// Pure geometry + connector routing for the tower graph (no DOM). Everything is STATIC: positions, ports, lane numbers and routes
// depend only on the density ("full" | "compact"), never on events, so connectors never re-layout when an event arrives.
// Edge kinds: hierarchy (lead -> specialists), message (agent pair), dependency (agent pair). Each kind has its own lane band and port slots,
// so two kinds never share a line segment. Routes are orthogonal (horizontal/vertical segments only).

import { AGENT_IDS, SPECIALISTS, pairKey } from "./model.js";

export const LANE = 9;
export const BOT_RESERVE = 4;
export const CROWN_H = 22;

function overlaps(a, b, margin = 6) { return a[0] - margin < b[1] && b[0] - margin < a[1]; }

/** Greedy interval colouring: edges are processed in the given order and take the first lane whose intervals do not overlap. */
export function assignLanes(items) {
  const lanes = [];
  const out = new Map();
  for (const it of items) {
    let k = 0;
    for (; k < lanes.length; k++) if (!lanes[k].some((r) => overlaps(r, it.range))) break;
    if (k === lanes.length) lanes.push([]);
    lanes[k].push(it.range); out.set(it.key, k);
  }
  return { lane: out, count: lanes.length };
}

const dedupe = (pts) => {
  const o = [];
  for (const p of pts) { const l = o[o.length - 1]; if (!l || l[0] !== p[0] || l[1] !== p[1]) o.push(p); }
  // drop collinear middle points
  const r = [];
  for (const p of o) {
    while (r.length >= 2) {
      const a = r[r.length - 2], b = r[r.length - 1];
      if ((a[0] === b[0] && b[0] === p[0]) || (a[1] === b[1] && b[1] === p[1])) r.pop(); else break;
    }
    r.push(p);
  }
  return r;
};

/** Points -> SVG path with rounded corners (radius r). */
export function roundedPath(pts, r = 7) {
  if (pts.length < 2) return "";
  let d = `M${pts[0][0]} ${pts[0][1]}`;
  for (let i = 1; i < pts.length - 1; i++) {
    const p0 = pts[i - 1], p1 = pts[i], p2 = pts[i + 1];
    const l1 = Math.hypot(p1[0] - p0[0], p1[1] - p0[1]), l2 = Math.hypot(p2[0] - p1[0], p2[1] - p1[1]);
    const rr = Math.min(r, l1 / 2, l2 / 2);
    const a = [p1[0] - ((p1[0] - p0[0]) / l1) * rr, p1[1] - ((p1[1] - p0[1]) / l1) * rr];
    const b = [p1[0] + ((p2[0] - p1[0]) / l2) * rr, p1[1] + ((p2[1] - p1[1]) / l2) * rr];
    d += ` L${a[0]} ${a[1]} Q${p1[0]} ${p1[1]} ${b[0]} ${b[1]}`;
  }
  const e = pts[pts.length - 1];
  return d + ` L${e[0]} ${e[1]}`;
}

/** makeLayout(density) -> {W,H,..., size, nodes, user, routes:{hier,msg,dep}, bands}.
 *  nodes[id] = {x,y,w,h,cx,cy}; routes.msg/dep are keyed by pairKey(a,b), each {a, b, pts, d} where the route is drawn from a to b. */
export function makeLayout(density = "full", paneW = 0) {
  const full = density === "full";
  const cw = paneW ? Math.max(126, Math.min(140, Math.floor((paneW - 24 - 40 - 50) / 6))) : 140;   // compact towers shrink a little so the row fits a narrow pane
  const W = full ? 184 : cw, H = full ? 268 : 212, G = full ? 14 : 10;
  const LW = full ? 400 : 340, LH = full ? 140 : 128;
  const M = 20;
  const RW = SPECIALISTS.length * W + (SPECIALISTS.length - 1) * G;
  const leadCx = M + RW / 2;
  const cxOf = (i) => M + i * (W + G) + W / 2;

  // ---- top channel: lead <-> specialists (kind message "m" and dependency "d"; hierarchy trunk at the very top)
  const lead = { x: leadCx - LW / 2, w: LW, h: LH, y: M };
  const leadBottom = lead.y + LH;
  const trunkY = leadBottom + 16;
  const leadPort = (kind, i) => {  // message ports on the left half of the lead roof, dependency ports on the right half
    const t = i / (SPECIALISTS.length - 1);
    return Math.round(kind === "m" ? leadCx - LW * 0.42 + t * LW * 0.34 : leadCx + LW * 0.08 + t * LW * 0.34);
  };
  const specTopPort = (kind, i) => Math.round(cxOf(i) + (kind === "m" ? W * 0.27 : -W * 0.27));
  const topItems = (kind) => SPECIALISTS.map((id, i) => {
    const xa = leadPort(kind, i), xb = specTopPort(kind, i);
    return { key: id, i, xa, xb, range: [Math.min(xa, xb), Math.max(xa, xb)], len: Math.abs(xa - xb) };
  }).sort((p, q) => q.len - p.len || p.i - q.i);                        // longest span = shallowest lane (nested, fewest crossings)
  const topM = assignLanes(topItems("m")), topD = assignLanes(topItems("d"));
  const topMStart = trunkY + 16;
  const topDStart = topMStart + topM.count * LANE + 8;
  const rowTop = topDStart + topD.count * LANE + 20;
  const rowBottom = rowTop + H;

  // ---- bottom channel: specialist <-> specialist. Lanes are STICKY (first-fit, assigned when a connector first appears and never moved
  //      afterwards) so existing lines never jump when a new one is added; BOT_RESERVE lanes are reserved so the towers never move either.
  const idx = Object.fromEntries(SPECIALISTS.map((id, i) => [id, i]));
  const botPort = (kind, i, j) => {   // port of tower i for partner j (partners ordered left to right), left half = message, right half = dependency
    const partners = SPECIALISTS.map((_, k) => k).filter((k) => k !== i);
    const k = partners.indexOf(j), t = k / (partners.length - 1);
    return Math.round(cxOf(i) + (kind === "m" ? -W * 0.42 + t * W * 0.34 : W * 0.08 + t * W * 0.34));
  };
  const botStart = rowBottom + 16;
  const height = botStart + BOT_RESERVE * LANE + 14;
  const botRange = (kind, a, b) => { const i = Math.min(idx[a], idx[b]), j = Math.max(idx[a], idx[b]); const xa = botPort(kind, i, j), xb = botPort(kind, j, i); return [Math.min(xa, xb), Math.max(xa, xb)]; };
  const botRoute = (kind, a, b, lane) => {
    const i = Math.min(idx[a], idx[b]), j = Math.max(idx[a], idx[b]);
    const y = botStart + lane * LANE, xa = botPort(kind, i, j), xb = botPort(kind, j, i);
    const pts = dedupe([[xa, rowBottom], [xa, y], [xb, y], [xb, rowBottom]]);
    return { a: SPECIALISTS[i], b: SPECIALISTS[j], pts, d: roundedPath(pts, 7), lane };
  };
  const pairs = [];
  for (let i = 0; i < SPECIALISTS.length; i++) for (let j = i + 1; j < SPECIALISTS.length; j++) pairs.push([i, j]);

  const nodes = { lead: { x: lead.x, y: lead.y, w: LW, h: LH, cx: leadCx, cy: lead.y + LH / 2 } };
  SPECIALISTS.forEach((id, i) => { nodes[id] = { x: M + i * (W + G), y: rowTop, w: W, h: H, cx: cxOf(i), cy: rowTop + H / 2 }; });
  const user = { x: M, y: lead.y + Math.round(LH / 2) - 20, w: full ? 92 : 78, h: 40 };

  // ---- routes
  const hier = {};
  SPECIALISTS.forEach((id, i) => { hier[id] = { id, pts: dedupe([[leadCx, leadBottom], [leadCx, trunkY], [cxOf(i), trunkY], [cxOf(i), rowTop - CROWN_H]]) }; });
  const msg = {}, dep = {};
  for (const id of SPECIALISTS) {
    const i = idx[id];
    for (const [kind, store, start, lanes] of [["m", msg, topMStart, topM], ["d", dep, topDStart, topD]]) {
      const y = start + lanes.lane.get(id) * LANE;
      store[pairKey("lead", id)] = { a: "lead", b: id, pts: dedupe([[leadPort(kind, i), leadBottom], [leadPort(kind, i), y], [specTopPort(kind, i), y], [specTopPort(kind, i), rowTop]]) };
    }
  }
  // user <-> lead messages: straight horizontal line between the user pill and the lead tower
  msg[pairKey("lead", "user")] = { a: "user", b: "lead", pts: [[user.x + user.w, user.y + user.h / 2], [lead.x, user.y + user.h / 2]] };
  for (const s of [msg, dep, hier]) for (const r of Object.values(s)) if (!r.d) r.d = roundedPath(r.pts, 7);

  return { density, W, H, G, M, size: { w: M * 2 + RW, h: height }, nodes, user, trunkY, rowTop, rowBottom, routes: { hier, msg, dep },
    bands: { top: { m: topM.count, d: topD.count }, bottomReserve: BOT_RESERVE }, botRange, botRoute, pairs: pairs.map(([i, j]) => [SPECIALISTS[i], SPECIALISTS[j]]) };
}

/** Do two orthogonal polylines share a collinear segment (overlap longer than eps)? Used by tests for "minimal overlaps". */
export function sharedSegmentLength(p, q) {
  let total = 0;
  for (let i = 0; i < p.length - 1; i++) for (let j = 0; j < q.length - 1; j++) {
    const [a1, a2, b1, b2] = [p[i], p[i + 1], q[j], q[j + 1]];
    if (a1[0] === a2[0] && b1[0] === b2[0] && a1[0] === b1[0]) total += Math.max(0, Math.min(Math.max(a1[1], a2[1]), Math.max(b1[1], b2[1])) - Math.max(Math.min(a1[1], a2[1]), Math.min(b1[1], b2[1])));
    if (a1[1] === a2[1] && b1[1] === b2[1] && a1[1] === b1[1]) total += Math.max(0, Math.min(Math.max(a1[0], a2[0]), Math.max(b1[0], b2[0])) - Math.max(Math.min(a1[0], a2[0]), Math.min(b1[0], b2[0])));
  }
  return total;
}

/** View transform helpers. t = {s, x, y}: screen = stage * s + (x, y). */
export function fitTransform(size, vw, vh, { min = 0.8, max = 1, pad = 12 } = {}) {
  const s = Math.max(min, Math.min(max, (vw - pad * 2) / size.w, (vh - pad * 2) / size.h));
  // content wider/taller than the pane at the minimum scale is anchored top-left (pan or centre-on-agent reaches the rest)
  return { s, x: Math.round(Math.max(pad - 0, (vw - size.w * s) / 2)), y: Math.round(Math.max(pad, (vh - size.h * s) / 2)) };
}
export function centerOnTransform(node, t, vw, vh) {
  return { s: t.s, x: Math.round(vw / 2 - node.cx * t.s), y: Math.round(vh / 2 - node.cy * t.s) };
}
export function zoomAt(t, factor, px, py, { min = 0.4, max = 2 } = {}) {
  const s = Math.max(min, Math.min(max, t.s * factor)), k = s / t.s;
  return { s, x: Math.round(px - (px - t.x) * k), y: Math.round(py - (py - t.y) * k) };
}

/** Keyboard neighbour between towers. dir in left|right|up|down. Specialists form one row; lead sits above. */
export function neighbor(id, dir, lastSpec = "strategy_researcher") {
  if (id === "lead") return dir === "down" ? lastSpec : null;
  const i = SPECIALISTS.indexOf(id);
  if (dir === "up") return "lead";
  if (dir === "left") return SPECIALISTS[Math.max(0, i - 1)];
  if (dir === "right") return SPECIALISTS[Math.min(SPECIALISTS.length - 1, i + 1)];
  return null;
}
export const TAB_ORDER = AGENT_IDS;

/** Sticky lane book for the bottom channel. assign(kind, a, b) -> route (lane never changes once given, until reset()). */
export function createLaneBook(L) {
  const lanes = [];               // lane -> ranges
  const taken = new Map();        // "kind:pairKey" -> route
  return {
    assign(kind, a, b) {
      const k = `${kind}:${pairKey(a, b)}`;
      if (taken.has(k)) return taken.get(k);
      const range = L.botRange(kind, a, b);
      let lane = 0;
      for (; lane < lanes.length; lane++) if (!lanes[lane].some((r) => overlaps(r, range))) break;
      if (lane === lanes.length) lanes.push([]);
      lanes[lane].push(range);
      const r = L.botRoute(kind, a, b, lane); taken.set(k, r); return r;
    },
    get: (kind, a, b) => taken.get(`${kind}:${pairKey(a, b)}`) || null,
    lanesUsed: () => lanes.length,
    reset() { lanes.length = 0; taken.clear(); },
  };
}
