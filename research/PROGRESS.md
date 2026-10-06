# PROGRESS
## Done
- 2026-10-06: Scaffold, CLAUDE.md, contracts.py, search budget created. Instrument chosen: MES (micro E-mini S&P) / NQ family; data source: user-committed CSVs.
- Network check: Yahoo/Stooq/Binance/Coinbase/Kraken/YouTube blocked; GitHub + PyPI reachable.
## Done (phase 1-2)
- Data tools, backtester, validation/MC built in parallel; independent audit (docs/REVIEW.md: 2 high, 4 medium, 7 low, all fixed; spec v1.1).
- Full suite: 176 tests (`cd research && python -m pytest -q`, about 2 min).
- Verdict so far: INCONCLUSIVE (docs/ASSESSMENT.md). No real data used.
## In progress
- Waiting on user-supplied real data.
## Results
None. No real data yet. Nothing here is evidence of profitability.
## Commands
- Quality: `python scripts/check_data.py <csv> --freq 5min --tz <tz>`
- Backtest: `python scripts/run_backtest.py --bars <csv> --strategy ... --segment train` (see script --help)
- Monte Carlo: `python scripts/run_montecarlo.py <trades.csv>`
## Limitations
- Placeholder costs; holiday list is conservative; closed-trade drawdown only; no real data.
## Next
- User: commit intraday MES/ES (1m or 5m) CSVs to research/data/raw/ (see docs/DATA_REQUEST.md once written).
