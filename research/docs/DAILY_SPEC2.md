# DAILY_SPEC2 v1 — trend-following stock strategies, pre-registered 2026-10-14 BEFORE any result was computed
Status: nothing in this spec has been run. Sample numbers are not known to the author. The sealed final-test segment of DAILY_SPEC v1 stays sealed.

## Why these
Simple, low-maintenance rules on three liquid US ETFs (SPY, QQQ, IWM). Long-only trend filters are standard, widely published ideas, not copies of the
YouTube channels; they are tested here as a plain, disclosed comparison to buy-and-hold. They are expected, if anything, to cut drawdowns, not to raise
returns. The 2022-10..2026-10 sample is mostly a rising market with one sharp drawdown episode (Apr 2025), so conclusions are regime-limited.

## Data and splits
- Frozen dataset A: data/processed (SPY, QQQ, IWM daily, 2022-10-12..2026-10-07, sha256 in data/raw/ibkr_connector/MANIFEST.json), unadjusted for dividends.
- Same chronological split as DAILY_SPEC v1 via validation.splits.make_split on dataset A: train 2022-10-12..2025-03-04, validation ..2025-12-20,
  SEALED test 2025-12-20..2026-10-07 (never computed here). Indicators warm up on earlier data only (past information), so segments are evaluated on
  daily-return series by date; no trade needs to fit inside a segment. First signals appear after the longest look-back (200 bars for V1/V2, 100 for V3).
- Dataset B (refresh 2026-10-14, MANIFEST_20261014.json): 995 overlapping days identical to A (0 revisions); adds 5 bars (2026-10-08..2026-10-14).
  Those 5 days are a FORWARD OBSERVATION only: reported separately, no inference, never mixed into train/validation.

## Strategies (long-only, one position per ETF, no leverage, no shorting, no derivatives)
Signal at the close of day t using closes through t only; trade at the OPEN of t+1.
- D4_V1_sma200: long if close_t > SMA(close, 200)_t, else cash.
- D4_V2_cross:  long if SMA(close, 50)_t > SMA(close, 200)_t, else cash.
- D4_V3_donchian: enter long if close_t > max(close_{t-100..t-1}); exit to cash if close_t < min(close_{t-50..t-1}); otherwise keep the previous state (start in cash).
No parameters are tuned: the windows (200; 50/200; 100/50) are fixed here.

## Daily return accounting (p_t = 1 if held during day t, decided at close t-1, executed at open t)
- p_{t-1}=1,p_t=1: close_t/close_{t-1}-1.  - buy at open (0→1): close_t/open_t-1-cost.  - sell at open (1→0): open_t/close_{t-1}-1-cost.  - else 0.
- Cash earns 0% (conservative vs T-bills); price return only, no dividends (strategies and baselines treated identically; understates both).
- Cost per buy or sell, as a fraction of notional: 1 bp slippage + half spread (0.5 cent / open price) + commission ($0.35 on a $10,000 position = 0.35 bp).
  ASSUMPTIONS, unverified; stress x1, x1.5, x2. Taxes, FX (CAD/USD) and short-sale fees not modelled (long-only, so no shorts).
- Pooled result = equal-weight average of the three ETFs' daily returns. Per-ETF results are also reported.

## Baselines and null
- B_H: buy and hold each ETF over the same eligible dates (from the first date the strategy's indicators exist), one buy at that open, same cost model.
- B_R random timing null: for each ETF and segment, shuffle the strategy's own on/off run lengths (same time-in-market and same number of switches), 10,000 draws,
  seed 20261014; compare the strategy's total return, max drawdown and return/volatility with the null distribution.

## Metrics (per segment, per ETF and pooled; definitions fixed)
Total net return; max drawdown (peak-to-trough of the cumulative return, daily closes); annualised volatility (daily sd x sqrt(252)); time in market; number of switches
and round trips; mean daily difference vs B_H with a day-block bootstrap 95% CI (block 10 days; sensitivity 5 and 20; 5000 resamples, seed 20261014);
drawdown reduction = 1 - MDD_strategy / MDD_B_H. No annualised return headline for samples shorter than 2 years.

## Decision rule (fixed now)
- REJECTED for a strategy if, in BOTH train and validation, its pooled total return is below B_H AND its drawdown reduction is below 25%.
- Otherwise INCONCLUSIVE. The label SUITABLE is not available from this sample: round trips are far below 30 per segment, so trade-level inference is meaningless.
- Beating the 95th percentile of the random-timing null is reported but does not change the label.

## Monte Carlo
Day-block bootstrap of each strategy's daily returns (block 10, sensitivity 5 and 20), 5000 paths, horizon = segment length, seed recorded. Report percentiles of total
return and max drawdown and P(max drawdown > 15%) and P(> 25%). Conditional on the sample; adds no independent evidence.

## Trial budget
Registered now: 3 variants (D4-V1..V3). Total registered in the project: 13 intraday + 3 daily (D3) + 3 trend (D4) = 19 of 24. Multiple-testing disclosure uses N = 19.
Final test: untouched; no variant is frozen by this spec.
