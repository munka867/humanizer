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
