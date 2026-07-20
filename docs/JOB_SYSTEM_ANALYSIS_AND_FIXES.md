# Job System — Deep Analysis & Fixes

**Date**: 2026-03-04  
**Scope**: Normal jobs (image/video) and Workflow jobs — full lifecycle from request to completion, retry after failure, and restart after API key insertion.

---

## Architecture Overview

Two separate processes handle the job system:

| Process | File | Role |
|---------|------|------|
| **Web API Server** | `app.py` | Handles HTTP requests from frontend; starts `WorkflowRetryManager` periodic loop |
| **Job Worker** | `job_worker_realtime.py` | Listens for Supabase Realtime events; processes image/video jobs; triggers workflow execution |

---

## Normal Job Lifecycle

```
Frontend → POST /api/jobs
  → create_job()  [jobs.py]
      ├─ Batch RPC (image-only, no image_url) → OR traditional method
      ├─ INSERT into jobs table  (status=pending)
      └─ INSERT into priority queue (priority1/2/3 by generation_count)

Supabase Realtime INSERT event on jobs table
  → handle_new_job()  [job_worker_realtime.py]
  → process_job_with_concurrency_control()
      ├─ Maintenance mode check
      ├─ Thread semaphore (max 40 concurrent)
      ├─ validate_job_inputs()
      ├─ Priority lock check (P1 only if active)
      ├─ Provider lock (per-provider concurrency)
      ├─ coordinator.on_job_start()  [serializes jobs globally]
      └─ process_job() → process_image_job() / process_video_job()
           ├─ Quota check
           ├─ get_api_key_for_job()
           │     └─ No key → reset_job_to_pending()
           │                  pending + deferred 30s retry thread
           ├─ generate()  [API call to provider]
           ├─ Download result + Cloudinary upload
           └─ POST /worker/job/{id}/complete → status=completed
                OR on error:
                  ├─ Hard errors (validation/format/removebg)
                  │     → mark_job_failed() → status=failed
                  ├─ Key errors
                  │     → reset_job_to_pending() → waits for key insertion
                  ├─ Transient (network/timeout/cloudinary)
                  │     → reset_job_to_pending() + 30s deferred retry
                  └─ API errors
                        → handle_api_key_rotation() → retry or reset_to_pending
```

---

## Workflow Job Lifecycle

```
Frontend → POST /api/workflow/execute
  → create_job(job_type="workflow")
      └─ INSERT into jobs table  (status=pending)

Supabase Realtime INSERT event
  → handle_new_job() → _start_new_workflow() thread
  → workflow_manager.execute_workflow()
      → coordinator.on_job_start()
          ├─ BLOCKED → RuntimeError  (no execution record created yet)
          │   jobs: status=pending, blocked_by_job_id=<blocker>
          │   coordinator.process_next_queued_job() will re-trigger when blocker finishes
          └─ ALLOWED → base_workflow.execute()
               → jobs.status = running
               → Step loop (per workflow step):
                   ├─ Step success
                   │     → checkpoint saved → continue to next step
                   └─ Step fails:
                       ├─ RetryableError
                       │     → jobs.status = pending_retry
                       │     → workflow_executions: status=pending_retry, error_info
                       │     → coordinator.on_job_complete() called in finally
                       └─ HardError
                             → jobs.status = failed
               → coordinator.on_job_complete()  [on full success]

Retry loop in app.py  [WorkflowRetryManager — every 300s]
  → retry_pending_workflows()
      → query jobs WHERE status=pending_retry AND job_type=workflow
      → _can_retry(execution):
          ├─ invalid_key / no_api_key  → False  (only retry on key insertion)
          ├─ quota_exceeded            → check quota + 300s backoff
          ├─ rate_limit               → check retry_after elapsed
          ├─ timeout / api_error      → True  (with backoff)
          └─ True → _resume_workflow(execution_id, job_id)
                      → workflow_manager.resume_workflow()
                          → coordinator.on_job_start()
                              └─ blocked → RuntimeError (re-queued)

  → retry_stale_pending_workflows()
      → query jobs WHERE status=pending AND job_type=workflow
            AND blocked_by_job_id IS NULL   [after Fix 7]
            AND created_at < 2 min ago
      → skip jobs with key-error messages
      → re-trigger as fresh executions

API Key Insertion  [api_key_realtime_listener]
  → handle_api_key_insertion()
      ├─ fetch_pending_jobs_for_provider()
      │     → reprocess pending image/video jobs with key errors
      ├─ fetch_pending_retry_workflow_jobs_for_provider()
      │     → resume pending_retry workflow jobs for this provider
      └─ fetch_pending_workflow_jobs_for_provider()   [NEW — Fix 3]
            → re-trigger pending workflow jobs with key errors for this provider
```

---

## Issues Found & Fixes Applied

### 🔴 Critical Issues

---

#### Issue 1 — `pending_retry` Workflows Re-blocked by Coordinator are Permanently Stuck

