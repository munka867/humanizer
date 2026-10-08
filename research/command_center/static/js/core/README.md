# core/ — shell platform and `ctx` contract

Everything a workspace needs. Vanilla ES modules, no build, no CDN, offline. Owner: SHELL worker. Do not edit `core/*` from a workspace; ask for a change.

A workspace is `static/js/workspaces/<id>.js`:

```js
export default {
  id: "stocks", title: "Stocks & Charts", icon: "stocks",
  async mount(el, ctx, params) { /* render into el; params = parsed #/stocks?k=v */ },
  unmount() { /* remove listeners/timers/subscriptions */ },
};
```
Ids: `command_center stocks portfolio agents research backtest data settings`. Replacing the file replaces the placeholder screen. A throw in `mount` is caught by the router (error view + Retry); the shell stays alive. `el` scrolls (the shell does not): never add a page-height wrapper with its own scrollbar; tables scroll inside `ui.table`.

**Dev harness:** `/static/dev.html?ws=<id>` mounts one workspace with a STUB ctx (in-memory prefs, GETs from `window.__dev.fixtures`, POSTs resolve `{ok:true, stub:true}`). Add `&live=1` to let GETs hit the real backend. Console: `__dev.emit({event_type, payload})`, `__dev.setFresh(feed, state, {asOf})`, `__dev.mount(id)`.

**Rules:** only `tokens.css` variables for colour; text goes in through `el()` children / `text()` (never `innerHTML`); every number has source + as-of + freshness; unknown = `fmt.missing(reason)`; no order submission anywhere.

## DOM helpers (`ctx.el`, `ctx.text`, also `ctx.ui.el/text/append/clear/icon`)
- `el(tag, attrs?, ...children) -> Element`. `attrs`: `class` (string|array), `text`, `dataset:{}`, `style:{}`/`"--var"`, `on:{click:fn}`, plain attributes (`aria-label`, `role`, `href`, `disabled`, ...). Children: strings/numbers become TEXT nodes, Nodes appended, arrays flattened, null/false skipped. `html/innerHTML/onclick=` are rejected. `tag` may be `"svg:path"` for SVG.
- `text(node, str) -> node` set textContent. `clear(node, ...children)`. `icon(name, size=16) -> <svg aria-hidden>` (names in `icons.js`: command_center stocks portfolio agents research backtest data settings menu search bell lock sun moon check alert x x_circle info clock dash pause shield shield_off plug plug_off signal radio flask gauge stop flag list columns download chevron_* external ...).

## Quick index (full names)
`ctx.api.get(path)` `ctx.api.post(path, body, {errorEl})` `ctx.events.on(type, fn)` `ctx.events.snapshot()` `ctx.freshness.get(feed)` `ctx.prefs.get(key, dflt)` `ctx.prefs.set(key, val)`
`ctx.fmt.money(v, {currency})` `ctx.fmt.pct(v)` `ctx.fmt.qty(v)` `ctx.fmt.time(v, {tz})` `ctx.fmt.missing(reason)`
`ctx.ui.table(opts)` `ctx.ui.tabs(opts)` `ctx.ui.drawer(opts)` `ctx.ui.toast(msg)` `ctx.ui.confirm(opts)` `ctx.ui.skeleton(opts)` `ctx.ui.empty(title, why, action)` `ctx.ui.badge(kind, text, icon)` `ctx.ui.resizer(opts)` `ctx.ui.tooltip(target, text)` `ctx.ui.popover(anchor, build)`
`ctx.env` `ctx.risk` `ctx.nav(id, params)` `ctx.audit(kind, detail)` and the DOM helpers `el(` / `text(`.

## `ctx.api`
- `ctx.api.get(path, {signal, timeout}) -> Promise<json>`. Rejects with `ApiError {message, status, code: 'no_token'|'bad_token'|'not_found'|'http'|'network'|'timeout', body}`.
- `ctx.api.post(path, body, {errorEl}) -> Promise<json>`. Adds `X-CC-Token`. With no token it rejects immediately (`code:'no_token'`). If `errorEl` (an Element) is given the failure is also rendered inline there (`role=alert`, "Not saved. <reason>"). Show success only after the promise resolves.
- `ctx.api.token.get()/has()/set(v)/clear()/onChange(fn)`. The token lives in the top-bar Token box and `localStorage['cc.token']`.
- `ctx.ui.inlineError(container, messageOrError)`.

