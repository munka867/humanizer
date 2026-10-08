# Resumable checklist (update as work completes; link evidence)
Slice 1 Inventory & preserve .......... [x] PROJECT_INVENTORY, DECISIONS, this file, RESEARCH_INDEX
Slice 2 Durable events + dashboard ..... [x] built; coordinator-verified: 33 tests, live dark-theme page, token refusal, real events, completed-count fix. Known gaps: Lead tower says 'no events yet' when it only sent messages (label keys off status events); other screens are honest stubs; no agent runtime/SDK; Claude Code team peer messages not observable (emit.py only)
Slice 3 IBKR import + dictionary ....... [ ] BLOCKED B1 (need Flex sample) — build parsers against user-provided files only
Slice 4 One strategy end-to-end ........ [x] H3 daily: implemented, reviewed, V1 REJECTED on train/val; sealed test intentionally unspent (docs/DAILY_RESULTS.md, REVIEW.md)
          open items: fix D-04 (split label of last bar), D-05 (strategy id vs log id), D-08 (freeze hash should cover code+data SHA)
Slice 5 Expanded validation ............ [ ] walk-forward, sensitivity heatmaps, regimes, deflated Sharpe check vs reference, portfolio
Slice 6 Broker adapter (mock/replay) ... [ ] order state machine, intent ledger, idempotency, reconciliation, risk engine
Slice 7 Paper connection ............... [ ] BLOCKED B2 (paper account credentials + written IBKR Canada eligibility, docs/BROKER_NOTES.md)
Slice 8 Live-capable release (inactive)  [ ] only after the above + user approval of exact account/instrument/strategy/policy

Blockers needing the user
- B1 (updated 2026-10-08: connector account is empty/zero, identity unconfirmed) Provide an Activity Flex and/or Trade Confirmation export (CSV/XML) or approve a specific account read.
- B2 Paper account details/credentials server-side only (never in chat/prompts); confirm API-order eligibility with IBKR Canada.
- B3 Intraday history source (see DATA_REQUEST.md) if intraday strategies are to be tested.
Acceptance tests still unwritten: see the prompt's list; track each here when implemented.
