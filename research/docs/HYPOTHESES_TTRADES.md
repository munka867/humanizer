# H2 — TTrades Fractal Model (TTFM), mechanical approximation  [REGISTERED, NOT RUN]
Source: the two transcripts in docs/SOURCES.md only. This is OUR approximation of a discretionary method.
Whatever it shows is not evidence about the channel's actual results. Charts were not seen.

What the transcripts say (paraphrased)
- Bias: a higher-timeframe (HTF) candle sweeps the previous candle's high/low and closes back inside the range
  ("candle 2 closure"); expect the next candle to expand the opposite way (open-high-low-close for bearish).
- Confirmation: a "change in state of delivery" — a close through the series of opposite-colour candles that
  made the swing — on the lower timeframe; HTF and LTF must be aligned.
- "T-spot": zone where the HTF wick is expected to form. Entry: 1-minute "inversion" (close through an inverted
  fair-value gap) inside it, or on the retest. Stop at the swing/wick. Target 2R, or a time exit near the hour close
  (example: hold until 11:00). Kill zones mentioned: New York 08:00–11:30 ET. Models: daily/1h, 1h/5m (1m inversion).
- Discretionary elements that cannot be coded faithfully: daily-profile context, SMT divergence, "is this just
  pattern trading", choosing retest vs close entry, skipping oversized t-spots.

Mechanical definition (v0, to be frozen before any data run): HTF = 1h candles (NY time); sweep = high>prev high
(or low<prev low) and close back inside; confirm = 5m close beyond the last opposite-colour run; entry = next 1m bar
open after a 1m close through the last opposing 1m FVG within the t-spot; stop = wick extreme + 1 tick; target 2R;
time exit at the end of the hour; window 08:00–11:30 ET; 1 trade/day; same cost model; same baselines (matched random).
Needs 1m, 5m and 1h bars (the existing backtester handles 5m only). NOT implemented, NOT run, 0 of the
remaining search budget used. Cannot be run until real multi-month 1-minute data exists.

# Method notes taken from DeltaTrend "How To: Monte Carlo Simulation" (transcript read)
- Reshuffling MC shows path dependence; a per-trade EV confidence interval that spans zero means the strategy is
  not distinguishable from no edge. (Already our rule: lower CI bound > 0 after costs.)
- Regime-switching MC (tag each trade by regime, resample with a transition matrix) preserves clustering. Our
  day-block bootstrap partly does this; a regime-tagged version is a possible later addition once real trades exist.
- Parametric/fat-tail MC is of limited use when stops cap losses. We do not add it.
- All of this resamples the same trades; it adds no independent market evidence.
