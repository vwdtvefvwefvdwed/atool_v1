# Job System — Issues & Fixes

Full analysis of bugs found across the normal job and workflow job pipelines, with root cause, impact, and the exact fix applied to each.

---

## Issue #1 — CRITICAL: Wrong `finally` block order causes unnecessary coordinator blocks

**File**: `job_worker_realtime.py` → `_process_job_with_concurrency_control_inner`

### Root Cause

```python
# BEFORE (broken)
finally:
    mark_provider_free(provider_key, job_id)       # ← runs FIRST
    if coordinator_started:
        coordinator.on_job_complete(job_id, "normal")  # ← runs SECOND
```

`mark_provider_free()` immediately pops the next in-memory queued job and spawns a thread. That thread calls `coordinator.on_job_start()` — but the coordinator's `job_queue_state` still shows the completing job as **active** because `on_job_complete()` hasn't run yet. The new thread hits a `"Job queue busy"` response, unnecessarily gets `blocked_by_job_id` written to DB, and must wait for the coordinator's next `process_next_queued_job()` sweep to be un-blocked.

### Impact

- Every in-memory queued job gets spuriously coordinator-blocked, adding one extra round-trip of latency per job.
- Extra DB writes (`blocked_by_job_id`, `queued_at` columns) for jobs that should just start.
- Coordinator queue log fills with false `"blocked"` events.

### Fix

```python
# AFTER (fixed)
finally:
    if coordinator_started:
        coordinator.on_job_complete(job_id, "normal")  # clear coordinator FIRST
    mark_provider_free(provider_key, job_id)             # then free provider
```

The coordinator slot is cleared before the provider is freed, so the next in-memory job sees an empty coordinator and proceeds immediately.

---

## Issue #2 — CRITICAL: `retry_pending_workflows()` picks up coordinator-blocked jobs → double execution

**File**: `workflow_retry_manager.py` → `retry_pending_workflows()`

### Root Cause

```python
# BEFORE (broken)
jobs_response = supabase.table('jobs')\
    .select('*')\
    .eq('status', 'pending_retry')\
    .eq('job_type', 'workflow')\
    .execute()   # ← no blocked_by_job_id filter
```

A `pending_retry` workflow that is coordinator-blocked (`blocked_by_job_id IS NOT NULL`) is also picked up by the 5-minute periodic retry timer. Two concurrent threads both try to resume the same workflow: one spawned by the coordinator (when the blocking job finishes) and one from the timer. Both call `coordinator.on_job_start()`. The loser gets blocked again and its `retry_count` in `workflow_executions` is incorrectly incremented.

### Impact

- Incorrect `retry_count` increments → workflows may be prematurely marked as failed (max retries exceeded).
- Wasted threads and DB writes.
- Log noise: `"blocked"` events for jobs that should just be waiting quietly.
- In worst case: two resume attempts start nearly simultaneously (one wins, one blocks), causing the workflow to process the same step twice if the coordinator check loses a race.

### Fix

```python
# AFTER (fixed)
jobs_response = supabase.table('jobs')\
    .select('*')\
    .eq('status', 'pending_retry')\
    .eq('job_type', 'workflow')\
    .is_('blocked_by_job_id', 'null')\   # ← only unblocked jobs
    .execute()
```

Coordinator-blocked `pending_retry` jobs are now exclusively handled by `coordinator.process_next_queued_job()` when the blocking job finishes.

---

## Issue #3 — CRITICAL: Race window in `process_next_queued_job()` — coordinator slot not pre-claimed

**File**: `job_coordinator.py` → `process_next_queued_job()`

### Root Cause

```python
# BEFORE (broken)
check = self.can_start_job(job_id, job_type, required_models)
if check['can_start']:
    self._trigger_job_processing(job)   # starts a thread
    return job                          # slot NOT yet claimed in DB
```

`_trigger_job_processing()` spawns a new thread that eventually calls `coordinator.on_job_start()`. Between the thread starting and the thread acquiring `_coordinator_lock`, any new job arriving via Supabase Realtime INSERT can also call `on_job_start()`, see `active_job_id = None`, and start — violating the serialization guarantee.

### Impact

- Two jobs run **simultaneously** when one finishes and a new one arrives at exactly the right moment.
- Both jobs call `set_active_job()`, and whichever runs second overwrites the first's state in `job_queue_state`. When the first job completes and calls `on_job_complete()`, it clears the second job's entry, causing the second job to run without being tracked by the coordinator.
- The result: jobs that use the same model/provider collide, API rate limits are hit unexpectedly, and the coordinator queue never drains correctly.

### Fix (two-part)

**Part A** — Add self-reservation check in `can_start_job()`:

```python
# If this job was pre-claimed by process_next_queued_job, allow it through
if active_job_id == job_id:
    return {"can_start": True, "reason": "Job already reserved as active by coordinator", ...}
```

**Part B** — Pre-claim slot before spawning thread:

```python
# AFTER (fixed): on_job_start atomically claims the slot, THEN trigger the thread
start_result = self.on_job_start(job_id, job_type, required_models)
if start_result['allowed']:
    self._trigger_job_processing(job)
    return job
```

`on_job_start()` writes `job_id` to `job_queue_state` while holding `_coordinator_lock`. Any concurrent new job calling `can_start_job()` will now see the pre-claimed slot and block correctly. When the spawned thread calls `on_job_start()` for itself, the self-reservation check returns `allowed: True` without re-claiming.

---

## Issue #4 — HIGH: `process_pending_workflows()` and `process_retryable_workflows()` include blocked jobs at startup

**File**: `workflow_retry_manager.py` → `process_pending_workflows()`, `process_retryable_workflows()`

### Root Cause

Both startup backlog functions query without a `blocked_by_job_id` filter:

