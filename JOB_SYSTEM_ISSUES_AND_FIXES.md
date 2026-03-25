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

---

---

# Proposed Enhancement — Model-Conflict-Based Parallel Scheduling

## Background & Motivation

The coordinator currently uses **global serialization**: exactly one job runs at a time. Any new job that arrives while another is running is blocked — regardless of whether the two jobs share any models or providers.

This was a deliberate safety choice (prevents global state overwrite), but it is too conservative. In practice the workflow job set and the normal image/video job set use **completely different providers**:

| Job type | Models / Providers used |
|---|---|
| All 9 workflows | `gemini-25-flash-aicc` (`vision-aicc`) + `clipdrop-upscale` (`vision-clipdrop`) |
| Normal image/video | `sdxl-lightning-xeven`, `flux`, `nova`, `atlas`, `wan22`, `kling`, `hailuo`, etc. (all different providers) |

There is **zero model overlap** between a running workflow and a typical normal image/video job. With the current design, a user submitting an image generation request while another user's workflow is running must wait 60–90 seconds for the workflow to finish — even though the two jobs would never interfere.

The idea: replace global serialization with **model-conflict-based scheduling**. Two jobs can run simultaneously if and only if their required model/provider sets do not overlap.

---

## The Core Logic Change

### Current (`can_start_job`)
```
If ANY job is active → block
```

### Proposed
```
If the NEW job's models overlap with ANY active job's models → block (name the conflicting job)
If no overlap → allow (run in parallel)
```

This is already half-built. `has_model_conflict()` exists in the coordinator but is unused — the current code skips it and falls through to the blanket block. The `active_models` list is already stored in `job_queue_state`.

---

## The Problem: Single-Slot DB State

`job_queue_state` (Worker1 DB) stores ONE active job:
```
active_job_id    → uuid
active_job_type  → text
active_models    → jsonb   (array of model names)
```

With parallel execution allowed, multiple jobs can be active simultaneously. The DB state must store ALL active jobs, not just one.

---

## Plan

### Step 1 — DB migration: add `active_jobs` JSONB column

Add a new column to `job_queue_state` in the Worker1 DB:

```sql
ALTER TABLE job_queue_state
ADD COLUMN active_jobs JSONB NOT NULL DEFAULT '[]'::jsonb;
```

Schema of each entry in the array:
```json
{
  "job_id": "uuid",
  "job_type": "workflow | image | video",
  "models": ["model-name-1", "model-name-2"]
}
```

Keep the existing `active_job_id` / `active_job_type` / `active_models` columns during the transition (for backward compatibility with any existing monitoring/ops tooling). After the new system is stable those columns can be dropped.

---

### Step 2 — Change `get_active_job_state()` to read `active_jobs` array

```python
# CURRENT
state = {
    "active_job_id": ...,
    "active_job_type": ...,
    "active_models": [...]
}

# PROPOSED
state = {
    "active_jobs": [
        {"job_id": "...", "job_type": "workflow", "models": ["gemini-25-flash-aicc", "clipdrop-upscale"]},
        ...
    ]
}
```

The in-memory cache (`_active_job_cache`) becomes:
```python
_active_job_cache = {
    "active_jobs": []   # list of {job_id, job_type, models}
}
```

---

### Step 3 — Change `can_start_job()` to check model conflict

```python
def can_start_job(self, job_id, job_type, required_models):
    state = self.get_active_job_state()
    active_jobs = state.get('active_jobs', []) if state else []

    # Self-reservation: job already pre-claimed
    if any(j['job_id'] == job_id for j in active_jobs):
        return {"can_start": True, "reason": "Job already reserved", ...}

    # No active jobs — start immediately
    if not active_jobs:
        return {"can_start": True, "reason": "No active jobs", ...}

    # Collect all models currently in use
    active_models_union = set()
    for j in active_jobs:
        active_models_union.update(j.get('models', []))

    # Check conflict
    new_set = set(required_models)
    conflict = new_set & active_models_union

    if conflict:
        # Find which specific job owns the conflicting models (for blocked_by_job_id)
        for j in active_jobs:
            if new_set & set(j.get('models', [])):
                blocking_job_id = j['job_id']
                blocking_job_type = j['job_type']
                break
        return {
            "can_start": False,
            "reason": f"Model conflict with {blocking_job_type} job {blocking_job_id}: {conflict}",
            "blocked_by": blocking_job_id,
            "conflict_models": list(conflict)
        }

    # No conflict — allow parallel execution
    return {"can_start": True, "reason": "No model conflict with active jobs", ...}
```

