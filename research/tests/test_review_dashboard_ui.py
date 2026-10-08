"""Phase-4 reviewer tests (browser): XSS inertness and UI honesty. Playwright + the preinstalled Chromium; skipped if unavailable.
Server on an ephemeral port with TEMP db/dirs. Payloads are posted through the real API and rendered by the real UI."""
import glob
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pw = pytest.importorskip("playwright.sync_api")
from command_center.schemas import ROLES  # noqa: E402
from command_center.server import make_server  # noqa: E402

EXE = next(iter(sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))), None)
pytestmark = pytest.mark.skipif(EXE is None, reason="Chromium not installed")
TOKEN = "ui-review-token"
PAYLOADS = ['<img src=x onerror="window.__xss=1">', '"><svg/onload=window.__xss=2>', "<script>window.__xss=3</script>",
            "javascript:window.__xss=4", "java\nscript:window.__xss=5", "</pre><b id=pwn>pwn</b>",
            "[click](javascript:window.__xss=7) [c2](https://evil.example/a) **b** `c`"]


@pytest.fixture()
def app(tmp_path):
    s = make_server(tmp_path / "events.sqlite3", port=0, token=TOKEN)
    s.research_paths = {"experiments_dir": tmp_path / "exp", "imports_dir": tmp_path / "imp", "test_log": tmp_path / "log.jsonl"}
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s, f"http://127.0.0.1:{s.server_address[1]}"
    s.stopping = True
    s.shutdown(); s.server_close()


@pytest.fixture()
def page(app):
    with pw.sync_playwright() as p:
        b = p.chromium.launch(executable_path=EXE, args=["--no-sandbox"])
        pg = b.new_page(viewport={"width": 1500, "height": 1000})
        pg.dialogs = []
        pg.on("dialog", lambda d: (pg.dialogs.append(d.message), d.dismiss()))
        yield pg
        b.close()


def post_events(srv, evs):
    import http.client, json
    c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1])
    c.request("POST", "/api/events", json.dumps({"events": evs}), {"Content-Type": "application/json", "X-CC-Token": TOKEN})
    r = c.getresponse(); body = json.loads(r.read())
    assert r.status == 200, body


def test_event_text_renders_inert_everywhere(app, page):
    srv, base = app
    r0, r1 = list(ROLES)[:2]
    evs = []
    for i, x in enumerate(PAYLOADS):
        mk = lambda t, p, **k: evs.append({"event_type": t, "source": "review", "run_id": "xss-run", "payload": p, **k})
        mk("task.created", {"task_id": f"t{i}", "title": x, "owner": r0})
        mk("message.sent", {"sender": r0, "recipient": r1, "preview": x})
        mk("tool.activity", {"tool": x, "summary": x, "target": x, "sources": [x]}, agent_id=r0)
        mk("artifact.created", {"path": x, "title": x, "url": x}, agent_id=r0)
        mk("decision.summary", {"summary": x}); mk("incident", {"severity": "critical", "summary": x})
        mk("test.result", {"name": x, "outcome": "fail", "detail": x, "command": x})
        mk("approval.requested", {"approval_id": f"ap{i}", "summary": x, "requested_by": r0})
        mk("agent.status", {"status": "working", "task": x, "blockers": [x]}, agent_id=r1)
    post_events(srv, evs)
    page.add_init_script(f"localStorage.setItem('cc.token','{TOKEN}')")
    page.goto(base + "/#/agents"); page.wait_for_timeout(1200)
    page.query_selector("select").select_option("xss-run"); page.wait_for_timeout(1500)
    for tab in ("Task board", "Dependencies", "Event timeline"):
        try: page.click(f"[role=tab]:has-text('{tab}')", timeout=800); page.wait_for_timeout(300)
        except Exception: pass
    for sel in (".ag-node", "table tbody tr", "button:has-text('List')", "button:has-text('Replay')"):
        for e in page.query_selector_all(sel)[:6]:
            try: e.click(timeout=300)
            except Exception: pass
    for ws in ("command_center", "settings", "data", "research", "backtest"):
        page.goto(base + f"/#/{ws}"); page.wait_for_timeout(500)
    page.goto(base + "/#/agents"); page.wait_for_timeout(800)
    txt = page.evaluate("document.body.innerText")
    assert page.evaluate("window.__xss") is None and not page.dialogs
    assert page.evaluate("document.querySelectorAll('img[src=x],svg[onload],#pwn,#app script').length") == 0
    assert "onerror" in txt or "xss-run" in txt          # the payload strings were actually on screen (as text)


def test_import_preview_renders_inert(app, page):
    srv, base = app
    p = Path(srv.research_paths["imports_dir"]).parent / "<img src=x onerror=window.__xss=99>.csv"
    p.write_text('Symbol,Quantity,TradePrice,DateTime,<svg onload=window.__xss=98>\nSPY,1,100.5,2024-01-02 10:00:00,"<b id=pwn>x</b>"\nBAD<img src=x onerror=window.__xss=96>,abc,1\n')
    page.add_init_script(f"localStorage.setItem('cc.token','{TOKEN}')")
    page.goto(base + "/#/data"); page.wait_for_timeout(1200)
    page.query_selector("input[type=file]").set_input_files(str(p)); page.wait_for_timeout(1200)
    assert "<img src=x" in page.evaluate("document.body.innerText")
    assert page.evaluate("window.__xss") is None and not page.dialogs
    assert page.evaluate("document.querySelectorAll('img[src=x],svg[onload],#pwn').length") == 0


def test_el_href_filter_blocks_scheme_obfuscation(app, page):
    _, base = app
    page.goto(base + "/")
    res = page.evaluate("""async()=>{const m=await import('/static/js/core/dom.js');
      return ['JaVa\\nScRiPt:1','java\\tscript:1',' \\u0001javascript:1'].map(v=>m.el('a',{href:v}).getAttribute('href'))}""")
    assert res == [None, None, None]


def test_token_box_does_not_claim_success_for_a_wrong_token(app, page):
    _, base = app
    page.goto(base + "/#/command_center"); page.wait_for_timeout(1000)
    page.evaluate("document.getElementById('token-btn').click()"); page.wait_for_timeout(300)
    page.fill("input[aria-label='X-CC-Token']", TOKEN)                      # a correct token first: pending prefs flush, nothing left dirty
    page.click("button:has-text('Save token')"); page.wait_for_timeout(1000)
    page.fill("input[aria-label='X-CC-Token']", "definitely-wrong-token")
    page.click("button:has-text('Save token')"); page.wait_for_timeout(800)
    msg = page.evaluate("document.querySelector('.token-pop .pop-note[aria-live]').textContent")
    assert "can be saved" not in msg


def test_agents_chip_does_not_imply_running_agents(app, page):
    _, base = app
    page.goto(base + "/#/agents"); page.wait_for_timeout(2500)
    chip = page.evaluate("document.body.innerText")
    assert "Agents live" not in chip
