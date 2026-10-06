# REVIEW (independent audit, phase 2)

Reviewer: validation engineer. Scope: read directly `src/tradelab/backtest/*`, `src/tradelab/data/*`,
`scripts/*`, `docs/STRATEGY_SPEC.md`, `docs/EXPERIMENT_LOG.md`, `configs/*`. No real data exists, so nothing here
says anything about market results; all runs are SYNTHETIC. Source files owned by others were NOT modified.

Tests added: `tests/test_review_integration.py` (all pass: integration + independent fill oracle + cost arithmetic)
and `tests/test_review_findings.py` (reproducers; `xfail(strict=True)` = currently reproduces the finding; the
marker must be removed when the owner fixes it, strict mode forces that). Run: `python -m pytest tests/test_review_*.py -q`
(2 passed + 7 xfailed in findings; 8 passed in integration).

Severity count: **HIGH 2, MEDIUM 4, LOW 7**, plus 1 validation-side fix made by the reviewer. No look-ahead
bug and no fill/cost arithmetic bug was found.

## Findings

### R-01 HIGH - nothing enforces the split / final-test protocol or the SYNTHETIC ban
`scripts/run_backtest.py:28-38,60-63,66-68` takes any `--bars` file (whole real history, including the final-test
period) and only prints a warning when the filename lacks "SYNTHETIC". It never consults `Split`, never writes
`results/test_access_log.jsonl`, and `contracts.py` says real validation/final-test runs "must refuse" SYNTHETIC data
but no code does (the flag is filename-only and is lost when bars are resampled/re-saved: `loader.py` keeps it in
`df.attrs` only, run_backtest re-reads a plain CSV). A researcher can therefore iterate on the full sample, and the
final test "untouched" guarantee rests on discipline alone.
Evidence: `run_backtest.py` has no split/guard import (grep). Suggested fix: add `--segment train|validation|test`
that loads a frozen split file (from `validation.splits.make_split`), filters trades with `apply_split` (never bars
without warm-up), routes `test` through `final_test_guard.evaluate_final_test` (requires freeze), writes
`data_is_synthetic` into `run_meta.json` from the CSV `SYNTHETIC` column as well as the filename, and refuses
SYNTHETIC for validation/test segments. Reviewer-side: `evaluate_final_test` now has `data_is_synthetic=` (refuses
without consuming the single look; tested).

### R-05 HIGH - run_backtest.py bypasses the tolerant loader and quality checks (timezone mislabelling)
`scripts/run_backtest.py:28-38` does `pd.to_datetime(df["ts_open"], utc=True)`. A naive Eastern-time CSV is silently
relabelled as UTC; every ET window (09:35-11:00, 11:30 exit) then lands at the wrong clock time or the day is skipped
as a data gap. A wrongly shifted dataset can yield trades at the wrong hours with plausible-looking P&L. It also skips
`data.loader.load_bars` (DST-ambiguity refusal, close-label handling) and `quality_report`.
Evidence: `tests/test_review_findings.py::test_run_backtest_script_rejects_naive_timestamps` (xfail; exit code 0).
Fix: use `load_bars(..., tz_hint=...)` (require explicit tz for naive stamps) and refuse to run if
`quality_report(...)["ok"]` is false; write the report path into `run_meta.json`.

### R-02 MEDIUM - abbreviated holiday sessions are traded
`sessions.py:22-33` skips only Dec 24, Jul 3 (Mon-Thu) and the Friday after Thanksgiving. MLK, Presidents, Memorial,
Labor Day, Juneteenth (US cash equities closed, CME equity futures trade abbreviated hours, no 09:30 cash open)
are normal "trade days" for H1/B0/B1: the day itself is traded, only the next day is skipped (incomplete prev
session). `data/calendar.py::holiday_candidates` exists but is unused by the strategy. Spec 3.6 states this limit,
so it is a documented design choice, but a sweep-reversal at an auction that does not exist is not the hypothesis.
Evidence: `test_memorial_day_is_not_traded` (xfail). Fix: skip `calendar.is_holiday_candidate(day)` days (new variant
rule -> log in EXPERIMENT_LOG before data), or require RTH completeness of the TRADING day known only later, which
is look-ahead, so a calendar is the right tool.

