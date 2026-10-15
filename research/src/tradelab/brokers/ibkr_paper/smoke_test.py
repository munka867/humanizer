"""PAPER smoke test - RUN BY THE USER on their own computer. Places ONE tiny buy and the offsetting sell in the user's TWS PAPER account.

    python -m tradelab.brokers.ibkr_paper.smoke_test

Needs: TWS paper login with the API enabled (docs/PAPER_SETUP.md), ib_async==2.1.0, CC_APPROVAL_KEY in the environment, a signed approval
record for strategy SMOKE_TEST version 1 / SPY (conId 756733) made with `python -m tradelab.execution.approve`, and
configs/risk_policy.smoke.yaml. You must type the exact phrase  I PLACE A PAPER ORDER  before anything is sent.
Hard limits that no file can raise: 1 share, SPY only, paper ports 7497/4002 only, DU accounts only, regular hours only, no shorting.
Exit codes: 0 all steps PASS | 1 failure/anomaly | 2 failure WITH possible residual position (check TWS) | 3 not confirmed, nothing sent | 4 refused before connecting"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from tradelab.brokers.ibkr_paper import guard as G
from tradelab.brokers.ibkr_paper.adapter import AdapterUnavailable, ContractSpec, IbkrPaperBroker
from tradelab.execution.common import D, sha256_hex, utcnow
from tradelab.execution.intents import ENTRY, EXIT, Proposal
from tradelab.execution.modes import KEY_ENV, Mode, ModeDenied, verify_approval
from tradelab.execution import orders as O
from tradelab.execution.risk import RiskEngine, load_policy
from tradelab.execution.service import ExecutionService

ROOT = Path(__file__).resolve().parents[4]
SMOKE_STRATEGY_ID, SMOKE_VERSION = "SMOKE_TEST", "1"
SPY_CON_ID, SYMBOL, MAX_QTY = 756733, "SPY", Decimal(1)       # HARD-CODED: a signed approval cannot scale these up
MAX_GROSS_CAP = Decimal(2000)
CONFIRM_PHRASE = "I PLACE A PAPER ORDER"


def smoke_registry(strategy_id: str, version: str):
    """Registry entry that exists ONLY for this test (and still needs the signed approval to matter)."""
    return "approved_paper" if (strategy_id, version) == (SMOKE_STRATEGY_ID, SMOKE_VERSION) else None


PLAN = f"""
PAPER SMOKE TEST - what this will do
  1. Check your signed approval, the smoke risk policy and the hard limits (1 share, SPY conId {SPY_CON_ID}, paper only).
  2. Connect to TWS PAPER on this computer (127.0.0.1, port 7497 or 4002, clientId {G.CLIENT_ID}); refuse unless every account id starts with DU
     and matches the approved account.
  3. Read a live SPY quote and your account; reconcile (must be clean and flat).
  4. Show you the exact orders and ask you to type:  {CONFIRM_PHRASE}
  5. Place ONE buy of 1 share SPY (limit a few cents above the ask, day order, regular hours), wait for the fill report,
     then place the offsetting sell of 1 share (limit a few cents below the bid) and wait for its fill.
  6. Print an audit trail and PASS/FAIL for every step. On any problem: cancel its own working orders, then flatten ONLY what it created.
