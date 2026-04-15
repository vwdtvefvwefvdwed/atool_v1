-- ============================================================================
-- Migration 030: Add LISTEN/NOTIFY for Job Worker
-- ============================================================================
-- Purpose: Replace fragile Supabase Realtime WebSocket with robust PostgreSQL
--          LISTEN/NOTIFY for job delivery to the worker service.
-- 
-- This is MUCH more stable on Render because:
--   - Uses raw TCP connection to PostgreSQL (not WebSocket through Phoenix relay)
--   - No load balancer WebSocket timeout issues
--   - No idle connection pruning
--   - Native PostgreSQL protocol
--
-- Impact:
--   - ZERO schema changes (no new tables, no new columns)
--   - Adds 1 trigger function (stored procedure)
--   - Adds 1 trigger on existing `jobs` table
--   - Compatible with Supabase free plan (no extra cost)
--   - Does NOT count against API call limits (native PostgreSQL, not HTTP REST)
--
-- IMPORTANT: DATABASE_URL must use port 5432 (Session mode), NOT 6543 (Transaction mode).
-- pgbouncer in transaction mode does NOT support LISTEN/NOTIFY.
--
-- Run this ONCE in the Supabase SQL Editor (Main DB: gtgnwrwbcxvasgetfzby)
-- ============================================================================

-- Step 1: Create the notification function
-- This function fires on every INSERT into jobs where status='pending'
CREATE OR REPLACE FUNCTION notify_job_insert()
RETURNS TRIGGER AS $$
BEGIN
  -- Send a notification on the 'job_events' channel with job details as JSON
  PERFORM pg_notify(
    'job_events',                              -- channel name (worker LISTENs to this)
    json_build_object(                          -- payload sent to worker
      'job_id',     NEW.job_id,
      'status',     NEW.status,
      'job_type',   NEW.job_type,
      'model',      NEW.model,
      'user_id',    NEW.user_id,
      'prompt',     NEW.prompt
    )::text
  );
  
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Step 2: Attach the trigger to the jobs table
-- Fires AFTER INSERT, only when status='pending' (new jobs ready for processing)
CREATE TRIGGER job_insert_notify
  AFTER INSERT ON jobs
  FOR EACH ROW
  WHEN (NEW.status = 'pending')
  EXECUTE FUNCTION notify_job_insert();

-- ============================================================================
-- Verification Queries (run these to confirm the trigger is active)
-- ============================================================================

-- Check that the trigger exists:
-- SELECT trigger_name, event_manipulation, event_object_table 
-- FROM information_schema.triggers 
-- WHERE trigger_name = 'job_insert_notify';
-- Expected: 1 row returned

-- Check that the function exists:
-- SELECT proname, prosrc FROM pg_proc WHERE proname = 'notify_job_insert';
-- Expected: 1 row returned

-- Test the trigger (creates a dummy job and checks if notification fires):
-- You can test by inserting a test job and watching the worker logs for:
--   "[LISTEN/NOTIFY] Received notification for job ..."
-- Then delete the test job:
-- DELETE FROM jobs WHERE job_id = 'TEST-LISTEN-NOTIFY-001';

-- ============================================================================
-- Rollback (run these if you need to remove the trigger)
-- ============================================================================

-- DROP TRIGGER IF EXISTS job_insert_notify ON jobs;
-- DROP FUNCTION IF EXISTS notify_job_insert();
