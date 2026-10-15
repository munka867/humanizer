"""IBKR PAPER adapter (TWS paper 7497 / Gateway paper 4002 only). The ONLY tradelab package allowed to import ib_async for orders
(the dashboard's read-only data adapter in command_center/ibkr is the only other place). ib_async is an OPTIONAL dependency, imported lazily."""
