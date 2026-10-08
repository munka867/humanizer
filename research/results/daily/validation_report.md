## Segment: validation  (candidate trade-days in union: 200; eligible per symbol: {'SPY': 200, 'QQQ': 200, 'IWM': 200})

### Performance (net of costs x1)

| variant | scope | n | EV $/trade | EV R | EV bps | hit | avg win $ | avg loss $ | PF | maxDD $ | days in mkt | total $ | day-block 95% CI EV $ | 95% CI EV R |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| D3_V1_c2_both | pooled-by-date | 152 | 1.91 | 0.009 | 1.91 | 0.539 | 83.02 | -93.11 | 1.04 | 2,039.72 | 0.475 | 289.60 | [-26.60, 29.52] | [-0.151, 0.176] |
| D3_V1_c2_both | SPY | 49 | -15.87 | -0.021 | -15.87 | 0.469 | 68.36 | -90.39 | 0.67 | 1,379.12 | 0.245 | -777.86 | [-71.98, 23.63] | [-0.260, 0.207] |
| D3_V1_c2_both | QQQ | 46 | 4.86 | -0.040 | 4.86 | 0.522 | 88.65 | -86.55 | 1.12 | 687.04 | 0.230 | 223.58 | [-27.38, 39.42] | [-0.271, 0.183] |
| D3_V1_c2_both | IWM | 57 | 14.80 | 0.076 | 14.80 | 0.614 | 88.79 | -102.90 | 1.37 | 985.53 | 0.285 | 843.88 | [-16.30, 46.67] | [-0.117, 0.276] |
| D3_V2_c2_long | pooled-by-date | 76 | -4.76 | -0.059 | -4.76 | 0.553 | 61.45 | -86.54 | 0.88 | 1,047.10 | 0.245 | -361.66 | [-31.09, 20.72] | [-0.257, 0.125] |
| D3_V2_c2_long | SPY | 20 | 0.05 | -0.028 | 0.05 | 0.550 | 54.81 | -66.89 | 1.00 | 213.41 | 0.100 | 0.93 | [-29.41, 28.84] | [-0.319, 0.240] |
| D3_V2_c2_long | QQQ | 26 | -18.16 | -0.173 | -18.16 | 0.538 | 63.88 | -113.87 | 0.65 | 581.99 | 0.130 | -472.14 | [-59.79, 20.77] | [-0.495, 0.120] |
| D3_V2_c2_long | IWM | 30 | 3.65 | 0.018 | 3.65 | 0.567 | 63.74 | -74.92 | 1.11 | 373.02 | 0.150 | 109.55 | [-29.15, 34.39] | [-0.178, 0.199] |
| D3_V3_c2_short | pooled-by-date | 76 | 8.57 | 0.078 | 8.57 | 0.526 | 105.67 | -99.32 | 1.18 | 2,208.02 | 0.260 | 651.25 | [-43.43, 53.51] | [-0.184, 0.342] |
| D3_V3_c2_short | SPY | 29 | -26.85 | -0.017 | -26.85 | 0.414 | 80.78 | -102.83 | 0.55 | 1,361.03 | 0.145 | -778.79 | [-121.16, 35.31] | [-0.374, 0.305] |
| D3_V3_c2_short | QQQ | 20 | 34.79 | 0.132 | 34.79 | 0.500 | 123.33 | -53.76 | 2.29 | 399.46 | 0.100 | 695.72 | [-15.66, 92.15] | [-0.159, 0.460] |
| D3_V3_c2_short | IWM | 27 | 27.20 | 0.140 | 27.20 | 0.667 | 112.45 | -143.31 | 1.57 | 857.51 | 0.135 | 734.32 | [-27.94, 82.02] | [-0.203, 0.492] |

### Cost stress (pooled; EV $/trade with day-block 95% CI)

| variant | cost x | n | EV $ | CI lo | CI hi | hit |
|---|---|---|---|---|---|---|
| D3_V1_c2_both | 1.0 | 152 | 1.91 | -26.60 | 29.52 | 0.539 |
| D3_V1_c2_both | 1.5 | 152 | 0.67 | -27.83 | 28.28 | 0.533 |
| D3_V1_c2_both | 2.0 | 152 | -0.57 | -29.06 | 27.04 | 0.533 |
| D3_V2_c2_long | 1.0 | 76 | -4.76 | -31.09 | 20.72 | 0.553 |
| D3_V2_c2_long | 1.5 | 76 | -6.00 | -32.33 | 19.47 | 0.553 |
| D3_V2_c2_long | 2.0 | 76 | -7.24 | -33.57 | 18.22 | 0.553 |
| D3_V3_c2_short | 1.0 | 76 | 8.57 | -43.43 | 53.51 | 0.526 |
| D3_V3_c2_short | 1.5 | 76 | 7.34 | -44.67 | 52.28 | 0.513 |
| D3_V3_c2_short | 2.0 | 76 | 6.10 | -45.90 | 51.05 | 0.513 |

