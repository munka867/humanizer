"""ExecutionService: the single execution authority.

submit path:  strategy proposal -> (controls) -> risk engine -> intent ledger -> [mode gate] -> broker port
Startup:      reconcile() FIRST; any unknown/mismatched broker state blocks new entries (protective orders are never cancelled
              by reconciliation) and raises an incident.
Controls:     pause_new_entries, cancel_entry_orders, flatten_strategy_positions, emergency_stop - semantics in docs/EXECUTION_DESIGN.md.
No method here is reachable from a strategy: strategies only ever see bars."""
from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Callable

from tradelab.execution import orders as O
from tradelab.execution.broker import (BrokerDisconnected, BrokerEvent, BrokerPort, BrokerRefused, BrokerTimeout, OrderRequest)
from tradelab.execution.common import D, canon, iso, sha256_hex
from tradelab.execution.db import kv_get, kv_set, open_db, tx
from tradelab.execution.intents import ENTRY, EXIT, PROTECTIVE, IntentLedger, Proposal, make_intent_id
from tradelab.execution.modes import KEY_ENV, Mode, ModeDenied, assert_may_submit, verify_approval
from tradelab.execution.permit import PermitAuthority
from tradelab.execution.risk import Quote, RiskDecision, RiskEngine, RiskInputs, is_reducing


class WriterLockHeld(RuntimeError):
    pass


@dataclass
class SubmitResult:
    intent_id: str
    status: str                 # SUBMITTED | SHADOWED | BLOCKED | DUPLICATE | UNCERTAIN | REJECTED
    reasons: tuple = ()
    duplicate: bool = False
    client_order_id: str | None = None
    decision: RiskDecision | None = None


