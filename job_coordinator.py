"""
Job Queue Coordinator - Model-based Parallel Job Scheduling
Coordinates workflow and normal jobs based on model usage.
Uses cross-process-safe SELECT FOR UPDATE stored procedures so that
app.py and job_worker_realtime.py (separate OS processes) can never
corrupt the shared active-jobs state.
"""

import logging
import threading
from typing import Dict, List, Optional, Any
from datetime import datetime
from provider_api_keys import get_worker1_client

logger = logging.getLogger(__name__)

# In-memory snapshot of active slots — used only as a fast-path for the
# self-reservation check so the spawned thread doesn't make an extra RPC
# round-trip when it calls on_job_start() for a job already pre-claimed by
# process_next_queued_job().  Never use this as ground truth; always prefer
# the DB via the RPC.
_active_jobs_cache: List[Dict] = []          # [{job_id, job_type, models}]
_cache_lock = threading.Lock()               # protects _active_jobs_cache writes


def _cache_contains(job_id: str) -> bool:
    with _cache_lock:
        return any(s.get("job_id") == job_id for s in _active_jobs_cache)


def _cache_set(active_jobs: List[Dict]):
    """Replace the cache with the list returned by the RPC."""
    with _cache_lock:
        global _active_jobs_cache
        _active_jobs_cache = list(active_jobs) if active_jobs else []


