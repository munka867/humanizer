# Broker facts verified from official pages (fetched 2026-10-08 via Firecrawl)
- TWS API limitations page: "Canadian Residents Restricted From Programmatically Trading Canadian Products" —
  Interactive Brokers Canada does not allow clients to submit orders via API (incl. third-party apps) for products
  traded on a Canadian exchange/marketplace, citing CIRO/IIROC DMR 3200 A.1.(b)(i). The page restricts CANADIAN products;
  it does not state a rule for US-listed stocks/ETFs or CME futures. Before enabling any API order submission, confirm in
  writing with IBKR Canada that your account type may submit API orders for the specific US/CME products.
- Same page: the paper-trading environment "relies on more simulated technologies than the Live trading environment";
  order execution behaviour may differ. Paper requires an approved, funded live account.
- Flex Queries (ibkrguides flex.htm, updated 2025-10-30): customisable Activity and Trade Confirmation templates; output TEXT or XML;
  saved queries cover the four previous calendar years plus the current year. Flex files are the intended source for account
  history; we have NO sample yet (see TASKS.md blocker B1).
- Not yet verified (need pages fetched): order-placement docs, paper-vs-live campus article, market-data entitlements,
  historical-data pacing, margin for MES.
- The connector's own limits (docs/DATA_FINDINGS.md): 1000 bars/request, no end date, delayed ~15 min.
