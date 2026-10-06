# VALIDATION PLAN (pre-registered; fixed before any real data is seen)

Owner: validation/review. Code: `src/tradelab/validation/`. Thresholds live in code
(`verdict.py::Thresholds`) so they are testable; changing one after results are visible must be
logged in `docs/EXPERIMENT_LOG.md` and the report must say "thresholds changed post hoc".

Mission reminder: "no credible edge" is an acceptable result. Nothing here can prove future profit.

## 1. Splits
* Chronological by TIME span, never shuffled: **train 60% / validation 20% / final test 20%** (`splits.make_split`).
  Rationale: the search (<= 24 variants) happens on train+validation, so most data goes there; 20% test is
  the minimum that is still worth spending once. With a small sample the test set will be underpowered
  (see section 6) -- that is accepted; a bigger test set would starve the search sets and is not obtainable.
* Train: fit/inspect/design. Validation: choose between variants (this *consumes* it as out-of-sample data;
  every variant tried counts toward `n_variants_tried`). Final test: one look at one frozen config.
* Segment membership is decided by **entry time**.

## 2. Purge rule
* `purge` >= max possible trade duration + longest indicator/feature lookback that crosses the boundary
  (default 1 trading day; set from the strategy spec, intraday-flat strategies need only the overnight gap).
* A trade is kept in a segment only if `entry_ts >= segment_start + purge` (2nd/3rd segments) and
  `exit_ts < segment_end`. Trades whose entry falls in the purge gap, or that straddle a boundary, are
  **dropped and counted** (`SplitResult.dropped`); the counts must be reported with results.
* Limits: purging removes trades only. A strategy whose indicators/state are warmed on data before the
  boundary is fine for the later segment only if the state uses no information from the earlier segment's
  *outcomes* (parameter fitting). Parameters chosen on train must be frozen before the validation run.
* Walk-forward (`walk_forward_windows`) uses the same rule per window and is a robustness check, not a
  substitute for the final test.

## 3. Search budget use
* `configs/search_budget.yaml`: `max_variants_total = 24` over train+validation, <= 3 values per parameter.
* Every variant (incl. failures) logged in EXPERIMENT_LOG.md **before** its results are read.
  `n_variants_tried` in all multiple-testing adjustments = number logged, not number "kept".
* Tuning on the final test is forbidden. Exactly one frozen config goes to the final test:
  `final_test_guard.freeze_config` -> `evaluate_final_test`; every attempt is appended to
  `results/test_access_log.jsonl` (timestamp, strategy id, config hash, git hash). A second granted access
  (by any strategy) prints **"NO LONGER UNTOUCHED"** and the test must then be reported as contaminated.
  The ledger is an audit device, not access control.

## 4. Statistics reported (`stats.metrics`)
Trade count, net EV/trade (USD and R), std, hit rate, profit factor, max drawdown (USD and % of running
peak, closed-trade basis), longest losing streak, exposure, per month/quarter/year breakdown, day-clustered
t-test, **day-block bootstrap 95% CI of mean net EV/trade**, per-day Sharpe-like ratio, Bonferroni-adjusted
p and a Deflated-Sharpe-style haircut (formula and approximations in the `deflated_sharpe` docstring; the
cross-trial Sharpe variance is approximated as 1/(T-1) unless supplied, which understates the haircut if
variants genuinely differ). All inference is on **net** PnL. Trades within a day are dependent, so the
day-level quantities are primary; trade-level t is informational only.

Monte Carlo (`montecarlo.py`) always prints that it **resamples the same evidence and adds no independent
market evidence**. Its uses: tail risk of drawdown/streaks, stress to costs (x1.0/1.5/2.0, +N ticks),
fill-miss and worse-stop stress, loss-limit breach and ruin probabilities. It is never used as support for an edge.

Account constraint (documented, not a forecast): the account size (default $600) must exceed the broker's
current MES margin requirement for the intended position *and* leave room for a stop-out; margin is a
parameter (`margin_per_contract_usd`) that must be looked up at IBKR -- this plan does not assert a number.
`account_feasibility` also reports max planned risk / account; > 2% per trade is flagged. A $600 account is
very likely not practically tradable for MES; the research question (is net EV positive?) is independent
of account size, ruin analysis is not.

## 5. Decision rules (numeric, fixed now)
Evidence is measured on **validation** unless stated; "Nx costs" = `cost_stress` re-pricing
(pnl_gross - N*costs); CI = 95% two-sided day-block bootstrap (lower bound = 2.5% one-sided).