---

### Step 4 — Change `set_active_job()` to APPEND (not replace)

```python
# CURRENT: replaces the single active-job slot
UPDATE job_queue_state SET active_job_id=?, active_job_type=?, active_models=? WHERE id=1

# PROPOSED: appends to the active_jobs array
UPDATE job_queue_state
SET active_jobs = active_jobs || '[{"job_id":?, "job_type":?, "models":?}]'::jsonb
WHERE id=1
```

In Python (using Supabase JS-style client):
```python
# Read current, append, write back — inside _coordinator_lock
current = self.get_active_job_state()
active_jobs = current.get('active_jobs', []) if current else []
active_jobs.append({"job_id": job_id, "job_type": job_type, "models": models})
self.supabase.table('job_queue_state').update({
    'active_jobs': active_jobs,
    'last_updated': datetime.utcnow().isoformat()
}).eq('id', 1).execute()
```

This read-modify-write is safe because it executes inside `_coordinator_lock`.

**Cross-process safety note**: `app.py` and `job_worker_realtime.py` are separate processes. `_coordinator_lock` only protects intra-process concurrency. For cross-process atomic writes, the safest approach is to use a Supabase RPC (stored procedure) that does the array append atomically:

```sql
CREATE OR REPLACE FUNCTION append_active_job(p_job_id text, p_job_type text, p_models jsonb)
RETURNS void AS $$
BEGIN
  UPDATE job_queue_state
  SET active_jobs = active_jobs || jsonb_build_array(
      jsonb_build_object('job_id', p_job_id, 'job_type', p_job_type, 'models', p_models)
  )
  WHERE id = 1;
END;
$$ LANGUAGE plpgsql;
```

Called as: `self.supabase.rpc('append_active_job', {...}).execute()`

---

### Step 5 — Change `clear_active_job()` to REMOVE by `job_id`

```python
# CURRENT: wipes the single active-job slot entirely
# PROPOSED: removes only the completed job from the array

# Read current array
current = self.get_active_job_state()
active_jobs = [j for j in current.get('active_jobs', []) if j['job_id'] != job_id]

self.supabase.table('job_queue_state').update({
    'active_jobs': active_jobs,
    'last_updated': datetime.utcnow().isoformat()
}).eq('id', 1).execute()
```

Corresponding Supabase RPC for cross-process atomicity:
```sql
CREATE OR REPLACE FUNCTION remove_active_job(p_job_id text)
RETURNS void AS $$
BEGIN
  UPDATE job_queue_state
  SET active_jobs = (
    SELECT jsonb_agg(elem)
    FROM jsonb_array_elements(active_jobs) AS elem
    WHERE elem->>'job_id' != p_job_id
  )
  WHERE id = 1;
END;
$$ LANGUAGE plpgsql;
```

---

### Step 6 — Change `process_next_queued_job()` to trigger ALL non-conflicting jobs

Currently it finds the FIRST eligible queued job and returns. With parallel execution, after a job completes there may be multiple queued jobs that can now start (they each conflict with the job that just finished, but not with each other or with any remaining active job).

