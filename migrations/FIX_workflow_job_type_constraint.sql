-- HOTFIX: Update job constraints to support workflow jobs
-- This fixes multiple issues with workflow job creation
-- 
-- Problems:
-- 1. job_type constraint only allowed ('image', 'video') - needed 'workflow'
-- 2. status constraint used 'processing' instead of 'running'
-- 3. shared_results job_type constraint needed 'workflow' support
--
-- Solutions: Update all constraints to match the codebase requirements

-- =====================================================
-- Fix jobs table status constraint
-- =====================================================
ALTER TABLE jobs 
  DROP CONSTRAINT IF EXISTS jobs_status_check;

ALTER TABLE jobs 
  ADD CONSTRAINT jobs_status_check 
  CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled', 'pending_retry'));

-- =====================================================
-- Fix jobs table job_type constraint
-- =====================================================
-- Drop both possible constraint names
ALTER TABLE jobs 
  DROP CONSTRAINT IF EXISTS check_job_type;

ALTER TABLE jobs 
  DROP CONSTRAINT IF EXISTS jobs_job_type_check;

-- Add new constraint with workflow support
ALTER TABLE jobs 
  ADD CONSTRAINT jobs_job_type_check 
  CHECK (job_type IN ('image', 'video', 'workflow'));

-- =====================================================
-- Fix shared_results table
-- =====================================================
ALTER TABLE shared_results 
  DROP CONSTRAINT IF EXISTS shared_results_job_type_check;

-- Add new constraint with workflow support
ALTER TABLE shared_results 
  ADD CONSTRAINT shared_results_job_type_check 
  CHECK (job_type IN ('image', 'video', 'workflow'));

-- =====================================================
-- Verify the fix
-- =====================================================
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 
    FROM information_schema.constraint_column_usage 
    WHERE table_name = 'jobs' 
    AND constraint_name = 'jobs_job_type_check'
  ) THEN
    RAISE NOTICE '✅ jobs table constraint updated successfully';
  ELSE
    RAISE WARNING '⚠️  jobs table constraint not found';
  END IF;
  
  IF EXISTS (
    SELECT 1 
    FROM information_schema.constraint_column_usage 
    WHERE table_name = 'shared_results' 
    AND constraint_name = 'shared_results_job_type_check'
  ) THEN
    RAISE NOTICE '✅ shared_results table constraint updated successfully';
  ELSE
    RAISE WARNING '⚠️  shared_results table constraint not found';
  END IF;
END $$;
