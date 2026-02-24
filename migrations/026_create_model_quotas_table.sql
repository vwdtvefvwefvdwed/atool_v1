-- =====================================================
-- Migration: 026_create_model_quotas_table
-- Purpose: Model quota tracking system with realtime updates
-- Allows limiting generation count per model/provider combination
-- Date: 2026-01-21
-- =====================================================

-- =====================================================
-- Table: model_quotas
-- Tracks usage quotas for specific model/provider combinations
-- =====================================================
CREATE TABLE IF NOT EXISTS model_quotas (
    id SERIAL PRIMARY KEY,
    provider_name TEXT NOT NULL,
    model_name TEXT NOT NULL,
    quota_limit INTEGER NOT NULL,
    quota_used INTEGER DEFAULT 0,
    reset_period TEXT DEFAULT 'daily',
    last_reset TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    enabled BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    CONSTRAINT unique_provider_model UNIQUE(provider_name, model_name),
    CONSTRAINT quota_used_non_negative CHECK (quota_used >= 0),
    CONSTRAINT quota_limit_positive CHECK (quota_limit > 0),
    CONSTRAINT valid_reset_period CHECK (reset_period IN ('daily', 'monthly', 'weekly', 'never'))
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_model_quotas_lookup ON model_quotas(provider_name, model_name, enabled);
CREATE INDEX IF NOT EXISTS idx_model_quotas_enabled ON model_quotas(enabled) WHERE enabled = true;
CREATE INDEX IF NOT EXISTS idx_model_quotas_reset ON model_quotas(reset_period, last_reset);

-- Comments
COMMENT ON TABLE model_quotas IS 'Tracks usage quotas for model/provider combinations with realtime updates';
COMMENT ON COLUMN model_quotas.provider_name IS 'Provider name (e.g., cinematic-pro, vision-atlas)';
COMMENT ON COLUMN model_quotas.model_name IS 'Model name (e.g., kling-2.6, flux-pro)';
COMMENT ON COLUMN model_quotas.quota_limit IS 'Maximum allowed generations';
COMMENT ON COLUMN model_quotas.quota_used IS 'Current usage count';
COMMENT ON COLUMN model_quotas.reset_period IS 'How often quota resets: daily, weekly, monthly, never';
COMMENT ON COLUMN model_quotas.last_reset IS 'When quota was last reset';
COMMENT ON COLUMN model_quotas.enabled IS 'Whether quota enforcement is active';

-- =====================================================
-- Function: increment_quota
-- Atomically increments quota if limit not exceeded
-- =====================================================
CREATE OR REPLACE FUNCTION increment_quota(
    p_provider TEXT,
    p_model TEXT
) RETURNS JSON AS $$
DECLARE
    v_result JSON;
BEGIN
    UPDATE model_quotas
    SET quota_used = quota_used + 1,
        updated_at = NOW()
    WHERE provider_name = p_provider 
        AND model_name = p_model
        AND enabled = true
        AND quota_used < quota_limit
    RETURNING json_build_object(
        'success', true,
        'quota_used', quota_used,
        'quota_limit', quota_limit,
        'remaining', quota_limit - quota_used
    ) INTO v_result;
    
    IF v_result IS NULL THEN
        SELECT json_build_object(
            'success', false,
            'reason', CASE 
                WHEN NOT EXISTS (SELECT 1 FROM model_quotas WHERE provider_name = p_provider AND model_name = p_model) THEN 'not_found'
                WHEN EXISTS (SELECT 1 FROM model_quotas WHERE provider_name = p_provider AND model_name = p_model AND enabled = false) THEN 'disabled'
                ELSE 'quota_exceeded'
            END
        ) INTO v_result;
    END IF;
    
    RETURN v_result;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- Function: reset_quota
-- Resets quota_used to 0 for a specific model
-- =====================================================
CREATE OR REPLACE FUNCTION reset_quota(
    p_provider TEXT,
    p_model TEXT
) RETURNS BOOLEAN AS $$
BEGIN
    UPDATE model_quotas
    SET quota_used = 0,
        last_reset = NOW(),
        updated_at = NOW()
    WHERE provider_name = p_provider 
        AND model_name = p_model;
    
    RETURN FOUND;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- Function: reset_all_quotas
-- Resets all quotas based on reset_period
-- =====================================================
CREATE OR REPLACE FUNCTION reset_all_quotas(
    p_period TEXT DEFAULT 'daily'
) RETURNS INTEGER AS $$
DECLARE
    v_count INTEGER;
BEGIN
    UPDATE model_quotas
    SET quota_used = 0,
        last_reset = NOW(),
        updated_at = NOW()
    WHERE reset_period = p_period
        AND enabled = true;
    
    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- Function: check_quota_available
-- Check if quota is available without incrementing
-- =====================================================
CREATE OR REPLACE FUNCTION check_quota_available(
    p_provider TEXT,
    p_model TEXT
) RETURNS JSON AS $$
DECLARE
    v_quota RECORD;
BEGIN
    SELECT 
        quota_used,
        quota_limit,
        enabled
    INTO v_quota
    FROM model_quotas
    WHERE provider_name = p_provider 
        AND model_name = p_model;
    
    IF NOT FOUND THEN
        RETURN json_build_object(
            'exists', false,
            'available', true
        );
    END IF;
    
    RETURN json_build_object(
        'exists', true,
        'available', v_quota.enabled AND v_quota.quota_used < v_quota.quota_limit,
        'quota_used', v_quota.quota_used,
        'quota_limit', v_quota.quota_limit,
        'enabled', v_quota.enabled
    );
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- Enable Realtime for model_quotas table
-- =====================================================
ALTER PUBLICATION supabase_realtime ADD TABLE model_quotas;

-- =====================================================
-- Row Level Security (RLS)
-- Disable RLS for service role operations
-- =====================================================
ALTER TABLE model_quotas ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role full access" ON model_quotas 
    USING (true)
    WITH CHECK (true);

-- =====================================================
-- Trigger: Update updated_at on changes
-- =====================================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_model_quotas_updated_at 
    BEFORE UPDATE ON model_quotas
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- =====================================================
-- End of Migration
-- =====================================================