### Baselines

B_D0 = every eligible day long open->close (same sizing/costs):

| scope | n days | EV $ | EV bps | EV R |
|---|---|---|---|---|
| pooled | 600 | 4.86 | 4.86 | -0.003 |
| SPY | 200 | 3.69 | 3.69 | -0.010 |
| QQQ | 200 | 3.76 | 3.76 | -0.016 |
| IWM | 200 | 7.12 | 7.12 | 0.018 |

B_D1 = matched random-day permutation (10,000 draws, per-instrument counts and long/short split matched). p = one-sided P(null >= observed), +1 smoothed.

| variant | metric | observed | null mean | null p05 | null p95 | p (one-sided) | percentile |
|---|---|---|---|---|---|---|---|
| D3_V1_c2_both | EV $ | 1.905 | -2.349 | -19.290 | 14.454 | 0.3384 | 0.662 |
| D3_V1_c2_both | EV bps | 1.905 | -2.349 | -19.290 | 14.454 | 0.3384 | 0.662 |
| D3_V1_c2_both | EV R | 0.009 | -0.017 | -0.104 | 0.070 | 0.3119 | 0.688 |
| D3_V2_c2_long | EV $ | -4.759 | 5.018 | -16.664 | 28.433 | 0.7551 | 0.245 |
| D3_V2_c2_long | EV bps | -4.759 | 5.018 | -16.664 | 28.433 | 0.7551 | 0.245 |
| D3_V2_c2_long | EV R | -0.059 | -0.001 | -0.117 | 0.115 | 0.7969 | 0.203 |
| D3_V3_c2_short | EV $ | 8.569 | -9.900 | -33.464 | 11.524 | 0.0817 | 0.918 |
| D3_V3_c2_short | EV bps | 8.569 | -9.900 | -33.464 | 11.524 | 0.0817 | 0.918 |
| D3_V3_c2_short | EV R | 0.078 | -0.035 | -0.151 | 0.083 | 0.0589 | 0.941 |

### Long vs short split (pooled)

| variant | side | n | EV $ | EV bps | EV R | hit |
|---|---|---|---|---|---|---|
| D3_V1_c2_both | long | 76 | -4.76 | -4.76 | -0.059 | 0.553 |
| D3_V1_c2_both | short | 76 | 8.57 | 8.57 | 0.078 | 0.526 |
| D3_V2_c2_long | long | 76 | -4.76 | -4.76 | -0.059 | 0.553 |
| D3_V2_c2_long | short | 0 | n/a | n/a | n/a | n/a |
| D3_V3_c2_short | long | 0 | n/a | n/a | n/a | n/a |
| D3_V3_c2_short | short | 76 | 8.57 | 8.57 | 0.078 | 0.526 |

### Per-quarter (pooled, by NY entry date)

| variant | quarter | n | net sum $ | mean $ | hit |
|---|---|---|---|---|---|
| D3_V1_c2_both | 2025Q1 | 14 | 167.55 | 11.97 | 0.571 |
| D3_V1_c2_both | 2025Q2 | 42 | -332.07 | -7.91 | 0.690 |
| D3_V1_c2_both | 2025Q3 | 50 | -1,493.52 | -29.87 | 0.360 |
| D3_V1_c2_both | 2025Q4 | 46 | 1,947.63 | 42.34 | 0.587 |
| D3_V2_c2_long | 2025Q1 | 10 | -386.90 | -38.69 | 0.400 |
| D3_V2_c2_long | 2025Q2 | 21 | 868.10 | 41.34 | 0.857 |
| D3_V2_c2_long | 2025Q3 | 23 | -537.84 | -23.38 | 0.478 |
| D3_V2_c2_long | 2025Q4 | 22 | -305.02 | -13.86 | 0.409 |
| D3_V3_c2_short | 2025Q1 | 4 | 554.45 | 138.61 | 1.000 |
| D3_V3_c2_short | 2025Q2 | 21 | -1,200.17 | -57.15 | 0.524 |
| D3_V3_c2_short | 2025Q3 | 27 | -955.68 | -35.40 | 0.259 |
| D3_V3_c2_short | 2025Q4 | 24 | 2,252.66 | 93.86 | 0.750 |

