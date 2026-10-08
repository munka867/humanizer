# IBKR connector data probe (2026-10-06) — what it can and cannot supply
Scope: read-only market-data calls only (search_contracts, search_futures, get_price_history). No account data,
no order instructions, no alerts/watchlists.

| Probe | Result |
|---|---|
| MES contract search | underlying id found; ladder with include_expired=true lists MESZ5, H6, M6, U6, Z6, H7..Z7 |
| 5-min bars, expired MESH6 | EMPTY. History is anchored to "now" and expired contracts return nothing |
| 5-min bars, MESZ6 (active) | works; ~600s delayed; last bar partial; timestamps look like bar OPEN but this is UNVERIFIED |
| Daily bars MESZ6 back to 2025-09 | volume ~0 until about Sept 2026, then 400k–1M/day: only ~3 weeks are the real liquid front month; earlier prices are an illiquid back contract |
| Index (IND) 5-min | error "Details currently unavailable" |
| SPY 5-min, 1 year | REFUSED: max 1000 data points per request; no end-date parameter, so no paging back |

Conclusions
1. The connector cannot supply a multi-month, roll-aware, intraday MES sample. Liquid MES 5-min history = ~3 weeks.
2. A 1000-bar cap means about 12 RTH days of 5-min SPY. Neither is enough: the power table in
   VALIDATION_PLAN.md needs hundreds to thousands of trades; ~3 weeks of one-trade-per-day gives ≈15 trades.
3. Tool output arrives in the chat, not on disk, so large pulls would have to be re-typed by an agent (costly,
   error-prone, and not reproducible). Not attempted.
4. Nothing was backtested on connector data. No result exists.

## Update 2026-10-08: daily bars work
- Tool results above the size limit are saved to disk by the harness (no retyping). get_price_history(step_count=1000,
  ONE_DAY) returned 1000 bars for SPY (ARCA), 999 for QQQ (NASDAQ) and IWM (ARCA): 2022-10-12/13 .. 2026-10-07.
- Ingested via src/tradelab/data/ibkr_connector.py; raw JSON kept locally in data/raw/ibkr_connector (git-ignored,
  market-data licensing / unknown repo visibility); MANIFEST.json (sha256, ids, retrieval date) IS committed.
  The container is ephemeral: raw files are lost when it is reclaimed. A re-pull returns a shifted window.
- Quality: no duplicates, zero-volume bars, or large gaps. 15 SPY / 4 QQQ / 7 IWM bars have close outside the
  reported high/low by 0.01-2.8 (closing-auction prints). Largest daily move 2025-04-09 (SPY +10.5% close-to-close).
  Prices are 'Last', unadjusted for dividends, feed delayed ~15 min.

## Account read, 2026-10-08 (read-only, user authorised; no live trading permission)
Connector queries: trades YTD + each of the last four quarters, positions, summary, balances, open orders. Result: ALL EMPTY/ZERO
(no trades, no positions, no open orders, net liquidation 0, base currency CAD). Nothing was saved (nothing to save).
Open question for the user: the connector does not return an account id here, so I cannot confirm WHICH account this is
(a funded live account would not normally be zero). Per the identity rule, account identity must be confirmed from broker
responses before any paper/live work; until then treat the account state as unknown. The data-import slice therefore still
has no real account sample (BLOCKED B1 stands; Flex export or a funded/paper account needed).
