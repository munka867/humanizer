"""READ-ONLY Interactive Brokers data adapter (TWS / IB Gateway on the user's own computer).

Hard rules (CLAUDE.md, DECISIONS D15):
  * market data + account data are READ. Nothing in this package can send, change or cancel an order; a static test scans every
    file here for the order-sending API names and a runtime test checks the wrapper object has none of them.
  * default ports are PAPER (TWS 7497, Gateway 4002). Live ports are refused unless CC_IBKR_ALLOW_LIVE_PORT_READONLY=1 (still read-only).
  * the third-party library (ib_async) is imported lazily; the dashboard server starts and works without it.
  * nothing here has been run against a real TWS / Gateway yet. Tests use a simulated IB (fake_ib.py), clearly labelled.
"""
