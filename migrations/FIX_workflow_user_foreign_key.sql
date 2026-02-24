-- HOTFIX: Fix workflow_executions foreign key to reference public.users
-- 
-- Problem: workflow_executions.user_id may have wrong constraint or missing
-- This causes foreign key violations when creating workflow executions
--
-- Solution: Drop old constraint and create new one referencing public.users

-- Drop the old foreign key constraint
ALTER TABLE workflow_executions 
  DROP CONSTRAINT IF EXISTS workflow_executions_user_id_fkey;

-- Add new constraint referencing public.users
ALTER TABLE workflow_executions 
  ADD CONSTRAINT workflow_executions_user_id_fkey 
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

-- Verify the fix
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 
    FROM information_schema.table_constraints 
    WHERE constraint_name = 'workflow_executions_user_id_fkey'
    AND table_name = 'workflow_executions'
  ) THEN
    RAISE NOTICE '✅ workflow_executions foreign key updated successfully';
  ELSE
    RAISE WARNING '⚠️  workflow_executions foreign key constraint not found';
  END IF;
END $$;
