# Workflow Job Management — SQL Queries

All queries run against the `jobs` and `workflow_executions` tables.

**Schema reference:** `migrations/000_clean_schema_no_coins.sql`

---

## Table Reference

| Table | Key Columns |
|---|---|
| `jobs` | `job_id`, `user_id`, `status`, `job_type`, `model` (stores workflow id), `workflow_metadata`, `error_message` |
| `workflow_executions` | `id`, `job_id`, `workflow_id`, `user_id`, `status`, `error_info`, `checkpoints` |

**Valid status values (jobs):** `pending` · `running` · `completed` · `failed` · `cancelled` · `pending_retry`

**Workflow IDs:**
- `avatar-style-img-to-img`
- `got-style-img-to-img`
- `knight-style-img-to-img`

---

## 1. Fail by Job ID

Fail a single specific workflow job.

```sql
-- Fail job in jobs table
UPDATE jobs
SET
    status        = 'failed',
    error_message = 'Manually marked as failed by admin',
    completed_at  = NOW(),
    updated_at    = NOW()
WHERE
    job_id   = 'YOUR-JOB-ID-HERE'
    AND job_type = 'workflow';

-- Fail corresponding workflow_execution
UPDATE workflow_executions
SET
    status     = 'failed',
    error_info = '{"error_type": "manual", "message": "Manually marked as failed by admin"}'::jsonb,
    updated_at = NOW()
WHERE
    job_id = 'YOUR-JOB-ID-HERE';
```

---

## 2. Fail by User ID

Fail all active workflow jobs for a specific user.

```sql
-- Fail all active workflow jobs for a user in jobs table
UPDATE jobs
SET
    status        = 'failed',
    error_message = 'Manually failed by admin for user',
    completed_at  = NOW(),
    updated_at    = NOW()
WHERE
    user_id  = 'YOUR-USER-ID-HERE'
    AND job_type = 'workflow'
    AND status IN ('pending', 'running', 'pending_retry');

-- Fail corresponding workflow_executions for the user
UPDATE workflow_executions
SET
    status     = 'failed',
    error_info = '{"error_type": "manual", "message": "Manually failed by admin for user"}'::jsonb,
    updated_at = NOW()
WHERE
    user_id = 'YOUR-USER-ID-HERE'
    AND status IN ('pending', 'running', 'pending_retry');
```

---

## 3. Fail by Workflow Name (Specific Workflow)

Fail all active jobs for a specific workflow (e.g. avatar, knight, got).

### Avatar Style
```sql
UPDATE jobs
SET
    status        = 'failed',
    error_message = 'Manually failed by admin — avatar workflow',
    completed_at  = NOW(),
    updated_at    = NOW()
WHERE
    job_type = 'workflow'
    AND model    = 'avatar-style-img-to-img'
    AND status IN ('pending', 'running', 'pending_retry');

UPDATE workflow_executions
SET
    status     = 'failed',
    error_info = '{"error_type": "manual", "message": "Manually failed — avatar workflow"}'::jsonb,
    updated_at = NOW()
WHERE
    workflow_id = 'avatar-style-img-to-img'
    AND status IN ('pending', 'running', 'pending_retry');
```

### Knight Style
```sql
UPDATE jobs
SET
    status        = 'failed',
    error_message = 'Manually failed by admin — knight workflow',
    completed_at  = NOW(),
    updated_at    = NOW()
WHERE
    job_type = 'workflow'
    AND model    = 'knight-style-img-to-img'
    AND status IN ('pending', 'running', 'pending_retry');

UPDATE workflow_executions
SET
    status     = 'failed',
    error_info = '{"error_type": "manual", "message": "Manually failed — knight workflow"}'::jsonb,
    updated_at = NOW()
WHERE
    workflow_id = 'knight-style-img-to-img'
    AND status IN ('pending', 'running', 'pending_retry');
```

### GOT Style
```sql
UPDATE jobs
SET
    status        = 'failed',
    error_message = 'Manually failed by admin — got workflow',
    completed_at  = NOW(),
    updated_at    = NOW()
WHERE
    job_type = 'workflow'
    AND model    = 'got-style-img-to-img'
    AND status IN ('pending', 'running', 'pending_retry');

UPDATE workflow_executions
SET
    status     = 'failed',
    error_info = '{"error_type": "manual", "message": "Manually failed — got workflow"}'::jsonb,
    updated_at = NOW()
WHERE
    workflow_id = 'got-style-img-to-img'
    AND status IN ('pending', 'running', 'pending_retry');
```

---

## 4. Fail ALL Workflow Jobs (All Workflows)

Fail every active workflow job across the entire platform.

```sql
-- Fail all active workflow jobs
UPDATE jobs
SET
    status        = 'failed',
    error_message = 'Manually failed by admin — all workflows',
    completed_at  = NOW(),
    updated_at    = NOW()
WHERE
    job_type = 'workflow'
    AND status IN ('pending', 'running', 'pending_retry');

-- Fail all active workflow executions
UPDATE workflow_executions
SET
    status     = 'failed',
    error_info = '{"error_type": "manual", "message": "Manually failed — all workflows"}'::jsonb,
    updated_at = NOW()
WHERE
    status IN ('pending', 'running', 'pending_retry');
```

---

## 5. Inspection Queries (Read-Only)

Use these before running updates to verify scope.

### View all active workflow jobs
```sql
SELECT
    j.job_id,
    j.user_id,
    j.status,
    j.model          AS workflow_id,
    j.error_message,
    j.created_at,
    j.updated_at,
    we.current_step,
    we.total_steps,
    we.retry_count
FROM jobs j
LEFT JOIN workflow_executions we ON we.job_id = j.job_id
WHERE
    j.job_type = 'workflow'
    AND j.status IN ('pending', 'running', 'pending_retry')
ORDER BY j.created_at DESC;
```

### View jobs for a specific workflow
```sql
SELECT job_id, user_id, status, created_at, updated_at, error_message
FROM jobs
WHERE
    job_type = 'workflow'
    AND model    = 'avatar-style-img-to-img'   -- change workflow id as needed
ORDER BY created_at DESC;
```

### View jobs for a specific user
```sql
SELECT job_id, model AS workflow_id, status, created_at, updated_at, error_message
FROM jobs
WHERE
    user_id  = 'YOUR-USER-ID-HERE'
    AND job_type = 'workflow'
ORDER BY created_at DESC;
```

### Count active workflow jobs per workflow
```sql
SELECT
    model AS workflow_id,
    status,
    COUNT(*) AS job_count
FROM jobs
WHERE job_type = 'workflow'
GROUP BY model, status
ORDER BY model, status;
```

---

## Quick Reference

| Goal | Filter |
|---|---|
| Fail one job | `job_id = 'UUID'` |
| Fail by user | `user_id = 'UUID' AND job_type = 'workflow'` |
| Fail one workflow | `model = 'avatar-style-img-to-img' AND job_type = 'workflow'` |
| Fail all workflows | `job_type = 'workflow'` |
| Active jobs only | add `AND status IN ('pending', 'running', 'pending_retry')` |

> Always run the **Inspection Query** first to verify the rows that will be affected before executing any UPDATE.
