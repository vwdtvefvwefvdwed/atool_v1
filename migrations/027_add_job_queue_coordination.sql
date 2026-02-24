-- =====================================================
-- Migration 027: Add Job Queue Coordination System
-- Purpose: Add model-based job scheduling to prevent resource collapse
-- Date: 2026-02-19
-- =====================================================
-- This migration adds support for coordinating workflow and normal jobs
-- based on model usage to prevent conflicts and resource exhaustion
-- =====================================================

BEGIN;

-- =====================================================
-- STEP 1: Add coordination columns to jobs table
-- =====================================================
DO $$ 
BEGIN
    -- Add required_models column (JSONB array of model names)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'jobs' AND column_name = 'required_models'
    ) THEN
        ALTER TABLE jobs ADD COLUMN required_models JSONB;
        COMMENT ON COLUMN jobs.required_models IS 'JSONB array of model names required by this job (e.g., ["motion-2.0-fast"])';
    END IF;
    
    -- Add queue_position column (position in queue)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'jobs' AND column_name = 'queue_position'
    ) THEN
        ALTER TABLE jobs ADD COLUMN queue_position INTEGER;
        COMMENT ON COLUMN jobs.queue_position IS 'Position in queue (1 = next to run, NULL = not queued)';
    END IF;
    
    -- Add blocked_by_job_id column (which job is blocking this one)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'jobs' AND column_name = 'blocked_by_job_id'
    ) THEN
        ALTER TABLE jobs ADD COLUMN blocked_by_job_id TEXT;
        COMMENT ON COLUMN jobs.blocked_by_job_id IS 'ID of job/workflow blocking this one due to model conflict';
    END IF;
    
    -- Add conflict_reason column (human-readable explanation)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'jobs' AND column_name = 'conflict_reason'
    ) THEN
        ALTER TABLE jobs ADD COLUMN conflict_reason TEXT;
        COMMENT ON COLUMN jobs.conflict_reason IS 'Human-readable explanation of why job is blocked (e.g., "Model motion-2.0-fast in use by workflow_123")';
    END IF;
    
    -- Add queued_at column (timestamp when job entered queue)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'jobs' AND column_name = 'queued_at'
    ) THEN
        ALTER TABLE jobs ADD COLUMN queued_at TIMESTAMP WITH TIME ZONE;
        COMMENT ON COLUMN jobs.queued_at IS 'Timestamp when job entered the queue';
    END IF;
END $$;

-- =====================================================
-- STEP 2: Add indexes for jobs table coordination
-- =====================================================
CREATE INDEX IF NOT EXISTS idx_jobs_queue 
    ON jobs(status, queue_position) 
    WHERE status IN ('pending', 'queued');

CREATE INDEX IF NOT EXISTS idx_jobs_blocked 
    ON jobs(blocked_by_job_id) 
    WHERE blocked_by_job_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_jobs_required_models 
    ON jobs USING GIN(required_models) 
    WHERE required_models IS NOT NULL;

-- =====================================================
-- STEP 3: Add coordination columns to workflow_executions table
-- =====================================================
DO $$ 
BEGIN
    -- Add required_models column (all models needed across all steps)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'workflow_executions' AND column_name = 'required_models'
    ) THEN
        ALTER TABLE workflow_executions ADD COLUMN required_models JSONB;
        COMMENT ON COLUMN workflow_executions.required_models IS 'JSONB array of all models needed across all workflow steps';
    END IF;
    
    -- Add current_step_model column (model being used in current step)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'workflow_executions' AND column_name = 'current_step_model'
    ) THEN
        ALTER TABLE workflow_executions ADD COLUMN current_step_model TEXT;
        COMMENT ON COLUMN workflow_executions.current_step_model IS 'Model being used in the current step';
    END IF;
    
    -- Add blocked_by_job_id column (which job is blocking this workflow)
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'workflow_executions' AND column_name = 'blocked_by_job_id'
    ) THEN
        ALTER TABLE workflow_executions ADD COLUMN blocked_by_job_id TEXT;
        COMMENT ON COLUMN workflow_executions.blocked_by_job_id IS 'ID of job blocking this workflow due to model conflict';
    END IF;
END $$;

-- =====================================================
-- STEP 4: Add indexes for workflow_executions coordination
-- =====================================================
CREATE INDEX IF NOT EXISTS idx_workflow_executions_models 
    ON workflow_executions USING GIN(required_models) 
    WHERE status IN ('running', 'pending_retry');

CREATE INDEX IF NOT EXISTS idx_workflow_executions_blocked 
    ON workflow_executions(blocked_by_job_id) 
    WHERE blocked_by_job_id IS NOT NULL;

-- =====================================================
-- STEP 5: Create job_queue_state table (global state tracker)
-- =====================================================
CREATE TABLE IF NOT EXISTS job_queue_state (
    id INTEGER PRIMARY KEY DEFAULT 1,
    active_job_id TEXT,
    active_job_type TEXT,
    active_models JSONB,
    started_at TIMESTAMP WITH TIME ZONE,
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Ensure only one row exists
    CONSTRAINT single_row CHECK (id = 1)
);

