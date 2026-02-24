-- =====================================================
-- Migration: 021_simplify_provider_api_keys
-- Purpose: Simplify provider API keys to a column-based structure
-- Each provider is a column, API keys stored as rows (1,2,3...)
-- Date: 2026-01-06
-- =====================================================

-- Drop old table and related objects
DROP TABLE IF EXISTS provider_api_keys CASCADE;
DROP FUNCTION IF EXISTS get_active_api_key(TEXT);
DROP FUNCTION IF EXISTS update_api_key_usage(UUID);
DROP FUNCTION IF EXISTS update_provider_api_keys_updated_at();

-- =====================================================
-- Table: providers
-- Stores unique provider names
-- =====================================================
CREATE TABLE IF NOT EXISTS providers (
    id SERIAL PRIMARY KEY,
    provider_name TEXT NOT NULL UNIQUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- =====================================================
-- Table: provider_api_keys
-- Simple structure: provider_id + key_number + api_key
-- =====================================================
CREATE TABLE IF NOT EXISTS provider_api_keys (
    id SERIAL PRIMARY KEY,
    provider_id INTEGER NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    key_number INTEGER NOT NULL,
    api_key TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    CONSTRAINT unique_provider_key_number UNIQUE(provider_id, key_number)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_provider_api_keys_provider_id ON provider_api_keys(provider_id);
CREATE INDEX IF NOT EXISTS idx_provider_api_keys_key_number ON provider_api_keys(key_number);

-- Comments
COMMENT ON TABLE providers IS 'Stores unique provider names';
COMMENT ON TABLE provider_api_keys IS 'Stores API keys for each provider, numbered 1,2,3...';
COMMENT ON COLUMN provider_api_keys.key_number IS 'Key number within the provider (1, 2, 3, etc.)';
COMMENT ON COLUMN provider_api_keys.api_key IS 'The actual API key';

-- =====================================================
-- Function: get_api_key
-- Get API key for a provider by key number (default 1)
-- =====================================================
CREATE OR REPLACE FUNCTION get_api_key(p_provider_name TEXT, p_key_number INTEGER DEFAULT 1)
RETURNS TEXT AS $$
DECLARE
    v_api_key TEXT;
BEGIN
    SELECT pak.api_key INTO v_api_key
    FROM provider_api_keys pak
    JOIN providers p ON pak.provider_id = p.id
    WHERE p.provider_name = p_provider_name 
        AND pak.key_number = p_key_number;
    
    RETURN v_api_key;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- Function: get_random_api_key
-- Get a random API key for a provider (for load balancing)
-- =====================================================
CREATE OR REPLACE FUNCTION get_random_api_key(p_provider_name TEXT)
RETURNS TEXT AS $$
DECLARE
    v_api_key TEXT;
BEGIN
    SELECT pak.api_key INTO v_api_key
    FROM provider_api_keys pak
    JOIN providers p ON pak.provider_id = p.id
    WHERE p.provider_name = p_provider_name
    ORDER BY RANDOM()
    LIMIT 1;
    
    RETURN v_api_key;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- Row Level Security (RLS)
-- =====================================================
ALTER TABLE providers ENABLE ROW LEVEL SECURITY;
ALTER TABLE provider_api_keys ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role only" ON providers USING (false);
CREATE POLICY "Service role only" ON provider_api_keys USING (false);

-- =====================================================
-- End of Migration
-- =====================================================