```python
def process_next_queued_job(self):
    # Get all blocked jobs (ordered by queued_at FIFO)
    queued_jobs = self.main_supabase.table('jobs')...

    if not queued_jobs:
        return None

    # Get currently active models (after the completing job has been removed)
    state = self.get_active_job_state()
    active_jobs = state.get('active_jobs', []) if state else []
    active_models_union = set()
    for j in active_jobs:
        active_models_union.update(j.get('models', []))

    triggered = []
    reserved_models = set(active_models_union)  # grows as we pre-claim slots below

    for job in queued_jobs:
        job_id = job.get('job_id')
        required = set(job.get('required_models') or [self.get_job_model(job)])

        if required & reserved_models:
            # Would conflict with something already active or already pre-claimed this cycle
            continue

        # Pre-claim the slot — appends to active_jobs
        start_result = self.on_job_start(job_id, job.get('job_type', 'image'), list(required))
        if start_result['allowed']:
            reserved_models |= required   # prevent next iteration from double-booking same models
            self._trigger_job_processing(job)
            triggered.append(job_id)

    return triggered if triggered else None
```

---

### Step 7 — Update `validate_job_queue_state_on_startup()` for multi-slot state

Currently clears the single active slot. Must now clear all entries from `active_jobs`:

```python
# CURRENT
coordinator.clear_active_job()

# PROPOSED
# Reset entire active_jobs array
self.supabase.table('job_queue_state').update({
    'active_jobs': [],
    'active_job_id': None,   # keep old columns null for compat
    'active_job_type': None,
    'active_models': [],
    'last_updated': datetime.utcnow().isoformat()
}).eq('id', 1).execute()
```

---

## Files Changed

| File | Change |
|------|--------|
| Worker1 DB migration | Add `active_jobs JSONB DEFAULT '[]'` column to `job_queue_state` |
| Worker1 DB migration | Add `append_active_job(job_id, job_type, models)` RPC |
| Worker1 DB migration | Add `remove_active_job(job_id)` RPC |
| `backend/job_coordinator.py` | `get_active_job_state()` — read `active_jobs` array into cache |
| `backend/job_coordinator.py` | `set_active_job()` — append to `active_jobs` via RPC |
| `backend/job_coordinator.py` | `clear_active_job(job_id)` — add `job_id` param; remove from array via RPC |
| `backend/job_coordinator.py` | `can_start_job()` — build union of active models; check conflict; name specific blocker |
| `backend/job_coordinator.py` | `process_next_queued_job()` — trigger ALL non-conflicting queued jobs in one pass |
| `backend/job_coordinator.py` | `on_job_complete(job_id, ...)` — pass `job_id` to `clear_active_job()` |
| `backend/job_coordinator.py` | `validate_job_queue_state_on_startup()` — reset `active_jobs = []` |
| `backend/workflow_manager.py` | Pass `required_models` to `on_job_complete()` if needed for validation |

---

## New Risks & Mitigations

| Risk | Scenario | Mitigation |
|------|----------|------------|
| **Cross-process race on `active_jobs` append** | app.py and worker both call `set_active_job()` within milliseconds → read-modify-write collision → one job's entry lost | Use DB-level atomic RPC (`append_active_job` stored proc) instead of Python read-modify-write |
| **`process_next_queued_job()` double-trigger** | Two jobs complete nearly simultaneously → both call `process_next_queued_job()` → both see the same queued job as eligible → it starts twice | Already mitigated: both calls hold `_coordinator_lock`. The second call sees `active_jobs` already contains the pre-claimed job and the self-reservation check allows it through without re-claiming. |
| **Workflow uses same model as normal job in future** | A new workflow added later uses `sdxl-lightning-xeven` — an operator adds it expecting it to block image jobs, but forgets about this system | The model-conflict check handles it automatically — the conflict check compares exact model names. No extra risk if `required_models` is populated correctly in both configs. |
| **`blocked_by_job_id` points to a job that is one of many actives** | A blocked job could be blocked by job A, but job A completes while job B (also conflicting) is still running | `process_next_queued_job()` checks against the current `active_jobs` union — it will correctly NOT unblock the job while job B is still active. The `blocked_by_job_id` value is just informational (for UI display); the coordinator doesn't rely on it for scheduling logic. |
| **Startup clears `active_jobs` while a workflow is mid-execution** | Worker restarts while app.py is still running a workflow → `validate_job_queue_state_on_startup()` clears `active_jobs` → new normal job starts → both run, but workflow's models are now untracked | This is acceptable — the workflow in app.py already has its own execution guard in `base_workflow.py` (per-step status checks). The real risk is a new workflow starting alongside the old one and conflicting. Mitigation: startup validation should check for `status=running` workflow jobs in the main DB and re-populate `active_jobs` from them before clearing stale entries. |