## `ctx.events` (SSE `/api/stream`, Last-Event-ID resume, dedupe by seq)
- `ctx.events.on(type|'*', fn(event, meta)) -> unsubscribe`. `event` = store row `{seq, event_id, event_type, timestamp_utc, run_id, agent_id, source, payload, ...}`. `meta.replay === true` for the start-up backlog (last ~500 events), false for live ones. Each seq is delivered at most once per page, ascending. Your own `snapshot().seq` may be ahead of or behind the stream: ignore `event.seq <= snap.seq`.
- `ctx.events.snapshot({run_id, upto_seq}) -> Promise<snapshot>` (`/api/snapshot`: agents, tasks, deps, messages, artifacts, approvals, commands, mode, has_demo, seq, event_count).
- `ctx.events.recent(n=200, filter?) -> event[]` (ring buffer, newest last). `ctx.events.state() -> {conn:'connecting'|'live'|'reconnecting'|'stale', lastSeq, lastBeat(ms), staleAfterS, serverMaxSeq}`; `ctx.events.onState(fn) -> unsubscribe`.
- Reconnect: exponential backoff 0.5-8 s with jitter, resumes from `lastSeq`. `stale` = no event/ping for `staleAfterS` (default 15 s from `/api/health`; override in tests with `?cc_stale_after=4`). Dev/test only: `ctx.events.inject(event, replay=false)`.

## `ctx.freshness` (three independent feeds)
- Feeds: `quotes` (market data), `broker`, `agents` (event stream). States: `realtime | delayed | historical | stale | unavailable | unknown`.
- `ctx.freshness.get(feed) -> {feed, state, asOf (ISO or YYYY-MM-DD or null), source, detail}`; `.all()`; `.on(fn(feed, info)) -> unsubscribe`; `.set(feed, state, {asOf, source, detail})` (core sets all three; a workspace that owns a real feed may call `set`).
- Today: `quotes` = `historical` (IBKR daily bars, as-of = last bar date); `broker` = `unavailable`; `agents` follows the stream. `ctx.ui.freshBadge(feed, {showAsOf}) -> live Element` renders icon + text + as-of.

## `ctx.prefs` (server-persisted)
- `ctx.prefs.get(key, dflt)` synchronous. `ctx.prefs.set(key, jsonValue)` (key `^[a-z0-9_.-]{1,64}$`, value <= 64 KB; throws otherwise). `remove(key)`, `onChange(fn(key, value))`, `status() -> {synced, pending, error, loaded}`, `flush() -> Promise<{ok,error?}>`, `all()`.
- Writes go to `POST /api/prefs` (token required) in the background; without a token they stay in the browser cache (`cc.prefs.cache`, `cc.prefs.dirty`) and are retried when a token is set. localStorage is a UI cache only. Namespaces used by core: `shell.*`, `ui.*`, `table.<id>`, `panel.<id>`, `tabs.<id>`. Use your own prefix (`stocks.`, `agents.`...).

## `ctx.fmt` (returns strings, `"—"` when missing, unless noted)
- `money(v, {currency='USD', dp=2, sign=false, compact=false})` e.g. `$1,234.50`, `−$3.20`; `price(v, {dp})`; `qty(v, {dp=0, maxDp=4})`; `num(v, dp=2)`; `compact(v, dp=1)`.
- `pct(v, {dp=2, sign=true, ratio=false})`: `v` in PERCENT POINTS (`1.5 -> +1.50%`), or a ratio with `ratio:true`. `dir(v) -> 'pos'|'neg'|''` (class for colour; the sign is always in the text).
- `time(v, {tz, date=true, seconds=true, label=true})`: Date|ISO|epoch ms -> `2026-10-08 14:03:22 EDT`; `tz` is `America/New_York` (default) or `UTC` (`fmt.tz('UTC')` switches the default). `dateOnly(v)`, `ago(v)`, `duration(s)`.
- DOM: `missing(reason) -> <span class="missing" title=reason>—</span>`; `cell(value, 'money'|'pct'|'qty'|'num'|'price'|'time'|'text'|fn, {reason, opts, colour}) -> Element` (numbers get class `num`: right-aligned, tabular); `timeEl(v, opts) -> <time datetime title>`.

## `ctx.ui`
- `table(opts) -> {el, setRows(rows), setColumns(cols), update(), sort(key,dir), setFilter(t), toCSV(), exportCSV(), rows(), visibleRows(), visibleColumns(), destroy()}`
  - opts: `id` (persists widths/hidden/sort in `table.<id>`), `columns`, `rows`, `rowKey(row,i)`, `caption`/`ariaLabel`, `maxHeight`, `compact`, `filter:true` (text filter box), `expand(row, tdEl)` (row expansion; Enter/Space/click toggles), `onRowClick(row,tr)`, `onRowActivate(row,tr)`, `emptyTitle`, `emptyWhy`, `emptyAction`, `exportName`, `exportable=true`, `columnMenu=true`, `pageSize=500`, `pageThreshold=2000` (above it the table pages instead of rendering every row).
  - column: `{key, label, align:'left'|'num'|'center'|'right', width, minWidth, sortable=true, hideable=true, value(row), sortValue(row), format:'money'|'pct'|..., formatOpts, colour, render(row,i)->Node|string, csv(row), missing:'reason'|fn(row), title}`.
  - Sticky header, sortable (click: asc, desc, off; aria-sort), resizable (drag or Left/Right on the grip) and hideable columns, roving-tabindex keyboard nav (Up/Down/Home/End/PageUp/PageDown, Enter/Space expand, Esc collapse), numeric columns right-aligned with tabular numerals, CSV export of the visible columns and the filtered/sorted rows (all pages; text cells starting `= + - @` are quote-prefixed against formula injection).
