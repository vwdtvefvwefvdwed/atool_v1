-- =============================================================================
-- MIGRATION: worker1_028_parallel_coordinator_slots.sql
-- TARGET DB:  Worker1 (gmhpbeqvqpuoctaqgnum)
-- PURPOSE:    Replace single active_job_id slot with active_jobs JSONB array
--             and two stored procedures that use SELECT FOR UPDATE for
--             cross-process atomicity (fixes blocked jobs stuck forever).
-- =============================================================================
--
-- BACKGROUND
-- ----------
-- The old coordinator used a single (active_job_id, active_job_type, active_models)
-- slot and serialised every job globally.  Two bugs resulted:
--
--   1. Blocked jobs never restarted: mark_job_queued() wrote to Worker1 but
--      jobs lives in Main DB — the write was silently a no-op.
--   2. Cross-process race: app.py and job_worker_realtime.py are separate OS
--      processes.  threading.RLock() only protects within one process.  Two
--      processes could both read "no active job" simultaneously and both claim
--      the slot, corrupting state.
--   3. No parallelism: workflows (vision-aicc + clipdrop) and normal jobs
--      (completely different providers) were always serialised even though they
--      never share a model.
--
-- SOLUTION
-- --------
--   • Add active_jobs JSONB column (array of slot objects).
--   • try_claim_coordinator_slot(): SELECT FOR UPDATE on the singleton row so
--     check + write is atomic across ALL processes and ALL threads.
--   • release_coordinator_slot(): atomically removes one slot by job_id.
--   • Python coordinator calls these two RPCs; model-conflict check lives
--     inside the locked transaction — no external race window possible.
--
-- =============================================================================

-- STEP 1: Add active_jobs column to job_queue_state
-- The column stores an array of slot objects:
--   [{"job_id": "...", "job_type": "workflow|normal", "models": [...], "started_at": "..."}]
-- -----------------------------------------------------------------------------
ALTER TABLE job_queue_state
    ADD COLUMN IF NOT EXISTS active_jobs JSONB NOT NULL DEFAULT '[]'::JSONB;

-- Also ensure last_updated column exists (some older Worker1 setups may lack it)
ALTER TABLE job_queue_state
    ADD COLUMN IF NOT EXISTS last_updated TIMESTAMPTZ DEFAULT now();

