# Job System Architecture

## Overview

The job system handles two distinct job types — **normal jobs** (image/video generation) and **workflow jobs** (multi-step AI pipelines) — through a unified queue backed by Supabase. A global coordinator tracks active jobs and prevents model/provider conflicts, while per-provider in-memory queues handle concurrency at the provider level.

Jobs that use different models can now run **in parallel**. Two jobs are only serialized when their required model sets overlap.

---

## Components

| Component | File | Role |
|---|---|---|
| Job Creator | `jobs.py` | Creates jobs in DB, assigns priority queue |
| App Backend | `app.py` | Worker HTTP endpoints (`/complete`, `/fail`, `/reset`, `/progress`) + SSE dispatch; executes workflow jobs |
| Job Worker | `job_worker_realtime.py` | Processes image/video normal jobs; realtime event listener; also handles workflow startup and pending_retry at startup |
| Job Coordinator | `job_coordinator.py` | Model-conflict-based parallel scheduler; tracks active jobs in `job_queue_state` table via atomic DB RPCs |
| Workflow Manager | `workflow_manager.py` | Executes and resumes workflow jobs |
| Base Workflow | `workflows/base_workflow.py` | Step-by-step execution engine with checkpoint/retry logic |
| Workflow Retry Manager | `workflow_retry_manager.py` | Periodic retry of `pending_retry` workflows; startup backlog |
| API Key Listener | `job_worker_realtime.py` | Realtime listener on `provider_api_keys` table; re-triggers key-blocked jobs |
| Multi-Endpoint Manager | `multi_endpoint_manager.py` | Routes generation calls to the correct AI provider API |
| Cloudinary Manager | `cloudinary_manager.py` | Uploads generated images/videos for permanent storage |

---

## Database Architecture

The system uses **two Supabase projects**:

| Project | ID | Contents |
|---|---|---|
| **Main DB** | `gtgnwrwbcxvasgetfzby` | `jobs`, `users`, priority queues, `workflow_executions`, `system_flags` |
| **Worker1 DB** | `gmhpbeqvqpuoctaqgnum` | `job_queue_state`, `job_queue_log`, `provider_api_keys` |

This distinction is critical. The coordinator (`job_coordinator.py`) must use **two separate clients**:

```python
self.main_supabase = supabase          # from supabase_client — main DB for 'jobs' table
self.supabase      = get_worker1_client()  # Worker1 DB for 'job_queue_state', 'job_queue_log'
```

