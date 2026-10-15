"""READ-ONLY Interactive Brokers data adapter (TWS / IB Gateway on the user's own computer).

Hard rules (CLAUDE.md, DECISIONS D15):
  * market data + account data are READ. Nothing in this package can send, change or cancel an order; a static test scans every
    file here for the order-sending API names and a runtime test checks the wrapper object has none of them.
  * PAPER ONLY: ports 7497 / 4002; live ports 7496 / 4001 are always refused (no override); every managed account id must start with DU.
  * the third-party library (ib_async) is imported lazily; the dashboard server starts and works without it.
  * nothing here has been run against a real TWS / Gateway yet. Tests use a simulated IB (fake_ib.py), clearly labelled.
"""
