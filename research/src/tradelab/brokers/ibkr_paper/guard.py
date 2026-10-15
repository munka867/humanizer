"""Connection guard: loopback host, PAPER ports only, fixed clientId, paper ('DU') accounts only. There is NO override: no env var,
argument or config value can add a port or a host."""
from __future__ import annotations

PAPER_PORTS = (7497, 4002)          # TWS paper, IB Gateway paper
LOOPBACK_HOSTS = ("127.0.0.1", "::1")
CLIENT_ID = 21                       # the dashboard data adapter uses 9100-9109 (command_center/ibkr/guard.py)
PAPER_PREFIX = "DU"


class GuardError(RuntimeError):
    pass


def check_target(host: str, port: int, client_id: int) -> tuple[str, int, int]:
    if host not in LOOPBACK_HOSTS:
        raise GuardError(f"Refused: host {host!r} is not loopback. The paper adapter only talks to TWS/Gateway on this computer (127.0.0.1).")
    if isinstance(port, bool) or not isinstance(port, int) or port not in PAPER_PORTS:
        raise GuardError(f"Refused: port {port!r} is not a PAPER port. Allowed: 7497 (TWS paper), 4002 (Gateway paper). Live ports are never allowed.")
    if client_id != CLIENT_ID:
        raise GuardError(f"Refused: clientId must be exactly {CLIENT_ID} (the data adapter uses 9100-9109).")
    return host, port, client_id


def check_accounts(managed, expected_account: str | None = None) -> list[str]:
    ids = [str(a) for a in managed]
    if not ids:
        raise GuardError("Refused: TWS reported no managed accounts.")
    bad = [a for a in ids if not a.startswith(PAPER_PREFIX)]
    if bad:
        raise GuardError("Refused: this login is not a paper account (every account id must start with 'DU'). Close TWS and log in with the PAPER login.")
    if expected_account is not None and expected_account not in ids:
        raise GuardError("Refused: the approved paper account id is not among the accounts of this TWS login.")
    return ids


def check_order_account(account_id: str, managed, approved_account: str | None = None) -> str:
    if not str(account_id).startswith(PAPER_PREFIX):
        raise GuardError("Refused: order account is not a paper account.")
    if account_id not in [str(a) for a in managed]:
        raise GuardError("Refused: order account is not one of the connected TWS login's accounts.")
    if approved_account is not None and account_id != approved_account:
        raise GuardError("Refused: order account differs from the account bound in the signed approval.")
    return account_id
