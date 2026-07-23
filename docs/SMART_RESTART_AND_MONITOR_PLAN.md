# Smart Auto-Restart (1001 Recovery) + Unified Monitor Dashboard — Plan

Status: ✅ IMPLEMENTED (all 3 phases)
Scope: `backend/app.py` (Render web service) + `backend/job_worker_realtime.py` (Render worker service) + one static `monitor.html`

Implemented files:
- `backend/monitor_api.py` — monitor blueprint, ring buffers, log capture, token auth (Phase 1)
- `backend/smart_restart.py` — 1001 detector, state machine, drain, Render restart (Phases 2–3)
- `backend/monitor.html` — single-file dashboard, served at `GET /monitor` (Phase 1)
- `backend/migrations/033_create_system_restarts.sql` — durable restart audit table (Phase 2)
- `backend/app.py` — `init_monitor(app, "backend")` + `init_smart_restart("backend")`
- `backend/job_worker_realtime.py` — `init_monitor(app, "worker")` + active-jobs gauge in `process_job_with_concurrency_control`
- `backend/error_notifier.py` — new `ErrorType.SMART_RESTART` alert type
- `backend/render.yaml` — new env vars documented for both services
- `docs/architecture/ARCHITECTURE_DIAGRAM.md` — new "Smart Auto-Restart & Monitor" architecture section

---

## 1. Deep Analysis of the Problem

### 1.1 What the log shows

```
INFO:realtime._async.client:send: {"topic": "phoenix", "event": "heartbeat", ...}
ERROR:realtime._async.client:Error on connect: received 1001 (going away); then sent 1001 (going away)
```

- WebSocket close code **1001 ("going away")** means the Supabase Realtime server (Phoenix) closed the connection — typically because the Realtime node was recycled/redeployed, the connection was idle-culled, or the JWT/socket state went stale.
- The error comes from the `realtime-py` async client used by `realtime_manager.py` (the shared Realtime connection in `app.py` that powers SSE job updates).
- Critically, the error repeats forever: **"Error on connect"** means every *reconnect attempt* also fails with 1001. The `realtime-py` client is stuck with poisoned internal state (stale socket/session inside the async client object). Our own reconnect loop in `RealtimeConnectionManager._run_async_loop()` creates a new event loop, but the failure happens inside the library's connect path and never recovers.
- Everything else (REST/PostgREST, Flask, worker HTTP push) keeps working — only the Realtime WebSocket is dead. That is why the app "works" but live job updates degrade, and why **a full process restart fixes it** (fresh interpreter → fresh realtime client → clean connect).

### 1.2 Why in-process recovery is not enough

`realtime_manager.py` already has:
- exponential-backoff reconnect (`_run_async_loop`)
- a heartbeat dead-connection detector (`_heartbeat_checker`, 90s timeout)

Despite that, the 1001 loop persists, because the broken state lives *inside* the `realtime-py` / `websockets` layer, not in our loop. Conclusion: **the reliable cure is a process restart**, which on Render means restarting the service. So the design goal is: *detect the persistent 1001 state → drain jobs safely → restart the Render deploy automatically.*

### 1.3 Existing building blocks we will reuse

| Piece | File | What it gives us |
|---|---|---|
| Maintenance mode (file flag `.maintenance_mode`) | `app.py` (`/admin/maintenance`, checked before job creation at ~L803, L2709, L3025) | Blocks NEW job submissions while existing jobs finish |
| Worker maintenance check | `job_worker_realtime.py` (~L1115, L1228) | Worker skips pending jobs when its local flag exists |
| Graceful shutdown logic (wait-for-running-jobs loop) | `graceful_shutdown.py` (`check_pending_jobs`, drain loop) | The "smart shutdown" pattern: query `jobs` where `status = running`, wait until 0 |
| Remote maintenance toggle | `remote_shutdown.py` → `POST /admin/maintenance` with `ADMIN_SECRET` | Remote control pattern + auth pattern |
| Health endpoints | `app.py /health`, `job_worker_realtime.py /health` | Cross-service liveness + worker `jobs_processed`, `ready`, `last_heartbeat` |
| Render blueprint | `render.yaml` (web: `atool-backend`, worker: `atool-worker`) | Both services autoDeploy; restart is per-service |