class ExecutionService:
    def __init__(self, db_path: str, mode: Mode, broker: BrokerPort, risk: RiskEngine, quote_source: Callable[[int], Quote | None],
                 clock: Callable[[], datetime], *, registry_state: Callable | None = None, risk_policy_path: Path | None = None,
                 approval_path: Path | None = None, env=None, correlations=None, margin_hook: Callable | None = None,
                 ack_timeout_seconds: int = 10, lock_path: str | None = None, lock_dir: str | None = None, max_clock_skew_seconds: int = 120):
        mode = Mode(mode)
        if mode is Mode.LIVE:
            raise ModeDenied("LIVE is refused unconditionally")
        if mode is Mode.PAPER and db_path == ":memory:":
            raise ModeDenied("PAPER refused: a persistent, file-backed ledger is required (':memory:' forgets intents on restart)")
        self.__mode = mode                      # name-mangled, exposed read-only
        self.broker, self.risk, self.quote_source, self.clock = broker, risk, quote_source, clock
        self._registry_state, self._policy_path, self._approval_path, self._env = registry_state, risk_policy_path, approval_path, env
        self._corr, self._margin_hook, self._ack_timeout = correlations, margin_hook, ack_timeout_seconds
        self._lock_fh, self._acct_lock_fh = None, None
        self._lock_dir, self._max_skew = lock_dir, max_clock_skew_seconds
        key = (env if env is not None else os.environ).get(KEY_ENV)
        self.__permits = PermitAuthority(key.encode() if key else None)    # private capability: signs permits, tags/verifies orderRefs
        self._paper_block: str | None = None
        self._approved_account: str | None = None
        if mode is Mode.PAPER:
            self._paper_block = self._check_paper_binding()
        if db_path != ":memory:":
            self._acquire_lock(lock_path or db_path + ".lock")
            self._acquire_account_lock()
        self.conn = open_db(db_path)
        if hasattr(broker, "bind_authority"):       # real adapters only act for THIS service (one-shot binding)
            broker.bind_authority(self.__permits.consume, self.__permits.make_ref, self.__permits.parse_ref)
        self.intents = IntentLedger(self.conn)
        self.orders = O.OrderBook(self.conn, self.incident)
        self.reconciled = False

    # ------------------------------------------------------------ identity / lock
    @property
    def mode(self) -> Mode:
        return self.__mode

    def _acquire_lock(self, path):
        fh = open(path, "a+")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            fh.close()
            raise WriterLockHeld(f"another execution service holds {path}") from e
        fh.seek(0); fh.truncate(); fh.write(str(os.getpid())); fh.flush()
        self._lock_fh = fh

    def _check_paper_binding(self) -> str | None:
        """Construction-time PAPER check: the approval must verify and bind the policy the RiskEngine actually enforces. Returns a
        reason string (and the service then refuses every order) instead of raising, so the operator sees an incident, not a crash."""
        try:
            key = (self._env if self._env is not None else os.environ).get(KEY_ENV)
            if not key or self._approval_path is None:
                raise ModeDenied("PAPER refused: approval key or approval path missing")
            payload = verify_approval(Path(self._approval_path), key.encode(), now_utc=self.clock())
            if payload["risk_policy_sha256"] != self.risk.policy.sha256:
                raise ModeDenied("PAPER refused: the RiskEngine's policy sha256 differs from the approved policy")
            self._approved_account = payload["account_id"]
            return None
        except ModeDenied as e:
            return str(e)

    def _acquire_account_lock(self):
        """One writer per broker ACCOUNT (not only per ledger file): lock file named by the account id, in the user's home dir."""
        acct = self._approved_account
        if acct is None:
            try:
                acct = self.broker.account().account_id
            except Exception:
                acct = None
        if acct is None:
            if self.mode is Mode.PAPER:
                raise ModeDenied("PAPER refused: cannot determine the account id for the per-account lock")
            return
        simulated = bool(getattr(self.broker, "is_simulated", False)) and self.mode is not Mode.PAPER     # P7-05: PAPER ignores the claim
        d = Path(self._lock_dir or (self._env if self._env is not None else os.environ).get("TRADELAB_LOCK_DIR") or
                 (Path(tempfile.gettempdir()) / "tradelab_sim_locks" if simulated else Path.home() / ".tradelab_locks"))
        d.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", f"account_{acct}") + (f"_{os.getpid()}_{id(self.broker)}" if simulated else "") + ".lock"
        fh = open(d / name, "a+")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            fh.close()
            raise WriterLockHeld(f"another execution service already drives account {acct} ({d / name})") from e
        fh.seek(0); fh.truncate(); fh.write(str(os.getpid())); fh.flush()
        self._acct_lock_fh = fh

    def close(self):
        for attr in ("_lock_fh", "_acct_lock_fh"):
            fh = getattr(self, attr)
            if fh:
                fcntl.flock(fh, fcntl.LOCK_UN)
                fh.close()
                setattr(self, attr, None)
        self.conn.close()

    # ------------------------------------------------------------ persisted control state
    def incident(self, kind, severity="high", detail=None):
        self.conn.execute("INSERT INTO incidents(ts,kind,severity,detail) VALUES(?,?,?,?)",
                          (iso(self.clock()), kind, severity, canon(detail or {})))

    def incidents(self, open_only=True):
        q = "SELECT * FROM incidents" + (" WHERE resolved_ts IS NULL" if open_only else "") + " ORDER BY id"
        return [dict(r) for r in self.conn.execute(q)]

    @property
    def paused(self) -> bool:
        return bool(kv_get(self.conn, "paused_new_entries", False))

    @property
    def emergency(self) -> bool:
        return bool(kv_get(self.conn, "emergency_stop", False))

    @property
    def entries_blocked_by_reconciliation(self) -> bool:
        return (not self.reconciled) or bool(kv_get(self.conn, "recon_blocked", False))

    def positions(self) -> dict[int, Decimal]:
        return self.orders.positions()

    # ------------------------------------------------------------ reconciliation
    def reconcile(self) -> dict:
        """Compare broker open orders / executions / positions with the ledger. It never cancels anything on its own account; the only
        cancels it may re-send are those that complete an operator-requested flatten (and only if the exit is still allowed)."""
        now = self.clock()
        found = []
        try:
            b_orders, b_exec, b_pos = self.broker.open_orders(), self.broker.executions(), self.broker.positions()
        except (BrokerDisconnected, BrokerTimeout) as e:
            self.incident("RECONCILE_BROKER_UNREACHABLE", "high", {"error": str(e)})
            kv_set(self.conn, "recon_blocked", True)
            self.reconciled = True
            return {"ok": False, "findings": ["broker unreachable"]}
        # executions first: fills are truth
        for ev in b_exec:
            if self.orders.get(ev.client_order_id) is None:
                if self.conn.execute("SELECT 1 FROM executions WHERE exec_id=?", (ev.exec_id,)).fetchone() is None:
                    found.append(f"unknown execution {ev.exec_id}")
                    self.incident("UNKNOWN_BROKER_EXECUTION", "high", {"exec_id": ev.exec_id, "client_order_id": ev.client_order_id})
                continue
            self.orders.apply_execution(self._ev_to_exec(ev), now)
        by_coid = {o.client_order_id: o for o in b_orders}
        # P7-07: a ledger order whose broker order id is live at the broker but whose orderRef we cannot verify (e.g. the approval key was
        # rotated) is treated as LIVE/unknown, never as cancelled: it keeps its capacity and entries stay blocked until the operator resolves it.
        foreign_by_bid = {o.broker_order_id: o for o in b_orders if o.client_order_id.startswith("FOREIGN-")}
        matched_foreign: set = set()
        for row in self.orders.by_state(O.SUBMITTING, O.UNCERTAIN, O.ACKED, O.PARTIAL, O.CANCEL_PENDING):
            coid = row["client_order_id"]
            bo = by_coid.get(coid)
            fresh = self.orders.get(coid)["state"]
            if fresh in O.TERMINAL:
                continue
            if bo is None and row["broker_order_id"] and row["broker_order_id"] in foreign_by_bid:
                matched_foreign.add(row["broker_order_id"])
                found.append(f"order {coid} is live at the broker but its orderRef is unverifiable (approval key rotated?)")
                self.incident("ORDER_REF_UNVERIFIABLE", "high", {"client_order_id": coid, "broker_order_id": row["broker_order_id"]})
                continue
            if bo is not None:
                if fresh in (O.SUBMITTING, O.UNCERTAIN):
                    self.orders.transition(coid, O.ACKED, now, "reconciled: found working at broker", bo.broker_order_id)
                    if fresh == O.UNCERTAIN:
                        self.incident("UNCERTAIN_RESOLVED_WORKING", "low", {"client_order_id": coid})
            else:
                filled = D(self.orders.get(coid)["filled_qty"])
                if filled >= D(row["qty"]):
                    continue
                if fresh == O.SUBMITTING:
                    self.orders.transition(coid, O.UNCERTAIN, now, "reconcile: outcome of submit unknown")
                self.orders.transition(coid, O.CANCELED, now, "reconciled: not working at broker")
                self.incident("ORDER_NOT_AT_BROKER", "medium", {"client_order_id": coid, "was": fresh})
        foreign_live = []
        for o in b_orders:
            if self.orders.get(o.client_order_id) is None and o.broker_order_id not in matched_foreign:
                rem = D(o.qty) - D(o.filled)
                if rem > 0:     # P7-10: an unknown working order may still fill; it counts as live capacity/exposure
                    foreign_live.append({"con_id": int(o.con_id), "side": int(o.side), "rem": str(rem)})
                found.append(f"unknown broker order {o.broker_order_id}")
                self.incident("UNKNOWN_BROKER_ORDER", "high", {"broker_order_id": o.broker_order_id, "con_id": o.con_id})
        ledger_pos = self.orders.positions()
        mism = []
        for cid in set(ledger_pos) | set(b_pos):
            if ledger_pos.get(cid, Decimal(0)) != b_pos.get(cid, Decimal(0)):
                mism.append(cid)
                found.append(f"position mismatch {cid}")
                self.incident("POSITION_MISMATCH", "high", {"con_id": cid, "ledger": str(ledger_pos.get(cid, 0)), "broker": str(b_pos.get(cid, 0))})
        if self.orders.by_state(O.UNCERTAIN):
            found.append("uncertain orders remain")
        corr = list(getattr(self.broker, "unresolved_corrections", None) or [])
        if corr:
            found.append("execution corrections need human review")
            self.incident("EXECUTION_CORRECTION_REVIEW", "high", {"exec_ids": corr[:10]})
        kv_set(self.conn, "foreign_live", foreign_live)
        kv_set(self.conn, "mismatch_cons", sorted(mism))
        kv_set(self.conn, "ack_cons", [c for c in kv_get(self.conn, "ack_cons", []) if c in mism])
        kv_set(self.conn, "recon_blocked", bool(found))
        self.reconciled = True
        self._retry_flatten_cancels()
        return {"ok": not found, "findings": found}

    def start(self) -> dict:
        """Startup sequence: reconciliation FIRST. No proposal is accepted for new exposure before it has run."""
        return self.reconcile()

    @staticmethod
    def _ev_to_exec(ev: BrokerEvent) -> dict:
        return {"exec_id": ev.exec_id, "client_order_id": ev.client_order_id, "broker_order_id": ev.broker_order_id,
                "con_id": ev.con_id, "side": ev.side, "qty": ev.qty, "price": ev.price, "ts": ev.ts}

    # ------------------------------------------------------------ broker events
    def process_events(self, _resume: bool = True) -> int:
        now = self.clock()
        try:
            events = self.broker.poll_events()
        except (BrokerDisconnected, BrokerTimeout):
            self.incident("POLL_FAILED", "medium", {})
            return 0
        for ev in events:
            if ev.kind == "resync_required":
                self.incident("BROKER_RESYNC_REQUIRED", "high", {"reason": ev.reason})
                kv_set(self.conn, "recon_blocked", True)
                continue
            row = self.orders.get(ev.client_order_id)
            if row is None:
                self.incident("EVENT_UNKNOWN_ORDER", "high", {"kind": ev.kind, "client_order_id": ev.client_order_id})
                kv_set(self.conn, "recon_blocked", True)
                continue
            st = row["state"]
            try:
                if ev.kind == "fill":
                    self.orders.apply_execution(self._ev_to_exec(ev), now)
                elif ev.kind == "ack":
                    if st in (O.SUBMITTING, O.UNCERTAIN):
                        self.orders.transition(ev.client_order_id, O.ACKED, now, "broker ack", ev.broker_order_id)
                elif ev.kind == "reject":
                    if st not in O.TERMINAL:
                        self.orders.transition(ev.client_order_id, O.REJECTED, now, ev.reason or "broker reject", ev.broker_order_id)
                        self.intents.set_status(row["intent_id"], "REJECTED")
                elif ev.kind == "cancel_ack":
                    if st in (O.CANCEL_PENDING, O.ACKED, O.PARTIAL, O.UNCERTAIN):
                        self.orders.transition(ev.client_order_id, O.CANCELED, now, "cancel ack")
                elif ev.kind == "uncertain":
                    if st not in O.TERMINAL and st != O.UNCERTAIN:
                        self.orders.transition(ev.client_order_id, O.UNCERTAIN, now, "broker signal ambiguous: " + ev.reason)
                        self.incident("ORDER_UNCERTAIN", "high", {"client_order_id": ev.client_order_id, "reason": ev.reason})
                elif ev.kind == "cancel_reject":
                    if st == O.CANCEL_PENDING:
                        filled = D(row["filled_qty"])
                        self.orders.transition(ev.client_order_id, O.PARTIAL if filled > 0 else O.ACKED, now, "cancel rejected: " + ev.reason)
            except O.IllegalTransition as e:
                self.incident("ILLEGAL_TRANSITION", "medium", {"error": str(e), "kind": ev.kind})
        self._cancel_orphan_protectives()
        if _resume:
            self._resume_pending_flattens(throttle=True)
        return len(events)

    def check_timeouts(self) -> list[str]:
        """SUBMITTING with no ack for ack_timeout seconds -> UNCERTAIN (never blindly retried)."""
        now, out = self.clock(), []
        for row in self.orders.by_state(O.SUBMITTING):
            age = (now - datetime.fromisoformat(row["updated_utc"])).total_seconds()
            if age >= self._ack_timeout:
                self.orders.transition(row["client_order_id"], O.UNCERTAIN, now, "ack timeout")
                self.incident("ORDER_UNCERTAIN", "high", {"client_order_id": row["client_order_id"]})
                out.append(row["client_order_id"])
        return out

    def _cancel_orphan_protectives(self):
        """When a strategy's position in an instrument is flat, its still-working protective orders would open a NEW position if hit."""
        pos = self.orders.positions()
        for row in self.orders.by_state(O.ACKED, O.PARTIAL):
            if row["purpose"] == PROTECTIVE and pos.get(row["con_id"], Decimal(0)) == 0:
                self._request_cancel(row, "orphan protective: position flat")

    def _request_cancel(self, row, why):
        """Cancel REQUEST. On a send failure the order stays CANCEL_PENDING (it may or may not be cancelled: it keeps holding reducing
        capacity), and flatten / reconcile re-send the request (P7-03). Re-requesting an order already CANCEL_PENDING is allowed."""
        coid = row["client_order_id"]
        now = self.clock()
        try:
            self.orders.transition(coid, O.CANCEL_PENDING, now, why)
            self.broker.cancel(coid)
        except (BrokerDisconnected, BrokerTimeout):
            self.incident("CANCEL_FAILED", "high", {"client_order_id": coid})
        except O.IllegalTransition:
            pass

    def acknowledge_position_mismatch(self, con_id: int, operator: str, note: str) -> None:
        """Operator-only: acknowledge that the ledger and broker disagree on `con_id` so an explicit flatten/exit may be sent. New ENTRIES stay
        blocked until a clean reconcile(). The acknowledgement lapses when reconciliation no longer reports the mismatch."""
        ack = set(kv_get(self.conn, "ack_cons", []))
        ack.add(int(con_id))
        kv_set(self.conn, "ack_cons", sorted(ack))
        self.incident("POSITION_MISMATCH_ACKNOWLEDGED", "high", {"con_id": con_id, "operator": operator, "note": note})

    def acknowledge_execution_corrections(self, operator: str, note: str) -> None:
        """Operator-only: after reviewing corrected executions in the broker UI, clear the adapter's pending-correction list."""
        clear = getattr(self.broker, "acknowledge_corrections", None)
        if callable(clear):
            clear()
        self.incident("EXECUTION_CORRECTION_ACKNOWLEDGED", "high", {"operator": operator, "note": note})

    # ------------------------------------------------------------ inputs for risk
    def _working_same_side(self) -> dict:
        out: dict = {}
        for r in self.orders.by_state(O.SUBMITTING, O.ACKED, O.PARTIAL, O.CANCEL_PENDING, O.UNCERTAIN):
            rem = D(r["qty"]) - D(r["filled_qty"])
            if rem > 0:
                out[(r["con_id"], r["side"])] = out.get((r["con_id"], r["side"]), Decimal(0)) + rem
        for f in kv_get(self.conn, "foreign_live", []):       # broker orders this ledger does not own still count (conservative)
            k = (int(f["con_id"]), int(f["side"]))
            out[k] = out.get(k, Decimal(0)) + D(f["rem"])
        return out

    def _risk_inputs(self, p: Proposal, frequency_exempt: bool = False) -> RiskInputs:
        now = self.clock()
        x = RiskInputs(now_utc=now, positions=self.orders.positions(), correlations=self._corr, frequency_exempt=frequency_exempt)
        x.working_same_side = self._working_same_side()
        x.mismatch_con_ids = frozenset(set(kv_get(self.conn, "mismatch_cons", [])) - set(kv_get(self.conn, "ack_cons", [])))
        if self.mode is Mode.PAPER:       # the approval's capital allocation is a hard cap on gross exposure (0 if the approval cannot be verified)
            try:
                key = (self._env if self._env is not None else os.environ).get(KEY_ENV)
                x.capital_cap = D(verify_approval(Path(self._approval_path), key.encode(), now_utc=now)["capital_allocation"])
            except Exception:
                x.capital_cap = Decimal(0)
        try:
            acc = self.broker.account()
            x.equity, x.available_funds, x.account_ts = acc.equity, acc.available_funds, acc.ts
            tz = self.risk.policy.timezone
            day = str(self.risk.trading_date(now))
            peak = D(kv_get(self.conn, "peak_equity", str(acc.equity)))
            peak = max(peak, acc.equity)
            kv_set(self.conn, "peak_equity", str(peak))
            if kv_get(self.conn, "day", None) != day:
                kv_set(self.conn, "day", day)
                kv_set(self.conn, "day_start_equity", str(acc.equity))
            x.peak_equity, x.day_start_equity = peak, D(kv_get(self.conn, "day_start_equity"))
            x.shortable = {p.con_id: self.broker.shortable(p.con_id)}
        except (BrokerDisconnected, BrokerTimeout):
            pass   # leaves fields None => MISSING_INPUT => new exposure blocked
        quotes = {}
        for cid in set(x.positions or {}) | {p.con_id} | {c for (c, _s) in (x.working_same_side or {})}:
            try:
                q = self.quote_source(cid)
            except Exception:      # any quote-source failure = missing input = new exposure blocked
                q = None
            if q is not None:
                quotes[cid] = q
        x.quotes = quotes
        day_start = datetime.fromisoformat(iso(now)).replace(hour=0, minute=0, second=0, microsecond=0)
        x.recent_order_times = tuple(datetime.fromisoformat(r["created_utc"]) for r in
                                     self.conn.execute("SELECT created_utc FROM orders WHERE created_utc>=?", (iso(day_start - timedelta(hours=24)),)))
        if self._margin_hook is not None:
            try:
                x.margin_ok = self._margin_hook(p)
            except Exception:
                x.margin_ok = None
        return x

    # ------------------------------------------------------------ submit path
    def propose(self, p: Proposal) -> SubmitResult:
        return self._propose(p, False)

    def _propose(self, p: Proposal, frequency_exempt: bool) -> SubmitResult:
        now = self.clock()
        row, created = self.intents.insert(p, self.mode.value, now)
        iid = row["intent_id"]
        if not created:
            existing = self.orders.for_intent(iid)
            if existing:
                st = existing[-1]["state"]
                status = "UNCERTAIN" if st == O.UNCERTAIN else "DUPLICATE"
                reasons = ("UNCERTAIN_ORDER_AWAITING_RECONCILIATION",) if st == O.UNCERTAIN else ("DUPLICATE_SIGNAL",)
                return SubmitResult(iid, status, reasons, True, existing[-1]["client_order_id"])
            if row["status"] != "PROPOSED":
                return SubmitResult(iid, "DUPLICATE", ("DUPLICATE_SIGNAL",), True)
        x = self._risk_inputs(p, frequency_exempt)
        decision = self.risk.evaluate(p, x, iid)
        reasons = list(decision.reasons)
        reducing = decision.reducing
        # service-level controls apply to NEW exposure only
        if not reducing:
            if self.emergency:
                reasons.append("EMERGENCY_STOP")
            if self.paused:
                reasons.append("PAUSED_NEW_ENTRIES")
            if self.entries_blocked_by_reconciliation:
                reasons.append("RECONCILIATION_NOT_CLEAN")
            if self.orders.by_state(O.UNCERTAIN):
                reasons.append("UNCERTAIN_ORDER_BLOCKS_ENTRIES")
        elif any(r["con_id"] == p.con_id for r in self.orders.by_state(O.UNCERTAIN)):
            reasons.append("UNCERTAIN_ORDER_SAME_INSTRUMENT")
        with tx(self.conn) as c:
            c.execute("INSERT OR REPLACE INTO risk_decisions(decision_id,intent_id,allowed,reducing,reasons,policy_hash,policy_version,ts,details) "
                      "VALUES(?,?,?,?,?,?,?,?,?)", (decision.decision_id, iid, int(not reasons), int(reducing), json.dumps(reasons),
                                                    decision.policy_hash, decision.policy_version, decision.ts, canon(decision.details)))
        if reasons:
            self.intents.set_status(iid, "BLOCKED", decision.decision_id)
            return SubmitResult(iid, "BLOCKED", tuple(reasons), False, None, decision)
        if self.mode is Mode.SHADOW:
            with tx(self.conn) as c:
                c.execute("INSERT OR IGNORE INTO shadow_orders(intent_id,ts,detail) VALUES(?,?,?)", (iid, iso(now), canon({
                    "con_id": p.con_id, "side": p.side, "qty": str(p.qty), "order_type": p.order_type, "purpose": p.purpose,
                    "policy_hash": decision.policy_hash})))
            self.intents.set_status(iid, "SHADOWED", decision.decision_id)
            return SubmitResult(iid, "SHADOWED", (), False, None, decision)
        # mode gate immediately before the broker port (re-evaluated every time: approvals expire)
        try:
            approval = self._mode_gate(p, now)
        except ModeDenied as e:
            self.intents.set_status(iid, "BLOCKED", decision.decision_id)
            return SubmitResult(iid, "BLOCKED", (f"MODE_GATE:{e}",), False, None, decision)
        return self._send(iid, row, p, decision, now, approval)

    def _mode_gate(self, p: Proposal, now):
        """Raises ModeDenied unless an order for `p` may be sent now. Returns the verified approval (PAPER) or None."""
        gate_now = now
        if self.mode is Mode.PAPER:
            if self._paper_block:
                raise ModeDenied(self._paper_block)
            try:
                bt = self.broker.server_time()
            except Exception as e:
                raise ModeDenied(f"PAPER refused: broker time unavailable ({type(e).__name__})") from e
            if abs((bt - now).total_seconds()) > self._max_skew:
                raise ModeDenied(f"PAPER refused: local clock and broker time differ by more than {self._max_skew}s")
            gate_now = max(now, bt)               # approval expiry is judged on the later of the two clocks
        return assert_may_submit(self.mode, broker_is_simulated=getattr(self.broker, "is_simulated", False),
                                 registry_state=self._registry_state, strategy_id=p.strategy_id, strategy_version=p.strategy_version,
                                 instruments=[p.con_id], risk_policy_path=self._policy_path, approval_path=self._approval_path,
                                 broker_account_id=self._safe_account_id(), now_utc=gate_now, env=self._env,
                                 engine_policy_sha256=self.risk.policy.sha256 if self.mode is Mode.PAPER else None)

    def _safe_account_id(self):
        try:
            return self.broker.account().account_id
        except (BrokerDisconnected, BrokerTimeout):
            return None

    def _send(self, iid, row, p, decision, now, approval=None) -> SubmitResult:
        coid = self.orders.create(row, now)
        self.orders.transition(coid, O.SUBMITTING, now, "submitting")
        self.intents.set_status(iid, "SENT", decision.decision_id)
        permit = None
        if self.mode is Mode.PAPER and approval is not None:
            permit = self.__permits.mint(approval, intent_id=iid, client_order_id=coid, con_id=p.con_id, side=p.side, qty=D(p.qty),
                                 order_type=p.order_type, limit_px=p.limit_px, stop_px=p.stop_px, tif=p.tif, outside_rth=p.outside_rth, now=now)
        req = OrderRequest(coid, p.con_id, p.side, D(p.qty), p.order_type, p.limit_px, p.stop_px, p.tif, p.outside_rth, intent_id=iid, permit=permit)
        try:
            bid = self.broker.submit(req)
        except BrokerRefused as e:
            self.orders.transition(coid, O.REJECTED, now, f"refused before sending: {e}")
            self.intents.set_status(iid, "BLOCKED", decision.decision_id)
            return SubmitResult(iid, "BLOCKED", (f"BROKER_PORT_REFUSED:{e}",), False, coid, decision)
        except (BrokerTimeout, BrokerDisconnected) as e:
            self.orders.transition(coid, O.UNCERTAIN, now, f"submit outcome unknown: {type(e).__name__}")
            self.incident("ORDER_UNCERTAIN", "high", {"client_order_id": coid, "error": type(e).__name__})
            return SubmitResult(iid, "UNCERTAIN", ("SUBMIT_OUTCOME_UNKNOWN",), False, coid, decision)
        with tx(self.conn) as c:
            c.execute("UPDATE orders SET broker_order_id=? WHERE client_order_id=?", (bid, coid))
        return SubmitResult(iid, "SUBMITTED", (), False, coid, decision)

    # ------------------------------------------------------------ controls
    def pause_new_entries(self, reason: str = "") -> None:
        """Blocks NEW exposure. Does NOT cancel anything; protective and reducing orders keep working and may still be sent."""
        kv_set(self.conn, "paused_new_entries", True)
        self.incident("PAUSE_NEW_ENTRIES", "info", {"reason": reason})

    def resume_new_entries(self, operator: str) -> None:
        if self.emergency:
            raise RuntimeError("emergency stop active: clear_emergency first")
        kv_set(self.conn, "paused_new_entries", False)
        self.incident("RESUME_NEW_ENTRIES", "info", {"operator": operator})

    def cancel_entry_orders(self, strategy_id: str) -> list[str]:
        """Cancel ONLY working orders whose purpose is 'entry' and that this ledger owns for `strategy_id`. Protective/exit orders and
        foreign broker orders are untouched. Cancels are REQUESTS: the state becomes CANCELED only on the broker's cancel ack."""
        out = []
        for row in self.orders.by_state(O.ACKED, O.PARTIAL, O.SUBMITTING):
            if row["strategy_id"] == strategy_id and row["purpose"] == ENTRY:
                if row["state"] == O.SUBMITTING:
                    continue            # outcome unknown yet; do not guess
                self._request_cancel(row, "cancel_entry_orders")
                out.append(row["client_order_id"])
        return out

    def cancel_working_orders(self, strategy_id: str) -> list[str]:
        """Operator/abort path: request cancellation of EVERY working order this ledger owns for `strategy_id` (all purposes). Used before a
        flatten so a resting exit cannot double-close. Requests only; confirm via the cancel acks."""
        out = []
        for row in self.orders.by_state(O.ACKED, O.PARTIAL):
            if row["strategy_id"] == strategy_id:
                self._request_cancel(row, "cancel_working_orders")
                out.append(row["client_order_id"])
        return out

    def emergency_stop(self, reason: str, strategy_ids=()) -> list[str]:
        """Persisted. Blocks all non-reducing orders and requests cancellation of entry orders of the listed strategies."""
        kv_set(self.conn, "emergency_stop", True)
        self.incident("EMERGENCY_STOP", "critical", {"reason": reason})
        c = []
        for s in strategy_ids:
            c += self.cancel_entry_orders(s)
        return c

    def clear_emergency(self, operator: str, note: str) -> None:
        if self.entries_blocked_by_reconciliation:
            raise RuntimeError("reconciliation is not clean; resolve before clearing the emergency")
        kv_set(self.conn, "emergency_stop", False)
        self.incident("EMERGENCY_CLEARED", "info", {"operator": operator, "note": note})

    def flatten_strategy_positions(self, strategy_id: str, version: str | None = None) -> dict:
        """Send risk-REDUCING market orders for the strategy's fill-derived positions. Returns what ACTUALLY happened: per instrument
        requested qty, filled qty, residual exposure, status. It may fail or fill poorly; a stop order does not cap loss (gaps,
        slippage); nothing here claims otherwise.

        Order of operations per instrument (P7-01/P7-02): (1) DRY-RUN the exit (risk capacity with this strategy's own stops/exits
        freed, plus the mode gate); if it would be refused NOTHING is cancelled and the stop keeps protecting (result REFUSED_STOPS_KEPT,
        critical incident). (2) Otherwise the flatten is persisted as PENDING and the stops get a cancel REQUEST. (3) The exit is sent as
        soon as the cancel acks have freed capacity, in this call if they arrive in time, otherwise automatically from process_events() /
        reconcile(); a failed cancel (disconnect) is re-sent by every later flatten() or reconcile(). Calling flatten again is always safe:
        a live flatten exit blocks any second one, so a position cannot flip."""
        pos = self.orders.positions(strategy_id)
        if version is None:      # the flatten order belongs to the strategy version that opened the position (approvals bind the version)
            r = self.conn.execute("SELECT strategy_version FROM intents WHERE strategy_id=? ORDER BY created_utc DESC, rowid DESC LIMIT 1", (strategy_id,)).fetchone()
            version = r["strategy_version"] if r else "flatten"
        report = {"strategy_id": strategy_id, "instruments": {}}
        refused: dict = {}
        for cid, q in pos.items():
            rows = self._flatten_cancelable(strategy_id, cid)
            if rows:
                reasons = self._exit_precheck(strategy_id, version, cid, q, rows)
                if reasons:
                    refused[cid] = reasons
                    self.incident("FLATTEN_REFUSED_STOPS_KEPT", "critical", {"strategy_id": strategy_id, "con_id": cid, "reasons": reasons})
                    continue
            self._set_pending_flatten(strategy_id, version, cid, True)
            for row in rows:
                self._request_cancel(self.orders.get(row["client_order_id"]), "flatten: free reducing capacity")
        self.process_events(_resume=False)
        results = self._resume_pending_flattens(throttle=False, only_strategy=strategy_id)
        self.process_events(_resume=False)
        after = self.orders.positions(strategy_id)
        for cid, q in pos.items():
            r = results.get(cid)
            resid = after.get(cid, Decimal(0))
            order = self.orders.get(r.client_order_id) if r is not None and r.client_order_id else None
            if cid in refused:
                status, reasons = "REFUSED_STOPS_KEPT", refused[cid]
            elif r is None:
                status, reasons = "PENDING_CANCEL_ACK", ["WAITING_FOR_STOP_CANCEL_ACK"]
            else:
                status, reasons = r.status, list(r.reasons)
            report["instruments"][cid] = {"requested": str(abs(q)), "result": status, "reasons": list(reasons),
                                          "order_state": order["state"] if order else None,
                                          "filled": str(D(order["filled_qty"])) if order else "0", "residual_position": str(resid),
                                          "complete": resid == 0}
        report["residual_exposure"] = {str(k): str(v) for k, v in after.items()}
        report["complete"] = not after
        return report

    # ---- flatten helpers (P7-01/02/03)
    def _flatten_cancelable(self, strategy_id: str, cid: int):
        """This strategy's own stops/exits on `cid` that a flatten must replace (never its own earlier flatten exits)."""
        return self.conn.execute(
            "SELECT o.* FROM orders o JOIN intents i ON i.intent_id=o.intent_id WHERE o.strategy_id=? AND o.con_id=? "
            "AND o.state IN ('ACKED','PARTIALLY_FILLED','CANCEL_PENDING') AND o.purpose IN ('protective','exit') "
            "AND i.signal_id NOT LIKE 'flatten-%' ORDER BY o.order_id", (strategy_id, cid)).fetchall()

    def _flatten_exit_proposal(self, strategy_id, version, cid, q, seq) -> Proposal:
        return Proposal(strategy_id, version, f"flatten-{seq}", cid, str(cid), -1 if q > 0 else 1, abs(q), "MKT", purpose=EXIT)

    def _exit_precheck(self, strategy_id, version, cid, q, rows) -> list[str]:
        """Would the flatten exit be accepted once `rows` (our own stops/exits) are cancelled? Returns refusal reasons ([] = allowed).
        Evaluates the risk engine with that capacity freed AND the mode gate, without cancelling or sending anything."""
        p = self._flatten_exit_proposal(strategy_id, version, cid, q, "precheck")
        now = self.clock()
        try:
            x = self._risk_inputs(p, True)
            key = (p.con_id, p.side)
            freed = sum((D(r["qty"]) - D(r["filled_qty"]) for r in rows), Decimal(0))
            w = dict(x.working_same_side or {})
            w[key] = max(Decimal(0), w.get(key, Decimal(0)) - freed)
            x.working_same_side = w
            reasons = list(self.risk.evaluate(p, x, None).reasons)
            if any(r["con_id"] == cid for r in self.orders.by_state(O.UNCERTAIN)):
                reasons.append("UNCERTAIN_ORDER_SAME_INSTRUMENT")
            if not reasons and self.mode is not Mode.SHADOW:
                self._mode_gate(p, now)
        except ModeDenied as e:
            reasons = [f"MODE_GATE:{e}"]
        except Exception as e:           # anything unexpected: refuse to remove protection
            reasons = [f"PRECHECK_ERROR:{type(e).__name__}"]
        return reasons

    def _pending_flattens(self) -> dict:
        return kv_get(self.conn, "pending_flatten", {})

    def _set_pending_flatten(self, strategy_id, version, cid, on: bool):
        d = self._pending_flattens()
        k = f"{strategy_id}|{cid}"
        if on:
            d.setdefault(k, {"strategy_id": strategy_id, "version": version, "con_id": int(cid), "last": None})
        else:
            d.pop(k, None)
        kv_set(self.conn, "pending_flatten", d)

    def _retry_flatten_cancels(self):
        """Re-send cancel requests for stops of a persisted pending flatten (e.g. the first request failed on a disconnect), but only
        while the exit is still allowed, so a retry can never strip protection that cannot be replaced."""
        for v in list(self._pending_flattens().values()):
            sid, cid = v["strategy_id"], int(v["con_id"])
            q = self.orders.positions(sid).get(cid, Decimal(0))
            rows = self._flatten_cancelable(sid, cid)
            if q == 0 or not rows:
                continue
            if self._exit_precheck(sid, v["version"], cid, q, rows):
                continue
            for row in rows:
                self._request_cancel(self.orders.get(row["client_order_id"]), "flatten: retry cancel")

    def _resume_pending_flattens(self, throttle: bool, only_strategy: str | None = None) -> dict:
        """Send the exit for each persisted pending flatten whose stops are gone. Returns {con_id: SubmitResult} for attempts made."""
        out: dict = {}
        now = self.clock()
        for k, v in list(self._pending_flattens().items()):
            sid, cid = v["strategy_id"], int(v["con_id"])
            if only_strategy is not None and sid != only_strategy:
                continue
            q = self.orders.positions(sid).get(cid, Decimal(0))
            if q == 0:
                self._set_pending_flatten(sid, v["version"], cid, False)
                continue
            if self._flatten_cancelable(sid, cid):
                continue                      # still waiting for the stop cancel ack
            live_exit = None if not throttle else self.conn.execute(
                "SELECT 1 FROM orders o JOIN intents i ON i.intent_id=o.intent_id WHERE o.strategy_id=? AND o.con_id=? AND o.state IN "
                "('SUBMITTING','ACKED','PARTIALLY_FILLED','CANCEL_PENDING','UNCERTAIN') AND i.signal_id LIKE 'flatten-%'", (sid, cid)).fetchone()
            if live_exit:
                self._set_pending_flatten(sid, v["version"], cid, False)       # an exit is already in place
                continue
            if throttle and v.get("last") and (now - datetime.fromisoformat(v["last"])).total_seconds() < 30:
                continue
            seq = int(kv_get(self.conn, "flatten_seq", 0)) + 1
            kv_set(self.conn, "flatten_seq", seq)
            d = self._pending_flattens()
            d[k]["last"] = iso(now)
            kv_set(self.conn, "pending_flatten", d)
            r = self._propose(self._flatten_exit_proposal(sid, v["version"], cid, q, seq), True)
            out[cid] = r
            if r.status in ("SUBMITTED", "UNCERTAIN"):
                self._set_pending_flatten(sid, v["version"], cid, False)
            else:
                self.incident("FLATTEN_EXIT_BLOCKED", "critical", {"strategy_id": sid, "con_id": cid, "reasons": list(r.reasons)})
        return out

    def record_manual_adjustment(self, con_id: int, qty: Decimal, operator: str, note: str) -> None:
        """Operator-only: record a position change that happened outside this system as a synthetic execution (fills are the only position source)."""
        n = int(kv_get(self.conn, "manual_seq", 0)) + 1
        kv_set(self.conn, "manual_seq", n)
        q = D(qty)
        with tx(self.conn) as c:
            c.execute("INSERT INTO executions(exec_id,client_order_id,broker_order_id,con_id,side,qty,price,ts,source) VALUES(?,?,?,?,?,?,?,?,?)",
                      (f"MANUAL-{n}", None, None, con_id, 1 if q > 0 else -1, str(abs(q)), "0", iso(self.clock()), f"manual:{operator}"))
        self.incident("MANUAL_ADJUSTMENT", "high", {"con_id": con_id, "qty": str(q), "operator": operator, "note": note})

    def resolve_incident(self, incident_id: int, operator: str) -> None:
        self.conn.execute("UPDATE incidents SET resolved_ts=?, resolver=? WHERE id=?", (iso(self.clock()), operator, incident_id))
