// NYSE regular-session clock, computed from the America/New_York wall clock (no network).
// Holiday / half-day rules are the STANDARD NYSE rules computed locally. They are NOT verified against the exchange's published
// calendar and do not know about ad-hoc closures (e.g. national days of mourning, weather), so the result is always labelled
// 'schedule unverified' (see `verified: false`).

const TZ = "America/New_York";
const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const OPEN_MIN = 9 * 60 + 30, CLOSE_MIN = 16 * 60, EARLY_CLOSE_MIN = 13 * 60;

const fmtr = new Intl.DateTimeFormat("en-US", { timeZone: TZ, hourCycle: "h23", year: "numeric", month: "numeric", day: "numeric", weekday: "short", hour: "numeric", minute: "numeric", second: "numeric" });

/** etParts(Date) -> {y,m,d,dow,minutes,seconds} in New York wall-clock time. */
export function etParts(date) {
  const p = Object.fromEntries(fmtr.formatToParts(date).map(x => [x.type, x.value]));
  return { y: +p.year, m: +p.month, d: +p.day, dow: DOW.indexOf(p.weekday), minutes: (+p.hour) * 60 + (+p.minute), seconds: +p.second };
}

const utcDate = (y, m, d) => new Date(Date.UTC(y, m - 1, d));
const dowOf = (y, m, d) => utcDate(y, m, d).getUTCDay();
const key = (y, m, d) => `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
function nthWeekday(y, m, dow, n) { const first = dowOf(y, m, 1); return 1 + ((dow - first + 7) % 7) + (n - 1) * 7; }
function lastWeekday(y, m, dow) { const last = new Date(Date.UTC(y, m, 0)).getUTCDate(); const ld = dowOf(y, m, last); return last - ((ld - dow + 7) % 7); }
function easter(y) { // anonymous Gregorian algorithm
  const a = y % 19, b = Math.floor(y / 100), c = y % 100, d = Math.floor(b / 4), e = b % 4, f = Math.floor((b + 8) / 25), g = Math.floor((b - f + 1) / 3);
  const h = (19 * a + b - d - g + 15) % 30, i = Math.floor(c / 4), k = c % 4, l = (32 + 2 * e + 2 * i - h - k) % 7, m = Math.floor((a + 11 * h + 22 * l) / 451);
  const month = Math.floor((h + l - 7 * m + 114) / 31), day = ((h + l - 7 * m + 114) % 31) + 1; return [month, day];
}
function shiftDay(y, m, d, delta) { const t = utcDate(y, m, d); t.setUTCDate(t.getUTCDate() + delta); return [t.getUTCFullYear(), t.getUTCMonth() + 1, t.getUTCDate()]; }
/** Fixed-date holiday: Saturday -> no weekday observance (NYSE rule for New Year's falling on Saturday), Sunday -> Monday. */
function fixed(y, m, d, name, out, { satObservedFriday = true } = {}) {
  const w = dowOf(y, m, d);
  if (w === 6) { if (satObservedFriday) out.set(key(...shiftDay(y, m, d, -1)), name + " (observed)"); }
  else if (w === 0) out.set(key(...shiftDay(y, m, d, 1)), name + " (observed)");
  else out.set(key(y, m, d), name);
}
const cache = new Map();
/** holidays(year) -> {full: Map<YYYY-MM-DD, name>, early: Map<YYYY-MM-DD, name>} (full-day closures and 13:00 ET early closes). */
export function holidays(y) {
  if (cache.has(y)) return cache.get(y);
  const full = new Map(), early = new Map();
  fixed(y, 1, 1, "New Year's Day", full, { satObservedFriday: false });
  full.set(key(y, 1, nthWeekday(y, 1, 1, 3)), "Martin Luther King Jr. Day");
  full.set(key(y, 2, nthWeekday(y, 2, 1, 3)), "Washington's Birthday");
  const [em, ed] = easter(y); full.set(key(...shiftDay(y, em, ed, -2)), "Good Friday");
  full.set(key(y, 5, lastWeekday(y, 5, 1)), "Memorial Day");
  if (y >= 2022) fixed(y, 6, 19, "Juneteenth", full);
  fixed(y, 7, 4, "Independence Day", full);
  full.set(key(y, 9, nthWeekday(y, 9, 1, 1)), "Labor Day");
  const tg = nthWeekday(y, 11, 4, 4); full.set(key(y, 11, tg), "Thanksgiving Day");
  early.set(key(y, 11, tg + 1), "Day after Thanksgiving");
  fixed(y, 12, 25, "Christmas Day", full);
  // Early closes (standard rule): Jul 3 when Jul 4 is Tue-Fri... and Dec 24 on a weekday. Only when that day is itself a trading day.
  const j3 = dowOf(y, 7, 3), j4 = dowOf(y, 7, 4);
  if (j3 >= 1 && j3 <= 5 && j4 !== 1 && !full.has(key(y, 7, 3))) early.set(key(y, 7, 3), "Day before Independence Day");
  const d24 = dowOf(y, 12, 24);
  if (d24 >= 1 && d24 <= 5 && !full.has(key(y, 12, 24))) early.set(key(y, 12, 24), "Christmas Eve");
  const r = { full, early }; cache.set(y, r); return r;
}

function dayInfo(y, m, d) {
  const w = dowOf(y, m, d), k = key(y, m, d), h = holidays(y);
  if (w === 0 || w === 6) return { trading: false, why: DOW[w] === "Sat" ? "Saturday" : "Sunday" };
  if (h.full.has(k)) return { trading: false, why: h.full.get(k) };
  return { trading: true, closeMin: h.early.has(k) ? EARLY_CLOSE_MIN : CLOSE_MIN, early: h.early.get(k) || null };
}

function nextOpen(y, m, d) { // first trading day strictly after y-m-d
  let [yy, mm, dd] = [y, m, d];
  for (let i = 0; i < 12; i++) { [yy, mm, dd] = shiftDay(yy, mm, dd, 1); const di = dayInfo(yy, mm, dd); if (di.trading) return { y: yy, m: mm, d: dd, dow: dowOf(yy, mm, dd) }; }
  return null;
}

const hhmm = (min) => `${String(Math.floor(min / 60)).padStart(2, "0")}:${String(min % 60).padStart(2, "0")}`;

/** nyseSession(Date=now) -> {state:'open'|'closed', label, detail, verified:false, closesAtMin, opensAtMin, reason, early, etNow}
 *  Regular hours only (09:30-16:00 ET, 13:00 on early-close days). Pre/after-hours are reported as closed (extended hours not modelled). */
export function nyseSession(date = new Date()) {
  const t = etParts(date), di = dayInfo(t.y, t.m, t.d);
  const base = { verified: false, early: false, etNow: `${hhmm(t.minutes)} ET`, note: "Schedule unverified: standard NYSE holiday/half-day rules computed locally, not checked against the exchange calendar; ad-hoc closures are not covered." };
  if (di.trading && t.minutes >= OPEN_MIN && t.minutes < di.closeMin) {
    const left = di.closeMin - t.minutes;
    return { ...base, state: "open", early: !!di.early, label: "NYSE open", detail: `Regular session, closes ${hhmm(di.closeMin)} ET${di.early ? ` (early close: ${di.early})` : ""}, ${Math.floor(left / 60)}h ${left % 60}m left`, closesAtMin: di.closeMin };
  }
  if (di.trading && t.minutes < OPEN_MIN) return { ...base, state: "closed", label: "NYSE closed", detail: `Opens today ${hhmm(OPEN_MIN)} ET (regular session)`, reason: "before the open", opensAtMin: OPEN_MIN };
  const n = nextOpen(t.y, t.m, t.d);
  const when = n ? (n.y === t.y && n.m === t.m && n.d === t.d ? "today" : `${DOW[n.dow]} ${key(n.y, n.m, n.d)}`) : "unknown";
  const why = !di.trading ? di.why : "after the close";
  return { ...base, state: "closed", label: "NYSE closed", detail: `Closed (${why}). Next regular open ${when} ${hhmm(OPEN_MIN)} ET`, reason: why, opensAtMin: OPEN_MIN };
}
