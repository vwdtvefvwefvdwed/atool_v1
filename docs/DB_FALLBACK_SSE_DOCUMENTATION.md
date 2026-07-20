# DB Fallback for SSE - Implementation Documentation

## Date: 2026-05-16 (Updated with bug fixes)

## Problem

Frontend showed jobs as "running" forever even when:
- Job completed successfully in the database
- Job failed in the database

### Root Cause
- Supabase Realtime websocket kept disconnecting (1001 "going away" errors)
- SSE endpoint relied entirely on Supabase Realtime for updates
- No fallback mechanism when Supabase Realtime failed
- Frontend never received job completion status

---

## Solution Implemented

### DB Fallback in SSE Endpoint (`backend/app.py`)

Added database polling fallback inside the `generate()` SSE generator. Every 30s keepalive timeout, the database is queried directly so the frontend receives status updates even when Supabase Realtime is completely down.

### How It Works

#### Normal Case (Supabase Realtime Working)
```
Job updates → Supabase DB write → Supabase Realtime → SSE queue → Frontend
```

#### Fallback Case (Supabase Realtime Down)
```
Job completes → DB write
Supabase Realtime fails (1001 error)
30s keepalive timeout triggers
DB fallback: queries Supabase directly
Finds status = "completed/failed/cancelled" → sends update + complete event to frontend ✓
```

### Timing

| Event | Time |
|-------|------|
| Job completes | t=0 |
| Supabase Realtime fails | t=0 |
| Keepalive timeout | t=~30s |
| DB fallback triggers | t=~30s |
| Frontend updated | t=~30s |

### Coverage

| Job Type | DB Fallback Works? |
|----------|-------------------|
| Workflow jobs (in app.py) | ✓ Yes |
| Worker jobs (job_worker_realtime) | ✓ Yes |

---

## Bugs Found and Fixed (2026-05-16)

### Bug 1 — `UnboundLocalError` in `generate()` (Critical)

**Problem:**  
The previous "fix" added assignments to `current_job` inside `generate()` (lines with `current_job = ...`), which caused Python to treat `current_job` as a local variable throughout the entire function. Any read of `current_job` before those assignments threw `UnboundLocalError`.

**Root cause:**  
Python's compiler marks a variable as local to a function if it is assigned *anywhere* in that function — even inside a conditional branch. Both the safeguard block and the DB fallback loop assigned `current_job`, making every read of it fail with `UnboundLocalError`.

**Fix:**  
Added `nonlocal current_job` at the very top of `generate()`. This tells Python to use the variable from the enclosing `jobs_stream_status()` scope instead of creating a new local.

Removed the broken safeguard block entirely — it was never needed once `nonlocal` is used correctly.

```python
def generate():
    nonlocal current_job   # correct fix
    ...
```

---

### Bug 2 — Dead `consecutive_empty` Counter

**Problem:**  
The counter incremented to 1 then immediately checked `>= 1` (always true) and reset to 0. It never accumulated. The DB was polled every 30s regardless, making the counter meaningless.

**Fix:**  
Removed the counter entirely. The DB is now polled directly on every keepalive timeout — which is the intended behavior.

---

### Bug 3 — `current_job` Not Updated on Realtime Events

**Problem:**  
When a realtime event arrived and `job_data` was extracted, `current_job` was never updated. This meant the DB fallback's status-change comparison (`fresh_status != current_status`) always compared against the initial status from the catch-up fetch, not the latest known status.

**Fix:**  
Added `current_job = job_data` immediately after extracting the realtime payload.

---

### Bug 4 — Redundant `mimetype` in Flask Response

**Problem:**  
Both `mimetype=` and `content_type=` were set on the `Response()` object. In Flask, `content_type` takes precedence and overrides `mimetype`, making `mimetype` redundant.

**Fix:**  
Removed the `mimetype=` kwarg, keeping only `content_type=`.

---

### Bug 5 — Frontend SSE Reconnect Loop

**Problem:**  
After the server sent `complete` and closed the stream, `EventSourcePolyfill`'s `onerror` fired. If `isCompletedRef.current` was not yet `true` at that moment (race condition between event processing and connection close), the client reconnected — causing the SSE endpoint to be hit 3+ times in a row for the same already-finished job.

Additionally, `isCompletedRef.current = true` was only set inside `if (data.job && onComplete)` in the `complete` handler. If `onComplete` was not provided, `isCompletedRef` was never set, causing infinite reconnects.

**Fix** (`src/components/JobStatus.jsx`):  
In the `complete` event listener, `isCompletedRef.current = true` and `eventSource.close()` are now set **unconditionally** whenever `data.job` exists, regardless of the job status or whether `onComplete` is provided. Added a fallback `else` branch for when `data.job` is missing that also closes and marks complete.

---

### Bug 6 — `complete` Event Called Wrong Callback for Failed Jobs

**Problem:**  
The `complete` event handler always called `onComplete(data.job)` regardless of `data.job.status`. When a job failed, the server sent `event: complete` with `status: "failed"`, but the frontend called `onComplete` (success path) instead of `onError`.

**Fix** (`src/components/JobStatus.jsx`):  
The `complete` event handler now branches on `data.job.status`:
- `completed` → calls `onComplete`
- `failed` / `cancelled` → calls `onError`

---

### Bug 7 — `cancelled` Status Not Handled in Frontend

**Problem:**  
The `update` event listener and `onmessage` handler only closed the `EventSource` for `completed` and `failed`. If a job reached `cancelled` status, the stream was never closed by the client.

**Fix** (`src/components/JobStatus.jsx`):  
Added `|| updatedJob.status === 'cancelled'` to all terminal status checks in both `onmessage` and the `update` named event listener.

---

## Final Code Structure (`backend/app.py`)

```python
def generate():
    nonlocal current_job                    # access outer scope variable
    try:
        yield connected event               # initial handshake
        yield update event (catch-up)       # send current DB state immediately
        if already terminal: yield complete, return

        while True:
            try:
                payload = queue.get(timeout=30)
                current_job = job_data      # keep current_job in sync
                yield update event
                if terminal: yield complete, break

            except queue.Empty:
                yield keepalive
                # DB FALLBACK: query DB every 30s
                fresh_job = supabase.query(job_id)
                if status changed: yield update event
                if terminal: yield update (if not already sent) + complete, break

    except GeneratorExit: ...
    finally: unsubscribe from realtime manager
```

---

## File Change Summary

| File | Change |
|------|--------|
| `backend/app.py` | Added `nonlocal current_job` in `generate()` |
| `backend/app.py` | Removed broken safeguard block |
| `backend/app.py` | Removed dead `consecutive_empty` counter |
| `backend/app.py` | Added `current_job = job_data` on realtime events |
| `backend/app.py` | Removed redundant `mimetype=` from Response |
| `src/components/JobStatus.jsx` | Fixed `complete` event handler — correct callback per status |
| `src/components/JobStatus.jsx` | Fixed reconnect loop — `isCompletedRef` set unconditionally |
| `src/components/JobStatus.jsx` | Added `cancelled` status handling in all terminal checks |

---

## Future Improvements

1. **Direct in-process notification** — Push updates directly from workflow code to SSE subscribers, bypassing Supabase Realtime entirely
2. **Worker callback** — When worker completes a job, make HTTP call to web process directly
3. **Postgres LISTEN/NOTIFY** — Use persistent DB connection instead of websocket
4. **SSE auth header** — JWT is currently passed as a URL query param (`?token=XXX`); should use `Authorization` header in `EventSourcePolyfill` to avoid token leaking in server logs