It never trades anything except 1 share of SPY and never touches a live account or a live port.
"""


class Steps:
    def __init__(self, out):
        self.out, self.rows = out, []

    def add(self, name, ok, detail=""):
        self.rows.append((name, ok, detail))
        self.out(f"  [{'PASS' if ok else 'FAIL' if ok is False else 'SKIP'}] {name}" + (f" - {detail}" if detail else ""))
        return ok

    @property
    def all_pass(self):
        return all(r[1] is not False for r in self.rows)


def run(argv=None, *, ib_module=None, input_fn=input, env=None, clock=utcnow, sleep=time.sleep, out=print) -> int:
    ap = argparse.ArgumentParser(prog="python -m tradelab.brokers.ibkr_paper.smoke_test", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=7497, choices=list(G.PAPER_PORTS), help="7497 = TWS paper (default), 4002 = Gateway paper")
    ap.add_argument("--approval", default=str(ROOT / "configs" / "approvals" / "paper_approval.json"))
    ap.add_argument("--policy", default=str(ROOT / "configs" / "risk_policy.smoke.yaml"))
    ap.add_argument("--ledger", default=str(Path.home() / ".tradelab_smoke" / "ledger.db"))
    ap.add_argument("--fill-timeout", type=int, default=30)
    ap.add_argument("--offset-cents", type=int, default=3, choices=range(1, 11))
    a = ap.parse_args(argv)
    env = os.environ if env is None else env
    out(PLAN)
    steps = Steps(out)

    # ---- 1. preconditions (nothing connected yet)
    key = env.get(KEY_ENV)
    if not steps.add("approval key present in environment", bool(key), f"set {KEY_ENV} (see docs/PAPER_SETUP.md)" if not key else ""):
        return 4
    try:
        pol = load_policy(a.policy)
        ok = (set(pol.instruments) == {SPY_CON_ID} and pol.instruments[SPY_CON_ID].max_position_qty <= MAX_QTY and not pol.allow_short
              and not pol.instruments[SPY_CON_ID].allow_short and pol.max_gross_exposure <= MAX_GROSS_CAP)
        steps.add("risk policy within the hard-coded smoke caps", ok, "" if ok else "policy exceeds 1 share / SPY only / no shorting / gross cap")
        if not ok:
            return 4
        approval = verify_approval(Path(a.approval), key.encode(), now_utc=clock())
        bound = (approval["strategy_id"], approval["strategy_version"]) == (SMOKE_STRATEGY_ID, SMOKE_VERSION) and approval["instruments"] == [SPY_CON_ID]
        bound = bound and approval["account_id"].startswith(G.PAPER_PREFIX) and approval["risk_policy_sha256"] == sha256_hex(Path(a.policy).read_bytes())
        steps.add("signed approval valid and bound to SMOKE_TEST v1 / SPY / this policy / a DU account", bound,
                  f"account {approval['account_id']}, expires {approval['expires_utc']}")
        if not bound:
            return 4
    except (ModeDenied, OSError, ValueError, KeyError) as e:
        steps.add("signed approval valid", False, str(e))
        return 4

    # ---- 2. connect (guard enforces loopback, paper port, clientId, DU accounts, approved account)
    adapter = IbkrPaperBroker({SPY_CON_ID: ContractSpec(SYMBOL, "USD", "SMART", "ARCA")}, ib_module=ib_module, port=a.port,
                              expected_account=approval["account_id"], clock=clock)
    try:
        accounts = adapter.connect()
    except (G.GuardError, AdapterUnavailable) as e:
        steps.add("connect to TWS paper with guard checks", False, str(e))
        return 4
    except Exception as e:                                   # TWS not running / API not enabled
        steps.add("connect to TWS paper with guard checks", False, f"{type(e).__name__}: {e}. Is TWS (paper) running with the API enabled?")
        return 4
    steps.add("connect to TWS paper with guard checks", True, f"port {a.port}, clientId {G.CLIENT_ID}, accounts {accounts}")
    svc = None
    try:
        # ---- 3. quote + account
        q = adapter.quote(SPY_CON_ID)
        if q is None or q.bid is None or q.ask is None:
            steps.add("live SPY bid/ask available", False, "no live quote (market data subscription / market closed?). Nothing was sent.")
            return 1
        age = (clock() - q.ts).total_seconds()
        if not steps.add("SPY quote is fresh", age <= pol.max_data_age_seconds, f"age {age:.0f}s (limit {pol.max_data_age_seconds}s; delayed data is refused)"):
            return 1
        off = Decimal(a.offset_cents) / 100
        buy_px, sell_px = (q.ask + off).quantize(Decimal("0.01")), (q.bid - off).quantize(Decimal("0.01"))
        info = adapter.account()
        steps.add("paper account readable", True, f"{info.account_id} equity {info.equity} available {info.available_funds}")
        out(f"\nORDERS THAT WILL BE SENT (account {info.account_id}):\n  BUY  1 {SYMBOL} LMT {buy_px} DAY (regular hours)\n"
            f"  SELL 1 {SYMBOL} LMT {sell_px} DAY (only after the buy has filled)")
        if input_fn(f"\nType exactly  {CONFIRM_PHRASE}  to continue, anything else aborts: ").strip() != CONFIRM_PHRASE:
            out("Not confirmed. NOTHING was sent.")
            return 3

        # ---- 4. service
        Path(a.ledger).parent.mkdir(parents=True, exist_ok=True)
        svc = ExecutionService(a.ledger, Mode.PAPER, adapter, RiskEngine(pol), lambda cid: adapter.quote(cid) if cid == SPY_CON_ID else None,
                               clock, registry_state=smoke_registry, risk_policy_path=Path(a.policy), approval_path=Path(a.approval),
                               env=env, correlations={}, margin_hook=adapter.margin_hook)
        rep = svc.start()
        if not steps.add("startup reconciliation clean", rep["ok"], "; ".join(rep["findings"])):
            return 1
        if not steps.add("flat at start (ledger and broker)", not svc.positions() and not adapter.positions()):
            return 1

        def wait(pred, timeout):
            t = 0.0
            while t <= timeout:
                svc.process_events()
                if pred():
                    return True
                sleep(0.5)
                t += 0.5
            return False

        stamp = clock().strftime("%Y%m%dT%H%M%S")
        # ---- 5. buy
        r = svc.propose(Proposal(SMOKE_STRATEGY_ID, SMOKE_VERSION, f"smoke-{stamp}-buy", SPY_CON_ID, SYMBOL, 1, MAX_QTY, "LMT", limit_px=buy_px,
                                 stop_px=buy_px - Decimal(1), tif="DAY", outside_rth=False, purpose=ENTRY))
        if not steps.add("buy order accepted by risk, mode gate and broker port", r.status == "SUBMITTED", f"{r.status} {list(r.reasons)}"):
            return _abort(svc, steps, out, sleep, wait)
        coid = r.client_order_id
        acked = wait(lambda: svc.orders.get(coid)["state"] not in (O.SUBMITTING,), 10)
        steps.add("buy acknowledged by TWS (ack is not a fill)", acked and svc.orders.get(coid)["state"] in (O.ACKED, O.PARTIAL, O.FILLED), svc.orders.get(coid)["state"])
        filled = wait(lambda: svc.orders.get(coid)["state"] == O.FILLED, a.fill_timeout)
        if not steps.add("buy filled (position taken from the fill report)", filled and svc.positions() == {SPY_CON_ID: MAX_QTY},
                         f"state {svc.orders.get(coid)['state']}, ledger position {svc.positions()}"):
            return _abort(svc, steps, out, sleep, wait)
        # ---- 6. sell
        r2 = svc.propose(Proposal(SMOKE_STRATEGY_ID, SMOKE_VERSION, f"smoke-{stamp}-sell", SPY_CON_ID, SYMBOL, -1, MAX_QTY, "LMT", limit_px=sell_px,
                                  tif="DAY", outside_rth=False, purpose=EXIT))
        if not steps.add("offsetting sell accepted", r2.status == "SUBMITTED", f"{r2.status} {list(r2.reasons)}"):
            return _abort(svc, steps, out, sleep, wait)
        c2 = r2.client_order_id
        done = wait(lambda: svc.orders.get(c2)["state"] == O.FILLED, a.fill_timeout)
        if not steps.add("sell filled", done and not svc.positions(), f"state {svc.orders.get(c2)['state']}, ledger position {svc.positions()}"):
            return _abort(svc, steps, out, sleep, wait)
        # ---- 7. verify
        steps.add("flat at the end (ledger and broker)", not svc.positions() and not adapter.positions(), f"broker {adapter.positions()}")
        bad = [i for i in svc.incidents() if i["severity"] in ("high", "critical")]
        steps.add("no high-severity incidents", not bad, f"{len(bad)} open")
        _audit(svc, adapter, out)
        out("\nRESULT: " + ("ALL STEPS PASSED (this proves only the plumbing on a paper account, nothing about any strategy)" if steps.all_pass else "FAILED"))
        return 0 if steps.all_pass else 1
    except Exception as e:                                    # any anomaly: clean up, never swallow silently
        steps.add("unexpected error", False, f"{type(e).__name__}: {e}")
        if svc is not None:
            return _abort(svc, steps, out, sleep, lambda pred, t: _wait_basic(svc, sleep, pred, t))
        return 1
    finally:
        try:
            adapter.disconnect()
        except Exception:
            pass
        if svc is not None:
            svc.close()          # releases the ledger and per-account locks


def _wait_basic(svc, sleep, pred, timeout):
    t = 0.0
    while t <= timeout:
        svc.process_events()
        if pred():
            return True
        sleep(0.5)
        t += 0.5
    return False


def _abort(svc, steps, out, sleep, wait) -> int:
    """Cancel this strategy's own working orders, then flatten ONLY the position it created. Never claims success it cannot see."""
    out("\nABORT: cancelling the smoke test's own working orders, then flattening only what it created.")
    try:
        pending = svc.cancel_working_orders(SMOKE_STRATEGY_ID)
        settled = wait(lambda: not svc.orders.by_state(O.ACKED, O.PARTIAL, O.CANCEL_PENDING, O.SUBMITTING, O.UNCERTAIN), 10) if pending else True
        pos = svc.orders.positions(SMOKE_STRATEGY_ID)
        if pos and settled:
            rep = svc.flatten_strategy_positions(SMOKE_STRATEGY_ID)
            wait(lambda: not svc.orders.positions(SMOKE_STRATEGY_ID), 20)
            out(f"  flatten report: complete={rep['complete']} residual (at report time)={rep['residual_exposure']}")
        elif pos:
            out("  could not confirm the cancels; NOT sending a flatten order blindly (it could double-close).")
    except Exception as e:
        out(f"  cleanup error: {type(e).__name__}: {e}")
    residual = svc.orders.positions(SMOKE_STRATEGY_ID)
    uncertain = svc.orders.by_state(O.UNCERTAIN)
    unresolved = svc.orders.by_state(O.ACKED, O.PARTIAL, O.CANCEL_PENDING, O.SUBMITTING)
    _audit(svc, None, out)
    if residual or uncertain or unresolved:
        out(f"\nRESULT: FAILED WITH POSSIBLE RESIDUAL EXPOSURE (ledger position {dict(residual)}, uncertain orders {len(uncertain)}, "
            f"working orders {len(unresolved)}). OPEN TWS NOW and check positions and orders in the PAPER account.")
        return 2
    out("\nRESULT: FAILED (position is flat per the ledger). Read the FAIL lines above.")
    return 1