> Note: the `.maintenance_mode` flag file is **local to each service's ephemeral filesystem**. Backend flag ≠ worker flag. A restart wipes the flag automatically, which is convenient (maintenance auto-clears after redeploy) but means cross-service coordination must go over HTTP or Supabase, not the file.

### 1.4 How to restart a Render service programmatically

Two supported options (we will implement both, API preferred):

1. **Render API restart** (preferred — true restart, no rebuild):
   `POST https://api.render.com/v1/services/{serviceId}/restart` with header `Authorization: Bearer $RENDER_API_KEY`.
2. **Deploy Hook URL** (fallback — triggers a fresh deploy):
   `GET/POST $RENDER_DEPLOY_HOOK_URL` (per-service secret URL from Render dashboard).

New env vars (both services):

```
RENDER_API_KEY=            # Render personal/team API key
RENDER_BACKEND_SERVICE_ID= # srv-... of atool-backend
RENDER_WORKER_SERVICE_ID=  # srv-... of atool-worker
RENDER_BACKEND_DEPLOY_HOOK=  # optional fallback
RENDER_WORKER_DEPLOY_HOOK=   # optional fallback
MONITOR_TOKEN=             # bearer token for /monitor/* endpoints (can reuse ADMIN_SECRET)
SMART_RESTART_ENABLED=true # kill-switch
```

---

## 2. Feature A — Smart Auto-Restart on persistent 1001

New module: `backend/smart_restart.py` (shared, imported by both services).

### 2.1 Detection (Phase D)

- Attach a custom `logging.Handler` to the `realtime._async.client` logger (and `realtime` root) that inspects ERROR records.
- Count records matching `1001` / `going away` in a **sliding window**.
- Trigger condition (tunable constants):
  - `>= 6` 1001 errors within `5 minutes`, **AND**
  - `RealtimeConnectionManager._get_last_event_age() > 120s` (no successful realtime traffic), **AND**
  - not currently in restart cooldown.
- Also expose a secondary trigger: `RealtimeConnectionManager` reconnect attempt counter exceeding N without a successful subscribe.

### 2.2 Drain — "smart shutdown", fully automated (Phase R)

State machine inside `smart_restart.py` (runs in a daemon thread):

```
HEALTHY → DETECTED → DRAINING → RESTART_TRIGGERED → (process dies, Render restarts) → HEALTHY
```

1. **DETECTED → enable maintenance mode** on BOTH services:
   - locally: touch `.maintenance_mode` (backend blocks new jobs immediately),
   - remotely: `POST {WORKER_URL}/monitor/maintenance {enable: true}` so the worker touches its own flag and stops picking pending jobs.
2. **DRAINING — verify no jobs are running anywhere** (adapted from `graceful_shutdown.check_pending_jobs`, but non-interactive):
   - Supabase: `jobs` where `status = 'running'` count must be `0`.
   - Worker: `GET {WORKER_URL}/monitor/status` must report `active_jobs == 0` (new field: count of in-flight threads/jobs in the worker, incl. workflow steps).
   - Poll every `10s`, max wait `15 min`. On timeout: abort restart, clear maintenance, alert via `error_notifier.py` (Telegram), retry detection later. **Never kill running jobs.**
3. **RESTART_TRIGGERED — restart BOTH deploys (user asked for complete restart)**:
   - Call Render API restart for `RENDER_WORKER_SERVICE_ID` first, then `RENDER_BACKEND_SERVICE_ID` (backend last, since it is the one executing the calls; it restarts itself at the end).
   - If API fails → fall back to deploy hooks.
   - Log a row to Supabase table `system_restarts` (new, tiny, standalone): `{service, reason: 'realtime_1001', errors_in_window, triggered_at}` for audit/history in the monitor UI.
