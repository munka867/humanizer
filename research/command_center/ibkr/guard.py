"""Connection guard: loopback only, fixed port allow-list, fixed client-id range, always read-only."""
from __future__ import annotations

import os
from dataclasses import dataclass

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
ENV_ALLOW_LIVE = "CC_IBKR_ALLOW_LIVE_PORT_READONLY"
CLIENT_ID_MIN, CLIENT_ID_MAX, CLIENT_ID_DEFAULT = 9100, 9109, 9101

# kind -> (human label, port, is_live_port)
KINDS = {
    "paper_tws": ("TWS (paper port 7497)", 7497, False),
    "paper_gateway": ("IB Gateway (paper port 4002)", 4002, False),
    "live_tws_readonly": ("TWS (LIVE port 7496, read-only)", 7496, True),
    "live_gateway_readonly": ("IB Gateway (LIVE port 4001, read-only)", 4001, True),
}


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


def live_ports_enabled(env=None) -> bool:
    return (env if env is not None else os.environ).get(ENV_ALLOW_LIVE) == "1"


def allowed_kinds(env=None) -> list[str]:
    return [k for k, (_, _, live) in KINDS.items() if not live or live_ports_enabled(env)]


def resolve_target(kind: str, host: str = "127.0.0.1", port: int | None = None, client_id: int | None = None, env=None) -> Target:
    if kind not in KINDS:
        raise GuardError(f"Unknown connection kind {kind!r}. Allowed: {', '.join(allowed_kinds(env))}.")
    label, default_port, live = KINDS[kind]
    if live and not live_ports_enabled(env):
        raise GuardError(f"Refused: {label} is a LIVE-account port. Live ports stay blocked unless you set {ENV_ALLOW_LIVE}=1 in the environment "
                         "that starts the dashboard (and even then this app only reads). Use the paper port instead.")
    if host not in LOOPBACK_HOSTS:
        raise GuardError(f"Refused: host {host!r} is not this computer. Only {', '.join(LOOPBACK_HOSTS)} are allowed.")
    p = default_port if port is None else port
    if isinstance(p, bool) or not isinstance(p, int):
        raise GuardError("Refused: port must be an integer.")
    allowed = {KINDS[k][1] for k in allowed_kinds(env)}
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
    for k, (_, p, _) in KINDS.items():
        if p == port and k in allowed_kinds(env):
            return k
    raise GuardError(f"Refused: port {port} is not allowed (allowed: {sorted(KINDS[k][1] for k in allowed_kinds(env))}).")