```python
# BEFORE (broken) — process_pending_workflows
response = supabase.table('jobs')\
    .select('*')\
    .eq('job_type', 'workflow')\
    .eq('status', 'pending')\
    .execute()   # ← includes coordinator-blocked jobs
```

On startup, coordinator state is cleared (stale locks removed). All blocked workflow jobs are then included in the backlog sweep. All of them attempt to run, the coordinator allows one and blocks the rest again, generating a cascade of `blocked_by_job_id` writes for jobs that would have been fine if triggered one at a time.

### Impact

- N-1 unnecessary DB writes per backlog of N blocked workflows.
- Misleading log output: workflows appear to fail coordination checks immediately after startup.
- Slightly slower startup due to cascading coordinator DB calls.

### Fix

```python
# AFTER (fixed) — added to both functions
.is_('blocked_by_job_id', 'null')\
```

Since coordinator state IS cleared on startup, previously-blocked jobs effectively have no blocker — but the filter ensures they are processed one-at-a-time through the coordinator chain rather than all simultaneously.

---

## Issue #5 — HIGH: `fetch_all_pending_jobs()` has no DB fallback — backlog silently skipped

**File**: `job_worker_realtime.py` → `fetch_all_pending_jobs()`

### Root Cause

```python
# BEFORE (broken)
response = requests.get(f"{BACKEND_URL}/worker/pending-jobs", timeout=10)
if response.status_code == 200:
    return response.json().get("jobs", [])
else:
    return []   # ← silent empty return on any failure
```

If the backend HTTP server is not yet reachable at startup (common race condition in multi-service deployments where the worker and backend start simultaneously), the backlog fetch returns an empty list with no retry or fallback. All `pending` jobs from before the restart are silently ignored until either:
- The Supabase Realtime listener receives an UPDATE event for them (only happens if they get reset), or
- The 10-minute `retry_transient_errors()` sweep catches them (and only if they have an error message).

### Impact

- Jobs stuck in `pending` state indefinitely after a worker restart if the backend was temporarily unavailable.
- No log indication that the backlog was missed — operator has no visibility.

### Fix

```python
# AFTER (fixed): HTTP first, direct Supabase fallback on any failure
try:
    response = requests.get(f"{BACKEND_URL}/worker/pending-jobs", timeout=10)
    if response.status_code == 200:
        return response.json().get("jobs", [])
    # fall through to DB fallback
except Exception:
    pass  # fall through to DB fallback

# Direct Supabase fallback
from supabase_client import supabase as _sb
result = _sb.table("jobs")\
    .select("*")\
    .eq("status", "pending")\
    .neq("job_type", "workflow")\
    .order("created_at", desc=False)\
    .limit(200)\
    .execute()
return result.data or []
```

---

## Issue #6 — HIGH: No status guard in `resume_workflow()` — duplicate resumes possible

**File**: `workflow_manager.py` → `resume_workflow()`

### Root Cause

`resume_workflow()` had no check against the job's current DB status before proceeding. If the periodic retry loop and the coordinator both trigger a resume for the same job at nearly the same time (e.g., timer fires just as a blocking job completes):

1. Thread A: `retry_pending_workflows()` → `_resume_workflow()` → `resume_workflow()`
2. Thread B: `coordinator.process_next_queued_job()` → `_trigger_job_processing()` → `_resume_workflow()` → `resume_workflow()`

Both call `coordinator.on_job_start()`. One wins, one blocks. The blocked one gets `blocked_by_job_id` set. When the active one completes, the coordinator finds the still-blocked one and tries to resume it a second time — while the first resume may have already set status to `completed`.

### Impact

- Double resume attempts on the same workflow execution.
- If the first resume succeeds and sets status to `completed`, the second resume attempts to run a completed workflow — wasting resources and potentially overwriting `workflow_executions` state.
- With Issue #2 fixed (timer skips blocked jobs), the primary trigger is reduced — but the guard is still needed for any remaining concurrent paths.

### Fix

```python
# AFTER (fixed): status guard at the top of resume_workflow()
job_check = supabase.table('jobs').select('status').eq('job_id', job_id).single().execute()
if job_check.data:
    current_status = job_check.data.get('status')
    if current_status not in ('pending_retry', 'pending'):
        logger.info(f"[RESUME] Skipping resume for {job_id} — status is '{current_status}', not resumable")
        return None
```

Only jobs in `pending_retry` or `pending` state are resumed. A `completed`, `failed`, or `running` job is silently skipped.

---

## Issue #7 — MEDIUM: Retry count check fails silently → infinite retries possible

**File**: `job_worker_realtime.py` → `reset_job_to_pending()`

### Root Cause

```python
# BEFORE (broken)
try:
    # read metadata, check count, increment count
    ...
except Exception as count_err:
    print(f"[RESET] Warning: could not check retry count ...")
    # ← execution continues! Job is reset without count check or increment
```

If the Supabase read (to get current `pending_retry_count`) or write (to increment it) fails due to a transient DB error, the exception is caught and swallowed. The job is still reset to `pending`. This means the `MAX_PENDING_RETRIES` cap is never hit, and the job retries indefinitely.

### Impact

- A job with a persistent failure condition (e.g. a model that always errors on a specific input) can loop forever, consuming API credits and clogging the worker queue.
- In unlimited mode, this can cause runaway API spending.

### Fix

```python
# AFTER (fixed): track whether count update succeeded; do a fallback check if not
_retry_count_ok = False
try:
    # read + increment count
    ...
    _retry_count_ok = True
except Exception as count_err:
    print(f"[RESET] Warning: could not check/update retry count: {count_err}")

# If the primary update failed, attempt a read-only cap check as safety net
if not _retry_count_ok:
    try:
        _meta = ... # re-read metadata
        if _meta.get("pending_retry_count", 0) >= MAX_PENDING_RETRIES:
            mark_job_failed(job_id, f"Job failed after {MAX_PENDING_RETRIES} retry attempts...")
            return False
    except Exception:
        pass  # best-effort only
```

