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
- IBKR (user authorisation, updated 2026-10-15, narrowed later the same day): the ONLY IBKR account this project may use is the user's PAPER trading account
  (TWS paper login; account ids start with "DU"). No other IBKR account (the user's live/TFSA account) may be read or traded: the Claude-session IBKR connector is
  linked to that other account and is NO LONGER USED for anything. Data and orders go through the user's own TWS / IB Gateway on loopback, PAPER ports only
  (TWS 7497, Gateway 4002); live ports (7496, 4001) are refused with no override. After connecting, every managed account id must start with "DU" or the adapter
  disconnects and refuses. The dashboard's data adapter (command_center/ibkr) is read-only and exposes no order calls. Order logic lives only in tradelab/execution
  (pure, simulated broker) and tradelab/brokers/ibkr_paper (the only code allowed to import ib_async for orders). Orders require ALL of: an approved_paper strategy
  state, an approved risk policy, the user's HMAC-signed approval record (key the agents do not hold, created by the user running the approve CLI locally) bound to the
  paper account id, and a paper-account identity check on every order. LIVE trading is NOT allowed and cannot be enabled by config. Keep account data out of git
  (the repository is PUBLIC).
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