**REJECTED** if any of:
1. train net EV/trade <= 0 at 1.0x costs with n_train >= 30;
2. validation net EV/trade <= 0 at 1.0x costs with n_val >= 30;
3. upper CI bound of validation net EV/trade at 1.0x costs < 0.

**SUITABLE FOR FURTHER PAPER TESTING** (never "live-ready"; it means only "not rejected and worth a
forward paper test") requires ALL of:
1. n_train + n_val >= 200, n_val >= 100, validation days >= 40;
2. **lower CI bound of validation net EV/trade > 0 at 1.5x costs**;
3. validation net EV/trade > 0 at 2.0x costs (point estimate);
4. Bonferroni-adjusted day-level p (two-sided, N = n_variants_tried) <= 0.10;
5. Deflated Sharpe probability >= 0.90 (N = n_variants_tried);
6. consistency: >= 60% of calendar quarters (those with >= 10 trades) have net EV > 0 at 1.0x costs, and no
   single month supplies > 50% of total net P&L;
7. beats the random baseline: validation net EV at 1.0x costs >= 95th percentile of a random-entry
   baseline (same exits, sizes, costs, time-of-day windows, matched trade count; baseline built by the
   backtest owner, seeded);
8. then the frozen config passes the **single** final-test look: n >= 100, net EV > 0 at 1.0x AND 1.5x
   costs, one-sided p <= 0.05 (`classify_final`). Final-test EV <= 0 at 1.0x with n >= 100 -> REJECTED.

**INCONCLUSIVE**: everything else (including too few trades). Given section 6, this is the most likely
outcome for any realistic edge; it is a legitimate result, not a failure to be fixed by loosening thresholds.

### 5a. Relation to STRATEGY_SPEC section 2 (added in review)
STRATEGY_SPEC s2 criteria 1-5 (CI lower bound of mean net R > 0 at x1, beats B0 95th pct and B1, EV > 0 at x1.5,
n >= 200, Bonferroni) ALSO apply; where a threshold differs, the stricter one governs. The spec states the CI in R
(`metrics()['boot_ci_ev_r']`), this plan in USD (`boot_ci_ev_usd`): require both. B0 percentiles must come from
replications matched to H1's days/trade count (see REVIEW R-07).

## 6. Data needed (assumption-driven power; NOT a forecast of the real edge or variance)
One-sample test of mean net EV > 0 with independent trades, normal approximation:
`n = ((z_{1-a} + z_{power}) * sd / edge)^2`, one-sided a = 0.05, power = 0.80 (z = 1.645 + 0.842).
Bonferroni column uses a/24. The per-trade sd (in R) and edge are *assumed*; real values are unknown until
data exists. Dependence between same-day trades makes the effective n smaller -> these are lower bounds.
Computed with `stats.power_table()`:

| assumed edge (R/trade, net) | assumed sd (R) | n trades, a=0.05 | n trades, a=0.05/24 |
|---|---|---|---|
| 0.02 | 1.0 | 15,456 | 34,352 |
| 0.05 | 1.0 | 2,473 | 5,496 |
| 0.10 | 1.0 | 618 | 1,374 |
| 0.20 | 1.0 | 155 | 344 |
| 0.02 | 1.5 | 34,777 | 77,293 |
| 0.05 | 1.5 | 5,564 | 12,367 |
| 0.10 | 1.5 | 1,391 | 3,092 |
| 0.20 | 1.5 | 348 | 773 |

Implications: with ~1 trade/day (~250/yr) and a 20% validation slice, a few years of data detect only large
edges (>= ~0.2R net). A small true edge (<= 0.05R) is statistically undetectable at this scale; the verdict for
such a strategy will be INCONCLUSIVE, and we will say so rather than lower the bar. The 1.5x-cost CI rule is
deliberately stricter than the power calculation: an edge that does not survive modest cost stress is
not trusted.

## 7. Known limitations
* Closed-trade drawdown understates intratrade drawdown. Day = NY entry date.
* Percentile bootstrap is anti-conservative with few days; the 40-day floor is a minimum, not a guarantee.
* Cost re-pricing does not re-run fills; worse-stop/fill-miss stress are approximations of execution risk.
* Validation is consumed by variant selection; DSR/Bonferroni correct only roughly for that.
* A single sample period (one regime) cannot validate regime robustness; consistency checks are weak.