The fallback read-only check catches the case where the increment failed but the count is already at the cap.

---

## Issue #8 — MEDIUM: `/reset` endpoint does not clear stale coordinator state

**File**: `app.py` → `worker_reset_job()`

### Root Cause

When a job crashes mid-execution at runtime (not at startup), the `/reset` endpoint is called to reset it to `pending`. However, the coordinator's `job_queue_state` may still show the crashed job as the `active_job_id`. The reset endpoint did not check or clear this.

`validate_job_queue_state_on_startup()` clears stale coordinator state — but only at startup. A runtime crash followed by a `/reset` call left the coordinator in a broken state permanently, blocking all subsequent jobs from starting.

### Impact

- All jobs queued after the crashed job are permanently stuck waiting (`blocked_by_job_id` pointing to the crashed job).
- No new jobs can start until the next worker restart (which triggers `validate_job_queue_state_on_startup()`).
- This is a silent failure — the jobs show as `pending` in the UI but never process.

### Fix

```python
# AFTER (fixed): added to worker_reset_job() before the status update
try:
    from job_coordinator import get_job_coordinator
    coordinator = get_job_coordinator()
    state = coordinator.get_active_job_state()
    if state and state.get('active_job_id') == job_id:
        coordinator.clear_active_job()
        print(f"[COORDINATOR] Cleared stale active-job lock for reset job {job_id}")
except Exception as coord_err:
    print(f"⚠️ Could not clear coordinator state for reset job {job_id}: {coord_err}")
```

If the job being reset is the current active coordinator job, the lock is cleared immediately — unblocking all queued jobs.

---

## Issue #9 — MEDIUM: No reconnect logic in realtime listeners — API key inserts stop triggering jobs

**File**: `job_worker_realtime.py` → `run_async_listener()`, `realtime_listener()`, `api_key_realtime_listener()`

### Root Cause

```python
# BEFORE (broken)
async def realtime_listener():
    try:
        # ... subscribe and listen forever
        while True:
            await asyncio.sleep(1)
    except Exception as e:
        print(f"Realtime listener error: {e}")
        # ← exits! No restart logic
```

Both listeners have a `try/except` that catches errors and logs them — then returns. Once the function returns, it never runs again. A single WebSocket disconnect (network blip, Supabase maintenance window, cloud provider reboot) permanently stops:
- New job notifications (Realtime INSERT/UPDATE on `jobs` table)
- API key insertion triggers (Realtime INSERT/UPDATE on `provider_api_keys` table)

The worker silently becomes a "zombie" — it appears healthy but processes no new jobs.

### Impact

- After any Supabase WebSocket disconnect, all new jobs queue up in `pending` state and are never processed.
- The 10-minute `retry_transient_errors()` sweep eventually catches jobs with transient errors, but not new jobs that were never attempted.
- API key-blocked jobs wait indefinitely even after keys are inserted.

### Fix

```python
# AFTER (fixed): _run_with_reconnect wrapper with exponential backoff
async def _run_with_reconnect(listener_fn, name: str):
    delay = 5
    max_delay = 60
    while True:
        try:
            await listener_fn()
            print(f"[RECONNECT] {name} exited cleanly, restarting in {delay}s...")
        except Exception as e:
            print(f"[RECONNECT] {name} crashed: {e} — restarting in {delay}s...")
            notify_error(ErrorType.REALTIME_LISTENER_CRASHED, f"{name} crashed and will reconnect", ...)
        await asyncio.sleep(delay)
        delay = min(delay * 2, max_delay)

# In run_async_listener():
await asyncio.gather(
    _run_with_reconnect(realtime_listener, "RealtimeJobListener"),
    _run_with_reconnect(api_key_realtime_listener, "ApiKeyRealtimeListener")
)
```

Both listeners now restart automatically after any crash with a backoff from 5s up to 60s.

---

## Issue #10 — MINOR: `credits_remaining` wrong in UNLIMITED_MODE

**File**: `jobs.py` → `create_job()`

### Root Cause

```python
# BEFORE (broken)
return {
    ...
    "credits_remaining": credits - 1   # ← always subtracts 1
}
```

In `UNLIMITED_MODE=true`, no credit is deducted (`credits` is not decremented in the DB). But the API response still returned `credits - 1`, causing the frontend to display the user's credit count as 1 lower than reality. On each generation, the displayed count would drop (even though the actual balance was unchanged), confusing users into thinking they were spending credits.

### Impact

- Frontend shows incorrect (lower) credit count after every generation in unlimited mode.
- Users may believe they are running out of credits when they are not.

### Fix

```python
# AFTER (fixed)
"credits_remaining": credits if UNLIMITED_MODE else credits - 1
```

---

## Issue #11 — MEDIUM: Duplicate `started` log + redundant DB writes in `on_job_start()` self-reservation path

**File**: `job_coordinator.py` → `on_job_start()`

### Root Cause

`process_next_queued_job()` calls `on_job_start()` to pre-claim the coordinator slot. That call executes:
1. `set_active_job()` — writes to `job_queue_state`
2. `clear_job_queue_info()` — clears `blocked_by_job_id` on the job row
3. `log_queue_event('started')` — inserts a row in `job_queue_log`

Then the spawned thread calls `on_job_start()` a second time. The self-reservation check in `can_start_job()` returns `can_start: True`, and `on_job_start()` goes ahead and calls all three functions **again** — writing identical data to `job_queue_state`, a no-op write to the job row, and a **duplicate `started` entry in `job_queue_log`**.

### Impact

- Every queued job that gets promoted by the coordinator produces two `started` entries in `job_queue_log`, making audit logs unreliable.
- Two redundant DB round-trips per job (`set_active_job` + `clear_job_queue_info`).

### Fix

Added an early-return in `on_job_start()` when `can_start_job()` returns the self-reservation reason:

