# Job Queue Coordinator - Usage Guide

## Overview
The Job Queue Coordinator prevents resource collapse by ensuring only one job per model runs at a time. When a workflow or normal job requests a model that's already in use, it's automatically queued and resumed when the blocking job completes.

---

## How It Works

### Workflow Execution Flow
```
1. User creates workflow job (e.g., knight-style-img-to-video)
   ↓
2. Workflow Manager extracts required models: ["nano-banana-pro-leonardo", "motion-2.0-fast"]
   ↓
3. Coordinator checks: Is any model in use?
   ↓
4a. NO CONFLICT → Workflow starts immediately
   ↓
4b. CONFLICT DETECTED → Workflow queued with blocking info
   ↓
5. When blocking job completes → Coordinator auto-processes next queued job
```

### Normal Job Execution Flow
```
1. User creates normal job (e.g., motion-2.0-fast video generation)
   ↓
2. Job Worker extracts required model: ["motion-2.0-fast"]
   ↓
3. Coordinator checks: Is this model in use?
   ↓
4a. NO CONFLICT → Job starts immediately
   ↓
4b. CONFLICT → Job queued
   ↓
5. When blocking job completes → Coordinator triggers next job
```

---

## File Structure

### Core Files
```
backend/
├── job_coordinator.py          # Core coordination logic
├── job_worker_realtime.py      # Updated with coordinator integration
├── workflow_manager.py         # Updated with coordinator integration
└── migrations/
    ├── 000_clean_schema_no_coins.sql      # Full schema (new accounts)
    └── 027_add_job_queue_coordination.sql  # Migration (existing accounts)
```

### Database Tables
- `jobs` - Added: `required_models`, `queue_position`, `blocked_by_job_id`, `conflict_reason`, `queued_at`
- `workflow_executions` - Added: `required_models`, `current_step_model`, `blocked_by_job_id`
- `job_queue_state` - NEW: Global state tracker
- `job_queue_log` - NEW: Audit trail

---

## Testing the System

### Test 1: Normal Job Coordination
```python
# Scenario: Create two jobs using the same model

# Job 1 starts
POST /jobs
{
  "prompt": "A knight riding a horse",
  "model": "motion-2.0-fast",
  "job_type": "video"
}
# Response: job_id_1

# Job 2 arrives while Job 1 is running
POST /jobs
{
  "prompt": "A dragon flying",
  "model": "motion-2.0-fast",
  "job_type": "video"
}
# Response: job_id_2

# Expected Behavior:
# - Job 1: Starts immediately
# - Job 2: Automatically queued
# - Job 2: Starts when Job 1 completes
```

**Check the logs:**
```bash
# Job 1 starts
[COORDINATOR] Checking if job job_id_1 can start - Models: ['motion-2.0-fast']
[COORDINATOR] No active job - job_id_1 can start immediately
[COORDINATOR] Set active job: job_id_1 (normal) - Models: ['motion-2.0-fast']

# Job 2 arrives
[COORDINATOR] Checking if job job_id_2 can start - Models: ['motion-2.0-fast']
[COORDINATOR] Model conflict detected: {'motion-2.0-fast'}
[COORDINATOR] Job job_id_2 blocked: Model conflict: motion-2.0-fast in use by normal job job_id_1
[COORDINATOR] Marked job job_id_2 as queued (blocked by job_id_1)

# Job 1 completes
[COORDINATOR] Job job_id_1 completed, checking for next queued job...
[COORDINATOR] Found 1 queued job(s), checking for next eligible job...
[COORDINATOR] Starting next queued job: job_id_2
```

### Test 2: Workflow vs Normal Job
```python
# Scenario: Workflow running when normal job arrives

# Start workflow (uses: nano-banana-pro-leonardo, motion-2.0-fast)
POST /workflows/knight-style-img-to-video/execute
{
  "input_image": "https://example.com/user.jpg"
}
# Response: workflow_job_id_1

# Create normal job while workflow is running
POST /jobs
{
  "prompt": "A cinematic video",
  "model": "motion-2.0-fast",  # CONFLICT!
  "job_type": "video"
}
# Response: normal_job_id_1

# Expected Behavior:
# - Workflow: Starts immediately
# - Normal job: Queued (model conflict: motion-2.0-fast)
# - Normal job: Starts when workflow completes
```

### Test 3: Serialized Execution (No Concurrency)
```python
# Scenario: ALL jobs are serialized (one at a time)

# Job 1
POST /jobs
{
  "prompt": "Generate image",
  "model": "flux-dev",
  "job_type": "image"
}

# Job 2 (different model, but still queued)
POST /jobs
{
  "prompt": "Generate video",
  "model": "minimax/video-01",
  "job_type": "video"
}

# Expected Behavior:
# - Job 1 starts immediately
# - Job 2 is queued (even with different model)
# - Job 2 starts when Job 1 completes
# - FIFO execution: one job at a time
```

---

## Monitoring Queue Status

### Check Global State
```sql
-- See what job is currently active
SELECT * FROM job_queue_state;

-- Example output:
-- id | active_job_id  | active_job_type | active_models                              | started_at
-- 1  | workflow_123   | workflow        | ["nano-banana-pro-leonardo", "motion-2.0-fast"] | 2026-02-19 10:30:00
```

