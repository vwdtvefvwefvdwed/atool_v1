# Job Queue Coordination System - Database Schema

## Overview
This system prevents resource collapse by coordinating workflow and normal jobs based on model usage.

## Files Updated

### ✅ For NEW Supabase Accounts
**File:** `000_clean_schema_no_coins.sql`

This is the **base schema** that includes everything from scratch. Use this when setting up a brand new Supabase project.

**What's included:**
- `jobs` table with coordination columns (`required_models`, `queue_position`, `blocked_by_job_id`, `conflict_reason`, `queued_at`)
- `workflow_executions` table with coordination columns (`required_models`, `current_step_model`, `blocked_by_job_id`)
- `job_queue_state` table (global state tracker)
- `job_queue_log` table (audit trail)
- All indexes and comments

**How to use:**
```bash
# Run the entire schema from scratch
psql -U postgres -d your_database -f 000_clean_schema_no_coins.sql
```

---

### ✅ For EXISTING Supabase Accounts
**File:** `027_add_job_queue_coordination.sql`

This is a **migration file** that adds only the new coordination features to your existing database.

**What it does:**
1. Adds 5 new columns to `jobs` table
2. Adds 3 new columns to `workflow_executions` table
3. Creates `job_queue_state` table
4. Creates `job_queue_log` table
5. Creates 8 new indexes
6. Verifies migration success

**How to use:**

#### Option 1: Supabase Dashboard (Recommended)
1. Go to your Supabase project dashboard
2. Navigate to **SQL Editor**
3. Click **New Query**
4. Copy and paste the contents of `027_add_job_queue_coordination.sql`
5. Click **Run**
6. Check the output for success message:
   ```
   ✅ Migration 027 completed successfully!
      - Added 5 columns to jobs table
      - Added 3 columns to workflow_executions table
      - Created job_queue_state table
      - Created job_queue_log table
      - Created 8 new indexes
   ```

#### Option 2: Command Line (psql)
```bash
# Connect to your Supabase database
psql "postgresql://postgres:[YOUR-PASSWORD]@db.[YOUR-PROJECT-REF].supabase.co:5432/postgres"

# Run the migration
\i 027_add_job_queue_coordination.sql

# Or directly:
psql "postgresql://..." -f 027_add_job_queue_coordination.sql
```

#### Option 3: Python Script
```python
from supabase import create_client

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

with open('027_add_job_queue_coordination.sql', 'r') as f:
    migration_sql = f.read()
    
# Execute migration (use service role key)
supabase.rpc('exec_sql', {'query': migration_sql}).execute()
```

---

## Schema Changes Summary

### `jobs` Table - New Columns
| Column | Type | Description |
|--------|------|-------------|
| `required_models` | JSONB | Array of model names (e.g., `["motion-2.0-fast"]`) |
| `queue_position` | INTEGER | Position in queue (1 = next) |
| `blocked_by_job_id` | TEXT | ID of blocking job |
| `conflict_reason` | TEXT | Why job is blocked |
| `queued_at` | TIMESTAMPTZ | When job entered queue |

### `workflow_executions` Table - New Columns
| Column | Type | Description |
|--------|------|-------------|
| `required_models` | JSONB | All models needed for workflow |
| `current_step_model` | TEXT | Model for current step |
| `blocked_by_job_id` | TEXT | ID of blocking job |

### `job_queue_state` Table - NEW
| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Always 1 (single row) |
| `active_job_id` | TEXT | Currently running job |
| `active_job_type` | TEXT | "workflow" or "normal" |
| `active_models` | JSONB | Models in use |
| `started_at` | TIMESTAMPTZ | When job started |
| `last_updated` | TIMESTAMPTZ | Last update time |

### `job_queue_log` Table - NEW
| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Log entry ID |
| `job_id` | TEXT | Job being logged |
| `job_type` | TEXT | "workflow" or "normal" |
| `event_type` | TEXT | "queued", "started", "completed", etc. |
| `models` | JSONB | Models involved |
| `blocked_by_job_id` | TEXT | Blocking job (if any) |
| `conflict_reason` | TEXT | Conflict explanation |
| `metadata` | JSONB | Additional data |
| `created_at` | TIMESTAMPTZ | Event timestamp |

---

## Verification

### Check if migration was applied
```sql
-- Check jobs table columns
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'jobs' 
AND column_name IN ('required_models', 'queue_position', 'blocked_by_job_id', 'conflict_reason', 'queued_at');

-- Check workflow_executions columns
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'workflow_executions' 
AND column_name IN ('required_models', 'current_step_model', 'blocked_by_job_id');

-- Check new tables exist
SELECT tablename FROM pg_tables WHERE tablename IN ('job_queue_state', 'job_queue_log');

-- Check global state
SELECT * FROM job_queue_state;
```

### Expected output:
```
-- jobs columns: 5 rows
-- workflow_executions columns: 3 rows
-- new tables: 2 rows
-- job_queue_state: 1 row with NULL active_job_id
```

---

## Rollback (if needed)

If you need to undo this migration:

```sql
-- Drop new tables
DROP TABLE IF EXISTS job_queue_log CASCADE;
DROP TABLE IF EXISTS job_queue_state CASCADE;

-- Remove columns from workflow_executions
ALTER TABLE workflow_executions 
    DROP COLUMN IF EXISTS required_models,
    DROP COLUMN IF EXISTS current_step_model,
    DROP COLUMN IF EXISTS blocked_by_job_id;

-- Remove columns from jobs
ALTER TABLE jobs 
    DROP COLUMN IF EXISTS required_models,
    DROP COLUMN IF EXISTS queue_position,
    DROP COLUMN IF EXISTS blocked_by_job_id,
    DROP COLUMN IF EXISTS conflict_reason,
    DROP COLUMN IF EXISTS queued_at;
```

---

## Next Steps After Migration

1. **Update Python code:**
   - Implement `job_coordinator.py`
   - Update `job_worker_realtime.py`
   - Update `workflow_manager.py`

2. **Test the system:**
   - Create a normal job
   - Create a workflow job
   - Verify conflict detection
   - Check queue logs

3. **Monitor:**
   - Watch `job_queue_log` for events
   - Check `job_queue_state` for active jobs
   - Verify `required_models` is populated

---

## Troubleshooting

### Migration fails with "column already exists"
- This is safe to ignore - the migration uses `IF NOT EXISTS` checks
- The column was likely added in a previous run

### Migration fails with "relation already exists"
- Tables may already exist from a previous attempt
- Run the verification queries to check current state

### No success message appears
- Check for errors in the output
- Verify you have sufficient permissions (use service role key)
- Ensure you're connected to the correct database

### How to check migration status manually
```sql
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'jobs' AND column_name = 'required_models'
    ) THEN
        RAISE NOTICE 'Migration 027 appears to be applied';
    ELSE
        RAISE NOTICE 'Migration 027 not yet applied';
    END IF;
END $$;
```

---

## Support

If you encounter issues:
1. Check the Supabase logs for detailed error messages
2. Verify your Supabase service role key has admin permissions
3. Ensure you're not running this on a production database without backup
4. Test on a development/staging database first

---

**Last Updated:** 2026-02-19  
**Migration Version:** 027  
**Status:** Ready for deployment