**Root cause**: `coordinator.process_next_queued_job()` queried only `status='pending'`. When a `pending_retry` workflow resumed via `_resume_workflow()` → `coordinator.on_job_start()` and got blocked (another job was running), the job ended up with:
- `jobs.status = pending_retry`
- `jobs.blocked_by_job_id = <blocker>`

The coordinator's next-job finder never saw it (wrong status filter), and the periodic retry loop skipped it permanently for `invalid_key`/`no_api_key` errors.

**Fix 1** — `job_coordinator.py` → `process_next_queued_job()`

```python
# Before
.eq('status', 'pending')

# After
.in_('status', ['pending', 'pending_retry'])
```

---

#### Issue 2 — Realtime Listener Only Subscribed to INSERT Events

**Root cause**: The realtime channel used `event="INSERT"`. When `reset_job_to_pending()` performed a DB UPDATE, no realtime event fired. Recovery depended on the 30s deferred thread (transient errors) or the 10-min periodic sweep — both are delayed.

**Fix 2** — `job_worker_realtime.py` → `realtime_listener()`

Added a second channel subscription for `event="UPDATE"` on the `jobs` table, using the same `handle_new_job()` callback. The existing guard `if status != "pending": return` and the status re-check inside `process_job()` prevent duplicate processing.

---

#### Issue 3 — Pending Workflow Jobs with Key Errors Not Retried on Key Insertion

**Root cause**: `handle_api_key_insertion()` called:
- `fetch_pending_jobs_for_provider()` — explicitly excluded workflow jobs (`.neq("job_type", "workflow")`)
- `fetch_pending_retry_workflow_jobs_for_provider()` — required `status=pending_retry`

Workflow jobs stuck at `status=pending` with a key-error message had no retry trigger when a key was inserted. The stale sweep also explicitly skipped them.

**Fix 3** — `job_worker_realtime.py`

Added `fetch_pending_workflow_jobs_for_provider()` that queries `status=pending` workflow jobs with key-error messages matching the inserted provider. Wired as a third fetch in `handle_api_key_insertion()`, re-triggering matched jobs as fresh executions.

---

### 🟠 Significant Issues

---

#### Issue 4 — `process_retryable_workflows()` at Startup Bypassed Backoff

**Root cause**: The startup path (`process_retryable_workflows()`) called `_resume_workflow()` directly for all `pending_retry` workflows without calling `_can_retry()`. The periodic loop (`retry_pending_workflows()`) correctly checked backoff, but startup did not. A workflow that failed 30 seconds ago with `quota_exceeded` (300s backoff) would be retried immediately on worker restart, fail again, and waste a retry count.

**Fix 4** — `workflow_retry_manager.py` → `process_retryable_workflows()`

Added a synchronous backoff check using a temporary event loop before each resume:

```python
_check_loop = asyncio.new_event_loop()
can_retry = _check_loop.run_until_complete(self._can_retry(execution))
_check_loop.close()
if not can_retry:
    continue   # wait for periodic loop
```

---

#### Issue 5 — `mark_job_failed` and `reset_job_to_pending` Depended on Backend HTTP

**Root cause**: Both functions used `requests.post()` to call the backend web server (`/worker/job/{id}/fail` and `/worker/job/{id}/reset`). If the backend was down, overloaded, or timing out, jobs remained stuck in `running` status permanently (only recovered by worker restart). The completion path already had a direct-DB fallback via `update_job_result()`, but fail/reset did not.

**Fix 5** — `job_worker_realtime.py`

Added a `(requests.exceptions.Timeout, requests.exceptions.RequestException)` except block in both functions that writes the status update directly to Supabase when the HTTP call fails. The deferred 30s retry thread is also re-spawned from the fallback path for non-key transient errors.

> **Note**: SSE events to connected frontend clients are not emitted when the DB fallback is used (those fire from the backend endpoint). Frontend realtime subscription or polling will still catch the status change via the DB update.

---

#### Issue 7 — Stale Sweep Re-triggered Coordinator-Blocked Workflow Jobs Uselessly

**Root cause**: `retry_stale_pending_workflows()` queried for `status=pending AND job_type=workflow AND created_at < 2min`. It did not filter on `blocked_by_job_id`, so coordinator-blocked jobs (which already had a proper queue position managed by the coordinator) were re-triggered every 300 seconds. Each re-trigger hit `coordinator.on_job_start()`, got blocked again, and the `RuntimeError` was caught silently — wasted threads and DB ops.

**Fix 7** — `workflow_retry_manager.py` → `retry_stale_pending_workflows()`

```python
# Added
.is_('blocked_by_job_id', 'null')
```

---

#### Issue 8 — No Thread Throttling for Workflow Retry Execution

**Root cause**: All three workflow spawn sites (`retry_stale_pending_workflows`, `process_pending_workflows`, `process_retryable_workflows`) created threads with bare `threading.Thread(...).start()` and no limit. Under a large backlog, this could create hundreds of simultaneous threads causing memory pressure and thrashing.

