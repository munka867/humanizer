"""Helper for validation tests: hand-made synthetic trade tables (TEST ONLY, not market data)."""
import pandas as pd
from tradelab.contracts import TRADE_COLUMNS


def mk(rows):
    """rows: (entry 'YYYY-MM-DD HH:MM' UTC, minutes_held, pnl_net[, costs[, risk]]) -> trade table."""
    recs = []
    for i, r in enumerate(rows):
        e = pd.Timestamp(r[0], tz="UTC")
        costs = r[3] if len(r) > 3 else 1.0
        risk = r[4] if len(r) > 4 else 10.0
        pn = float(r[2])
        recs.append(dict(trade_id=i, strategy="T", side=1, qty=1, signal_ts=e, entry_ts=e,
                         exit_ts=e + pd.Timedelta(minutes=r[1]), entry_px=100.0, exit_px=100.0,
                         exit_reason="stop" if pn < 0 else "target", risk_usd=risk, pnl_gross=pn + costs,
                         costs=costs, pnl_net=pn, r_multiple=pn / risk, bars_held=1))
    df = pd.DataFrame(recs, columns=TRADE_COLUMNS)
    return df