---

## Interaction with Existing Issues

| Issue # | Interaction |
|---------|-------------|
| **#3** (pre-claim race) | Pre-claim logic still needed, unchanged — but now appends to array rather than setting a single slot |
| **#11** (duplicate `started` log) | Self-reservation check still valid — detect pre-claim by searching `active_jobs` for `job_id` match |
| **#17** (UPDATE event double-execution) | Unchanged — workflow UPDATE events still skipped in worker |
| **#21** (UPDATE channel removed) | Unchanged — no new UPDATE channel introduced |
| **Issue fixed 2026-03-25** (wrong DB for `jobs` table) | Unchanged — `main_supabase` for `jobs` table, `self.supabase` for `job_queue_state` |

---

## Expected Real-World Impact

Given current workflow model set (`vision-aicc` + `vision-clipdrop`) vs normal job providers (all different):

- **Before**: workflow running (60–90s) → ALL normal jobs wait queued → user sees "pending" for entire workflow duration
- **After**: workflow running → normal image/video jobs start immediately → only a 2nd workflow or a job using `vision-aicc`/`vision-clipdrop` is queued

In the current provider setup, **100% of workflow + normal job combinations would pass the conflict check** and run in parallel. Queuing only occurs between two concurrent workflows or two jobs on the same provider.

---

# Implemented Fixes — Parallel Coordinator Rewrite (2026-03-25)

The enhancement above was implemented. The following issues were discovered and fixed during the full implementation.

---

## Issue #22 — CRITICAL: `JobCoordinator` used Worker1 DB client for `jobs` table — all `blocked_by_job_id` writes were silent no-ops

**File**: `job_coordinator.py` → `mark_job_queued()`, `clear_job_queue_info()`, `process_next_queued_job()`

### Root Cause

```python
# BEFORE (broken) — __init__
self.supabase = get_worker1_client()   # Worker1 DB
# ...
# mark_job_queued writes blocked_by_job_id
self.supabase.table('jobs').update({...}).eq('job_id', job_id).execute()
#                  ^^^^ 'jobs' lives in MAIN DB — Worker1 has no 'jobs' table
```

`get_worker1_client()` returns a Supabase client pointed at Worker1 (`gmhpbeqvqpuoctaqgnum`), which holds `job_queue_state`, `job_queue_log`, and `provider_api_keys`. The `jobs` table is in the **main** Supabase database (`gtgnwrwbcxvasgetfzby`).

Every call that wrote `blocked_by_job_id`, `queued_at`, or `required_models` to the `jobs` table silently wrote to a non-existent table in Worker1 — Supabase RLS returns no error for cross-DB mismatches, the rows just don't exist.

`process_next_queued_job()` queried the Worker1 `jobs` table for `blocked_by_job_id IS NOT NULL` — always empty — so blocked jobs were **never unblocked**. This is the primary cause of the original reported bug: "blocked jobs stuck forever after workflow completes."

### Impact

- `blocked_by_job_id` written by `mark_job_queued()` never persisted → DB stays at `blocked_by_job_id=NULL`
- `process_next_queued_job()` query returned zero rows → `"No queued jobs found"` → no jobs ever triggered after a workflow finishes
- Normal jobs blocked by a running workflow stayed `pending` until a worker restart
- `queued_at` timestamps never written → FIFO ordering meaningless

### Fix

```python
# AFTER (fixed) — __init__
from supabase_client import supabase as _main_supabase
self.main_supabase = _main_supabase   # main DB — for 'jobs' table
self.supabase = get_worker1_client()  # Worker1 DB — for 'job_queue_state', 'job_queue_log'

# All jobs-table operations now use self.main_supabase
self.main_supabase.table('jobs').update({...}).eq('job_id', job_id).execute()
```

