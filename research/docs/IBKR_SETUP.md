# IBKR setup: live/delayed data and history from YOUR OWN TWS or IB Gateway (read-only)

**Honesty first.** This feature was written and tested against a SIMULATED IB (`command_center/ibkr/fake_ib.py`). It has **not** been run against a real TWS or
IB Gateway, and nothing here claims it works with your account. The first real run is yours: start with the doctor (section 7) and report any FAIL.

**PAPER ACCOUNT ONLY.** The only IBKR account this project may use is your **paper-trading** account (its ids start with `DU`). Keep your TWS / Gateway login on the paper account. Live ports (7496, 4001) are always refused, with no override. After connecting, the dashboard reads the managed account ids; if any id does not start with `DU` it disconnects at once, shows "Not a paper account: refused", and serves no quotes or account data from that session.

**What it can and cannot do.** The dashboard reads market data and account data. It has no order screen, no order endpoint, and the adapter package contains
no order-sending, changing or cancelling call (a static test scans every file for them, and a runtime test checks the wrapper object has none). Execution stays "none".
For the dashboard's data adapter, **Read-Only API can stay ON** (IBKR says it is on by default and blocks all API orders). A separate, order-capable paper adapter (not part of this dashboard, not built here) would need it OFF; **never log in to a live account in a TWS where Read-Only API is OFF**, and keep one TWS for paper only. (Consequence: IBKR also hides order/execution information from the API while it is on, so
the Portfolio screen shows working orders and fills as "unknown", not "none".)

## 1. What you need
- A Windows computer with TWS (Trader Workstation) **or** IB Gateway installed and logged in. Either works; the API is the same. Download: https://www.interactivebrokers.com/en/trading/tws.php (TWS) and https://www.interactivebrokers.com/en/trading/ibgateway-stable.php (Gateway).
- Log in to your **paper** account (ids start with DU). IBKR's API docs say paper trading "requires an approved, funded live account" (docs/BROKER_NOTES.md); your connected account was empty/new on 2026-10-08, so you may not be able to get one yet. Live data also needs "live data permissions", which for most instruments means a funded account with market-data subscriptions.
- IBKR staff replied on Campus that APIs are not available for IBKR Lite accounts (a reader comment thread, not formal documentation): check your account plan with IBKR.
- Python 3.10 or newer. ib_async 2.1.0 lists Python 3.10 to 3.14; **Python 3.15 is UNVERIFIED** (the server still starts without ib_async, you just cannot connect).

