# READINESS — honest status (2026-10-15)

**Bottom line: nothing has ever touched a real or paper broker. A real paper pilot has not been run. The user's IBKR account is unfunded, and IBKR states a paper account requires an approved and funded live account (D15). No strategy in this project has shown positive net expectancy (docs/DAILY_RESULTS.md, docs/TREND_RESULTS.md), so there is nothing worth trading yet.**

## Local tests (simulated broker, deterministic) - `python -m pytest tests/test_exec_*.py -q`
Run on 2026-10-15: **90 passed** (see the final report for the exact line). These prove the machinery's logic against scripted faults; they do not prove behaviour against IBKR.

| area | implemented | tested vs simulated broker |
|---|---|---|
| Modes / PAPER gate / LIVE refusal | yes | yes (HMAC tamper per field, expiry, missing key, wrong account/policy/state/instrument, LIVE always refused) |
| Intent ledger idempotency | yes | yes (duplicate signal, duplicate callback) |
| Order state machine, fills-only positions | yes | yes (acks not fills, partials, out-of-order, cancel races, late fills) |
| UNCERTAIN handling / no blind retry | yes | yes |
| Risk engine (every limit in EXECUTION_DESIGN section 6, missing/stale inputs, reducing exits) | yes | yes, each limit separately |
| Single-writer lock, persisted pause/emergency | yes (flock + SQLite) | yes |
| Reconciliation on startup | yes | yes (restart, unknown orders/executions/positions) |
| Controls: pause, cancel entries, flatten, emergency | yes | yes, incl. partial/failed flatten |
| Replay (strategy -> risk -> sim broker), SHADOW | yes | yes (fixed-seed reproducibility) |
| Static: no SDK/network imports, no live endpoints | yes | yes |

## Broker paper tests
**None exist.** No test, script or code path connects to any broker, paper or live.

## NOT done
- Real broker adapter (and its review). The data-only wrapper in `command_center/ibkr` is separate, read-only and exposes no orders.
- Paper account connection; account-id check against a real account; real fill/commission behaviour.
- Margin checks against real IBKR what-if/margin data (only a hook that tests stub).
- Real shortability data, market-hours/holiday calendar, half-day handling, corporate actions, dividends.
- Confirmation from IBKR Canada that API orders are allowed for the intended US/CME products (docs/BROKER_NOTES.md).
- Cancel/replace (modify) flow; multi-instrument replay; bracket/OCO semantics (protective stops are separate orders and can both fire).
- Alerting/paging, process supervision, log shipping, backups of the ledger; encryption of the ledger.
- Operational drills (kill -9 during submit on a real session, network partition), time sync verification.
- A tested approval workflow on the user's machine (the CLI exists, its signing logic is unit tested; it has not been run by the user).
- Any strategy in registry state `approved_paper` (none exists; the repo registry cannot reach it).

## Known weaknesses to review before any real adapter
- HMAC approval defends against forged/edited files, not against an agent that can read the key from its environment or run code inside the service process; `Mode` is read-only by property but Python cannot make in-process state tamper-proof.
- Daily loss includes unrealised P&L and relies on broker equity being correct and fresh; per-day order cap is a rolling 24 h window.
- Reducing orders are exempt from frequency limits and session/spread checks by design (exits must not be blocked); a bug that mislabels an order as reducing would bypass entry checks - the test suite covers flips but real-world partial-fill/position-unknown cases need more.
- Flatten + protective stop can double-fill (see EXECUTION_DESIGN section 8).
- The simulated broker is simple; IBKR/TWS callback semantics (error codes, order-id reuse, delayed executions, 'inactive' orders, competing sessions) are not modelled.
- Single-host file lock only; not safe on network filesystems.