```python
# AFTER (fixed): skip redundant writes for self-reservation
if check_result.get('reason') == "Job already reserved as active by coordinator":
    return {"allowed": True, "reason": check_result['reason'], "action": "start"}
```

---

## Issue #12 — HIGH: `on_job_start()` silently drops job when `set_active_job()` DB write fails

**File**: `job_coordinator.py` → `on_job_start()`

### Root Cause

```python
# BEFORE (broken)
if self.set_active_job(job_id, job_type, required_models):
    ...
    return {"allowed": True, ...}
else:
    return {"allowed": False, "reason": "Failed to update global state", "action": "error"}
```

When `set_active_job()` fails (transient Supabase write error), `on_job_start()` returns `allowed: False`. The calling code in `_process_job_with_concurrency_control_inner()` treats any `allowed: False` as a block and returns `None`. The job is never processed and stays `pending` permanently — no retry, no re-queue, no error logged to the job record.

### Impact

- Any transient Supabase write failure during coordinator state update permanently strands the job.
- The job stays `pending` until the next 10-minute `retry_transient_errors()` sweep — but only if it has a transient error message in `error_message`, which it doesn't (it was never attempted).
- In practice: job silently stuck forever.

### Fix

```python
# AFTER (fixed): allow the job through rather than dropping it
else:
    logger.error(f"[COORDINATOR] set_active_job failed for {job_id} — allowing job through without state tracking")
    return {
        "allowed": True,
        "reason": "DB state update failed — allowing without coordinator tracking",
        "action": "start_untracked"
    }
```

The job proceeds without coordinator tracking on a DB failure. Coordinator serialization is weakened for that single job, but the alternative (silently dropping the job) is worse.

---

## Issue #13 — MEDIUM: `retry_transient_errors()` uses wrong Supabase client + re-triggers coordinator-blocked jobs

**File**: `job_worker_realtime.py` → `retry_transient_errors()`

### Root Cause — Wrong client

```python
# BEFORE (broken)
supabase = get_worker1_client()   # ← Worker1 DB client, not the main jobs DB
```

`get_worker1_client()` returns the Worker1 database client (separate service). The `jobs` table is in the main Supabase database. If `WORKER_1_URL`/`WORKER_1_SERVICE_ROLE_KEY` are absent or point to a different DB:
- `get_worker1_client()` returns `None` → function returns early silently
- Or the query hits the wrong database → always returns empty

The entire 10-minute periodic sweep for transient-error jobs would silently stop working.

### Root Cause — Missing blocked-job filter

```python
# BEFORE (also broken)
.eq("status", "pending")
# ← no blocked_by_job_id filter
```

A coordinator-blocked normal job that has an error message (e.g., failed before getting blocked, then got blocked on re-attempt) would be re-triggered by the 10-minute sweep. The coordinator would block it again, generating unnecessary DB writes.

### Impact

- Periodic sweep for transient-error retry may silently do nothing if Worker1 client is unavailable.
- Coordinator-blocked jobs re-triggered unnecessarily.

### Fix

```python
# AFTER (fixed): main supabase client + blocked-job filter
from supabase_client import supabase as _retry_sb

result = _retry_sb.table("jobs") \
    .select("job_id, model, error_message, created_at") \
    .eq("status", "pending") \
    .is_("blocked_by_job_id", "null") \      # ← skip coordinator-blocked jobs
    .not_.is_("error_message", "null") \
    ...
```

---

## Issue #14 — MEDIUM: Key-insertion handler re-triggers coordinator-blocked jobs

**File**: `job_worker_realtime.py` → `fetch_pending_jobs_for_provider()`, `fetch_pending_retry_workflow_jobs_for_provider()`

### Root Cause

Both functions query without a `blocked_by_job_id` filter:

```python
# BEFORE (broken) — fetch_pending_jobs_for_provider
response = supabase.table("jobs")\
    .eq("status", "pending")\
    .neq("job_type", "workflow")\
    .execute()   # ← includes coordinator-blocked jobs

# BEFORE (broken) — fetch_pending_retry_workflow_jobs_for_provider
jobs_response = supabase.table("jobs")\
    .eq("status", "pending_retry")\
    .eq("job_type", "workflow")\
    .execute()   # ← includes coordinator-blocked workflows
```

When an API key is inserted, ALL pending jobs matching the provider are re-triggered — including jobs that are coordinator-blocked (waiting for another job to finish). These jobs:
1. Enter `process_job_with_concurrency_control()` or `_resume_workflow()`
2. Call `on_job_start()` → blocked again by the coordinator
3. `mark_job_queued()` overwrites `blocked_by_job_id` and `queued_at` with fresh values

The overwrite of `queued_at` is particularly bad: it loses the original queue time, making the job appear newer in the coordinator queue — potentially reordering it behind later-arriving jobs.

### Impact

- Every API key insertion triggers redundant coordinator block cycles for all blocked jobs matching the provider.
- `queued_at` is reset to a newer timestamp, disrupting FIFO ordering in `process_next_queued_job()`.
- Extra DB writes and log noise.

### Fix

```python
# AFTER (fixed): both fetch functions now exclude blocked jobs
.is_("blocked_by_job_id", "null")\
```

---

## Fix Summary Table

