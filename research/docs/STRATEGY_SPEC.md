# STRATEGY_SPEC — Pre-registered strategy and backtest specification

Status: PRE-REGISTERED (v1, written before any backtest code was run and before any real data exists).
Any change to a rule marked FIXED below creates a new variant and must be logged in
`docs/EXPERIMENT_LOG.md` BEFORE results are read.

## 0. Provenance and honesty statement

- We could NOT access the YouTube channels @deltatrendtrading or @TTrades_edu from this sandbox.
  No transcript, video, or page from either channel was read. Nothing here is copied from them.
- The hypotheses are built from general public knowledge of ICT-style ideas ("liquidity sweep / stop
  run of a prior high or low, then reversal"). Whether that matches what the channels teach is UNKNOWN.
- No real market data exists in the project at the time of writing. No result is claimed. "No credible
  edge" is an expected-possible outcome.
- All cost numbers are ASSUMPTIONS (see `configs/costs_mes.yaml`), not verified broker fees.

## 1. Instrument and data conventions

- Primary: MES (point value USD 5.00, tick 0.25 = USD 1.25). Parameterisable: ES (50), NQ (20), MNQ (2).
  Risk-filter limits in points (section 3.6) are MES-calibrated and MUST be re-stated in the config
  before running another instrument.
- Bars: 5-minute, format of `contracts.py` (UTC `ts_open`; bar [T, T+5min) knowable at T+5min).
- Session logic uses `America/New_York` local time (ET), converted from UTC at use, DST-aware.
- Definitions (ET): RTH = 09:30 <= t < 16:00. ON (overnight) for trading date D = bars with open time in
  [18:00 ET on the previous calendar day, 09:30 ET on D). (For Monday this starts Sunday 18:00.)
  Bars 16:00-18:00 ET are used for nothing.
- Bar labelled with open time t is "completed" at t+5min. A decision at bar close uses only bars with
  open time <= t. Orders decided at that close are filled at the open of the NEXT bar (open time = t+5min).

## 2. Hypothesis H1 (falsifiable form) — prior-extreme liquidity sweep reversal

Economic story (unverified): resting stops beyond obvious prior extremes get triggered, and a close back
inside the level signals absorption, followed by mean reversion for some minutes.

H1: A mechanically defined fade of a one-bar sweep-and-reject of {previous RTH high/low, overnight
high/low} during 09:35-11:00 ET on MES has positive expected net P&L per trade after the assumed costs.

H1 is REJECTED (treated as "no credible edge") unless ALL of the following hold, evaluated by the
validation module on chronologically separated data (train for exploration, validation for selection,
final test once):
1. Mean net R per trade > 0 and mean net USD per trade > 0 at cost scenario x1, with the lower bound of a
   95% day-block bootstrap CI of mean net R > 0 on validation data.
2. Mean net R exceeds the 95th percentile of the B0 random-entry replications (section 6) on the same
   days, and exceeds B1 (ORB) mean net R on the same dates (reported with CI of the difference).
3. Mean net R remains > 0 at cost scenario x1.5.
4. At least 200 trades in train+validation combined; fewer => verdict "INCONCLUSIVE", not "supported".
5. Multiple-testing: the best grid cell must survive Bonferroni adjustment using n_variants_tried
   (budget in `configs/search_budget.yaml`).
Passing 1-5 on validation only makes H1 a candidate for the single final-test evaluation; it is not proof.

## 3. H1 exact rules (all FIXED unless listed in the grid, section 7)

### 3.1 Reference levels (only completed data)
- `prev_hi`, `prev_lo`: high/low of the previous trading day's RTH session. "Previous trading day" is the
  last ET date with RTH bars in the data, at most 4 calendar days earlier. It must be COMPLETE: exactly 78
  consecutive 5-min bars 09:30..15:55 ET, single contract.
- `on_hi`, `on_lo`: high/low of the ON window of D (bars up to and including the 09:25 bar). The ON
  window is valid only if its first bar opens at 18:00 ET, there is no gap between consecutive bars
  greater than 60 minutes, its last bar opens at 09:25 ET, and it has a single contract.
- Levels are fixed when RTH of D begins; they do not update during the day. Today's own bars are never
  part of today's levels. The `levels` parameter selects which set is active (`prev_rth`, `overnight`, `both`).
  A day is skipped if a source required by the active set is invalid.

### 3.2 Signal bar
- Eligible signal bars: open time in [09:35, 10:55] ET inclusive (so the signal bar closes by 11:00 ET).
- DECISION: the first RTH bar (09:30-09:35) is NEVER a signal bar. Justification: its range includes the
  opening auction/print dislocation, spreads and slippage are worst there, and a signal on it would be
  filled at 09:35 into the most volatile minutes with the least trustworthy fill model. This is a
  conservative choice made before seeing data; the cost is possibly missing some of the most frequent
  sweeps. The 09:30 bar is still used for the contiguity check and for the next day's levels.
- SHORT signal: `high >= L + min_sweep_ticks*tick` and `close < L` for some active high level L
  (the sweep and the rejection close are the SAME bar). LONG: `low <= L - min_sweep_ticks*tick` and
  `close > L` for an active low level L. `min_sweep_ticks` = 1 (FIXED).
- A bar that satisfies both a short and a long condition produces NO signal (and does not consume the day).
- A level does not need to be "untouched" before the signal bar (the simple reading of the hypothesis is
  used; first-touch variants would be new variants).
- The first valid signal of the day consumes the single daily opportunity, including when it is then
  rejected by a filter (no hunting for a better second signal).

### 3.3 Entry
- Market order, sent at the signal bar close, filled at the open of the next bar (nominal price = that
  open; costs added separately, section 5). A signal on the last eligible bar (10:55) fills at the 11:00 open.
- The engine CANCELS the order (no trade, counted in diagnostics) if: the next bar is not the
  contiguous next bar; the contract differs; the fill bar open is at/after the time exit; the nominal fill
  leaves a stop distance outside the risk filter (3.6) or on the wrong side of the stop.

### 3.4 Stop and target
- SHORT stop = signal bar high + `stop_buffer_ticks*tick` (1). LONG stop = signal bar low - 1 tick.
  Stop is a resting stop-market order.
- Stop distance for filtering at the decision time: `D_sig = |stop - signal_close|`.
  Stop distance at fill: `D = |stop - fill_open|` (used for risk and target).
- Target = entry nominal -/+ `target_r * D`, rounded to the tick AWAY from entry (harder to hit).
  Resting limit order. FIXED model: a limit fills only if the bar trades at least `limit_through_ticks`
  (=1) beyond the price (touching is not enough); fills at the limit price, never improved.

### 3.5 Exits (all inside one ET day; never held across overnight, roll, or a data gap)
Evaluated bar by bar after entry, the entry bar itself included for stop/target only:
1. Open gap through the stop (open beyond stop): exit at the OPEN (worse than stop), reason `stop`.
2. Time exit: first bar with open time >= 11:30 ET: market exit at that open, reason `time`.
3. Hard flat: first bar with open >= 15:55 ET: market exit at that open, reason `eod`
   (cannot bind in the baseline as 11:30 is earlier; engine enforces it for any variant).
4. Intrabar: stop touched (high>=stop for shorts / low<=stop for longs) and target traded through.
   Both in the same bar => STOP first, reason `ambiguous_stop`. Stop only => `stop` at the stop price.
   Target only => `target` at the target price.
5. Discontinuity safety: if a position is open and the next bar is missing, belongs to another ET date, or
   the contract changed, exit at the PREVIOUS bar close, reason `eod`. If data ends, exit at the last close.
- exit_ts: bar open time for exits at an open; bar CLOSE time (open+5min) for intrabar stop/target
  (the latest possible time, since the true intrabar time is unknown); previous bar close time for rule 5.

### 3.6 No-trade conditions (all causal: only data up to the decision time)
Skip the whole day when any holds:
- US equity half-day (calendar rule: day after US Thanksgiving; Dec 24 on a weekday; Jul 3 on Mon-Thu).
  No other holiday calendar is used; abnormal holiday sessions are caught by the completeness checks.
- Data gap today: any missing 5-min bar between 09:30 and the decision bar (checked incrementally; a gap
  later in the day is unknowable at decision time and is handled by exit rule 5 instead).
- Contract roll day (the contract seen in today's ON/RTH bars differs from the previous RTH session's, or more
  than one contract appears today) and the day AFTER a roll day.
- Previous session incomplete or stale, or ON invalid, when the active level set needs them (3.1).
- Day 1 of the data (no previous session).
Skip the trade (consumes the day) when the stop distance is < 2.0 points or > 12.0 points, measured at
decision (`D_sig`) and again at fill (`D`).

### 3.7 Position size
- Baseline: FIXED 1 contract. Specified (not used in baseline) risk rule: `qty = clamp(floor(
  risk_budget_usd / (D * point_value)), 1, max_qty)`. Only if a later phase enables it; it would be a new variant.
- Max 1 trade per ET day, max 1 open position. Enforced by both strategy and engine.

## 4. Fill-model rules (engine)
- Fills only at the next bar open (market), resting stop/limit orders against bar high/low.
- Stop and target both in one bar => stop first. Gap through stop => open price (worse), plus extra slippage.
- Stop fills are market orders after trigger: pay half spread + slippage + stop extra slippage.
- Limit (target) fills pay no spread/slippage but pay commission/fees.
- Costs are not baked into prices: `entry_px`/`exit_px` are nominal, `pnl_gross` uses them, `costs` is a
  positive USD amount, `pnl_net = pnl_gross - costs`, `risk_usd = qty*D*point_value` (before costs),
  `r_multiple = pnl_net/risk_usd`.
- Known optimism/pessimism not modelled: queue position, partial fills, market impact (negligible at 1 lot),
  intrabar path order beyond "stop first", exchange halts, bar-aggregation differences between data vendors.

## 5. Cost model (assumptions; see `configs/costs_mes.yaml`)
Per contract: commission + exchange/regulatory fee per side (2 sides per round trip, always, also for
limit exits); each MARKET fill (entry, time/eod exit, stop exit) pays `spread_ticks/2 + slippage_ticks_per_side`
ticks, stop exits additionally `stop_extra_slippage_ticks`; limit exits pay no spread/slippage.
`cost_multiplier` scales every component. Stress scenarios: x1, x1.5, x2.

## 6. Baselines that H1 must beat
- B0 random entry (null for "any entry in this window with this stop/target geometry"):
  same eligible days as H1 (`levels=both` eligibility), same window, same target_r, same time exit and
  costs. Per day: one random signal bar uniform over the 17 eligible bars 09:35..10:55, direction +/-1 with
  p=0.5, stop distance D_pts drawn from the empirical H1 stop-distance distribution of the same dataset
  (supplied explicitly; default uniform over ticks in [2,12] points). Stop = signal close -/+ D_pts.
  Randomness: `default_rng([seed, rep, date_ordinal])` — depends only on the date, never on future data.
  >= 200 replications; compare H1's mean R to the replication distribution.
- B1 opening-range breakout: OR = high/low of bars 09:30..09:55 (6 bars, all present). From 10:00 to
  10:55 (signal bar open), the first bar CLOSING beyond OR high (long) / OR low (short) is the signal.
  Stop = opposite OR extreme -/+ 1 tick, target = target_r*D, same risk filter 2-12 points, time exit
  11:30, same costs, max 1 trade/day, same day-skip rules (it needs no prior-session LEVELS, but a previous session must still exist for the roll check).
  Note B1's eligible day set differs slightly from H1's; comparisons restrict to common dates.
- Both baselines are benchmarks, not tuned: only target_r varies for B1.

## 7. Metrics and parameter grid
Metrics (computed from the trades DataFrame; the validation module owns inference):
net EV per trade in USD and in R; profit factor (sum net wins / |sum net losses|); max drawdown (USD,
closed-trade cumulative net P&L by exit time, peak to trough); exposure (sum bars_held*5min divided by
trading days*390min, plus fraction of days traded); trade count; plus exit_reason mix and skip/cancel
diagnostics. Report at cost x1, x1.5, x2.

Grid for LATER optimisation (not run now; every other parameter FIXED):
| param | values |
|---|---|
| H1 `target_r` | 1.5, 2.0, 3.0 |
| H1 `levels` | prev_rth, overnight, both |
| B1 `target_r` | 1.5, 2.0, 3.0 |
| B0 | 1 variant (replications are not variants) |
Total = 9 + 3 + 1 = 13 variants <= `max_variants_total` 24 (11 held in reserve). Max 3 values per
parameter respected. Baseline H1 = (`target_r`=2.0, `levels`=both).
Not in grid, FIXED: `min_sweep_ticks`=1, `stop_buffer_ticks`=1, window, time exit 11:30, risk filter 2-12.

## 8. Look-ahead protection
Strategies are streaming objects: `on_bar` receives one completed bar at a time and holds only state
derived from past bars. `tests/test_no_lookahead.py` checks that signals and closed trades up to time t are
identical when bars after t are truncated or replaced by random garbage.