### R-04 MEDIUM - engine accepts corrupt bars
`engine.py:62-72` `validate_bars` checks columns, UTC index, order and NaN only. `high < low`, `high < max(open,close)`,
off-tick prices, spikes are accepted; a corrupted high/low can fabricate or suppress stop/target touches.
Evidence: `test_engine_rejects_high_below_low` (xfail). Fix: assert `high>=max(o,c,l)`, `low<=min(o,c,h)`, positive prices.

### R-06 MEDIUM - roll detection can be silently disabled by the loader
`loader.py:118` accepts a column named `symbol` as the contract label, and `check_data.py --contract X` stamps one
label on a whole file. A root symbol "MES" (or one label across several quarters) makes every bar look like the same
contract, so roll-day skipping (`sessions.py` roll logic, contract-change exits in the engine) never fires while an
unadjusted price jump enters levels and fills.
Evidence: `test_loader_does_not_treat_root_symbol_as_contract` (xfail). Fix: drop `symbol` from the aliases or
require a month/year code pattern (`^MES[FGHJKMNQUVXZ]\d{1,2}$`); in `quality_report` flag a single contract spanning
more than ~100 calendar days or any unexplained large session-break jump (the code already counts
`large_gaps_without_contract_change`; make it an error for backtests).

### R-07 MEDIUM - B0 is a looser null than the spec implies
`baselines.py:57-75`: B0 takes one trade on EVERY eligible day, at a uniformly random bar. H1 trades only on days with a
sweep, with times concentrated by the setup. Spec 2.2 asks that H1's mean R exceed the 95th percentile of the B0
replication MEANS; a mean over ~all days has a much smaller sampling sd than H1's mean over its (fewer) trades, so the
bar is lower than "H1 vs chance at its own trade count". Synthetic example (90 days): H1 and B0 trade counts differ
(see `test_b0_days_match_h1_days`, xfail). Fix: B0 replications must be matched to H1: same days as H1 and
time-of-day drawn from H1's empirical distribution, or subsample B0 reps to H1's n before taking percentiles
(VALIDATION_PLAN s5.7 already requires "matched trade count"). Exit/cost machinery IS identical (verified, below).

### R-03 LOW - 1-minute (or any non-5-min) bars are silently accepted
Strategies hard-code `BAR_MIN=5` (`sessions.py`); 1-minute bars make `rth_contiguous` false every day, so zero trades with
only a diagnostics counter (`strategy_skip_data_gap_today: 324` reproduced). Evidence: `test_one_minute_bars_rejected_not_silently_empty`
(xfail). Fix: `validate_bars` checks modal step == `bar_minutes*60`.

### R-08 LOW - default variant id is not registered
`config.build()` without overrides yields ids `H1_sweep_reversal_v1`, `B1_orb30_v1` (not in the log; the log has the
`@levels=..,target_r=..` forms). Running the baseline cell without `--set` produces trades whose `strategy` id matches
no EXPERIMENT_LOG row, defeating the "log before reading" check. Evidence: `test_every_runnable_variant_id_is_logged`
(xfail). Fix: `build()` should always emit the fully-qualified id (all grid keys) and refuse ids not present in the log.
Verified OK: the 13 logged rows equal exactly the grid cross-product in the configs/spec (test passes), within the
24-variant budget. Note `n_variants_tried` must still count any design decision revisited after seeing data.

### R-09 LOW - B0 replications concatenated; `trade_id` repeats
`run_backtest.py:83` concatenates reps with `rep` column but `trade_id` restarts per rep. Feeding the whole file to
`validation.stats.metrics` treats reps as one huge sample (wrong); key is `(rep, trade_id)`. Test:
`test_b0_reps_have_unique_trade_ids_or_rep_column` (documents). Fix: analyse each rep separately; consider a global id.

