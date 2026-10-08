"""Loads every workspace with the CSP ENFORCED (no bypass) and fails on any CSP violation or page error.
The other verify_*.py scripts bypass CSP only so Playwright can drive the page with string expressions."""
import glob, subprocess, sys, tempfile, time, os, signal
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8831
WS = ["command_center", "stocks", "portfolio", "agents", "research", "backtest", "data", "settings"]

def main():
    tmp = tempfile.mkdtemp()
    env = {**os.environ, "CC_DB": f"{tmp}/e.db", "CC_PORT": str(PORT), "CC_TOKEN": "t"}
    srv = subprocess.Popen([sys.executable, "-m", "command_center.server"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fails = []
    try:
        time.sleep(2)
        subprocess.run([sys.executable, "command_center/load_events.py", "--server", f"http://127.0.0.1:{PORT}", "--token", "t"], cwd=ROOT, stdout=subprocess.DEVNULL, check=True)
        exe = (glob.glob("/opt/pw-browsers/chromium*/chrome-linux*/chrome") or glob.glob("/opt/pw-browsers/chromium"))[0]
        with sync_playwright() as p:
            br = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
            ctx = br.new_context(viewport={"width": 1440, "height": 900})  # CSP enforced
            ctx.add_init_script("window.__csp=[];document.addEventListener('securitypolicyviolation',e=>window.__csp.push(e.violatedDirective+' '+e.blockedURI));")
            for w in WS:
                pg = ctx.new_page(); errs = []
                pg.on("pageerror", lambda e: errs.append(str(e)))
                pg.on("console", lambda m: errs.append(m.text) if m.type == "error" and "ontent Security" in m.text else None)
                pg.goto(f"http://127.0.0.1:{PORT}/#/{w}"); pg.wait_for_timeout(3000)
                viol = pg.evaluate("window.__csp")
                ok = not errs and not viol
                print(("PASS" if ok else "FAIL"), w, errs[:2], viol[:2])
                if not ok: fails.append(w)
                pg.close()
            br.close()
    finally:
        srv.send_signal(signal.SIGTERM)
    print("CSP check:", "all workspaces clean" if not fails else f"violations in {fails}")
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
