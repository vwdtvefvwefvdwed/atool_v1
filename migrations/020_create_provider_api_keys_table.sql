-- =====================================================
-- Migration: 020_create_provider_api_keys_table
-- Purpose: Create table to store API keys for various providers in Worker1
-- Date: 2026-01-06
-- =====================================================

-- =====================================================
-- Table: provider_api_keys
-- Stores API keys and credentials for AI generation providers
-- =====================================================
CREATE TABLE IF NOT EXISTS provider_api_keys (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    provider_key TEXT NOT NULL,
    provider_name TEXT NOT NULL,
    api_key TEXT NOT NULL,
    api_secret TEXT,
    additional_config JSONB DEFAULT '{}'::jsonb,
    is_active BOOLEAN DEFAULT true,
    priority INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_used_at TIMESTAMP WITH TIME ZONE,
    usage_count INTEGER DEFAULT 0,
    
    CONSTRAINT unique_provider_key_api_key UNIQUE(provider_key, api_key)
);

-- Indexes for provider_api_keys
CREATE INDEX IF NOT EXISTS idx_provider_api_keys_provider ON provider_api_keys(provider_key);
CREATE INDEX IF NOT EXISTS idx_provider_api_keys_active ON provider_api_keys(is_active) WHERE is_active = true;
CREATE INDEX IF NOT EXISTS idx_provider_api_keys_priority ON provider_api_keys(priority DESC);
CREATE INDEX IF NOT EXISTS idx_provider_api_keys_provider_active ON provider_api_keys(provider_key, is_active) WHERE is_active = true;

-- Comments for provider_api_keys
COMMENT ON TABLE provider_api_keys IS 'Stores API keys and credentials for AI generation providers';
COMMENT ON COLUMN provider_api_keys.provider_key IS 'Provider identifier (e.g., flux_schnell, runway_gen3, openai, replicate)';
COMMENT ON COLUMN provider_api_keys.provider_name IS 'Display name for the provider';
COMMENT ON COLUMN provider_api_keys.api_key IS 'API key or token for authentication';
COMMENT ON COLUMN provider_api_keys.api_secret IS 'Optional API secret for providers requiring key+secret auth';
COMMENT ON COLUMN provider_api_keys.additional_config IS 'Additional configuration like endpoints, regions, model versions, etc.';
COMMENT ON COLUMN provider_api_keys.is_active IS 'Whether this API key is currently active and should be used';
COMMENT ON COLUMN provider_api_keys.priority IS 'Priority for key rotation (higher number = higher priority)';
COMMENT ON COLUMN provider_api_keys.last_used_at IS 'Last time this API key was used';
COMMENT ON COLUMN provider_api_keys.usage_count IS 'Number of times this API key has been used';

-- =====================================================
-- Trigger: Auto-update updated_at
-- =====================================================
CREATE OR REPLACE FUNCTION update_provider_api_keys_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_provider_api_keys_timestamp
BEFORE UPDATE ON provider_api_keys
FOR EACH ROW
EXECUTE FUNCTION update_provider_api_keys_updated_at();

-- =====================================================
-- Function: get_active_api_key
-- Get the highest priority active API key for a provider
-- =====================================================
CREATE OR REPLACE FUNCTION get_active_api_key(p_provider_key TEXT)
RETURNS TABLE (
    id UUID,
    api_key TEXT,
    api_secret TEXT,
    additional_config JSONB
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        pak.id,
        pak.api_key,
        pak.api_secret,
        pak.additional_config
    FROM provider_api_keys pak
    WHERE pak.provider_key = p_provider_key 
        AND pak.is_active = true
    ORDER BY pak.priority DESC, pak.usage_count ASC
    LIMIT 1;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- Function: update_api_key_usage
-- Update usage statistics for an API key
-- =====================================================
CREATE OR REPLACE FUNCTION update_api_key_usage(p_api_key_id UUID)
RETURNS VOID AS $$
BEGIN
    UPDATE provider_api_keys
    SET 
        usage_count = usage_count + 1,
        last_used_at = NOW()
    WHERE id = p_api_key_id;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- Row Level Security (RLS)
-- Disabled for service role access only
-- =====================================================
ALTER TABLE provider_api_keys ENABLE ROW LEVEL SECURITY;

-- Only service role can access (backend operations only)
CREATE POLICY "Service role only" ON provider_api_keys 
    USING (false);

-- =====================================================
-- Grant Permissions
-- Grant to service role only (configured in backend)
-- =====================================================
-- No grants to anon or authenticated - service role has full access by default

-- =====================================================
-- End of Migration
-- =====================================================
