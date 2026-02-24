-- =====================================================
-- Migration: 025_create_deleted_api_keys
-- Purpose: Create table to store deleted/invalid API keys
-- Tracks which keys were deleted, when, and why
-- Date: 2026-01-21
-- =====================================================

-- =====================================================
-- Table: deleted_api_keys
-- Stores API keys that were deleted due to errors
-- =====================================================
CREATE TABLE IF NOT EXISTS deleted_api_keys (
    id SERIAL PRIMARY KEY,
    provider_id INTEGER NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    key_number INTEGER NOT NULL,
    api_key TEXT NOT NULL,
    error_message TEXT,
    deleted_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Optional: reference to original key if we keep history
    original_key_id INTEGER
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_deleted_keys_provider_id ON deleted_api_keys(provider_id);
CREATE INDEX IF NOT EXISTS idx_deleted_keys_deleted_at ON deleted_api_keys(deleted_at);
CREATE INDEX IF NOT EXISTS idx_deleted_keys_key_number ON deleted_api_keys(key_number);

-- Comments
COMMENT ON TABLE deleted_api_keys IS 'Stores deleted/invalid API keys with deletion reason';
COMMENT ON COLUMN deleted_api_keys.provider_id IS 'Reference to the provider';
COMMENT ON COLUMN deleted_api_keys.key_number IS 'Original key number (1, 2, 3, etc.)';
COMMENT ON COLUMN deleted_api_keys.api_key IS 'The deleted API key';
COMMENT ON COLUMN deleted_api_keys.error_message IS 'Error message that caused deletion';
COMMENT ON COLUMN deleted_api_keys.deleted_at IS 'When the key was deleted';

-- =====================================================
-- Function: delete_and_archive_api_key
-- Moves API key from provider_api_keys to deleted_api_keys
-- =====================================================
CREATE OR REPLACE FUNCTION delete_and_archive_api_key(
    p_provider_name TEXT,
    p_key_number INTEGER,
    p_error_message TEXT
)
RETURNS BOOLEAN AS $$
DECLARE
    v_provider_id INTEGER;
    v_api_key TEXT;
    v_original_key_id INTEGER;
BEGIN
    -- Get provider ID and key details
    SELECT p.id, pak.api_key, pak.id
    INTO v_provider_id, v_api_key, v_original_key_id
    FROM providers p
    JOIN provider_api_keys pak ON pak.provider_id = p.id
    WHERE p.provider_name = p_provider_name 
        AND pak.key_number = p_key_number;
    
    -- If key not found, return false
    IF v_api_key IS NULL THEN
        RETURN FALSE;
    END IF;
    
    -- Insert into deleted_api_keys
    INSERT INTO deleted_api_keys (
        provider_id,
        key_number,
        api_key,
        error_message,
        original_key_id
    ) VALUES (
        v_provider_id,
        p_key_number,
        v_api_key,
        p_error_message,
        v_original_key_id
    );
    
    -- Delete from provider_api_keys
    DELETE FROM provider_api_keys
    WHERE id = v_original_key_id;
    
    RETURN TRUE;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- Row Level Security (RLS)
-- =====================================================
ALTER TABLE deleted_api_keys ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role only" ON deleted_api_keys USING (false);

-- =====================================================
-- End of Migration
-- =====================================================
