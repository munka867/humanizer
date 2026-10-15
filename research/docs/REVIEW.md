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

---

# Daily pipeline audit (phase 3)

Scope: `docs/DAILY_SPEC.md`, `src/tradelab/daily/*`, `scripts/run_daily.py`, `configs/daily_*.yaml`, `docs/DAILY_RESULTS.md`,
experiment-log rows. Tests: `tests/test_review_daily_audit.py` (7 pass, 2 strict-xfail reproducers). Hard rules observed: the sealed
test segment was NOT run or evaluated and no test-segment returns were computed or viewed; no broker tools were called; no raw prices printed.
The only test-window information used is bar-count / label counts (e.g. "197 test-labelled candidates per symbol", needed for D-04).

Severity count: **HIGH 0, MEDIUM 1, LOW 7, INFO 3.** No look-ahead, rule-fidelity or arithmetic bug found.

## Verdict on V1
**I agree: V1 = REJECTED follows from the pre-registered rule as written.** DAILY_SPEC: "REJECTED ... if train/val net EV <= 0 at 1.0x costs
with n >= 30". Train pooled net EV = -$5.32/trade (n = 471 trades on about 297 distinct dates), reproduced independently (below), so
`classify_development` correctly returns REJECTED on its first branch (`test_v1_rejection_follows_pre_registered_rule_literally`).
Caveat (D-02): this is a point-estimate screen, not evidence that EV is negative. The train 95% day-block CI is [-16.5, +6.0], the validation EV is
+$1.91 with CI [-26.6, +29.5], B_D1 percentiles 0.25 / 0.66. Statistically the honest reading is "no detectable edge; sample far too small to
distinguish +-$20/trade", and the pooled-by-instrument n (471) overstates independent information.

## Independent re-implementation (task 4) - matches exactly
Loop code written from the spec text only (own ATR, own signal rule, own cost formula, own split arithmetic), train+validation only:

| variant | segment | n (mine = worker) | net total $ (mine = worker) |
|---|---|---|---|
| V1 | train / validation | 471 / 152 | -2506.85 / +289.60 |
| V2 | train / validation | 236 / 76 | -4348.38 / -361.66 |
| V3 | train / validation | 235 / 76 | +1841.54 / +651.25 |

Per-symbol counts equal as well (e.g. V1 train SPY/QQQ/IWM 148/155/168). Gross totals also agree (V1 train -1315.54, validation +665.95).
Both implementations share my reading of ambiguous wording (simple-mean ATR, entry slippage 1 bp / exit 0.5 bp), so this validates coding, not interpretation.

## Findings

### D-01 MEDIUM - B_D1 null draws instruments independently; signals cluster by date (anti-conservative)
`src/tradelab/daily/baselines.py:200-218` (and DAILY_SPEC "per instrument"). In train+validation the three ETFs share signal dates far more
than chance: V3 train has 158 distinct dates for 235 trades vs about 205 expected under independence (V1 297 vs 356; validation V3 52 vs 67).
Quantified with a date-shuffle null (one random permutation of eligible dates applied jointly to every instrument, which preserves the
observed cross-instrument overlap and side labels; 10,000 draws, scratch script, not committed):

| variant, segment | null sd independent -> date-matched | one-sided p independent -> date-matched | percentile independent -> date-matched |
|---|---|---|---|
| V3 train | 5.61 -> 6.72 | 0.020 -> 0.048 | 0.980 -> 0.952 |
| V3 validation | 13.79 -> 17.10 | 0.082 -> 0.123 | 0.918 -> 0.877 |
| V1 train | 4.29 -> 5.20 | 0.752 -> 0.703 | 0.248 -> 0.297 |
| V1 validation | 10.33 -> 13.29 | 0.338 -> 0.361 | 0.662 -> 0.639 |
| V2 train | 5.60 -> 6.72 | 0.999 -> 0.994 | 0.001 -> 0.006 |

Null sd is understated by about 20-30%, so "favourable" p-values are too small (DAILY_RESULTS says this "flatters the strategy slightly": direction
correct, magnitude as above). No verdict changes (V3 train p = 0.048 is still far from Bonferroni N=16; V3 validation fails either way), but the
`baseline_percentile` fed to the verdict is overstated for V3 (0.918 vs 0.877). Reproducer showing the extreme case:
`test_independent_null_is_narrower_than_date_matched_null_when_signals_cluster`. Fix: report the date-matched null as primary (permute dates jointly,
or block-permute by date), keep the independent one as a footnote; or compute p from the day-clustered bootstrap of (signal - null) differences.

### D-02 INFO - REJECTED is a point-estimate rule
`validation/verdict.py` (my own rule). A single negative point estimate with n >= 30 rejects, regardless of CI width; a true-zero strategy is rejected
about half the time, and one with a small positive true EV is rejected often. For V1 here it is applied as pre-registered and is not misapplied, but
the label should be read as "failed the pre-registered screen". Test: `test_reject_rule_is_a_point_estimate_screen_not_evidence_of_negative_ev`.
Suggested (for any future pre-registration, not retroactively): reject on upper-CI < 0 or on two consecutive segments <= 0.

### D-03 LOW - ATR warm-up silently removes signals (spec silent)
`signals.py:60` requires ATR14 for eligibility; ATR is only used for the R normalisation (no stop). First 14 bars of each series cannot trade:
2 / 3 / 4 raw C2 signals lost (QQQ / SPY / IWM) in train. DAILY_RESULTS discloses it. Impact negligible, no directional bias. Fix: compute R lazily
(NaN R for those rows) and keep the trade.

### D-04 LOW - the final bar's trade is dropped by the split (sealed-test impact)
`scripts/run_daily.py:146` passes `first`/`last` = bar OPEN times to `make_split`, so the test segment ends at the last bar's open + 1 ns, while the
last candidate trade (entry = last bar open, exit 16:00 NY the same day) has `exit_ts` after it: label counts show `straddle: 1` per symbol.
Verified on real bars by labels only (no returns): the last candidate is classified `straddle` for SPY, QQQ and IWM. Up to 3 trades lost from the
test segment. Not directional, but it changes the test n. Reproducer: `test_last_day_trade_is_not_dropped_by_the_split` (xfail strict, synthetic).
Fix: pass `last = last_bar_open + 1 day` (or the last session close) to `make_split`.

