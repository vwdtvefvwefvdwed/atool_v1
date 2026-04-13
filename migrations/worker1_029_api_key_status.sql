-- =====================================================
-- Migration: 029_api_key_status
-- Purpose: Track error state and cooldown for each API key.
-- Date: 2026-04-11
-- =====================================================

-- Create table to hold per-key status and cooldown information.
CREATE TABLE IF NOT EXISTS api_key_status (
    id SERIAL PRIMARY KEY,
    provider_id INTEGER NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    key_number INTEGER NOT NULL,
    -- Error tracking
    last_error_type TEXT,
    last_error_message TEXT,
    last_error_at TIMESTAMPTZ,
    -- Cooldown handling
    cooldown_until TIMESTAMPTZ,
    cooldown_duration_seconds INTEGER,
    -- Metrics
    consecutive_errors INTEGER DEFAULT 0,
    total_errors INTEGER DEFAULT 0,
    last_success_at TIMESTAMPTZ,
    -- Permanent disable flag for keys that are exhausted
    is_permanently_disabled BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT unique_provider_key_status UNIQUE(provider_id, key_number)
);

-- Indexes for fast lookup by provider and key number
CREATE INDEX IF NOT EXISTS idx_api_key_status_provider_key ON api_key_status(provider_id, key_number);
CREATE INDEX IF NOT EXISTS idx_api_key_status_cooldown ON api_key_status(cooldown_until);

-- Trigger function: automatically create a status row whenever a new API key is inserted.
CREATE OR REPLACE FUNCTION auto_create_key_status()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO api_key_status (provider_id, key_number)
    VALUES (NEW.provider_id, NEW.key_number)
    ON CONFLICT (provider_id, key_number) DO NOTHING;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Attach trigger to provider_api_keys after INSERT.
DROP TRIGGER IF EXISTS trigger_create_key_status ON provider_api_keys;
CREATE TRIGGER trigger_create_key_status
AFTER INSERT ON provider_api_keys
FOR EACH ROW
EXECUTE FUNCTION auto_create_key_status();

-- Trigger function: update updated_at timestamp on any change to status row.
CREATE OR REPLACE FUNCTION update_api_key_status_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_update_status_timestamp ON api_key_status;
CREATE TRIGGER trigger_update_status_timestamp
BEFORE UPDATE ON api_key_status
FOR EACH ROW
EXECUTE FUNCTION update_api_key_status_timestamp();

-- =====================================================
-- End of Migration 029_api_key_status
-- =====================================================
