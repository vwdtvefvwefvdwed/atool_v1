# HTTP Push Job Notification Implementation

## Overview

This document describes the HTTP Push mechanism for job notification from app.py to job_worker.

**This replaces the previous LISTEN/NOTIFY mechanism.**

## Architecture

```
User Request → app.py → INSERT job (status=pending) → HTTP POST to worker_url → Worker processes
                                     ↓
                            If no response: retry with backoff
                                     ↓
                            Retry 1: immediate
                            Retry 2: after 20 seconds
                            Retry 3: after 40 seconds
                                     ↓
                            If all fail: job stays pending
                                     ↓
                            Periodic retry (every 10 min) catches missed jobs
```

## Key Components

### 1. `jobs.py:notify_worker()`

Sends HTTP POST to worker after job creation:
- URL: `{WORKER_URL}/worker/process-job`
- Payload: `{"job_id": "..."}`
- Timeout: 5 seconds per request (non-blocking)
- Runs in background thread
- Skips workflow jobs (handled by app.py)

### 2. `jobs.py:_notify_worker_with_retry()`

Internal retry logic with exponential backoff:
- **Attempt 1**: immediate
- **Attempt 2**: after 20 seconds
- **Attempt 3**: after 40 seconds
- If all fail: job stays pending

### 3. `job_worker_realtime.py:/worker/process-job`

HTTP endpoint that receives job notifications:
- Validates job exists and is pending
- Skips workflow jobs
- Queues job for processing in background thread

### 4. Fallback Mechanisms

1. **Periodic Retry** (`retry_transient_errors()`): Every 10 minutes
   - Transient errors (cloudinary, timeout, network, connection)
   - Quota exceeded (after 24 hours)
   - **STALE jobs**: pending with no error, created >2 min ago (never reached worker)

2. **Worker Startup Backlog**: On restart, processes all pending jobs
3. **API Key Insertion Handler**: Re-triggers jobs waiting for API keys

## Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `WORKER_URL` | URL of the worker service | `https://atool-worker.onrender.com` |

## Migration

Run this SQL in Supabase to remove the old LISTEN/NOTIFY trigger:

```sql
-- File: backend/migrations/031_remove_listen_notify_trigger.sql
DROP TRIGGER IF EXISTS job_insert_notify ON jobs;
DROP FUNCTION IF EXISTS notify_job_insert();
```

## What Remains Unchanged

| Component | Purpose |
|-----------|---------|
| `api_key_realtime_listener()` | Listens for new API keys to re-trigger waiting jobs |
| `priority_lock_listener()` | Monitors system_flags for priority lock changes |
| `retry_transient_errors()` | Periodic retry for failed AND stale jobs |
| `reset_running_jobs_to_pending()` | Startup cleanup for crashed jobs |

## Comparison

| Aspect | LISTEN/NOTIFY | HTTP Push |
|--------|---------------|-----------|
| Job delivery | Database trigger → asyncpg | HTTP POST with retry |
| Retry on failure | None | 3 attempts (0s, 20s, 40s) |
| Worker down handling | Jobs pile up | Same + periodic retry |
| Stale job detection | None | Every 10 min (>2 min old) |
| Complexity | Medium | Low |
| Dependencies | asyncpg, DATABASE_URL | requests only |

## Testing

1. Create a job via app.py
2. Check logs for `[WORKER_NOTIFY]` messages
3. Verify job is processed

```bash
# Test the endpoint directly
curl -X POST https://your-worker-url/worker/process-job \
  -H "Content-Type: application/json" \
  -d '{"job_id": "test-job-id"}'
```

## Retry Timeline Example

```
T+0s:   [WORKER_NOTIFY] Attempt 1/3 - Sending job abc-123 to worker
T+5s:   [WORKER_NOTIFY] ⚠️ Timeout on attempt 1 (5s limit)
T+5s:   [WORKER_NOTIFY] Retrying in 20s... (attempt 2/3)
T+25s:  [WORKER_NOTIFY] Attempt 2/3 - Sending job abc-123 to worker
T+30s:  [WORKER_NOTIFY] ⚠️ Connection error on attempt 2
T+30s:  [WORKER_NOTIFY] Retrying in 40s... (attempt 3/3)
T+70s:  [WORKER_NOTIFY] Attempt 3/3 - Sending job abc-123 to worker
T+71s:  [WORKER_NOTIFY] ✅ Job abc-123 accepted by worker (attempt 3): queued
```

If all 3 attempts fail, the job stays pending and periodic retry catches it within 10 minutes.