def _audit(svc, adapter, out):
    out("\nAUDIT TRAIL")
    for r in svc.conn.execute("SELECT intent_id,signal_id,side,qty,order_type,limit_px,purpose,status FROM intents ORDER BY created_utc"):
        out(f"  intent {r['intent_id']} {r['signal_id']} side={r['side']} qty={r['qty']} {r['order_type']} lmt={r['limit_px']} {r['purpose']} status={r['status']}")
    for r in svc.conn.execute("SELECT client_order_id,broker_order_id,state,qty,filled_qty FROM orders ORDER BY order_id"):
        out(f"  order {r['client_order_id']} broker_id={r['broker_order_id']} state={r['state']} qty={r['qty']} filled={r['filled_qty']}")
    for r in svc.conn.execute("SELECT exec_id,side,qty,price,ts FROM executions ORDER BY ts"):
        com = adapter.commissions.get(r["exec_id"]) if adapter else None
        out(f"  fill {r['exec_id']} side={r['side']} qty={r['qty']} price={r['price']} commission={com[0] if com else 'n/a'} {com[1] if com else ''}")
    for i in svc.incidents(open_only=False):
        out(f"  incident #{i['id']} {i['kind']} [{i['severity']}] {i['detail']}")
    if adapter:
        for d in adapter.diagnostics:
            out(f"  adapter {d}")


def main():
    sys.exit(run())


if __name__ == "__main__":
    main()