-- =============================================================================
-- STEP 2: try_claim_coordinator_slot
-- =============================================================================
-- Atomically check for model conflicts and claim a slot if safe.
--
-- Parameters:
--   p_job_id   TEXT  — job ID wanting to start
--   p_job_type TEXT  — "workflow" or "normal"
--   p_models   JSONB — JSON array of model strings used by this job
--
-- Returns JSONB with shape:
--   { "result": "claimed" | "already_active" | "conflict",
--     "active_jobs": [...current array after operation...],
--     "conflicting_models": [...] }
--
-- Result semantics:
--   "claimed"        — slot acquired, caller may proceed
--   "already_active" — p_job_id already holds a slot (self-reservation hit)
--                      caller may also proceed (spawned thread re-checking)
--   "conflict"       — model overlap with a different active job; caller must
--                      keep the job queued and wait
-- =============================================================================
CREATE OR REPLACE FUNCTION try_claim_coordinator_slot(
    p_job_id   TEXT,
    p_job_type TEXT,
    p_models   JSONB   -- e.g. '["gemini-25-flash-aicc", "clipdrop-upscale"]'
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_row           RECORD;
    v_active_jobs   JSONB;
    v_entry         JSONB;
    v_conflict_mods JSONB  := '[]'::JSONB;
    v_new_entry     JSONB;
BEGIN
    -- -------------------------------------------------------------------------
    -- Acquire a row-level lock on the singleton coordinator row.
    -- Any concurrent call (same process, different process) will block here
    -- until the current transaction commits — making check+write fully atomic.
    -- -------------------------------------------------------------------------
    SELECT * INTO v_row
    FROM   job_queue_state
    WHERE  id = 1
    FOR UPDATE;

    IF NOT FOUND THEN
        -- Safety: if the row doesn't exist, create it and return claimed.
        INSERT INTO job_queue_state (id, active_jobs, last_updated)
        VALUES (1, jsonb_build_array(
            jsonb_build_object(
                'job_id',     p_job_id,
                'job_type',   p_job_type,
                'models',     p_models,
                'started_at', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
            )
        ), now())
        ON CONFLICT (id) DO NOTHING;

        RETURN jsonb_build_object(
            'result',            'claimed',
            'active_jobs',       jsonb_build_array(
                jsonb_build_object('job_id', p_job_id, 'job_type', p_job_type, 'models', p_models)
            ),
            'conflicting_models','[]'::JSONB
        );
    END IF;

    v_active_jobs := COALESCE(v_row.active_jobs, '[]'::JSONB);

    -- -------------------------------------------------------------------------
    -- Pass 1: check for self-reservation (same job_id already holds a slot).
    -- This happens when process_next_queued_job() pre-claims then the spawned
    -- thread calls on_job_start() for the same job.
    -- -------------------------------------------------------------------------
    FOR v_entry IN SELECT value FROM jsonb_array_elements(v_active_jobs) AS t(value)
    LOOP
        IF (v_entry->>'job_id') = p_job_id THEN
            RETURN jsonb_build_object(
                'result',            'already_active',
                'active_jobs',       v_active_jobs,
                'conflicting_models','[]'::JSONB
            );
        END IF;
    END LOOP;

    -- -------------------------------------------------------------------------
    -- Pass 2: check for model conflicts with every currently active slot.
    -- -------------------------------------------------------------------------
    FOR v_entry IN SELECT value FROM jsonb_array_elements(v_active_jobs) AS t(value)
    LOOP
        -- Find models that appear in both p_models and this slot's models
        SELECT COALESCE(jsonb_agg(m.val), '[]'::JSONB)
        INTO   v_conflict_mods
        FROM   jsonb_array_elements_text(p_models)          AS m(val)
        WHERE  m.val IN (
                   SELECT jsonb_array_elements_text(v_entry->'models')
               );

        IF jsonb_array_length(v_conflict_mods) > 0 THEN
            -- Model conflict — do NOT update the row; just return conflict info.
            RETURN jsonb_build_object(
                'result',            'conflict',
                'active_jobs',       v_active_jobs,
                'conflicting_models', v_conflict_mods
            );
        END IF;
    END LOOP;

    -- -------------------------------------------------------------------------
    -- No conflict found — append new slot and write back.
    -- -------------------------------------------------------------------------
    v_new_entry := jsonb_build_object(
        'job_id',     p_job_id,
        'job_type',   p_job_type,
        'models',     p_models,
        'started_at', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
    );

    v_active_jobs := v_active_jobs || jsonb_build_array(v_new_entry);

    UPDATE job_queue_state
    SET    active_jobs   = v_active_jobs,
           last_updated  = now()
    WHERE  id = 1;

    RETURN jsonb_build_object(
        'result',            'claimed',
        'active_jobs',       v_active_jobs,
        'conflicting_models','[]'::JSONB
    );
END;
$$;

-- =============================================================================
-- STEP 3: release_coordinator_slot
-- =============================================================================
-- Atomically removes the slot for p_job_id from active_jobs.
-- Safe to call even if p_job_id is not currently in the array (idempotent).
--
-- Returns JSONB:
--   { "result": "released", "active_jobs": [...remaining slots...] }
-- =============================================================================
CREATE OR REPLACE FUNCTION release_coordinator_slot(p_job_id TEXT)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_row          RECORD;
    v_active_jobs  JSONB;
    v_new_jobs     JSONB  := '[]'::JSONB;
    v_entry        JSONB;
BEGIN
    SELECT * INTO v_row
    FROM   job_queue_state
    WHERE  id = 1
    FOR UPDATE;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('result', 'released', 'active_jobs', '[]'::JSONB);
    END IF;

    v_active_jobs := COALESCE(v_row.active_jobs, '[]'::JSONB);

    -- Rebuild array excluding the released job_id
    FOR v_entry IN SELECT value FROM jsonb_array_elements(v_active_jobs) AS t(value)
    LOOP
        IF (v_entry->>'job_id') != p_job_id THEN
            v_new_jobs := v_new_jobs || jsonb_build_array(v_entry);
        END IF;
    END LOOP;

    UPDATE job_queue_state
    SET    active_jobs  = v_new_jobs,
           last_updated = now()
    WHERE  id = 1;

    RETURN jsonb_build_object(
        'result',     'released',
        'active_jobs', v_new_jobs
    );
END;
$$;

-- =============================================================================
-- STEP 4: reset_all_coordinator_slots  (utility — for startup / emergency reset)
-- =============================================================================
CREATE OR REPLACE FUNCTION reset_all_coordinator_slots()
RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    UPDATE job_queue_state
    SET    active_jobs   = '[]'::JSONB,
           -- Keep legacy columns in sync for any old code still reading them
           active_job_id   = NULL,
           active_job_type = NULL,
           active_models   = '[]'::JSONB,
           started_at      = NULL,
           last_updated    = now()
    WHERE  id = 1;

    RETURN jsonb_build_object('result', 'reset', 'active_jobs', '[]'::JSONB);
END;
$$;

-- =============================================================================
-- STEP 5: Grant execute to service_role (Supabase RPC calls use this role)
-- =============================================================================
GRANT EXECUTE ON FUNCTION try_claim_coordinator_slot(TEXT, TEXT, JSONB)  TO service_role;
GRANT EXECUTE ON FUNCTION release_coordinator_slot(TEXT)                  TO service_role;
GRANT EXECUTE ON FUNCTION reset_all_coordinator_slots()                   TO service_role;

-- =============================================================================
-- VERIFICATION
-- =============================================================================
DO $$
DECLARE
    v_col_exists   BOOLEAN;
    v_claim_result JSONB;
    v_release_result JSONB;
BEGIN
    -- Check column exists
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE  table_name  = 'job_queue_state'
        AND    column_name = 'active_jobs'
    ) INTO v_col_exists;

    IF NOT v_col_exists THEN
        RAISE EXCEPTION 'MIGRATION FAILED: active_jobs column not found on job_queue_state';
    END IF;

    -- Smoke-test: claim → already_active → release
    v_claim_result := try_claim_coordinator_slot(
        'migration-test-job',
        'normal',
        '["test-model-a"]'::JSONB
    );
    RAISE NOTICE 'First claim result: %', v_claim_result->>'result';
    -- Should be "claimed"

    v_claim_result := try_claim_coordinator_slot(
        'migration-test-job',
        'normal',
        '["test-model-a"]'::JSONB
    );
    RAISE NOTICE 'Second claim (same job) result: %', v_claim_result->>'result';
    -- Should be "already_active"

    -- Conflict test: different job, same model
    v_claim_result := try_claim_coordinator_slot(
        'migration-test-job-2',
        'normal',
        '["test-model-a"]'::JSONB
    );
    RAISE NOTICE 'Conflict claim result: %', v_claim_result->>'result';
    -- Should be "conflict"

    -- No-conflict test: different job, different model
    v_claim_result := try_claim_coordinator_slot(
        'migration-test-job-3',
        'workflow',
        '["test-model-b"]'::JSONB
    );
    RAISE NOTICE 'No-conflict claim result: %', v_claim_result->>'result';
    -- Should be "claimed" (parallel!)

    -- Release all test jobs
    PERFORM release_coordinator_slot('migration-test-job');
    PERFORM release_coordinator_slot('migration-test-job-3');

    -- Verify clean state
    v_release_result := (
        SELECT jsonb_build_object('active_jobs', active_jobs)
        FROM   job_queue_state WHERE id = 1
    );
    RAISE NOTICE 'Final active_jobs after cleanup: %', v_release_result->'active_jobs';

    RAISE NOTICE '✅ worker1_028_parallel_coordinator_slots migration verified successfully';
END;
$$;
