# Plain-language assessment

**Status: INCONCLUSIVE — no real market data has been tested.**

What exists: a tested research pipeline (data loading and quality checks, a backtester with conservative fills and
costs, chronological splits with a one-look final test, and Monte Carlo/stress tools), a pre-registered strategy
spec (H1: prior-extreme sweep-and-reject reversal on MES 5-minute bars, 09:30–11:00 ET) and two baselines
(matched random entry, opening-range breakout). 176 automated tests pass on hand-made and synthetic data.

What does NOT exist: any evidence about profitability. Synthetic data only verifies the software. No result in
this repository is a market finding. Cost figures are unverified placeholders. The two YouTube channels could not be
accessed, so no rule has been copied from them.

Expected difficulty: detecting a small edge (about 0.05R per trade) needs roughly 2,500 trades, and more after the
multiple-testing adjustment. Intraday sweep reversals on MES may produce only a few hundred trades per year, so
"inconclusive" is a likely honest outcome even with several years of data.

Practical constraint: a $600 account likely cannot meet MES margin and risk limits (verify current IBKR margin).

Next step: commit real MES/ES 5-minute (or 1-minute) data with a per-bar contract column to data/raw/, then run
quality checks, baselines, and H1 on train/validation only. The final test is looked at once, after freezing.

## Update 2026-10-08 — first real-data result (daily, train+validation only)
H3 (daily candle-2 closure; SPY/QQQ/IWM, 4 years of delayed, unadjusted IBKR connector bars) was tested under
DAILY_SPEC v1 on the first 80% of the data after an independent audit (docs/REVIEW.md, "Daily pipeline audit":
0 high, 1 medium, 7 low; independent re-implementation matched counts and P&L).
- V1 (both sides, primary): REJECTED by the pre-registered point-estimate rule (train net EV -$5.32/trade, n=471).
  This is "no detectable edge", not proof of a negative edge: the 95% interval is about -$16 to +$6.
- V2 long-only: rejected. V3 short-only: inconclusive (fails sample size, interval, multiple-testing, consistency and
  concentration gates; random-null p rises from 0.082 to 0.123 with the date-matched null).
- Final test segment: NOT touched and NOT spent. Not run for a rejected primary; running V3 alone would be post-hoc.
- Intraday MES hypotheses (H1, H2) remain untested for lack of data.
Overall status: no credible edge found so far; inconclusive for everything not yet testable.