`self.supabase` (Worker1) is used exclusively for `job_queue_state` and `job_queue_log`. `self.main_supabase` is used for all `jobs` table reads/writes.

---

## Issue #23 — CRITICAL: `threading.RLock` provides no cross-process atomicity — two processes can both claim coordinator slot simultaneously

**File**: `job_coordinator.py` → `on_job_start()`, `on_job_complete()`

### Root Cause

```python
# BEFORE (broken)
self._coordinator_lock = threading.RLock()

def on_job_start(self, job_id, job_type, models):
    with self._coordinator_lock:
        state = self.get_active_job_state()  # DB read
        # ... check + decide ...
        self.set_active_job(job_id, ...)     # DB write
```

`threading.RLock` is an **intra-process** mutex. It protects concurrent threads within `app.py` OR within `job_worker_realtime.py` — but these are two separate OS processes with separate memory spaces. There is zero synchronization between them.

**Race window**: `app.py` executes `on_job_start()` for a workflow. Simultaneously, `job_worker_realtime.py` executes `on_job_start()` for a normal job. Both read `active_job_id = NULL` from DB. Both decide "no active job — allowed". Both write their job as active. The DB write is a simple `UPDATE SET active_job_id=?` — the second write overwrites the first. Now one job's slot is silently lost from the coordinator state.

### Impact

- Two jobs run simultaneously even when their models conflict
- The overwritten job's slot is never in `job_queue_state` → `on_job_complete()` for that job calls `process_next_queued_job()` which sees an empty or wrong active state → may trigger jobs at wrong time
- Extremely hard to reproduce in development (single process) but consistently present in production (two processes on separate dynos/containers)

### Fix

Replace Python-level locking with a PostgreSQL `SELECT FOR UPDATE` stored procedure. The DB row-level lock is the only synchronization primitive that is truly atomic across multiple OS processes:

```sql
-- try_claim_coordinator_slot: atomic check + claim in one transaction
SELECT * FROM job_queue_state WHERE id = 1 FOR UPDATE;
-- check for self-reservation
-- check for model conflicts
-- append new slot to active_jobs
-- commit → lock released
```

Python coordinator calls `try_claim_coordinator_slot(job_id, job_type, models)` RPC. The entire check+write is one atomic DB transaction. Any concurrent call from any process blocks on `FOR UPDATE` until the transaction commits.

---

## Issue #24 — HIGH: `on_job_complete()` called from `finally` even when coordinator slot was never claimed

**File**: `workflow_manager.py` → `execute_workflow()`, `resume_workflow()`

### Root Cause

```python
# BEFORE (broken)
try:
    start_result = coordinator.on_job_start(job_id, "workflow", required_models)
    if not start_result['allowed']:
        raise RuntimeError("Workflow queued: ...")  # caught by except, re-raised
    ...
except Exception as e:
    raise
finally:
    coordinator.on_job_complete(job_id, "workflow")  # ← always fires, even if start was not allowed
```

When `on_job_start()` returns `allowed=False` (job was blocked), the code raises `RuntimeError`. The `finally` block fires unconditionally and calls `on_job_complete()`. This triggers `release_slot(job_id)` (no-op — slot was never claimed) and then `process_next_queued_job()`. Calling `process_next_queued_job()` spuriously is harmless most of the time, but:

1. It causes a DB round-trip on every coordinator block event
2. It can trigger a queued job prematurely if the blocking job happens to complete at the exact same moment
3. Log pollution: "Job X completed" log line for a job that never started

### Impact

- Every blocked workflow emits a spurious `on_job_complete` + `process_next_queued_job` cycle
- In high-concurrency scenarios, the spurious trigger can race with the legitimate `on_job_complete` from the running job → two concurrent `process_next_queued_job` calls

### Fix

