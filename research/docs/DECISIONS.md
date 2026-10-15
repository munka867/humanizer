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
- D12 2026-10-14 Windows setup gap: Python needs the tzdata package on Windows (added to requirements, friendly start-up check); optional packages (pandas/numpy/pyyaml) now give a clear 503 message.
- D13 2026-10-14 Repository confirmed PUBLIC. Market data, account data and new snapshots are git-ignored; screenshots show delayed ETF prices. User advised how to make it private.
- D14 2026-10-14 Data refresh kept as a separate snapshot (dataset B); frozen dataset A and its split are unchanged so the sealed test window is not shifted.
- D15 2026-10-15 User asked for automatic live data from IBKR and for the broker/risk build. Decision: read-only data adapter via local TWS/Gateway (paper ports by default, order calls not exposed);
  execution core built against a SIMULATED broker only; approvals gated by an HMAC key agents do not hold. IBKR states a paper account requires an approved AND funded live account; the user's account is currently unfunded (all zeros).
- D16 2026-10-15 User created a TWS paper account and asked to use ONLY it (all permissions on paper only) and to drop the TFSA. Decisions: (1) rules narrowed to the DU paper account; the Claude-session connector (linked to the other account) is no longer used;
  (2) TFSA/FHSA notes removed from the repo; IBKR accounts cannot be closed from here and the user is warned that closing the underlying live account may remove the paper account; (3) no order has been placed and none can be until the approval chain exists;
  (4) a paper broker adapter is built against a fake only; the first real paper connection is the user's.
