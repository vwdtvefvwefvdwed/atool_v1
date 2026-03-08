# Job System Architecture

## Overview

The job system handles two distinct job types — **normal jobs** (image/video generation) and **workflow jobs** (multi-step AI pipelines) — through a unified queue backed by Supabase. A single global coordinator serializes all job execution to prevent resource conflicts, while per-provider in-memory queues handle concurrency at the provider level.

---

## Components

| Component | File | Role |
|---|---|---|
| Job Creator | `jobs.py` | Creates jobs in DB, assigns priority queue |
| App Backend | `app.py` | Worker HTTP endpoints (`/complete`, `/fail`, `/reset`, `/progress`) + SSE dispatch |
| Job Worker | `job_worker_realtime.py` | Processes image/video jobs; realtime event listener |
| Job Coordinator | `job_coordinator.py` | Serializes all jobs; tracks active job in `job_queue_state` table |
| Workflow Manager | `workflow_manager.py` | Executes and resumes workflow jobs |
| Base Workflow | `workflows/base_workflow.py` | Step-by-step execution engine with checkpoint/retry logic |
| Workflow Retry Manager | `workflow_retry_manager.py` | Periodic retry of `pending_retry` workflows; startup backlog |
| API Key Listener | `job_worker_realtime.py` | Realtime listener on `provider_api_keys` table; re-triggers key-blocked jobs |
| Multi-Endpoint Manager | `multi_endpoint_manager.py` | Routes generation calls to the correct AI provider API |
| Cloudinary Manager | `cloudinary_manager.py` | Uploads generated images/videos for permanent storage |

---

## Database Tables

| Table | Purpose |
|---|---|
| `jobs` | Master job record: status, prompt, model, metadata, result URLs |
| `priority1_queue` | Jobs for users with ≤10 generations (highest priority) |
| `priority2_queue` | Jobs for users with 11–50 generations |
| `priority3_queue` | Jobs for users with >50 generations (lowest priority) |
| `workflow_executions` | Per-execution state: current step, checkpoints, error info, retry count |
| `job_queue_state` | Single-row global coordinator state: active job ID, type, models |
| `job_queue_log` | Audit trail of coordinator events (queued, started, completed, blocked) |
| `provider_api_keys` | Available API keys per provider; INSERT/UPDATE triggers job retry |
| `system_flags` | Runtime flags (e.g. `priority_lock`) |

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

The worker subscribes to **INSERT** and **UPDATE** events on the `jobs` table via Supabase Realtime WebSocket.

```
Supabase Realtime INSERT/UPDATE event
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
    │               ├── ALLOWED → set active job in job_queue_state
    │               └── BLOCKED → mark blocked_by_job_id in jobs table → RETURN
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
    │   [COORDINATOR LOCK]
    ├── clear_active_job() → job_queue_state: active_job_id = NULL
    └── process_next_queued_job()
            ├── Query: jobs WHERE status IN ('pending','pending_retry')
            │                  AND blocked_by_job_id IS NOT NULL
            │                  ORDER BY queued_at ASC
            ├── on_job_start(next_job_id, ...) → PRE-CLAIM slot in job_queue_state
            └── _trigger_job_processing(next_job)
                    └── Thread: process_job_with_concurrency_control(next_job)
                            └── on_job_start() sees self as pre-claimed → allowed through

mark_provider_free(provider_key, job_id)
    └── process_next_queued_job(provider_key) [IN-MEMORY QUEUE]
            └── Pop next in-memory queued job → spawn thread
```

---

## Retry Flow for Normal Jobs

### Transient Error Retry

```
reset_job_to_pending(job_id, provider_key, error_message)
    ├── Check pending_retry_count in metadata
    │       └── count >= MAX_PENDING_RETRIES (5) → mark_job_failed() STOP
    ├── Increment pending_retry_count in metadata
    ├── POST /worker/job/{id}/reset → jobs: status=pending, error_message=...
    │       └── Supabase Realtime UPDATE event fired → worker picks it up immediately
    └── _is_key_error(error_message)?
            ├── YES → silent wait (only api_key_realtime_listener re-triggers)
            └── NO  → spawn _deferred_retry thread (30s delay, then re-trigger)
```

