# DAILY_SPEC v1 — pre-registered 2026-10-08, BEFORE any daily result was computed
Status: no daily backtest has been run. Data pulled and quality-checked only (docs/DATA_FINDINGS.md, MANIFEST.json).

## Data
SPY, QQQ, IWM daily bars from the IBKR connector (read-only market data): ~1000 bars each, 2022-10-12..2026-10-07,
RTH session bars, "Last" prices, unadjusted for dividends, ~15-min delayed feed. 4 years is all the connector allows
(1000-bar cap, no end-date parameter). Open/close are used as-is. Envelope repair (fixed, not a parameter):
H* = max(high, open, close), L* = min(low, open, close), because 15/1000 SPY bars have a close outside the
reported high/low (closing-auction print vs continuous-session range; largest case 2025-04-09, +2.29).

## Hypothesis H3 — "daily candle-2 closure" bias (from TTrades transcripts, docs/SOURCES.md; our mechanization)
Day t is read at its close (knowable at t close). Using H*, L* of days t and t-1:
- BEARISH C2: H*_t > H*_{t-1} (sweep of prior high) AND close_t < H*_{t-1} AND close_t > L*_{t-1} (closed back inside prior range).
  Action: SHORT at the open of day t+1, exit at the close of day t+1. 
- BULLISH C2: L*_t < L*_{t-1} AND close_t > L*_{t-1} AND close_t < H*_{t-1}. Action: LONG at open t+1, exit at close t+1.
- If both conditions hold on the same day (outside day), no signal. No stops, no targets, 1 position per instrument per day,
  no overnight holding beyond the session, no signals when t+1 is not the next trading day in the data (gap > 4 calendar days).
Economic rationale (stated by the source, unverified): failed breaches of prior extremes mark liquidity grabs followed by
reversal. Opposing evidence: equities drift up (short signals face headwind), daily mean reversion/momentum effects are
small and unstable; the source offers only hand-picked examples. TTrades also conditions on lower-timeframe
confirmation, daily-profile context and time windows — NOT mechanizable at daily frequency; H3 is a partial test.

Variants (registered, count toward the 24 total; none tuned): V1 both sides; V2 long-only (bullish C2); V3 short-only.
No free parameters. Primary = V1.

## Sizing / costs (ASSUMPTIONS, to be stress-tested; not verified fee facts)
Fixed notional $10,000 per trade (fractional shares allowed in simulation; IBKR fractional API support not verified).
risk_usd normalisation (for R-multiples ONLY, no actual stop) = qty * ATR14 where ATR14 uses H*,L*, closes through day t.
Costs per trade (round trip): commission $0.35/order minimum assumption x2; half-spread 0.5 cent per side x qty;
slippage 1 bp of notional per side at the open, 0.5 bp at the close; SEC/FINRA sell fees ignored (small; flagged);
FX conversion CAD<->USD ignored (flagged: may matter for a CAD account). Stress: x1, x1.5, x2.
Dividends: not included (open->close holding avoids ex-dividend overnight drops except ex-date open gap, which is outside the hold).

## Baselines / null
B_D0 unconditional: every day open->close long, per instrument (drift reference; also gives mean daily open->close).
B_D1 matched random-day permutation: same number of signals and same long/short split, days drawn uniformly at random
(seeded, 10,000 draws) per instrument; report where the strategy's mean net return falls. A short signal must beat the
random SHORT distribution, not buy&hold.
Pooled inference: instruments are highly correlated on the same day -> pool by DATE with day-block bootstrap; also report each.

## Splits / protocol
Chronological 60/20/20 on dates via validation.splits.make_split (purge = 3 calendar days). Develop on train+validation
(no parameters to fit; used for descriptive stats and to check the pipeline). Final test evaluated ONCE through
validation.final_test_guard after a frozen config; reuse marks it consumed.

## Decision rule (fixed now)
Use validation/verdict.py thresholds. With ~100 expected signals total across 3 correlated ETFs, the sample-size gates
(n_train+n_val >= 200, n_val >= 100) will almost surely fail -> best attainable verdict is INCONCLUSIVE; REJECTED is
possible if train/val net EV <= 0 at 1.0x costs with n >= 30, or the validation CI upper bound < 0. We will NOT lower
the gates. "No credible edge" or "inconclusive" are acceptable outcomes.

## Monte Carlo
validation.montecarlo: day_block (default), trade_block, iid; seeds recorded; cost stress x1/1.5/2; fill-miss and
worse-fill stress. Conditional-on-sample estimates only; adds no independent evidence.