| # | Severity | File(s) | Description |
|---|---|---|---|
| 1 | 🔴 Critical | `job_worker_realtime.py` | Reverse `finally` block: coordinator cleared **before** provider freed |
| 2 | 🔴 Critical | `workflow_retry_manager.py` | Add `blocked_by_job_id IS NULL` filter to `retry_pending_workflows()` |
| 3 | 🔴 Critical | `job_coordinator.py` | Pre-claim coordinator slot in `process_next_queued_job()` + self-reservation check in `can_start_job()` |
| 4 | 🟠 High | `workflow_retry_manager.py` | Add `blocked_by_job_id IS NULL` filter to `process_pending_workflows()` and `process_retryable_workflows()` at startup |
| 5 | 🟠 High | `job_worker_realtime.py` | Add direct Supabase DB fallback to `fetch_all_pending_jobs()` |
| 6 | 🟠 High | `workflow_manager.py` | Add job status guard at top of `resume_workflow()` — skip if not resumable |
| 7 | 🟡 Medium | `job_worker_realtime.py` | Retry count failure-safe: fallback read-only cap check if primary increment fails |
| 8 | 🟡 Medium | `app.py` | `/reset` endpoint clears stale coordinator lock if the resetting job was the active job |
| 9 | 🟡 Medium | `job_worker_realtime.py` | Wrap both realtime listeners in `_run_with_reconnect()` with exponential backoff |
| 10 | 🔵 Minor | `jobs.py` | `credits_remaining` returns `credits` (not `credits - 1`) when `UNLIMITED_MODE=true` |
| 11 | 🟡 Medium | `job_coordinator.py` | Skip redundant DB writes + duplicate `started` log when `on_job_start()` hits self-reservation path |
| 12 | 🟠 High | `job_coordinator.py` | `on_job_start()` DB-write failure now allows the job through instead of silently dropping it |
| 13 | 🟠 High | `job_worker_realtime.py` | `retry_transient_errors()`: use main Supabase client + add `blocked_by_job_id IS NULL` filter |
| 14 | 🟡 Medium | `job_worker_realtime.py` | Key-insertion handler excludes coordinator-blocked jobs from re-trigger to prevent `queued_at` overwrite |
| 15 | 🔴 Critical | `workflows/base_workflow.py` | `_get_or_create_execution()` crash loop: existing execution with no progress + re-trigger → unique constraint violation → permanent stuck |
| 16 | 🟠 High | `workflows/base_workflow.py` | DB error in `_update_execution()` / `_save_checkpoint()` left job stuck in `running` forever; now resets to `pending_retry` |
| 17 | 🟠 High | `job_worker_realtime.py` | Realtime UPDATE event re-triggers coordinator-unblocked workflow jobs → double execution (both threads pass self-reservation) |
| 18 | 🟡 Medium | `job_worker_realtime.py` | `fetch_pending_workflow_jobs_for_provider()` missing `blocked_by_job_id IS NULL` → coordinator-blocked pending workflows re-triggered on key insert → `queued_at` overwrite |
| 19 | 🟠 High | `job_worker_realtime.py` | `reset_job_to_pending()` uses `get_worker1_client()` to query `jobs` table (main DB) → `pending_retry_count` never read/written → `MAX_PENDING_RETRIES` never enforced → infinite retry loops |
| 20 | 🟡 Medium | `app.py`, `job_worker_realtime.py` | Startup backlog (`/worker/pending-jobs` + `fetch_all_pending_jobs` fallback) missing `blocked_by_job_id IS NULL` filter → coordinator-blocked jobs re-submitted on every restart → `queued_at` overwritten → FIFO corruption |

---

## Issue #15 — CRITICAL: `_get_or_create_execution()` crash loop for key-error workflows

**File**: `workflows/base_workflow.py` → `_get_or_create_execution()`

### Root Cause

```python
# BEFORE (broken)
if existing.data:
    existing_exec = existing.data
    has_progress = any(v.get('status') == 'completed' ...)
    if resume or has_progress:      # ← only returns existing if resume=True OR has completed steps
        return existing_exec
    # Falls through to INSERT if has_progress=False and resume=False
```

**Scenario**: A workflow job arrives via Realtime INSERT. `execute_workflow()` is called (`resume=False`). It calls `coordinator.on_job_start()` (allowed), then `workflow_instance.execute()` → `_get_or_create_execution()`. No existing execution → INSERT succeeds. The first step runs immediately and fails with a key error (`RetryableError(error_type='no_api_key')`). Status is set to `pending_retry` in DB.

Now the job has an `workflow_executions` record with:
- `retry_count = 1`  
- `current_step = 0`  
- No completed checkpoints (`has_progress = False`)

An operator inserts the missing API key. `handle_api_key_insertion()` → `fetch_pending_retry_workflow_jobs_for_provider()` → finds the job → `_resume_workflow()` → `workflow_manager.resume_workflow()` (`resume=True`) → this path is actually OK (`resume=True`).

But `fetch_pending_workflow_jobs_for_provider()` ALSO finds the job if it's in `pending` status with a key error. `_start_fresh_wf()` → `execute_workflow()` (`resume=False`) → `_get_or_create_execution()` with `resume=False` → existing execution found but `has_progress=False` → condition `False or False = False` → **falls through to INSERT** → **unique constraint violation** → exception propagates → thread crashes → `coordinator.on_job_complete()` called but job stays in whatever status it was in.

On the NEXT key insertion event, the exact same crash happens again. The job is permanently stuck in a crash loop.

### Impact

- Workflow job that fails on step 0 with a key error becomes permanently stuck after the key is inserted.
- Every API key insertion for that provider triggers a crash thread that pollutes logs and wastes resources.
- The job can never complete via the API key insertion path.

### Fix

```python
# AFTER (fixed): always return existing execution — never attempt to INSERT a second one
if existing.data:
    existing_exec = existing.data
    # Backfill input if missing
    ...
    return existing_exec  # Always reuse existing execution
```

Removing the `resume or has_progress` gate ensures that if an execution record already exists, it is always reused. The insertion path only runs when there is truly no existing record.

---

## Issue #16 — HIGH: DB errors in `_update_execution()` / `_save_checkpoint()` leave jobs stuck in `running`

**File**: `workflows/base_workflow.py` → `execute()`

### Root Cause

`_update_execution()` and `_save_checkpoint()` (the DB write helpers used throughout the step loop) have no error handling:

```python
async def _update_execution(self, execution_id: str, updates: Dict):
    supabase.table('workflow_executions').update(updates).eq('id', execution_id).execute()
    # ← no try/except
```

