## Segment: train  (2022-10-11 .. <2025-03-04)

### D4_V1_sma200  (397 days 2023-08-02..2025-03-03)

| scope | total ret | B_H total | MDD | B_H MDD | DD reduction | vol ann | B_H vol | time in mkt | buys | sells (round trips) | mean daily diff vs B_H (bps) | 95% CI blk10 | blk5 | blk20 | Bonf p (N=19) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SPY | 22.23% | 28.78% | 9.30% | 9.41% | 0.0107 | 12.39% | 12.60% | 0.9798 | 2 | 2 | -1.33 | [-3.69, 0.09] | [-3.72, 0.14] | [-3.66, 0.03] | 1.0000 |
| QQQ | 31.04% | 31.04% | 13.56% | 13.56% | 0.0000 | 17.66% | 17.66% | 1.0000 | 1 | 0 | 0.00 | [0.00, 0.00] | [0.00, 0.00] | [0.00, 0.00] | 1.0000 |
| IWM | 10.44% | 6.75% | 10.55% | 17.06% | 0.3816 | 18.43% | 20.43% | 0.8489 | 3 | 3 | 0.70 | [-5.19, 5.05] | [-5.05, 5.40] | [-6.00, 4.95] | 1.0000 |
| pooled | 21.50% | 22.23% | 10.21% | 11.85% | 0.1384 | 14.46% | 15.42% | 0.9429 | n/a | n/a | -0.21 | [-2.58, 1.51] | [-2.64, 1.60] | [-2.82, 1.44] | 1.0000 |

Cost stress (pooled):

| cost | total ret | B_H total | mean daily diff (bps) | CI blk10 (bps) |
|---|---|---|---|---|
| x1.0 | 21.50% | 22.23% | -0.21 | [-2.58, 1.51] |
| x1.5 | 21.47% | 22.22% | -0.21 | [-2.59, 1.50] |
| x2.0 | 21.43% | 22.21% | -0.22 | [-2.60, 1.49] |

B_R random-timing null (10,000 draws, seed 20261014; 'pct' = share of null draws the strategy beats; >=0.95 flags beating the 95th percentile; does not change the label):

| scope | total: obs / null median / null p95 / pct | MDD: obs / null median / null p05 / pct (better=lower) | return/vol pct |
|---|---|---|---|
| SPY | 22.23% / 25.39% / 30.10% / 0.043 | 9.30% / 10.03% / 8.41% / 0.549 | 0.0430 |
| QQQ | always in/out of market: no shuffle possible | | |
| IWM | 10.44% / 19.83% / 27.54% / 0.099 | 10.55% / 15.01% / 10.55% / 0.957 | 0.0985 |
| pooled | 21.50% / 26.30% / 29.56% / 0.041 | 10.21% / 11.08% / 10.21% / 0.949 | 0.0413 |

Monte Carlo day-block bootstrap of daily returns (5000 paths, horizon = segment length, seed 20261014). Resamples the same evidence; adds no independent market evidence:

| series | block | total ret p05/p50/p95 | max DD p05/p50/p95 | P(MDD>15%) | P(MDD>25%) |
|---|---|---|---|---|---|
| pooled | 10 | -4.03% / 27.03% / 68.95% | 7.25% / 11.91% / 21.54% | 0.254 | 0.022 |
| pooled | 5 | -8.23% / 23.18% / 66.96% | 7.57% / 12.95% / 23.64% | 0.341 | 0.035 |
| pooled | 20 | 4.23% / 31.64% / 63.56% | 6.82% / 10.67% / 17.91% | 0.123 | 0.002 |
| pooled B_H | 10 | -5.39% / 28.89% / 76.28% | 7.78% / 12.96% / 23.76% | 0.334 | 0.037 |
| SPY | 10 | -1.01% / 28.29% / 64.19% | 6.07% / 10.11% / 18.73% | 0.137 | 0.007 |
| QQQ | 10 | -1.11% / 40.82% / 99.10% | 8.40% / 13.97% / 25.30% | 0.412 | 0.054 |
| IWM | 10 | -21.46% / 11.87% / 62.54% | 10.39% / 18.34% / 33.08% | 0.708 | 0.209 |

