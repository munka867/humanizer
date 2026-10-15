# EXECUTION_DESIGN — execution core (SIMULATED BROKER ONLY)

Status 2026-10-15: `src/tradelab/execution/` is deterministic machinery proven against `SimulatedBroker`. It contains **no broker adapter**,
no network code and no broker SDK import (static test `tests/test_exec_static.py`; it stays pure). A separate PAPER-only adapter,
`src/tradelab/brokers/ibkr_paper/`, implements the `BrokerPort` over ib_async and has been tested ONLY against a fake ib_async object
(section 11). Nothing here has ever talked to a real broker, paper or live. See docs/READINESS.md.

## 1. Principles
1. One execution authority (`ExecutionService`). Strategies only produce `on_bar(bar) -> Signal`; they never get a handle to the service, mode, risk engine or broker.
2. Risk is deterministic code, not prompts. Limits live in a hashed YAML policy; there is no API to raise a limit. A different limit = a different file = a new sha256 = a new approval.
3. Fills are truth. An ACK means "the broker accepted the order" and is NOT a fill; positions are the sum of executions, never of acks or order quantities.
4. Unknown means stop. Missing/stale inputs, unknown broker state or an uncertain order block NEW exposure. Orders that strictly reduce a position stay allowed.
5. LIVE does not exist. `assert_may_submit(LIVE)` always raises, and `ExecutionService(LIVE)` refuses to construct. PAPER needs five independent proofs.

## 2. Modules
| module | role |
|---|---|
| `modes.py` | `Mode` (DEMO, BACKTEST, REPLAY, SHADOW, PAPER, LIVE); `assert_may_submit`; HMAC approval creation/verification |
| `approve.py` | USER-run CLI that signs an approval record (`python -m tradelab.execution.approve --help`); agents do not hold the key |
| `intents.py` | `Proposal`, stable `intent_id`, idempotent `IntentLedger` (UNIQUE constraint) |
| `orders.py` | order state machine, executions store (dedup on exec_id), fill-derived positions |
| `risk.py` | `RiskPolicy` (YAML, validated, sha256), `RiskEngine.evaluate`, `RiskDecision` |
| `broker.py` | `BrokerPort` protocol; `SimulatedBroker` with scripted faults. No real adapter |
| `service.py` | `ExecutionService`: single-writer lock, reconciliation, submit path, controls |
| `strategy_adapter.py`, `replay.py` | one Strategy contract for backtest/replay/shadow/execution; deterministic `ReplayRunner` |
| `db.py`, `common.py` | SQLite WAL schema; Decimal helpers |

## 3. Submit path
```
strategy.on_bar(bar) -> Signal          (no authority)
   -> SignalTranslator -> Proposal      (sizing function lives here, still advisory)
   -> ExecutionService.propose():
        1. IntentLedger.insert (idempotent; duplicates return the existing intent and NEVER a second order)
        2. RiskEngine.evaluate(inputs)   inputs: broker account snapshot, ledger positions (from fills), quotes, shortable flag, margin hook
        3. service controls (new exposure only): emergency stop, pause, reconciliation not clean, any UNCERTAIN order
        4. risk decision + reasons + policy hash persisted
        5. mode: SHADOW -> record would-be order, stop.  Others -> assert_may_submit(...) re-evaluated on EVERY submit
        6. order row NEW -> SUBMITTING -> broker.submit -> (ack arrives later as an event)
```
Reducing orders (opposite side, qty <= current position, never flipping) skip the new-exposure checks, so exits are not blocked by
stale data, a breached loss limit or an emergency stop. They are still blocked when an UNCERTAIN order exists on the same instrument (blind double-close risk), and they are still subject to the mode gate.

## 4. Order state machine
```
NEW --> SUBMITTING --> ACKED --> PARTIALLY_FILLED --> FILLED
            |            |  \                 |  \
            |            |   +--> CANCEL_PENDING +--> CANCELED
            |            +--> REJECTED / CANCELED (broker/reconciliation)
            +--> UNCERTAIN (timeout, lost ack, disconnect during submit, ack timeout)
                     --> ACKED | PARTIALLY_FILLED | FILLED | REJECTED | CANCELED   (only via reconciliation or a late callback)
SUBMITTING --> PARTIALLY_FILLED / FILLED is legal: a fill callback may overtake the ack.
CANCEL_PENDING --> FILLED / PARTIALLY_FILLED / ACKED: a cancel can lose the race to a fill, or be rejected.
FILLED, CANCELED, REJECTED are terminal.  Any other transition raises IllegalTransition.
```
* Duplicate callbacks: executions are `INSERT OR IGNORE` on `exec_id`; acks/cancel-acks are no-ops when already applied.
* A fill after a terminal state is still recorded (fills are truth) and raises a FILL_AFTER_TERMINAL incident.
* UNCERTAIN blocks: the same signal returns the existing intent (no resubmission), all new entries are blocked, reducing orders on that instrument are blocked. It resolves only through `reconcile()`: found working -> ACKED; fills found -> FILLED/PARTIAL; absent at the broker and no fills -> CANCELED plus an incident (the strategy must emit a new signal id to try again; there is no automatic retry).