4. **Safety rails**:
   - Cooldown: max **1 auto-restart per 30 minutes**, max **4 per day**; beyond that, only alert (prevents restart storms if 1001 is caused by a Supabase-side outage that a restart cannot fix).
   - `SMART_RESTART_ENABLED=false` disables the whole loop instantly.
   - Maintenance flag is a local file → automatically gone after restart → services come back accepting jobs with zero manual steps.

### 2.3 Worker-side

The worker's realtime usage (LISTEN/NOTIFY + HTTP push) is more stable, but the same detector is installed there too; if the worker sees persistent 1001, it reports it via its `/monitor/status`, and the backend orchestrator (single decision-maker to avoid split-brain) performs the same drain-and-restart sequence.

---

## 3. Feature B — Monitor endpoints on both services

New blueprint `backend/monitor_api.py`, mounted in `app.py` AND in the worker's Flask app. All endpoints require `Authorization: Bearer $MONITOR_TOKEN`, CORS enabled (the HTML file is opened locally / served statically).

| Endpoint | Method | Returns |
|---|---|---|
| `/monitor/status` | GET | service name/role, uptime, memory, realtime state (`running`, `last_event_age`, `1001_count_5m`, reconnect attempts), smart-restart state (`HEALTHY/DETECTED/DRAINING/...`, cooldown, last restart), maintenance flag, worker extras (`active_jobs`, `jobs_processed`, `ready`) |
| `/monitor/jobs` | GET | from Supabase `jobs`: currently `running` (full rows), `pending` count, last 20 `completed`, last 20 `failed` (with error text), today's totals |
| `/monitor/logs?limit=200` | GET | in-memory **ring buffer** (last 500 log records, captured by a logging handler: level, logger, message, timestamp) — this is how the UI "receives logs" without Render log access |
| `/monitor/requests?limit=100` | GET | in-memory ring buffer of recent HTTP requests (method, path, status, duration ms, ip) captured via Flask `before/after_request` hooks in `middleware.py` |
| `/monitor/errors?limit=100` | GET | ring buffer filtered to ERROR/CRITICAL records + last exceptions with traceback head |
| `/monitor/maintenance` | POST | `{enable: bool}` — touch/remove local `.maintenance_mode` (worker needs this for remote coordination; backend already has `/admin/maintenance`, this aliases it) |
| `/monitor/restart` | POST | `{scope: "self"|"all", force: bool}` — manual trigger of the smart drain-and-restart from the UI; `force=false` always drains first |
| `/monitor/restart-history` | GET | rows from `system_restarts` table |

Implementation notes:
- Ring buffers = `collections.deque(maxlen=500)` guarded by a lock; zero external dependencies; lost on restart (acceptable — Supabase `system_restarts` + `jobs` hold the durable history).
- The log-capture handler is the SAME handler used by the 1001 detector (one handler, two consumers).

---

## 4. Feature C — `monitor.html` (single-file dashboard)

One static file: `backend/monitor.html` (also served by backend at `GET /monitor` for convenience). Plain HTML + vanilla JS + inline CSS (dark theme), no build step, works when opened directly from disk.

### 4.1 Config & behavior
- Top bar inputs: **Backend URL**, **Worker URL**, **Token** → saved to `localStorage`; "Connect" button.
- Polling: `/monitor/status` every **5s** (both services in parallel), `/monitor/jobs` every **10s**, `/monitor/logs` + `/monitor/requests` + `/monitor/errors` every **10s**. On page open it immediately loads completed/failed history (satisfies "completed when the page opens").
- Every panel shows which service it came from (Backend / Worker badge).

### 4.2 UI layout

