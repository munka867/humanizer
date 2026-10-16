"""IBKR doctor: step-by-step READ-ONLY checks with the exact fix for each failure. Makes no orders and never writes data.

    python -m command_center.ibkr.doctor [--port 7497]     # 7497 paper TWS (default), 4002 paper Gateway. Live ports 7496/4001 are always refused.
    python -m command_center.ibkr.doctor --fake            # runs the same steps against the SIMULATED IB (test fixture), to prove the tool itself works

Not yet run against a real TWS/Gateway by the author: the first real run is yours. Report any FAIL text you cannot resolve.
"""
from __future__ import annotations

import argparse
import asyncio
import socket
import sys
import time

from . import errors as E
from . import guard
from .readonly_client import ReadOnlyClient, library_info, LibraryMissing, DATA_TYPE_LABEL
from .manager import mask_account

DOCTOR_CLIENT_ID = 9102


async def run_doctor(port: int | None = None, ib_factory=None, stock_factory=None, out=print, fake=False, wait_tick: float = 8.0) -> int:
    fails = 0

    def ok(name, detail=""):
        out(f"PASS  {name}" + (f" - {detail}" if detail else ""))

    def bad(name, why, fix):
        nonlocal fails
        fails += 1
        out(f"FAIL  {name} - {why}\n      FIX: {fix}")

    out("IBKR doctor (read-only). No orders are placed; nothing is written." + ("  [SIMULATED IB: test fixture]" if fake else ""))
    out(f"Python {sys.version.split()[0]}")
    if not fake:
        lib = library_info()
        if lib["installed"]:
            ok("ib_async installed", f"version {lib['version']}")
        else:
            bad("ib_async installed", lib["message"], "python -m pip install ib_async==2.1.0   (then run the doctor again)")
            return 1
    else:
        ok("ib_async installed", "skipped in simulated mode")
    try:
        kind = guard.kind_for_port(port if port else 7497)
        target = guard.resolve_target(kind, client_id=DOCTOR_CLIENT_ID)
        ok("port allowed by the safety guard", f"{target.label}, clientId {target.client_id}, read-only")
    except guard.GuardError as e:
        bad("port allowed by the safety guard", str(e), "use 7497 (TWS paper) or 4002 (Gateway paper); live ports are never allowed")
        return 1
    if not fake:
        s = socket.socket()
        s.settimeout(2.0)
        try:
            s.connect((target.host, target.port))
            ok("port reachable", f"something is listening on {target.host}:{target.port}")
        except OSError as e:
            bad("port reachable", f"nothing is listening on {target.host}:{target.port} ({e})", E.explain(502)["fix"])
            return 1
        finally:
            s.close()
    client = ReadOnlyClient(target, ib=ib_factory() if ib_factory else None, stock_factory=stock_factory, source="fake" if fake else "ibkr")
    try:
        try:
            await client.connect(8.0)
            ok("connect (read-only API client)", "handshake completed")
        except Exception as e:  # noqa: BLE001
            d = E.explain_exception(e, target.port)
            bad("connect (read-only API client)", f"{d['title']}: {d['message']}", d["fix"])
            return 1
        try:
            guard.check_paper_accounts(client.managed_accounts())
            ok("paper account identity", f"all managed account ids start with DU ({', '.join(mask_account(a) for a in client.managed_accounts())})")
        except guard.AccountIdentityError as e:
            bad("paper account identity", str(e), "quit TWS/Gateway and log in with your PAPER username (ids start with DU); nothing else was read")
            return 1
        try:
            t = await client.server_time()
            ok("server time", t.isoformat(timespec="seconds"))
        except Exception as e:  # noqa: BLE001
            bad("server time", str(e), "TWS connected but did not answer; restart TWS/Gateway and retry")
        inst = None
        try:
            inst = await client.qualify_stock("SPY", "ARCA", "USD")
            ok("contract qualifies (SPY)", f"conId {inst['conid']}, exchange {inst['exchange']}")
        except Exception as e:  # noqa: BLE001
            bad("contract qualifies (SPY)", str(e), E.explain(200)["fix"])
        if inst:
            try:
                client.set_market_data_type(3)
                client.subscribe_quote(inst)
                t0 = time.time()
                q = None
                while time.time() - t0 < wait_tick:
                    q = client.quote(inst["conid"])
                    if q and (q.last is not None or q.bid is not None):
                        break
                    await asyncio.sleep(0.25)
                if q and (q.last is not None or q.bid is not None):
                    ok("market-data type and a quote", f"tick type: {q.data_type_label} (1 live, 2 frozen, 3 delayed, 4 delayed frozen); last={q.last}")
                else:
                    benign = {2104, 2106, 2107, 2108, 2119, 2158}
                    seen = [(c, t) for (_, _, c, t) in client.errors if c not in benign]
                    for c, t in seen[-6:]:
                        print(f"      TWS message {c}: {t[:240]}")
                    codes = sorted({c for (_, _, c, _) in client.errors if c in (354, 10167, 10186, 10197)})
                    d = E.explain(codes[0]) if codes else None
                    bad("market-data type and a quote", f"no tick within {wait_tick:.0f}s" + (f" (TWS said {codes[0]}: {d['title']})" if d else " (the market may be closed, or no data permission)"),
                        d["fix"] if d else "Outside market hours TWS may send nothing; try during US market hours, or request frozen data. See docs/IBKR_SETUP.md section 'Market data'.")
            except Exception as e:  # noqa: BLE001
                bad("market-data type and a quote", str(e), "see docs/IBKR_SETUP.md")
            try:
                import datetime as dt
                bars, errs = await client.historical_bars(inst, None, "5 D", "1 day", True)
                if bars:
                    ok("1 daily bar (history)", f"{len(bars)} daily bars, newest {bars[-1]['time']}")
                else:
                    d = E.explain(errs[0][0], errs[0][1]) if errs else None
                    bad("1 daily bar (history)", "no bars returned" + (f" ({d['title']})" if d else ""), d["fix"] if d else "Check market-data permissions and that the historical-data farm is connected (message 2106).")
            except Exception as e:  # noqa: BLE001
                bad("1 daily bar (history)", str(e), "see docs/IBKR_SETUP.md")
        try:
            rows = await client.account_summary()
            acct = (client.managed_accounts() or [None])[0]
            if rows:
                ok("account summary readable", f"{len(rows)} values for account {mask_account(acct)} (values not printed)")
            else:
                bad("account summary readable", "TWS returned no account values", "Wait a few seconds after login and retry; make sure you are logged in to the account, not just the login screen.")
        except Exception as e:  # noqa: BLE001
            bad("account summary readable", str(e), "see docs/IBKR_SETUP.md")
    finally:
        try:
            client.disconnect()
        except Exception:
            pass
    out("\nRESULT: " + ("all checks passed" if not fails else f"{fails} check(s) failed: fix the first FAIL above and run again"))
    return 0 if not fails else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read-only IBKR connection doctor")
    ap.add_argument("--port", type=int, default=None, help="7497 TWS paper (default), 4002 Gateway paper")
    ap.add_argument("--fake", action="store_true", help="run against the SIMULATED IB (test fixture)")
    a = ap.parse_args(argv)
    ibf = sf = None
    if a.fake:
        from .fake_ib import FakeIB, FakeStock
        ibf, sf = FakeIB, FakeStock
    return asyncio.run(run_doctor(a.port, ibf, sf, fake=a.fake))


if __name__ == "__main__":
    sys.exit(main())
