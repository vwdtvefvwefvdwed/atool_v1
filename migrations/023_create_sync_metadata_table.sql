-- Migration: 023_create_sync_metadata_table.sql
-- Description: Create sync_metadata table for dual-account sync system
-- Purpose: Track sync operations between OLD and NEW Supabase accounts
-- Created: 2026-01-13
-- Run on: NEW Supabase account (migration target)

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

-- =====================================================
-- Indexes
-- =====================================================
CREATE INDEX IF NOT EXISTS idx_sync_metadata_type ON sync_metadata(sync_type);
CREATE INDEX IF NOT EXISTS idx_sync_metadata_timestamp ON sync_metadata(last_sync_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_sync_metadata_status ON sync_metadata(sync_status);
CREATE INDEX IF NOT EXISTS idx_sync_metadata_created ON sync_metadata(created_at DESC);

-- =====================================================
-- Table and Column Comments
-- =====================================================
COMMENT ON TABLE sync_metadata IS 'Tracks sync operations between OLD and NEW Supabase accounts for dual-account migration';
COMMENT ON COLUMN sync_metadata.id IS 'Unique identifier for each sync operation';
COMMENT ON COLUMN sync_metadata.sync_type IS 'Type of sync operation: hourly, manual, full, etc.';
COMMENT ON COLUMN sync_metadata.last_sync_timestamp IS 'Timestamp of last successful sync - used as checkpoint for incremental sync';
COMMENT ON COLUMN sync_metadata.sync_status IS 'Current status of sync operation: in_progress, completed, failed';
COMMENT ON COLUMN sync_metadata.records_synced IS 'JSON object tracking number of records synced per table (e.g., {"users": 10, "jobs": 50})';
COMMENT ON COLUMN sync_metadata.error_message IS 'Error message if sync failed - null if successful';
COMMENT ON COLUMN sync_metadata.created_at IS 'Timestamp when sync operation started';
COMMENT ON COLUMN sync_metadata.updated_at IS 'Timestamp when sync operation last updated';

-- =====================================================
-- Row Level Security (RLS)
-- =====================================================
-- Enable RLS on sync_metadata table
ALTER TABLE sync_metadata ENABLE ROW LEVEL SECURITY;

-- Policy: Allow service role full access (backend sync scripts use service role key)
CREATE POLICY "Service role has full access to sync_metadata"
    ON sync_metadata
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- Policy: Authenticated users can view sync history (read-only for monitoring)
CREATE POLICY "Authenticated users can view sync_metadata"
    ON sync_metadata
    FOR SELECT
    TO authenticated
    USING (true);

-- =====================================================
-- Verification Query
-- =====================================================
-- Run this query after migration to verify table creation:
-- SELECT 
--     table_name, 
--     column_name, 
--     data_type, 
--     is_nullable 
-- FROM information_schema.columns 
-- WHERE table_name = 'sync_metadata' 
-- ORDER BY ordinal_position;

-- =====================================================
-- Initial Baseline Record (Optional)
-- =====================================================
-- Uncomment to insert initial baseline record (24 hours ago)
-- This tells the sync system to start syncing from 24 hours ago
-- INSERT INTO sync_metadata (
--     sync_type,
--     last_sync_timestamp,
--     sync_status,
--     records_synced,
--     error_message
-- ) VALUES (
--     'initial_baseline',
--     NOW() - INTERVAL '24 hours',
--     'completed',
--     '{"baseline": true}'::jsonb,
--     NULL
-- );

-- =====================================================
-- Migration Notes
-- =====================================================
-- 1. Run this migration on NEW Supabase account (migration target)
-- 2. After migration, run: python setup_sync.py
-- 3. Enable sync: Set ENABLE_HOURLY_SYNC=true in .env
-- 4. Monitor sync: python sync_status.py
-- 5. See SYNC_SYSTEM_GUIDE.md for complete migration instructions
