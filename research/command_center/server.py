"""Command-centre HTTP server (stdlib only). Binds 127.0.0.1. No broker/trading imports."""
from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import secrets
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .derive import STALE_AFTER_S, snapshot
from .schemas import BLOCKED_MODES, ROLES, SELECTABLE_MODES, ValidationError, utc_now_iso, validate_event
from .store import Store
from . import router

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
STATIC = HERE / "static"
MAX_BODY = 1_000_000
IMPORT_MAX_BODY = 6_500_000  # /api/import/* only (preview enforces its own 5 MB content limit)
MAX_BATCH = 500
SSE_POLL_S = 0.25
SSE_PING_S = 5.0
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".json": "application/json", ".md": "text/plain; charset=utf-8",
        ".png": "image/png", ".woff2": "font/woff2"}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
NOT_ATTACHED = "recorded, no runtime attached"
CANCEL_NOTE = "Cancel research is only a recorded request. It never touches positions or protective orders."


class CCServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, store: Store, token: str, docs_dir=None, results_dir=None, budget_max=3):
        super().__init__(addr, Handler)
        self.store, self.token = store, token
        self.docs_dir = Path(docs_dir) if docs_dir else ROOT / "docs"
        self.results_dir = Path(results_dir) if results_dir else ROOT / "results" / "daily"
        self.budget_max = max(1, min(3, int(budget_max)))
        self.stopping = False


def _title_of(path: Path) -> str:
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("# "):
                    return line[2:].strip()
    except OSError:
        pass
    return path.stem


