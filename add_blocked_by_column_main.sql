-- =====================================================
-- MAIN DATABASE SETUP (gtgnwrwbcxvasgetfzby.supabase.co)
-- Run this SQL in Main Supabase Dashboard -> SQL Editor
-- =====================================================

-- Add blocked_by_job_id column to jobs table for queue visualization
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS blocked_by_job_id TEXT;

-- Add index for performance
CREATE INDEX IF NOT EXISTS idx_jobs_blocked_by ON jobs(blocked_by_job_id) WHERE blocked_by_job_id IS NOT NULL;

-- Add comment
COMMENT ON COLUMN jobs.blocked_by_job_id IS 'ID of job that is blocking this job from running (queue coordination)';

-- Reload PostgREST schema cache
NOTIFY pgrst, 'reload schema';

-- Verify
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'jobs' AND column_name = 'blocked_by_job_id';