### D4_V2_cross  (397 days 2023-08-02..2025-03-03)

| scope | total ret | B_H total | MDD | B_H MDD | DD reduction | vol ann | B_H vol | time in mkt | buys | sells (round trips) | mean daily diff vs B_H (bps) | 95% CI blk10 | blk5 | blk20 | Bonf p (N=19) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SPY | 27.89% | 28.78% | 10.03% | 9.41% | -0.0667 | 12.64% | 12.60% | 1.0000 | 0 | 0 | -0.17 | [-0.17, 0.00] | [-0.17, 0.00] | [-0.17, 0.00] | 1.0000 |
| QQQ | 31.04% | 31.04% | 13.56% | 13.56% | 0.0000 | 17.66% | 17.66% | 1.0000 | 1 | 0 | 0.00 | [0.00, 0.00] | [0.00, 0.00] | [0.00, 0.00] | 1.0000 |
| IWM | -7.15% | 6.75% | 15.73% | 17.06% | 0.0777 | 18.25% | 20.43% | 0.8640 | 2 | 1 | -3.68 | [-10.03, 1.84] | [-9.75, 1.96] | [-11.13, 1.52] | 1.0000 |
| pooled | 16.37% | 22.23% | 10.38% | 11.85% | 0.1243 | 14.65% | 15.42% | 0.9547 | n/a | n/a | -1.28 | [-3.37, 0.61] | [-3.26, 0.64] | [-3.72, 0.50] | 1.0000 |

Cost stress (pooled):

| cost | total ret | B_H total | mean daily diff (bps) | CI blk10 (bps) |
|---|---|---|---|---|
| x1.0 | 16.37% | 22.23% | -1.28 | [-3.37, 0.61] |
| x1.5 | 16.36% | 22.22% | -1.29 | [-3.37, 0.61] |
| x2.0 | 16.34% | 22.21% | -1.29 | [-3.37, 0.60] |

B_R random-timing null (10,000 draws, seed 20261014; 'pct' = share of null draws the strategy beats; >=0.95 flags beating the 95th percentile; does not change the label):

| scope | total: obs / null median / null p95 / pct | MDD: obs / null median / null p05 / pct (better=lower) | return/vol pct |
|---|---|---|---|
| SPY | always in/out of market: no shuffle possible | | |
| QQQ | always in/out of market: no shuffle possible | | |
| IWM | -7.15% / -7.15% / 0.63% / 0.253 | 15.73% / 15.73% / 15.73% / 0.747 | 0.2526 |
| pooled | 16.37% / 16.37% / 19.56% / 0.253 | 10.38% / 10.38% / 10.38% / 0.747 | 0.2526 |

Monte Carlo day-block bootstrap of daily returns (5000 paths, horizon = segment length, seed 20261014). Resamples the same evidence; adds no independent market evidence:

| series | block | total ret p05/p50/p95 | max DD p05/p50/p95 | P(MDD>15%) | P(MDD>25%) |
|---|---|---|---|---|---|
| pooled | 10 | -7.45% / 23.25% / 63.99% | 7.66% / 12.59% / 23.15% | 0.316 | 0.034 |
| pooled | 5 | -11.47% / 18.99% / 60.92% | 7.89% / 13.81% / 25.33% | 0.409 | 0.052 |
| pooled | 20 | 0.38% / 27.58% / 59.69% | 7.25% / 11.30% / 19.30% | 0.177 | 0.006 |
| pooled B_H | 10 | -5.39% / 28.89% / 76.28% | 7.78% / 12.96% / 23.76% | 0.334 | 0.037 |
| SPY | 10 | 3.44% / 34.07% / 73.25% | 6.07% / 9.80% / 17.98% | 0.114 | 0.003 |
| QQQ | 10 | -1.11% / 40.82% / 99.10% | 8.40% / 13.97% / 25.30% | 0.412 | 0.054 |
| IWM | 10 | -31.22% / -2.55% / 40.95% | 11.78% / 22.02% / 38.80% | 0.828 | 0.376 |

### D4_V3_donchian  (496 days 2023-03-10..2025-03-03)

