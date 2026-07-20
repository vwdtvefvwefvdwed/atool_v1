# Fix Queue Coordination Errors

## Problem
Worker shows these errors:
```
[COORDINATOR] Error fetching active job state: Could not find table 'job_queue_state'
[COORDINATOR] Error clearing queue info: Could not find column 'blocked_by_job_id'
[COORDINATOR] Error logging queue event: Could not find table 'job_queue_log'
```

## Solution

### Step 1: Fix Worker1 Database
Run **[create_queue_state_worker1.sql](./create_queue_state_worker1.sql)** in **Worker1** Supabase Dashboard:
- **URL**: https://gmhpbeqvqpuoctaqgnum.supabase.co
- **Location**: Dashboard → SQL Editor
- **Creates**:
  - `job_queue_state` table (required for coordination)
  - `job_queue_log` table (optional audit logging)

### Step 2: Fix Main Database
Run **[add_blocked_by_column_main.sql](./add_blocked_by_column_main.sql)** in **Main** Supabase Dashboard:
- **URL**: https://gtgnwrwbcxvasgetfzby.supabase.co
- **Location**: Dashboard → SQL Editor
- **Adds**:
  - `blocked_by_job_id` column to `jobs` table (optional queue visualization)

### Step 3: Restart Worker
```bash
python job_worker_realtime.py
```

## Result
✅ No more PGRST205 or PGRST204 errors
✅ Jobs process normally
✅ Queue coordination works properly

## Notes
- **Worker1** is the central coordination database
- **Main** database stores the actual jobs table
- Both need to be updated for full functionality