**Any code that queries `jobs`, `workflow_executions`, or the priority queues must use the main DB client.** Using the Worker1 client for these tables results in silent no-ops (the table doesn't exist in Worker1, Supabase returns an empty result with no error).

### Main DB Tables

| Table | Purpose |
|---|---|
| `jobs` | Master job record: status, prompt, model, metadata, result URLs, `blocked_by_job_id`, `required_models` |
| `priority1_queue` | Jobs for users with ≤10 generations (highest priority) |
| `priority2_queue` | Jobs for users with 11–50 generations |
| `priority3_queue` | Jobs for users with >50 generations (lowest priority) |
| `workflow_executions` | Per-execution state: current step, checkpoints, error info, retry count |
| `system_flags` | Runtime flags (e.g. `priority_lock`) |

### Worker1 DB Tables

| Table | Purpose |
|---|---|
| `job_queue_state` | Single-row global coordinator state: `active_jobs` JSONB array |
| `job_queue_log` | Audit trail of coordinator events (queued, started, completed, blocked) |
| `provider_api_keys` | Available API keys per provider; INSERT/UPDATE triggers job retry |

### Job Status State Machine

```
                   ┌─────────────────────────────────────────┐
                   │                                         │
          ┌────────▼────────┐                               │
          │    pending      │ ◄── created / reset / re-triggered
          └────────┬────────┘
                   │ worker picks up job
          ┌────────▼────────┐
          │    running      │
          └────────┬────────┘
         ╱         │         ╲
        ╱          │          ╲
┌──────▼──┐  ┌────▼─────┐  ┌──▼──────────┐
│completed│  │  failed  │  │pending_retry│ (workflow only)
└─────────┘  └──────────┘  └──────┬──────┘
                                   │ retry manager / coordinator
                                   └──────► running (resume)
```

**Transition rules:**
- `pending` → `running`: worker starts processing
- `running` → `completed`: generation + Cloudinary upload succeeded
- `running` → `failed`: hard error (bad input, unsupported format, max retries exceeded)
- `running` → `pending`: retryable error (network, timeout, Cloudinary) — reset for retry
- `running` → `pending_retry`: workflow step failed with retryable error — resume from that step
- `running` → `pending`: coordinator blocked the job while status was `running` — mark_job_queued() resets it
- `pending_retry` → `running`: retry manager or coordinator resumes workflow
- `pending` / `running` → `cancelled`: user cancels job

---

## Normal Job Flow (Image / Video)

### 1. Job Creation (`jobs.py: create_job`)

```
User API call
    │
    ├── UNLIMITED_MODE + simple image?
    │       └── Batch RPC (create_job_batch) ─► 1 DB call for jobs + queue insert
    │
    └── Traditional path:
            ├── Read user credits + generation_count
            ├── Atomic increment generation_count (RPC: increment_generation_count)
            ├── Assign priority level: ≤10 → P1, ≤50 → P2, >50 → P3
            ├── INSERT into jobs (status=pending, metadata={priority, duration?, input_image_url?})
            ├── INSERT into priority queue table (priorityN_queue)
            └── Deduct 1 credit (unless UNLIMITED_MODE)
```

**Returned**: `{ job_id, status, priority, credits_remaining }`

### 2. Worker Receives Job (Realtime)

The worker subscribes to **INSERT** events on the `jobs` table via Supabase Realtime WebSocket.

> **Note**: The UPDATE channel subscription was removed (Issue #21). Jobs reset to `pending` after a transient error are handled by dedicated retry paths, not the realtime UPDATE event.

```
Supabase Realtime INSERT event
    │
    ▼
handle_new_job(payload)
    ├── status != "pending" → SKIP
    ├── job_type == "workflow" → route to workflow path (separate branch)
    └── Spawn thread: process_job_with_concurrency_control(job)
```

### 3. Concurrency Control (`_process_job_with_concurrency_control_inner`)

```
_process_job_with_concurrency_control_inner(job)
    │
    ├── Maintenance mode? → SKIP
    ├── Semaphore acquire (max 40 concurrent threads)
    ├── Priority lock? (P2/P3 blocked if lock active)
    ├── validate_job_inputs() → mark_job_failed() if invalid
    ├── Determine provider_key (from metadata or model mapping)
    │
    ├── [ACQUIRE PROVIDER LOCK]
    │       ├── Provider busy? → enqueue_job(in-memory queue) → RETURN
    │       └── coordinator.on_job_start(job_id, "normal", [model])
    │               ├── ALLOWED (no model conflict) → claim slot in job_queue_state
    │               └── BLOCKED (model conflict)   → mark blocked_by_job_id in jobs → RETURN
    │   [RELEASE PROVIDER LOCK]
    │
    ├── mark_provider_busy(provider_key, job_id)
    │
    ├── process_job(job)
    │       └── → process_image_job() or process_video_job()
    │
    └── [FINALLY BLOCK] (always executes)
            ├── coordinator.on_job_complete(job_id, "normal")  ← clears slot FIRST
            └── mark_provider_free(provider_key, job_id)        ← then frees provider
```

> **Order matters**: coordinator is cleared before the provider is freed, so any in-memory queued job that becomes runnable doesn't hit a stale coordinator lock.

### 4. Job Processing (`process_image_job` / `process_video_job`)

```
process_image_job(job) / process_video_job(job)
    │
    ├── POST /worker/job/{id}/progress (10%) → DB + SSE to frontend
    ├── get_api_key_for_job(model, provider_key)
    │       └── No key available?
    │               ├── notify_error(NO_API_KEY_FOR_PROVIDER)
    │               └── reset_job_to_pending(job_id, provider_key, "No API key...") → RETURN
    │
    ├── generate(prompt, model, api_key, ...) → multi_endpoint_manager
    │       └── failure? → raise Exception(error_msg)
    │
    ├── Download result (URL) OR decode base64
    ├── Compress image if > 10 MB (image jobs only)
    ├── Cloudinary upload → permanent URL
    │
    └── POST /worker/job/{id}/complete
            ├── update jobs: status=completed, image_url, video_url, progress=100
            └── SSE dispatch to frontend

    [On any Exception]:
        ├── INVALID_IMAGE_FORMAT / IMAGE_NOT_SUPPORTED → mark_job_failed() (hard)
        ├── Input image download failure → mark_job_failed() (hard)
        ├── Completion API failure → mark_job_failed() (hard, video already generated)
        ├── Cloudinary error → reset_job_to_pending() + 30s deferred retry
        ├── Timeout / Network error → reset_job_to_pending() + 30s deferred retry
        ├── Validation error (user input) → mark_job_failed() (hard)
        └── API / provider error:
                ├── handle_api_key_rotation() → try next key → retry process_*_job()
                └── No keys left → reset_job_to_pending("No API key...") — waits for key insert
```

### 5. Job Completion Notification Chain

```
POST /worker/job/{id}/complete (app.py)
    ├── update_job_result() → jobs table: status=completed, progress=100, image_url
    ├── use_provider_trial() → mark trial used for this provider/user
    ├── Clear pending_retry_count from metadata
    └── SSE dispatch → realtime_manager._dispatch_event(job_id, payload)
            └── Frontend receives job update → shows result image/video
```

### 6. Queue Drain After Job Completes

```
coordinator.on_job_complete(job_id, "normal")
    │
    ├── release_slot(job_id) → RPC: release_coordinator_slot(job_id)
    │       └── Atomically removes job_id from active_jobs JSONB array
    │
    └── process_next_queued_job()
            ├── Query main DB: jobs WHERE status IN ('pending','pending_retry')
            │                          AND blocked_by_job_id IS NOT NULL
            │                          ORDER BY queued_at ASC
            │
            ├── For EACH queued job (not just first):
            │       ├── Get required_models (re-extract from config if empty)
            │       ├── try_claim_slot(job_id, job_type, required_models) → RPC
            │       │       ├── claimed        → pre-claimed, clear_job_queue_info(), trigger
            │       │       ├── already_active → already holds slot, clear_job_queue_info(), trigger
            │       │       └── conflict       → still blocked by another active job, skip
            │       │
            │       └── (workflow) set status=running BEFORE clearing blocked_by_job_id
            │               → closes retry-manager race window
            │
            └── All non-conflicting queued jobs are triggered in parallel

mark_provider_free(provider_key, job_id)
    └── process_next_queued_job(provider_key) [IN-MEMORY QUEUE]
            └── Pop next in-memory queued job → spawn thread
```

---

## Retry Flow for Normal Jobs

### Transient Error Retry

```
reset_job_to_pending(job_id, provider_key, error_message)
    ├── Check pending_retry_count in metadata (main DB client)
    │       └── count >= MAX_PENDING_RETRIES (5) → mark_job_failed() STOP
    ├── Increment pending_retry_count in metadata
    ├── POST /worker/job/{id}/reset → jobs: status=pending, error_message=...
    └── _is_key_error(error_message)?
            ├── YES → silent wait (only api_key_realtime_listener re-triggers)
            └── NO  → spawn _deferred_retry thread (30s delay, then re-trigger)
```

### Periodic Retry Sweep (every 10 minutes)

```
retry_transient_errors()
    └── Query pending jobs WHERE error_message IS NOT NULL
                              AND blocked_by_job_id IS NULL   ← skip coordinator-blocked
        ├── Skip key-error jobs (waiting for API key insert)
        └── Retry cloudinary/timeout/network error jobs → process_job_with_concurrency_control()
```

### API Key Insertion Trigger

```
api_key_realtime_listener  (Supabase Realtime on provider_api_keys)
    └── handle_api_key_insertion(payload)
            ├── Resolve provider_id → provider_name
            │
            ├── fetch_pending_jobs_for_provider(provider_key)
            │       └── pending image/video jobs matching provider
            │           AND blocked_by_job_id IS NULL          ← skip blocked
            │           → re-check status before spawning thread
            │           → process_job_with_concurrency_control()
            │
            ├── fetch_pending_retry_workflow_jobs_for_provider(provider_key)
            │       └── pending_retry workflows where failing step = this provider
            │           AND blocked_by_job_id IS NULL          ← skip blocked
            │           → retry_manager._resume_workflow(execution_id, job_id)
            │
            └── fetch_pending_workflow_jobs_for_provider(provider_key)
                    └── pending workflows with key-error message
                        AND blocked_by_job_id IS NULL          ← skip blocked
                        → execute_workflow() fresh
```

---

## Workflow Job Flow

### 1. Creation

Same `create_job()` path with `job_type="workflow"`. The `model` field holds the `workflow_id`. The input image is stored in `jobs.image_url` or `metadata.input_image_url`.

### 2. Worker Routes to Workflow Manager

```
Realtime INSERT → handle_new_job()
    └── job_type == "workflow"
            └── Thread: workflow_manager.execute_workflow(
                    workflow_id=job.model,
                    input_data=image_url,
                    user_id, job_id
                )
```

> **UPDATE events for workflow jobs are skipped** in `handle_new_job()`. When the coordinator unblocks a workflow job, it directly spawns `_trigger_job_processing()` — no Realtime event is involved.

### 3. Coordinator Check

```
execute_workflow()
    ├── coordinator.get_workflow_models(workflow_config) → list of all model names in steps
    │
    ├── coordinator_slot_claimed = False
    ├── coordinator.on_job_start(job_id, "workflow", required_models)
    │       ├── ALLOWED → coordinator_slot_claimed = True → continue
    │       └── BLOCKED (model conflict with active job):
    │               ├── mark_job_queued(): jobs.blocked_by_job_id = conflicting_job_id
    │               ├── mark_job_queued(): status running → pending (guarded reset)
    │               └── raise RuntimeError("Workflow queued: ...") → caller catches silently
    │                   NOTE: coordinator_slot_claimed stays False → finally does NOT call on_job_complete
    │
    └── Store required_models in workflow_executions
```

### 4. Step Execution (`base_workflow.execute`)

```
base_workflow.execute(input_data, user_id, job_id, resume=False)
    │
    ├── _get_or_create_execution() → workflow_executions record
    │       └── If execution already exists → always reuse (never INSERT a second one)
    ├── jobs: status = "running"
    │
    └── For step i in [start_step .. N-1]:
            ├── Check quota for this step's provider:model
            │       └── Quota exceeded → raise RetryableError(quota_exceeded)
            │
            ├── _execute_step(step, i, execution, input_data)
            │       └── Calls step_{step_name}(input_data, step) on the workflow subclass
            │           with timeout (default 600s)
            │
            ├── [SUCCESS]:
            │       ├── _save_checkpoint(execution_id, step_index, {output, status=completed})
            │       ├── execution.current_step = i + 1
            │       └── quota_manager.increment_quota(provider, model)
            │
            ├── [RetryableError]:
            │       ├── _save_checkpoint(..., {status=failed_retryable, error_type})
            │       ├── execution: status=pending_retry, error_info={error_type, model, provider, step}
            │       ├── jobs: status=pending_retry
            │       └── raise → coordinator.on_job_complete() called → slot freed
            │
            └── [HardError / Infrastructure error]:
                    ├── HardError: execution: status=failed, jobs: status=failed
                    ├── Infrastructure error: jobs: status=pending_retry (for retry sweep)
                    └── raise → coordinator.on_job_complete() called → slot freed

    [All steps complete]:
        ├── execution: status=completed
        └── jobs: status=completed, image_url=final_output
```

### 5. Workflow Retry (`WorkflowRetryManager`)

Runs every 5 minutes in a background thread.

```
retry_pending_workflows()
    ├── Query main DB: jobs WHERE status=pending_retry AND job_type=workflow
    │                          AND blocked_by_job_id IS NULL   ← skip coordinator-blocked
    ├── For each job:
    │       ├── Check execution.retry_count >= max_retries (5) → mark_failed()
    │       └── _can_retry(execution)?
    │               ├── quota_exceeded  → check quota + backoff (300s)
    │               ├── invalid_key     → SKIP (wait for API key insert only)
    │               ├── no_api_key      → SKIP (wait for API key insert only)
    │               ├── rate_limit      → check retry_after elapsed
    │               ├── timeout         → always retry (30s backoff)
    │               └── generic_api_error → always retry (180s backoff)
    └── _resume_workflow(execution_id, job_id)
            └── workflow_manager.resume_workflow()
                    ├── Status guard: skip if job not in pending_retry/pending
                    ├── coordinator.on_job_start() → may block again
                    └── base_workflow.execute(resume=True, start_step=current_step)
```

### 6. Stale Pending Workflow Sweep

```
retry_stale_pending_workflows()  (runs every 5 minutes)
    └── Query main DB: jobs WHERE status=pending AND job_type=workflow
                               AND blocked_by_job_id IS NULL    ← skip blocked
                               AND created_at < NOW() - 2min    ← only stale ones
        └── Re-trigger as fresh execution (handles missed Realtime INSERT events)
```

---

## Coordinator State Machine

The `job_queue_state` table (Worker1 DB, single row, id=1) holds the `active_jobs` JSONB array.

```
State: { active_jobs: [ {job_id, job_type, models, started_at}, ... ] }

on_job_start(job_id, job_type, models):
    RPC: try_claim_coordinator_slot(job_id, job_type, models)
        ── SELECT FOR UPDATE (row-level lock — atomic across all OS processes)
        ├── job_id already in active_jobs → "already_active" → ALLOWED
        │       clear_job_queue_info(job_id)
        ├── models ∩ any active slot models == ∅ → "claimed" → ALLOWED
        │       append {job_id, job_type, models} to active_jobs
        │       clear_job_queue_info(job_id)
        └── models ∩ any active slot models != ∅ → "conflict" → BLOCKED
                mark_job_queued(job_id, conflicting_job_id)
                reset status running → pending (guarded)

on_job_complete(job_id, job_type):
    RPC: release_coordinator_slot(job_id)
        ── SELECT FOR UPDATE
        └── Remove job_id entry from active_jobs array
    process_next_queued_job()
        └── Trigger ALL non-conflicting queued jobs in FIFO order
```

**Cross-process atomicity**: The `try_claim_coordinator_slot` and `release_coordinator_slot` stored procedures use `SELECT FOR UPDATE` on the singleton `job_queue_state` row. Any concurrent call from any OS process blocks at the DB level until the current transaction commits. `threading.RLock` is no longer used for coordinator operations.

**In-memory cache**: `_active_jobs_cache` (Python list) provides a fast self-reservation check within the same process, avoiding a DB round-trip when a pre-claimed job's thread calls `on_job_start()`. The cache is **not** authoritative — the DB RPC is always the source of truth for cross-process decisions.

---

## Parallel Execution Rules

| Scenario | Behavior |
|---|---|
| Workflow A running + normal image job arrives | **Parallel** — workflows use `vision-aicc` + `clipdrop`; image jobs use entirely different providers |
| Workflow A running + Workflow B arrives | **Blocked** — both require `vision-aicc` + `clipdrop` → model conflict |
| Normal job (sdxl) running + normal job (flux) arrives | **Parallel** — different models |
| Normal job (sdxl) running + another normal job (sdxl) arrives | **Blocked** — same model |
| Workflow A running + job using `gemini-25-flash-aicc` arrives | **Blocked** — model overlap |

The conflict check is based purely on **model name intersection**. Any two jobs whose `required_models` lists share at least one string are serialized; otherwise they run in parallel.

---

## Worker Startup Sequence

```
main()
    ├── Flask health server starts (foreground, port from $PORT)
    └── worker_thread: start_realtime()
            │
            ├── realtime_thread: run_async_listener()
            │       ├── _run_with_reconnect(realtime_listener, ...)    ← auto-reconnects
            │       └── _run_with_reconnect(api_key_realtime_listener, ...)
            │
            └── startup_thread: worker_startup_tasks()
                    ├── _load_priority_lock_from_db()
                    ├── validate_job_queue_state_on_startup()
                    │       ├── Read active_jobs array from job_queue_state
                    │       ├── For each slot job_id: check if job is done/pending in main DB
                    │       │       └── Stale → coordinator.release_slot(job_id)  (not blanket clear)
                    │       └── If no active jobs remain → state is clean
                    ├── reset_running_jobs_to_pending()
                    │       └── All "running" jobs → reset to "pending" (crash recovery)
                    ├── process_all_pending_jobs()
                    │       └── fetch_all_pending_jobs() (HTTP API → DB fallback)
                    │           AND blocked_by_job_id IS NULL   ← exclude coordinator-blocked
                    │           → process_job_with_concurrency_control() per job
                    └── process_all_pending_workflow_jobs()
                            ├── retry_manager.process_pending_workflows()
                            └── retry_manager.process_retryable_workflows()
```

### Main Worker Loop (after startup)

```
while True:
    sleep(5)
    │
    ├── [every 30s] heartbeat log
    └── [every 10min] retry_transient_errors()
            └── Re-trigger cloudinary/network/timeout failed pending jobs
                AND blocked_by_job_id IS NULL   ← exclude coordinator-blocked
```

---

## API Key Rotation (Normal Jobs)

When a provider API call returns an error:

```
handle_api_key_rotation(api_key_id, provider_key, error_message, job_id)
    ├── Mark current key as failed/rate-limited
    ├── Fetch next available key for provider
    │       ├── Found → return (rotation_success=True, next_key)
    │       └── Not found → return (rotation_success=False, None)
    │
[In process_image/video_job]:
    ├── rotation_success + next_key → retry process_*_job(job) with new key
    └── rotation failed → reset_job_to_pending("No API key...") ← waits for key insert
```

---

## SSE (Server-Sent Events) to Frontend

All job state changes dispatch SSE events to connected frontend clients:

```
app.py worker endpoints (/complete, /fail, /reset, /progress)
    └── realtime_manager._dispatch_event(job_id, payload)
            └── Frontend JobStatus component receives update → re-renders
```

The SSE channel is per-`job_id`. Frontend subscribes on job creation and unsubscribes on terminal state (completed/failed/cancelled).

---

## Priority Queue System

Three Supabase tables partition job priority by the user's total generation count:

| Queue | Users | Field |
|---|---|---|
| `priority1_queue` | generation_count ≤ 10 | New/light users — fastest |
| `priority2_queue` | generation_count 11–50 | Regular users |
| `priority3_queue` | generation_count > 50 | Power users — lowest priority |

The worker's `get_next_priority_job` RPC returns the highest-priority unprocessed entry across all three tables in a single DB call.

**Priority lock**: When `system_flags.priority_lock = true`, the worker only processes `priority1_queue` jobs. Setting it to `false` triggers `process_all_pending_jobs()` to flush the P2/P3 backlog immediately.

---

## Concurrency Limits

| Layer | Limit | Mechanism |
|---|---|---|
| Global job threads | 40 | `_job_thread_semaphore` (BoundedSemaphore) |
| Workflow retry threads | 10 | `_workflow_thread_semaphore` (BoundedSemaphore) |
| Jobs running simultaneously | Unlimited if no model conflict | `active_jobs` JSONB array + `try_claim_coordinator_slot` RPC |
| Two jobs with overlapping models | 1 at a time (serialized) | Model-conflict check inside `try_claim_coordinator_slot` |
| Jobs per provider (in-memory) | 1 active + unlimited queued | `provider_active_jobs` + `provider_job_queues` |

---

## Coordinator DB Stored Procedures (Worker1)

All stored procedures live in the Worker1 DB and are called via Supabase RPC.

### `try_claim_coordinator_slot(p_job_id TEXT, p_job_type TEXT, p_models JSONB)`

- Acquires `SELECT FOR UPDATE` row lock on `job_queue_state WHERE id=1`
- **Pass 1**: checks if `p_job_id` already holds a slot → returns `already_active`
- **Pass 2**: checks if any model in `p_models` appears in any active slot's models → returns `conflict` with `conflicting_models`
- **If no conflict**: appends new entry to `active_jobs`, returns `claimed`

Returns JSONB: `{ "result": "claimed"|"already_active"|"conflict", "active_jobs": [...], "conflicting_models": [...] }`

### `release_coordinator_slot(p_job_id TEXT)`

- Acquires `SELECT FOR UPDATE` row lock
- Rebuilds `active_jobs` array excluding the entry with `job_id = p_job_id`
- Idempotent — safe to call even if `p_job_id` is not in the array

Returns JSONB: `{ "result": "released", "active_jobs": [...remaining...] }`

### `reset_all_coordinator_slots()`

- Clears `active_jobs = '[]'`
- Also nulls legacy columns (`active_job_id`, `active_job_type`, `active_models`) for backward compat
- Used at startup for full reset; prefer `release_coordinator_slot` for targeted release

### Migration

The SQL to create the column and all three stored procedures is in:
`backend/migrations/worker1_028_parallel_coordinator_slots.sql`

Run this migration against the **Worker1** DB only (`gmhpbeqvqpuoctaqgnum`).
