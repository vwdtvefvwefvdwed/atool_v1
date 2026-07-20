-- workflow_gallery_prompts — fallback delivery channel for the common-workflow
-- per-image prompt map (primary channel: the repo file
-- backend/workflows/common_workflow/gallery_prompts.json).
--
-- Written by scripts/generate-gallery-json.js on every frontend deploy
-- (service-role upsert, single row id=1). Read by
-- workflows/common_workflow/prompt_store.py when the local file is missing or
-- stale (ephemeral disks on Render/Railway).
--
-- SECURITY: RLS is ENABLED with NO policies — only the service_role key can
-- read/write, so prompts stay private (never reachable with the anon key).

create table if not exists public.workflow_gallery_prompts (
    id            integer primary key,
    version       text not null,
    data          jsonb not null,
    generated_at  timestamptz not null default now()
);

alter table public.workflow_gallery_prompts enable row level security;

comment on table public.workflow_gallery_prompts is
  'Deploy-generated per-image prompt map for common-workflow (Gallery Remix). Single row id=1. Service-role only.';
