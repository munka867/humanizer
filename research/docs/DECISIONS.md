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
- D10 2026-10-08 INCIDENT NOTE (ledger hygiene): results/test_access_log.jsonl was accidentally created by dashboard test runs
  (6 REFUSED rows for D3_V1_c2_both, 0 granted; 2026-10-08 05:53-06:01Z), got swept into WIP commit 8bcce96, then emptied/deleted by a
  worker. Verified from git history: no granted access ever occurred; the sealed final test is still unspent. The record is
  recoverable via `git show 8bcce96:research/results/test_access_log.jsonl`. Fix: tests/verify scripts must redirect the ledger
  (CC_TEST_ACCESS_LOG), and workers must not delete audit files; a real ledger, if created, must never be removed.
- D11 2026-10-08 Core fix: ctx.fmt.num accepted only a number; it now also accepts {dp} (table column formatters passed an options object).
