# Project inventory (2026-10-08)
Repo: github.com/munka867/humanizer, branch claude/trading-strategy-research-l7xgy7. The repo root is an unrelated
text-"humanizer" skill project; everything trading-related lives in research/ (visibility of the repo unknown; treat as
possibly public: no account data and no raw market data are committed).
Running services/processes found: none for trading (only a PDF MCP server). No pre-existing strategies, datasets, tests
or agent setup existed before this project began on 2026-10-06; nothing from the user was overwritten.

| Area | State | Evidence |
|---|---|---|
| Intraday MES pipeline (loader, quality, 5-min backtester, validation, MC) | IMPLEMENTED, tested (176 tests), reviewed (REVIEW.md), NEVER run on real data | tests, docs/ASSESSMENT.md |
| H1 sweep reversal / ORB / matched random (MES 5-min) | pre-registered, NOT RUN: no real intraday data | docs/STRATEGY_SPEC.md v1.1 |
| H2 TTrades fractal model (mechanical approximation) | registered, NOT implemented/run (needs 1m+5m+1h data) | docs/HYPOTHESES_TTRADES.md |
| H3 daily C2 closure (SPY/QQQ/IWM) | pre-registered; implementation by worker in progress | docs/DAILY_SPEC.md |
| Real data | SPY/QQQ/IWM daily, 4y, IBKR connector, delayed, unadjusted (local only; manifest committed) | data/raw/ibkr_connector/MANIFEST.json |
| IBKR account/Flex data | NONE ingested. Trade history YTD and one earlier quarter returned empty; one query was refused by the permission classifier (not retried). No Flex sample | TASKS.md B1 |
| Command-centre dashboard | slice 2 in progress (worker) | research/command_center/ |
| Broker adapter / risk engine / execution | NOT STARTED (slices 6-8). No order tools used. Live disabled | — |
| YouTube research | 2 TTrades + 1 DeltaTrend transcripts read | docs/SOURCES.md |
