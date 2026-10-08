# IBKR account-export import: SUPPORT MATRIX (written 2026-10-08)

## Read this first
- **No real IBKR export sample exists.** TASKS.md blocker B1 still stands (no Flex/Trade Confirmation file from the user; the connector account is empty).
- **Every IBKR field name below is from recollection of public documentation and is UNVERIFIED.** Nothing here was checked against a real file.
- **All test fixtures are SYNTHETIC** (`tests/fixtures/SYNTHETIC_*`): invented symbols (`SYNTHA`), account id (`SYNTH0001`), prices and ids. They prove the software
  path, not that real IBKR files will parse.
- The importer **stores and validates; it does not normalize and it does not reconcile.** `reconciliation: not performed (no normalization implemented)`.
- Account exports are kept apart from market data: raw files go to `data/raw/ibkr_exports/` (git-ignored, mode 0600); market data lives in `data/processed/` and
  `data/raw/ibkr_connector/`. The importer never reads market data and the coverage endpoint never reads exports.

## Endpoints (command_center/api_data.py)
| Endpoint | Effect |
|---|---|
| `POST /api/import/preview` `{filename, content}` | detect format, header, first 20 rows (account-like fields masked), per-field mapping status, validation, duplicate check. Stores nothing. |
| `POST /api/import/commit` `{filename, content}` | stores the raw bytes (UTF-8 of the text received), registers sha256, time, filename, size, row counts. Same bytes again: reported as duplicate (HTTP 200, `created:false`), nothing new is written. Unknown format or unparseable XML: refused (HTTP 422). |
| `GET /api/import/registry` | list of registered imports (no contents). |
Limits: `content` is text, at most 5 MB (the server's global `MAX_BODY` of 1,000,000 bytes currently rejects larger request bodies before this module sees them).
Duplicate detection is on the sha256 of the text as received; a browser that strips a BOM or normalises line endings produces a different hash than the original file.
File contents are never logged; only the preview rows (first 20) are returned.

## Format detection (heuristic, header-name matching only)
| Detected as | Rule | Status |
|---|---|---|
| `ibkr_flex_xml` | root element `FlexQueryResponse`; rows are attribute-bearing `Trade` (also `Order`, `Lot`, `OpenPosition`, `CashTransaction`) elements | UNVERIFIED structure; synthetic fixture only |
| `trade_confirmation_like` (XML) | same root, rows are `TradeConfirm` elements | UNVERIFIED |
| `ibkr_flex_csv` | header row with >= 3 of the Flex trade field names below; or sectioned layout (`BOF`/`HEADER`/`DATA`/`EOF` rows) | flat layout: synthetic fixture only; sectioned layout: handled from recollection, NO fixture |
| `trade_confirmation_like` (CSV) | `SettleDate` present with `Price` instead of `TradePrice`, or >= 3 confirmation field names | UNVERIFIED |
| `unknown` | anything else (header and rows are still shown; nothing is mapped; commit refused) | - |
XML safety: DTD/entity declarations are refused (no external entity resolution, no entity expansion). Malformed XML is reported with line and column.

## Field mapping (normalized field -> accepted source names, first match wins, case-insensitive)
`required` fields missing from the header are listed in `missing_required_fields`; rows with an empty required value are quarantined.
| Normalized | Required | Accepted source names (UNVERIFIED) | Row check |
|---|---|---|---|
| account_id | no | ClientAccountID, AccountID, Account, accountId | masked in preview |
| symbol | yes | Symbol | non-empty |
| con_id | no | ConID, conid | - |
| asset_class | no | AssetClass, assetCategory | - |
| trade_datetime | yes | DateTime, TradeDateTime, TradeDate, tradeDate, ExecutionTime, Date/Time | parseable (`YYYY-MM-DD`, `YYYYMMDD`, `YYYYMMDD;HHMMSS`, `YYYY-MM-DD;HHMMSS`, ISO, `MM/DD/YYYY`) |
| side | yes | Buy/Sell, buySell, Side, Action | non-empty (values not checked) |
| quantity | yes | Quantity, Shares | numeric |
| price | yes | TradePrice, Price | numeric |
| currency | no | CurrencyPrimary, Currency | - |
| commission | no | IBCommission, Commission | numeric |
| proceeds | no | Proceeds, Amount, NetCash | numeric |
| trade_id | no | TradeID, ExecID, IBExecID | - |
| order_id | no | IBOrderID, OrderID | - |
| exchange | no | Exchange | - |
Source fields not in this table are reported under `unmapped_source_fields` and ignored.

## Validation summary semantics
- A row is **quarantined** (not dropped silently) with its 1-based line number and a reason: `column count N != header M`, `missing value for required field X`,
  `non-numeric value in X`, `unparseable date/time in X`. For XML the line is the line of the element's start tag.
- Only the first 50 quarantined rows are listed (`quarantined_truncated`); counts are complete.
- Quarantined rows do not block a commit (the raw file is kept as received); they are counted in the registry.

## NOT supported (explicitly)
Sign conventions, currency conversion, fee categories, corporate actions, option/futures multipliers, multi-section Flex files beyond the first row type, cash and
position reconciliation against any account state, anything about order placement. Add support only after a real sample is supplied by the user and fixtures are
derived from its structure (with values replaced).
