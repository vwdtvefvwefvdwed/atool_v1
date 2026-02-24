-- Fix: Add generate_share_id function for share feature

-- Drop existing function if it exists
DROP FUNCTION IF EXISTS generate_share_id();

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

-- Grant execute permission to authenticated users
GRANT EXECUTE ON FUNCTION generate_share_id() TO authenticated;
GRANT EXECUTE ON FUNCTION generate_share_id() TO anon;

-- Verify function exists
SELECT generate_share_id() as test_share_id;
