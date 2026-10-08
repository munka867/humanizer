# How to view the command centre
It is a small web app that runs on YOUR machine at http://127.0.0.1:8765. It does not run anywhere you can reach from the
sandbox: the sandbox is a cloud container, so `127.0.0.1` there is not your computer. Two ways to see it:

1. Look at screenshots now (no setup): `research/command_center/screenshots/*.png` (seeded DEMO data, dark theme).
2. Run it locally (Python 3.11+, no pip install needed for the server):
```bash
git clone https://github.com/munka867/humanizer.git && cd humanizer
git checkout claude/trading-strategy-research-l7xgy7
cd research
bash command_center/run.sh                       # prints a token; open http://127.0.0.1:8765 in your browser
python command_center/load_events.py             # (second terminal) loads the real events from this project's work
python command_center/seed_demo.py               # optional: labelled DEMO data to see every state
```
The "Research library" screen lists the project docs; "Backtest lab" lists results/daily/*.md. PAPER and LIVE modes are
blocked by design. The dashboard only shows events sent to it (emit.py / load_events.py); it cannot see Claude Code's own
agent messages by itself.


## Simplest start (any OS, from the research folder)
`python -m command_center.server` then open http://127.0.0.1:8765 . In a second terminal: `python command_center/load_events.py`.
Real market data only appears if the local files exist: data/processed/{SPY,QQQ,IWM}_1d.csv are NOT in git (licensing); without them the
chart screens say no data is available. Browser checks: `python command_center/verify_shell.py` (and verify_agents / verify_market / verify_research / verify_csp).