If a transient Supabase write failure hits here, the exception bubbles up through the step loop, exits the `for` block, and is caught by the outer bare `except Exception as e: raise` at the bottom of `execute()`. That re-raise propagates to `workflow_manager.execute_workflow()` or `resume_workflow()`, which calls `coordinator.on_job_complete()` (correct) but then re-raises.

The calling thread (e.g., `_start_new_workflow`) swallows the exception:
```python
except Exception as _wf_err:
    print(f"[WORKFLOW] New workflow {job_id} failed: {_wf_err}")
```

The job's status in DB was set to `running` at the start of `execute()` and was **never updated** to any terminal/retryable state — because `_update_job_status()` is only called inside `RetryableError` / `HardError` handlers, not for unexpected infrastructure exceptions.

Result: job stuck in `running` until the next worker restart triggers `reset_running_jobs_to_pending()`.

### Impact

- Any transient Supabase write failure during checkpoint/execution-state updates leaves the job permanently stuck in `running` for the lifetime of the worker process.
- Other coordinator-queued jobs may be blocked waiting for this "running" job to finish (since the coordinator clears its slot, but the DB still shows `running`).

### Fix

```python
# AFTER (fixed): outer except now resets non-workflow-error exceptions to pending_retry
except Exception as e:
    logger.error(f"Workflow execution failed: {e}")
    if not isinstance(e, (RetryableError, HardError)):
        try:
            await self._update_job_status(job_id, 'pending_retry', {
                'error': f"Infrastructure error: {str(e)[:200]}",
                'can_resume': True,
                'retryable': True
            })
        except Exception as _status_err:
            logger.error(f"[BASE_WORKFLOW] Could not reset job {job_id} to pending_retry: {_status_err}")
    raise
```

`RetryableError` and `HardError` already call `_update_job_status()` before re-raising, so they are excluded. All other (infrastructure) exceptions now push the job to `pending_retry`, making it visible to the periodic retry sweep.

---

## Issue #17 — HIGH: Realtime UPDATE event causes duplicate workflow execution on coordinator unblock

**File**: `job_worker_realtime.py` → `handle_new_job()` (inside `realtime_listener`)

### Root Cause

The realtime listener subscribes to **both INSERT and UPDATE** events on the `jobs` table, using the same `handle_new_job` callback:

```python
# INSERT subscription — intended for new job arrivals
await channel.on_postgres_changes(event="INSERT", ..., callback=handle_new_job).subscribe()

# UPDATE subscription — intended to catch reset-to-pending jobs
await update_channel.on_postgres_changes(event="UPDATE", ..., callback=handle_new_job).subscribe()
```

When the coordinator unblocks a queued workflow job it:
1. Pre-claims the coordinator slot (`set_active_job`)
2. Calls `clear_job_queue_info(job_id)` → DB UPDATE: `blocked_by_job_id=NULL, queued_at=NULL`
3. Spawns **Thread A** via `_trigger_job_processing()` → `_resume_workflow()` or `execute_workflow()`

The DB UPDATE in step 2 fires the Realtime UPDATE subscription → `handle_new_job` sees `status=pending`, `job_type=workflow` → spawns **Thread B** → `_start_new_workflow()` → `execute_workflow()`.

Both Thread A and Thread B call `coordinator.on_job_start()`. The coordinator's `can_start_job()` has a **self-reservation check**: if `active_job_id == job_id`, it returns `allowed=True` regardless of which thread is asking. So **both threads are allowed through concurrently**.

- Thread A calls `base_workflow.execute(resume=True)` — resumes from the saved checkpoint step
- Thread B calls `base_workflow.execute(resume=False)` — starts from step 0

Both share the same `workflow_execution` record. Thread B overwrites checkpoint data written by Thread A. Both threads invoke the AI generation APIs for the same steps simultaneously.

### Impact

- Every coordinator-unblocked workflow job is executed **twice simultaneously**.
- Thread A (resume) and Thread B (fresh from step 0) race on the same step loop — step outputs, checkpoint updates, and `current_step` increments overwrite each other.
- Double AI API calls are made for the same prompt → wasted credits, duplicated results.
- The first thread to call `coordinator.on_job_complete()` frees the slot; the second has no active job to release (coordinator slot already cleared), so `on_job_complete()` clears an already-empty slot — no crash but coordinator log pollution.

### Fix

```python
# BEFORE (broken): handle_new_job spawns workflow thread on ALL events
if job_type == "workflow":
    def _start_new_workflow(): ...
    threading.Thread(target=_start_new_workflow, daemon=True).start()

# AFTER (fixed): skip UPDATE events for workflow jobs
if job_type == "workflow":
    _event_type = (
        payload.get("eventType")
        or payload.get("data", {}).get("eventType", "INSERT")
    )
    if _event_type == "UPDATE":
        print(f"[WORKFLOW] UPDATE event for {job_id} skipped — "
              f"coordinator or retry-manager will dispatch it")
        return
    # Only INSERT events (genuine new submissions) spawn here
    def _start_new_workflow(): ...
    threading.Thread(target=_start_new_workflow, daemon=True).start()
```

UPDATE events for workflow jobs are now dropped. The coordinator's `_trigger_job_processing()` handles coordinator-unblocked jobs directly. Jobs reset to `pending` after a crash are picked up within 2 minutes by `workflow_retry_manager.retry_stale_pending_workflows()`.

---

## Issue #18 — MEDIUM: `fetch_pending_workflow_jobs_for_provider()` missing `blocked_by_job_id IS NULL` filter

**File**: `job_worker_realtime.py` → `fetch_pending_workflow_jobs_for_provider()`

### Root Cause

```python
# BEFORE (broken)
response = _sb.table("jobs")\
    .select("*")\
    .eq("status", "pending")\
    .eq("job_type", "workflow")\
    .execute()   # ← no blocked_by_job_id filter
```