### D-05 LOW - strategy ids differ from the experiment log
Trades carry `D3_V1_c2_both@universe=SPY|QQQ|IWM`; the log registers `D3-V1`. The "id must match EXPERIMENT_LOG" contract (and the final-test
guard's strategy id) cannot be checked mechanically. Same class as R-08. Reproducer: `test_strategy_id_matches_experiment_log` (xfail strict). Fix: log the exact id.

### D-06 LOW - cost model gaps / wording
`engine.py:92-98`, `configs/daily_costs.yaml`. Arithmetic matches the spec exactly (2 x $0.35 + 2 x $0.005 x qty + 1 bp x entry notional + 0.5 bp x exit notional;
x1.5/x2 scale all terms; `test_cost_arithmetic_by_hand`). Gaps (all flagged by the worker, none quantified): SEC/FINRA sell-side fees (roughly $0.3 per
$10k sale, 3-5% of the V3 EV), short availability / margin-account requirement for the short side (V3 is the only positive variant), CAD<->USD FX, whole-share
rounding (fractional shares cannot be sent as MOO/MOC orders), MOO/MOC cut-off times. Wording ambiguity "1 bp per side at the open, 0.5 bp at the close" is
read as entry 1 bp / exit 0.5 bp (a short's entry is a sell at the open); the alternative reading (1 bp on both fills) would cost another 0.5 bp ($0.5/trade). Net: costs
are probably mildly conservative for SPY/QQQ (1 bp at the open exceeds the quoted spread) and fair-to-light for IWM; direction of the error is not certain.

### D-07 LOW - unadjusted prices around ex-dividend dates
The C2 conditions compare day t with day t-1 on unadjusted prices; an ex-date drop of about 0.3-0.4% at the open shifts the comparisons toward bullish C2 on about 4
ex-dates/year/ETF (roughly 5-6% of days across the sample). Not quantified (no dividend calendar in the repo). The holding period (open->close) itself is unaffected, as the
spec says. Fix: adjust history for distributions, or drop ex-dates with a calendar.

### D-08 LOW - freeze covers config, not code or data
`run_daily.py:127-128`: `config_hash` hashes strategy + cost YAML only. Editing `src/tradelab/daily/*` or replacing `data/processed/*.csv` after the freeze changes results
without changing the hash; `--freeze` does not require a clean git tree or record the data SHA. Fix: include git commit (clean-tree check) and the bars' sha256 in the frozen hash.

### D-09 LOW - purge for 1-day trades
`configs/daily_strategy.yaml` purge = 3 calendar days drops 4 trades per symbol (2 per boundary) when 1 day would do. Harmless and conservative; boundaries fall overnight
(03:54 and 08:42 UTC), so no trade straddles. The segment-level `dropped_by_split` is reported. Noted, no change required.

### D-10 INFO - pooled n and the n>=30 / sample-size gates
Pooled trade counts treat three highly correlated ETFs as independent (train V1: 471 trades on about 297 dates). Gates in `verdict.py` use trade counts; day-level inference
(t-test, bootstrap) is correctly clustered by NY entry date (`stats.day_codes`), so the p-values and CIs are not inflated by this, only the gates.

### D-11 INFO - verified correct, nothing to fix
* Spec fidelity: H*=max(h,o,c), L*=min(l,o,c) applied to all bars (it changes no train+validation signal: 0 differing days for all three ETFs); bearish/bullish C2 with strict
  inequalities; outside day -> no signal (also correct when the close is inside the prior range); next-day check on calendar gap in [1,4] (data has gaps of 1-4 days only, no missing
  weekdays); fill at the next bar's open, exit at that day's close; qty = $10,000 / entry open; R = net / (qty x ATR14), ATR14 simple mean of TR including the first valid TR at row 1.
* Look-ahead: `signal_table` values at row t are identical on a prefix ending at t (`test_look_ahead_prefix_invariance`); the only forward reference is the DATE of t+1 (calendar), not prices.
* Daily timestamps: all bars open 09:30 ET (13:30/14:30 UTC by DST); exit_ts 16:00 ET converted with DST; day key = NY entry date.
* Verdict plumbing: `n_val_days` = distinct entry dates; Bonferroni N=16 on the two-sided day-level p; DSR from daily net P&L of days with trades (T = those days, V = 1/(T-1), documented);
  `max_period_share` computed on the validation trades by month and treated as failing when total <= 0; `val_ci_*` come from the same day-block bootstrap; cost x1.5/x2 equal engine scaling.
* Drift vs signal: B_D0 (always long) is about break-even after costs in train (-$1.51/trade, positive gross) so the open->close drift is small in this sample; the B_D1 null matches each
  instrument's long/short counts, so long-heavy strategies get no free drift credit and shorts are compared with random shorts. SPY-short losses vs QQQ/IWM-short gains (validation n = 29/20/27, CIs
  wide) are sample noise, not a bug.
* Sealed-test protocol: `--segment test` requires one variant and a freeze record, goes through `evaluate_final_test`, and the stored result files contain no test-segment statistics.

---

# Dashboard security audit (phase 4)

Scope: `command_center/` server (`server.py`, `router.py`, `schemas.py`, `store.py`, `jobs.py`, `api_core.py`, `api_market.py`,
`api_research.py`, `api_data.py`, `market_store.py`) and `static/js/**`. Method: read the code, then attacked a live instance (own port 8830, temp DB,
temp ledger/import dirs, Chromium via Playwright) with XSS, injection, traversal, auth, DoS and protocol-abuse payloads. No broker tool was called and
the sealed test segment was not run. Tests: `tests/test_review_dashboard_http.py` (25 pass, 5 strict-xfail) and `tests/test_review_dashboard_ui.py`
(2 pass, 3 strict-xfail). Strict xfail = open finding; remove the marker when fixed.

Severity count: **HIGH 0, MEDIUM 2, LOW 9, INFO 4.** No XSS, no auth bypass, no path traversal, no SQL injection, no argument injection and no
order/broker code path was found.

## Top issues
1. **P4-01 (MEDIUM)** the experiment builder lets a token holder run unlimited validation experiments with chosen seeds / symbol subsets without counting them as variants.
2. **P4-03 (MEDIUM)** `/api/bars` and the Stocks chart serve and draw the sealed final-test period's prices (199 of 1000 SPY bars) unmarked.
3. **P4-09 (LOW, honesty)** the token box says "Token set. Changes can be saved." for any string; the token is never checked against the server.

## Findings

### P4-01 MEDIUM - pre-registration is advisory; the builder is a forking-paths machine on validation
`api_research.py:221-295`. `POST /api/experiments` accepts any `seed` in [0, 2^31), any non-empty subset of the universe and any cost scenario for `train` or `validation`;
each run is queued and executed. The only brake is a flag `deviates_from_preregistered_defaults` and text telling the human to add an EXPERIMENT_LOG line. The runs are not
counted toward `n_variants_tried` (N = 16 in Bonferroni/DSR), there is no per-strategy cap (only 20 active jobs) and no requirement that a deviation be logged first (CLAUDE.md:
"every variant/parameter tried goes in EXPERIMENT_LOG BEFORE results are read"). Seven symbol subsets x seeds x two segments can be browsed until something looks good.
Evidence: `test_experiment_argv_cannot_be_injected` shows the validation rules; the acceptance of deviating runs is by design (response `deviates_from_preregistered_defaults: true`).
Fix: refuse (HTTP 422) any run whose (strategy, symbols, seed, segment) is not the registered default unless a matching REGISTERED row exists in EXPERIMENT_LOG; count accepted
deviating runs in a `variants_tried` figure shown next to every result and fed into the adjustment.

### P4-02 LOW - audit/ledger/event writes are unauthenticated in origin and spoofable
`schemas.py:validate_event`, `server.py:208`, `api_research.py:190-201`. Any token holder can post events with any `source` ("research_api", "ui", ...) and any agent id, so the Agent/Audit
views cannot distinguish system events from fabricated ones (`decision.summary`, `test.result`, `approval.resolved` look identical). A refused test-segment request also appends a row to
`results/test_access_log.jsonl` per call (unbounded spam; it never counts as an access because `granted:false`, verified with `final_test_guard.granted_accesses`).
Fix: reserve sources for server-generated events (reject `source in {"ui","research_api",...}` from `/api/events`), rate-limit/de-duplicate the denied-access rows (or write them to the audit table only).

### P4-03 MEDIUM - sealed-period prices are served and charted
`api_market.py:196-230` (`/api/bars`, `/api/quotes`), `static/js/market/chartpanel.js`. The final-test window (last 20%, 2025-12-20 onward) is served like any other data: `/api/bars` returns 199 of
1000 SPY bars in the sealed window and the Stocks chart's "ALL"/"2Y" ranges draw them. This is not a strategy evaluation, but it shows the researcher the market path the final test will be judged on,
weakening "untouched". Reproducer: `test_sealed_test_period_prices_are_not_served_unmarked` (xfail; uses timestamps only).
Fix: add `sealed_from` (derived from the frozen split) to `/api/bars` meta and shade/label the sealed window in the chart, or cap the default range at the end of validation until the final test has been consumed.

### P4-04 LOW - Content-Type check is a substring test
`server.py:102`. `"application/json" in Content-Type` accepts `text/plain;x=application/json`, a CORS-safelisted type, so a cross-site form/`fetch` can issue the request without a preflight.
Mitigated by the token requirement and the Origin check; no unauthenticated state-changing route exists. Reproducer: `test_content_type_must_be_exactly_json`. Fix: parse the media type and compare `== "application/json"`.

### P4-05 LOW - malformed bodies crash the handler instead of returning 400
`server.py:104`. `int(Content-Length)` on a non-integer, invalid UTF-8 JSON bytes (`UnicodeDecodeError`) and 100k-deep JSON (`RecursionError`) escape `do_POST` (only `ValidationError` is caught): traceback on stderr and
the connection is dropped with an empty reply. Requires the token; availability/robustness only. Reproducer: `test_malformed_bodies_get_a_400`. Fix: catch `ValueError, RecursionError` in `_body`/`do_POST` and answer 400; set a socket timeout.

### P4-06 LOW - ticker regex accepts a trailing newline
`api_research.py:31`, `api_data.py:44`. `^...$` with `re.match` lets `"SPY\n"` through (`$` matches before a final newline). It is stopped later by the universe membership check and cannot reach `argv` (symbols are joined and must be universe members), so no injection today.
Reproducer: `test_symbol_regex_rejects_trailing_newline`. Fix: `re.fullmatch` / `\Z`.

### P4-07 LOW - no Content-Security-Policy, X-Frame-Options or frame-ancestors
`server.py:69-78`. The XSS safety of the UI rests entirely on code discipline (no `innerHTML`; verified). A CSP (`default-src 'self'; script-src 'self' 'sha256-<theme snippet>'; frame-ancestors 'none'`) would contain a future slip and block clickjacking by a hostile page
(token storage is partitioned in third-party frames, which limits impact). Reproducer: `test_security_headers_present`.

### P4-08 LOW (latent) - `el()` URL-scheme filter is bypassable
`static/js/core/dom.js:8-11`. Filter `/^\s*(javascript|data|vbscript):/i` misses `java\tscript:`, `java\nscript:` (browsers strip tab/newline inside URLs) and leading control characters (`\u0001javascript:`), all returned as `ALLOWED` in Chromium. No current sink passes external text to `href`/`src`
(markdown links are limited to `https?://` or in-app doc ids, `md.js:15-16`; doc/result links are built from regex-validated names), so it is not exploitable today. Reproducer: `test_el_href_filter_blocks_scheme_obfuscation`.
Fix: strip `[\u0000- ]` before testing, and allow-list schemes (`https:`, `http:`, `#`, `/`).

### P4-09 LOW - token box claims success without validating the token
`static/js/core/shell.js:176-179`. With no pending preference writes, `Save token` calls `prefs.flush()` which is a no-op that returns ok, and the box shows "Token set. Changes can be saved." for any string; the lock icon turns to "set". The user learns the token is wrong only on the next write.
Reproducer: `test_token_box_does_not_claim_success_for_a_wrong_token`. Fix: validate with a cheap authenticated no-op (e.g. `POST /api/audit` dry-run or a `GET /api/auth/check` that requires the token) and show "Token rejected".

### P4-10 LOW - "Agents live" and "ready" labels overstate
`static/js/core/shell.js:251`, `core/env.js:59-67`. The chip reads "Agents live" when only the SSE event stream is connected; the Agents page itself says "No agent running" / "Not started". Navigation badges say "ready" for workspaces whose only condition is that some files or events exist.
Nothing claims profitability, a connected broker or fabricated numbers, but "live"/"ready" invite the wrong reading. Reproducer: `test_agents_chip_does_not_imply_running_agents`. Fix: "Event stream live", and "has data"/"loaded" instead of "ready".

### P4-11 LOW - local resource exhaustion
`server.py` (`ThreadingHTTPServer`, no socket timeout), `/api/stream`, `api_data.py:661-700`, `store.py`. Unauthenticated: unlimited SSE connections (each polls SQLite 4x/s) and partial-header sockets hold a thread forever. With the token: events (500 x 1 MB per request) and unique valid import files (up to 5 MB each) accumulate with no quota. Loopback-only, so low.
Fix: `timeout` on the handler socket, cap concurrent SSE clients, total quota on events DB / import dir.

### P4-12 INFO - token handling
Token is printed to the console at start (`server.py:342`), written to `command_center/.cc_token` (0600, git-ignored and not under `static/`, so not served), and held in plaintext `localStorage["cc.token"]`, readable by any script in the origin. It is never placed in a URL or log (`log_message` is a no-op). Acceptable for loopback research tooling; an XSS would expose it, which is why P4-07/P4-08 matter.

### P4-13 INFO - raw prices are served over the API
`/api/bars`, `/api/experiments/<id>/trades` (entry/exit px) return IBKR connector prices to the local browser. Not committed (verified below); keep them out of exports/screenshots shared outside the repo (vendor data terms are unverified).

### P4-14 INFO - strategy state overlay is reversible
`api_research.py:148-173`. `rejected -> candidate/testing` is allowed with a free-text reason (audited, `approved_*` refused with HTTP 403 and audit rows). A rejection can therefore be undone without a new pre-registered row. Consider requiring an EXPERIMENT_LOG reference for re-opening.

### P4-15 INFO - checks that held (evidence)
* **XSS/injection (task 1):** `<img onerror>`, `<svg onload>`, `<script>`, `javascript:` / `java\nscript:` URLs, template-literal and markdown-link payloads posted via `/api/events` (every text-bearing event type, agent id/task/blockers/sources/artifact path+title+url), prefs, watchlist names (blocked by `[<>]` rule, quotes/ampersands stored and shown as text), `/api/import/preview` rows and filenames. Chromium on every workspace plus inspector, drawers, tabs, graph nodes and timeline: `window.__xss` never set, 0 dialogs, 0 injected elements, payload visible as literal text (`test_event_text_renders_inert_everywhere`, `test_import_preview_renders_inert`). All sinks use `el()`/text nodes; `grep` finds no `innerHTML`/`insertAdjacentHTML`/`document.write`/`eval`/`new Function` outside comments; `md.js` builds DOM nodes only. CSV export escapes `= + - @` (`core/ui.js:432`).
* **Auth/host (task 2):** all 10 POST route patterns return 401 with no/wrong `X-CC-Token` and the test fails if a new POST route is added without being covered; constant-time comparison; Host must be loopback (evil.com, localhost.evil.com, 127.0.0.1.evil.com, userinfo tricks rejected: DNS rebinding blocked); foreign/`null` Origin rejected even with a valid token; no CORS headers; PUT/DELETE/PATCH/OPTIONS return 501.
* **Order path (task 3):** no import of any broker/network library in `command_center` or the daily pipeline (AST test); no route vocabulary for orders/execution; the only subprocess is `jobs.subprocess_runner` (argv list, no shell, scrubbed env, stdin closed, 900 s timeout). Argv is `[python, scripts/run_daily.py, --segment <train|validation>, --variant <registry value>, --symbols <regex+universe checked>, --seed <int>, --out-dir <server-generated>, --data-dir <server path>, --progress]`; injected symbol/seed/segment/strategy/notes/unknown-field payloads were all rejected (`test_experiment_argv_cannot_be_injected`). `approved_paper/approved_live` and PAPER/LIVE modes return 403; SHADOW 501.
* **Sealed test (task 4):** `segment: test` (and every spelling variant) is refused with 403/400; the refusal appends a `granted:false` row that `granted_accesses()` ignores, so it cannot consume the single look; no endpoint can create a freeze record, run `--segment test`, truncate or rewrite `results/test_access_log.jsonl`, or write `configs/strategies.json`, `docs/EXPERIMENT_LOG.md` or any config (the only file writes are: events/prefs/research/market SQLite files, experiment output dirs, quarantined import copies under `data/raw/ibkr_exports/`, and the append-only denied-access row). Events and audit tables are SQLite-trigger append-only.
* **Traversal/DoS limits (task 5):** `/api/docs|results/<name>.md` restricted to `[A-Za-z0-9_.-]+\.md` and a fixed directory; static serving resolves and checks the parent (`%2e%2e`, `..%2f`, absolute and double-slash forms all 404; a NUL byte yields a 400 with only the Python message); import filenames are reduced to a safe basename with a sha prefix (`../`, `..\`, `/etc/passwd`, quotes, NUL tested) and written via temp file + `os.replace` inside the import dir; bodies are capped at 1 MB (6.5 MB for imports, 5 MB content); XML entity-expansion and external-entity (XXE) documents are not expanded; a 5 MB CSV previews in about 2.6 s; every SQL statement is parameterised (the one f-string column name is a constant); watchlist/prefs/audit inputs are validated (key regex, 64 KB values, 1000 keys, 200 per request).
* **Exposure (task 6):** `git ls-files` shows no `data/raw/*` market files, no `data/processed`, no `.cc_token`, no SQLite files; `data/raw/ibkr_connector/MANIFEST.json` (symbols, counts, hashes, no prices) and `results/daily/*` (aggregates) and `command_center/real_events.jsonl` (3.5 KB) are tracked and contain no account ids, e-mail addresses or absolute paths (regex scan); `/api/connections`, `/api/data/coverage`, `/api/research/audit`, job tails and errors are path-redacted (`redact`, `rel`) and returned no `/home`/`/tmp` strings; 130 PNG screenshots are tracked (not inspected image by image; the account is empty and the UI shows "unavailable" for all account fields).
* **Honesty (task 7):** broker chip "Broker not connected", portfolio/account figures "— (unavailable ...; unknown, not zero)", data chip "Historical", session chip "unverified", footer "Research and simulation only", mode refusals, Monte Carlo labelled "CONDITIONAL SIMULATION", no "profitable"/"live-ready" badge anywhere, committed verdicts shown as development verdicts. Exceptions are P4-09 and P4-10.


### Dashboard audit: resolution status (coordinator, 2026-10-08)
Fixed and test-covered: P4-01 (deviating experiments refused unless registered first), P4-03 (sealed final-test window marked in /api/bars and shaded on the chart; prices are not hidden),
P4-04 (exact JSON content-type), P4-05 (malformed bodies return 400), P4-06 (ticker fullmatch), P4-07 (CSP, X-Frame-Options, Referrer-Policy; verified by command_center/verify_csp.py,
which loads every workspace with the CSP enforced and fails on any violation), P4-08 (URL scheme filter normalises control characters, allows only http/https/mailto), P4-09 (token box checks
the token with GET /api/auth/check), P4-10 (chip relabelled 'Event stream live').
Still open / accepted: P4-02 (event/audit/ledger writes are spoofable by anyone holding the token; single-user local tool), P4-11 (no socket timeouts, SSE client cap or quotas), and the
Bonferroni/DSR variant count is not auto-updated for accepted deviating runs (the count of 16 is manual until a variant ledger is wired to the stats).

---

# Trend strategies audit (phase 5)

Scope: `docs/DAILY_SPEC2.md` (read first, alone), then `src/tradelab/daily_trend/*`, `scripts/run_trend.py`, `configs/trend_*.yaml`, `results/trend/*`, `docs/TREND_RESULTS.md`.
Order of work: (1) independent loop-based oracle written from the spec text before any worker code was read (`tests/oracle_trend.py`, `tests/oracle_trend_run.py`: plain Python lists and
`csv`, nothing shared with the worker), (2) hand-computed micro-case tests, (3) only then the worker's code and results. Rules observed: performance was computed only on dataset A
truncated strictly before the sealed start (rows at/after 2025-12-20 are skipped before their prices are parsed, `oracle_trend.load`); dataset B was used only for the equality check and
the 5 forward days; no raw prices printed; no broker tools; no test segment run.
Tests: `tests/test_review_trend_oracle.py` (14 pass), `tests/test_review_trend_compare.py` (18 pass, 1 strict xfail).

Severity count: **HIGH 0, MEDIUM 0, LOW 3, INFO 6.** No bug found in signals, accounting, costs, segmenting, bootstrap, null, forward observation or the label.

## Do the independent numbers match? Yes, to machine precision
* Worker library vs oracle, all 3 variants x {train, validation} x {purge on, purge off} x {SPY, QQQ, IWM, pooled}: total return, B_H total, max drawdown, B_H max drawdown, drawdown reduction,
  annualised vol, time in market, number of days, switches and buys agree to 1e-9 (worst absolute difference 9e-16; `test_worker_matches_oracle`, 12 parametrised cases).
* The committed `results/trend/{train,validation}_summary.json` (the numbers in `docs/TREND_RESULTS.md`) vs the oracle: same, worst difference 9e-16, no mismatch; last evaluated date 2025-12-19.
* Forward observation (dataset B, 5 days 2026-10-08..14): oracle on A+new rows equals the worker's positions and returns exactly (difference 0). Dataset B equals A on all 995 overlapping bars
  (B is a rolling 1000-bar window, so it drops A's five oldest bars and adds five new ones; no revisions).
* Pooled sanity (train / validation): V1 21.50% vs B_H 22.23% (DD reduction 13.8%) / 16.07% vs 22.89% (54.8%); V2 16.37% / 7.64% vs the same B_H (12.4% / 31.2%); V3 8.73% vs 43.96% (15.3%) / 7.12% vs 22.89% (54.9%).
* Label: all three are INCONCLUSIVE under the pre-registered rule (REJECTED needs total < B_H AND DD reduction < 25% in BOTH segments; validation DD reduction is 31-55% in every variant). I derived this
  from my numbers before reading `verdict.md`; it agrees, and `test_verdict_truth_table_matches_preregistered_rule` covers the rule's edge cases. The label does not depend on the purge convention
  (V2 validation DD reduction 31.2% with purge, 26.9% without; both >= 25%, but note V2 is the variant closest to the line).

## Interpretation choices where the spec is silent (both implementations resolved them the same way; flagged, not bugs)
common start date per variant and segment across the three ETFs (SPY has one extra early bar, so per-ETF rows on its own dates would differ by one day: SPY train V1 total 22.26% vs 22.23%);
3-day purge applied by dropping the first 3 calendar days after the train/validation boundary; B_H restarted (one buy cost) at each segment's first eligible open; round trips = sells
(buys shown separately; I counted buys, and the table header says "sells (round trips)"); vol with ddof 1; first held day = bar after the first signal (index 200 for V1/V2, 101 for V3); cash 0%, price return only.

## Findings

### P5-01 INFO - segment B_H pays a buy cost that a carried strategy position does not
`analysis.py:window`. In validation B_H is a fresh buy at the first validation open (one cost, about 1.5-2 bp) while a strategy already long from train continues without a trade. Magnitude ~0.02 pp of return; no label effect.
Forward days use "held since before: no buy cost" for B_H (`forward.py`), a different convention again. Fix: state the convention in the spec or give both B_H variants.

### P5-02 LOW - the B_R null is thin and ignores cross-ETF structure
`analysis.py:shuffle_runs/random_timing_null`. (a) With few runs the null has few distinct series: a per-ETF structure of 3 on-runs and 2 off-runs allows at most 3! x 2! = 12 arrangements (`test_null_has_few_distinct_arrangements_for_short_run_structures`),
and several ETFs have 0-4 switches, so the 10,000 draws are mostly ties and a "pct" printed to three decimals (0.949, 0.957) is false precision. (b) Each ETF is shuffled independently, so the pooled null forgets that the three ETFs
trend together. A joint-timing null (one common circular shift applied to all ETFs' own on/off vectors) has a pooled total-return sd of 4.3% (train) and 7.0% (validation) for V1, against 2.5% and 2.7% for the independent shuffle (my loop
re-implementation of the independent null reproduces the worker's mean/sd/p95 within Monte Carlo error: SPY train mean 26.2% sd 2.5%, p95 30.1% in both). Same direction as D-01 in the daily audit: p95 flags are over-confident.
No label effect (the spec says the null does not change the label) and no scope reports beating p95 in a way the text over-claims, but treat the 'pct' column as indicative only. Fix: report the number of distinct arrangements and use a joint null.

### P5-03 LOW - pooled scope has no switch / round-trip counts
`scripts/run_trend.py:69-71` pops buys/sells/switches for the pooled scope and the report prints n/a, while the spec asks for switches and round trips per segment, per ETF "and pooled". Per-ETF counts are present and correct (equal to mine).
Reproducer: `test_pooled_scope_reports_switch_counts` (strict xfail). Fix: report the sum over ETFs (or the average) and say which.

### P5-04 LOW - bootstrap p-value is a non-standard formula
`analysis.py:bootstrap_mean_diff`: `p = min(1, 2*min(P(mean*<=0), P(mean*>=0)) + 1/(n+1))` on the UNcentred bootstrap distribution, then x19 for "Bonf p". It is a reasonable sign-probability but not a calibrated test, and the +1/(n+1) is added after doubling.
The CIs themselves are right: my independent moving-block bootstrap (block 10, 20,000 resamples) gives V1 pooled difference CI [-2.63, 1.44] bps (train) and [-15.85, 9.56] bps (validation) vs the worker's [-2.59, 1.51] and [-15.83, 9.32]. The columns are informational and do not drive the label, as documented.

### P5-05 INFO - sealed window
`run_trend.py:setup/truncate_before` drops every bar at/after `split.test[0]` before any frame is built (assert on the max index); `--segment test` returns exit 2 (tested: `test_script_refuses_test_segment`, `test_nothing_at_or_after_sealed_start_survives_truncation`);
the committed JSON ends 2025-12-19. The forward module needs the sealed-period closes as indicator inputs and runs the Donchian state machine through them, so the five forward POSITIONS carry information about the sealed path, but no
return for any sealed date is computed or printed (returns are taken from the last 5 rows only). Acceptable under the stated rule; it should be said that the forward positions are not independent of the sealed window.

### P5-06 INFO - look-ahead and accounting verified
Signals at row t use closes <= t only (prefix test with garbage after t, all three rules; `test_signals_use_no_future_bars`), position during day t is the signal at t-1 (`positions`), and the four cases are right: hold = close/close, buy = close/open - cost (overnight gap not earned),
sell = open/prev_close - cost (intraday move not earned), flat = 0 (hand cases in `test_case_*`). Cost is charged only on the buy day and the sell day, on that day's open, 1 bp + 0.5 cent/open + 0.35 bp. Donchian windows exclude today (t-100..t-1 and t-50..t-1) and a close equal to the
channel does not trigger. Warm-up: the first signal exists at bar 199 (V1/V2) / 100 (V3) and the first held day is the next bar; before it the strategy is in cash and not evaluated.

### P5-07 INFO - cash and dividends bias in opposite directions
Cash earns 0% in a period with positive short rates (penalises time out of market) and dividends are omitted for strategies and B_H alike (penalises B_H more, since it is always invested). Neither is quantified here; the first favours B_H, the second favours the strategies.

### P5-08 INFO - what the label means
INCONCLUSIVE here is "not rejected under a deliberately narrow rule", not support. The rule rejects only if a strategy loses on return AND fails to cut drawdown by 25% in both segments; in this sample every pooled segment shows lower total return than B_H (six of six), so the only thing that kept each variant from REJECTED was the single April-2025 drawdown episode inside validation.

## Plain reading of what these results can and cannot show
They show, descriptively and reproducibly (two independent implementations agree), that in 2023-2025 simple long-only trend filters on SPY/QQQ/IWM gave up return relative to buy-and-hold in every pooled segment (V1 by about 0.7 pp in train and 6.8 pp in validation, V2 by 5.9 / 15.3 pp, V3 by 35 / 16 pp) and cut the maximum drawdown by 12-15% in train and 31-55% in validation; costs are irrelevant at this trading frequency (x2 costs moves totals by under 0.1 pp). They cannot show that any rule has an edge or will protect capital in a future bear market:
the sample is one rising regime with one sharp but short drawdown, each ETF has 0-4 round trips per segment, the three ETFs are highly correlated so the pooled series is not three independent trials, the validation drawdown benefit rests on a single episode, the sealed period is untouched and unknown, and the five forward days carry no inference.

---

# Broker safety audit (phase 6)

Scope: the read-only IBKR data adapter (`command_center/ibkr/*`, `api_ibkr.py`, the portfolio/quotes routes that use it) and the execution core + PAPER adapter
(`src/tradelab/execution/*`, `src/tradelab/brokers/ibkr_paper/*`), against the contract in `CLAUDE.md`, `docs/DECISIONS.md` D15-D16, `docs/EXECUTION_DESIGN.md`,
`docs/PAPER_SETUP.md`, `docs/IBKR_SETUP.md`, `docs/READINESS.md`. Rules observed: no connection to any broker, TWS or Gateway (only `SimulatedBroker`, `tests/fake_ib_orders.FakeIB`,
`command_center/ibkr/fake_ib.FakeIB`); only throwaway keys; no approval file outside a temp dir; no connector tools. Nothing here proves anything about the real TWS.
Tests: `tests/test_review_broker_exec.py` (23 pass, 17 strict xfail), `tests/test_review_broker_dashboard.py` (21 pass), `tests/test_review_broker_exposure.py` (7 pass, 1 strict xfail).
A plain test = an attack that FAILED against the current guard; a strict xfail = a reproduced weakness (remove the marker when fixed). The workers' own 278 exec/paper/ibkr tests still pass.

Severity key (stricter than usual for this project): **HIGH** = a path to an unapproved order or non-paper account, OR a failure of risk containment that is reachable in normal operation with a fully valid approval.
Count: **HIGH 2, MEDIUM 5, LOW 6, INFO 6.**

## Verdict (plain)
* **Dashboard / HTTP API / UI / data adapter / experiment runner / import / prefs: I found NO path to place, modify or cancel an order, to reach the paper adapter, the approval key or the ledger, or to read a non-DU account.**
  The dashboard imports neither `tradelab.execution` nor `tradelab.brokers`; `ib_async` is imported only in `readonly_client.py`; no order-API name appears anywhere (AST scan of names, attributes and string constants); no
  `eval/exec/importlib/getattr(computed)`; the single subprocess site is the fixed-argv experiment runner; no route or UI call has order/approval vocabulary; the connect route accepts only a `kind`, so host, port, clientId and readonly
  cannot be chosen over HTTP (fake recorded 127.0.0.1:7497, readonly=True, clientId 9101); every live-ish `kind` string is refused; a fake non-DU login exposes no account data on any route; the account id is masked; fake mode is banner-labelled.
* **Order path (execution core + paper adapter): YES, there are ways to reach the broker without the FULL chain, but only from code running in the same Python process (P6-01), plus several ways a fully approved chain still exceeds its own risk limits (P6-02, 03, 04, 10, 11).**
  Every individual proof is genuinely enforced when the service is used as intended (missing/wrong key, strategy not `approved_paper`, edited policy file, other account, expired, tampered approval, replayed/edited/expired permit, 16-thread race on one permit, LIVE, simulated-only modes with the real adapter: all HELD).
* **Single most dangerous finding: P6-02** - ordinary operation (a strategy that repeats an exit signal, or a double flatten) turns a long into a short through "reducing" orders, demonstrated end-to-end with every PAPER gate satisfied (`test_paper_chain_end_to_end_stacked_exits_flip_short`: +10 becomes -10 at the fake broker).
  P6-01 is the most serious answer to "is the approval chain unbypassable": it is not, against code in the process, which `EXECUTION_DESIGN.md` states but `CLAUDE.md` does not.

## HIGH

### P6-01 HIGH (in-process only) - the order permit is forgeable; the adapter does not enforce the approval itself
`src/tradelab/execution/permit.py:19-23,150-153` (`_SECRET`, `_MINT`, `_sign` are module globals), `permit.py:124-126` (caller check = `sys._getframe(1).f_globals["__name__"]`), `brokers/ibkr_paper/adapter.py:293` (`consume_permit` is the only gate in `submit`).
Evidence: code that can `import tradelab.execution.permit` builds `PaperOrderPermit(sig=P._sign(body), _mint=P._MINT, **body)` and calls `IbkrPaperBroker.submit` directly: the fake receives `placeOrder` with no key, no strategy state, no policy hash, no risk check
(`test_adapter_refuses_a_permit_forged_from_module_globals`); `exec(code, {"__name__": "tradelab.execution.service"})` defeats `mint_permit`'s caller check (`test_mint_permit_caller_check_cannot_be_spoofed`). Even simpler, such code can call `adapter.ib.placeOrder` or `ib_async` directly.
Precondition: arbitrary Python execution on the machine that can reach the TWS paper API port (so NOT reachable from the dashboard or any HTTP route). `EXECUTION_DESIGN.md` s9/permit docstring admit "not a defence against hostile code in the same process"; `CLAUDE.md` says orders "require ALL of" the approval chain, which is only true for the service path.
Fix: make the permit unforgeable without the user's key: sign it with a key derived from `CC_APPROVAL_KEY` (agents do not hold it) instead of a process secret, and have `submit` re-verify the approval file + registry state itself; delete the frame-name check; state in CLAUDE.md that an agent with local code execution and TWS access is out of scope of the in-process controls and keep the paper API read-only in TWS ("Read-Only API" ON) except while the user runs the smoke test.

### P6-02 HIGH (risk containment) - "reducing" is judged on filled position only: stacked exits flip and flood
`execution/risk.py:159-164,184-187` (`is_reducing`), `service.py:303-314`, `service.py:418-446` (`flatten_strategy_positions`). A reducing order skips ALL new-exposure checks (instrument, session, spread, shorting, max position, frequency), and the ledger position changes only on a fill.
So with +10 filled and no fill processed yet, two SELL 10 are both "reducing" and both are sent: -10 at the broker although `allow_short: false` (`test_two_reducing_exits_cannot_flip_the_position_to_short`);
20 SELL 1 go out against a 5/minute limit (`test_reducing_order_flood_...`); calling `flatten_strategy_positions` twice does the same (`test_double_flatten_does_not_flip`). With the full PAPER chain (key, approved state, approved policy, signed approval, permits, DU checks) all satisfied the result is identical (`test_paper_chain_end_to_end_stacked_exits_flip_short`).
Realistic triggers: a strategy that re-emits an exit each bar while a limit exit rests, a retry, a double click on "flatten", a market closed with orders queued.
Fix: count working/uncertain/submitting opposite-side quantity per instrument and allow only `|position| - working_opposite_qty` as reducing; the remainder is new exposure and fully checked; apply the frequency limits to every order; make `flatten` skip instruments that already have working exits.

## MEDIUM

### P6-03 MEDIUM - the reducing exemption stays active while reconciliation says the position is wrong
`service.py:303-314`. After `reconcile()` reports `position mismatch` for an instrument (broker flat, ledger +10) a SELL 10 is still "reducing" and unchecked, and at the broker it opens a short (`test_reducing_exemption_not_used_when_ledger_and_broker_disagree`). Fix: when `recon_blocked` for that instrument, reducing quantity = min(ledger, broker) or block.

### P6-04 MEDIUM - the approval binds a policy hash, but not the policy the engine enforces
`execution/modes.py:113`, `service.py:44-47,331-334`. The gate hashes the FILE at `risk_policy_path`; the `RiskEngine` is built separately. A looser engine plus the path of the approved (stricter) file passes: 20 shares are sent although the approved policy caps SPY at 5 (`test_risk_engine_policy_must_be_the_approved_policy`).
Fix: pass `risk.policy.sha256` into `assert_may_submit` and compare with the approval; refuse construction if they differ.

### P6-05 MEDIUM - PAPER can run on an in-memory ledger; the writer lock protects a file, not an account
`service.py:56-58`. `ExecutionService(":memory:", Mode.PAPER, ...)` is accepted: no lock, no persistence (a restart forgets every intent, so duplicate signals are re-sent; reconciliation then flags the unknown orders after the fact) (`test_paper_mode_requires_a_persistent_locked_ledger`).
The lock is `flock(ledger + ".lock")` (or any `lock_path`), so two services with different ledgers can drive one account concurrently (`test_two_services_cannot_drive_the_same_broker_account`); same-ledger exclusion HOLDS (`test_single_writer_lock_blocks_second_service_on_same_ledger`).
Fix: refuse `:memory:` outside simulated modes; take an additional lock keyed on the broker account id (and have the adapter hold it for the life of the connection).

### P6-10 MEDIUM - the purpose label can skip the per-trade-risk rule
`execution/risk.py:246-255`. Stop-required and `max_risk_per_trade` apply only when `purpose == 'entry'`. A flat-to-long BUY of 40 shares labelled `exit` or `protective` passes with no stop and no risk check, while the same order as an `entry` is blocked (`NO_STOP_FOR_RISK`) (`test_purpose_label_cannot_skip_the_per_trade_risk_check`).
Fix: purposes `exit`/`protective` must be reducing or are rejected; apply the stop/risk rule to every non-reducing order.

### P6-11 MEDIUM - `capital_allocation` is signed but never enforced
Grep: the field appears only in `modes.py` (signing/field list) and `approve.py`. The documentation says the approval binds capital; an approval for $500 still allows ~$1,000 of stock (`test_signed_capital_allocation_is_enforced`). Fix: enforce gross notional after the order <= `capital_allocation`, or remove the claim.

## LOW

* **P6-06** `modes.py:74`: a non-ASCII `hmac_sha256` makes `hmac.compare_digest` raise `TypeError` (not `ModeDenied`); `propose` crashes instead of returning BLOCKED (fails closed, unhandled). Reproducer: `test_corrupt_approval_mac_is_a_clean_block`. Fix: compare `.encode()`d digests inside `try/except (TypeError, ValueError)`.
* **P6-07** `adapter.py:154-168`: executions are deduplicated on the full `execId`. If IBKR re-sends a corrected execution with the same id stem and a higher revision suffix (documented IBKR convention; NOT verified here, and listed as a residual risk in the design doc) the correction is booked as a second fill (`test_execution_correction_is_not_double_counted`, assumption stated in the marker). Fix: key on the stem, let the highest revision win, raise `resync_required` on any change.
* **P6-08** `adapter.py:127-130`, `orders.py:31-33`: the client order id uses only the first 20 hex characters of `orderRef`; a foreign order whose orderRef shares that prefix maps to OUR order (`test_foreign_orderref_with_same_prefix_is_foreign`). Intent ids are deterministic hashes, so the prefix is guessable by anyone who knows strategy/signal names. Fix: use the full 32 characters and verify against the ledger.
* **P6-09** doc/comment mismatch: `brokers/ibkr_paper/guard.py:6` and `EXECUTION_DESIGN.md` say the data adapter uses clientIds 11-19; `command_center/ibkr/guard.py` uses 9100-9109 (`test_documented_client_id_ranges_match_the_code`). No collision (the paper adapter is 21), but the stated rule is wrong.
* **P6-12** the approval expiry, permit TTL and staleness checks use the caller's clock; the broker's time (`adapter.server_time()`, unused) is never compared, so a wrong or wound-back clock revives an expired approval (`test_expired_approval_cannot_be_revived_by_a_wrong_local_clock`). Fix: refuse when |local - broker time| > a few seconds.
* **P6-13** `approve.py:35-50`: the CLI signs approvals for non-DU accounts (`U...`) and for any validity (876,000 h accepted) (`test_approve_cli_refuses_non_paper_accounts_and_unbounded_validity`). The adapter refuses a non-DU account later, so no order results, but the record should not exist. It does refuse to run without the key and writes nothing (HELD: `test_approve_cli_refuses_without_the_key_and_writes_nothing`).

## INFO
* The data adapter's "private" IB object is only name-mangled (`ReadOnlyClient._ReadOnlyClient__ib`); no HTTP path can reach it, but in-process code can (same class of limit as P6-01). `ib_async` `readonly=True` is not a client-side order block (the docs say so); the real protection is TWS "Read-Only API" plus the wrapper having no order calls. The paper adapter needs that setting OFF, so enable it only while the user runs the smoke test.
* `GET /api/portfolio`, `/api/ibkr/status|account` need no token (loopback + Host check only) and return paper-account positions/fills/exec ids when connected; the account id is masked.
* `reconcile()` marks an order CANCELED when it is absent from `reqAllOpenOrders` and has no fills; if the broker query is incomplete (API quirk, day-window limits) a live order can be treated as gone and a later signal re-sent. Listed as a residual risk in the design doc; unverifiable without a real TWS.
* Daily-loss and drawdown baselines are the first equity the service sees each trading date / ever (persisted in the ledger), not the prior close; limits and equity are compared in one unit with no currency conversion although the adapter allows CAD contracts.
* `adapter.quote()` stamps a quote with the local clock when the ticker has no time, so a delayed quote without a timestamp would look fresh (the smoke test relies on `time`).
* Honesty: `READINESS.md`, `PAPER_SETUP.md`, `IBKR_SETUP.md` and the UI badge all say nothing was run against a real TWS (test `test_docs_do_not_claim_real_broker_verification`); the fake data adapter is labelled SIMULATED in the banner, status, portfolio source and badge; `git ls-files` shows no approval, key, ledger, token, account export or real-looking account id (only placeholders such as DU1234567/DUFAKE001), no absolute user paths, and `.gitignore` covers approvals, snapshots, tokens and exports (`test_review_broker_exposure.py`).

## Guards that held under attack (for the record)
Host other than 127.0.0.1/::1; ports 7496, 4001, 4003, 7498, 7497.0, "7497", True, None; any of ten plausible environment variables; non-DU and mixed logins (disconnect, nothing sent); order account not in the login or not equal to the approved account; permit missing, forged signature, edited fields, wrong intent, expired, replayed, raced by 16 threads;
approval missing, wrong key, edited payload, other account, other strategy/version, uncovered instrument, expired, edited policy file; LIVE construction and mode reassignment; DEMO/BACKTEST/REPLAY with the real adapter; duplicate signal, lost ack then new signal (UNCERTAIN blocks entries and no resend); ack never becomes a position, tampered `filled_qty` moves nothing; foreign working order blocks entries and is never cancelled; smoke registry answers only for (`SMOKE_TEST`, `1`).
