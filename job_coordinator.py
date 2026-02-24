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

# Global lock for thread-safe operations
_coordinator_lock = threading.Lock()

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
        
        # Check if job can start
        check_result = self.can_start_job(job_id, job_type, required_models)
        
        if check_result['can_start']:
            # Mark as active
            if self.set_active_job(job_id, job_type, required_models):
                # Clear any previous queue info
                self.clear_job_queue_info(job_id)
                
                # Log event
                self.log_queue_event(job_id, job_type, 'started', models=required_models)
                
                return {
                    "allowed": True,
                    "reason": check_result['reason'],
                    "action": "start"
                }
            else:
                return {
                    "allowed": False,
                    "reason": "Failed to update global state",
                    "action": "error"
                }
        else:
            # Job is blocked - mark as queued
            self.mark_job_queued(
                job_id,
                check_result['blocked_by'],
                check_result['reason'],
                required_models
            )
            
            # Log event
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
        
        # Log completion event
        self.log_queue_event(job_id, job_type, 'completed')
        
        # Clear active job state
        if not self.clear_active_job():
            logger.error(f"[COORDINATOR] Failed to clear active job state for {job_id}")
            return False
        
        # Process next queued job
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
            # Get all pending jobs with queue info (blocked jobs)
            response = self.supabase.table('jobs').select('*')\
                .eq('status', 'pending')\
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
                if not required_models:
                    if job_type == 'workflow':
                        # Need to get workflow config - skip for now
                        logger.warning(f"[COORDINATOR] Workflow {job_id} has no required_models stored")
                        continue
                    else:
                        required_models = [self.get_job_model(job)]
                
                # Check if this job can start now
                check = self.can_start_job(job_id, job_type, required_models)
                
                if check['can_start']:
                    logger.info(f"[COORDINATOR] Starting next queued job: {job_id}")
                    
                    # Trigger job processing
                    self._trigger_job_processing(job)
                    
                    return job
            
            logger.info("[COORDINATOR] No queued jobs can start yet (all still have conflicts)")
            return None
            
        except Exception as e:
            logger.error(f"[COORDINATOR] Error processing next queued job: {e}")
            return None
    
    def _trigger_job_processing(self, job: Dict):
        """
        Trigger job processing (call job worker).
        This is a placeholder - actual implementation depends on your job processing system.
        
        Args:
            job: Job dict to process
        """
        job_id = job.get('job_id')
        logger.info(f"[COORDINATOR] Triggering processing for job {job_id}")
        
        # Import here to avoid circular dependency
        try:
            from job_worker_realtime import process_job_with_concurrency_control
            import threading
            
            # Start job in new thread
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