class Handler(BaseHTTPRequestHandler):
    server: CCServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet
        pass

    # ---- helpers
    def _send(self, code, body: bytes, ctype="application/json", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self._security_headers()
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _security_headers(self):
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")

    def _json(self, code, obj):
        self._send(code, json.dumps(obj).encode())

    def _err(self, code, msg, **kw):
        self._json(code, {"ok": False, "error": msg, **kw})

    def _local_ok(self) -> bool:
        host = urlparse("//" + (self.headers.get("Host") or "")).hostname or ""
        if host not in LOCAL_HOSTS:
            return False
        origin = self.headers.get("Origin")
        if origin:
            oh = urlparse(origin).hostname or ""
            if oh not in {"127.0.0.1", "localhost", "::1"}:
                return False
        return True

    def _token_ok(self) -> bool:
        tok = self.headers.get("X-CC-Token") or ""
        return bool(tok) and hmac.compare_digest(tok.encode(), self.server.token.encode())

    def _body(self, limit=None):
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            raise ValidationError("Content-Type must be application/json")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ValidationError("invalid Content-Length") from None
        if n <= 0 or n > (limit or MAX_BODY):
            raise ValidationError("body missing or too large")
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as e:
            raise ValidationError(f"invalid JSON: {type(e).__name__}") from None

    # ---- routing
    def do_GET(self):
        if not self._local_ok():
            return self._err(403, "local requests only")
        u = urlparse(self.path)
        q = {k: v[-1] for k, v in parse_qs(u.query).items()}
        p = u.path
        try:
            if p in ("/", "/index.html"):
                return self._static("index.html")
            if p.startswith("/static/"):
                return self._static(unquote(p[len("/static/"):]))
            if p == "/api/health":
                return self._json(200, {"ok": True, "max_seq": self.server.store.max_seq(), "server_time": utc_now_iso(),
                                        "stale_after_s": STALE_AFTER_S, "runtime_attached": False})
            if p == "/api/events":
                evs = self.server.store.events(int(q.get("after_seq", 0)), int(q.get("limit", 500)), q.get("run_id") or None,
                                               int(q["upto_seq"]) if q.get("upto_seq") else None)
                return self._json(200, {"events": evs, "max_seq": self.server.store.max_seq()})
            if p == "/api/snapshot":
                return self._snapshot(q)
            if p == "/api/runs":
                return self._json(200, {"runs": self.server.store.runs(), "latest": self.server.store.latest_run()})
            if p == "/api/search":
                qq = (q.get("q") or "").strip()
                if not qq:
                    return self._err(400, "q is required")
                return self._json(200, {"q": qq, "events": self.server.store.search(qq, int(q.get("limit", 100)), q.get("run_id") or None)})
            if p == "/api/stream":
                return self._stream(q)
            if p == "/api/docs":
                return self._list_md(self.server.docs_dir)
            if p == "/api/results":
                return self._list_md(self.server.results_dir)
            m = re.fullmatch(r"/api/(docs|results)/([A-Za-z0-9_.\-]+\.md)", p)
            if m:
                d = self.server.docs_dir if m.group(1) == "docs" else self.server.results_dir
                f = d / m.group(2)
                if not f.is_file():
                    return self._err(404, "not found")
                return self._send(200, f.read_text(encoding="utf-8", errors="replace").encode(), "text/plain; charset=utf-8")
            fn, mt = router.find("GET", p)
            if fn:
                return fn(self, mt, q, None)
            return self._err(404, "not found")
        except (ValueError, ValidationError) as e:
            return self._err(400, str(e))

    def do_POST(self):
        if not self._local_ok():
            return self._err(403, "local requests only")
        p = urlparse(self.path).path
        if p not in ("/api/events", "/api/commands") and not router.has_post(p):
            return self._err(404, "not found")
        if not self._token_ok():
            return self._err(401, "missing or invalid X-CC-Token")
        try:
            body = self._body(IMPORT_MAX_BODY if p.startswith("/api/import/") else None)
            fn, mt = router.find("POST", p)
            if fn:
                return fn(self, mt, {}, body)
            if p == "/api/events":
                return self._post_events(body)
            return self._post_command(body)
        except ValidationError as e:
            return self._err(400, str(e))

    # ---- handlers
    def _static(self, rel):
        f = (STATIC / rel).resolve()
        if STATIC.resolve() not in f.parents or not f.is_file():
            return self._err(404, "not found")
        self._send(200, f.read_bytes(), MIME.get(f.suffix, "application/octet-stream"))

    def _list_md(self, d: Path):
        items = []
        if d.is_dir():
            for f in sorted(d.glob("*.md")):
                items.append({"name": f.name, "title": _title_of(f), "bytes": f.stat().st_size,
                              "modified": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(f.stat().st_mtime))})
        self._json(200, {"dir_exists": d.is_dir(), "dir": str(d.relative_to(ROOT)) if ROOT in d.parents else str(d), "items": items})

    def _snapshot(self, q):
        st = self.server.store
        run_id = q.get("run_id") or st.latest_run()
        upto = int(q["upto_seq"]) if q.get("upto_seq") else None
        evs, after = [], 0
        while run_id:
            chunk = st.events(after, 5000, run_id, upto)
            evs += chunk
            if len(chunk) < 5000:
                break
            after = chunk[-1]["seq"]
        snap = snapshot(evs, run_id, st.max_seq(), self.server.budget_max, replay=upto is not None)
        self._json(200, snap)

    def _post_events(self, body):
        batch = body if isinstance(body, list) else body.get("events") if isinstance(body, dict) and "events" in body else [body]
        if not isinstance(batch, list) or not batch:
            raise ValidationError("expected an event object, a list, or {events:[...]}")
        if len(batch) > MAX_BATCH:
            raise ValidationError(f"batch too large (max {MAX_BATCH})")
        res = self.server.store.insert(batch)
        bad = res["rejected"] and not res["accepted"] and not res["duplicates"]
        self._json(400 if bad else 200, {"ok": not bad, **res})

    def _record_cmd(self, run_id, command, accepted, message, args, extra_events=()):
        evs = [{"event_id": uuid.uuid4().hex, "event_type": "command.recorded", "run_id": run_id, "source": "ui",
                "payload": {"command": command, "accepted": accepted, "runtime_attached": False, "message": message, "args": args}}]
        evs += list(extra_events)
        return self.server.store.insert(evs)

    def _post_command(self, body):
        if not isinstance(body, dict):
            raise ValidationError("command body must be an object")
        cmd, args = body.get("command"), body.get("args") or {}
        if not isinstance(args, dict):
            raise ValidationError("args must be an object")
        run_id = body.get("run_id") or self.server.store.latest_run() or "live"
        srv = self.server

        def reply(code, accepted, msg, extra=None, evs=()):
            res = self._record_cmd(run_id, cmd, accepted, msg, args, evs)
            out = {"ok": accepted, "accepted": accepted, "recorded": True, "runtime_attached": False, "message": msg,
                   "event_ids": [a["event_id"] for a in res["accepted"]], **(extra or {})}
            return self._json(code, out)

        if cmd == "assign_task":
            if args.get("agent_id") not in ROLES or not str(args.get("title") or "").strip():
                return self._err(400, "assign_task needs args.agent_id (a role id) and args.title")
            return reply(200, True, f"{NOT_ATTACHED}: task NOT started; nothing will execute it until an agent runtime exists")
        if cmd == "request_update":
            if args.get("agent_id") not in ROLES:
                return self._err(400, "request_update needs args.agent_id (a role id)")
            return reply(200, True, f"{NOT_ATTACHED}: no update will be produced")
        if cmd == "cancel_research":
            return reply(200, True, f"{NOT_ATTACHED}. {CANCEL_NOTE}", {"note": CANCEL_NOTE})
        if cmd == "approve_proposal":
            aid, res_ = str(args.get("approval_id") or ""), args.get("resolution", "approved")
            if not aid or res_ not in ("approved", "rejected"):
                return self._err(400, "approve_proposal needs args.approval_id and resolution approved|rejected")
            snap = snapshot(srv.store.events(0, 5000, run_id), run_id, 0)
            pend = [a for a in snap["approvals"] if a["approval_id"] == aid and not a["resolution"]]
            if not pend:
                return reply(409, False, f"no pending approval '{aid}' in run {run_id}", None)
            ev = {"event_id": uuid.uuid4().hex, "event_type": "approval.resolved", "run_id": run_id, "source": "ui",
                  "payload": {"approval_id": aid, "resolution": res_, "note": "human decision recorded via UI; nothing was executed"}}
            return reply(200, True, f"{NOT_ATTACHED}: decision recorded; nothing was executed", None, [ev])
        if cmd == "set_concurrency":
            v = args.get("value")
            if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= srv.budget_max:
                return reply(400, False, f"concurrency must be an integer 1..{srv.budget_max} (budget limit)")
            return reply(200, True, f"{NOT_ATTACHED}: limit {v} stored as a setting; not enforced (no runtime)")
        if cmd == "set_mode":
            m = args.get("mode")
            if m in BLOCKED_MODES:
                return reply(403 if m != "SHADOW" else 501, False, BLOCKED_MODES[m])
            if m not in SELECTABLE_MODES:
                return reply(400, False, f"mode must be one of {list(SELECTABLE_MODES)}")
            ev = {"event_id": uuid.uuid4().hex, "event_type": "mode.changed", "run_id": run_id, "source": "ui",
                  "payload": {"mode": m, "note": "display/research mode only; no trading capability exists"}}
            return reply(200, True, f"mode set to {m} (display label; no runtime attached)", None, [ev])
        return self._err(400, "unknown command", commands=["assign_task", "request_update", "cancel_research",
                                                            "approve_proposal", "set_concurrency", "set_mode"])

    def _stream(self, q):
        st = self.server.store
        last = self.headers.get("Last-Event-ID") or q.get("last_event_id") or q.get("after_seq") or "0"
        try:
            cursor = max(0, int(last))
        except ValueError:
            cursor = 0
        run_id = q.get("run_id") or None
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self._security_headers()
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.close_connection = True
        w = self.wfile
        try:
            w.write(b"retry: 2000\n\n")
            w.flush()
            last_ping = 0.0
            while not self.server.stopping:
                batch = st.events(cursor, 200, run_id)
                for e in batch:
                    w.write(f"id: {e['seq']}\nevent: cc\ndata: {json.dumps(e)}\n\n".encode())
                    cursor = e["seq"]
                now = time.monotonic()
                if batch:
                    w.flush()
                if now - last_ping >= SSE_PING_S:
                    w.write(f": hb\n\nevent: ping\ndata: {json.dumps({'server_time': utc_now_iso(), 'max_seq': st.max_seq()})}\n\n".encode())
                    w.flush()
                    last_ping = now
                if not batch:
                    time.sleep(SSE_POLL_S)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


def default_db() -> str:
    return os.environ.get("CC_DB") or str(HERE / "data" / "events.sqlite3")


def make_server(db_path, host="127.0.0.1", port=8765, token=None, **kw) -> CCServer:
    router.load_modules()
    return CCServer((host, port), Store(db_path), token or secrets.token_urlsafe(16), **kw)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Research command centre (local only; no trading capability)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("CC_PORT", 8765)))
    ap.add_argument("--db", default=default_db())
    a = ap.parse_args(argv)
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("refusing to bind to a non-loopback address")
    token = os.environ.get("CC_TOKEN")
    generated = not token
    token = token or secrets.token_urlsafe(16)
    srv = make_server(a.db, a.host, a.port, token, budget_max=int(os.environ.get("CC_MAX_CONCURRENCY", 3)))
    if generated:
        tf = HERE / ".cc_token"
        tf.write_text(token)
        os.chmod(tf, 0o600)
    print(f"Command centre (research/simulation only; no broker API) on http://{a.host}:{a.port}/")
    print(f"DB: {a.db}")
    print(f"X-CC-Token: {token}" + ("  (random; also written to command_center/.cc_token)" if generated else "  (from CC_TOKEN)"))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.stopping = True


if __name__ == "__main__":
    main()
