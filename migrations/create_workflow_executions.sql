-- Migration: Create workflow_executions table
-- Purpose: Store workflow execution state and checkpoints for resume capability
-- Created: 2024-02-16

-- Create workflow_executions table
CREATE TABLE IF NOT EXISTS workflow_executions (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  job_id UUID REFERENCES jobs(job_id) ON DELETE CASCADE,
  workflow_id TEXT NOT NULL,
  user_id UUID REFERENCES users(id) ON DELETE CASCADE,
  current_step INTEGER DEFAULT 0,
  total_steps INTEGER NOT NULL,
  status TEXT CHECK (status IN ('pending', 'running', 'completed', 'failed', 'pending_retry')) DEFAULT 'pending',
  checkpoints JSONB DEFAULT '{}',
  error_info JSONB,
  retry_count INTEGER DEFAULT 0,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create indexes for efficient queries
CREATE INDEX IF NOT EXISTS idx_workflow_executions_job_id ON workflow_executions(job_id);
CREATE INDEX IF NOT EXISTS idx_workflow_executions_status ON workflow_executions(status);
CREATE INDEX IF NOT EXISTS idx_workflow_executions_user_id ON workflow_executions(user_id);
CREATE INDEX IF NOT EXISTS idx_workflow_executions_workflow_id ON workflow_executions(workflow_id);

-- Create index for pending_retry queries
CREATE INDEX IF NOT EXISTS idx_workflow_executions_pending_retry 
  ON workflow_executions(status, updated_at) 
  WHERE status = 'pending_retry';

-- Add updated_at trigger
CREATE OR REPLACE FUNCTION update_workflow_executions_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_workflow_executions_updated_at ON workflow_executions;
CREATE TRIGGER trigger_workflow_executions_updated_at
  BEFORE UPDATE ON workflow_executions
  FOR EACH ROW
  EXECUTE FUNCTION update_workflow_executions_updated_at();

-- Update jobs table to support pending_retry status
ALTER TABLE jobs 
  DROP CONSTRAINT IF EXISTS jobs_status_check;

ALTER TABLE jobs 
  ADD CONSTRAINT jobs_status_check 
  CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled', 'pending_retry'));

-- Update jobs table to support workflow job_type
-- Drop both possible constraint names (from migration 015 and this migration)
ALTER TABLE jobs 
  DROP CONSTRAINT IF EXISTS check_job_type;

ALTER TABLE jobs 
  DROP CONSTRAINT IF EXISTS jobs_job_type_check;

ALTER TABLE jobs 
  ADD CONSTRAINT jobs_job_type_check 
  CHECK (job_type IN ('image', 'video', 'workflow'));

-- Add workflow_metadata column to jobs table
ALTER TABLE jobs 
  ADD COLUMN IF NOT EXISTS workflow_metadata JSONB;

-- Add updated_at column to jobs table
ALTER TABLE jobs 
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW();

-- Add updated_at trigger for jobs table
CREATE OR REPLACE FUNCTION update_jobs_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_jobs_updated_at ON jobs;
CREATE TRIGGER trigger_jobs_updated_at
  BEFORE UPDATE ON jobs
  FOR EACH ROW
  EXECUTE FUNCTION update_jobs_updated_at();

-- Create index for pending_retry jobs
CREATE INDEX IF NOT EXISTS idx_jobs_pending_retry 
  ON jobs(status, updated_at) 
  WHERE status = 'pending_retry';

-- Create index for workflow jobs (optimizes workflow job queries)
CREATE INDEX IF NOT EXISTS idx_jobs_workflow 
  ON jobs(job_type, status, created_at DESC) 
  WHERE job_type = 'workflow';

COMMENT ON TABLE workflow_executions IS 'Stores workflow execution state and checkpoints for resume capability';
COMMENT ON COLUMN workflow_executions.checkpoints IS 'JSONB object with step outputs: {"0": {"status": "completed", "output": {...}}}';
COMMENT ON COLUMN workflow_executions.error_info IS 'Error details for failed steps: {"error_type": "quota_exceeded", "message": "..."}';
COMMENT ON COLUMN workflow_executions.retry_count IS 'Number of retry attempts for this execution';
