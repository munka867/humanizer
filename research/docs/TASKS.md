# Resumable checklist (update as work completes; link evidence)
Slice 1 Inventory & preserve .......... [x] PROJECT_INVENTORY, DECISIONS, this file, RESEARCH_INDEX
Slice 2 Durable events + dashboard ..... [~] worker running (command_center/). Verify: pytest tests/test_cc_*.py, screenshots
Slice 3 IBKR import + dictionary ....... [ ] BLOCKED B1 (need Flex sample) — build parsers against user-provided files only
Slice 4 One strategy end-to-end ........ [~] H3 daily: code/tests/results by worker -> independent review -> single sealed test look
Slice 5 Expanded validation ............ [ ] walk-forward, sensitivity heatmaps, regimes, deflated Sharpe check vs reference, portfolio
Slice 6 Broker adapter (mock/replay) ... [ ] order state machine, intent ledger, idempotency, reconciliation, risk engine
Slice 7 Paper connection ............... [ ] BLOCKED B2 (paper account credentials + written IBKR Canada eligibility, docs/BROKER_NOTES.md)
Slice 8 Live-capable release (inactive)  [ ] only after the above + user approval of exact account/instrument/strategy/policy

Blockers needing the user
- B1 Provide an Activity Flex and/or Trade Confirmation export (CSV/XML) or approve a specific account read.
- B2 Paper account details/credentials server-side only (never in chat/prompts); confirm API-order eligibility with IBKR Canada.
- B3 Intraday history source (see DATA_REQUEST.md) if intraday strategies are to be tested.
Acceptance tests still unwritten: see the prompt's list; track each here when implemented.