### R-10 LOW - exit on a missing bar uses the last known close
`engine.py:162-164`: a gap/new day/contract change closes at the previous bar close with label `eod`. If the stop
would have been hit inside the missing interval the true loss is larger. Rare, unknowable. Fix: label it
`gap_exit` (not `eod`, which also means the 15:55 flat) and count it; optionally assume stop-fill for adverse cases.

### R-11 LOW - B0 stop-distance geometry differs from H1's
`run_backtest.py:66-68` samples `risk_usd/qty/point_value` (distance measured from the fill OPEN) but B0 applies it
from the signal CLOSE (`baselines.py:71-75`) and the engine re-measures from the next open, blurring the
distribution and sending some draws to `cancel_risk_filter_at_fill`. Also `--match-trades` accepts any trades file,
including one that covers the test period (mild leakage of test-period stop geometry into a baseline). Fix: sample
from `meta`/signal-time distances of train+validation trades only.

### R-12 LOW - `run_meta.json` has no exposure and `summarize` ignores equity start
`run_backtest.py` calls `summarize(res.trades)` without `n_trading_days`, so exposure is never reported;
inference should use `validation.stats.metrics` anyway (done in integration test).

### R-13 LOW (fixed by reviewer) - spec criteria vs VALIDATION_PLAN
Spec s2 states CI on mean net R at cost x1; VALIDATION_PLAN s5 used net USD at 1.5x. Both must hold (the stricter
governs). `stats.metrics` now also returns `boot_ci_ev_r`. Added to VALIDATION_PLAN s5.

## Verified (no defect found)
* Look-ahead: strategies are streaming; signal bar close = `signal_ts`; fill at next bar open (`engine.py:116-157`);
  levels frozen from past data only (`sweep_reversal.py` freeze at first eligible bar, ON window ends 09:25, prev
  session snapshot taken at the first RTH bar of the new day, today's bars excluded). Existing truncation/garbage test
  covers all four strategies; I additionally re-derived every exit independently (below).
* Fill logic: independent oracle in `test_fill_oracle_matches_engine` re-simulates each trade for H1, B1 and B0 from bars
  + (side, entry_px, risk_usd) and matches exit price, reason and `exit_ts` for all trades (gap-through-stop at open,
  stop-first when both touched incl. the entry bar, target needs 1 tick through and fills at the limit, target rounded
  away from entry, 11:30 time exit at the bar open, intrabar exit_ts = bar close).
* Costs: `test_cost_arithmetic_independent` recomputes every trade's cost from the YAML numbers (2 x (0.85+0.35)
  commission; entry 0.75 tick; stop exit 1.75 ticks; limit exit 0; time exit 0.75 tick); `cost_multiplier` scales all
  components; engine scenario x1_5 equals `validation.montecarlo.cost_stress(1.5)` re-pricing exactly (fills do not
  depend on costs).
* DST / 09:30 ET window: ET conversion at use; existing DST tests plus ON-window logic (Sunday 18:00 start) correct;
  no DST transition occurs inside an ON window on a trading day.
* Roll handling (given a genuine contract column): roll day and day-after skipped, in-position contract change
  exits, fill cancelled on contract change.
* One trade/day and max one position enforced in both strategy and engine; half-day rule dates correct.
* exit_ts/entry_ts vs purge: entry_ts is the fill-bar open, exit_ts is bar close for intrabar exits (latest possible),
  so the purge/straddle test is conservative; engine trades pass `check_trades` and flow through
  `metrics`, `apply_split`, `run_montecarlo`, `cost_stress` (`test_review_integration.py`).
* B0 vs H1 exits/costs: same engine, same time exit, same cost model (given the same `--scenario`; the script lets a
  caller pass different scenarios per run, so compare runs with identical meta).

## Things that could still make results look better than reality (assumptions, not bugs)
Cost inputs are unverified assumptions (`configs/costs_mes.yaml`); 5-minute bars hide intrabar order (stop-first covers
only the both-touched case); no queue/partial-fill model for the target limit (1-tick trade-through is the proxy);
fills at the next open assume 0.75 tick total entry cost even right after a sweep spike; closed-trade drawdown only;
validation is consumed by variant selection (13 logged variants + any design decision revisited); one regime/sample.
