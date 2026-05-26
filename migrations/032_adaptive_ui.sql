-- =====================================================================
-- 032_adaptive_ui.sql
-- Adaptive UI telemetry + decision tables.
--
-- HARD CONSTRAINTS:
--   * Touches ONLY new tables. Never references jobs, providers, queues.
--   * No new worker process. Aggregation is a Postgres MATERIALIZED VIEW
--     refreshed by pg_cron (optional) or lazily by the API handler.
--   * Ad count is conserved by the application layer (AdSlot). This SQL
--     does NOT make assumptions about ad components.
--
-- This migration is IDEMPOTENT and NON-DESTRUCTIVE:
--   * All CREATE statements use IF NOT EXISTS.
--   * No DROP statements anywhere.
--   * Row Level Security is enabled on every new table with explicit
--     policies appropriate to the access pattern.
-- =====================================================================

-- 1. Raw event log -----------------------------------------------------
CREATE TABLE IF NOT EXISTS ui_events (
    id              BIGSERIAL PRIMARY KEY,
    user_id         UUID NULL,
    session_id      TEXT NOT NULL,
    cohort_key      TEXT NOT NULL DEFAULT 'default',
    region          TEXT NOT NULL DEFAULT 'XX',
    place           TEXT NOT NULL,
    slot_id         TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
    ts              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ui_events_lookup_idx
    ON ui_events (cohort_key, region, place, slot_id, ts DESC);
CREATE INDEX IF NOT EXISTS ui_events_session_idx
    ON ui_events (session_id, ts DESC);
CREATE INDEX IF NOT EXISTS ui_events_ts_idx
    ON ui_events (ts DESC);

-- 2. Aggregated attention map (materialized view, no worker needed) ----
-- Created only if it does not already exist. This statement is wrapped
-- in a DO block to make it idempotent without needing a destructive DROP.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_matviews WHERE matviewname = 'cohort_attention_map'
    ) THEN
        EXECUTE $mv$
            CREATE MATERIALIZED VIEW cohort_attention_map AS
            SELECT
                cohort_key,
                region,
                place,
                slot_id,
                COUNT(DISTINCT session_id)                                              AS sample_size,
                COUNT(*) FILTER (WHERE event_type = 'view_enter')                       AS view_count,
                COALESCE(AVG(NULLIF((payload->>'dwell_ms')::numeric, 0)), 0)            AS dwell_avg_ms,
                COUNT(*) FILTER (WHERE event_type IN ('click','ad_click'))              AS click_count,
                COUNT(*) FILTER (WHERE event_type = 'ad_view')                          AS ad_view_count,
                COUNT(*) FILTER (WHERE event_type = 'ad_click')                         AS ad_click_count,
                COUNT(*) FILTER (WHERE event_type = 'ad_dismiss')                       AS ad_dismiss_count,
                CASE WHEN COUNT(*) FILTER (WHERE event_type='view_enter') > 0
                     THEN COUNT(*) FILTER (WHERE event_type IN ('click','ad_click'))::float
                          / COUNT(*) FILTER (WHERE event_type='view_enter')
                     ELSE 0 END                                                          AS interaction_rate,
                CASE WHEN COUNT(*) FILTER (WHERE event_type='ad_view') > 0
                     THEN COUNT(*) FILTER (WHERE event_type='ad_dismiss')::float
                          / COUNT(*) FILTER (WHERE event_type='ad_view')
                     ELSE 0 END                                                          AS dismiss_rate,
                now()                                                                    AS computed_at
            FROM ui_events
            WHERE ts > now() - interval '14 days'
            GROUP BY cohort_key, region, place, slot_id
        $mv$;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS cohort_attention_map_pk
    ON cohort_attention_map (cohort_key, region, place, slot_id);

-- 3. Locked layout decisions per cohort --------------------------------
CREATE TABLE IF NOT EXISTS layout_decisions (
    cohort_key      TEXT NOT NULL,
    region          TEXT NOT NULL,
    place           TEXT NOT NULL,
    config          JSONB NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    locked_until    TIMESTAMPTZ NOT NULL DEFAULT now() + interval '7 days',
    promoted_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (cohort_key, region, place)
);

CREATE TABLE IF NOT EXISTS layout_history (
    id              BIGSERIAL PRIMARY KEY,
    cohort_key      TEXT NOT NULL,
    region          TEXT NOT NULL,
    place           TEXT NOT NULL,
    config          JSONB NOT NULL,
    version         INTEGER NOT NULL,
    promoted_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 4. Row Level Security ------------------------------------------------
-- Backend uses the service_role key which bypasses RLS entirely. These
-- policies exist to safely deny any access from anon / authenticated
-- public keys, while still allowing the telemetry insert from clients.

ALTER TABLE ui_events        ENABLE ROW LEVEL SECURITY;
ALTER TABLE layout_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE layout_history   ENABLE ROW LEVEL SECURITY;

-- 4a. ui_events: anon/authenticated may INSERT telemetry; no one may
--     read it from a public key (privacy: only the service_role/API
--     should read aggregated stats).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname='public' AND tablename='ui_events'
                     AND policyname='ui_events_anon_insert') THEN
        CREATE POLICY ui_events_anon_insert
            ON ui_events
            FOR INSERT
            TO anon, authenticated
            WITH CHECK (true);
    END IF;
END $$;
-- NOTE: no SELECT / UPDATE / DELETE policy => those operations are
--       denied for anon and authenticated. service_role bypasses RLS.

-- 4b. layout_decisions: read-only for everyone (so the client could
--     fetch it directly if ever needed); writes restricted to
--     service_role (which bypasses RLS).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE schemaname='public' AND tablename='layout_decisions'
                     AND policyname='layout_decisions_public_read') THEN
        CREATE POLICY layout_decisions_public_read
            ON layout_decisions
            FOR SELECT
            TO anon, authenticated
            USING (true);
    END IF;
END $$;

-- 4c. layout_history: completely private. Only service_role may access
--     it (no policies = all anon/authenticated operations are denied).
--     RLS is enabled above; intentionally no policies created.

-- 5. OPTIONAL: schedule view refresh every 6h via pg_cron --------------
--   Enable only if pg_cron is installed on your Supabase project.
--
-- SELECT cron.schedule(
--     'refresh-cohort-attention-map',
--     '0 */6 * * *',
--     $$ REFRESH MATERIALIZED VIEW CONCURRENTLY cohort_attention_map; $$
-- );

-- 6. Convenience helper: lazy refresh when stale -----------------------
CREATE OR REPLACE FUNCTION refresh_cohort_map_if_stale(p_max_age_minutes INT DEFAULT 360)
RETURNS BOOLEAN
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    last_run TIMESTAMPTZ;
BEGIN
    SELECT MAX(computed_at) INTO last_run FROM cohort_attention_map;
    IF last_run IS NULL OR last_run < now() - (p_max_age_minutes || ' minutes')::interval THEN
        REFRESH MATERIALIZED VIEW CONCURRENTLY cohort_attention_map;
        RETURN TRUE;
    END IF;
    RETURN FALSE;
END;
$$;
