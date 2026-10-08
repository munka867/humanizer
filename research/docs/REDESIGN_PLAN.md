# Dashboard redesign: plan, contract, checklist (started 2026-10-08)
Scope: replace the front end of research/command_center/ (vanilla JS, no build step, offline, no CDN) with a stock-focused
workstation. PRESERVE: event store, SSE replay, snapshot API, token auth, tests (33), research docs, strategy logic.
Stocks only (SPY/QQQ/IWM daily bars are the data actually available). No derivatives UI. No order submission anywhere:
no module may import a broker library or call a trading endpoint; paper/live need an approved broker adapter that does not exist.
Authorisation (user, 2026-10-08): read-only account data allowed; paper trading allowed in principle for the new account;
LIVE not allowed. Account is brand new and empty (all zero) so the app must read as "no account data yet", never as fabricated zeros.

## Stack audit (before)
Frontend: static/index.html + app.js (523 lines) + style.css (92 lines), single-page, SVG graph, light/dark. Backend: stdlib
http.server + SQLite event store (store.py, derive.py, schemas.py, server.py). Router hook added: command_center/router.py
(feature modules register routes; POST routes need X-CC-Token). Charting: lightweight-charts 5.2.1 (Apache-2.0) vendored in static/vendor/.

## File ownership
- core/shell: static/index.html, static/css/{base,components,shell}.css, static/js/core/*, api_core.py  (worker SHELL)
- Command Center, Stocks & Charts, Portfolio & Orders: static/js/workspaces/{command_center,stocks,portfolio}.js,
  static/css/{market}.css, api_market.py  (worker MARKET)
- Agent Team: static/js/workspaces/agents.js, static/js/agents/*, static/css/agents.css  (worker AGENTS)
- Research & Strategies, Backtesting & Monte Carlo, Data & Connections, Settings & Audit: static/js/workspaces/{research,backtest,data,settings}.js,
  static/css/research.css, api_research.py, api_data.py  (worker RESEARCH)
- tokens.css and this plan: coordinator. Tests: tests/test_cc_<area>.py per owner.

## Workspace module contract
`static/js/workspaces/<id>.js` is an ES module: `export default { id, title, icon, mount(el, ctx), unmount() }`.
Ids: command_center, stocks, portfolio, agents, research, backtest, data, settings. `mount` renders into `el` and may return nothing;
`unmount` must remove listeners/timers. ctx (built by core): `ctx.api.get(path)`, `ctx.api.post(path, body)` (adds token header when set),
`ctx.events.on(type|'*', fn)` / `ctx.events.snapshot()` (SSE with Last-Event-ID replay + dedupe by seq), `ctx.freshness` (quotes/broker/agents),
`ctx.prefs.get(key, dflt)`/`set(key, val)` (server persisted), `ctx.fmt` (money, pct, qty, time with timezone, tabular helpers),
`ctx.ui` (table(), tabs(), drawer(), toast(), confirm(), skeleton(), empty(), badge(), resizer()), `ctx.env` (mode, broker state, read-only),
`ctx.nav(id, params)`, `ctx.audit(kind, detail)`. Components live in core/ui.js and use only tokens.css variables.

## Data semantics rules (apply everywhere)
Every number shows source + as-of time + freshness class (realtime | delayed | historical | stale | unavailable). Missing = em dash with tooltip reason.
Change% is vs prior regular-session close. Daily bars only: no intraday intervals are offered. Instruments keyed by IBKR conId + exchange
(manifest has ids), not ticker alone. Demo data isolated and labelled (source='demo').

## Checklist (update as items become genuinely functional; link evidence)
- [ ] 1 Audit + tokens + components  - [ ] 2 Shell, nav, top bar, Command Center  - [x] 3 Stocks & Charts, watchlists, positions/orders tables, Command Center screen (MARKET; verified 2026-10-08: command_center/verify_market.py 42/42 on SYNTHETIC fixtures and on real local files, tests/test_cc_market*.py; screenshots/after/market-*.png)
- [ ] 4 Agent Team tower view + inspector  - [x] 5 Research, experiments, results, data import (verified 2026-10-08: command_center/verify_research.py 55/55, tests/test_cc_research*.py; screenshots/after/research-*.png)  - [ ] 6 Risk/execution controls (no order path)
- [ ] 7 Visual QA at 1920x1080, 1440x900, 1280x720 + responsive; before/after screenshots  - [ ] 8 Journeys + security review

## Polish list (coordinator, from real-data screenshots 2026-10-08)
1. Stocks & Charts opens on IWM; it should open on the first watchlist item (SPY) or the last-selected symbol (persisted).
2. Command Center "Recent events": test.result rows show 'backtester:' with the test name missing; show name + outcome.
3. Command Center right column is clipped at the bottom at 1440x900 (data freshness list cut off); make that panel scroll internally or compact it.
4. Watchlist table in Stocks sits below the fold at 1440x900 (acceptable but consider a compact side placement).
