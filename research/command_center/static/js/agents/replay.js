// Pure replay scheduling + a small player. Replay never posts anything: it only reveals already-recorded events using their ORIGINAL
// timestamps for labels. Long silences are compressed on the playback clock and flagged ("gap compressed 14m").
import { tms, gapText } from "./model.js";

export const SPEEDS = [0.5, 1, 2, 4, 8, 16];

/** buildSchedule(events) -> {at[], gap[], total}. at[i] = virtual ms at which event i is revealed. gap[i] > 0 = ORIGINAL ms compressed before event i. */
export function buildSchedule(events, { thresholdMs = 5000, capMs = 1200 } = {}) {
  const at = [], gap = [];
  let v = 0, prev = null;
  events.forEach((e, i) => {
    const t = tms(e.timestamp_utc) ?? prev ?? 0;
    if (i === 0) { at.push(0); gap.push(0); prev = t; return; }
    const d = Math.max(0, t - prev);
    if (d > thresholdMs) { v += capMs; gap.push(d); } else { v += d; gap.push(0); }
    at.push(v); prev = t;
  });
  return { at, gap, total: v };
}

export const gapLabel = (ms) => `gap compressed ${gapText(ms)}`;

/** createPlayer({events, onChange(state), now, setTimer, clearTimer}) — a deterministic clock-driven reveal. state: {idx (events revealed), playing, speed, virt, total, gapMs}. */
export function createPlayer({ events, onChange, now = () => performance.now(), setTimer = (f, ms) => setTimeout(f, ms), clearTimer = (h) => clearTimeout(h), opts } = {}) {
  const sch = buildSchedule(events, opts);
  const S = { idx: 0, playing: false, speed: 1, virt: 0, total: sch.total, n: events.length, gapMs: 0 };
  let timer = null, last = 0;
  const emit = (revealed = []) => { try { onChange?.({ ...S }, revealed); } catch (e) { console.error(e); } };
  const gapNow = () => { const nxt = S.idx; if (nxt < events.length && sch.gap[nxt] > 0 && S.idx > 0) return sch.gap[nxt]; return 0; };

  function advanceTo(v) {
    const revealed = [];
    while (S.idx < events.length && sch.at[S.idx] <= v) { revealed.push(events[S.idx]); S.idx += 1; }
    S.virt = Math.min(v, sch.total); S.gapMs = gapNow();
    return revealed;
  }
  function tick() {
    timer = null; if (!S.playing) return;
    const t = now(), dt = Math.min(250, t - last) * S.speed; last = t;
    const rev = advanceTo(S.virt + dt);
    if (S.idx >= events.length) { S.playing = false; S.gapMs = 0; emit(rev); return; }
    emit(rev);
    timer = setTimer(tick, 60);
  }
  const api = {
    state: () => ({ ...S }), schedule: sch,
    play() { if (S.playing) return; if (S.idx >= events.length) { S.idx = 0; S.virt = 0; S.gapMs = 0; emit([]); } S.playing = true; last = now(); timer = setTimer(tick, 60); emit([]); },
    pause() { S.playing = false; if (timer != null) { clearTimer(timer); timer = null; } emit([]); },
    toggle() { S.playing ? api.pause() : api.play(); },
    setSpeed(x) { if (SPEEDS.includes(x)) { S.speed = x; emit([]); } },
    /** seek(n): reveal exactly the first n events (no pulses are produced for a seek). */
    seek(n) { n = Math.max(0, Math.min(events.length, Math.round(n))); S.idx = n; S.virt = n ? sch.at[n - 1] : 0; S.gapMs = gapNow(); emit([]); },
    dispose() { S.playing = false; if (timer != null) clearTimer(timer); timer = null; },
  };
  return api;
}