### Check Queued Jobs
```sql
-- See all queued jobs
SELECT 
    job_id, 
    job_type, 
    model,
    blocked_by_job_id, 
    conflict_reason,
    queued_at
FROM jobs
WHERE blocked_by_job_id IS NOT NULL
ORDER BY queued_at ASC;

-- Example output:
-- job_id       | job_type | model            | blocked_by_job_id | conflict_reason                           | queued_at
-- normal_456   | video    | motion-2.0-fast  | workflow_123      | Model conflict: motion-2.0-fast in use... | 2026-02-19 10:31:00
```

### Check Queue Events (Audit Trail)
```sql
-- See recent queue events
SELECT 
    job_id, 
    job_type, 
    event_type, 
    conflict_reason,
    created_at
FROM job_queue_log
ORDER BY created_at DESC
LIMIT 20;

-- Example output:
-- job_id       | job_type | event_type | conflict_reason                           | created_at
-- workflow_123 | workflow | started    | NULL                                      | 2026-02-19 10:30:00
-- normal_456   | normal   | blocked    | Model conflict: motion-2.0-fast in use... | 2026-02-19 10:31:00
-- workflow_123 | workflow | completed  | NULL                                      | 2026-02-19 10:35:00
-- normal_456   | normal   | started    | NULL                                      | 2026-02-19 10:35:01
```

---

## API Integration

### Check Queue Status Endpoint (Optional - Add to app.py)
```python
@app.route("/queue/status", methods=["GET"])
@require_auth
def queue_status():
    """Get current queue status"""
    from job_coordinator import get_job_coordinator
    
    coordinator = get_job_coordinator()
    active_state = coordinator.get_active_job_state()
    
    # Get queued jobs
    queued_jobs = supabase.table('jobs').select('job_id, model, blocked_by_job_id, conflict_reason, queued_at')\
        .not_.is_('blocked_by_job_id', 'null')\
        .order('queued_at', desc=False)\
        .execute()
    
    return jsonify({
        "active_job": {
            "job_id": active_state.get('active_job_id') if active_state else None,
            "job_type": active_state.get('active_job_type') if active_state else None,
            "models": active_state.get('active_models', []) if active_state else []
        },
        "queued_jobs": queued_jobs.data if queued_jobs else [],
        "queue_length": len(queued_jobs.data) if queued_jobs and queued_jobs.data else 0
    }), 200
```

---

## Troubleshooting

### Issue: Jobs stuck in queue forever
**Symptoms:**
- Jobs have `blocked_by_job_id` but blocking job is completed
- `job_queue_state.active_job_id` is NULL but jobs still queued

**Solution:**
```sql
-- Clear stale queue info
UPDATE jobs 
SET blocked_by_job_id = NULL, 
    conflict_reason = NULL, 
    queued_at = NULL
WHERE blocked_by_job_id IS NOT NULL
  AND status = 'pending';

-- Reset global state
UPDATE job_queue_state 
SET active_job_id = NULL, 
    active_job_type = NULL, 
    active_models = '[]'::jsonb
WHERE id = 1;
```

### Issue: Coordinator not detecting conflicts
**Check:**
1. Are `required_models` populated in jobs table?
   ```sql
   SELECT job_id, model, required_models FROM jobs WHERE required_models IS NULL;
   ```

2. Is coordinator being called?
   ```bash
   # Check logs for:
   grep "COORDINATOR" worker.log
   ```

3. Is Supabase connection working?
   ```python
   from job_coordinator import get_job_coordinator
   coordinator = get_job_coordinator()
   state = coordinator.get_active_job_state()
   print(state)  # Should not be None
   ```

### Issue: Workflows not extracting models
**Check workflow config:**
```python
from workflows import get_all_workflows

workflows = get_all_workflows()
for workflow in workflows:
    print(f"Workflow: {workflow['id']}")
    for step in workflow.get('steps', []):
        print(f"  Step: {step.get('name')} - Model: {step.get('default_model')}")
```

---

## Performance Considerations

### Database Load
- Each job start/complete = 3-4 database queries
- Queue log grows over time

**Optimization:**
```sql
-- Clean up old queue logs (older than 7 days)
DELETE FROM job_queue_log 
WHERE created_at < NOW() - INTERVAL '7 days';

-- Add this to a daily cron job
```

### Concurrency
- Global state uses row-level locking (safe for multiple workers)
- Coordinator uses threading locks for in-memory cache

### Scalability
- Current design: 1 job at a time (serialized, safe)
- Trade-off: Lower throughput, but 100% prevents resource collisions
- Future: Can implement multi-job tracking if needed (requires schema change)

---

## Logging Best Practices

### Enable Coordinator Logging
```python
# In your main app startup
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('job_coordinator')
logger.setLevel(logging.DEBUG)  # For detailed logs
```

### Monitor Key Metrics
```sql
-- Jobs blocked in last hour
SELECT COUNT(*) as blocked_jobs
FROM job_queue_log
WHERE event_type = 'blocked'
  AND created_at > NOW() - INTERVAL '1 hour';

-- Average queue time
SELECT AVG(EXTRACT(EPOCH FROM (started_at - queued_at))) as avg_queue_seconds
FROM jobs
WHERE queued_at IS NOT NULL
  AND started_at IS NOT NULL;
```

---

## Next Steps

1. ✅ Apply database migration (see `README_JOB_QUEUE_COORDINATION.md`)
2. ✅ Restart job worker and backend
3. ✅ Test with sample jobs (see Test 1-3 above)
4. ✅ Monitor queue logs for conflicts
5. ⚠️ Consider adding `/queue/status` endpoint
6. ⚠️ Set up log cleanup cron job

---

**Last Updated:** 2026-02-19  
**Status:** Ready for production testing
