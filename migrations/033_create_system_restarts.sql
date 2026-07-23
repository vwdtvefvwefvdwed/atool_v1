-- ============================================================================
-- 033: system_restarts — durable audit log for the Smart Auto-Restart system
-- (see docs/SMART_RESTART_AND_MONITOR_PLAN.md and backend/smart_restart.py)
--
--  * Completely STANDALONE table: NO foreign keys to any existing table.
--  * Written by smart_restart.py whenever a drain-and-restart is triggered
--    (auto: persistent Realtime 1001 loop / manual: dashboard button).
--  * Read by /monitor/restart-history and used for the cross-restart
--    cooldown + daily restart cap (state survives process restarts).
--  * Safe to run on an existing database: CREATE IF NOT EXISTS only.
-- ============================================================================

create table if not exists public.system_restarts (
  id                bigint generated always as identity primary key,
  service           text not null default 'all',      -- 'all' | 'backend' | 'worker'
  reason            text not null,                    -- e.g. 'backend realtime 1001 loop (8 errors in 300s)'
  errors_in_window  int,                              -- 1001 count in the detection window
  triggered_by      text not null default 'auto',     -- 'auto' | 'manual'
  triggered_at      timestamptz not null default now()
);

create index if not exists system_restarts_triggered_at_idx
  on public.system_restarts (triggered_at desc);
create index if not exists system_restarts_triggered_by_idx
  on public.system_restarts (triggered_by);

-- Service-role key is used by the backend, so RLS can stay enabled with no
-- public policies (deny-all for anon users).
alter table public.system_restarts enable row level security;
