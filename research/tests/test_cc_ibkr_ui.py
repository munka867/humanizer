"""Static contract for the IBKR UI pieces (no browser): safe DOM, honest copy, no order paths, core edits are small and listed."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
S = ROOT / "command_center" / "static"
PANEL = (S / "js" / "ibkr" / "panel.js").read_text()
STORE = (S / "js" / "market" / "ibkr.js").read_text()
SHELL = (S / "js" / "core" / "shell.js").read_text()
ENV = (S / "js" / "core" / "env.js").read_text()
DATA = (S / "js" / "workspaces" / "data.js").read_text()


def test_panel_and_store_use_safe_dom_only():
    for name, s in (("panel", PANEL), ("store", STORE)):
        for bad in (r"\.innerHTML", r"\.outerHTML", r"insertAdjacentHTML", r"document\.write", r"\beval\(", r"new Function"):
            assert not re.search(bad, s), f"{name}: {bad}"
        assert not re.search(r"https?://", s), name


def test_panel_only_posts_to_read_only_ibkr_controls():
    posts = re.findall(r'api\.post\(\s*("[^"]+")', PANEL)
    assert sorted(set(posts)) == ['"/api/ibkr/connect"', '"/api/ibkr/disconnect"', '"/api/ibkr/history"', '"/api/ibkr/history/stop"']
    low = PANEL.lower()
    for bad in ("/api/order", "place_order", "submit", "transmit"):
        assert bad not in low
    assert not re.search(r'["\'](Buy|Sell|Place order)["\']', PANEL)


def test_panel_copy_is_honest():
    for needle in ("Not yet verified against a real TWS/Gateway", "SIMULATED IBKR (test fixture)", "Read-only", "Execution: none", "data/processed/", "never touched or overwritten",
                   "pip install ib_async==2.1.0", "ibkr.doctor", "Read-Only API", "127.0.0.1", "IBKR_SETUP.md", "total is an estimate", "paper"):
        assert needle in PANEL, needle
    assert "live ports stay blocked" in PANEL.lower()


def test_panel_shows_plain_language_error_with_fix():
    assert "What to do" in PANEL and "le.fix" in PANEL and "le.message" in PANEL and 'role: "alert"' in PANEL


def test_data_js_edit_is_small_and_isolated():
    assert 'import { mountIbkrPanel } from "../ibkr/panel.js"' in DATA
    assert DATA.count("mountIbkrPanel") == 2 and DATA.count("ibSlot") == 3


def test_core_edits_are_limited_to_broker_chip_banner_and_env_reflection():
    assert "ibkrStore" in SHELL and "chipText" in SHELL and "SIMULATED IBKR (test fixture)" in SHELL and "banner-sim" in SHELL
    assert "ibkrStore" in ENV and "data_only" in ENV and 'execution: "none"' in ENV
    assert 'execution: "none"' in ENV and "readOnly: true" in ENV           # nothing changed about execution being none


def test_chip_wording_distinguishes_data_from_execution():
    assert "Broker data: connected (read-only" in STORE and "Broker data: not connected" in STORE and "Broker data: error" in STORE
    assert "SIMULATED IBKR" in STORE
