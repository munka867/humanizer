# PAPER_SETUP - first paper-trading smoke test (for a non-technical Windows user)

**What this is.** A one-time check that the order plumbing works against your TWS PAPER account: it buys 1 share of SPY and sells it
again, then prints a report. It proves nothing about any strategy (none of our strategies has shown a positive result). It uses **play money
in the paper account only**. It cannot run against a live account or a live port: the code refuses.

**What has and has not been verified (read this first).**
- VERIFIED (automated tests against a FAKE TWS object, no network): the safety checks, the order mapping, the permit/approval logic, the
  report, and every abort path.
- UNVERIFIED (nobody has tried it): everything against the real TWS. Real TWS may send messages or timings the fake does not. The first
  real run is yours; if anything looks odd, stop and tell the assistant what the screen printed (never the key).
- IBKR says a paper account requires an approved and funded live account. If your paper login does not work, that is the likely reason.
- IBKR Canada may restrict API orders on some products (docs/BROKER_NOTES.md). SPY is a US product; confirm with IBKR if unsure.

## 1. Log in to TWS PAPER
Start Trader Workstation (TWS) and choose the **Paper Trading** login (separate username/password from your real account). The title bar
should say paper trading and your account number starts with **DU**. If it starts with anything else, close TWS: you are on the wrong login.

## 2. Turn on the API (paper session only)
In TWS: **File > Global Configuration** (or Edit > Global Configuration) **> API > Settings**:
1. tick **Enable ActiveX and Socket Clients**;
2. **Socket port: 7497** (the paper default; do not use 7496);
3. **untick "Read-Only API"** (the smoke test must be allowed to send orders; turn Read-Only back ON afterwards);
4. under **Trusted IPs** add **127.0.0.1** (this computer only);
5. click **Apply/OK**. Keep TWS open and logged in during the test.
Leave any "allow connections from localhost only" option ON.

## 3. Install the one extra package
In a Windows terminal (PowerShell) inside the project's `research` folder:
```
pip install ib_async==2.1.0
```
`ib_async` 2.1.0 is the exact pinned version (PyPI, checked 2026-10-15). Its license is BSD 2-Clause (permissive). It is an OPTIONAL
dependency used only by `tradelab/brokers/ibkr_paper` and the dashboard's read-only data adapter. Do not install `ibapi` or `ib_insync`.

## 4. Choose and keep a secret key (never share it with an assistant)
The approval record is signed with a secret that only you know. Pick a long random phrase (20+ characters, e.g. 4 unrelated words plus digits).
Write it in your password manager. **Never paste it into a chat, a file in the project, an email, or a screenshot, and never tell any
assistant what it is.** An assistant that knows the key could sign its own approvals.
In PowerShell, for the current window only:
```
$env:CC_APPROVAL_KEY = "your secret phrase here"
```
(Closing the window forgets it. That is intended.)

## 4b. Tell Python where the project code is (every new window)
The commands below start with `python -m tradelab...`, which only works if Python is told to look in the project's `src` folder. In the same window,
from the `research` folder, run ONE of these (the first for PowerShell, the second for the older black Command Prompt):
```
$env:PYTHONPATH = "src"
```
```
set PYTHONPATH=src
```
Without this you will see "No module named 'tradelab'". (The dashboard and the doctor, `python -m command_center...`, do not need it.)

## 5. Create the signed approval (you run this, not the assistant)
From the `research` folder, with the key set in the same window:
```
python -m tradelab.execution.approve --account DUxxxxxxx --instruments 756733 --strategy-id SMOKE_TEST --strategy-version 1 --capital 1000 --risk-policy configs/risk_policy.smoke.yaml --valid-hours 4 --out configs/approvals/paper_approval.json
```
Replace `DUxxxxxxx` with YOUR paper account id from the TWS title bar (starts with DU). The file binds: your account, SPY only, strategy
SMOKE_TEST version 1, the exact smoke risk policy file (any edit to it invalidates the approval) and an expiry 4 hours from now.
Do NOT commit or share `configs/approvals/paper_approval.json` (it contains your account id; the repository is public).

Notes: the approve command refuses non-paper (non-`DU`) accounts and validity above 24 hours. `--capital` is enforced as a HARD CAP on gross exposure (the smaller of it and the
policy cap), so it must be at least the price of one SPY share (use 1000 or more). The execution service also takes a per-account lock file in your home folder
(`.tradelab_locks`), so a second run for the same account at the same time is refused.

## 6. Run the smoke test (during regular US market hours: 09:35-15:55 New York time)
```
python -m tradelab.brokers.ibkr_paper.smoke_test
```
It first prints what it will do, checks everything, shows the exact two orders, and waits. Only if you type exactly
`I PLACE A PAPER ORDER` does it send anything. Use `--port 4002` only if you use IB Gateway (paper) instead of TWS.
Hard limits that no file can change: 1 share, SPY only, paper ports only, DU accounts only, no shorting, regular hours, no outside-hours orders.
You need a **live (real-time) SPY quote** in TWS; delayed data is refused on purpose.

## 7. Reading the result
`[PASS]`/`[FAIL]` for every step, then an audit trail (intent ids, order ids, states, fills, commissions) and a final RESULT line.
Exit codes: **0** all passed | **1** a step failed, position is flat | **2** a failure that may have left a position or order: **open TWS now and
check Positions and Orders in the paper account, close anything left by hand** | **3** you did not type the phrase, nothing was sent |
**4** refused before connecting (setup problem).

| what you see | meaning / what to do |
|---|---|
| `approval key present` FAIL | `$env:CC_APPROVAL_KEY` not set in this window (step 4) |
| `signed approval valid...` FAIL | file missing, expired, edited, signed with a different key, wrong account/strategy/policy; redo step 5 |
| `risk policy within the hard-coded smoke caps` FAIL | `configs/risk_policy.smoke.yaml` was changed beyond 1 share / SPY only |
| `connect to TWS paper` FAIL "refused/port" | TWS not running, API not enabled, or the port is not 7497/4002 (step 2) |
| connect FAIL "not a paper account" | you are logged into a non-paper login; log into PAPER |
| `SPY quote is fresh` FAIL | no real-time data subscription, market closed, or data delayed; nothing was sent |
| `startup reconciliation clean` FAIL | the paper account already holds SPY or has open/unknown orders; clear them in TWS first |
| `buy order accepted` FAIL with `OUTSIDE_SESSION` etc. | the risk policy refused (hours, spread, data age, margin check); read the reason |
| `buy filled` FAIL | the order was cancelled after 30 s without a fill; check TWS; try again in liquid hours |
| RESULT exit 2 | possible leftover position/order: check TWS immediately |

## 8. Afterwards
Turn **Read-Only API** back ON in TWS, close the PowerShell window (forgets the key), and send the printed report (without the key) to
the assistant. Delete `configs/approvals/paper_approval.json` when you are done. The ledger lives in `%USERPROFILE%\.tradelab_smoke\ledger.db`
(outside the project).

## 9. Notes for reviewers
- Permits: ExecutionService mints a single-use, 60-second `PaperOrderPermit` after `assert_may_submit(PAPER)` for each order; the adapter
  refuses anything without one. The smoke registry entry (`SMOKE_TEST`/`1` = approved_paper) is hard-coded in `smoke_test.py` and exists only
  for this test; it still needs your signed approval.
- Not covered: partial-fill behaviour of real TWS, commission reports arriving late, order-id/permId quirks, paper-account market-data
  entitlements, margin what-if output formats, Gateway differences, multiple API clients. See docs/READINESS.md.
