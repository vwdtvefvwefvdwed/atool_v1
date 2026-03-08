"""
Job Queue Coordinator - Model-based Job Scheduling
Prevents resource collapse by coordinating workflow and normal jobs based on model usage.
"""

import logging
import threading
from typing import Dict, List, Optional, Any, Set
from datetime import datetime
from provider_api_keys import get_worker1_client

logger = logging.getLogger(__name__)

# Global re-entrant lock for thread-safe coordinator operations
# RLock allows the same thread to acquire it multiple times (needed since
# on_job_start holds the lock and calls set_active_job which also acquires it)
_coordinator_lock = threading.RLock()

# In-memory cache of current state (synced with database)
_active_job_cache = {
    "job_id": None,
    "job_type": None,
    "models": []
}


class JobCoordinator:
    """
    Coordinates job execution based on model usage.
    Ensures only compatible jobs run simultaneously.
    """
    
    def __init__(self):
        self.supabase = None
        self._initialize_supabase()
    
    def _initialize_supabase(self):
        """Initialize Supabase client"""
        try:
            self.supabase = get_worker1_client()
            if not self.supabase:
                logger.error("[COORDINATOR] Failed to initialize Supabase client")
        except Exception as e:
            logger.error(f"[COORDINATOR] Error initializing Supabase: {e}")
    
    # =========================================================================
    # MODEL EXTRACTION
    # =========================================================================
    
    def get_workflow_models(self, workflow_config: Dict) -> List[str]:
        """
        Extract all models used across all workflow steps.
        
        Args:
            workflow_config: Workflow configuration dict with 'steps' key
            
        Returns:
            List of model names (e.g., ["nano-banana-pro-leonardo", "motion-2.0-fast"])
        """
        models = []
        steps = workflow_config.get('steps', [])
        
        for step in steps:
            # Get model from step config
            model = step.get('default_model') or step.get('model')
            if model:
                models.append(model)
        
        # Remove duplicates while preserving order
        seen = set()
        unique_models = []
        for model in models:
            if model not in seen:
                seen.add(model)
                unique_models.append(model)
        
        logger.info(f"[COORDINATOR] Extracted {len(unique_models)} models from workflow: {unique_models}")
        return unique_models
    
    def get_job_model(self, job: Dict) -> str:
        """
        Extract model from normal job.
        
        Args:
            job: Job dict with 'model' key
            
        Returns:
            Model name (e.g., "motion-2.0-fast")
        """
        model = job.get('model', '')
        logger.debug(f"[COORDINATOR] Extracted model from job: {model}")
        return model
    
    def has_model_conflict(self, new_models: List[str], active_models: List[str]) -> bool:
        """
        Check if any models overlap between new job and active job.
        
        Args:
            new_models: Models required by new job
            active_models: Models currently in use
            
        Returns:
            True if there's a conflict (overlap), False otherwise
        """
        new_set = set(new_models)
        active_set = set(active_models)
        conflict = bool(new_set & active_set)
        
        if conflict:
            conflicting_models = new_set & active_set
            logger.warning(f"[COORDINATOR] Model conflict detected: {conflicting_models}")
        
        return conflict
    
    # =========================================================================
    # GLOBAL STATE MANAGEMENT
    # =========================================================================
    
    def get_active_job_state(self) -> Optional[Dict]:
        """
        Get current active job state from database.
        
        Returns:
            Dict with active_job_id, active_job_type, active_models or None
        """
        if not self.supabase:
            logger.error("[COORDINATOR] Supabase not initialized")
            return None
        
        try:
            response = self.supabase.table('job_queue_state').select('*').eq('id', 1).single().execute()
            
            if response.data:
                state = response.data
                # Update cache
                global _active_job_cache
                _active_job_cache = {
                    "job_id": state.get('active_job_id'),
                    "job_type": state.get('active_job_type'),
                    "models": state.get('active_models', [])
                }
                return state
            
            return None
        except Exception as e:
            logger.error(f"[COORDINATOR] Error fetching active job state: {e}")
            return None
    
    def set_active_job(self, job_id: str, job_type: str, models: List[str]) -> bool:
        """
        Mark a job as active in global state.
        
        Args:
            job_id: Job ID (workflow ID or job ID)
            job_type: "workflow" or "normal"
            models: List of models this job uses
            
        Returns:
            True if successful, False otherwise
        """
        if not self.supabase:
            logger.error("[COORDINATOR] Supabase not initialized")
            return False
        
        try:
            with _coordinator_lock:
                self.supabase.table('job_queue_state').update({
                    'active_job_id': job_id,
                    'active_job_type': job_type,
                    'active_models': models,
                    'started_at': datetime.utcnow().isoformat(),
                    'last_updated': datetime.utcnow().isoformat()
                }).eq('id', 1).execute()
                
                # Update cache
                global _active_job_cache
                _active_job_cache = {
                    "job_id": job_id,
                    "job_type": job_type,
                    "models": models
                }
                
                logger.info(f"[COORDINATOR] Set active job: {job_id} ({job_type}) - Models: {models}")
                return True
        except Exception as e:
            logger.error(f"[COORDINATOR] Error setting active job: {e}")
            return False
    
    def clear_active_job(self) -> bool:
        """
        Clear active job state (job completed).
        
        Returns:
            True if successful, False otherwise
        """
        if not self.supabase:
            logger.error("[COORDINATOR] Supabase not initialized")
            return False
        
        try:
            with _coordinator_lock:
                self.supabase.table('job_queue_state').update({
                    'active_job_id': None,
                    'active_job_type': None,
                    'active_models': [],
                    'started_at': None,
                    'last_updated': datetime.utcnow().isoformat()
                }).eq('id', 1).execute()
                
                # Update cache
                global _active_job_cache
                _active_job_cache = {
                    "job_id": None,
                    "job_type": None,
                    "models": []
                }
                
                logger.info("[COORDINATOR] Cleared active job state")
                return True
        except Exception as e:
            logger.error(f"[COORDINATOR] Error clearing active job: {e}")
            return False
    
    # =========================================================================
    # QUEUE LOGGING
    # =========================================================================
    
    def log_queue_event(self, job_id: str, job_type: str, event_type: str, 
                       models: Optional[List[str]] = None,
                       blocked_by_job_id: Optional[str] = None,
                       conflict_reason: Optional[str] = None,
                       metadata: Optional[Dict] = None) -> bool:
        """
        Log a queue event for audit trail.
        
        Args:
            job_id: Job ID
            job_type: "workflow" or "normal"
            event_type: "queued", "started", "completed", "blocked", "conflict", "skipped"
            models: Models involved (optional)
            blocked_by_job_id: ID of blocking job (optional)
            conflict_reason: Reason for conflict (optional)
            metadata: Additional metadata (optional)
            
        Returns:
            True if successful, False otherwise
        """
        if not self.supabase:
            return False
        
        try:
            self.supabase.table('job_queue_log').insert({
                'job_id': job_id,
                'job_type': job_type,
                'event_type': event_type,
                'models': models,
                'blocked_by_job_id': blocked_by_job_id,
                'conflict_reason': conflict_reason,
                'metadata': metadata,
                'created_at': datetime.utcnow().isoformat()
            }).execute()
            
            logger.debug(f"[COORDINATOR] Logged event: {event_type} for job {job_id}")
            return True
        except Exception as e:
            logger.error(f"[COORDINATOR] Error logging queue event: {e}")
            return False
    
    # =========================================================================
    # JOB COORDINATION LOGIC
    # =========================================================================
    
    def can_start_job(self, job_id: str, job_type: str, required_models: List[str]) -> Dict[str, Any]:
        """
        Check if a job can start based on current active job and model conflicts.
        
        Args:
            job_id: Job ID to check
            job_type: "workflow" or "normal"
            required_models: Models required by this job
            
        Returns:
            Dict with:
                - can_start: bool (True if job can start)
                - reason: str (explanation)
                - blocked_by: str or None (ID of blocking job)
                - conflict_models: List[str] (models in conflict)
        """
        # Get current active job state
        active_state = self.get_active_job_state()
        
        # No active job - can start immediately
        if not active_state or not active_state.get('active_job_id'):
            logger.info(f"[COORDINATOR] No active job - {job_id} can start immediately")
            return {
                "can_start": True,
                "reason": "No active job",
                "blocked_by": None,
                "conflict_models": []
            }
        
        # Get active job details
        active_job_id = active_state.get('active_job_id')
        active_job_type = active_state.get('active_job_type')
        active_models = active_state.get('active_models', [])

        # Self-reservation check: process_next_queued_job() pre-claims the coordinator
        # slot BEFORE spawning the processing thread.  When the thread eventually calls
        # on_job_start() it must not block itself — allow it through so it can proceed
        # normally and call on_job_complete() when done.
        if active_job_id == job_id:
            logger.info(f"[COORDINATOR] Job {job_id} is already the reserved/active job — allowing through")
            return {
                "can_start": True,
                "reason": "Job already reserved as active by coordinator",
                "blocked_by": None,
                "conflict_models": []
            }

        # SERIALIZE ALL JOBS: Only one job can run at a time
        # This prevents global state overwrite issues and ensures safety
        logger.warning(f"[COORDINATOR] Job {job_id} blocked: Another job is running ({active_job_id})")
        
        return {
            "can_start": False,
            "reason": f"Job queue busy: {active_job_type} job {active_job_id} is currently running",
            "blocked_by": active_job_id,
            "conflict_models": []  # Not checking conflicts, just serializing all jobs
        }
    
    def mark_job_queued(self, job_id: str, blocked_by: str, conflict_reason: str, 
                       required_models: List[str]) -> bool:
        """
        Mark a job as queued (blocked) in the database.
        
        Args:
            job_id: Job ID to mark as queued
            blocked_by: ID of blocking job
            conflict_reason: Human-readable reason
            required_models: Models required by this job
            
        Returns:
            True if successful, False otherwise
        """
        if not self.supabase:
            return False
        
        try:
            # Update job in database
            self.supabase.table('jobs').update({
                'blocked_by_job_id': blocked_by,
                'conflict_reason': conflict_reason,
                'required_models': required_models,
                'queued_at': datetime.utcnow().isoformat()
            }).eq('job_id', job_id).execute()
            
            logger.info(f"[COORDINATOR] Marked job {job_id} as queued (blocked by {blocked_by})")
            return True
        except Exception as e:
            logger.error(f"[COORDINATOR] Error marking job as queued: {e}")
            return False
    
    def clear_job_queue_info(self, job_id: str) -> bool:
        """
        Clear queue info from job when it starts.
        
        Args:
            job_id: Job ID to clear
            
        Returns:
            True if successful, False otherwise
        """
        if not self.supabase:
            return False
        
        try:
            self.supabase.table('jobs').update({
                'blocked_by_job_id': None,
                'conflict_reason': None,
                'queued_at': None
            }).eq('job_id', job_id).execute()
            
            logger.debug(f"[COORDINATOR] Cleared queue info for job {job_id}")
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
        Checks if it can start and updates state accordingly.
        The entire read-check-write is wrapped in _coordinator_lock to prevent
        two concurrent calls both seeing "no active job" and both starting.
        
        Args:
            job_id: Job ID
            job_type: "workflow" or "normal"
            required_models: Models required by this job
            
        Returns:
            Dict with:
                - allowed: bool (True if job can start)
                - reason: str (explanation)
                - action: str ("start", "queue", "error")
        """
        logger.info(f"[COORDINATOR] Job start request: {job_id} ({job_type}) - Models: {required_models}")
        
        with _coordinator_lock:
            check_result = self.can_start_job(job_id, job_type, required_models)

            if check_result['can_start']:
                # Self-reservation: the slot was already claimed by process_next_queued_job().
                # Skip the redundant set_active_job / clear_job_queue_info / log_queue_event
                # calls — they were already executed during pre-claim.  Re-running them would
                # produce a duplicate 'started' entry in job_queue_log and unnecessary DB writes.
                if check_result.get('reason') == "Job already reserved as active by coordinator":
                    return {
                        "allowed": True,
                        "reason": check_result['reason'],
                        "action": "start"
                    }

                if self.set_active_job(job_id, job_type, required_models):
                    self.clear_job_queue_info(job_id)
                    self.log_queue_event(job_id, job_type, 'started', models=required_models)
                    return {
                        "allowed": True,
                        "reason": check_result['reason'],
                        "action": "start"
                    }
                else:
                    # DB write failed — fall back to allowing the job so it is not silently
                    # dropped.  Without this, the job stays pending forever because the
                    # calling code returns None and no retry is scheduled.
                    logger.error(f"[COORDINATOR] set_active_job failed for {job_id} — allowing job through without state tracking")
                    return {
                        "allowed": True,
                        "reason": "DB state update failed — allowing without coordinator tracking",
                        "action": "start_untracked"
                    }
            else:
                self.mark_job_queued(
                    job_id,
                    check_result['blocked_by'],
                    check_result['reason'],
                    required_models
                )
                self.log_queue_event(
                    job_id, job_type, 'blocked',
                    models=required_models,
                    blocked_by_job_id=check_result['blocked_by'],
                    conflict_reason=check_result['reason']
                )
                return {
                    "allowed": False,
                    "reason": check_result['reason'],
                    "action": "queue",
                    "blocked_by": check_result['blocked_by']
                }
    
    def on_job_complete(self, job_id: str, job_type: str) -> bool:
        """
        Called when a job completes.
        Clears active state and processes next queued job.
        
        Args:
            job_id: Job ID that completed
            job_type: "workflow" or "normal"
            
        Returns:
            True if successful, False otherwise
        """
        logger.info(f"[COORDINATOR] Job complete: {job_id} ({job_type})")
        
        # Log completion event (outside lock — non-critical audit trail)
        self.log_queue_event(job_id, job_type, 'completed')
        
        # Hold the coordinator lock across BOTH clear and find-next so that a
        # concurrent realtime INSERT cannot sneak in between the two steps and
        # see an empty active-job slot before the queued job is promoted.
        # _coordinator_lock is an RLock so the re-entrant acquisitions inside
        # clear_active_job() and on_job_start() (called by process_next_queued_job)
        # work without deadlock.
        with _coordinator_lock:
            if not self.clear_active_job():
                logger.error(f"[COORDINATOR] Failed to clear active job state for {job_id}")
                return False
            self.process_next_queued_job()
        
        return True
    
    def process_next_queued_job(self) -> Optional[Dict]:
        """
        Find and process the next queued job that can now run.
        
        Returns:
            Job dict if found and triggered, None otherwise
        """
        if not self.supabase:
            return None
        
        try:
            # Get all blocked jobs — includes both 'pending' (blocked before execution started)
            # and 'pending_retry' (blocked during a resume attempt after a retryable failure).
            # Using 'pending' alone caused pending_retry re-blocked jobs to be permanently stuck.
            response = self.supabase.table('jobs').select('*')\
                .in_('status', ['pending', 'pending_retry'])\
                .not_.is_('blocked_by_job_id', 'null')\
                .order('queued_at', desc=False)\
                .limit(50)\
                .execute()
            
            queued_jobs = response.data if response and response.data else []
            
            if not queued_jobs:
                logger.info("[COORDINATOR] No queued jobs found")
                return None
            
            logger.info(f"[COORDINATOR] Found {len(queued_jobs)} queued job(s), checking for next eligible job...")
            
            # Find first job that can start
            for job in queued_jobs:
                job_id = job.get('job_id')
                job_type = job.get('job_type', 'image')
                required_models = job.get('required_models', [])
                
                # If no models stored, extract from job
                if not required_models and job_type != 'workflow':
                    required_models = [self.get_job_model(job)]
                
                # Pre-claim the coordinator slot BEFORE spawning the processing thread.
                # This closes the race window where a new incoming job (arriving via
                # Supabase Realtime between _trigger_job_processing() returning and the
                # spawned thread calling on_job_start()) could see an empty active slot
                # and start concurrently.  The spawned thread's own on_job_start() call
                # will hit the self-reservation check in can_start_job() and be allowed
                # through without re-claiming or re-logging.
                start_result = self.on_job_start(job_id, job_type, required_models)

                if start_result['allowed']:
                    logger.info(f"[COORDINATOR] Slot pre-claimed for next queued job: {job_id}")
                    self._trigger_job_processing(job)
                    return job
                
                # on_job_start returned not-allowed — this shouldn't happen since we just
                # cleared the active slot, but guard against it anyway.
                logger.warning(f"[COORDINATOR] Unexpected block for queued job {job_id}: {start_result['reason']}")
            
            logger.info("[COORDINATOR] No queued jobs can start yet (all still have conflicts)")
            return None
            
        except Exception as e:
            logger.error(f"[COORDINATOR] Error processing next queued job: {e}")
            return None
    
    def _trigger_job_processing(self, job: Dict):
        """
        Trigger job processing for the next queued job.
        Routes workflow jobs to the workflow retry/resume system.
        Routes image/video jobs to the normal job worker.
        
        Args:
            job: Job dict to process
        """
        job_id = job.get('job_id')
        job_type = job.get('job_type', 'image')
        logger.info(f"[COORDINATOR] Triggering processing for job {job_id} (type: {job_type})")

        if job_type == 'workflow':
            try:
                import asyncio
                import threading as _threading
                from workflow_retry_manager import get_retry_manager

                retry_manager = get_retry_manager()
                execution_id = None

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
                    # No execution record: this workflow was blocked by the coordinator BEFORE
                    # base_workflow.execute() had a chance to create it. Re-trigger it as a
                    # brand-new execution (not a resume).
                    logger.info(f"[COORDINATOR] No execution record for workflow {job_id} - re-triggering as fresh execution")
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
                            img = job_data.get('image_url') or meta.get('input_image_url')
                            wm = get_workflow_manager()
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

        try:
            from job_worker_realtime import process_job_with_concurrency_control
            import threading

            job_thread = threading.Thread(
                target=process_job_with_concurrency_control,
                args=(job,),
                daemon=True
            )
            job_thread.start()

        except Exception as e:
            logger.error(f"[COORDINATOR] Error triggering job processing: {e}")


# =========================================================================
# SINGLETON INSTANCE
# =========================================================================

_coordinator_instance: Optional[JobCoordinator] = None

def get_job_coordinator() -> JobCoordinator:
    """Get singleton job coordinator instance"""
    global _coordinator_instance
    if _coordinator_instance is None:
        _coordinator_instance = JobCoordinator()
    return _coordinator_instance