```python
# AFTER (fixed)
coordinator_slot_claimed = False   # set True only after on_job_start succeeds

start_result = coordinator.on_job_start(job_id, "workflow", required_models)
if not start_result['allowed']:
    raise RuntimeError(f"Workflow queued: {start_result['reason']}")

coordinator_slot_claimed = True    # slot is now held
try:
    ...
finally:
    if coordinator_slot_claimed:
        coordinator.on_job_complete(job_id, "workflow")  # only fires if slot was claimed
```

The `coordinator_slot_claimed` flag ensures `on_job_complete` only fires when a slot was actually acquired.

---

## Issue #25 — HIGH: `blocked_by_job_id` not cleared in `already_active` / cache-hit paths — job keeps reappearing in coordinator queue

**File**: `job_coordinator.py` → `on_job_start()`

### Root Cause

When `on_job_start()` is called for a job that is already pre-claimed (self-reservation), the code returns `allowed=True` without calling `clear_job_queue_info()`:

```python
# BEFORE (broken) — cache hit path
if self._cache_contains(job_id):
    logger.info(f"[COORDINATOR] Cache hit for {job_id} — slot already pre-claimed")
    return {'allowed': True, 'reason': 'Pre-claimed (cache)'}
    # clear_job_queue_info() NOT called
```

`clear_job_queue_info()` removes `blocked_by_job_id` and `queued_at` from the `jobs` row. If these are not cleared when the job is actually allowed to start, the job still appears as `blocked_by_job_id IS NOT NULL` in the DB. The next `process_next_queued_job()` sweep finds this job again — still in the list — and tries to trigger it a second time.

