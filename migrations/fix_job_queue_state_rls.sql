-- Fix job_queue_state table access issues
-- This disables RLS and reloads the PostgREST schema cache

-- 1. Disable RLS on job_queue_state (if enabled)
ALTER TABLE job_queue_state DISABLE ROW LEVEL SECURITY;

-- 2. Grant permissions to authenticated and service_role
GRANT ALL ON job_queue_state TO authenticated;
GRANT ALL ON job_queue_state TO service_role;
GRANT ALL ON job_queue_state TO anon;

-- 3. Reload PostgREST schema cache
NOTIFY pgrst, 'reload schema';

-- 4. Verify table is accessible
SELECT * FROM job_queue_state;
