import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_IB_ASYNC = (ROOT / "src" / "tradelab" / "brokers" / "ibkr_paper", ROOT / "command_center" / "ibkr")
BANNED_EVERYWHERE = {"ibapi", "ib_insync"}


def py_files():
    for base in ("src", "scripts", "command_center", "tests"):
        d = ROOT / base
        if d.exists():
            yield from (p for p in d.rglob("*.py") if "__pycache__" not in p.parts and ".venv" not in p.parts)


def imported(path):
    try:
        tree = ast.parse(path.read_text())
    except SyntaxError:
        return set()
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            out.add(n.module.split(".")[0])
    return out


def inside(path, dirs):
    return any(d in path.parents for d in dirs)


def test_ib_async_is_imported_only_in_the_two_allowed_places():
    offenders = [str(p.relative_to(ROOT)) for p in py_files() if "ib_async" in imported(p) and not inside(p, ALLOWED_IB_ASYNC)]
    assert offenders == []
    assert any("ib_async" in imported(p) for p in (ROOT / "src" / "tradelab" / "brokers" / "ibkr_paper").glob("*.py")), \
        "the paper adapter is expected to import ib_async lazily"


def test_ibapi_and_ib_insync_are_banned_everywhere():
    offenders = [str(p.relative_to(ROOT)) for p in py_files() if imported(p) & BANNED_EVERYWHERE]
    assert offenders == []


def test_ib_async_import_is_lazy_in_the_adapter():
    # no module-level import: importing the package must work without ib_async installed
    for p in (ROOT / "src" / "tradelab" / "brokers" / "ibkr_paper").glob("*.py"):
        tree = ast.parse(p.read_text())
        for n in tree.body:
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in n.names] + ([n.module] if isinstance(n, ast.ImportFrom) and n.module else [])
                assert not any(x.split(".")[0] == "ib_async" for x in names if x), f"{p.name} imports ib_async at module level"


def test_execution_package_never_imports_ib_async_or_brokers():
    for p in (ROOT / "src" / "tradelab" / "execution").glob("*.py"):
        src = p.read_text()
        assert "ib_async" not in imported(p) and "tradelab.brokers" not in src and "from tradelab import brokers" not in src, p.name


def test_command_center_does_not_import_the_brokers_package():
    cc = ROOT / "command_center"
    for p in cc.rglob("*.py") if cc.exists() else []:
        assert "tradelab.brokers" not in p.read_text(), str(p)


def test_brokers_package_has_only_paper_ports_and_no_network_modules():
    pkg = ROOT / "src" / "tradelab" / "brokers"
    for p in pkg.rglob("*.py"):
        t = p.read_text()
        assert not re.search(r"\b(7496|4001)\b", t), f"{p.name} mentions a live port"
        assert not (imported(p) & {"socket", "requests", "urllib", "http", "httpx", "aiohttp", "ssl", "subprocess"}), p.name
        assert "command_center" not in imported(p), p.name


def test_ib_async_is_an_optional_pinned_dependency_documented():
    doc = (ROOT / "docs" / "PAPER_SETUP.md").read_text()
    assert "ib_async==2.1.0" in doc and "BSD" in doc
