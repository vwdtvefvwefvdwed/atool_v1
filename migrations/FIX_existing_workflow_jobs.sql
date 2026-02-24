-- Fix existing workflow jobs that were created with wrong job_type
-- This updates jobs where the prompt starts with "Workflow:" but job_type is 'image'

-- Update existing workflow jobs to have correct job_type
UPDATE jobs 
SET job_type = 'workflow'
WHERE job_type = 'image' 
  AND prompt LIKE 'Workflow:%'
  AND status IN ('pending', 'running');

-- Verify the update
DO $$
DECLARE
  updated_count INTEGER;
BEGIN
  SELECT COUNT(*) INTO updated_count
  FROM jobs 
  WHERE job_type = 'workflow';
  
  RAISE NOTICE '✅ Updated workflow jobs. Total workflow jobs now: %', updated_count;
END $$;
