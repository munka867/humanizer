"""Pluggable API routes so feature modules do not edit server.py's monolithic handler.
A route fn receives (h, m, q, body): h = request Handler (use h._json/h._err/h.server), m = regex match, q = query dict,
body = parsed JSON for POST. POST routes require X-CC-Token (enforced by dispatch). No broker/trading imports here."""
from __future__ import annotations
import re
from typing import Callable

ROUTES: list[tuple[str, re.Pattern, Callable]] = []


def route(method: str, pattern: str):
    def deco(fn):
        ROUTES.append((method.upper(), re.compile(pattern + r"\Z"), fn))
        return fn
    return deco


def find(method: str, path: str):
    for m, rx, fn in ROUTES:
        if m == method.upper():
            mt = rx.match(path)
            if mt:
                return fn, mt
    return None, None


def has_post(path: str) -> bool:
    return find("POST", path)[0] is not None


def load_modules():
    """Import optional feature modules; each registers routes at import time."""
    import importlib
    for name in ("api_core", "api_market", "api_research", "api_data"):
        try:
            importlib.import_module(f"command_center.{name}")
        except ModuleNotFoundError as e:
            if e.name != f"command_center.{name}":
                raise
