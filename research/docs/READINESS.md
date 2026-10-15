# READINESS — honest status (2026-10-15)

**Update 2026-10-15 (later): a PAPER-only adapter (`tradelab/brokers/ibkr_paper`) and a user-run smoke test now exist, tested ONLY against a FAKE ib_async object (tests/test_paper_*.py). It has never connected to TWS. The first real paper connection will be the user's; see docs/PAPER_SETUP.md.**

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
**None have been run against a real TWS.** The adapter's tests use `tests/fake_ib_orders.py` (a hand-written stand-in for ib_async) and cannot place an order.
The smoke test (`python -m tradelab.brokers.ibkr_paper.smoke_test`) is the first real test and is run by the USER only.

### PAPER adapter - implemented and FAKE-tested (tests/test_paper_*.py)
| area | status |
|---|---|
| Guard: loopback only, ports 7497/4002 only, clientId 21, DU accounts, approved-account match | implemented; tested vs fake |
| PaperOrderPermit (service-minted, signed, expiring, single-use, bound to intent+fields) | implemented; tested |
| Order mapping (STK, MKT/LMT/STP, DAY/GTC, account, orderRef=intent_id) | implemented; tested vs fake |
| Callback mapping (ack != fill, dedup, out-of-order, partial, reject, lost ack -> UNCERTAIN) | implemented; tested vs fake |
| IBKR error-code table (201,202,103,104,110,135,161,200,354,399,504,1100-1102) | implemented; tested as a table and through the fake; real TWS ordering/semantics unverified |
| Reconnect reconciliation (reqAllOpenOrders/reqExecutions/reqPositions) | implemented; tested vs fake |
| what-if margin pre-check | implemented; tested vs fake; real OrderState formats UNVERIFIED |
| Smoke test happy path + abort paths | tested vs fake with fake clock/key; NEVER run for real |
| Static guard: ib_async only in the two allowed places; execution/ pure | tested |

### Still NOT done / unverified after this step
- Any real TWS paper connection, fill, commission report or what-if output. The fake encodes MY understanding of ib_async/TWS behaviour; it can be wrong.
- ib_async 2.1.0 itself was not installed or imported in the test environment (BSD-2-Clause per its wheel metadata; the wheel was downloaded to inspect the API names only).
- Real-time market-data entitlement for the paper account; shortability data (stays None, shorting blocked); half-day/holiday calendar.
- IBKR Canada confirmation that API orders are allowed for the intended products; a funded live account is required for a paper account (D15).
- Optional dependency declaration in pyproject (`paper = ["ib_async==2.1.0"]`) is a coordinator-owned change; docs say `pip install ib_async==2.1.0`.


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
