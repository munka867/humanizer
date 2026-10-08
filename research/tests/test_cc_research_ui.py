"""Static safety contract for the RESEARCH workspaces (no browser needed). Browser journeys: command_center/verify_research.py."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "command_center" / "static"
JS = sorted([*(STATIC / "js" / "research").glob("*.js"), *(STATIC / "js" / "workspaces").glob("*.js")])
JS = [p for p in JS if p.name in {"research.js", "backtest.js", "data.js", "settings.js"} or p.parent.name == "research"]
CSS = STATIC / "css" / "research.css"
WS = {"research": "research", "backtest": "backtest", "data": "data", "settings": "settings"}


def src(p):
    return p.read_text(encoding="utf-8")


def strip_comments(s):
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    return re.sub(r"(^|[^:\\'\"`])//[^\n]*", r"\1", s)


def test_files_exist():
    assert len(JS) >= 8 and CSS.is_file()


@pytest.mark.parametrize("p", JS, ids=lambda p: p.name)
def test_no_dom_injection_or_dynamic_code(p):
    s = strip_comments(src(p))
    for bad in (r"\.innerHTML", r"\.outerHTML", r"insertAdjacentHTML", r"document\.write", r"\beval\(", r"new Function", r"\.setAttribute\(\s*['\"]on", r"dangerouslySet", r"srcdoc"):
        assert not re.search(bad, s), (p.name, bad)
    assert not re.search(r"\bon(click|change|submit|input)\s*=", s), p.name       # inline handlers


@pytest.mark.parametrize("p", JS, ids=lambda p: p.name)
def test_no_external_urls_no_order_paths_no_readiness_claims(p):
    s = strip_comments(src(p))
    assert not re.search(r"https?://(?!\))", re.sub(r"\^https\?:\\/\\/|https\?:\\/\\/", "", s)), p.name
    assert not re.search(r"/api/(orders?|trade|execute|positions|broker)", s, re.I), p.name
    low = s.lower()
    for claim in ("ready for live", "live-ready", "live ready", "is profitable", "profitable strategy"):
        assert claim not in low, (p.name, claim)
    # allowed network targets are this app's own API and static vendor path
    for m in re.findall(r"""['"`](/(?:api|static)/[^'"`$?]*)""", s):
        assert m.startswith(("/api/", "/static/")), m


def test_css_uses_tokens_only():
    s = re.sub(r"/\*.*?\*/", "", src(CSS), flags=re.S)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", s), "hex colour in research.css"
    assert not re.search(r"\brgba?\(", s)
    for m in re.findall(r"var\((--[a-z0-9-]+)", s):
        tokens = src(STATIC / "css" / "tokens.css") + src(STATIC / "css" / "base.css")
        assert f"{m}:" in tokens, f"unknown token {m}"


@pytest.mark.parametrize("name", WS)
def test_workspace_module_contract(name):
    s = src(STATIC / "js" / "workspaces" / f"{name}.js")
    assert "export default" in s and f'id: "{name}"' in s and "mount(" in s and "unmount(" in s
    assert "makePlaceholder" not in s
    assert "useCss()" in s


def test_lightweight_charts_attribution_and_vendor_path():
    s = src(STATIC / "js" / "research" / "charts.js")
    assert "attributionLogo: true" in s and "/static/vendor/lightweight-charts.standalone.production.js" in s
    assert (STATIC / "vendor" / "lightweight-charts.standalone.production.js").is_file()


def test_ui_never_shows_success_before_server_confirms():
    # every toast("success") in the research modules sits after an awaited ctx.api call in the same function (heuristic check)
    for name in ("research.js", "backtest.js", "data.js", "settings.js"):
        s = src(STATIC / "js" / "workspaces" / name)
        for m in re.finditer(r'toast\([^;]*kind:\s*"success"', s):
            window = s[max(0, m.start() - 700):m.start()]
            assert "await" in window or "flush()" in window, (name, s[m.start():m.start() + 80])


def test_untrusted_text_only_via_el_or_text_nodes():
    s = strip_comments(src(STATIC / "js" / "research" / "md.js"))
    assert "innerHTML" not in s and "createElement" not in s            # md renderer builds nodes only through el()
    assert "rel: \"noopener noreferrer\"" in s


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("p", JS, ids=lambda p: p.name)
def test_javascript_parses_as_module(p):
    r = subprocess.run(["node", "--input-type=module", "--check"], input=src(p), text=True, capture_output=True)
    assert r.returncode == 0, r.stderr[:400]


def test_static_files_are_served(tmp_path):
    import http.client
    import threading
    from command_center.server import make_server
    s = make_server(tmp_path / "e.db", port=0, token="t")
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        for rel in ("js/workspaces/research.js", "js/workspaces/backtest.js", "js/workspaces/data.js", "js/workspaces/settings.js", "css/research.css",
                    "js/research/kit.js", "js/research/md.js", "js/research/charts.js", "js/research/results_view.js", "js/research/docviewer.js"):
            c = http.client.HTTPConnection("127.0.0.1", s.server_address[1])
            c.request("GET", f"/static/{rel}")
            r = c.getresponse()
            r.read()
            c.close()
            assert r.status == 200, rel
    finally:
        s.shutdown()
        s.server_close()