The same issue existed in the `already_active` RPC result path (job_id found in DB's `active_jobs` array).

### Impact

- A pre-claimed job that gets the cache-hit or already_active path keeps `blocked_by_job_id` set
- Next `process_next_queued_job()` call re-triggers the already-running job
- Leads to a second `_trigger_job_processing()` call for an in-flight job → double execution

### Fix

```python
# AFTER (fixed) — clear queue info in ALL allowed paths

# Cache hit path
if self._cache_contains(job_id):
    self.clear_job_queue_info(job_id)   # ← added
    return {'allowed': True, 'reason': 'Pre-claimed (cache)'}

# already_active RPC result
if rpc_result == 'already_active':
    self.clear_job_queue_info(job_id)   # ← added
    return {'allowed': True, 'reason': 'already_active'}

# claimed RPC result (new claim)
if rpc_result == 'claimed':
    self.clear_job_queue_info(job_id)   # ← already here
    return {'allowed': True, ...}
```

`clear_job_queue_info()` is called in every allowed-to-start path, ensuring `blocked_by_job_id` and `queued_at` are always cleared when the job actually starts.

---

## Issue #26 — HIGH: Workflow job stays `running` in DB when coordinator blocks it — `process_next_queued_job()` never finds it

**File**: `job_coordinator.py` → `mark_job_queued()`

### Root Cause

When `on_job_start()` returns `allowed=False`, `mark_job_queued()` sets `blocked_by_job_id` and `queued_at`, but does not check or reset the job's `status`. If the job arrived via the realtime path, `handle_new_job()` had already set `status=running` in the DB (or it was set by a prior execution attempt). The job sits with `status=running` and `blocked_by_job_id IS NOT NULL`.

`process_next_queued_job()` queried:
```python
.in_('status', ['pending', 'pending_retry'])
.not_.is_('blocked_by_job_id', 'null')
```

`status=running` is not in `['pending', 'pending_retry']` → the job is never found → it waits forever.

In practice this happened most visibly for workflow jobs: `handle_new_job()` sets `status=running` immediately when it spawns the workflow thread, before `coordinator.on_job_start()` is called. If the coordinator blocks the job, the DB shows `running` + `blocked_by_job_id IS NOT NULL` — an impossible state that the query filter never matches.

### Impact

- Any workflow job that is blocked by the coordinator while `status=running` never gets unblocked
- Job appears to the user as perpetually "running" with no progress
- No log evidence of the issue from the coordinator's perspective — `process_next_queued_job()` simply reports "No queued jobs found"

### Fix

```python
# AFTER (fixed) — mark_job_queued() conditionally resets running → pending
self.main_supabase.table('jobs').update({
    'blocked_by_job_id': blocked_by,
    'queued_at': datetime.utcnow().isoformat(),
}).eq('job_id', job_id).execute()

# Reset running → pending so process_next_queued_job() can find it
self.main_supabase.table('jobs').update({
    'status': 'pending'
}).eq('job_id', job_id).eq('status', 'running').execute()
#                         ^^^ guard: only reset if still 'running'
```

The `.eq('status', 'running')` guard prevents accidentally resetting a job that was already set to `pending_retry` or another status by a concurrent write.

---

## Issue #27 — HIGH: Retry sweep races with coordinator when promoting blocked workflow to `running`

**File**: `job_coordinator.py` → `process_next_queued_job()`

### Root Cause

When unblocking a workflow job, the original code:
1. Called `clear_job_queue_info()` (clears `blocked_by_job_id`, sets `queued_at=NULL`)
2. Set `status=running`
3. Spawned `_trigger_job_processing()`

Step 1 happens before step 2. Between step 1 and step 2, `blocked_by_job_id` is `NULL` and `status` is still `pending`. The `workflow_retry_manager` periodic sweep queries:
```python
.eq('status', 'pending')
.is_('blocked_by_job_id', 'null')
```

This window allows the retry manager to find the job and spawn a second thread — just as the coordinator is also about to spawn a thread. Both threads call `on_job_start()`. The self-reservation check allows both through. **Double execution**.

### Impact

- Every coordinator-triggered workflow resume has a brief race window where the retry sweep can also pick it up
- Two simultaneous workflow executions for the same job
- Steps run twice, checkpoints overwrite each other, duplicate AI API calls

### Fix

Reverse the order — set `status=running` **before** clearing `blocked_by_job_id`:

```python
# AFTER (fixed) — for workflow jobs in process_next_queued_job()
# Step 1: promote status to 'running' first — closes the retry sweep's query window
self.main_supabase.table('jobs').update({
    'status': 'running'
}).eq('job_id', job_id).execute()

# Step 2: now safe to clear blocked_by_job_id
self.clear_job_queue_info(job_id)

# Step 3: spawn thread
self._trigger_job_processing(job)
```

Once `status=running`, the retry sweep's `status=pending` filter excludes this job. The coordinator's thread is the sole executor.

---

## Issue #28 — HIGH: `workflows` with `required_models=[]` stored — `process_next_queued_job()` sees no conflict → two workflows claim slots simultaneously

**File**: `job_coordinator.py` → `process_next_queued_job()`

### Root Cause

When a workflow job is blocked by the coordinator and written to the queue, `required_models` is stored in the `jobs` row. In some cases (race between `execute_workflow()` and the DB write), `required_models` is persisted as an empty list `[]`.

When `process_next_queued_job()` reads two queued workflow jobs with `required_models=[]`:

```python
required_models_a = []   # workflow A
required_models_b = []   # workflow B

# Conflict check: set([]) & set([]) == set() — empty intersection → NO CONFLICT
# Both are "claimed" — run in parallel
```

An empty model set has no intersection with anything. Two workflows that both require `vision-aicc` + `clipdrop` appear conflict-free and both claim slots. Both then attempt to call the same external APIs simultaneously.

### Impact

- Two concurrent workflows using the same AI providers → quota exhaustion, rate limits, or corrupted shared state
- The second workflow's `on_job_start()` returns `claimed` (correctly) because `required_models=[]` has no conflict — but the intent was to block it
- One workflow typically fails with a provider quota error → `pending_retry` → retry manager handles it, but the root cause remains

### Fix

```python
# AFTER (fixed) — in process_next_queued_job(), re-extract models when stored list is empty
required_models = job.get('required_models') or []

if not required_models:
    if job_type == 'workflow':
        try:
            from workflow_manager import get_workflow_manager
            _wm = get_workflow_manager()
            _cfg = _wm.get_workflow(job.get('model', ''))
            if _cfg:
                required_models = self.get_workflow_models(_cfg)
                if required_models:
                    # Persist so next call doesn't need to re-extract
                    self.main_supabase.table('jobs').update(
                        {'required_models': required_models}
                    ).eq('job_id', job_id).execute()
        except Exception as _e:
            logger.warning(f"Could not re-extract models for workflow {job_id}: {_e}")
    else:
        required_models = [self.get_job_model(job)]
```

If `required_models` is empty for a workflow, the config is re-read to get the correct model list. The repopulated value is written back to the DB so future calls don't repeat the work.

---

## Issue #29 — MEDIUM: `try_claim_coordinator_slot` RPC received a JSON string instead of JSONB — PostgreSQL error 22023

**File**: `job_coordinator.py` → `try_claim_slot()`

### Root Cause

```python
# BEFORE (broken)
import json

def try_claim_slot(self, job_id, job_type, models):
    result = self.supabase.rpc('try_claim_coordinator_slot', {
        'p_job_id':   job_id,
        'p_job_type': job_type,
        'p_models':   json.dumps(models),   # ← produces a JSON string: '["model-a", "model-b"]'
    }).execute()
```

`json.dumps(models)` produces a Python `str`. The Supabase Python client serializes this as a JSON string in the RPC payload. PostgreSQL receives `p_models` as a `text` scalar — not a `jsonb` array. When the stored procedure calls `jsonb_array_elements(p_models)`, PostgreSQL raises:

```
ERROR: cannot extract elements from a scalar
DETAIL: ERROR: 22023
```

### Impact

- Every `on_job_start()` call throws a `PostgrestAPIError` with code `22023`
- Coordinator never claims any slot — all jobs are effectively "allowed" through without coordination
- No model-conflict checking — parallel execution without guard rails

### Fix

```python
# AFTER (fixed)
def try_claim_slot(self, job_id, job_type, models):
    result = self.supabase.rpc('try_claim_coordinator_slot', {
        'p_job_id':   job_id,
        'p_job_type': job_type,
        'p_models':   models,   # ← pass Python list directly; Supabase client serialises to JSONB
    }).execute()
```

Passing the Python `list` directly lets the Supabase client serialize it as a JSON array, which PostgreSQL correctly receives as `jsonb`. The `import json` statement was also removed (no longer needed in this module).

---

## Updated Fix Summary Table

| # | Severity | File(s) | Description |
|---|---|---|---|
| 22 | 🔴 Critical | `job_coordinator.py` | Use `main_supabase` (main DB) for all `jobs` table ops; use `self.supabase` (Worker1) only for `job_queue_state` / `job_queue_log` |
| 23 | 🔴 Critical | `job_coordinator.py` + Worker1 DB | Replace `threading.RLock` with PostgreSQL `SELECT FOR UPDATE` stored procs (`try_claim_coordinator_slot`, `release_coordinator_slot`) for true cross-process atomicity |
| 24 | 🟠 High | `workflow_manager.py` | `coordinator_slot_claimed` flag — only call `on_job_complete()` in `finally` when slot was actually acquired |
| 25 | 🟠 High | `job_coordinator.py` | Call `clear_job_queue_info()` in ALL allowed paths (`claimed`, `already_active`, cache hit) — prevents `blocked_by_job_id` staying set after job starts |
| 26 | 🟠 High | `job_coordinator.py` | `mark_job_queued()` conditionally resets `status: running → pending` (guarded by `.eq('status', 'running')`) — workflows blocked while `running` now visible to `process_next_queued_job()` |
| 27 | 🟠 High | `job_coordinator.py` | Set `status=running` BEFORE clearing `blocked_by_job_id` in `process_next_queued_job()` — closes retry-sweep race window |
| 28 | 🟠 High | `job_coordinator.py` | Re-extract `required_models` from workflow config when stored value is `[]` — prevents empty-set false "no conflict" between two workflows |
| 29 | 🟡 Medium | `job_coordinator.py` | Pass `models` as Python `list` to `try_claim_coordinator_slot` RPC (not `json.dumps`) — fixes PostgreSQL `22023: cannot extract elements from a scalar` error |
