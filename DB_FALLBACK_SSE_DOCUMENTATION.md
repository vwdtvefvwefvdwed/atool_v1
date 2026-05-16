# DB Fallback for SSE - Implementation Documentation

## Date: 2026-05-16

## Problem

Frontend showed jobs as "running" forever even when:
- Job completed successfully in the database
- Job failed in the database

### Root Cause
- Supabase Realtime websocket kept disconnecting (1001 "going away" errors)
- SSE endpoint relied entirely on Supabase Realtime for updates
- No fallback mechanism when Supabase Realtime failed
- Frontend never received job completion status

## Solution Implemented

### Phase 1: DB Fallback in SSE Endpoint

Added database polling fallback in `backend/app.py` to check job status directly from Supabase when Supabase Realtime is not working.

#### Changes in `backend/app.py`

1. **Line 1281**: Added `consecutive_empty = 0` counter at the start of `generate()` function

2. **Line 1302**: Reset counter when a realtime event is received (means Supabase Realtime is working)

3. **Lines 1344-1387**: Added DB fallback in the keepalive timeout:
   - On first keepalive timeout (~30s), poll the database
   - Fetch fresh job status from Supabase
   - If status changed, send update event to frontend
   - If terminal status (completed/failed/cancelled), send complete event and close stream

4. **Lines 1279-1286**: Added safeguard to ensure `current_job` is always defined

### How It Works

#### Normal Case (Supabase Realtime Working)
```
Job updates → Supabase DB write → Supabase Realtime → SSE → Frontend
Counter resets on each realtime event → No DB polling needed
```

#### Fallback Case (Supabase Realtime Down)
```
Job completes → DB write
Supabase Realtime fails (1001 error)
30s keepalive timeout triggers
DB fallback: queries Supabase directly
Finds status = "completed" → sends complete event to frontend ✓
```

### Timing

| Event | Time |
|-------|------|
| Job completes | t=0 |
| Supabase Realtime fails | t=0 |
| 1st keepalive timeout | t=~30s |
| DB fallback triggers | t=~30s |
| Frontend updated | t=~30s |

### Coverage

| Job Type | DB Fallback Works? |
|----------|-------------------|
| Workflow jobs (in app.py) | ✓ Yes |
| Worker jobs (job_worker_realtime) | ✓ Yes |

Both work because the fallback queries Supabase database directly, not local state.

## Code Locations

| File | Line | Change |
|------|------|--------|
| backend/app.py | 1279-1286 | Safeguard for current_job |
| backend/app.py | 1281 | Initialize consecutive_empty counter |
| backend/app.py | 1302 | Reset counter on realtime event |
| backend/app.py | 1344-1387 | DB fallback polling logic |

## Future Improvements (Phase 2)

To make updates even faster (instant instead of ~30s):

1. **Direct in-process notification**: Push updates directly from workflow code to SSE subscribers, bypassing Supabase Realtime entirely
2. **Worker callback**: When worker completes a job, make HTTP call to web process directly
3. **Postgres LISTEN/NOTIFY**: Use persistent DB connection instead of websocket

## Security Note

The JWT is still passed in SSE URL query string (`?token=XXX`). This should be changed to use Authorization header in EventSourcePolyfill to avoid JWT leaking in logs.