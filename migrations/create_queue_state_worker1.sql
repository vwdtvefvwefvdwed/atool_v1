-- =====================================================
-- WORKER1 DATABASE SETUP (gmhpbeqvqpuoctaqgnum.supabase.co)
-- Run this SQL in Worker1 Supabase Dashboard -> SQL Editor
-- =====================================================

-- 1. Add coordination columns to jobs table (if not already present)
DO $$ 
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'jobs' AND column_name = 'required_models'
    ) THEN
        ALTER TABLE jobs ADD COLUMN required_models JSONB;
        COMMENT ON COLUMN jobs.required_models IS 'JSONB array of model names required by this job (e.g., ["motion-2.0-fast"])';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'jobs' AND column_name = 'queue_position'
    ) THEN
        ALTER TABLE jobs ADD COLUMN queue_position INTEGER;
        COMMENT ON COLUMN jobs.queue_position IS 'Position in queue (1 = next to run, NULL = not queued)';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'jobs' AND column_name = 'blocked_by_job_id'
    ) THEN
        ALTER TABLE jobs ADD COLUMN blocked_by_job_id TEXT;
        COMMENT ON COLUMN jobs.blocked_by_job_id IS 'ID of job/workflow blocking this one due to model conflict';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'jobs' AND column_name = 'conflict_reason'
    ) THEN
        ALTER TABLE jobs ADD COLUMN conflict_reason TEXT;
        COMMENT ON COLUMN jobs.conflict_reason IS 'Human-readable explanation of why job is blocked';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'jobs' AND column_name = 'queued_at'
    ) THEN
        ALTER TABLE jobs ADD COLUMN queued_at TIMESTAMP WITH TIME ZONE;
        COMMENT ON COLUMN jobs.queued_at IS 'Timestamp when job entered the queue';
    END IF;
END $$;

-- Indexes for jobs coordination columns
CREATE INDEX IF NOT EXISTS idx_jobs_queue ON jobs(status, queue_position) WHERE status IN ('pending', 'queued');
CREATE INDEX IF NOT EXISTS idx_jobs_blocked ON jobs(blocked_by_job_id) WHERE blocked_by_job_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_jobs_required_models ON jobs USING GIN(required_models) WHERE required_models IS NOT NULL;

-- 2. Create job_queue_state table (global job coordination)
CREATE TABLE IF NOT EXISTS job_queue_state (
    id INTEGER PRIMARY KEY DEFAULT 1,
    active_job_id TEXT,
    active_job_type TEXT,
    active_models JSONB,
    started_at TIMESTAMP WITH TIME ZONE,
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT single_row CHECK (id = 1)
);

-- Initialize with no active job
INSERT INTO job_queue_state (id, active_job_id, active_job_type, active_models)
VALUES (1, NULL, NULL, '[]'::JSONB)
ON CONFLICT (id) DO NOTHING;

-- Create index
CREATE INDEX IF NOT EXISTS idx_queue_state_active ON job_queue_state(active_job_id) WHERE active_job_id IS NOT NULL;

-- Disable RLS for service access
ALTER TABLE job_queue_state DISABLE ROW LEVEL SECURITY;

-- Grant permissions
GRANT ALL ON job_queue_state TO authenticated;
GRANT ALL ON job_queue_state TO service_role;
GRANT ALL ON job_queue_state TO anon;

COMMENT ON TABLE job_queue_state IS 'Global state tracker for job queue coordination - tracks currently running job and models in use';
COMMENT ON COLUMN job_queue_state.active_job_id IS 'ID of currently running job (workflow or normal)';
COMMENT ON COLUMN job_queue_state.active_job_type IS 'Type of active job: "workflow" or "normal"';
COMMENT ON COLUMN job_queue_state.active_models IS 'JSONB array of models currently in use by active job';

-- 3. Create job_queue_log table (audit logging)
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

CREATE INDEX IF NOT EXISTS idx_queue_log_job ON job_queue_log(job_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_queue_log_events ON job_queue_log(event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_queue_log_created ON job_queue_log(created_at DESC);

-- Disable RLS
ALTER TABLE job_queue_log DISABLE ROW LEVEL SECURITY;

-- Grant permissions
GRANT ALL ON job_queue_log TO authenticated;
GRANT ALL ON job_queue_log TO service_role;
GRANT ALL ON job_queue_log TO anon;

COMMENT ON TABLE job_queue_log IS 'Audit trail for job queue events - tracks queued, started, completed, blocked, and conflict events';
COMMENT ON COLUMN job_queue_log.event_type IS 'Event type: "queued", "started", "completed", "blocked", "conflict", "skipped"';
COMMENT ON COLUMN job_queue_log.models IS 'Models required by or in use during this event';
COMMENT ON COLUMN job_queue_log.blocked_by_job_id IS 'ID of job that blocked this job (if event_type is "blocked")';
COMMENT ON COLUMN job_queue_log.conflict_reason IS 'Human-readable reason for conflict or blocking';

-- 4. Reload PostgREST schema cache
NOTIFY pgrst, 'reload schema';

-- 5. Verify setup
SELECT 'job_queue_state table:' AS info;
SELECT * FROM job_queue_state;

SELECT 'job_queue_log table:' AS info;
SELECT COUNT(*) AS total_log_entries FROM job_queue_log;

SELECT 'jobs coordination columns:' AS info;
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name = 'jobs' 
AND column_name IN ('required_models', 'queue_position', 'blocked_by_job_id', 'conflict_reason', 'queued_at')
ORDER BY column_name;
