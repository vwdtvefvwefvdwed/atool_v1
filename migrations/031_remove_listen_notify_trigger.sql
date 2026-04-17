-- ============================================================================
-- Migration 031: Remove LISTEN/NOTIFY Trigger (Switch to HTTP Push)
-- ============================================================================
-- Purpose: Remove the LISTEN/NOTIFY trigger from jobs table.
--          Job notifications now handled via HTTP push from app.py to worker.
--
-- Why this change:
--   - HTTP push is simpler and more direct
--   - No duplicate processing risk (single notification path)
--   - app.py has direct control over worker notification
--   - Periodic retry (every 10 min) serves as safety net for missed jobs
--
-- Run this ONCE in the Supabase SQL Editor (Main DB: gtgnwrwbcxvasgetfzby)
-- ============================================================================

-- Step 1: Drop the trigger
DROP TRIGGER IF EXISTS job_insert_notify ON jobs;

-- Step 2: Drop the function
DROP FUNCTION IF EXISTS notify_job_insert();

-- ============================================================================
-- Verification
-- ============================================================================

-- Verify trigger is removed (should return 0 rows):
-- SELECT trigger_name FROM information_schema.triggers WHERE trigger_name = 'job_insert_notify';

-- Verify function is removed (should return 0 rows):
-- SELECT proname FROM pg_proc WHERE proname = 'notify_job_insert';

-- ============================================================================
-- What happens after this migration:
-- ============================================================================
-- 1. app.py calls notify_worker(job_id) after job INSERT
-- 2. Worker receives HTTP POST at /worker/process-job
-- 3. If worker is down, job stays pending
-- 4. Periodic retry (every 10 min) catches missed jobs
-- 5. Worker restart backlog processing catches running jobs from crashes
-- ============================================================================
