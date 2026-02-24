# Normal Job Management — Supabase SQL Queries

All queries below are for **normal jobs** (`job_type = 'image'` or `job_type = 'video'`).
Run these directly in your **Supabase SQL Editor**.

---

## 1. VIEW JOBS

### View all pending jobs
```sql
SELECT job_id, user_id, status, model, job_type, prompt, created_at,
       metadata->>'priority' AS priority,
       metadata->>'provider_key' AS provider
FROM jobs
WHERE status = 'pending'
  AND job_type IN ('image', 'video')
ORDER BY created_at ASC;
```

### View all running jobs
```sql
SELECT job_id, user_id, status, model, job_type, progress, started_at,
       metadata->>'provider_key' AS provider
FROM jobs
WHERE status = 'running'
  AND job_type IN ('image', 'video')
ORDER BY started_at ASC;
```

### View all failed jobs
```sql
SELECT job_id, user_id, status, model, job_type, error_message, created_at,
       metadata->>'provider_key' AS provider
FROM jobs
WHERE status = 'failed'
  AND job_type IN ('image', 'video')
ORDER BY created_at DESC;
```

### View all jobs for a specific user
```sql
SELECT job_id, status, model, job_type, progress, error_message, created_at,
       metadata->>'priority' AS priority,
       metadata->>'provider_key' AS provider
FROM jobs
WHERE user_id = 'REPLACE_WITH_USER_ID'
  AND job_type IN ('image', 'video')
ORDER BY created_at DESC;
```

### View a specific job by job_id
```sql
SELECT *
FROM jobs
WHERE job_id = 'REPLACE_WITH_JOB_ID';
```

### View jobs by model name
```sql
SELECT job_id, user_id, status, model, job_type, progress, error_message, created_at
FROM jobs
WHERE model = 'REPLACE_WITH_MODEL_NAME'
  AND job_type IN ('image', 'video')
ORDER BY created_at DESC;
```

### View jobs by provider
```sql
SELECT job_id, user_id, status, model, job_type, progress, created_at
FROM jobs
WHERE metadata->>'provider_key' = 'REPLACE_WITH_PROVIDER_NAME'
  AND job_type IN ('image', 'video')
ORDER BY created_at DESC;
```

### View job counts by status
```sql
SELECT status, COUNT(*) AS total
FROM jobs
WHERE job_type IN ('image', 'video')
GROUP BY status
ORDER BY total DESC;
```

### View jobs by priority
```sql
SELECT
    metadata->>'priority' AS priority,
    status,
    COUNT(*) AS total
FROM jobs
WHERE job_type IN ('image', 'video')
GROUP BY priority, status
ORDER BY priority ASC, status ASC;
```

---

## 2. FAIL A SPECIFIC JOB

### Fail a specific job by job_id (with custom error message)
```sql
UPDATE jobs
SET
    status       = 'failed',
    error_message = 'REPLACE_WITH_ERROR_MESSAGE',
    progress     = 0,
    updated_at   = NOW()
WHERE job_id = 'REPLACE_WITH_JOB_ID'
  AND job_type IN ('image', 'video');
```

---

## 3. FAIL JOBS BY MODEL + PROVIDER (specific user)

### Fail all pending/running jobs for a specific model + provider for one user
```sql
UPDATE jobs
SET
    status        = 'failed',
    error_message = 'REPLACE_WITH_ERROR_MESSAGE',
    progress      = 0,
    updated_at    = NOW()
WHERE user_id  = 'REPLACE_WITH_USER_ID'
  AND model    = 'REPLACE_WITH_MODEL_NAME'
  AND metadata->>'provider_key' = 'REPLACE_WITH_PROVIDER_NAME'
  AND status   IN ('pending', 'running')
  AND job_type IN ('image', 'video');
```

### Fail all pending/running jobs for a specific model only (specific user)
```sql
UPDATE jobs
SET
    status        = 'failed',
    error_message = 'REPLACE_WITH_ERROR_MESSAGE',
    progress      = 0,
    updated_at    = NOW()
WHERE user_id  = 'REPLACE_WITH_USER_ID'
  AND model    = 'REPLACE_WITH_MODEL_NAME'
  AND status   IN ('pending', 'running')
  AND job_type IN ('image', 'video');
```

### Fail all pending/running jobs for a specific provider only (specific user)
```sql
UPDATE jobs
SET
    status        = 'failed',
    error_message = 'REPLACE_WITH_ERROR_MESSAGE',
    progress      = 0,
    updated_at    = NOW()
WHERE user_id  = 'REPLACE_WITH_USER_ID'
  AND metadata->>'provider_key' = 'REPLACE_WITH_PROVIDER_NAME'
  AND status   IN ('pending', 'running')
  AND job_type IN ('image', 'video');
```

---

## 4. FAIL JOBS BY MODEL + PROVIDER (ALL USERS)

### Fail all pending/running jobs for a specific model + provider across ALL users
```sql
UPDATE jobs
SET
    status        = 'failed',
    error_message = 'REPLACE_WITH_ERROR_MESSAGE',
    progress      = 0,
    updated_at    = NOW()
WHERE model    = 'REPLACE_WITH_MODEL_NAME'
  AND metadata->>'provider_key' = 'REPLACE_WITH_PROVIDER_NAME'
  AND status   IN ('pending', 'running')
  AND job_type IN ('image', 'video');
```

