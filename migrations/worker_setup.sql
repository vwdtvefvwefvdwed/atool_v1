-- =====================================================
-- WORKER SUPABASE SETUP
-- For Worker1, Worker2, Worker3 Supabase accounts
-- Run this SQL in Supabase SQL Editor
-- =====================================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =====================================================
-- Priority Queue Tables
-- =====================================================

-- Priority 1 Queue (Highest Priority)
CREATE TABLE IF NOT EXISTS priority1_queue (
    queue_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL,
    job_id UUID NOT NULL,
    request_payload JSONB NOT NULL,
    processed BOOLEAN DEFAULT FALSE,
    processed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_priority1_unprocessed ON priority1_queue(processed, created_at) WHERE processed = FALSE;
CREATE INDEX IF NOT EXISTS idx_priority1_job_id ON priority1_queue(job_id);

-- Priority 2 Queue (Medium Priority)
CREATE TABLE IF NOT EXISTS priority2_queue (
    queue_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL,
    job_id UUID NOT NULL,
    request_payload JSONB NOT NULL,
    processed BOOLEAN DEFAULT FALSE,
    processed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_priority2_unprocessed ON priority2_queue(processed, created_at) WHERE processed = FALSE;
CREATE INDEX IF NOT EXISTS idx_priority2_job_id ON priority2_queue(job_id);

-- Priority 3 Queue (Lowest Priority)
CREATE TABLE IF NOT EXISTS priority3_queue (
    queue_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL,
    job_id UUID NOT NULL,
    request_payload JSONB NOT NULL,
    processed BOOLEAN DEFAULT FALSE,
    processed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_priority3_unprocessed ON priority3_queue(processed, created_at) WHERE processed = FALSE;
CREATE INDEX IF NOT EXISTS idx_priority3_job_id ON priority3_queue(job_id);

-- =====================================================
-- Row Level Security (RLS) Policies
-- =====================================================

-- Enable RLS
ALTER TABLE priority1_queue ENABLE ROW LEVEL SECURITY;
ALTER TABLE priority2_queue ENABLE ROW LEVEL SECURITY;
ALTER TABLE priority3_queue ENABLE ROW LEVEL SECURITY;

-- Create permissive policies (allow service role full access)
CREATE POLICY "Service role full access" ON priority1_queue FOR ALL USING (true);
CREATE POLICY "Service role full access" ON priority2_queue FOR ALL USING (true);
CREATE POLICY "Service role full access" ON priority3_queue FOR ALL USING (true);

-- =====================================================
-- Optimized Job Fetching Function
-- =====================================================

-- Drop existing function if exists
DROP FUNCTION IF EXISTS get_next_priority_job();

-- Create function to fetch next job from all queues efficiently
CREATE OR REPLACE FUNCTION get_next_priority_job()
RETURNS TABLE(
    queue_id UUID,
    user_id UUID,
    job_id UUID,
    request_payload JSONB,
    created_at TIMESTAMPTZ,
    priority_level INTEGER,
    queue_table TEXT
) AS $$
BEGIN
    -- Single query combining all 3 priority queues
    -- Returns first unprocessed job by priority order
    RETURN QUERY
    SELECT * FROM (
        (
            SELECT 
                p1.queue_id,
                p1.user_id,
                p1.job_id,
                p1.request_payload,
                p1.created_at,
                1 AS priority_level,
                'priority1_queue'::TEXT AS queue_table
            FROM priority1_queue p1
            WHERE p1.processed = false
            ORDER BY p1.created_at ASC
            LIMIT 1
        )
        UNION ALL
        (
            SELECT 
                p2.queue_id,
                p2.user_id,
                p2.job_id,
                p2.request_payload,
                p2.created_at,
                2 AS priority_level,
                'priority2_queue'::TEXT AS queue_table
            FROM priority2_queue p2
            WHERE p2.processed = false
            ORDER BY p2.created_at ASC
            LIMIT 1
        )
        UNION ALL
        (
            SELECT 
                p3.queue_id,
                p3.user_id,
                p3.job_id,
                p3.request_payload,
                p3.created_at,
                3 AS priority_level,
                'priority3_queue'::TEXT AS queue_table
            FROM priority3_queue p3
            WHERE p3.processed = false
            ORDER BY p3.created_at ASC
            LIMIT 1
        )
    ) combined
    ORDER BY priority_level ASC, created_at ASC
    LIMIT 1;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Grant execute permissions
GRANT EXECUTE ON FUNCTION get_next_priority_job() TO authenticated;
GRANT EXECUTE ON FUNCTION get_next_priority_job() TO service_role;

-- Add comment
COMMENT ON FUNCTION get_next_priority_job IS 'Fetches the next unprocessed job from all priority queues in priority order. Used by worker processes.';

-- =====================================================
-- Verification Queries
-- =====================================================

-- Run these to verify setup:
-- 
-- 1. Check tables exist:
-- SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE 'priority%';
--
-- 2. Check function exists:
-- SELECT routine_name FROM information_schema.routines WHERE routine_name = 'get_next_priority_job';
--
-- 3. Test function (should return no rows if queues are empty):
-- SELECT * FROM get_next_priority_job();

-- =====================================================
-- Setup Complete!
-- =====================================================
-- 
-- This worker account is now ready to:
-- 1. Receive job queue entries from the main Supabase account
-- 2. Fetch jobs using get_next_priority_job() function
-- 3. Process jobs and update their status
--
-- Next steps:
-- 1. Copy SUPABASE_URL and SERVICE_ROLE_KEY from Settings > API
-- 2. Add to your .env as WORKER1_SUPABASE_URL and WORKER1_SUPABASE_KEY
-- 3. Repeat for WORKER2 and WORKER3
--
-- ⚠️ IMPORTANT: WORKER1 ONLY - Additional Setup Required
-- =====================================================
-- Worker1 serves as the CENTRAL COORDINATION database.
-- After running this SQL, also run create_queue_state_worker1.sql
-- on Worker1 ONLY (not on Worker2/Worker3).
-- 
-- This adds:
-- - job_queue_state (global job coordination singleton)
-- - job_queue_log (audit logging)
-- =====================================================