This function is called by `handle_api_key_insertion()` to find `status=pending` workflow jobs that have a key-error message. If such a job is also coordinator-blocked (`blocked_by_job_id IS NOT NULL`), it will be included in the results. The function re-triggers it via `execute_workflow()` → `coordinator.on_job_start()`. Because a different job is still active (the blocker), the coordinator returns `allowed=False` and calls `mark_job_queued()`, which:

```python
self.supabase.table('jobs').update({
    'blocked_by_job_id': blocked_by,       # same value (no harm)
    'conflict_reason': conflict_reason,    # overwritten
    'queued_at': datetime.utcnow().isoformat()  # ← RESET TO NOW
}).eq('job_id', job_id).execute()
```

The `queued_at` timestamp is reset to the current time on every API key insertion event, even though the job's place in the FIFO queue should have been locked in when it was originally blocked. On the next `process_next_queued_job()` sweep, this job appears to have just joined the queue and is sorted behind newer jobs — **breaking FIFO ordering**.

### Impact

- Coordinator-blocked pending workflow jobs with key errors get their `queued_at` reset on every API key insertion.
- Long-waiting blocked jobs lose their queue position, causing starvation in busy systems.
- Mirrors Issue #14 (same bug on the `fetch_pending_retry_workflow_jobs_for_provider` and `fetch_pending_jobs_for_provider` functions, already fixed).

### Fix

```python
# AFTER (fixed)
response = _sb.table("jobs")\
    .select("*")\
    .eq("status", "pending")\
    .eq("job_type", "workflow")\
    .is_("blocked_by_job_id", "null")\   # ← only unblocked jobs
    .execute()
```

Coordinator-blocked pending workflow jobs are now excluded. The coordinator's `process_next_queued_job()` remains the sole authority for dispatching blocked jobs when their blocker finishes.

---

## Issue #19 — HIGH: `reset_job_to_pending()` uses Worker1 client to query `jobs` table — `MAX_PENDING_RETRIES` never enforced

**File**: `job_worker_realtime.py` → `reset_job_to_pending()`

### Root Cause

```python
# BEFORE (broken)
supabase = get_worker1_client()          # ← Worker1 DB client
if supabase:
    job_resp = supabase.table("jobs")... # ← 'jobs' is in the MAIN DB, not Worker1
```

`get_worker1_client()` returns a Supabase client pointed at the Worker1 database, which holds provider/API key data. The `jobs` table lives in the **main** Supabase database. The same mistake exists in the fallback check:

```python
if not _retry_count_ok:
    _sb = get_worker1_client()           # ← same wrong client
    if _sb:
        _r = _sb.table("jobs")...
```

Results:
1. If Worker1 credentials are absent: `get_worker1_client()` returns `None`. Both `if supabase:` and `if _sb:` guards skip entirely. `_retry_count_ok` stays `False` and the fallback also skips.
2. If Worker1 credentials are present but the Worker1 DB has no `jobs` table: the query raises a Postgres exception, caught by the outer `except`, `_retry_count_ok` stays `False`, fallback also fails.

In both cases `pending_retry_count` is **never read, never incremented, and never checked**. The `MAX_PENDING_RETRIES = 5` cap is completely bypassed.

### Impact

- Jobs experiencing persistent non-key errors (network failures, Cloudinary outages, provider API errors with no keys) can retry **indefinitely** via the 30-second deferred retry mechanism.
- Under a prolonged outage, a single job spawns a new retry thread every 30 seconds for the entire duration — dozens of threads for a single stuck job.
- Each retry thread holds a coordinator slot for the duration of the attempt, blocking all other jobs behind it.

### Fix

```python
# AFTER (fixed): use the main Supabase client for the jobs table
from supabase_client import supabase as _main_sb
job_resp = _main_sb.table("jobs").select("metadata").eq("job_id", job_id).execute()
...
_main_sb.table("jobs").update({"metadata": meta}).eq("job_id", job_id).execute()

# fallback also uses main client
from supabase_client import supabase as _main_sb2
_r = _main_sb2.table("jobs").select("metadata").eq("job_id", job_id).execute()
```

Both the primary path and the fallback now query the correct database. `pending_retry_count` is read, incremented, and persisted correctly. After 5 failures the job is permanently marked as failed.

---

## Issue #20 — MEDIUM: Startup backlog fetch includes coordinator-blocked jobs → FIFO `queued_at` corruption on restart

**Files**: `app.py` → `worker_get_pending_jobs()` and `job_worker_realtime.py` → `fetch_all_pending_jobs()` (direct DB fallback)

### Root Cause

```python
# BEFORE (broken) — app.py /worker/pending-jobs
response = (
    supabase.table("jobs")
    .select("*")
    .eq("status", "pending")   # ← includes blocked jobs
    .order(...)
    .execute()
)

# BEFORE (broken) — fetch_all_pending_jobs() fallback
result = _sb.table("jobs")\
    .select("*")\
    .eq("status", "pending")\
    .neq("job_type", "workflow")\
    .execute()   # ← includes blocked jobs
```

On worker startup, `process_all_pending_jobs()` calls `fetch_all_pending_jobs()` which calls the HTTP `/worker/pending-jobs` endpoint. Neither query has a `blocked_by_job_id IS NULL` filter. Coordinator-blocked jobs (`blocked_by_job_id IS NOT NULL`) are included.

All fetched jobs are submitted to `process_job_with_concurrency_control()`. The coordinator slot is empty (cleared by `validate_job_queue_state_on_startup()`). The first job becomes active; every subsequent job — including the previously-blocked ones — calls `coordinator.on_job_start()`, is blocked by the now-active job, and goes through `mark_job_queued()` which sets `queued_at = datetime.utcnow()`. This overwrites the original `queued_at` that encoded the job's true position in the FIFO queue, replacing it with the current restart timestamp. All previously-blocked jobs appear to have just arrived — their relative ordering is lost.