### Fail all pending/running jobs for a specific model across ALL users
```sql
UPDATE jobs
SET
    status        = 'failed',
    error_message = 'REPLACE_WITH_ERROR_MESSAGE',
    progress      = 0,
    updated_at    = NOW()
WHERE model    = 'REPLACE_WITH_MODEL_NAME'
  AND status   IN ('pending', 'running')
  AND job_type IN ('image', 'video');
```

### Fail all pending/running jobs for a specific provider across ALL users
```sql
UPDATE jobs
SET
    status        = 'failed',
    error_message = 'REPLACE_WITH_ERROR_MESSAGE',
    progress      = 0,
    updated_at    = NOW()
WHERE metadata->>'provider_key' = 'REPLACE_WITH_PROVIDER_NAME'
  AND status   IN ('pending', 'running')
  AND job_type IN ('image', 'video');
```

---

## 5. FAIL ALL JOBS (ALL USERS)

### Fail ALL pending and running jobs across all users
```sql
UPDATE jobs
SET
    status        = 'failed',
    error_message = 'REPLACE_WITH_ERROR_MESSAGE',
    progress      = 0,
    updated_at    = NOW()
WHERE status   IN ('pending', 'running')
  AND job_type IN ('image', 'video');
```

---

## 6. COMPLETE A SPECIFIC JOB

### Mark a specific job as completed
```sql
UPDATE jobs
SET
    status       = 'completed',
    progress     = 100,
    completed_at = NOW(),
    updated_at   = NOW()
WHERE job_id = 'REPLACE_WITH_JOB_ID'
  AND job_type IN ('image', 'video');
```

---

## 7. RESET JOBS TO PENDING

### Reset a specific failed/stuck job back to pending (re-queue it)
```sql
UPDATE jobs
SET
    status        = 'pending',
    error_message = NULL,
    progress      = 0,
    started_at    = NULL,
    updated_at    = NOW()
WHERE job_id = 'REPLACE_WITH_JOB_ID'
  AND job_type IN ('image', 'video');
```

### Reset all failed jobs back to pending (re-queue all failures)
```sql
UPDATE jobs
SET
    status        = 'pending',
    error_message = NULL,
    progress      = 0,
    started_at    = NULL,
    updated_at    = NOW()
WHERE status   = 'failed'
  AND job_type IN ('image', 'video');
```

### Reset all stuck running jobs back to pending
```sql
UPDATE jobs
SET
    status        = 'pending',
    error_message = NULL,
    progress      = 0,
    started_at    = NULL,
    updated_at    = NOW()
WHERE status   = 'running'
  AND job_type IN ('image', 'video');
```

---

## 8. CANCEL JOBS

### Cancel a specific job
```sql
UPDATE jobs
SET
    status     = 'cancelled',
    progress   = 0,
    updated_at = NOW()
WHERE job_id = 'REPLACE_WITH_JOB_ID'
  AND job_type IN ('image', 'video');
```

### Cancel all pending jobs for a specific user
```sql
UPDATE jobs
SET
    status     = 'cancelled',
    progress   = 0,
    updated_at = NOW()
WHERE user_id  = 'REPLACE_WITH_USER_ID'
  AND status   = 'pending'
  AND job_type IN ('image', 'video');
```

---

## 9. DELETE JOBS

### Delete a specific job
```sql
DELETE FROM jobs
WHERE job_id = 'REPLACE_WITH_JOB_ID';
```

### Delete all failed jobs older than 7 days
```sql
DELETE FROM jobs
WHERE status   = 'failed'
  AND job_type IN ('image', 'video')
  AND created_at < NOW() - INTERVAL '7 days';
```

### Delete all jobs for a specific user
```sql
DELETE FROM jobs
WHERE user_id  = 'REPLACE_WITH_USER_ID'
  AND job_type IN ('image', 'video');
```

---

## 10. GENERATION COUNT — RESET

### Reset generation_count to 0 for ALL users
```sql
UPDATE users
SET generation_count = 0;
```

### Reset generation_count to 0 for a specific user
```sql
UPDATE users
SET generation_count = 0
WHERE id = 'REPLACE_WITH_USER_ID';
```

### View current generation_count for all users
```sql
SELECT id, email, generation_count, created_at
FROM users
ORDER BY generation_count DESC;
```

### View top 20 users by generation count
```sql
SELECT id, email, generation_count, created_at
FROM users
ORDER BY generation_count DESC
LIMIT 20;
```

---

## 11. PRIORITY LOCK FLAG

### Check current priority lock state
```sql
SELECT key, value, updated_at
FROM system_flags
WHERE key = 'priority_lock';
```

### Manually enable priority lock (P2/P3 jobs blocked)
```sql
UPDATE system_flags
SET value = TRUE, updated_at = NOW()
WHERE key = 'priority_lock';
```

### Manually disable priority lock (all jobs resume)
```sql
UPDATE system_flags
SET value = FALSE, updated_at = NOW()
WHERE key = 'priority_lock';
```

---

## QUICK REFERENCE — PLACEHOLDER VALUES

| Placeholder | Replace with |
|---|---|
| `REPLACE_WITH_JOB_ID` | UUID from `jobs.job_id` column |
| `REPLACE_WITH_USER_ID` | UUID from `users.id` column |
| `REPLACE_WITH_MODEL_NAME` | e.g. `flux1-krea-dev.safetensors`, `motion-2.0-fast` |
| `REPLACE_WITH_PROVIDER_NAME` | e.g. `vision-nova`, `cinematic-nova`, `fal-ai` |
| `REPLACE_WITH_ERROR_MESSAGE` | e.g. `Manually failed by admin - provider unavailable` |
