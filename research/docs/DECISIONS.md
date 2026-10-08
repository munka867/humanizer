# Decisions log
- D1 2026-10-06 Instrument family: MES/NQ futures chosen by user; superseded for testing by data availability (D5).
- D2 2026-10-06 Pre-register before results; 24-variant budget; final test looked at once (final_test_guard).
- D3 2026-10-06 Fills: stop-first on ambiguous bars; next-open fills; costs are assumptions until verified.
- D4 2026-10-07 Independent audit findings R-01..R-12 fixed before any real data (REVIEW.md).
- D5 2026-10-08 Connector can't supply intraday history; user approved daily tests -> DAILY_SPEC v1 (H3) pre-registered
  before computing any result. Universe SPY,QQQ,IWM.
- D6 2026-10-08 Raw market data + processed data stay out of git (licensing, unknown repo visibility); only MANIFEST committed.
- D7 2026-10-08 Account data: a trade-history read was refused by the permission classifier (privacy). Not retried or
  bypassed. Account data will be ingested only from files the user provides (Flex) or after explicit approval.
- D8 2026-10-08 Stack for command centre: stdlib+minimal deps, SQLite event store, static offline frontend (single user).
- D9 2026-10-08 LIVE and PAPER remain disabled; no order-capable tool is called by any code in this repo.
