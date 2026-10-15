"""IBKR error-code table -> (action, plain-language text). Actions:
  reject      order definitely not working at the broker                       -> REJECTED
  cancelled   order cancelled                                                   -> CANCELED
  cancel_fail a cancel request could not be applied (order already done)        -> keep state, reconcile
  uncertain   outcome ambiguous: NEVER blind-retry                              -> UNCERTAIN until reconciled
  resync      connectivity changed: state may have changed while blind          -> block entries, reconcile
  disconnect  API socket not connected                                          -> in-flight orders UNCERTAIN, resync
  info        informational / market-data problem, no order state change"""
from __future__ import annotations

ERRORS = {
    103: ("uncertain", "Duplicate order id: the broker may already hold an order with this id. Do not resend; reconcile."),
    104: ("uncertain", "Cannot modify a filled order: the order may already be filled. Reconcile."),
    110: ("reject", "The price does not conform to the minimum price variation for this contract."),
    135: ("uncertain", "IBKR cannot find the order for this request (cancel/modify). It may be already done; reconcile."),
    161: ("cancel_fail", "Cancel attempted when the order is not in a cancellable state (already filled or cancelled)."),
    200: ("reject", "No security definition found for the request (wrong conId/symbol/exchange/currency)."),
    201: ("reject", "Order rejected by IBKR."),
    202: ("cancelled", "Order cancelled."),
    354: ("info", "No market-data subscription for this instrument: quotes are unavailable."),
    399: ("info", "Order message from IBKR (warning/notice); the order is not changed by this message."),
    504: ("disconnect", "Not connected to TWS."),
    1100: ("resync", "Connectivity between TWS and IBKR was lost. Order state may be changing unseen."),
    1101: ("resync", "Connectivity restored but data was lost. Reconcile orders/positions before trading."),
    1102: ("resync", "Connectivity restored, data maintained. Reconcile to be sure."),
    1300: ("disconnect", "TWS socket port was reset: the connection is gone."),
}
INFO_CODES = {2100, 2101, 2102, 2103, 2104, 2105, 2106, 2107, 2108, 2109, 2110, 2119, 2137, 2158, 10167, 10168, 10187}


def classify(code: int, order_related: bool) -> tuple[str, str]:
    if code in ERRORS:
        return ERRORS[code]
    if code in INFO_CODES or code >= 2100 and code < 2200:
        return "info", "IBKR status message (data farm / connectivity notice)."
    if order_related:
        return "uncertain", f"Unmapped IBKR error {code} on an order: outcome ambiguous; reconcile before any retry."
    return "info", f"Unmapped IBKR error {code} (not tied to an order)."