## 5. Why acks are not fills
A broker ack says the order is accepted and working. Quantity owned changes only with executions. The system therefore (a) never updates a position on an ack, (b) never treats a FILLED order state as the source of a position (the executions table is), (c) reconciles against the broker's executions and positions on startup. A test tampers with `orders.filled_qty` and shows positions do not move.

## 6. Risk engine
Checks (new exposure only): instrument allowed (by conId) and session (policy timezone local time); missing/stale account or quote; spread and expected-slippage tolerance; max position per instrument; shorting (policy AND instrument rule AND broker-confirmed shortable); per-trade risk (needs a planned stop on entries); daily loss (trading date in America/New_York, measured as day-start equity minus equity, so it includes unrealised P&L); max drawdown from persisted peak equity; account headroom (available funds / equity); margin hook (must return True when required); gross, net and correlated exposure (a missing correlation pair blocks); order frequency per minute and rolling 24 h. Every decision is stored with reasons, policy version and sha256. Limitations of each are in docs/READINESS.md.

## 7. Reconciliation and what survives a crash
`ExecutionService.start()` runs `reconcile()` first; until it has run (and is clean) new entries are blocked.

| survives an application crash (SQLite WAL, `synchronous=FULL`) | does NOT survive |
|---|---|
| intents, orders, state history, executions, risk decisions, incidents, shadow orders | undelivered broker events (recovered from broker order/execution queries at reconcile) |
| pause and emergency-stop flags, peak equity, day-start equity | in-memory quotes and caches |
| the single-writer lock is released by the OS when the process dies | any order that was in flight: it is found in SUBMITTING/UNCERTAIN and settled by reconciliation |

Crash windows: after the order row is written but before `broker.submit` -> order SUBMITTING, not at broker -> reconcile marks UNCERTAIN then CANCELED with an incident. After `submit` but before the ledger update -> found working at the broker -> ACKED. A fill the process never saw -> pulled from broker executions -> position corrected. Anything the ledger cannot explain (unknown broker order, unknown execution, position mismatch on any instrument) -> incident + new entries blocked; **nothing is cancelled automatically**, so existing protective orders keep working.

Not covered: a crash of the BROKER session or a disk loss (the ledger is the only record); clock skew (the service trusts the injected clock; staleness uses it).

## 8. Controls (precise semantics)
* `pause_new_entries`: persisted flag; blocks new exposure. Cancels nothing. Protective/reducing orders continue to be accepted.
* `cancel_entry_orders(strategy_id)`: requests cancellation of working ORDERS this ledger owns for that strategy with purpose `entry`. Protective/exit orders, other strategies and orders unknown to the ledger are untouched. Cancels are requests: CANCELED is recorded only on the broker's cancel ack; a fill that wins the race leaves the order FILLED.
* `flatten_strategy_positions(strategy_id)`: sends market, risk-reducing `exit` orders for the strategy's fill-derived positions, runs one event pass and RETURNS what actually happened (per instrument requested, filled, order state, residual position; overall `complete`). It can fail (reject, uncertain, delayed) and market orders can fill poorly. It does not cancel working protective orders up front; they are cancelled once the position is observed flat (an orphan stop would otherwise open a new position). Residual risk: a protective stop and the flatten order could both fill before the cancel lands and reverse the position - this is reported through positions/incidents, not hidden. **A stop order never "caps" a loss**: it becomes a market order after a trigger and can gap or slip.
* `emergency_stop(reason, strategies)`: persisted; blocks all non-reducing orders and requests cancel of the listed strategies' entry orders. `clear_emergency` requires a clean reconciliation.
* Cancelling a research job (an agent/dashboard action) is a different system: tests assert that the execution package imports nothing from `command_center` and that `command_center` does not import `tradelab.execution`.

