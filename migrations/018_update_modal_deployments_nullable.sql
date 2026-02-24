-- Migration 018: Update modal_deployments table to allow NULL image/video URLs
-- Purpose: Allow storing individual image or video URLs (not requiring both)
-- Date: 2026-01-03
-- Author: Droid

-- NOTE: This migration is OPTIONAL - only needed if migration 014 was applied before the update
-- If migration 014 already contains these constraints, this migration can be skipped

-- Drop the old constraints if they exist (from old migration 014 version)
ALTER TABLE modal_deployments 
DROP CONSTRAINT IF EXISTS chk_image_url_not_empty;

ALTER TABLE modal_deployments 
DROP CONSTRAINT IF EXISTS chk_video_url_not_empty;

-- Update columns to allow NULL (if they're currently NOT NULL)
ALTER TABLE modal_deployments 
ALTER COLUMN image_url DROP NOT NULL;

ALTER TABLE modal_deployments 
ALTER COLUMN video_url DROP NOT NULL;

-- Add new constraints (if they don't already exist)
ALTER TABLE modal_deployments
DROP CONSTRAINT IF EXISTS chk_at_least_one_url;

ALTER TABLE modal_deployments
ADD CONSTRAINT chk_image_url_not_empty CHECK (image_url IS NULL OR image_url <> ''),
ADD CONSTRAINT chk_video_url_not_empty CHECK (video_url IS NULL OR video_url <> ''),
ADD CONSTRAINT chk_at_least_one_url CHECK (image_url IS NOT NULL OR video_url IS NOT NULL);

-- Add comment explaining the change
COMMENT ON COLUMN modal_deployments.image_url IS 'Modal URL for image generation endpoint (nullable - set NULL if not deploying image endpoint)';
COMMENT ON COLUMN modal_deployments.video_url IS 'Modal URL for video generation endpoint (nullable - set NULL if not deploying video endpoint)';

-- Verification query
-- Run this to see your deployments and their URL status:
-- SELECT deployment_number, is_active, 
--        CASE WHEN image_url IS NOT NULL THEN '✅' ELSE '❌' END as has_image,
--        CASE WHEN video_url IS NOT NULL THEN '✅' ELSE '❌' END as has_video,
--        created_at
-- FROM modal_deployments
-- ORDER BY created_at DESC;

-- Migration complete!
-- If you see "constraint already exists" error above, it's OK - the constraint was already added in migration 014