- `tabs({tabs:[{id,label,render(panelEl, api)}], active, id?, label, onChange}) -> {el, select(id), panel(id), active()}` (render runs on first show; `id` persists the active tab).
- `drawer({title, content: Node|fn(bodyEl, api), width=480, label, onClose, footer}) -> {el, body, close(), setTitle(t)}`: right-side modal, focus trap, Esc/scrim closes, focus returns to the opener, `#app` is `inert` while open.
- `confirm({title, scope, body, confirmLabel, cancelLabel, danger, typed}) -> Promise<boolean>`: `scope` states exactly what is affected; `typed:'FLATTEN'` requires typing it. Cancel is focused by default.
- `toast(message, {kind:'success'|'error'|'info', title, ttl}) -> Element`: ONLY for outcomes the server has confirmed (persisted). Never toast optimistic success.
- `skeleton({lines, variant:'lines'|'block'|'table', height})`, `empty(title, why, action?{label,onClick|href,kind})`, `badge(kind, text, iconName?)` (kind `ok|warn|bad|info|demo|neutral`; always icon + text), `btn(label, {onClick, icon, kind:'default'|'primary'|'ghost'|'danger', title, disabled, iconOnly})`.
- `resizer({panel, side:'left'|'right', id, min, max, width, collapsible, label}) -> {handle, width(), isCollapsed(), setWidth(px), collapse(bool), toggle()}`: inserts a separator handle next to `panel` (`side` = which edge of the panel it sits on), pointer + keyboard (arrows, Home/End, Enter collapses, double-click toggles), persists `{w, collapsed}` in `panel.<id>`.
- `tooltip(target, textOrFn, {delay}) -> {set(t), destroy()}` (hover + focus, `aria-describedby`, Esc hides). `popover(anchor, build(box, api), {label, width, placement}) -> {el, close, reposition}` (one at a time; Esc/outside click closes; focus returns to anchor). `closePopover()`. `live(msg)` announces to screen readers. `freshBadge(feed)`.

## `ctx.env`, `ctx.risk`, navigation, audit
- `ctx.env`: `mode` (display label `DEMO|BACKTEST|REPLAY`), `execution` (always `'none'`), `environment`, `readOnly` (true), `broker` (`{state:'not_connected', reason, ...}` from `/api/connections`), `hasDemo`, `approvalsPending`, `connections` (raw `/api/connections`), `capability(id) -> {state, tone, detail}` (what the nav shows), `setMode(id) -> {ok, reason?, audit}`, `on(fn)`, `refresh()`. Changing the mode never changes execution; `PAPER`, `LIVE`, `SHADOW` are refused with a reason. There is no order path: do not add one.
- `ctx.risk`: `get() -> {policy, usage, flags, asOf}`, `set({policy, usage, flags})`, `on(fn)`. Until a module sets a policy the top-bar chip reads "No risk policy" and the Risk panel shows em dashes with reasons. Shapes are in `risk.js`.
- `ctx.nav(id, params?)` sets `#/<id>?k=v`; `ctx.route = {id, params}`.
- `ctx.audit(kind, {target, old, new, outcome:'applied'|'refused'|'failed'|'info', reason, detail}) -> Promise<{ok, seq?, error?}>` (never throws): records an `audit.config` event via `POST /api/audit` (token required). `kind` = `area.action` (e.g. `settings.theme`). Secrets in detail are refused by the server.

## Backend added for the shell (`api_core.py`)
`GET/POST /api/prefs`, `GET /api/instruments?q=&limit=` (from `data/raw/ibkr_connector/MANIFEST.json`: symbol, con_id, exchange, currency (USD only when inferred from a US exchange, with `currency_basis`), `name` only if the manifest has one, else null), `GET /api/connections`, `GET/POST /api/audit`. Instruments are keyed by `con_id` + `exchange`, not by ticker alone.

## Files
`ctx.js` (assembly) `api.js` `events.js` (+freshness) `prefs.js` `format.js` `ui.js` `dom.js` `icons.js` `env.js` `risk.js` `registry.js` `router.js` `shell.js` `session.js` (NYSE clock) `main.js` `dev.js`. Contract changes need coordination: other workers code against this file.
