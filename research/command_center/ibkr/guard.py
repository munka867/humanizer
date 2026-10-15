"""Connection guard: loopback only, PAPER ports only (no override), fixed client-id range, always read-only, paper-account identity check.

CLAUDE.md (narrowed 2026-10-15): the only IBKR account this project may use is the user's PAPER account (ids start with "DU"). Live ports 7496/4001 are
refused with NO override; after connecting every managed account id must start with "DU" or the adapter disconnects and refuses."""
from __future__ import annotations

import os
from dataclasses import dataclass

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
PAPER_ACCOUNT_PREFIX = "DU"
CLIENT_ID_MIN, CLIENT_ID_MAX, CLIENT_ID_DEFAULT = 9100, 9109, 9101

# kind -> (human label, port, is_live_port)
KINDS = {
    "paper_tws": ("TWS (paper port 7497)", 7497, False),
    "paper_gateway": ("IB Gateway (paper port 4002)", 4002, False),
}
REFUSED_LIVE_PORTS = {7496: "TWS live", 4001: "IB Gateway live"}


class GuardError(ValueError):
    """The requested connection is not allowed. The message says exactly why and what to do."""


@dataclass(frozen=True)
class Target:
    kind: str
    label: str
    host: str
    port: int
    client_id: int
    live_port: bool
    readonly: bool = True  # always True; there is no way to construct a Target without it


def allowed_kinds(env=None) -> list[str]:
    """Paper kinds only. `env` is accepted for compatibility and ignored: there is no environment override for live ports."""
    return list(KINDS)


class AccountIdentityError(RuntimeError):
    """The TWS/Gateway login is not (only) a paper account."""


def check_paper_accounts(accounts) -> None:
    accts = [str(a) for a in (accounts or [])]
    if not accts or not all(a.startswith(PAPER_ACCOUNT_PREFIX) for a in accts):
        raise AccountIdentityError("Refused: this TWS/Gateway login is not a PAPER account (every managed account id must start with \"DU\"). "
                                   "Log in to your paper-trading account, not your live account. Nothing was read.")


def resolve_target(kind: str, host: str = "127.0.0.1", port: int | None = None, client_id: int | None = None, env=None) -> Target:
    if kind in ("live_tws_readonly", "live_gateway_readonly") or kind.startswith("live"):
        raise GuardError("Refused: live-account ports (7496, 4001) are never allowed in this project; there is no override. Use the paper port (TWS 7497 or Gateway 4002).")
    if kind not in KINDS:
        raise GuardError(f"Unknown connection kind {kind!r}. Allowed: {', '.join(KINDS)}.")
    label, default_port, live = KINDS[kind]
    if host not in LOOPBACK_HOSTS:
        raise GuardError(f"Refused: host {host!r} is not this computer. Only {', '.join(LOOPBACK_HOSTS)} are allowed.")
    p = default_port if port is None else port
    if isinstance(p, bool) or not isinstance(p, int):
        raise GuardError("Refused: port must be an integer.")
    if p in REFUSED_LIVE_PORTS:
        raise GuardError(f"Refused: port {p} ({REFUSED_LIVE_PORTS[p]}) is a live-account port; live ports are never allowed here and there is no override. Use 7497 (TWS paper) or 4002 (Gateway paper).")
    allowed = {KINDS[k][1] for k in KINDS}
    if p not in allowed:
        raise GuardError(f"Refused: port {p} is not on the allow-list {sorted(allowed)}. In TWS/Gateway set the socket port to one of these.")
    if p != default_port:
        # a port from a different kind (e.g. 4002 passed with paper_tws) would silently change what we talk to
        raise GuardError(f"Refused: port {p} does not belong to {label}. Pick the matching kind instead.")
    cid = CLIENT_ID_DEFAULT if client_id is None else client_id
    if isinstance(cid, bool) or not isinstance(cid, int) or not (CLIENT_ID_MIN <= cid <= CLIENT_ID_MAX):
        raise GuardError(f"Refused: clientId must be an integer in {CLIENT_ID_MIN}-{CLIENT_ID_MAX}.")
    return Target(kind=kind, label=label, host=host, port=p, client_id=cid, live_port=live)


def kind_for_port(port: int, env=None) -> str:
    if port in REFUSED_LIVE_PORTS:
        raise GuardError(f"Refused: port {port} ({REFUSED_LIVE_PORTS[port]}) is a live-account port; never allowed, no override. Use 7497 or 4002.")
    for k, (_, p, _) in KINDS.items():
        if p == port:
            return k
    raise GuardError(f"Refused: port {port} is not allowed (allowed: {sorted(v[1] for v in KINDS.values())}).")
