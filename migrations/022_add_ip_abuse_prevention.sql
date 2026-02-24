-- =====================================================
-- Migration 022: Add IP Abuse Prevention
-- Purpose: Add IP tracking and flagging system
-- Date: 2026-01-13
-- =====================================================

-- Add columns to users table if they don't exist
DO $$ 
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name = 'users' AND column_name = 'registration_ip') THEN
        ALTER TABLE users ADD COLUMN registration_ip INET;
        CREATE INDEX idx_users_registration_ip ON users(registration_ip);
        COMMENT ON COLUMN users.registration_ip IS 'IP address used during account creation/verification';
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name = 'users' AND column_name = 'is_flagged') THEN
        ALTER TABLE users ADD COLUMN is_flagged BOOLEAN DEFAULT FALSE;
        CREATE INDEX idx_users_is_flagged ON users(is_flagged) WHERE is_flagged = TRUE;
        COMMENT ON COLUMN users.is_flagged IS 'Flagged when IP has 3+ accounts created';
    END IF;
END $$;

-- Create flagged_ips table if it doesn't exist
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

-- Enable RLS on flagged_ips
ALTER TABLE flagged_ips ENABLE ROW LEVEL SECURITY;

-- Drop existing policy if exists
DROP POLICY IF EXISTS "Service role can manage flagged IPs" ON flagged_ips;

-- Create RLS policy (only service role can view/manage)
CREATE POLICY "Service role can manage flagged IPs" ON flagged_ips FOR ALL USING (true);

-- Success message
DO $$
BEGIN
    RAISE NOTICE 'Migration 022 completed: IP abuse prevention system added';
END $$;