class JobCoordinator:
    """
    Coordinates job execution based on model usage.
    Parallel execution is allowed when jobs use different models/providers.
    All state mutations go through Worker1 stored procedures that hold a
    PostgreSQL row lock (SELECT FOR UPDATE) — safe across multiple OS processes.
    """

    def __init__(self):
        self.supabase = None      # Worker1 DB: job_queue_state, job_queue_log, providers
        self.main_supabase = None # Main DB: jobs table
        self._initialize_supabase()

    def _initialize_supabase(self):
        try:
            self.supabase = get_worker1_client()
            if not self.supabase:
                logger.error("[COORDINATOR] Failed to initialize Worker1 Supabase client")
        except Exception as e:
            logger.error(f"[COORDINATOR] Error initializing Worker1 Supabase: {e}")
        try:
            from supabase_client import supabase as _main_sb
            self.main_supabase = _main_sb
        except Exception as e:
            logger.error(f"[COORDINATOR] Error initializing main Supabase client: {e}")

    # =========================================================================
    # MODEL EXTRACTION
    # =========================================================================

    def get_workflow_models(self, workflow_config: Dict) -> List[str]:
        """Extract all unique models used across all workflow steps."""
        models = []
        for step in workflow_config.get('steps', []):
            model = step.get('default_model') or step.get('model')
            if model:
                models.append(model)
        seen: set = set()
        unique = []
        for m in models:
            if m not in seen:
                seen.add(m)
                unique.append(m)
        logger.info(f"[COORDINATOR] Extracted {len(unique)} models from workflow: {unique}")
        return unique

    def get_job_model(self, job: Dict) -> str:
        model = job.get('model', '')
        logger.debug(f"[COORDINATOR] Extracted model from job: {model}")
        return model

    # =========================================================================
    # ATOMIC SLOT OPERATIONS (RPC — cross-process safe)
    # =========================================================================

    def try_claim_slot(self, job_id: str, job_type: str, models: List[str]) -> Dict[str, Any]:
        """
        Atomically try to claim a coordinator slot via the Worker1 stored procedure.

        Uses SELECT FOR UPDATE inside the stored procedure so that any concurrent
        call from another OS process blocks at the DB level until this transaction
        commits — eliminating the check-then-act race window entirely.

        Returns dict with keys:
            result           — "claimed" | "already_active" | "conflict"
            active_jobs      — current list of active slots after the operation
            conflicting_models — models that caused a conflict (empty if no conflict)
        """
        if not self.supabase:
            logger.error("[COORDINATOR] Worker1 Supabase not initialised")
            return {"result": "error", "active_jobs": [], "conflicting_models": []}

        try:
            resp = self.supabase.rpc(
                'try_claim_coordinator_slot',
                {
                    'p_job_id':   job_id,
                    'p_job_type': job_type,
                    'p_models':   models,   # pass list directly — Supabase serialises to JSONB
                }
            ).execute()

            data = resp.data if resp else None
            if not data:
                logger.error(f"[COORDINATOR] try_claim_coordinator_slot returned no data for {job_id}")
                return {"result": "error", "active_jobs": [], "conflicting_models": []}

            result = data.get('result', 'error')
            active_jobs = data.get('active_jobs', [])

            # Keep cache in sync
            _cache_set(active_jobs)

            logger.info(f"[COORDINATOR] try_claim_slot({job_id}) → {result}")
            return {
                "result":            result,
                "active_jobs":       active_jobs,
                "conflicting_models": data.get('conflicting_models', []),
            }
        except Exception as e:
            logger.error(f"[COORDINATOR] Error calling try_claim_coordinator_slot: {e}")
            return {"result": "error", "active_jobs": [], "conflicting_models": []}

    def release_slot(self, job_id: str) -> bool:
        """
        Atomically remove the slot for job_id from active_jobs.
        Safe to call even if the slot no longer exists (idempotent).
        """
        if not self.supabase:
            logger.error("[COORDINATOR] Worker1 Supabase not initialised")
            return False

        try:
            resp = self.supabase.rpc(
                'release_coordinator_slot',
                {'p_job_id': job_id}
            ).execute()

            data = resp.data if resp else None
            if data:
                _cache_set(data.get('active_jobs', []))

            logger.info(f"[COORDINATOR] release_slot({job_id}) completed")
            return True
        except Exception as e:
            logger.error(f"[COORDINATOR] Error calling release_coordinator_slot: {e}")
            return False

    def clear_active_job(self) -> bool:
        """
        Reset ALL active slots to empty.
        Used at startup, worker restart, and error recovery.
        Calls reset_all_coordinator_slots() which also zeroes the legacy columns.
        """
        if not self.supabase:
            logger.error("[COORDINATOR] Worker1 Supabase not initialised")
            return False

        try:
            resp = self.supabase.rpc('reset_all_coordinator_slots', {}).execute()
            _cache_set([])
            logger.info("[COORDINATOR] reset_all_coordinator_slots — all slots cleared")
            return True
        except Exception as e:
            logger.error(f"[COORDINATOR] Error resetting coordinator slots: {e}")
            return False

    # =========================================================================
    # STATE QUERY
    # =========================================================================

    def get_active_job_state(self) -> Optional[Dict]:
        """
        Read current coordinator state from Worker1 DB.
        Returns the raw row dict (includes active_jobs JSONB array).
        Also refreshes the in-memory cache.
        """
        if not self.supabase:
            return None
        try:
            resp = self.supabase.table('job_queue_state').select('*').eq('id', 1).single().execute()
            if resp.data:
                _cache_set(resp.data.get('active_jobs', []))
                return resp.data
            return None
        except Exception as e:
            logger.error(f"[COORDINATOR] Error fetching active job state: {e}")
            return None

    # =========================================================================
    # QUEUE LOGGING
    # =========================================================================

    def log_queue_event(self, job_id: str, job_type: str, event_type: str,
                        models: Optional[List[str]] = None,
                        blocked_by_job_id: Optional[str] = None,
                        conflict_reason: Optional[str] = None,
                        metadata: Optional[Dict] = None) -> bool:
        if not self.supabase:
            return False
        try:
            self.supabase.table('job_queue_log').insert({
                'job_id':           job_id,
                'job_type':         job_type,
                'event_type':       event_type,
                'models':           models,
                'blocked_by_job_id': blocked_by_job_id,
                'conflict_reason':  conflict_reason,
                'metadata':         metadata,
                'created_at':       datetime.utcnow().isoformat()
            }).execute()
            return True
        except Exception as e:
            logger.error(f"[COORDINATOR] Error logging queue event: {e}")
            return False

    # =========================================================================
    # QUEUE DB HELPERS  (operate on Main DB — jobs table)
    # =========================================================================

    def mark_job_queued(self, job_id: str, blocked_by: str, conflict_reason: str,
                        required_models: List[str]) -> bool:
        if not self.main_supabase:
            return False
        try:
            self.main_supabase.table('jobs').update({
                'blocked_by_job_id': blocked_by,
                'conflict_reason':   conflict_reason,
                'required_models':   required_models,
                'queued_at':         datetime.utcnow().isoformat()
            }).eq('job_id', job_id).execute()

            # BUG FIX: app.py and retry_manager set status="running" BEFORE calling
            # execute_workflow, which then calls on_job_start.  If on_job_start returns
            # "conflict" the job must be reset back to "pending" so that
            # process_next_queued_job() (which filters status IN pending/pending_retry)
            # can find and re-trigger it when the blocker finishes.
            # The .eq('status','running') guard makes this a no-op for jobs that were
            # already pending/pending_retry when they were blocked.
            self.main_supabase.table('jobs').update({'status': 'pending'}) \
                .eq('job_id', job_id).eq('status', 'running').execute()

            logger.info(f"[COORDINATOR] Marked job {job_id} as queued (blocked by {blocked_by})")
            return True
        except Exception as e:
            logger.error(f"[COORDINATOR] Error marking job as queued: {e}")
            return False

    def clear_job_queue_info(self, job_id: str) -> bool:
        if not self.main_supabase:
            return False
        try:
            self.main_supabase.table('jobs').update({
                'blocked_by_job_id': None,
                'conflict_reason':   None,
                'queued_at':         None
            }).eq('job_id', job_id).execute()
            return True
        except Exception as e:
            logger.error(f"[COORDINATOR] Error clearing queue info: {e}")
            return False

    # =========================================================================
    # MAIN COORDINATION METHODS
    # =========================================================================

    def on_job_start(self, job_id: str, job_type: str, required_models: List[str]) -> Dict[str, Any]:
        """
        Called when a job wants to start.

        1. Fast-path: if job_id is already in the in-memory cache it was
           pre-claimed by process_next_queued_job() — allow through immediately
           without a DB round-trip.
        2. Otherwise: call try_claim_slot() (SELECT FOR UPDATE — atomic across
           all processes and threads).
        3. Handle the three outcomes:
               "claimed"        → job may start
               "already_active" → job pre-claimed by coordinator, may start
               "conflict"       → models clash with another active job; queue it

        Returns dict:
            allowed — bool
            reason  — explanation string
            action  — "start" | "start_preclaimed" | "queue" | "error"
        """
        logger.info(f"[COORDINATOR] on_job_start: {job_id} ({job_type}) models={required_models}")

        # ── Fast-path self-reservation check ─────────────────────────────────
        if _cache_contains(job_id):
            logger.info(f"[COORDINATOR] {job_id} already in cache — pre-claimed, allowing through")
            # Clear blocked_by so the job doesn't keep appearing in process_next_queued_job queries
            self.clear_job_queue_info(job_id)
            return {"allowed": True, "reason": "Pre-claimed by coordinator", "action": "start_preclaimed"}

        # ── Atomic RPC claim ─────────────────────────────────────────────────
        claim = self.try_claim_slot(job_id, job_type, required_models)
        result = claim['result']

        if result == 'claimed':
            self.clear_job_queue_info(job_id)
            self.log_queue_event(job_id, job_type, 'started', models=required_models)
            return {"allowed": True, "reason": "Slot claimed", "action": "start"}

        if result == 'already_active':
            # Another call for the same job (e.g. spawned thread after pre-claim).
            # Clear blocked_by here as a safety net in case it wasn't cleared earlier.
            self.clear_job_queue_info(job_id)
            logger.info(f"[COORDINATOR] {job_id} already_active in DB — allowing through")
            return {"allowed": True, "reason": "Already active (self-reservation)", "action": "start_preclaimed"}

        if result == 'conflict':
            conflict_models = claim.get('conflicting_models', [])
            active_jobs     = claim.get('active_jobs', [])
            blocked_by      = active_jobs[0].get('job_id') if active_jobs else 'unknown'
            reason          = f"Model conflict with active job {blocked_by}: {conflict_models}"
            logger.warning(f"[COORDINATOR] {job_id} blocked — {reason}")
            self.mark_job_queued(job_id, blocked_by, reason, required_models)
            self.log_queue_event(job_id, job_type, 'blocked',
                                 models=required_models,
                                 blocked_by_job_id=blocked_by,
                                 conflict_reason=reason)
            return {"allowed": False, "reason": reason, "action": "queue", "blocked_by": blocked_by}

        # result == 'error'
        logger.error(f"[COORDINATOR] try_claim_slot returned error for {job_id} — allowing untracked to avoid stuck job")
        return {"allowed": True, "reason": "RPC error — allowing untracked", "action": "start_untracked"}

    def on_job_complete(self, job_id: str, job_type: str) -> bool:
        """
        Called when a job completes (success or failure).
        Releases the coordinator slot and triggers all queued jobs that can
        now run in parallel (no model conflict).
        """
        logger.info(f"[COORDINATOR] on_job_complete: {job_id} ({job_type})")

        # Non-critical audit log (outside DB lock — fine if this fails)
        self.log_queue_event(job_id, job_type, 'completed')

        if not self.release_slot(job_id):
            logger.error(f"[COORDINATOR] release_slot failed for {job_id}")
            # Still attempt to process queued jobs even if release failed
            # (release is idempotent; worst case the slot lingers until restart)

        # After releasing, trigger ALL queued jobs that no longer have a conflict.
        # process_next_queued_job() calls try_claim_slot() for each queued job —
        # parallel-safe because the RPC holds the row lock per claim.
        self.process_next_queued_job()
        return True

    def process_next_queued_job(self) -> List[Dict]:
        """
        Find ALL queued jobs that can now start (no model conflict with current
        active slots) and trigger them in parallel.

        Unlike the old code which triggered only the first eligible job, this
        loops the entire queue and claims every non-conflicting job.  Since
        workflows use {vision-aicc, clipdrop} and normal jobs use completely
        different providers, multiple jobs will typically all be claimable at once.

        Returns:
            List of job dicts that were triggered (may be empty).
        """
        if not self.main_supabase:
            return []

        try:
            # Fetch all blocked jobs ordered by when they were queued (oldest first)
            resp = self.main_supabase.table('jobs').select('*') \
                .in_('status', ['pending', 'pending_retry']) \
                .not_.is_('blocked_by_job_id', 'null') \
                .order('queued_at', desc=False) \
                .limit(50) \
                .execute()

            queued_jobs = resp.data if resp and resp.data else []

            if not queued_jobs:
                logger.info("[COORDINATOR] No queued jobs found")
                return []

            logger.info(f"[COORDINATOR] Found {len(queued_jobs)} queued job(s), attempting parallel claims...")

            triggered = []
            for job in queued_jobs:
                job_id         = job.get('job_id')
                job_type       = job.get('job_type', 'image')
                required_models = job.get('required_models', [])

                if not required_models:
                    if job_type == 'workflow':
                        # Workflow stored required_models=[] (legacy row or blocked before models
                        # were written).  Re-extract from the live workflow config so the RPC
                        # conflict check gets the real model list instead of an empty array.
                        # Empty array → every active slot passes the conflict loop → two workflows
                        # with [] would both get "claimed" and run in parallel (wrong).
                        try:
                            from workflow_manager import get_workflow_manager
                            _wm = get_workflow_manager()
                            _cfg = _wm.get_workflow(job.get('model', ''))
                            if _cfg:
                                required_models = self.get_workflow_models(_cfg)
                                if required_models:
                                    # Persist so we don't have to re-extract next time
                                    self.main_supabase.table('jobs').update(
                                        {'required_models': required_models}
                                    ).eq('job_id', job_id).execute()
                        except Exception as _e:
                            logger.warning(f"[COORDINATOR] Could not extract workflow models for {job_id}: {_e}")
                    else:
                        required_models = [self.get_job_model(job)]

                # Pre-claim the slot atomically.  If "claimed" → trigger.
                # If "already_active" → another thread already claimed it, skip.
                # If "conflict" → still blocked, skip.
                claim = self.try_claim_slot(job_id, job_type, required_models)
                result = claim['result']

                if result == 'claimed':
                    logger.info(f"[COORDINATOR] Slot claimed for queued job {job_id} — triggering")
                    # For workflow jobs: set status="running" BEFORE clearing blocked_by_job_id.
                    # If we clear blocked_by first, retry_stale_pending_workflows() could see
                    # the job as status=pending + blocked_by=null and race-claim it, causing
                    # double execution.  Promoting to "running" first closes that race window.
                    if job_type == 'workflow':
                        try:
                            self.main_supabase.table('jobs').update({'status': 'running'}) \
                                .eq('job_id', job_id) \
                                .in_('status', ['pending', 'pending_retry']) \
                                .execute()
                        except Exception as _e:
                            logger.warning(f"[COORDINATOR] Could not promote workflow {job_id} to running: {_e}")
                    self.clear_job_queue_info(job_id)
                    self._trigger_job_processing(job)
                    triggered.append(job)

                elif result == 'already_active':
                    # Pre-claimed by a concurrent call; processing already started
                    logger.debug(f"[COORDINATOR] {job_id} already claimed by concurrent call — skipping")

                else:
                    # 'conflict' or 'error'
                    logger.debug(f"[COORDINATOR] {job_id} still blocked ({result}) — keeping queued")

            if triggered:
                logger.info(f"[COORDINATOR] Triggered {len(triggered)} job(s) in parallel: "
                            f"{[j.get('job_id') for j in triggered]}")
            else:
                logger.info("[COORDINATOR] No queued jobs could be started (all still have conflicts)")

            return triggered

        except Exception as e:
            logger.error(f"[COORDINATOR] Error in process_next_queued_job: {e}")
            return []

    def _trigger_job_processing(self, job: Dict):
        """
        Route a just-claimed queued job to the correct execution path:
          - workflow jobs → workflow_retry_manager (resume) or workflow_manager (fresh)
          - image/video jobs → job_worker_realtime.process_job_with_concurrency_control
        """
        job_id   = job.get('job_id')
        job_type = job.get('job_type', 'image')
        logger.info(f"[COORDINATOR] Triggering processing for job {job_id} (type: {job_type})")

        if job_type == 'workflow':
            try:
                import asyncio
                import threading as _threading
                from workflow_retry_manager import get_retry_manager

                retry_manager = get_retry_manager()
                execution_id  = None

                try:
                    from supabase_client import supabase as _sb
                    exec_resp = _sb.table('workflow_executions') \
                        .select('id') \
                        .eq('job_id', job_id) \
                        .single() \
                        .execute()
                    if exec_resp.data:
                        execution_id = exec_resp.data['id']
                except Exception as lookup_err:
                    logger.warning(f"[COORDINATOR] Could not fetch execution for workflow {job_id}: {lookup_err}")

                if execution_id:
                    def _resume():
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            loop.run_until_complete(retry_manager._resume_workflow(execution_id, job_id))
                        finally:
                            loop.close()
                    _threading.Thread(target=_resume, daemon=True).start()
                else:
                    # Blocked before base_workflow.execute() created the execution record —
                    # re-trigger as a fresh execution.
                    logger.info(f"[COORDINATOR] No execution record for {job_id} — re-triggering as fresh execution")

                    def _execute_fresh(jid=job_id):
                        from workflow_manager import get_workflow_manager
                        from supabase_client import supabase as _sb
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            job_row = _sb.table('jobs').select('*').eq('job_id', jid).single().execute()
                            if not job_row.data:
                                logger.error(f"[COORDINATOR] Cannot re-trigger {jid}: job row not found")
                                return
                            job_data = job_row.data
                            meta = job_data.get('metadata', {}) or {}
                            img  = job_data.get('image_url') or meta.get('input_image_url')
                            wm   = get_workflow_manager()
                            loop.run_until_complete(wm.execute_workflow(
                                workflow_id=job_data.get('model'),
                                input_data=img,
                                user_id=job_data.get('user_id'),
                                job_id=jid
                            ))
                        except Exception as _e:
                            logger.error(f"[COORDINATOR] Fresh workflow execution failed for {jid}: {_e}")
                        finally:
                            loop.close()
                    _threading.Thread(target=_execute_fresh, daemon=True).start()

            except Exception as e:
                logger.error(f"[COORDINATOR] Error triggering workflow job {job_id}: {e}")
            return

        # Normal image / video job
        try:
            from job_worker_realtime import process_job_with_concurrency_control
            import threading
            threading.Thread(
                target=process_job_with_concurrency_control,
                args=(job,),
                daemon=True
            ).start()
        except Exception as e:
            logger.error(f"[COORDINATOR] Error triggering job processing: {e}")


# =============================================================================
# SINGLETON
# =============================================================================

_coordinator_instance: Optional[JobCoordinator] = None


def get_job_coordinator() -> JobCoordinator:
    """Get singleton job coordinator instance."""
    global _coordinator_instance
    if _coordinator_instance is None:
        _coordinator_instance = JobCoordinator()
    return _coordinator_instance
