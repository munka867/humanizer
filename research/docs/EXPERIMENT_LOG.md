# Experiment Log
Every variant is registered here BEFORE its results are read. Failed candidates stay.
| id | date | description | params | data period | status | result summary (filled after) |
|----|------|-------------|--------|-------------|--------|-------------------------------|
| H1_sweep_reversal_v1@levels=prev_rth,target_r=1.5 | 2026-10-06 | H1 prior-extreme sweep reversal (spec v1) | target_r=1.5, levels=prev_rth | NOT RUN (no real data) | registered | - |
| H1_sweep_reversal_v1@levels=overnight,target_r=1.5 | 2026-10-06 | H1 prior-extreme sweep reversal (spec v1) | target_r=1.5, levels=overnight | NOT RUN (no real data) | registered | - |
| H1_sweep_reversal_v1@levels=both,target_r=1.5 | 2026-10-06 | H1 prior-extreme sweep reversal (spec v1) | target_r=1.5, levels=both | NOT RUN (no real data) | registered | - |
| H1_sweep_reversal_v1@levels=prev_rth,target_r=2.0 | 2026-10-06 | H1 prior-extreme sweep reversal (spec v1) | target_r=2.0, levels=prev_rth | NOT RUN (no real data) | registered | - |
| H1_sweep_reversal_v1@levels=overnight,target_r=2.0 | 2026-10-06 | H1 prior-extreme sweep reversal (spec v1) | target_r=2.0, levels=overnight | NOT RUN (no real data) | registered | - |
| H1_sweep_reversal_v1@levels=both,target_r=2.0 | 2026-10-06 | H1 prior-extreme sweep reversal (spec v1) | target_r=2.0, levels=both | NOT RUN (no real data) | registered | - |
| H1_sweep_reversal_v1@levels=prev_rth,target_r=3.0 | 2026-10-06 | H1 prior-extreme sweep reversal (spec v1) | target_r=3.0, levels=prev_rth | NOT RUN (no real data) | registered | - |
| H1_sweep_reversal_v1@levels=overnight,target_r=3.0 | 2026-10-06 | H1 prior-extreme sweep reversal (spec v1) | target_r=3.0, levels=overnight | NOT RUN (no real data) | registered | - |
| H1_sweep_reversal_v1@levels=both,target_r=3.0 | 2026-10-06 | H1 prior-extreme sweep reversal (spec v1) | target_r=3.0, levels=both | NOT RUN (no real data) | registered | - |
| B1_orb30_v1@target_r=1.5 | 2026-10-06 | 30-min ORB baseline | target_r=1.5 | NOT RUN (no real data) | registered | - |
| B1_orb30_v1@target_r=2.0 | 2026-10-06 | 30-min ORB baseline | target_r=2.0 | NOT RUN (no real data) | registered | - |
| B1_orb30_v1@target_r=3.0 | 2026-10-06 | 30-min ORB baseline | target_r=3.0 | NOT RUN (no real data) | registered | - |
| B0_random_entry_v1 | 2026-10-06 | random-entry baseline, 200 reps (reps are not variants) | target_r=2.0, seed 20261006 | NOT RUN (no real data) | registered | - |

## Change notes
- 2026-10-06 (before any real data): spec v1.1 after independent review (docs/REVIEW.md). Rule changes: all holiday-candidate dates and the day after are skipped (conservative; some normal sessions are lost); B0 is matched to H1 signal days/times/stop distances; variant ids fully qualified. The 13 registered variants are unchanged and still NOT RUN. Budget used: 13 of 24.
- 2026-10-06: H2 (TTFM mechanical approximation) registered in docs/HYPOTHESES_TTRADES.md as NOT RUN, no variants counted yet. IBKR connector data judged insufficient (docs/DATA_FINDINGS.md); no backtest executed.
| D3-V1 | 2026-10-08 | H3 daily C2 closure, both sides (DAILY_SPEC v1) | none | SPY,QQQ,IWM 2022-10..2026-10 | REGISTERED, NOT RUN | |
| D3-V2 | 2026-10-08 | H3 long-only | none | same | REGISTERED, NOT RUN | |
| D3-V3 | 2026-10-08 | H3 short-only | none | same | REGISTERED, NOT RUN | |
| D3-B0 | 2026-10-08 | baselines B_D0 buy&hold O->C, B_D1 matched random-day permutation | seed 20261008 | same | REGISTERED, NOT RUN | |
Variants registered so far: 13 intraday + 3 daily = 16 of 24 budget.
| D3-V1 result | 2026-10-08 | train+validation run (DAILY_RESULTS.md), test sealed | none | train 2022-10-12..2025-03-04, val ..2025-12-20 | RUN (train, validation) | train EV -$5.32/trade n=471; val EV +$1.91 n=152 CI [-26.6,29.5]; verdict REJECTED (train EV<=0) |
| D3-V2 result | 2026-10-08 | same | none | same | RUN (train, validation) | train EV -$18.43 n=236; val -$4.76 n=76; verdict REJECTED |
| D3-V3 result | 2026-10-08 | same | none | same | RUN (train, validation) | train +$7.84 n=235 (B_D1 p=0.022); val +$8.57 n=76 CI [-43.4,53.5], B_D1 p=0.082; verdict INCONCLUSIVE |
| D3-B0 result | 2026-10-08 | baselines | seed 20261008 | same | RUN (train, validation) | B_D0 pooled EV -$1.51 (train), +$4.86 (val) per $10k trade; B_D1 in DAILY_RESULTS.md |
