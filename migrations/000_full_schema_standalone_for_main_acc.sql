-- =====================================================
-- AI Image/Video Generation Platform - Clean Database Schema
-- Purpose: Complete schema without coin monetization system
-- Date: 2026-01-05
-- =====================================================
-- This file combines all migrations into a single clean schema
-- without coin-related tables, columns, or logic
-- =====================================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- =====================================================
-- CLEANUP: Drop old coin-related tables and objects
-- =====================================================

-- Drop old coin system tables if they exist
DROP TABLE IF EXISTS coin_transactions CASCADE;
DROP TABLE IF EXISTS ad_completions CASCADE;
DROP TABLE IF EXISTS user_coins CASCADE;

-- Drop old coin-related triggers
DROP TRIGGER IF EXISTS trigger_initialize_user_coins ON auth.users;

-- Drop old coin-related functions
DROP FUNCTION IF EXISTS initialize_user_coins();

-- Remove coin-related columns from jobs table if they exist
DO $$ 
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns 
               WHERE table_name = 'jobs' AND column_name = 'coins_cost') THEN
        ALTER TABLE jobs DROP COLUMN coins_cost;
    END IF;
    
    IF EXISTS (SELECT 1 FROM information_schema.columns 
               WHERE table_name = 'jobs' AND column_name = 'coins_deducted_at') THEN
        ALTER TABLE jobs DROP COLUMN coins_deducted_at;
    END IF;
    
    IF EXISTS (SELECT 1 FROM information_schema.columns 
               WHERE table_name = 'jobs' AND column_name = 'requires_coin_check') THEN
        ALTER TABLE jobs DROP COLUMN requires_coin_check;
    END IF;
END $$;

-- =====================================================
-- CORE TABLES
-- =====================================================

-- =====================================================
-- Table: users
-- =====================================================
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email TEXT UNIQUE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_login TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    credits INTEGER DEFAULT 100,
    is_active BOOLEAN DEFAULT TRUE,
    metadata JSONB DEFAULT '{}'::jsonb,
    generation_count INTEGER DEFAULT 0,
    registration_ip INET,
    is_flagged BOOLEAN DEFAULT FALSE
);

-- Indexes for users table
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_generation_count ON users(generation_count);
CREATE INDEX IF NOT EXISTS idx_users_registration_ip ON users(registration_ip);
CREATE INDEX IF NOT EXISTS idx_users_is_flagged ON users(is_flagged) WHERE is_flagged = TRUE;

-- Comments for users table
COMMENT ON COLUMN users.registration_ip IS 'IP address used during account creation/verification';
COMMENT ON COLUMN users.is_flagged IS 'Flagged when IP has 3+ accounts created';

-- =====================================================
-- Table: magic_links
-- =====================================================
CREATE TABLE IF NOT EXISTS magic_links (
    token UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email TEXT NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    used BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    used_at TIMESTAMP WITH TIME ZONE
);

-- Indexes for magic links
CREATE INDEX IF NOT EXISTS idx_magic_links_token ON magic_links(token);
CREATE INDEX IF NOT EXISTS idx_magic_links_expires ON magic_links(expires_at);

-- =====================================================
-- Table: jobs
-- =====================================================
CREATE TABLE IF NOT EXISTS jobs (
    job_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled', 'pending_retry')),
    prompt TEXT NOT NULL,
    model TEXT DEFAULT 'flux1-krea-dev.safetensors',
    aspect_ratio TEXT DEFAULT '1:1',
    image_url TEXT,
    thumbnail_url TEXT,
    video_url TEXT,
    error_message TEXT,
    progress INTEGER DEFAULT 0 CHECK (progress >= 0 AND progress <= 100),
    width INTEGER,
    height INTEGER,
    job_type TEXT DEFAULT 'image' NOT NULL CHECK (job_type IN ('image', 'video', 'workflow')),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb,
    workflow_metadata JSONB,
    
    -- Job Queue Coordination fields (added for model-based scheduling)
    required_models JSONB,
    queue_position INTEGER,
    blocked_by_job_id TEXT,
    conflict_reason TEXT,
    queued_at TIMESTAMP WITH TIME ZONE
);

-- Indexes for jobs table
CREATE INDEX IF NOT EXISTS idx_jobs_user_id ON jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_user_status ON jobs(user_id, status);
CREATE INDEX IF NOT EXISTS idx_jobs_user_created ON jobs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_video_url ON jobs(video_url) WHERE video_url IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_jobs_user_type_status ON jobs(user_id, job_type, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_pending_retry ON jobs(status, updated_at) WHERE status = 'pending_retry';
CREATE INDEX IF NOT EXISTS idx_jobs_workflow ON jobs(job_type, status, created_at DESC) WHERE job_type = 'workflow';

-- Job Queue Coordination indexes
CREATE INDEX IF NOT EXISTS idx_jobs_queue ON jobs(status, queue_position) WHERE status IN ('pending', 'queued');
CREATE INDEX IF NOT EXISTS idx_jobs_blocked ON jobs(blocked_by_job_id) WHERE blocked_by_job_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_jobs_required_models ON jobs USING GIN(required_models) WHERE required_models IS NOT NULL;

-- Comments for jobs table
COMMENT ON COLUMN jobs.video_url IS 'Cloudinary URL for generated video (used for video generation jobs)';
COMMENT ON COLUMN jobs.job_type IS 'Type of generation job: image, video, or workflow';
COMMENT ON COLUMN jobs.workflow_metadata IS 'Metadata for workflow executions (workflow_id, execution context)';
COMMENT ON COLUMN jobs.required_models IS 'JSONB array of model names required by this job (e.g., ["motion-2.0-fast"])';
COMMENT ON COLUMN jobs.queue_position IS 'Position in queue (1 = next to run, NULL = not queued)';
COMMENT ON COLUMN jobs.blocked_by_job_id IS 'ID of job/workflow blocking this one due to model conflict';
COMMENT ON COLUMN jobs.conflict_reason IS 'Human-readable explanation of why job is blocked (e.g., "Model motion-2.0-fast in use by workflow_123")';
COMMENT ON COLUMN jobs.queued_at IS 'Timestamp when job entered the queue';

-- Trigger for jobs updated_at
CREATE OR REPLACE FUNCTION update_jobs_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_jobs_updated_at
    BEFORE UPDATE ON jobs
    FOR EACH ROW
    EXECUTE FUNCTION update_jobs_updated_at();

-- Enable realtime for jobs table
ALTER TABLE jobs REPLICA IDENTITY FULL;

-- =====================================================
-- Table: workflow_executions
-- Purpose: Store workflow execution state and checkpoints for resume capability
-- =====================================================
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
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Job Queue Coordination fields (added for model-based scheduling)
    required_models JSONB,
    current_step_model TEXT,
    blocked_by_job_id TEXT
);

