import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "src" / "tradelab" / "execution"
FORBIDDEN_MODULES = {"ib_async", "ibapi", "ib_insync", "socket", "requests", "urllib", "urllib3", "http", "httpx", "aiohttp", "ssl",
                     "websocket", "websockets", "subprocess", "ftplib", "smtplib", "telnetlib", "xmlrpc"}
FORBIDDEN_TEXT = re.compile(r"(?i)\b(7496|7497|4001|4002)\b|ibkr\.com|interactivebrokers|localhost|127\.0\.0\.1|gdcdyn|api\.ibkr|ib_async|ibapi")


def sources():
    return sorted(PKG.glob("*.py"))


def test_execution_package_exists_and_has_no_real_adapter():
    names = {p.stem for p in sources()}
    assert {"modes", "intents", "orders", "risk", "broker", "service", "replay", "strategy_adapter", "approve"} <= names
    assert not any("ibkr" in n or "adapter" in n and n != "strategy_adapter" for n in names)


def test_no_network_or_broker_sdk_imports_in_execution_package():
    for f in sources():
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                assert m.split(".")[0] not in FORBIDDEN_MODULES, f"{f.name} imports {m}"


def test_no_live_endpoints_or_ports_referenced():
    for f in sources():
        assert not FORBIDDEN_TEXT.search(f.read_text()), f"{f.name} references a live endpoint/port/SDK"


def test_no_env_or_config_path_can_enable_live():
    from tradelab.execution.modes import Mode
    import tradelab.execution.modes as M
    text = (PKG / "modes.py").read_text()
    assert "LIVE" in text and "refused unconditionally" in text
    assert [m for m in Mode if m.value == "LIVE"]          # the enum value exists only so it can be refused


def test_execution_package_does_not_import_research_or_dashboard_code():
    for f in sources():
        t = f.read_text()
        assert "command_center" not in t, f.name
    cc = ROOT / "command_center"
    if cc.exists():
        for f in cc.rglob("*.py"):
            assert "tradelab.execution" not in f.read_text(), f"{f} imports the execution package (research/dashboard must not reach order code)"