Normal job processing already had `_job_thread_semaphore = BoundedSemaphore(40)`.

**Fix 8** — `workflow_retry_manager.py`

Added module-level:
```python
_workflow_thread_semaphore = threading.BoundedSemaphore(10)

def _spawn_workflow_thread(target_fn, name="WorkflowThread"):
    def _guarded():
        if not _workflow_thread_semaphore.acquire(timeout=5):
            logger.warning(f"Workflow thread limit reached — skipping {name}")
            return
        try:
            target_fn()
        finally:
            _workflow_thread_semaphore.release()
    threading.Thread(target=_guarded, daemon=True, name=name).start()
```

All three spawn sites replaced with `_spawn_workflow_thread(fn, name=f"<Type>WF-{job_id}")`.

---

#### Issue 9 — Race Condition Between Coordinator State Clear and Next-Job Trigger

**Root cause**: `on_job_complete()` performed two sequential operations without holding a lock across both:
1. `clear_active_job()` — clears `job_queue_state`
2. `process_next_queued_job()` — finds and triggers next blocked job

Between steps 1 and 2, a concurrent realtime INSERT event could start a new job, call `on_job_start()`, see an empty slot, and mark itself as active — before the previously queued job was promoted. The queued job would then be re-blocked.

`_coordinator_lock` is already an `RLock` (re-entrant), so `on_job_start()` called from inside `process_next_queued_job()` can re-acquire it safely.

**Fix 9** — `job_coordinator.py` → `on_job_complete()`

```python
with _coordinator_lock:          # hold across BOTH steps
    if not self.clear_active_job():
        return False
    self.process_next_queued_job()
```

---

### 🟡 Minor / Behavioral Issues

---

#### Issue 10 — All Backlog Jobs Submitted Simultaneously at Startup

**Root cause**: `process_all_pending_jobs()` launched all pending image/video jobs in rapid succession without any delay. All threads simultaneously contended on the coordinator lock and each set `blocked_by_job_id` in the DB at the same instant. Functionally correct (coordinator chains them correctly), but created a burst of DB lock contention and threads.

**Fix 10** — `job_worker_realtime.py` → `process_all_pending_jobs()`

Added `time.sleep(0.1)` after each thread start. 100ms × N jobs is negligible for any realistic backlog size and allows coordinator lock to settle between submissions so `queued_at` timestamps are recorded in order.

---

#### Issue 11 — All Workflows Assumed to Require Input Image

**Root cause**: `process_pending_workflows()` hard-failed any workflow job without `image_url` / `metadata.input_image_url`, regardless of whether the workflow actually required an image. Any future workflow with no image requirement would be incorrectly failed permanently at startup.

**Fix 11** — `workflow_retry_manager.py` → `process_pending_workflows()`

```python
workflow_config = workflow_manager.get_workflow(workflow_id) or {}
requires_image = workflow_config.get('requires_input_image', True)  # default True

if requires_image and not image_url:
    # hard fail
    ...
```

Default is `True`, so all existing workflows behave identically. Also guarded `image_url[:50]` print against `None` to prevent `TypeError` for no-image workflows.

**To enable for a new workflow**: add `"requires_input_image": false` to its config dict.

---

#### Issue 12 — Empty `error_message` Jobs Re-triggered on Any Key Insertion

**Root cause**: `fetch_pending_jobs_for_provider()` passed all jobs with an empty `error_message` through to the matching stage (the key-keyword filter only applied when `error_msg` was non-empty). A pending job with no error message that mapped to the same provider as a newly inserted key would be re-triggered, even if it was an old stuck job with a completely unrelated issue.

**Fix 12** — `job_worker_realtime.py` → `fetch_pending_jobs_for_provider()`

```python
if error_msg:
    if not any(k in error_msg for k in _api_key_keywords):
        continue   # non-key error — skip
else:
    # No error yet — only include if job is recent (< 30 min old)
    if (job.get("created_at") or "") < recent_cutoff:
        continue
```

Old no-error pending jobs (> 30 min) are no longer re-triggered on every key insertion.

---

## Files Changed

| File | Fixes Applied |
|------|--------------|
| `backend/job_coordinator.py` | Fix 1, Fix 9 |
| `backend/job_worker_realtime.py` | Fix 2, Fix 3, Fix 5, Fix 10, Fix 12 |
| `backend/workflow_retry_manager.py` | Fix 4, Fix 7, Fix 8, Fix 11 |

## Schema Changes Required

**None.** All fixes are pure Python code changes. No database migrations needed. All columns referenced (`blocked_by_job_id`, `status`, `created_at`, `error_message`, `job_type`) already exist in the schema.

## What Was Left Unchanged

- **Global job serialization** (`JobCoordinator.can_start_job` — one job at a time globally): left as-is by design. This is an intentional architectural choice to prevent global state overwrites. The per-provider concurrency layer (`provider_active_jobs`) handles provider-level parallelism within that constraint.