-- Indexes for workflow_executions table
CREATE INDEX IF NOT EXISTS idx_workflow_executions_job_id ON workflow_executions(job_id);
CREATE INDEX IF NOT EXISTS idx_workflow_executions_status ON workflow_executions(status);
CREATE INDEX IF NOT EXISTS idx_workflow_executions_user_id ON workflow_executions(user_id);
CREATE INDEX IF NOT EXISTS idx_workflow_executions_workflow_id ON workflow_executions(workflow_id);
CREATE INDEX IF NOT EXISTS idx_workflow_executions_pending_retry ON workflow_executions(status, updated_at) WHERE status = 'pending_retry';

-- Job Queue Coordination indexes
CREATE INDEX IF NOT EXISTS idx_workflow_executions_models ON workflow_executions USING GIN(required_models) WHERE status IN ('running', 'pending_retry');
CREATE INDEX IF NOT EXISTS idx_workflow_executions_blocked ON workflow_executions(blocked_by_job_id) WHERE blocked_by_job_id IS NOT NULL;

-- Comments for workflow_executions table
COMMENT ON TABLE workflow_executions IS 'Stores workflow execution state and checkpoints for resume capability';
COMMENT ON COLUMN workflow_executions.checkpoints IS 'JSONB object with step outputs: {"0": {"status": "completed", "output": {...}}}';
COMMENT ON COLUMN workflow_executions.error_info IS 'Error details for failed steps: {"error_type": "quota_exceeded", "message": "..."}';
COMMENT ON COLUMN workflow_executions.retry_count IS 'Number of retry attempts for this execution';
COMMENT ON COLUMN workflow_executions.required_models IS 'JSONB array of all models needed across all workflow steps';
COMMENT ON COLUMN workflow_executions.current_step_model IS 'Model being used in the current step';
COMMENT ON COLUMN workflow_executions.blocked_by_job_id IS 'ID of job blocking this workflow due to model conflict';

-- Trigger for workflow_executions updated_at
CREATE OR REPLACE FUNCTION update_workflow_executions_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_workflow_executions_updated_at
    BEFORE UPDATE ON workflow_executions
    FOR EACH ROW
    EXECUTE FUNCTION update_workflow_executions_updated_at();

-- =====================================================
-- Table: sessions
-- =====================================================
CREATE TABLE IF NOT EXISTS sessions (
    session_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token TEXT UNIQUE NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_activity TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    user_agent TEXT,
    ip_address INET
);

-- Indexes for sessions
CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