```
┌──────────────────────────────────────────────────────────────┐
│  ATOOL MONITOR        [backend url][worker url][token][Go]   │
├───────────────┬───────────────┬──────────────────────────────┤
│ BACKEND card  │ WORKER card   │ REALTIME card                │
│ ● healthy     │ ● healthy     │ ws: CONNECTED / 1001-LOOP    │
│ uptime, mem   │ active_jobs=0 │ 1001 count (5m), last event  │
│ maintenance   │ processed=124 │ restart state + cooldown     │
├───────────────┴───────────────┴──────────────────────────────┤
│ RUNNING JOBS (live table: id, type, model, user, started,    │
│               elapsed, step)                                 │
├──────────────────────────────────────────────────────────────┤
│ COMPLETED (last 20)      │ FAILED / ERRORS (last 20 + msg)   │
├──────────────────────────────────────────────────────────────┤
│ LIVE LOG STREAM (merged backend+worker, level-colored,       │
│  filter box, auto-scroll toggle)                             │
├──────────────────────────────────────────────────────────────┤
│ RECENT REQUESTS (method, path, status, ms)  │ RESTART HISTORY│
├──────────────────────────────────────────────────────────────┤
│ [Enable Maintenance] [Drain + Restart All] (confirm dialog)  │
└──────────────────────────────────────────────────────────────┘
```

- Status dots: green (healthy), yellow (DETECTED/DRAINING/maintenance), red (unreachable / 1001-loop).
- "Drain + Restart All" button calls `POST /monitor/restart {scope:"all"}` — same safe path as the automatic flow.
- If a service is unreachable (mid-restart), its card turns red with a retry countdown instead of breaking the page.

---

## 5. Implementation Phases & File Changes

**Phase 1 — Observability core** (no behavior change, safe to deploy)
1. `backend/monitor_api.py` (new): blueprint, token auth, ring buffers, log handler, `/monitor/status|logs|errors|requests|jobs`.
2. `backend/middleware.py`: add request-recording hooks.
3. Mount blueprint in `app.py` and `job_worker_realtime.py`; add `active_jobs` gauge to worker.
4. `backend/monitor.html` (new) + `GET /monitor` route.

**Phase 2 — Detection + manual restart**
5. `backend/smart_restart.py` (new): 1001 sliding-window detector, state machine, drain loop (non-interactive port of `graceful_shutdown.py`), Render API/deploy-hook client, cooldowns, Telegram alerts via `error_notifier.py`.
6. `/monitor/restart` + `/monitor/maintenance` endpoints; wire "Drain + Restart" button.
7. Migration: `system_restarts` table (standalone, no FKs).

**Phase 3 — Full automation**
8. Enable auto-trigger (`SMART_RESTART_ENABLED=true`), start detector thread on startup in both services.
9. `render.yaml`: document new env vars.
10. Test matrix: simulate 1001 (revoke realtime, kill socket), verify drain waits for a running workflow job, verify cooldown, verify both services restart and come back with maintenance auto-cleared.

**Env setup (Render dashboard, both services):** `RENDER_API_KEY`, `RENDER_BACKEND_SERVICE_ID`, `RENDER_WORKER_SERVICE_ID`, `MONITOR_TOKEN`, optional deploy hooks, `SMART_RESTART_ENABLED`.

---

## 6. Edge Cases & Decisions

- **Job started between detection and flag?** Flag is checked at submission time in `app.py`; the drain loop re-checks `running` count every poll, so late jobs are simply waited on.
- **Workflow jobs with long steps** (like `common-workflow` in the log): drain timeout 15 min; if exceeded → abort + alert, never force-kill.
- **Restart order**: worker first, backend last (backend orchestrates; restarting itself last avoids losing the orchestrator mid-sequence). Deploy-hook fallback triggers a rebuild (slower) — acceptable.
- **Split-brain**: only the backend triggers restarts; the worker only reports.
- **Supabase-wide realtime outage**: cooldown + daily cap ensure we alert instead of restart-looping.
- **Security**: `/monitor/*` never exposes env values or keys; token required; logs ring buffer redacts `Authorization` headers.

---

## 7. Answer to feasibility

Yes — fully possible. Render exposes both a restart API and deploy hooks; the existing maintenance-mode + graceful-shutdown code already contains the drain pattern; it only needs to be made non-interactive, cross-service (HTTP), and wired to a 1001 log detector. The monitor UI needs no framework: two polled JSON endpoints per service and one static HTML file.
