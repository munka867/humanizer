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
