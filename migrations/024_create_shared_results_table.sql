-- =====================================================
-- Table: shared_results
-- Purpose: Store shared generation results for viral growth
-- Date: 2026-01-16
-- =====================================================

-- Create the shared_results table
CREATE TABLE IF NOT EXISTS shared_results (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    share_id TEXT UNIQUE NOT NULL,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    job_id UUID REFERENCES jobs(job_id) ON DELETE SET NULL,
    
    -- Result data
    prompt TEXT NOT NULL,
    image_url TEXT,
    video_url TEXT,
    job_type TEXT NOT NULL CHECK (job_type IN ('image', 'video')),
    
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
$$ LANGUAGE plpgsql;

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
$$ LANGUAGE plpgsql;

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
$$ LANGUAGE plpgsql;

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
$$ LANGUAGE plpgsql;

-- Trigger to auto-update updated_at timestamp
CREATE OR REPLACE FUNCTION update_shared_results_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

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

-- Grant permissions
GRANT SELECT ON shared_results TO anon, authenticated;
GRANT INSERT, UPDATE, DELETE ON shared_results TO authenticated;
GRANT EXECUTE ON FUNCTION generate_share_id() TO authenticated;
GRANT EXECUTE ON FUNCTION increment_share_view(TEXT) TO anon, authenticated;
GRANT EXECUTE ON FUNCTION increment_share_click(TEXT) TO anon, authenticated;
GRANT EXECUTE ON FUNCTION increment_share_conversion(TEXT) TO authenticated;

-- =====================================================
-- Completed!
-- =====================================================