### Impact

- On every worker restart, all coordinator-blocked pending jobs lose their original `queued_at` value.
- Jobs that had been waiting for a long time (queued before the restart) are placed at equal priority with jobs that were just blocked moments before the restart.
- Under high load (many jobs), FIFO order is randomized on every restart.
- Mirrors Issues #14, #18 (same root cause, different entry points).

### Fix

```python
# AFTER (fixed) — app.py /worker/pending-jobs
response = (
    supabase.table("jobs")
    .select("*")
    .eq("status", "pending")
    .is_("blocked_by_job_id", "null")   # ← only unblocked jobs
    .order(...)
    .execute()
)

# AFTER (fixed) — fetch_all_pending_jobs() fallback
result = _sb.table("jobs")\
    .select("*")\
    .eq("status", "pending")\
    .neq("job_type", "workflow")\
    .is_("blocked_by_job_id", "null")\   # ← only unblocked jobs
    .execute()
```

Coordinator-blocked jobs are excluded from both the HTTP API path and the direct DB fallback. They will be promoted by `coordinator.process_next_queued_job()` when their blocker completes, with their original `queued_at` intact.

---

## Issue #21 — CRITICAL: UPDATE channel subscription causes race condition → jobs stuck in `running`

**File**: `job_worker_realtime.py` → `realtime_listener()`

### Root Cause

A second Supabase realtime channel (`job-worker-pending-updates`) was subscribed to **all job UPDATE events** and called the same `handle_new_job()` callback that processes new INSERT jobs:

```python
# BEFORE (broken) — inside realtime_listener()
update_channel = async_client.channel("job-worker-pending-updates")
await update_channel.on_postgres_changes(
    event="UPDATE",
    schema="public",
    table="jobs",
    callback=handle_new_job   # ← same callback as INSERT channel
).subscribe()
```

When `reset_job_to_pending()` updated a job's status to `'pending'`, Supabase fired this UPDATE event. `handle_new_job()` has an inner guard (`if _img_event_type == "UPDATE": return`) but it was silently failing: `payload.get("eventType")` returned `None` (not present at the top level of the Supabase SDK payload), and the fallback `payload.get("data", {}).get("eventType", "INSERT")` defaulted to `"INSERT"` — bypassing the guard entirely.

### Race Condition Sequence

```
reset_job_to_pending()
  ├── DB UPDATE (status → 'pending', retry_after = now+30s)
  ├── _deferred_retry thread spawned (non-key errors only) — sleeps 30s
  └── Supabase UPDATE event fires → UPDATE channel → handle_new_job() → Thread C spawned

Thread A (_deferred_retry or handle_api_key_insertion):
  └── provider lock acquired → coordinator → BUSY
      → process_job() → /progress → status = 'running'
      → generate() ... [working]

Thread C (from UPDATE channel):
  └── provider BUSY → queued

Thread C (dequeued after Thread A releases lock):
  └── process_job() → DB status = 'running' → SKIP
      (Thread A is still in generate() or has already set running)

Result: Job stuck at status='running' if Thread A errors out after
        Thread C already consumed its queue slot and skipped.
```

### Impact

- Jobs from Picsart, Clipdrop, and any provider that calls `reset_job_to_pending()` could get stuck at `status='running'` indefinitely.
- Three concurrent threads processing the same job: Thread A (intended handler), Thread B (queue from RETRY-DELAY), Thread C (spurious UPDATE channel thread).
- Observed as: provider queue filling up with duplicate entries of the same job ID, all skipping with `[SKIP] status is 'running'`.

### Fix

**1. Removed the UPDATE channel entirely** — all retry paths are already covered by dedicated handlers:

| Error type | Handler |
|---|---|
| Non-key transient (network, timeout, Cloudinary) | `_deferred_retry` thread in `reset_job_to_pending()` — 30s delay |
| Key-related error | `handle_api_key_insertion()` — fires when a new key is inserted |
| Quota / stuck pending | `retry_transient_errors()` — 10-minute periodic sweep |
| Worker restart | `reset_running_jobs_to_pending()` + `process_all_pending_jobs()` on startup |

```python
# AFTER (fixed) — UPDATE channel subscription removed
# NOTE: We intentionally do NOT subscribe to UPDATE events here.
# Jobs reset to 'pending' after a transient error are handled by two dedicated paths:
#   1. _deferred_retry thread (spawned by reset_job_to_pending for non-key errors) — 30s delay
#   2. handle_api_key_insertion (for key-related errors, fires when a new key is inserted)
# Subscribing to UPDATE events caused a race condition where a third thread was spawned
# for the same job, racing with the above two dedicated handlers and causing the job to
# get stuck in 'running' state (all subsequent threads see status='running' and skip).
```

**2. Added status re-fetch guard in `handle_api_key_insertion()`** before spawning threads for pending jobs, preventing the case where the job was picked up by another thread between the initial DB fetch and the spawn:

```python
# AFTER — inside handle_api_key_insertion() image/video loop
_status_check = _sb_check.table("jobs").select("status").eq("job_id", job_id).single().execute()
if _status_check.data:
    _current = _status_check.data.get("status")
    if _current not in ("pending",):
        print(f"Job {job_id} status is '{_current}' — already being processed, skipping")
        continue
```

### Safety — All Job Types Verified

| Job type | Affected by removed UPDATE channel? | Still covered? |
|---|---|---|
| **Image** | Yes — UPDATE channel was the race source | `_deferred_retry` + `handle_api_key_insertion` + 10-min sweep ✓ |
| **Video** | Yes — same code path as image | Same as image ✓ |
| **Workflow** | No — inner guard at line 2027 already skipped UPDATE events for workflows | `workflow_retry_manager` 2-min sweep + `handle_api_key_insertion` ✓ |