-- =====================================================
-- Table: usage_logs (for analytics)
-- =====================================================
CREATE TABLE IF NOT EXISTS usage_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    job_id UUID REFERENCES jobs(job_id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    credits_used INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Indexes for usage_logs
CREATE INDEX IF NOT EXISTS idx_usage_logs_user_id ON usage_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_usage_logs_created_at ON usage_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_usage_logs_job_id ON usage_logs(job_id);
CREATE INDEX IF NOT EXISTS idx_usage_logs_user_created ON usage_logs(user_id, created_at DESC);

-- =====================================================
-- JOB QUEUE COORDINATION TABLES
-- =====================================================

-- =====================================================
-- Table: job_queue_state
-- Purpose: Global state tracker for active job and model coordination
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

-- Initialize with no active job
INSERT INTO job_queue_state (id, active_job_id, active_job_type, active_models)
VALUES (1, NULL, NULL, '[]'::JSONB)
ON CONFLICT (id) DO NOTHING;

-- Indexes for job_queue_state
CREATE INDEX IF NOT EXISTS idx_queue_state_active ON job_queue_state(active_job_id) WHERE active_job_id IS NOT NULL;

-- Comments for job_queue_state
COMMENT ON TABLE job_queue_state IS 'Global state tracker for job queue coordination - tracks currently running job and models in use';
COMMENT ON COLUMN job_queue_state.active_job_id IS 'ID of currently running job (workflow or normal)';
COMMENT ON COLUMN job_queue_state.active_job_type IS 'Type of active job: "workflow" or "normal"';
COMMENT ON COLUMN job_queue_state.active_models IS 'JSONB array of models currently in use by active job';

-- =====================================================
-- Table: job_queue_log
-- Purpose: Audit trail for queue events and debugging
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
CREATE INDEX IF NOT EXISTS idx_queue_log_job ON job_queue_log(job_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_queue_log_events ON job_queue_log(event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_queue_log_created ON job_queue_log(created_at DESC);

-- Comments for job_queue_log
COMMENT ON TABLE job_queue_log IS 'Audit trail for job queue events - tracks queued, started, completed, blocked, and conflict events';
COMMENT ON COLUMN job_queue_log.event_type IS 'Event type: "queued", "started", "completed", "blocked", "conflict", "skipped"';
COMMENT ON COLUMN job_queue_log.models IS 'Models required by or in use during this event';
COMMENT ON COLUMN job_queue_log.blocked_by_job_id IS 'ID of job that blocked this job (if event_type is "blocked")';
COMMENT ON COLUMN job_queue_log.conflict_reason IS 'Human-readable reason for conflict or blocking';

-- =====================================================
-- PRIORITY QUEUE TABLES
-- =====================================================

-- =====================================================
-- Table: priority1_queue (for users with ≤10 generations)
-- =====================================================
CREATE TABLE IF NOT EXISTS priority1_queue (
    queue_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id UUID NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    request_payload JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    processed BOOLEAN DEFAULT FALSE,
    processed_at TIMESTAMP WITH TIME ZONE
);

-- Indexes for priority1_queue
CREATE INDEX IF NOT EXISTS idx_priority1_queue_processed ON priority1_queue(processed);
CREATE INDEX IF NOT EXISTS idx_priority1_queue_created ON priority1_queue(created_at ASC) WHERE processed = FALSE;
CREATE INDEX IF NOT EXISTS idx_priority1_queue_job_id ON priority1_queue(job_id);
CREATE INDEX IF NOT EXISTS idx_priority1_queue_user_id ON priority1_queue(user_id);
CREATE INDEX IF NOT EXISTS idx_priority1_queue_user_processed ON priority1_queue(user_id, processed);

-- =====================================================
-- Table: priority2_queue (for users with ≤50 generations)
-- =====================================================
CREATE TABLE IF NOT EXISTS priority2_queue (
    queue_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id UUID NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    request_payload JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    processed BOOLEAN DEFAULT FALSE,
    processed_at TIMESTAMP WITH TIME ZONE
);

-- Indexes for priority2_queue
CREATE INDEX IF NOT EXISTS idx_priority2_queue_processed ON priority2_queue(processed);
CREATE INDEX IF NOT EXISTS idx_priority2_queue_created ON priority2_queue(created_at ASC) WHERE processed = FALSE;
CREATE INDEX IF NOT EXISTS idx_priority2_queue_job_id ON priority2_queue(job_id);
CREATE INDEX IF NOT EXISTS idx_priority2_queue_user_id ON priority2_queue(user_id);
CREATE INDEX IF NOT EXISTS idx_priority2_queue_user_processed ON priority2_queue(user_id, processed);

-- =====================================================
-- Table: priority3_queue (for users with >50 generations)
-- =====================================================
CREATE TABLE IF NOT EXISTS priority3_queue (
    queue_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id UUID NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    request_payload JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    processed BOOLEAN DEFAULT FALSE,
    processed_at TIMESTAMP WITH TIME ZONE
);

-- Indexes for priority3_queue
CREATE INDEX IF NOT EXISTS idx_priority3_queue_processed ON priority3_queue(processed);
CREATE INDEX IF NOT EXISTS idx_priority3_queue_created ON priority3_queue(created_at ASC) WHERE processed = FALSE;
CREATE INDEX IF NOT EXISTS idx_priority3_queue_job_id ON priority3_queue(job_id);
CREATE INDEX IF NOT EXISTS idx_priority3_queue_user_id ON priority3_queue(user_id);
CREATE INDEX IF NOT EXISTS idx_priority3_queue_user_processed ON priority3_queue(user_id, processed);

-- =====================================================
-- AD TRACKING TABLES (NO COIN AWARDS)
-- =====================================================

-- =====================================================
-- Table: ad_sessions (for ad tracking and analytics)
-- =====================================================
CREATE TABLE IF NOT EXISTS ad_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    monetag_click_id TEXT,
    zone_id TEXT,
    ad_type TEXT,
    ip_address TEXT,
    user_agent TEXT,
    status TEXT DEFAULT 'pending',
    monetag_verified BOOLEAN DEFAULT FALSE,
    monetag_revenue DECIMAL(10, 4) DEFAULT 0,
    completed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for ad_sessions
CREATE INDEX IF NOT EXISTS idx_ad_sessions_user_id ON ad_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_ad_sessions_monetag_click_id ON ad_sessions(monetag_click_id);
CREATE INDEX IF NOT EXISTS idx_ad_sessions_status ON ad_sessions(status);
CREATE INDEX IF NOT EXISTS idx_ad_sessions_created_at ON ad_sessions(created_at);

-- Comments for ad_sessions
COMMENT ON TABLE ad_sessions IS 'Tracks ad sessions for analytics and revenue tracking only - no coin awards';
COMMENT ON COLUMN ad_sessions.monetag_verified IS 'Whether Monetag postback confirmed the ad completion';
COMMENT ON COLUMN ad_sessions.monetag_revenue IS 'Revenue generated from this ad session';

-- =====================================================
-- IP ABUSE PREVENTION TABLES
-- =====================================================

-- =====================================================
-- Table: flagged_ips
-- =====================================================
CREATE TABLE IF NOT EXISTS flagged_ips (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ip_address INET UNIQUE NOT NULL,
    account_count INTEGER NOT NULL DEFAULT 3,
    flagged_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    reason TEXT DEFAULT 'Maximum accounts per IP exceeded',
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Indexes for flagged_ips
CREATE INDEX IF NOT EXISTS idx_flagged_ips_ip_address ON flagged_ips(ip_address);
CREATE INDEX IF NOT EXISTS idx_flagged_ips_flagged_at ON flagged_ips(flagged_at DESC);

-- Comments for flagged_ips
COMMENT ON TABLE flagged_ips IS 'Tracks IPs that have exceeded maximum account creation limit';
COMMENT ON COLUMN flagged_ips.ip_address IS 'Blocked IP address';
COMMENT ON COLUMN flagged_ips.account_count IS 'Number of accounts created from this IP';
COMMENT ON COLUMN flagged_ips.reason IS 'Reason for flagging';

-- =====================================================
-- DUAL-ACCOUNT SYNC SYSTEM
-- =====================================================

-- =====================================================
-- Table: sync_metadata
-- =====================================================
CREATE TABLE IF NOT EXISTS sync_metadata (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    sync_type TEXT NOT NULL DEFAULT 'hourly',
    last_sync_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    sync_status TEXT NOT NULL CHECK (sync_status IN ('in_progress', 'completed', 'failed')),
    records_synced JSONB DEFAULT '{}'::jsonb,
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for sync_metadata
CREATE INDEX IF NOT EXISTS idx_sync_metadata_type ON sync_metadata(sync_type);
CREATE INDEX IF NOT EXISTS idx_sync_metadata_timestamp ON sync_metadata(last_sync_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_sync_metadata_status ON sync_metadata(sync_status);
CREATE INDEX IF NOT EXISTS idx_sync_metadata_created ON sync_metadata(created_at DESC);

-- Comments for sync_metadata
COMMENT ON TABLE sync_metadata IS 'Tracks sync operations between OLD and NEW Supabase accounts';
COMMENT ON COLUMN sync_metadata.sync_type IS 'Type of sync: hourly, manual, etc.';
COMMENT ON COLUMN sync_metadata.last_sync_timestamp IS 'Timestamp of last successful sync';
COMMENT ON COLUMN sync_metadata.sync_status IS 'Status: in_progress, completed, failed';
COMMENT ON COLUMN sync_metadata.records_synced IS 'JSON object with table names and record counts';
COMMENT ON COLUMN sync_metadata.error_message IS 'Error message if sync failed';

-- =====================================================
-- MODAL DEPLOYMENT TABLES
-- =====================================================

-- =====================================================
-- Table: modal_deployments
-- =====================================================
CREATE TABLE IF NOT EXISTS modal_deployments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    deployment_number INTEGER UNIQUE NOT NULL,
    image_url TEXT,
    video_url TEXT,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_used_at TIMESTAMP WITH TIME ZONE,
    notes TEXT,
    metadata JSONB,
    
    -- Constraints
    CONSTRAINT chk_image_url_not_empty CHECK (image_url IS NULL OR image_url <> ''),
    CONSTRAINT chk_video_url_not_empty CHECK (video_url IS NULL OR video_url <> ''),
    CONSTRAINT chk_deployment_number_positive CHECK (deployment_number > 0),
    CONSTRAINT chk_at_least_one_url CHECK (image_url IS NOT NULL OR video_url IS NOT NULL)
);

-- Indexes for modal_deployments
CREATE INDEX IF NOT EXISTS idx_modal_deployments_active ON modal_deployments(is_active, created_at);
CREATE INDEX IF NOT EXISTS idx_modal_deployments_number ON modal_deployments(deployment_number);
CREATE INDEX IF NOT EXISTS idx_modal_deployments_created ON modal_deployments(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_modal_deployments_last_used ON modal_deployments(last_used_at DESC NULLS LAST);

-- Comments for modal_deployments
COMMENT ON TABLE modal_deployments IS 'Stores Modal deployment information with paired image and video URLs';
COMMENT ON COLUMN modal_deployments.deployment_number IS 'Sequential deployment number for easy reference';
COMMENT ON COLUMN modal_deployments.image_url IS 'Modal URL for image generation endpoint';
COMMENT ON COLUMN modal_deployments.video_url IS 'Modal URL for video generation endpoint';
COMMENT ON COLUMN modal_deployments.is_active IS 'Whether this deployment is active and should be used';
COMMENT ON COLUMN modal_deployments.last_used_at IS 'Last time this deployment was used for a job';
COMMENT ON COLUMN modal_deployments.metadata IS 'Additional metadata (startup time, models, etc.)';

-- =====================================================
-- Table: modal_endpoints
-- =====================================================
CREATE TABLE IF NOT EXISTS modal_endpoints (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    endpoint_type TEXT NOT NULL CHECK (endpoint_type IN ('image', 'video')),
    url TEXT NOT NULL,
    is_active BOOLEAN DEFAULT true,
    description TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for modal_endpoints
CREATE INDEX IF NOT EXISTS idx_modal_endpoints_type_active ON modal_endpoints(endpoint_type, is_active);

-- Unique constraint: only one active endpoint per type
CREATE UNIQUE INDEX IF NOT EXISTS idx_modal_endpoints_unique_active ON modal_endpoints(endpoint_type) WHERE is_active = true;

-- Comments for modal_endpoints
COMMENT ON TABLE modal_endpoints IS 'Stores Modal endpoint URLs for hybrid image/video routing';
COMMENT ON COLUMN modal_endpoints.endpoint_type IS 'Type of endpoint: image or video';
COMMENT ON COLUMN modal_endpoints.is_active IS 'Whether this endpoint is currently active and should be used';
COMMENT ON COLUMN modal_endpoints.metadata IS 'Additional metadata like supported models, startup time, etc.';

-- =====================================================
-- ROW LEVEL SECURITY (RLS) POLICIES
-- =====================================================

-- Enable RLS on all tables
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE magic_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE priority1_queue ENABLE ROW LEVEL SECURITY;
ALTER TABLE priority2_queue ENABLE ROW LEVEL SECURITY;
ALTER TABLE priority3_queue ENABLE ROW LEVEL SECURITY;
ALTER TABLE ad_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE flagged_ips ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if they exist
DROP POLICY IF EXISTS "Users can view own data" ON users;
DROP POLICY IF EXISTS "Users can update own data" ON users;
DROP POLICY IF EXISTS "Users can read own valid magic links" ON magic_links;
DROP POLICY IF EXISTS "Anonymous can validate magic links" ON magic_links;
DROP POLICY IF EXISTS "Service role has full access to magic links" ON magic_links;
DROP POLICY IF EXISTS "Users can view own jobs" ON jobs;
DROP POLICY IF EXISTS "Users can create own jobs" ON jobs;
DROP POLICY IF EXISTS "Users can update own jobs" ON jobs;
DROP POLICY IF EXISTS "Users can delete own jobs" ON jobs;
DROP POLICY IF EXISTS "Users can view own sessions" ON sessions;
DROP POLICY IF EXISTS "Users can view own usage logs" ON usage_logs;
DROP POLICY IF EXISTS "Users can view own priority1 entries" ON priority1_queue;
DROP POLICY IF EXISTS "Users can view own priority2 entries" ON priority2_queue;
DROP POLICY IF EXISTS "Users can view own priority3 entries" ON priority3_queue;
DROP POLICY IF EXISTS "Users can view own ad sessions" ON ad_sessions;
DROP POLICY IF EXISTS "Service role can manage all ad sessions" ON ad_sessions;

-- Users policies
CREATE POLICY "Users can view own data" ON users FOR SELECT USING (id = (SELECT auth.uid()));
CREATE POLICY "Users can update own data" ON users FOR UPDATE USING (id = (SELECT auth.uid()));

-- Magic links policies
CREATE POLICY "Users can read own valid magic links" ON magic_links
    FOR SELECT TO authenticated
    USING (
        email = (SELECT email FROM auth.users WHERE id = (SELECT auth.uid()))
        AND used = false
        AND expires_at > NOW()
    );

CREATE POLICY "Anonymous can validate magic links" ON magic_links
    FOR SELECT TO anon
    USING (
        used = false
        AND expires_at > NOW()
    );

CREATE POLICY "Service role has full access to magic links" ON magic_links
    FOR ALL TO service_role
    USING (true)
    WITH CHECK (true);

-- Jobs policies
CREATE POLICY "Users can view own jobs" ON jobs FOR SELECT USING (user_id = (SELECT auth.uid()));
CREATE POLICY "Users can create own jobs" ON jobs FOR INSERT WITH CHECK (user_id = (SELECT auth.uid()));
CREATE POLICY "Users can update own jobs" ON jobs FOR UPDATE USING (user_id = (SELECT auth.uid()));
CREATE POLICY "Users can delete own jobs" ON jobs FOR DELETE USING (user_id = (SELECT auth.uid()));

-- Sessions policies
CREATE POLICY "Users can view own sessions" ON sessions FOR SELECT USING (user_id = (SELECT auth.uid()));

-- Usage logs policies
CREATE POLICY "Users can view own usage logs" ON usage_logs FOR SELECT USING (user_id = (SELECT auth.uid()));

-- Priority queue policies
CREATE POLICY "Users can view own priority1 entries" ON priority1_queue FOR SELECT USING (user_id = (SELECT auth.uid()));
CREATE POLICY "Users can view own priority2 entries" ON priority2_queue FOR SELECT USING (user_id = (SELECT auth.uid()));
CREATE POLICY "Users can view own priority3 entries" ON priority3_queue FOR SELECT USING (user_id = (SELECT auth.uid()));

-- Ad sessions policies
CREATE POLICY "Users can view own ad sessions" ON ad_sessions FOR SELECT USING (user_id = (SELECT auth.uid()));
CREATE POLICY "Service role can manage all ad sessions" ON ad_sessions FOR ALL USING (true);

-- Flagged IPs policies (only service role can view/manage)
CREATE POLICY "Service role can manage flagged IPs" ON flagged_ips FOR ALL USING (true);

-- =====================================================
-- FUNCTIONS
-- =====================================================

-- Drop existing triggers first (before dropping functions they depend on)
DROP TRIGGER IF EXISTS trigger_update_session_activity ON sessions;
DROP TRIGGER IF EXISTS trigger_update_modal_deployments_timestamp ON modal_deployments;
DROP TRIGGER IF EXISTS trigger_initialize_user_coins ON auth.users;

-- Now drop existing functions
DROP FUNCTION IF EXISTS cleanup_expired_magic_links();
DROP FUNCTION IF EXISTS cleanup_expired_sessions();
DROP FUNCTION IF EXISTS update_user_last_login(UUID);
DROP FUNCTION IF EXISTS get_user_stats(UUID);
DROP FUNCTION IF EXISTS update_modal_deployments_updated_at() CASCADE;
DROP FUNCTION IF EXISTS update_session_activity() CASCADE;
DROP FUNCTION IF EXISTS initialize_user_coins() CASCADE;

-- Function to clean up expired magic links
CREATE OR REPLACE FUNCTION cleanup_expired_magic_links()
RETURNS void AS $$
BEGIN
    DELETE FROM magic_links 
    WHERE expires_at < NOW() OR (used = TRUE AND created_at < NOW() - INTERVAL '24 hours');
END;
$$ LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public;

-- Function to clean up expired sessions
CREATE OR REPLACE FUNCTION cleanup_expired_sessions()
RETURNS void AS $$
BEGIN
    DELETE FROM sessions WHERE expires_at < NOW();
END;
$$ LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public;

-- Function to update user last login
CREATE OR REPLACE FUNCTION update_user_last_login(user_uuid UUID)
RETURNS void AS $$
BEGIN
    UPDATE users SET last_login = NOW() WHERE id = user_uuid;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public;

-- Function to get user stats
CREATE OR REPLACE FUNCTION get_user_stats(user_uuid UUID)
RETURNS TABLE (
    total_jobs BIGINT,
    completed_jobs BIGINT,
    failed_jobs BIGINT,
    pending_jobs BIGINT,
    total_credits_used BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        COUNT(*)::BIGINT as total_jobs,
        COUNT(*) FILTER (WHERE status = 'completed')::BIGINT as completed_jobs,
        COUNT(*) FILTER (WHERE status = 'failed')::BIGINT as failed_jobs,
        COUNT(*) FILTER (WHERE status IN ('pending', 'running'))::BIGINT as pending_jobs,
        COALESCE(SUM(ul.credits_used), 0)::BIGINT as total_credits_used
    FROM jobs j
    LEFT JOIN usage_logs ul ON j.job_id = ul.job_id
    WHERE j.user_id = user_uuid;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public;

-- =====================================================
-- OPTIMIZATION FUNCTIONS
-- =====================================================
-- These functions reduce Supabase API calls by ~1.18M/month
-- Equivalent to migrations: 003, 004, 005
-- =====================================================

-- =====================================================
-- OPTIMIZATION 1: Atomic Generation Count Increment
-- Reduces: 2 calls → 1 call per job
-- Savings: ~30,000 calls/month
-- =====================================================

CREATE OR REPLACE FUNCTION increment_generation_count(user_uuid UUID)
RETURNS INTEGER AS $$
DECLARE
    new_count INTEGER;
BEGIN
    -- Atomic increment and return new value in one operation
    UPDATE users 
    SET generation_count = COALESCE(generation_count, 0) + 1
    WHERE id = user_uuid
    RETURNING generation_count INTO new_count;
    
    -- Return the new count
    RETURN new_count;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public;

-- Grant permissions
GRANT EXECUTE ON FUNCTION increment_generation_count(UUID) TO authenticated;
GRANT EXECUTE ON FUNCTION increment_generation_count(UUID) TO service_role;

COMMENT ON FUNCTION increment_generation_count IS 'Atomically increments user generation_count by 1 and returns new value. Reduces API calls and prevents race conditions.';

-- =====================================================
-- OPTIMIZATION 2: Batch Priority Queue Query
-- Reduces: 3 calls → 1 call per worker poll
-- Savings: ~1,000,000 calls/month
-- =====================================================

DROP FUNCTION IF EXISTS get_next_priority_job();

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
$$ LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public;

-- Grant permissions
GRANT EXECUTE ON FUNCTION get_next_priority_job() TO authenticated;
GRANT EXECUTE ON FUNCTION get_next_priority_job() TO service_role;

COMMENT ON FUNCTION get_next_priority_job IS 'Optimized function that checks all 3 priority queues in one query. Reduces 3 API calls to 1 per worker poll. Savings: ~34,000 calls/day.';

-- =====================================================
-- OPTIMIZATION 3: Batch Job Creation
-- Reduces: 6 calls → 1 call per image job
-- Savings: ~150,000 calls/month
-- =====================================================

DROP FUNCTION IF EXISTS create_job_batch(UUID, TEXT, TEXT, TEXT, INTEGER);

CREATE OR REPLACE FUNCTION create_job_batch(
    p_user_id UUID,
    p_prompt TEXT,
    p_model TEXT DEFAULT 'flux-dev',
    p_aspect_ratio TEXT DEFAULT '1:1',
    p_generation_threshold INTEGER DEFAULT 10
)
RETURNS JSONB AS $$
DECLARE
    v_user_credits INTEGER;
    v_generation_count INTEGER;
    v_new_generation_count INTEGER;
    v_job_id UUID;
    v_priority_level INTEGER;
    v_queue_table TEXT;
    v_result JSONB;
BEGIN
    -- 1. Get user credits and generation count (SELECT users)
    SELECT credits, generation_count INTO v_user_credits, v_generation_count
    FROM users
    WHERE id = p_user_id;
    
    IF NOT FOUND THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', 'User not found'
        );
    END IF;
    
    -- Check credits (can be disabled via UNLIMITED_MODE in backend)
    -- Backend will handle credit check, we just proceed
    
    -- 2. Increment generation count (was separate RPC call)
    v_new_generation_count := COALESCE(v_generation_count, 0) + 1;
    
    UPDATE users
    SET generation_count = v_new_generation_count
    WHERE id = p_user_id;
    
    -- 3. Determine priority queue FIRST (before creating job)
    IF v_new_generation_count <= 10 THEN
        v_priority_level := 1;
        v_queue_table := 'priority1_queue';
    ELSIF v_new_generation_count <= 50 THEN
        v_priority_level := 2;
        v_queue_table := 'priority2_queue';
    ELSE
        v_priority_level := 3;
        v_queue_table := 'priority3_queue';
    END IF;
    
    -- 4. Create job WITH priority in metadata (for Realtime broadcast)
    INSERT INTO jobs (user_id, prompt, model, aspect_ratio, status, progress, metadata)
    VALUES (p_user_id, p_prompt, p_model, p_aspect_ratio, 'pending', 0, 
            jsonb_build_object('priority', v_priority_level))
    RETURNING job_id INTO v_job_id;
    
    -- 5. Insert into priority queue
    EXECUTE format(
        'INSERT INTO %I (user_id, job_id, request_payload) VALUES ($1, $2, $3)',
        v_queue_table
    ) USING p_user_id, v_job_id, jsonb_build_object(
        'prompt', p_prompt,
        'model', p_model,
        'aspect_ratio', p_aspect_ratio
    );
    
    -- 6. Deduct credit (UPDATE users)
    -- Note: Backend handles UNLIMITED_MODE check
    UPDATE users
    SET credits = credits - 1
    WHERE id = p_user_id;
    
    -- 7. Log usage (INSERT usage_logs)
    INSERT INTO usage_logs (user_id, job_id, credits_used, action)
    VALUES (p_user_id, v_job_id, 1, 'image_generation');
    
    -- Get updated credits
    SELECT credits INTO v_user_credits
    FROM users
    WHERE id = p_user_id;
    
    -- Build success response
    v_result := jsonb_build_object(
        'success', true,
        'job', jsonb_build_object(
            'id', v_job_id,
            'status', 'pending',
            'progress', 0,
            'prompt', p_prompt,
            'model', p_model,
            'aspect_ratio', p_aspect_ratio,
            'priority', v_priority_level,
            'generation_number', v_new_generation_count
        ),
        'credits_remaining', v_user_credits
    );
    
    RETURN v_result;
    
EXCEPTION
    WHEN OTHERS THEN
        RETURN jsonb_build_object(
            'success', false,
            'error', SQLERRM
        );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public;

-- Grant permissions
GRANT EXECUTE ON FUNCTION create_job_batch(UUID, TEXT, TEXT, TEXT, INTEGER) TO authenticated;
GRANT EXECUTE ON FUNCTION create_job_batch(UUID, TEXT, TEXT, TEXT, INTEGER) TO service_role;

COMMENT ON FUNCTION create_job_batch IS 'Optimized function that combines all job creation operations into a single transaction. Reduces 6 API calls to 1. Savings: ~150,000 calls/month.';

-- =====================================================
-- END OPTIMIZATION FUNCTIONS
-- =====================================================

-- Function to auto-update modal_deployments updated_at timestamp
CREATE OR REPLACE FUNCTION update_modal_deployments_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public;

-- =====================================================
-- TRIGGERS
-- =====================================================

-- Triggers were already dropped in FUNCTIONS section above

-- Trigger to auto-update last_activity in sessions
CREATE OR REPLACE FUNCTION update_session_activity()
RETURNS TRIGGER AS $$
BEGIN
    NEW.last_activity = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public;

CREATE TRIGGER trigger_update_session_activity
BEFORE UPDATE ON sessions
FOR EACH ROW
EXECUTE FUNCTION update_session_activity();

-- Trigger to automatically update updated_at for modal_deployments
CREATE TRIGGER trigger_update_modal_deployments_timestamp
BEFORE UPDATE ON modal_deployments
FOR EACH ROW
EXECUTE FUNCTION update_modal_deployments_updated_at();

-- =====================================================
-- SHARED RESULTS TABLE (for viral growth feature)
-- =====================================================

CREATE TABLE IF NOT EXISTS shared_results (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    share_id TEXT UNIQUE NOT NULL,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id UUID REFERENCES jobs(job_id) ON DELETE SET NULL,
    
    -- Result data
    prompt TEXT NOT NULL,
    image_url TEXT,
    video_url TEXT,
    job_type TEXT NOT NULL CHECK (job_type IN ('image', 'video', 'workflow')),
    
    -- Analytics
    view_count INTEGER DEFAULT 0,
    click_count INTEGER DEFAULT 0,
    conversion_count INTEGER DEFAULT 0,
    
    -- Metadata
    is_public BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_viewed_at TIMESTAMP WITH TIME ZONE,
    
    metadata JSONB DEFAULT '{}'::jsonb,
    
    -- Constraints
    CONSTRAINT chk_shared_results_has_url CHECK (image_url IS NOT NULL OR video_url IS NOT NULL)
);

-- Indexes for shared_results
CREATE INDEX IF NOT EXISTS idx_shared_results_share_id ON shared_results(share_id);
CREATE INDEX IF NOT EXISTS idx_shared_results_user_id ON shared_results(user_id);
CREATE INDEX IF NOT EXISTS idx_shared_results_job_id ON shared_results(job_id);
CREATE INDEX IF NOT EXISTS idx_shared_results_created_at ON shared_results(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_shared_results_is_public ON shared_results(is_public) WHERE is_public = TRUE;
CREATE INDEX IF NOT EXISTS idx_shared_results_view_count ON shared_results(view_count DESC);

-- Comments for shared_results
COMMENT ON TABLE shared_results IS 'Stores publicly shared generation results for viral growth and attribution';
COMMENT ON COLUMN shared_results.share_id IS 'Unique short ID for URL (e.g., "abc123")';
COMMENT ON COLUMN shared_results.view_count IS 'Number of times the shared link was viewed';
COMMENT ON COLUMN shared_results.click_count IS 'Number of times "Create Your Own" was clicked';
COMMENT ON COLUMN shared_results.conversion_count IS 'Number of signups attributed to this share';
COMMENT ON COLUMN shared_results.is_public IS 'Whether the share is publicly accessible';

-- Function to generate unique short share ID
CREATE OR REPLACE FUNCTION generate_share_id()
RETURNS TEXT AS $$
DECLARE
    chars TEXT := 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789';
    result TEXT := '';
    i INTEGER;
    random_index INTEGER;
BEGIN
    FOR i IN 1..8 LOOP
        random_index := floor(random() * length(chars) + 1)::INTEGER;
        result := result || substr(chars, random_index, 1);
    END LOOP;
    RETURN result;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public;

-- Function to increment view count
CREATE OR REPLACE FUNCTION increment_share_view(p_share_id TEXT)
RETURNS void AS $$
BEGIN
    UPDATE shared_results 
    SET 
        view_count = view_count + 1,
        last_viewed_at = NOW(),
        updated_at = NOW()
    WHERE share_id = p_share_id AND is_public = TRUE;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public;

-- Function to increment click count
CREATE OR REPLACE FUNCTION increment_share_click(p_share_id TEXT)
RETURNS void AS $$
BEGIN
    UPDATE shared_results 
    SET 
        click_count = click_count + 1,
        updated_at = NOW()
    WHERE share_id = p_share_id AND is_public = TRUE;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public;

-- Function to increment conversion count
CREATE OR REPLACE FUNCTION increment_share_conversion(p_share_id TEXT)
RETURNS void AS $$
BEGIN
    UPDATE shared_results 
    SET 
        conversion_count = conversion_count + 1,
        updated_at = NOW()
    WHERE share_id = p_share_id AND is_public = TRUE;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public;

-- Trigger to auto-update updated_at timestamp
CREATE OR REPLACE FUNCTION update_shared_results_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER
SET search_path = public;

CREATE TRIGGER trigger_update_shared_results_timestamp
BEFORE UPDATE ON shared_results
FOR EACH ROW
EXECUTE FUNCTION update_shared_results_timestamp();

-- Enable Row Level Security
ALTER TABLE shared_results ENABLE ROW LEVEL SECURITY;

-- RLS Policy: Anyone can view public shared results
CREATE POLICY "Public shared results are viewable by anyone" ON shared_results
    FOR SELECT USING (is_public = TRUE);

-- RLS Policy: Users can view their own shared results
CREATE POLICY "Users can view own shared results" ON shared_results
    FOR SELECT USING (user_id = auth.uid());

-- RLS Policy: Users can create their own shared results
CREATE POLICY "Users can create own shared results" ON shared_results
    FOR INSERT WITH CHECK (user_id = auth.uid());

-- RLS Policy: Users can update their own shared results
CREATE POLICY "Users can update own shared results" ON shared_results
    FOR UPDATE USING (user_id = auth.uid());

-- RLS Policy: Users can delete their own shared results
CREATE POLICY "Users can delete own shared results" ON shared_results
    FOR DELETE USING (user_id = auth.uid());

-- =====================================================
-- GRANT PERMISSIONS
-- =====================================================

-- Grant usage on schema
GRANT USAGE ON SCHEMA public TO anon, authenticated;

-- Grant permissions on tables
GRANT SELECT, INSERT, UPDATE, DELETE ON users TO authenticated;
GRANT SELECT, INSERT ON magic_links TO anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON jobs TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON sessions TO authenticated;
GRANT SELECT, INSERT ON usage_logs TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON priority1_queue TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON priority2_queue TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON priority3_queue TO authenticated;
GRANT ALL ON ad_sessions TO service_role;
GRANT SELECT, INSERT, UPDATE ON ad_sessions TO authenticated;
GRANT SELECT ON shared_results TO anon, authenticated;
GRANT INSERT, UPDATE, DELETE ON shared_results TO authenticated;

-- Grant execute on functions
GRANT EXECUTE ON FUNCTION cleanup_expired_magic_links() TO authenticated;
GRANT EXECUTE ON FUNCTION cleanup_expired_sessions() TO authenticated;
GRANT EXECUTE ON FUNCTION update_user_last_login(UUID) TO authenticated;
GRANT EXECUTE ON FUNCTION get_user_stats(UUID) TO authenticated;
GRANT EXECUTE ON FUNCTION generate_share_id() TO authenticated;
GRANT EXECUTE ON FUNCTION increment_share_view(TEXT) TO anon, authenticated;
GRANT EXECUTE ON FUNCTION increment_share_click(TEXT) TO anon, authenticated;
GRANT EXECUTE ON FUNCTION increment_share_conversion(TEXT) TO authenticated;

-- Grant realtime permissions
GRANT SELECT ON jobs TO anon;
GRANT SELECT ON jobs TO authenticated;

-- =====================================================
-- SYSTEM FLAGS TABLE (for priority lock and global toggles)
-- =====================================================

CREATE TABLE IF NOT EXISTS system_flags (
    key TEXT PRIMARY KEY,
    value BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

INSERT INTO system_flags (key, value)
VALUES ('priority_lock', FALSE)
ON CONFLICT (key) DO NOTHING;

ALTER TABLE system_flags REPLICA IDENTITY FULL;

ALTER PUBLICATION supabase_realtime ADD TABLE system_flags;

GRANT SELECT ON system_flags TO anon;
GRANT SELECT ON system_flags TO authenticated;
GRANT ALL ON system_flags TO service_role;

COMMENT ON TABLE system_flags IS 'Global system toggles (e.g. priority_lock to block P2/P3 job processing)';
COMMENT ON COLUMN system_flags.key IS 'Flag name (e.g. priority_lock)';
COMMENT ON COLUMN system_flags.value IS 'true = active/enabled, false = inactive/disabled';
COMMENT ON COLUMN system_flags.updated_at IS 'Last time this flag was changed';

-- =====================================================
-- COMPLETED!
-- =====================================================

-- To verify installation, run:
-- SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name;

-- Expected tables (without coin system):
-- 1. users
-- 2. magic_links
-- 3. jobs
-- 4. sessions
-- 5. usage_logs
-- 6. priority1_queue
-- 7. priority2_queue
-- 8. priority3_queue
-- 9. ad_sessions
-- 10. modal_deployments
-- 11. modal_endpoints
-- 12. shared_results
-- 13. flagged_ips
-- 14. sync_metadata
-- 15. system_flags
