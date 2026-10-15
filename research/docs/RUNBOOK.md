# RUNBOOK — execution core (applies to a FUTURE paper pilot; today only the simulated broker exists)

There is no real broker connection in this repository. Everything below describes how the code behaves and what an operator must do if one is ever added after review. Commands run only against the simulated broker today.

## Start-up
1. Confirm mode. The only modes that may reach a broker port are DEMO/BACKTEST/REPLAY (simulated broker) and PAPER (all gates, see EXECUTION_DESIGN section 9). LIVE is not possible.
2. One writer only: the service takes an exclusive file lock `<ledger>.lock`. A second instance fails with `WriterLockHeld`; do not delete the lock file to "fix" it - find the other process.
3. Call `service.start()` before anything else. It reconciles broker open orders, executions and positions with the ledger. Read the report: `ok` False means entries are blocked.
4. Review open incidents (`service.incidents()`).

## Reconciliation incidents
| incident | meaning | operator action |
|---|---|---|
| UNKNOWN_BROKER_ORDER | an order exists at the broker that this ledger did not create | identify it in the broker UI; decide manually; nothing was cancelled automatically |
| UNKNOWN_BROKER_EXECUTION / POSITION_MISMATCH | fills or holdings the ledger cannot explain | if a legitimate outside holding: `record_manual_adjustment(con_id, qty, operator, note)`, then `reconcile()`; otherwise flatten at the broker yourself |
| ORDER_UNCERTAIN | submit timed out / ack lost / disconnect | do NOT resubmit. Run `reconcile()`; it settles the order from broker data |
| ORDER_NOT_AT_BROKER | order was never found working and has no fills; marked CANCELED | the strategy must emit a new signal to try again |
| FILL_AFTER_TERMINAL / OVERFILL | broker reports a fill we thought finished | position is corrected from fills; inspect before resuming |
| RECONCILE_BROKER_UNREACHABLE | broker did not answer | entries stay blocked; retry `reconcile()` when reachable |
Resolve an incident only after the cause is understood: `resolve_incident(id, operator)`. Resolving does not unblock entries; a clean `reconcile()` does.

## Emergency steps (in this order)
1. `pause_new_entries(reason)` - stops new exposure, touches nothing else.
2. `emergency_stop(reason, [strategy_ids])` - persisted; also requests cancel of those strategies' ENTRY orders.
3. If the position must go: `flatten_strategy_positions(strategy_id)`. READ the report: `complete`, `residual_exposure`, per-instrument order state. It may not have completed. Check the broker UI independently.
4. Keep protective orders in place unless the position is flat; the service removes orphan protective orders once flat.
5. Do not restart the service "to clear" state: pause and emergency flags persist across restarts on purpose.

## Recovery
1. Fix the cause. 2. Start the service (reconciliation runs first). 3. `reconcile()` must be clean. 4. `clear_emergency(operator, note)` (refused while reconciliation is unclean), then `resume_new_entries(operator)`.

## What the operator must NEVER do
- Never set or share `CC_APPROVAL_KEY` with an agent, put it in the repo, a chat, a shell history on a shared host, or CI.
- Never edit the ledger database by hand, delete `*.lock`, or run two services on one ledger.
- Never raise a limit by editing code or an in-memory object; change the policy FILE, re-sign the approval (new sha256), restart.
- Never resubmit an UNCERTAIN order by hand without reconciling first.
- Never assume a protective stop caps the loss, or that a flatten order filled because it was sent.
- Never enable or approve LIVE (the code refuses; do not add a way around it).
- Never commit approval records, account ids or ledger databases (the repository is public).

## Creating an approval (user only, on your own machine)
`export CC_APPROVAL_KEY=...; python -m tradelab.execution.approve --help` (see that help for flags). Not run by agents; no record exists in the repo.