-- Initialize with no active job (if table was just created)
INSERT INTO job_queue_state (id, active_job_id, active_job_type, active_models)
VALUES (1, NULL, NULL, '[]'::JSONB)
ON CONFLICT (id) DO NOTHING;

-- Indexes for job_queue_state
CREATE INDEX IF NOT EXISTS idx_queue_state_active 
    ON job_queue_state(active_job_id) 
    WHERE active_job_id IS NOT NULL;

-- Comments for job_queue_state
COMMENT ON TABLE job_queue_state IS 'Global state tracker for job queue coordination - tracks currently running job and models in use';
COMMENT ON COLUMN job_queue_state.active_job_id IS 'ID of currently running job (workflow or normal)';
COMMENT ON COLUMN job_queue_state.active_job_type IS 'Type of active job: "workflow" or "normal"';
COMMENT ON COLUMN job_queue_state.active_models IS 'JSONB array of models currently in use by active job';

-- =====================================================
-- STEP 6: Create job_queue_log table (audit trail)
-- =====================================================
CREATE TABLE IF NOT EXISTS job_queue_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id TEXT NOT NULL,
    job_type TEXT NOT NULL,
    event_type TEXT NOT NULL,
    models JSONB,
    blocked_by_job_id TEXT,
    conflict_reason TEXT,
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for job_queue_log
CREATE INDEX IF NOT EXISTS idx_queue_log_job 
    ON job_queue_log(job_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_queue_log_events 
    ON job_queue_log(event_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_queue_log_created 
    ON job_queue_log(created_at DESC);

-- Comments for job_queue_log
COMMENT ON TABLE job_queue_log IS 'Audit trail for job queue events - tracks queued, started, completed, blocked, and conflict events';
COMMENT ON COLUMN job_queue_log.event_type IS 'Event type: "queued", "started", "completed", "blocked", "conflict", "skipped"';
COMMENT ON COLUMN job_queue_log.models IS 'Models required by or in use during this event';
COMMENT ON COLUMN job_queue_log.blocked_by_job_id IS 'ID of job that blocked this job (if event_type is "blocked")';
COMMENT ON COLUMN job_queue_log.conflict_reason IS 'Human-readable reason for conflict or blocking';

-- =====================================================
-- STEP 7: Verify migration success
-- =====================================================
DO $$
DECLARE
    jobs_cols_count INTEGER;
    workflow_cols_count INTEGER;
    queue_state_exists BOOLEAN;
    queue_log_exists BOOLEAN;
BEGIN
    -- Count new columns in jobs table
    SELECT COUNT(*) INTO jobs_cols_count
    FROM information_schema.columns
    WHERE table_name = 'jobs' 
    AND column_name IN ('required_models', 'queue_position', 'blocked_by_job_id', 'conflict_reason', 'queued_at');
    
    -- Count new columns in workflow_executions table
    SELECT COUNT(*) INTO workflow_cols_count
    FROM information_schema.columns
    WHERE table_name = 'workflow_executions' 
    AND column_name IN ('required_models', 'current_step_model', 'blocked_by_job_id');
    
    -- Check if new tables exist
    SELECT EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_name = 'job_queue_state'
    ) INTO queue_state_exists;
    
    SELECT EXISTS (
        SELECT 1 FROM information_schema.tables 
        WHERE table_name = 'job_queue_log'
    ) INTO queue_log_exists;
    
    -- Verify all changes were applied
    IF jobs_cols_count = 5 AND workflow_cols_count = 3 AND queue_state_exists AND queue_log_exists THEN
        RAISE NOTICE '✅ Migration 027 completed successfully!';
        RAISE NOTICE '   - Added 5 columns to jobs table';
        RAISE NOTICE '   - Added 3 columns to workflow_executions table';
        RAISE NOTICE '   - Created job_queue_state table';
        RAISE NOTICE '   - Created job_queue_log table';
        RAISE NOTICE '   - Created 8 new indexes';
    ELSE
        RAISE WARNING '⚠️  Migration 027 may be incomplete:';
        RAISE WARNING '   - jobs columns: % of 5', jobs_cols_count;
        RAISE WARNING '   - workflow_executions columns: % of 3', workflow_cols_count;
        RAISE WARNING '   - job_queue_state exists: %', queue_state_exists;
        RAISE WARNING '   - job_queue_log exists: %', queue_log_exists;
    END IF;
END $$;

COMMIT;

-- =====================================================
-- Migration 027 Complete
-- =====================================================
-- Next steps:
-- 1. Update job_worker_realtime.py to use job coordinator
-- 2. Update workflow_manager.py to extract required models
-- 3. Implement job_coordinator.py with conflict detection
-- 4. Test with mixed workflow and normal jobs
-- =====================================================