### Concentration, inference, Monte Carlo (pooled)

Top-5 share = (sum of 5 best trades)/(total net PnL); meaningless if total <= 0. p_day = day-clustered t-test; Bonferroni N=16; DSR assumptions in the notes.

| variant | top-5 share of PnL | longest losing streak | p_day 1-sided | p_day 2-sided | Bonferroni p (N=16) | DSR |
|---|---|---|---|---|---|---|
| D3_V1_c2_both | 5.49 | 12 | 0.447 | 0.893 | 1.000 | 0.048 |
| D3_V2_c2_long | n/a | 6 | 0.635 | 0.730 | 1.000 | 0.015 |
| D3_V3_c2_short | 2.44 | 13 | 0.368 | 0.735 | 1.000 | 0.073 |

Monte Carlo (day_block, 5000 paths, seed 20261008). RESAMPLES THE SAME EVIDENCE; adds no independent market evidence.

| variant | statistic | mean | p05 | p50 | p95 |
|---|---|---|---|---|---|
| D3_V1_c2_both | EV $/trade | 1.82 | -22.03 | 1.88 | 25.03 |
| D3_V1_c2_both | total PnL $ | 293.10 | -3,279.37 | 285.47 | 3,857.18 |
| D3_V1_c2_both | max DD $ | 2,172.92 | 809.46 | 1,931.20 | 4,245.24 |
| D3_V1_c2_both | longest losing streak | 7.96 | 5.00 | 8.00 | 13.00 |
| D3_V2_c2_long | EV $/trade | -4.62 | -26.88 | -4.39 | 16.87 |
| D3_V2_c2_long | total PnL $ | -356.09 | -2,079.95 | -332.23 | 1,256.39 |
| D3_V2_c2_long | max DD $ | 1,273.25 | 506.12 | 1,157.51 | 2,428.07 |
| D3_V2_c2_long | longest losing streak | 7.05 | 4.00 | 7.00 | 12.00 |
| D3_V3_c2_short | EV $/trade | 7.35 | -33.74 | 8.21 | 46.46 |
| D3_V3_c2_short | total PnL $ | 594.43 | -2,508.58 | 613.48 | 3,652.62 |
| D3_V3_c2_short | max DD $ | 1,673.68 | 484.92 | 1,487.81 | 3,484.10 |
| D3_V3_c2_short | longest losing streak | 6.82 | 4.00 | 6.00 | 11.00 |

### Development verdict (validation.verdict.classify_development, pooled-by-date)

**D3_V1_c2_both: REJECTED**

Inputs: `{"n_train": 471.0, "n_val": 152.0, "n_val_days": 95.0, "ev_train_1x": -5.32239, "ev_val_1x": 1.90526, "ev_val_2x": -0.57072, "val_ci_lo_at_pass_mult": -27.82744, "val_ci_hi_1x": 29.5232, "p_bonferroni_val": 1.0, "dsr": 0.04809, "positive_quarter_fraction": 0.5, "max_period_share": 4.64623, "baseline_percentile": 0.6617}`

- train net EV/trade <= 0 at 1.0x costs

**D3_V2_c2_long: REJECTED**

Inputs: `{"n_train": 236.0, "n_val": 76.0, "n_val_days": 49.0, "ev_train_1x": -18.42535, "ev_val_1x": -4.75862, "ev_val_2x": -7.24342, "val_ci_lo_at_pass_mult": -32.33359, "val_ci_hi_1x": 20.72329, "p_bonferroni_val": 1.0, "dsr": 0.01455, "positive_quarter_fraction": 0.25, "max_period_share": null, "baseline_percentile": 0.2449}`

- train net EV/trade <= 0 at 1.0x costs
- validation net EV/trade <= 0 at 1.0x costs

**D3_V3_c2_short: INCONCLUSIVE**

Inputs: `{"n_train": 235.0, "n_val": 76.0, "n_val_days": 52.0, "ev_train_1x": 7.83632, "ev_val_1x": 8.56913, "ev_val_2x": 6.10199, "val_ci_lo_at_pass_mult": -44.66671, "val_ci_hi_1x": 53.51242, "p_bonferroni_val": 1.0, "dsr": 0.07327, "positive_quarter_fraction": 0.33333, "max_period_share": 2.54114, "baseline_percentile": 0.9184}`

- not met: n_val >= 100
- not met: validation CI lower bound > 0 at 1.5x costs
- not met: Bonferroni p <= 0.1
- not met: DSR >= 0.9
- not met: positive quarters >= 60%
- not met: max single-period P&L share <= 50%
- not met: beats random baseline at p95%

