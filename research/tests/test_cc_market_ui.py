"""Static contract checks for the MARKET front end (no browser): safe DOM use, no external URLs, no order paths, module contract.
Browser journeys live in command_center/verify_market.py."""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "command_center" / "static"
JS_FILES = sorted([*(STATIC / "js" / "market").glob("*.js"), *(STATIC / "js" / "workspaces").glob("*.js")])
MINE = [STATIC / "js" / "workspaces" / f"{n}.js" for n in ("command_center", "stocks", "portfolio")] + sorted((STATIC / "js" / "market").glob("*.js"))
CSS = STATIC / "css" / "market.css"


def src(p):
    return p.read_text(encoding="utf-8")


def strip_comments(s):
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$|(?<=[\s;,)])//[^\n\"'`]*$", "", s)


@pytest.mark.parametrize("p", MINE, ids=lambda p: p.name)
def test_no_html_injection_apis(p):
    s = strip_comments(src(p))
    for bad in (r"\.innerHTML", r"\.outerHTML", r"insertAdjacentHTML", r"document\.write", r"\beval\(", r"new Function", r"\bhtml\s*:", r"setAttribute\(\s*['\"]on", r"\.onclick\s*="):
        assert not re.search(bad, s), f"{p.name}: forbidden pattern {bad}"


@pytest.mark.parametrize("p", MINE + [CSS], ids=lambda p: p.name)
def test_no_external_urls(p):
    s = src(p)
    assert not re.search(r"https?://|//cdn\.|@import\s+url\(\s*['\"]?https?", s), f"{p.name} references an external URL"


@pytest.mark.parametrize("p", MINE, ids=lambda p: p.name)
def test_no_order_paths(p):
    s = src(p).lower()
    for bad in ("/api/order", "/api/trade", "place_order", "placeorder", "submitorder", "submit_order", "create_order", "cancelorder", "cancel_order", "modifyorder", "ib_insync", "ibapi", "create_order_instruction", "/api/execute"):
        assert bad not in s, f"{p.name} mentions {bad}"
    posts = re.findall(r"api\.post\(\s*([^,]+),", src(p))
    for target in posts:  # the only writes this module family performs are watchlist edits
        assert "/api/watchlists" in target or "watchlists" in target or target.strip().startswith("items("), f"{p.name}: unexpected POST target {target}"


def test_css_uses_tokens_only():
    s = strip_comments(src(CSS))
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", s), "hex colour literal in market.css (use tokens.css variables)"
    assert not re.search(r"\brgba?\(|\bhsla?\(", s), "raw colour function in market.css"


@pytest.mark.parametrize("ws", ["command_center", "stocks", "portfolio"])
def test_workspace_module_contract(ws):
    s = src(STATIC / "js" / "workspaces" / f"{ws}.js")
    assert re.search(r"export default\s*\{", s)
    assert f'id: "{ws}"' in s and "title:" in s and f'icon: "{ws}"' in s
    assert re.search(r"async mount\(\s*host\s*,\s*ctx", s) or re.search(r"mount\(", s)
    assert "unmount(" in s
    assert "ensureCss" in s  # market.css is linked dynamically (index.html is not ours)


def test_chart_library_is_vendored_and_attributed():
    cp = src(STATIC / "js" / "market" / "chartpanel.js")
    ut = src(STATIC / "js" / "market" / "util.js")
    assert "/static/vendor/lightweight-charts.standalone.production.js" in ut
    assert "attributionLogo: true" in cp
    assert (STATIC / "vendor" / "lightweight-charts.standalone.production.js").is_file()
    assert "ResizeObserver" in cp


def test_honesty_strings_present():
    allsrc = "\n".join(src(p) for p in MINE)
    for needle in ("unavailable: no entitled source connected", "Order entry unavailable: no broker adapter, no approved risk policy", "Unadjusted for dividends", "chart-only", "not a strategy rule",
                   "unavailable on daily bars", "America/New_York", "Connect broker", "Deposits and withdrawals"):
        assert needle.lower() in allsrc.lower(), needle
    assert "Intraday" in allsrc and "disabled: true" in allsrc


def test_no_submit_controls_in_portfolio():
    s = strip_comments(src(STATIC / "js" / "workspaces" / "portfolio.js"))
    assert not re.search(r"""type:\s*["']submit["']|<form|el\(\s*["']form["']|el\(\s*["']input["']""", s)
    assert not re.search(r"""["'](Buy|Sell|Submit|Place order|Send order)["']""", s)
