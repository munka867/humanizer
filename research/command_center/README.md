# Research Command Centre (slice 2)

Single-user, **local-only, research/simulation-only** dashboard for the tradelab agent team.
It imports and calls **no broker or trading API** (a static test enforces this). Nothing here can place orders.

## Run
```bash
cd research
bash command_center/run.sh            # http://127.0.0.1:8765  (CC_PORT, CC_DB, CC_TOKEN, CC_MAX_CONCURRENCY env vars)
python command_center/seed_demo.py    # labelled DEMO events (source='demo'); idempotent
python command_center/emit.py --type message.sent --agent lead --recipient backtester --preview 'please run baseline'
python -m pytest tests/test_cc_*.py -q
python command_center/verify_e2e.py   # Playwright end-to-end check, temp DB + own port, writes screenshots/
```
The server prints the `X-CC-Token` at start (random unless `CC_TOKEN` is set; a random one is also written to
`command_center/.cc_token`, git-ignored, which `emit.py`/`seed_demo.py` read). Paste it into the header "Token" box
to use the Controls. GET endpoints need no token (loopback only; non-local Host/Origin headers are refused).
Stack: Python stdlib only (see `requirements.txt`); frontend is static files, no build, no CDN, works offline.

## Architecture
- `schemas.py` – roles, statuses, modes, **versioned payload schemas** (`SCHEMAS[type][version]`), validation.
- `store.py` – SQLite WAL `events` table, append-only (UPDATE/DELETE blocked by triggers), idempotent insert by `event_id`
  (duplicates are ignored and reported; they do not consume a `seq`).
- `derive.py` – builds the snapshot from events (single source of truth for the UI) + stale logic.
- `server.py` – HTTP API + static files. `static/` – the UI. `emit.py`, `seed_demo.py`, `run.sh`, `verify_e2e.py`.

### API
| Endpoint | Notes |
|---|---|
| `POST /api/events` | token required; one event, a list, or `{events:[...]}`; response lists accepted (with seq) / duplicates / rejected |
| `GET /api/events?after_seq=&limit=&run_id=&upto_seq=` | history with original timestamps (replay source) |
| `GET /api/snapshot?run_id=&upto_seq=` | derived agents/tasks/deps/messages/artifacts/approvals/commands; default run = latest |
| `GET /api/stream?run_id=` | SSE; honours `Last-Event-ID` (= seq) or `last_event_id=`; replays missed then live; `event: ping` every 5s + `: hb` comment |
| `GET /api/search?q=&run_id=` | substring search over stored events |
| `GET /api/runs`, `/api/health`, `/api/docs`, `/api/results` | run list, health, `../docs/*.md` and `../results/daily/*.md` indexes (read-only) |
| `POST /api/commands` | token required: `assign_task`, `request_update`, `cancel_research`, `approve_proposal`, `set_concurrency` (1..3, within `CC_MAX_CONCURRENCY`), `set_mode` |

Commands only **record** a `command.recorded` event (refusals are recorded too) and reply "recorded, no runtime attached";
nothing is executed or started. `cancel_research` states it never touches positions or protective orders.
`PAPER`/`LIVE` are refused ("blocked: requires approval & broker adapter"), also via `mode.changed` events; `SHADOW` is not implemented.

### Event schema
Envelope: `seq` (autoinc), `event_id` (unique, idempotency key), `event_type`, `timestamp_utc` (ISO, normalised to ms `Z`),
`run_id` (default `live`), `agent_id` (one of the 7 role ids), `parent_task_id`, `correlation_id`, `source` (required;
`demo` = seeded example, drives the DEMO banner), `status`, `payload_version`, `payload`.
Roles: `lead, data_ibkr, strategy_researcher, backtester, validator, risk_execution, monitor`.
Statuses: `queued, working, awaiting_dependency, awaiting_approval, idle, failed, complete`.
Types and required payload fields (v1): `agent.status{status}` (+optional `task, blockers, usage`), `task.created{task_id,title}`,
`task.updated{task_id}`, `task.dependency{task_id,depends_on}`, `message.sent{sender,recipient,preview}` (+`kind: message|delegation`),
`tool.activity{tool,summary}` (+`sources`), `artifact.created{path}`, `test.result{name,outcome}`, `decision.summary{summary}`,
`incident{severity,summary}`, `mode.changed{mode}`, `approval.requested{approval_id,summary}`, `approval.resolved{approval_id,resolution}`,
`heartbeat`, `command.recorded{command,accepted,runtime_attached,message}`.
Dependencies (`task.dependency`, dashed edges) are deliberately distinct from messages (`message.sent`, solid edges that pulse).
Completed-task count rule (`completed_count`): distinct tasks owned by the agent whose status is `complete` (from `task.created`/`task.updated`), plus each `agent.status` transition into `complete` whose task text is not already a counted task title (deduped by task text; a transition with no task text counts once).
Usage/tokens are shown **only** if an `agent.status` event carried `usage`; no percentage completion or cost is ever computed.

### UI behaviour
Dark neutral theme by default (WCAG AA text/status contrast, status never colour-only: icon + text + border style); theme toggle cycles dark / light / system (system follows the OS only when chosen). Persistent mode badge + mode bar (DEMO/BACKTEST/REPLAY selectable; SHADOW/PAPER/LIVE disabled with reasons); DEMO banner and per-item DEMO tags whenever
displayed data has `source='demo'`; connection badge connecting/live/reconnecting/STALE (no ping/event for 15s -> "STALE: showing last known state as of HH:MM:SS",
UI greyed and inputs disabled); refresh = `/api/snapshot` + `/api/events` replay, then SSE from the highest seq (client dedupes by seq);
edges animate only when a real `message.sent` arrives live or during replay; reduced-motion disables travelling dots;
keyboard: Tab/arrows/Enter on towers, `+ - 0` on the graph; replay with 0.5-8x speed (client-side, original timestamps, gaps >3s compressed).
Screens: Overview + agent panel are real; Research library (docs index + viewer) and Backtest lab (results/daily index, empty if none) are read-only indexes;
Audit/settings shows the command log; Data centre, Strategy registry, Monte Carlo lab, Execution console, Risk centre render "Not implemented in this slice: <prerequisite>".

## Limitations (honest)
- **Claude Code interactive agent-team peer messages are NOT observable by this app.** Only events posted via `emit.py`/`POST /api/events` (or future hooks)
  appear. The coordinator must emit real delegations explicitly. Anything not emitted is invisible, and agents with no events show "no events yet".
- App-owned agents (via the Agent SDK) are **not implemented**; there is no runtime, so commands are recorded only and concurrency is a stored, unenforced setting.
- Private reasoning is never captured or shown.
- Single local user; token is a shared secret in a header, not user auth. SSE polls the DB every 0.25s (fine for local use, not for scale).
- Verified in Chromium only (headless). Not tested: Firefox/Safari, screen readers, touch devices, very large histories.
- DEMO events are seeded examples; `seed_demo.py` data are not real activity, results or test passes.
