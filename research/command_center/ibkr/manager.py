"""Background manager: one daemon thread with its own asyncio loop. Never blocks the HTTP server (callers wait with short timeouts).

States: disconnected / connecting / connected / error. Reconnects with backoff while the user wants a connection. Keeps per-feed freshness,
the last human-readable error, cached quotes/account, and a one-at-a-time history job queue.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import threading
import time
import uuid
from pathlib import Path

from . import errors as E
from . import guard
from .history import HistoryDownloader, HistoryError, Cancelled, BAR_SIZES, parse_span, Pacer, snapshot_dir, assert_safe_output
from .readonly_client import ReadOnlyClient, LibraryMissing, NotStockError, library_info, DATA_TYPE_LABEL

RESEARCH_ROOT = Path(__file__).resolve().parents[2]
BACKOFF = (2, 4, 8, 16, 30, 60)
MAX_JOBS_KEPT = 30


def now_iso():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def mask_account(a: str | None) -> str | None:
    if not a:
        return None
    return a[0] + "*" * max(2, len(a) - 3) + a[-2:]


class IBKRManager:
    def __init__(self, ib_factory=None, stock_factory=None, source="ibkr", data_root=RESEARCH_ROOT, env=None, clock=time.time, sleep=None, backoff=BACKOFF, connect_timeout=8.0):
        self.ib_factory, self.stock_factory, self.source = ib_factory, stock_factory, source
        self.fake = source == "fake"
        self.data_root = Path(data_root)
        self.env = env if env is not None else os.environ
        self.clock, self._sleep, self.backoff, self.connect_timeout = clock, sleep, backoff, connect_timeout
        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self.state, self.target, self.last_error = "disconnected", None, None
        self.want = False
        self.client: ReadOnlyClient | None = None
        self.feeds = {"quotes": {"as_of": None, "data_type": None}, "account": {"as_of": None}, "history": {"as_of": None}}
        self.server_time = None
        self.attempts = 0
        self.next_retry_at = None
        self.market_data_type = 3          # ask for delayed when live is not entitled; TWS serves live if the account has it
        self._insts: dict = {}             # (symbol, exchange, conid) -> qualified inst (per connection)
        self._wanted: dict = {}            # (symbol, exchange, conid) -> requested instrument; re-subscribed after a reconnect
        self._conn_task = None
        self._account = None
        self._account_ts = 0.0
        self.jobs: list[dict] = []
        self._job_cancel: set[str] = set()
        self._job_task = None
        self._notes: list[str] = []

    # ------------------------------------------------------------------ loop plumbing
    def _ensure_loop(self):
        with self._lock:
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
                self._thread = threading.Thread(target=self._loop.run_forever, name="ibkr-manager", daemon=True)
                self._thread.start()
        return self._loop

    def _call(self, coro, timeout=10.0):
        fut = asyncio.run_coroutine_threadsafe(coro, self._ensure_loop())
        return fut.result(timeout)

    def _spawn(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._ensure_loop())

    async def _sleeper(self, d):
        await (self._sleep(d) if self._sleep else asyncio.sleep(d))

    def shutdown(self):
        self.want = False
        try:
            if self._loop:
                self._call(self._do_disconnect(), 5)
        except Exception:
            pass
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        with self._lock:
            c = self.client
            lib = {"installed": True, "version": "fake", "message": None} if self.fake else library_info()
            return {
                "state": self.state, "fake": self.fake, "source": self.source, "library": lib,
                "kind": self.target.kind if self.target else None, "label": self.target.label if self.target else None,
                "port": self.target.port if self.target else None, "host": self.target.host if self.target else None, "live_port": bool(self.target and self.target.live_port),
                "readonly": True, "execution": "none", "market_data_type_requested": self.market_data_type, "last_error": self.last_error,
                "attempts": self.attempts, "next_retry_at": self.next_retry_at, "server_time": self.server_time, "feeds": {k: dict(v) for k, v in self.feeds.items()},
                "subscribed": len(c.subscribed()) if c and self.state == "connected" else 0, "account_masked": mask_account(self._account["account"]) if self._account else None,
                "allowed_kinds": guard.allowed_kinds(self.env), "live_ports_enabled": guard.live_ports_enabled(self.env),
                "banner": "SIMULATED IBKR (test fixture): not real market data, not a real account" if self.fake else None,
                "notes": list(self._notes),
            }

    def _set_error(self, info: dict):
        self.last_error = {**info, "at": now_iso()}

    # ------------------------------------------------------------------ connect / disconnect
    def connect(self, kind: str) -> dict:
        target = guard.resolve_target(kind, env=self.env)          # raises GuardError
        if not self.fake:
            lib = library_info()
            if not lib["installed"]:
                self._set_error(E.explain_exception(LibraryMissing(lib["message"])))
                raise LibraryMissing(lib["message"])
        with self._lock:
            if self.want and self.state in ("connecting", "connected"):
                return self.status()
            self.want, self.target, self.state, self.attempts, self.last_error = True, target, "connecting", 0, None
        self._conn_task = self._spawn(self._connection_loop(target))
        return self.status()

    def disconnect(self) -> dict:
        with self._lock:
            self.want = False
        if self._loop:
            try:
                self._call(self._do_disconnect(), 8)
            except Exception:
                pass
        with self._lock:
            self.state, self.next_retry_at = "disconnected", None
        return self.status()

    async def _do_disconnect(self):
        if self._conn_task:
            self._conn_task.cancel()
        c, self.client = self.client, None
        if c:
            try:
                c.disconnect()
            except Exception:
                pass

    async def _connection_loop(self, target):
        i = 0
        while self.want:
            with self._lock:
                self.state, self.attempts = "connecting", self.attempts + 1
                self.next_retry_at = None
            dropped = asyncio.Event()
            try:
                client = ReadOnlyClient(target, ib=self.ib_factory() if self.ib_factory else None, stock_factory=self.stock_factory, source=self.source)
                client.on_error = self._on_ib_error
                client.on_disconnect = lambda: self._loop.call_soon_threadsafe(dropped.set)
                await client.connect(self.connect_timeout)
                self.client = client
                st = await client.server_time()
                client.set_market_data_type(self.market_data_type)
                with self._lock:
                    self.state, self.last_error, self.server_time = "connected", None, st.isoformat(timespec="seconds")
                i = 0
                await self._resubscribe()
                await self._refresh_account(force=True)
                while self.want and client.connected and not dropped.is_set():
                    try:
                        await asyncio.wait_for(dropped.wait(), 5.0)
                    except asyncio.TimeoutError:
                        pass
                if not self.want:
                    break
                if not self.last_error or self.last_error.get("severity") != "error":
                    self._set_error({**E.explain(1100), "message": "The connection to TWS/Gateway was lost.", "code": None})
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self._set_error(E.explain_exception(e, target.port))
            finally:
                c, self.client = self.client, None
                if c:
                    try:
                        c.disconnect()
                    except Exception:
                        pass
            if not self.want:
                break
            delay = self.backoff[min(i, len(self.backoff) - 1)]
            i += 1
            with self._lock:
                self.state = "error"
                self.next_retry_at = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=delay)).isoformat(timespec="seconds")
            await self._sleeper(delay)
        with self._lock:
            if not self.want:
                self.state = "disconnected"

    def _on_ib_error(self, code, text, req_id):
        info = E.explain(code, text)
        if code in E.INFO_CODES:
            return
        if code in (1100, 1101, 354, 10186, 10197, 321, 326, 507) or (req_id in (-1, 0, None) and info["severity"] in ("warn", "error")):
            self._set_error(info)

    # ------------------------------------------------------------------ quotes
    async def _qualify(self, ins: dict) -> dict:
        key = (ins["symbol"], ins.get("exchange"), ins.get("conid"))
        if key not in self._insts:
            self._insts[key] = await self.client.qualify_stock(ins["symbol"], ins.get("exchange"), ins.get("currency") or "USD", ins.get("conid"))
        return self._insts[key]

    async def _resubscribe(self):
        self._insts.clear()                         # contracts must be re-qualified on the new connection
        if self._wanted:
            await self._ensure_quotes(list(self._wanted.values()), 0.0)

    async def _ensure_quotes(self, instruments: list[dict], wait: float):
        c = self.client
        if not c or self.state != "connected":
            return {}
        fresh = []
        for ins in instruments:
            self._wanted[(ins["symbol"], ins.get("exchange"), ins.get("conid"))] = ins
            try:
                inst = await self._qualify(ins)
                if inst["conid"] not in c.subscribed():
                    c.subscribe_quote(inst)
                    fresh.append(inst["conid"])
            except NotStockError as e:
                self._set_error(E.explain_exception(e))
            except Exception as e:  # noqa: BLE001
                self._set_error({**E.explain(200, str(e)), "message": str(e)[:300]})
        end = time.time() + wait
        while fresh and time.time() < end and any(not (c.quote(x) and (c.quote(x).last is not None or c.quote(x).bid is not None)) for x in fresh):
            await asyncio.sleep(0.1)
        out = {}
        for ins in instruments:
            inst = self._insts.get((ins["symbol"], ins.get("exchange"), ins.get("conid")))
            q = c.quote(inst["conid"]) if inst else None
            if q and (q.last is not None or q.bid is not None):
                out[(ins["symbol"], ins.get("exchange"), ins.get("conid"))] = q
        if out:
            with self._lock:
                self.feeds["quotes"] = {"as_of": now_iso(), "data_type": max(q.data_type or 0 for q in out.values()) or None}
        return out

    def live_quotes(self, instruments: list[dict], wait: float = 1.5) -> dict:
        """-> {(symbol, exchange, conid): QuoteTick}; empty when not connected. Subscribes on first use; ticks arrive asynchronously."""
        if self.state != "connected" or not self._loop:
            return {}
        try:
            return self._call(self._ensure_quotes(instruments, wait), wait + 6)
        except Exception as e:  # noqa: BLE001
            self._set_error(E.explain_exception(e))
            return {}

    # ------------------------------------------------------------------ account
    async def _refresh_account(self, force=False):
        c = self.client
        if not c or (not force and self.clock() - self._account_ts < 10):
            return self._account
        rows = await c.account_summary()
        acct = (c.managed_accounts() or [None])[0]
        vals = {}
        for r in rows:
            vals.setdefault(r["tag"], (r["value"], r["currency"]))
        pnl = c.daily_pnl(acct) if acct else {"daily": None}
        snap = {"account": acct, "as_of": now_iso(), "values": vals, "pnl": pnl, "positions": c.positions(), "open_orders": c.open_orders(), "executions": c.executions()}
        self._account, self._account_ts = snap, self.clock()
        with self._lock:
            self.feeds["account"] = {"as_of": snap["as_of"]}
        return snap

    def account_snapshot(self, max_age: float = 10.0):
        if self.state != "connected" or not self._loop:
            return None
        try:
            if self.clock() - self._account_ts > max_age:
                return self._call(self._refresh_account(force=True), 15)
        except Exception as e:  # noqa: BLE001
            self._set_error(E.explain_exception(e))
        return self._account

    # ------------------------------------------------------------------ history jobs (one at a time)
    def submit_history(self, items: list[dict], bar_size: str, years=None, duration=None, use_rth: bool = True) -> dict:
        if self.state != "connected":
            raise RuntimeError("not connected to TWS/Gateway: connect first")
        if bar_size not in BAR_SIZES:
            raise ValueError(f"bar_size must be one of {sorted(BAR_SIZES)}")
        span = parse_span(years, duration, bar_size)
        if not items or len(items) > 10:
            raise ValueError("choose 1 to 10 instruments")
        if not isinstance(use_rth, bool):
            raise ValueError("use_rth must be true or false")
        with self._lock:
            if any(j["status"] in ("queued", "running") for j in self.jobs):
                raise RuntimeError("a history download is already running or queued; wait for it to finish (one at a time keeps within IBKR pacing)")
            job = {"id": uuid.uuid4().hex[:12], "status": "queued", "bar_size": bar_size, "span_days": span, "use_rth": use_rth, "session": "regular" if use_rth else "regular+extended",
                   "symbols": [f"{i['symbol']}:{i.get('exchange')}" for i in items], "created": now_iso(), "started": None, "finished": None, "error": None, "source": self.source,
                   "progress": {"symbol_index": 0, "symbols_total": len(items), "chunks_done": 0, "chunks_total_estimate": None, "rows": 0, "message": "queued"}, "outputs": [], "dir": None}
            self.jobs.insert(0, job)
            del self.jobs[MAX_JOBS_KEPT:]
        self._job_task = self._spawn(self._run_job(job, items))
        return dict(job)

    def cancel_job(self, job_id: str) -> bool:
        with self._lock:
            j = next((x for x in self.jobs if x["id"] == job_id), None)
            if not j or j["status"] not in ("queued", "running"):
                return False
            self._job_cancel.add(job_id)
            return True

    def list_jobs(self) -> list[dict]:
        with self._lock:
            return [dict(j, progress=dict(j["progress"]), outputs=list(j["outputs"])) for j in self.jobs]

    async def _run_job(self, job, items):
        def upd(**kw):
            with self._lock:
                job.update(kw)

        def prog(sym_i, name):
            def f(p):
                with self._lock:
                    job["progress"].update({**p, "symbol_index": sym_i, "message": f"{name}: chunk {p['chunks_done']}, {p['rows']} rows"})
            return f
        upd(status="running", started=now_iso())
        try:
            lib = None if self.fake else library_info().get("version")
            dl = HistoryDownloader(self.client, self.data_root, self.source, clock=self.clock, sleep=self._sleeper, lib_version=lib)
            out = snapshot_dir(self.data_root, self.source, dt.datetime.fromtimestamp(self.clock(), dt.timezone.utc))
            assert_safe_output(out, self.data_root, self.source)
            upd(dir=str(out.relative_to(self.data_root)))
            for i, ins in enumerate(items):
                inst = await self._qualify(ins)
                with self._lock:
                    job["progress"].update({"symbol_index": i, "message": f"{inst['symbol']}: starting"})
                res = await dl.download(inst, job["bar_size"], job["span_days"], job["use_rth"], self.market_data_type, prog(i, inst["symbol"]), lambda: job["id"] in self._job_cancel, out)
                with self._lock:
                    job["outputs"].append({k: res[k] for k in ("symbol", "conId", "exchange", "file", "path", "rows", "first", "last", "sha256", "stop_reason", "chunks", "requests", "dropped_incomplete_bars")})
                    self.feeds["history"] = {"as_of": now_iso()}
            upd(status="done", finished=now_iso())
            with self._lock:
                job["progress"]["message"] = f"done: {len(job['outputs'])} file(s) in {job['dir']}"
        except Cancelled as e:
            upd(status="cancelled", finished=now_iso(), error=str(e))
        except HistoryError as e:
            upd(status="error", finished=now_iso(), error=e.info.get("message"), error_info=e.info)
        except Exception as e:  # noqa: BLE001
            info = E.explain_exception(e)
            upd(status="error", finished=now_iso(), error=info["message"], error_info=info)


_MANAGER: IBKRManager | None = None
_MLOCK = threading.Lock()


def get_manager() -> IBKRManager:
    """Process-wide manager. CC_IBKR_FAKE=1 selects the SIMULATED IB (test fixture); otherwise the real ib_async is used (lazily)."""
    global _MANAGER
    with _MLOCK:
        if _MANAGER is None:
            if os.environ.get("CC_IBKR_FAKE") == "1":
                from .fake_ib import FakeIB, FakeStock
                _MANAGER = IBKRManager(ib_factory=FakeIB, stock_factory=FakeStock, source="fake")
            else:
                _MANAGER = IBKRManager()
        return _MANAGER


def set_manager(m: IBKRManager | None):
    global _MANAGER
    with _MLOCK:
        _MANAGER = m
