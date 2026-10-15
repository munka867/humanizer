"""Plain-language explanations for IBKR / TWS message codes. Codes come from the TWS API 'Message Codes' page
(https://interactivebrokers.github.io/tws-api/message_codes.html) unless marked UNVERIFIED in the 'basis' field."""
from __future__ import annotations

# code -> (severity, title, message, fix, basis)
_T = {
    162: ("error", "Historical data request failed", "The historical-data service returned an error. Common causes: a pacing violation (too many requests too fast), no data for that period, or no market-data permission.",
          "Wait a minute and retry. The downloader already paces itself; if this repeats, lower the number of symbols or try a larger bar size.", "official: 'Historical market data Service error message'"),
    165: ("info", "Historical data query message", "The historical-data service sent an informational message (for example, no data in its database for that period).", "Usually nothing to do.", "official"),
    200: ("error", "Contract not found or ambiguous", "IBKR has no single instrument matching that symbol/exchange (or more than one matches).",
          "Check the symbol and exchange. The app identifies instruments by conId + exchange; re-pick the instrument from the list.", "official: 'No security definition has been found' / ambiguous"),
    321: ("warn", "Request rejected by TWS", "TWS rejected a request ('Server error when validating an API client request'). While 'Read-Only API' is ON this is expected for anything that is not a read.",
          "If it appears for a read (quotes, bars, account), check Configure > Settings > API > Settings. Keep Read-Only API ON: this app never sends orders.", "official text; the read-only link is UNVERIFIED"),
    326: ("error", "Client id already in use", "Another program is connected to TWS/Gateway with the same client id.", "Close the other program, or restart this dashboard after it disconnects.", "official"),
    354: ("warn", "No live market-data subscription", "You are not subscribed to live data for this instrument.",
          "The app asks for delayed data (type 3) automatically; delayed quotes are labelled. To get live quotes, subscribe in Client Portal > Settings > Market Data Subscriptions.", "official"),
    501: ("error", "Already connected", "This app is already connected to TWS/Gateway.", "Disconnect first, then connect again.", "official"),
    502: ("error", "Could not connect to TWS / Gateway", "Nothing accepted the connection on that port.",
          "Start TWS or IB Gateway and log in (paper account). Then Configure > Settings > API > Settings: tick 'Enable ActiveX and Socket Clients', make the Socket port match, add 127.0.0.1 to Trusted IPs.", "official"),
    503: ("error", "TWS is out of date", "TWS/Gateway is too old for this API client.", "Update TWS/Gateway to a current version.", "official"),
    504: ("error", "Not connected", "A request was made while there is no connection to TWS/Gateway.", "Click Connect again.", "official"),
    507: ("error", "Connection was closed", "TWS/Gateway closed the connection (often a client id clash, a login lock, or TWS closing).", "Check TWS is still logged in, then Connect again.", "official"),
    1100: ("warn", "TWS lost its connection to IBKR", "TWS/Gateway is disconnected from IBKR's servers (internet problem, nightly reset, or a competing login).", "Wait; it normally reconnects by itself. Check that you are not logged in elsewhere with the same username.", "official"),
    1101: ("warn", "Connection restored, data requests lost", "IBKR connectivity returned but market-data subscriptions were dropped.", "The app re-subscribes automatically.", "official"),
    1102: ("info", "Connection restored", "IBKR connectivity returned and subscriptions were kept.", "Nothing to do.", "official"),
    1300: ("error", "TWS socket port changed", "The socket port in TWS/Gateway settings was changed while connected.", "Reconnect using the new port (it must still be 7497 or 4002).", "official"),
    2103: ("warn", "A market-data farm is disconnected", "TWS cannot reach one of IBKR's market-data servers.", "Usually an internet issue or the nightly reset; wait a few minutes.", "official"),
    2104: ("info", "Market-data farm connection is OK", "Informational.", "Nothing to do.", "official"),
    2105: ("warn", "A historical-data farm is disconnected", "TWS cannot reach IBKR's historical-data server.", "Wait a few minutes; history downloads pause meanwhile.", "official"),
    2106: ("info", "Historical-data farm is connected", "Informational.", "Nothing to do.", "official"),
    2107: ("info", "Historical-data farm inactive", "Idle connection; it wakes when a request is made.", "Nothing to do.", "official"),
    2108: ("info", "Market-data farm inactive", "Idle connection; it wakes when needed.", "Nothing to do.", "official"),
    2158: ("info", "Security-definition farm is OK", "Informational.", "Nothing to do.", "official"),
    10167: ("info", "Showing delayed market data", "No live subscription, so TWS serves delayed data (about 15-20 minutes old).", "Quotes are labelled 'delayed'. Subscribe in Client Portal for live data.", "UNVERIFIED: code not on the fetched official table"),
    10186: ("warn", "Delayed data is not enabled", "Requested market data is not subscribed and delayed data is not enabled.", "The app requests delayed data automatically; if it persists restart TWS/Gateway.", "official"),
    10197: ("warn", "No market data during competing session", "A paper and a live session are both asking for live data; the live one wins.", "Use only one session at a time for market data.", "official"),
}
INFO_CODES = {c for c, v in _T.items() if v[0] == "info"}


def explain(code, raw: str | None = None) -> dict:
    """Return {code, severity, title, message, fix, basis, raw}. Unknown codes are passed through honestly."""
    try:
        c = int(code)
    except (TypeError, ValueError):
        c = None
    if c in _T:
        sev, title, msg, fix, basis = _T[c]
        return {"code": c, "severity": sev, "title": title, "message": msg, "fix": fix, "basis": basis, "raw": (raw or "")[:300]}
    return {"code": c, "severity": "warn", "title": f"IBKR message {c}" if c is not None else "IBKR message", "message": (raw or "No text supplied.")[:300],
            "fix": "See the TWS API message-code table (link in docs/IBKR_SETUP.md).", "basis": "unmapped code", "raw": (raw or "")[:300]}


def explain_exception(exc: BaseException, port: int | None = None) -> dict:
    """Map a Python exception from connecting into a plain-language error."""
    name = type(exc).__name__
    text = str(exc) or name
    where = f" on port {port}" if port else ""
    if isinstance(exc, ConnectionRefusedError) or "refused" in text.lower() or "10061" in text:
        d = explain(502, text)
        d["message"] = f"Nothing is listening{where}. TWS / IB Gateway is not running, or its API is not enabled, or the port differs."
        return d
    if isinstance(exc, TimeoutError) or name in ("TimeoutError", "CancelledError") or "timeout" in text.lower():
        return {"code": None, "severity": "error", "title": "TWS / Gateway did not answer in time",
                "message": f"The connection{where} opened but TWS did not finish the handshake.",
                "fix": "Look at the TWS window: it may be waiting for you to log in or to accept an 'incoming connection' prompt. Add 127.0.0.1 to Trusted IPs in the API settings, then retry.",
                "basis": "general", "raw": text[:300]}
    if name == "LibraryMissing":
        return {"code": None, "severity": "error", "title": "ib_async is not installed", "message": text, "fix": "Run: python -m pip install ib_async==2.1.0  (then restart the dashboard).", "basis": "local", "raw": text[:300]}
    if name == "GuardError":
        return {"code": None, "severity": "error", "title": "Connection refused by the safety guard", "message": text, "fix": "Use the paper port (TWS 7497 or Gateway 4002).", "basis": "local", "raw": text[:300]}
    return {"code": None, "severity": "error", "title": "Connection failed", "message": f"{name}: {text}"[:300], "fix": "Check that TWS/Gateway is running and logged in; see docs/IBKR_SETUP.md.", "basis": "general", "raw": text[:300]}