| scope | total ret | B_H total | MDD | B_H MDD | DD reduction | vol ann | B_H vol | time in mkt | buys | sells (round trips) | mean daily diff vs B_H (bps) | 95% CI blk10 | blk5 | blk20 | Bonf p (N=19) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SPY | 20.49% | 49.28% | 8.41% | 10.29% | 0.1832 | 10.32% | 12.58% | 0.7359 | 3 | 2 | -4.42 | [-9.09, -0.49] | [-8.93, -0.62] | [-8.71, -0.64] | 0.5054 |
| QQQ | 15.25% | 69.71% | 17.56% | 13.56% | -0.2950 | 14.65% | 17.55% | 0.7137 | 4 | 4 | -7.99 | [-14.52, -1.94] | [-14.42, -2.33] | [-13.59, -2.56] | 0.1482 |
| IWM | -8.87% | 15.74% | 16.48% | 18.37% | 0.1025 | 14.73% | 20.28% | 0.5121 | 3 | 3 | -5.20 | [-14.38, 0.24] | [-13.96, 1.00] | [-14.91, 0.02] | 1.0000 |
| pooled | 8.73% | 43.96% | 10.95% | 12.93% | 0.1535 | 11.24% | 15.28% | 0.6539 | n/a | n/a | -5.87 | [-11.17, -1.98] | [-11.08, -1.87] | [-10.89, -2.32] | 0.1102 |

Cost stress (pooled):

| cost | total ret | B_H total | mean daily diff (bps) | CI blk10 (bps) |
|---|---|---|---|---|
| x1.0 | 8.73% | 43.96% | -5.87 | [-11.17, -1.98] |
| x1.5 | 8.68% | 43.95% | -5.88 | [-11.18, -1.99] |
| x2.0 | 8.63% | 43.94% | -5.89 | [-11.19, -2.00] |

B_R random-timing null (10,000 draws, seed 20261014; 'pct' = share of null draws the strategy beats; >=0.95 flags beating the 95th percentile; does not change the label):

| scope | total: obs / null median / null p95 / pct | MDD: obs / null median / null p05 / pct (better=lower) | return/vol pct |
|---|---|---|---|
| SPY | 20.49% / 23.11% / 42.45% / 0.374 | 8.41% / 9.88% / 5.35% / 0.838 | 0.4036 |
| QQQ | 15.25% / 52.21% / 81.97% / 0.008 | 17.56% / 13.56% / 7.42% / 0.015 | 0.0086 |
| IWM | -8.87% / 13.72% / 42.81% / 0.010 | 16.48% / 14.35% / 10.07% / 0.456 | 0.0098 |
| pooled | 8.73% / 29.87% / 43.85% / 0.000 | 10.95% / 10.21% / 7.04% / 0.289 | 0.0003 |

Monte Carlo day-block bootstrap of daily returns (5000 paths, horizon = segment length, seed 20261014). Resamples the same evidence; adds no independent market evidence:

| series | block | total ret p05/p50/p95 | max DD p05/p50/p95 | P(MDD>15%) | P(MDD>25%) |
|---|---|---|---|---|---|
| pooled | 10 | -14.35% / 11.28% / 41.76% | 6.68% / 12.48% / 24.51% | 0.337 | 0.044 |
| pooled | 5 | -16.91% / 9.60% / 42.50% | 7.13% / 13.36% / 26.05% | 0.386 | 0.064 |
| pooled | 20 | -12.83% / 12.42% / 40.97% | 6.39% / 12.31% / 23.89% | 0.319 | 0.040 |
| pooled B_H | 10 | 7.87% / 50.69% / 109.46% | 7.78% / 12.18% / 21.15% | 0.273 | 0.014 |
| SPY | 10 | -2.08% / 23.81% / 55.64% | 5.58% / 9.40% / 17.70% | 0.115 | 0.004 |
| QQQ | 10 | -15.12% / 19.90% / 66.11% | 8.25% / 15.39% / 29.39% | 0.525 | 0.120 |
| IWM | 10 | -33.70% / -8.99% / 25.34% | 11.43% / 22.63% / 39.75% | 0.830 | 0.408 |