## 9. Mode gate for PAPER (all must hold, checked on every submit)
1. `CC_APPROVAL_KEY` present in the process environment (a secret held by the user; never in the repo).
2. Strategy registry callable returns `approved_paper` for (strategy_id, version). The repo's `configs/strategies.json` has no such state.
3. Risk policy file exists and its sha256 equals the one in the approval.
4. `configs/approvals/paper_approval.json` verifies with HMAC-SHA256 over {account_id, instruments, strategy_id, strategy_version, capital_allocation, risk_policy_sha256, issued_utc, expires_utc}, is not expired and not issued in the future, and covers the instrument.
5. The account id the broker reports equals the approved account id.
The HMAC protects against an agent forging or editing a record; it does NOT protect against code running in the same process or an agent that can read the user's environment. Keep the key off agent hosts.

## 10. Known design limits
Single instrument per replay runner; daily-loss measured from equity (needs a reliable broker equity feed); no holiday/half-day session calendar; exposure uses mid quotes, not margin; correlation map is supplied, not estimated; quantities are Decimal but fractional-share and lot rules are not modelled; no order modification (cancel/replace) path beyond cancel + new intent.

## 11. PAPER adapter (`tradelab/brokers/ibkr_paper`) - added 2026-10-15, fake-tested only
Dependency direction: `brokers.ibkr_paper -> execution`; never the reverse; `command_center` imports neither. ib_async (pin 2.1.0, BSD-2-Clause, optional,
lazy import) may be imported only in `brokers/ibkr_paper` and `command_center/ibkr` (static test); `ibapi`/`ib_insync` nowhere.
* `guard.py`: loopback host only; ports 7497 and 4002 only, no override; clientId fixed at 21 (data adapter uses 11-19); after connect every managed account
  must start with `DU` (and contain the approved account) or the adapter disconnects and refuses; every order's account must equal the account bound in the
  signed approval and be one of the connected login's accounts.
* `execution/permit.py`: `PaperOrderPermit` is minted only by `ExecutionService` (caller check) after `assert_may_submit(PAPER)` succeeds for THAT order;
  per-process-secret signature (tamper-evident), 60 s expiry, bound to the intent id and the exact order fields, single use. The adapter refuses any
  `submit` without a valid permit (`BrokerRefused`: nothing was sent, so the order is marked REJECTED).
* `adapter.py`: STK only, USD/CAD, MKT/LMT/STP, DAY/GTC, whole shares, outsideRth False unless enabled, explicit `account`, `orderRef = intent_id`
  (reconciliation recomputes the client order id from it; orders without our orderRef are FOREIGN and block entries). Contract is qualified by conId
  and must come back as the same STK conId. Optional what-if margin check (also exposed as the service's `margin_hook`): a warning or an excessive
  margin change refuses; unavailable what-if returns None (the risk policy decides, `require_margin_check` blocks).
* Callback mapping: `orderStatus` PreSubmitted/Submitted -> ack (once); Cancelled -> cancel_ack; Inactive -> uncertain; Filled -> NO event (an ack/status is never a
  fill). Fills come only from `execDetails` (deduped on execId; commissions kept by execId in `adapter.commissions`). If a status reports more filled
  than execution details seen, the adapter emits `resync_required` instead of guessing, and the service blocks entries until `reconcile()` is clean.
* Error table (`errors.py`): 201/110/200 reject; 202 cancelled; 161 cancel not possible; 103/104/135 and any unmapped order error -> `uncertain` (never blind
  retry); 504/1300 disconnect (unacked orders -> UNCERTAIN) and 1100/1101/1102 resync; 354/399/21xx informational.
* Reconnect: `open_orders()` = `reqAllOpenOrders`, `executions()` = `reqExecutions`, `positions()` = `reqPositions`, all filtered to the connected accounts; the
  existing service reconciliation applies them. `shortable()` returns None, so shorting stays blocked.
* New service behaviour: `uncertain` and `resync_required` events; `BrokerRefused` marks the order REJECTED; `cancel_working_orders` (abort path);
  `flatten_strategy_positions` uses the version of the strategy that opened the position (approvals bind the version).
* `smoke_test.py`: user-run end-to-end check (docs/PAPER_SETUP.md): hard-coded 1 share / SPY 756733 / `SMOKE_TEST` v1 registry entry (exists only there, still
  needs the signed approval), typed confirmation phrase, one buy then the offsetting sell, audit trail, per-step PASS/FAIL, non-zero exit on any anomaly,
  cancels its own working orders and flattens only what it created (never a blind flatten if the cancels are unconfirmed).
Residual risks: execution-id corrections, delayed commission reports, `reqExecutions` day-window limits, STP orders that gap, real TWS error ordering.
