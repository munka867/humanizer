# CLAUDE.md — tradelab research project

## Mission
Test whether ONE precisely defined intraday futures strategy (MES/NQ family, IBKR Canada) has positive
net expected value after realistic costs. "No credible edge" is an acceptable, expected-possible result.
Do not optimise for an impressive backtest.

## Hard rules
- RESEARCH + PAPER ONLY. Never add live credentials, never place real orders, never import broker order APIs
  with live endpoints. Any future execution code must enforce risk limits in code (max loss/day, max
  position, kill switch) with tests — not in prompts.
- Never invent data, trades, results, citations or profitability. Synthetic data lives in `data/fixtures/`,
  must be labelled `SYNTHETIC` in filename and metadata, and is used ONLY to test software.
- YouTube channels (@deltatrendtrading, @TTrades_edu) are hypothesis sources. Transcripts are read via the
  Firecrawl youtube/read capability (no charts/visuals). Record exactly what was accessed in docs/SOURCES.md.
- IBKR (user authorisation, updated 2026-10-15): the dashboard may take LIVE/delayed market data and READ-ONLY account data from the user's own
  TWS / IB Gateway through command_center/ibkr (read-only wrapper). Default ports are PAPER (TWS 7497, Gateway 4002); live ports (7496, 4001) are
  refused unless an explicit env var is set, and are still read-only. The data adapter must not expose order/cancel/modify calls (static + runtime tests).
  The Claude-session IBKR connector remains read-only (never create_order_instruction, alerts or watchlist writes). Order logic lives only in
  tradelab/execution against a SIMULATED broker. No real or paper order path is wired. Enabling PAPER requires ALL of: an approved strategy state, an approved
  risk policy, the user's signed approval record (HMAC with a secret the agents do not hold), and a paper-account identity check. LIVE is NOT allowed.
  Keep account data out of git and out of public docs (the repository is PUBLIC).
- Only information available at the decision time may be used (no look-ahead). Conservative intrabar fills:
  if stop and target are both touched in one bar, assume the STOP filled first.
- Chronological train / validation / final-test split. Purge so trades cannot straddle boundaries.
  The final test set is evaluated at most ONCE per frozen strategy; every access is logged in
  results/test_access_log.jsonl. If it is touched twice it is no longer "untouched" — say so.
- Every variant/parameter tried goes in docs/EXPERIMENT_LOG.md (including failures) BEFORE results are read.
  Search budget in configs/search_budget.yaml; account for multiple testing.
- No ML until a baseline is trusted; then report incremental out-of-sample value only.
- Monte Carlo resamples the same evidence; it adds no independent market evidence. Say so in reports.

## Layout & ownership
- `src/tradelab/contracts.py` — shared formats (coordinator owns).
- `src/tradelab/data/` — loaders, quality checks, synthetic fixtures.
- `src/tradelab/backtest/` — strategy, order/fill sim, costs.
- `src/tradelab/validation/` — splits, purging, stats, Monte Carlo.
- `docs/` — STRATEGY_SPEC, DATA_QUALITY, EXPERIMENT_LOG, REVIEW, SOURCES, ASSESSMENT.
- `data/raw/` — user-supplied real CSVs (git-ignored contents except where user commits them).

## Commands
- Install: `pip install -r requirements.txt`
- Tests: `cd research && python -m pytest -q`
- Seeds: every stochastic function takes an explicit `seed`; configs store seeds.

## Style
Small modules, typed, deterministic. Test critical calculations: timing, fills, costs, risk controls.
Update PROGRESS.md after each milestone.
