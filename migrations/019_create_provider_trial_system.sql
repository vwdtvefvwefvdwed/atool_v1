-- =====================================================
-- Migration: 019_create_provider_trial_system
-- Purpose: Create provider trial system for free first-generation per provider
-- Date: 2026-01-05
-- =====================================================

-- =====================================================
-- Table: providers
-- Master list of all AI generation providers
-- =====================================================
CREATE TABLE IF NOT EXISTS providers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    provider_key TEXT UNIQUE NOT NULL,
    provider_name TEXT NOT NULL,
    provider_type TEXT NOT NULL CHECK (provider_type IN ('image', 'video')),
    is_active BOOLEAN DEFAULT true,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for providers
CREATE INDEX IF NOT EXISTS idx_providers_key ON providers(provider_key);
CREATE INDEX IF NOT EXISTS idx_providers_type ON providers(provider_type);
CREATE INDEX IF NOT EXISTS idx_providers_active ON providers(is_active) WHERE is_active = true;

-- Comments for providers
COMMENT ON TABLE providers IS 'Master list of all AI generation providers (image/video)';
COMMENT ON COLUMN providers.provider_key IS 'Unique identifier key for the provider (e.g., flux_schnell, runway_gen3)';
COMMENT ON COLUMN providers.provider_name IS 'Display name for the provider';
COMMENT ON COLUMN providers.provider_type IS 'Type of generation: image or video';
COMMENT ON COLUMN providers.is_active IS 'Whether provider is currently available for use';

-- =====================================================
-- Table: user_provider_trials
-- Tracks which users have used their free trial for each provider
-- =====================================================
CREATE TABLE IF NOT EXISTS user_provider_trials (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider_id UUID NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
    job_id UUID REFERENCES jobs(job_id) ON DELETE SET NULL,
    used_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    CONSTRAINT unique_user_provider_trial UNIQUE(user_id, provider_id)
);

-- Indexes for user_provider_trials
CREATE INDEX IF NOT EXISTS idx_user_provider_trials_user ON user_provider_trials(user_id);
CREATE INDEX IF NOT EXISTS idx_user_provider_trials_provider ON user_provider_trials(provider_id);
CREATE INDEX IF NOT EXISTS idx_user_provider_trials_user_provider ON user_provider_trials(user_id, provider_id);

-- Comments for user_provider_trials
COMMENT ON TABLE user_provider_trials IS 'Tracks free trial usage per user per provider. Row exists = trial used.';
COMMENT ON COLUMN user_provider_trials.job_id IS 'Reference to the job created during free trial';
COMMENT ON COLUMN user_provider_trials.used_at IS 'When the free trial was used';

-- =====================================================
-- Function: check_provider_trial_available
-- Check if user has free trial available for a provider
-- =====================================================
CREATE OR REPLACE FUNCTION check_provider_trial_available(
    p_user_id UUID,
    p_provider_key TEXT
)
RETURNS BOOLEAN AS $$
BEGIN
    RETURN NOT EXISTS (
        SELECT 1 
        FROM user_provider_trials upt
        JOIN providers p ON upt.provider_id = p.id
        WHERE upt.user_id = p_user_id 
        AND p.provider_key = p_provider_key
        AND p.is_active = true
    );
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- Function: use_provider_trial
-- Mark a provider trial as used for a user
-- =====================================================
CREATE OR REPLACE FUNCTION use_provider_trial(
    p_user_id UUID,
    p_provider_key TEXT,
    p_job_id UUID DEFAULT NULL
)
RETURNS BOOLEAN AS $$
DECLARE
    v_provider_id UUID;
BEGIN
    SELECT id INTO v_provider_id 
    FROM providers 
    WHERE provider_key = p_provider_key AND is_active = true;
    
    IF v_provider_id IS NULL THEN
        RETURN FALSE;
    END IF;
    
    INSERT INTO user_provider_trials (user_id, provider_id, job_id)
    VALUES (p_user_id, v_provider_id, p_job_id)
    ON CONFLICT (user_id, provider_id) DO NOTHING;
    
    RETURN TRUE;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- Function: get_user_provider_trials_status
-- Get all providers with user's trial availability status
-- =====================================================
CREATE OR REPLACE FUNCTION get_user_provider_trials_status(p_user_id UUID)
RETURNS TABLE (
    provider_key TEXT,
    provider_name TEXT,
    provider_type TEXT,
    free_trial_available BOOLEAN
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        p.provider_key,
        p.provider_name,
        p.provider_type,
        (upt.id IS NULL) AS free_trial_available
    FROM providers p
    LEFT JOIN user_provider_trials upt 
        ON p.id = upt.provider_id AND upt.user_id = p_user_id
    WHERE p.is_active = true
    ORDER BY p.provider_type, p.provider_name;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- Trigger: Auto-update updated_at for providers
-- =====================================================
CREATE OR REPLACE FUNCTION update_providers_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_providers_timestamp
BEFORE UPDATE ON providers
FOR EACH ROW
EXECUTE FUNCTION update_providers_updated_at();

-- =====================================================
-- Row Level Security (RLS)
-- =====================================================
ALTER TABLE providers ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_provider_trials ENABLE ROW LEVEL SECURITY;

-- Providers: Everyone can view active providers
CREATE POLICY "Anyone can view active providers" ON providers 
    FOR SELECT USING (is_active = true);

-- User provider trials: Users can only view their own trials
CREATE POLICY "Users can view own trials" ON user_provider_trials 
    FOR SELECT USING (user_id = auth.uid());

CREATE POLICY "Users can insert own trials" ON user_provider_trials 
    FOR INSERT WITH CHECK (user_id = auth.uid());

-- =====================================================
-- Grant Permissions
-- =====================================================
GRANT SELECT ON providers TO anon, authenticated;
GRANT SELECT, INSERT ON user_provider_trials TO authenticated;
GRANT EXECUTE ON FUNCTION check_provider_trial_available(UUID, TEXT) TO authenticated;
GRANT EXECUTE ON FUNCTION use_provider_trial(UUID, TEXT, UUID) TO authenticated;
GRANT EXECUTE ON FUNCTION get_user_provider_trials_status(UUID) TO authenticated;

-- =====================================================
-- Insert initial providers (examples - modify as needed)
-- =====================================================
INSERT INTO providers (provider_key, provider_name, provider_type) VALUES
    ('flux_schnell', 'Flux Schnell', 'image'),
    ('flux_dev', 'Flux Dev', 'image'),
    ('sdxl_turbo', 'SDXL Turbo', 'image'),
    ('stable_diffusion_3', 'Stable Diffusion 3', 'image'),
    ('runway_gen3', 'Runway Gen-3', 'video'),
    ('kling_ai', 'Kling AI', 'video')
ON CONFLICT (provider_key) DO NOTHING;