### Periodic Retry Sweep (every 10 minutes)

```
retry_transient_errors()
    └── Query pending jobs WHERE error_message IS NOT NULL
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
            │       └── pending image/video jobs matching provider → process_job_with_concurrency_control()
            │
            ├── fetch_pending_retry_workflow_jobs_for_provider(provider_key)
            │       └── pending_retry workflows where failing step = this provider
            │           → retry_manager._resume_workflow(execution_id, job_id)
            │
            └── fetch_pending_workflow_jobs_for_provider(provider_key)
                    └── pending workflows with key-error message → execute_workflow() fresh
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

### 3. Coordinator Check

```
execute_workflow()
    ├── coordinator.get_workflow_models(workflow_config) → list of all model names in steps
    ├── coordinator.on_job_start(job_id, "workflow", required_models)
    │       ├── ALLOWED → continue
    │       └── BLOCKED:
    │               ├── jobs: blocked_by_job_id = active_job_id, status stays "pending"
    │               └── raise RuntimeError("Workflow queued: ...") → caller catches silently
    │
    └── Store required_models in workflow_executions
```

### 4. Step Execution (`base_workflow.execute`)

```
base_workflow.execute(input_data, user_id, job_id, resume=False)
    │
    ├── _get_or_create_execution() → workflow_executions record
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
            └── [HardError]:
                    ├── _save_checkpoint(..., {status=failed_permanent})
                    ├── execution: status=failed
                    ├── jobs: status=failed
                    └── raise → coordinator.on_job_complete() called → slot freed

    [All steps complete]:
        ├── execution: status=completed
        └── jobs: status=completed, image_url=final_output
```

### 5. Workflow Retry (`WorkflowRetryManager`)

Runs every 5 minutes in a background thread.

```
retry_pending_workflows()
    ├── Query: jobs WHERE status=pending_retry AND job_type=workflow
    │                  AND blocked_by_job_id IS NULL   ← skip coordinator-blocked jobs
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
    └── Query: jobs WHERE status=pending AND job_type=workflow
                       AND blocked_by_job_id IS NULL    ← skip blocked
                       AND created_at < NOW() - 2min    ← only stale ones
        └── Re-trigger as fresh execution (handles missed Realtime INSERT events)
```

---

## Coordinator State Machine

The `job_queue_state` table (single row, id=1) is the global serialization lock.

```
State: { active_job_id, active_job_type, active_models, started_at }

on_job_start(job_id, job_type, models):
    ├── active_job_id == NULL → SET active job → ALLOWED
    ├── active_job_id == job_id → SELF-RESERVATION (pre-claimed) → ALLOWED
    └── active_job_id != NULL → BLOCKED
            └── jobs: blocked_by_job_id = active_job_id

on_job_complete(job_id, job_type):
    ├── clear_active_job() → active_job_id = NULL
    └── process_next_queued_job()
            └── Find oldest blocked job → on_job_start() (pre-claim) → _trigger_job_processing()
```

**Thread safety**: `_coordinator_lock` is a `threading.RLock()`. All reads and writes to `job_queue_state` happen inside this lock. `on_job_complete` holds the lock across both `clear_active_job()` and `process_next_queued_job()` to prevent a new incoming job from stealing the slot before the next queued job is promoted.

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
                    │       └── If active_job_id's job is done/pending → clear_active_job()
                    ├── reset_running_jobs_to_pending()
                    │       └── All "running" jobs → reset to "pending" (crash recovery)
                    ├── process_all_pending_jobs()
                    │       └── fetch_all_pending_jobs() (HTTP API → DB fallback)
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
| Jobs running simultaneously | 1 | `job_queue_state` coordinator (serialized) |
| Jobs per provider (in-memory) | 1 active + unlimited queued | `provider_active_jobs` + `provider_job_queues` |