## 2. Turn the API on in TWS / Gateway
TWS: click the cog wheel (top right) > Global Configuration > **API > Settings**. IB Gateway: **Configure > Settings > API > Settings**. (Menu names differ between versions; IBKR's older docs say Edit > Global Configuration > API > Settings.)
1. Tick **Enable ActiveX and Socket Clients** (IB Gateway turns this on by itself; IBKR says the checkbox may not even appear there).
2. Leave **Read-Only API ON**.
3. **Socket port**: IBKR's defaults are TWS live 7496 / paper **7497**; IB Gateway live 4001 / paper **4002**. This dashboard allows only the paper ports 7497 and 4002; 7496 and 4001 are refused with no override.
4. Make sure **127.0.0.1** is in **Trusted IPs** (if TWS shows an "accept incoming connection" prompt, accept it). Only this computer is ever allowed by the dashboard.
5. Apply, OK. Keep TWS/Gateway running and logged in. IBKR designs both to restart daily (there is a nightly reset); the dashboard reconnects automatically with backoff.

## 3. Market data: live, delayed, frozen
- Market data types: 1 = live (needs subscriptions), 2 = frozen (last recorded at close), 3 = delayed (free, 15 to 20 minutes), 4 = delayed frozen. The dashboard asks for type 3: IBKR serves live data if your account is entitled, otherwise delayed. **Every quote is labelled live / delayed / frozen from the type TWS reports for that tick**, with the tick's own timestamp.
- Subscriptions are managed in IBKR **Client Portal** (Settings > Market Data Subscriptions; the exact menu path is UNVERIFIED). Subscriptions are per live username.
- Outside US market hours there may be no ticks at all; the doctor then says so.

## 4. Install the optional library
```
python -m pip install -r requirements-ibkr.txt
```
(or `python -m pip install ib_async==2.1.0`). It is optional: without it Data & Connections shows "ib_async not installed" and the rest of the dashboard works.

## 5. Connect from the dashboard
Start the dashboard as usual, open **Data & Connections**, choose "TWS paper (7497)" or "Gateway paper (4002)", click **Connect (read-only)**. The top-bar chip reads
"Broker data: connected (read-only, paper port 7497)" when it works, "not connected" or "error" otherwise (always separate from "Execution: none"). Errors show in plain language with the fix.
Command Center and Portfolio then show real read-only account data (a zero the broker reported is shown as zero **with an as-of time**; anything the broker did not report stays an em dash).
Watchlist and chart quotes switch to live/delayed ticks (request `/api/quotes?live=1`).

## 6. Download history (daily and intraday)
Data & Connections > "Download history": symbols, bar size (daily, 1 hour, 15 min, 5 min, 1 min), years, regular or extended hours. Files go to a **new** folder `data/processed_<YYYYMMDD>/`
(git-ignored) as the project's bar CSV (`ts_open` UTC, open, high, low, close, volume, contract) plus `MANIFEST.json` (symbol, conId, exchange, bar size, whatToShow, useRTH, market-data type, retrieved UTC, rows, sha256, delayed flag, library version).
The frozen dataset `data/processed/` used by pre-registered runs is **never** written, overwritten or deleted (enforced in code and tested). Existing files in a snapshot folder are never overwritten (a re-download gets `_r2`).
Pacing follows IBKR's published limits (below); long downloads take time and the progress bar counts real completed requests (the total is an estimate). A cancelled or interrupted download resumes from its partial file the same day.
Daily bars are stored with `ts_open` = 09:30 America/New_York of the session date in UTC (same convention as the connector files). The still-forming bar is dropped.

## 7. The doctor (run this first)
```
python -m command_center.ibkr.doctor            # TWS paper 7497
python -m command_center.ibkr.doctor --port 4002   # Gateway paper
python -m command_center.ibkr.doctor --fake        # same steps against the SIMULATED IB, proves the tool itself
```
Steps (read-only; no orders, nothing written): ib_async installed, port allowed, port reachable, connect, paper-account identity (DU),  server time, SPY qualifies, market-data type and a quote, 1 daily bar, account summary readable. Each FAIL prints the exact fix.

## 8. Facts: VERIFIED vs UNVERIFIED
Verified = read on an official page or the package source by me on 2026-10-15 (fetched via Firecrawl). Nothing below was verified by running against a real TWS/Gateway.

| Fact | Status | Source |
|---|---|---|
| ib_async 2.1.0 is the latest on PyPI (released 2025-12-07), requires Python >= 3.10, classifiers 3.10-3.14 | VERIFIED | https://pypi.org/project/ib-async/ |
| ib_async licence is BSD 2-Clause | VERIFIED (LICENSE file; PyPI metadata only says "Other/Proprietary License (BSD)") | https://raw.githubusercontent.com/ib-api-reloaded/ib_async/main/LICENSE |
| Python 3.15 support | **UNVERIFIED** | not listed on PyPI |
| ib_async `connect(readonly=True)` only skips asking TWS for order state; it is **not** a client-side order block. Real protection = TWS Read-Only API + this wrapper having no order calls | VERIFIED in the 2.1.0 source (`ib.py` connectAsync) | the wheel on PyPI |
| Pacing: no identical request within 15 s; fewer than 6 requests per 2 s for the same contract/exchange/tick type; at most 60 requests per 10 minutes; BID_ASK counts double; at most 50 simultaneous requests | VERIFIED (the page states them for bars of 30 s or smaller; we apply them to every bar size, 58 per 10 min, 5 per 2 s) | https://interactivebrokers.github.io/tws-api/historical_limitations.html |
| For bar sizes of 1 min and larger the hard historical limits "have been lifted" but a soft throttle remains; requests that take minutes should be cancelled | VERIFIED | same page, note 1 |
| Step-size table (1 D allows 1 min to 1 day; 1 W 3 mins to 1 week; 1 M 30 mins to 1 month; 1 Y 1 day to 1 month). Our chunk sizes: daily 1 Y, 1 hour 1 M, 15 min 1 W, 5 min 1 W, 1 min 1 D | VERIFIED table; chunk choice is conservative, **not** a claim about IBKR's maxima | same page |
| How far back each bar size goes in total | **UNVERIFIED** (the page gives no per-bar-size depth; downloads stop when IBKR returns no older data). App caps: 30 y daily, 10 y intraday, 5 y 1-minute | |
| Market data types 1 live, 2 frozen, 3 delayed (free, 15-20 min), 4 delayed frozen; a delayed request is ignored if live data is available | VERIFIED | https://interactivebrokers.github.io/tws-api/market_data_type.html |
| Default ports: TWS live 7496 / paper 7497; IB Gateway live 4001 / paper 4002 | VERIFIED | https://www.interactivebrokers.com/campus/trading-lessons/installing-configuring-tws-for-the-api/ and https://interactivebrokers.github.io/tws-api/initial_setup.html |
| "Read-Only" API setting is on by default, blocks all API orders; order information is not available to the API in read-only mode | VERIFIED | the two pages above |
| IB Gateway enables socket clients automatically | VERIFIED (IBKR staff reply on that Campus page) | Campus lesson comments |
| Message codes 162, 200, 354, 502, 504, 1100, 1101, 1102, 2103-2108, 2158, 326, 10186, 10197 and their meanings | VERIFIED | https://interactivebrokers.github.io/tws-api/message_codes.html |
| Codes **10167** (delayed data displayed) and **321** being tied to Read-Only mode | **UNVERIFIED** (not on the official table I fetched / only the generic text is) | marked in `command_center/ibkr/errors.py` |
| IBKR Canada: orders via API are not allowed for products traded on a Canadian exchange (CIRO DMR 3200). Says nothing about US stocks/ETFs; confirm in writing before any order feature | VERIFIED 2026-10-08 | docs/BROKER_NOTES.md (TWS API limitations page) |
| Paper account requires an approved AND funded live account | VERIFIED 2026-10-08 | docs/BROKER_NOTES.md |
| This code works against a real TWS/Gateway | **NOT VERIFIED: never run** | |
| Client Portal menu path for market-data subscriptions | UNVERIFIED | |

## 9. Safety summary (all enforced in code and tests)
- Loopback only; paper ports 7497 and 4002 only; 7496/4001 always refused (no environment override); clientId fixed to 9100 to 9109; the connection flag is read-only; every managed account id must start with `DU` or the adapter disconnects and refuses.
- Stocks and ETFs only: other security types are refused.
- No order, cancel, modify or what-if call exists anywhere in `command_center/ibkr` or `api_ibkr.py`; the wrapper never hands out the underlying IB object.
- Simulated mode (`CC_IBKR_FAKE=1`, tests only, never the default): unmissable banner, `source='fake'`, files only under `data/fake_<date>/`.
- Account ids are masked in every response; nothing account-related is written into the repository.
- Doc links: IBKR TWS API docs https://interactivebrokers.github.io/tws-api/ (marked deprecated by IBKR in favour of IBKR Campus), IBKR Campus API https://www.interactivebrokers.com/campus/ibkr-api-page/twsapi-doc/.
