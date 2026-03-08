import asyncio
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Cap concurrent workflow execution threads spawned by the retry manager.
# Without this, a large backlog of pending_retry jobs could spawn hundreds
# of threads simultaneously on startup or in a busy retry cycle.
_workflow_thread_semaphore = threading.BoundedSemaphore(10)


def _spawn_workflow_thread(target_fn, name: str = "WorkflowThread"):
    """
    Throttled wrapper for spawning a workflow execution thread.
    Silently drops the spawn attempt if the semaphore is full (the job
    will be retried in the next periodic cycle).
    """
    def _guarded():
        if not _workflow_thread_semaphore.acquire(timeout=5):
            logging.getLogger(__name__).warning(
                f"[SEMAPHORE] Workflow thread limit reached — skipping spawn for {name}. "
                f"Job will be retried in the next cycle."
            )
            return
        try:
            target_fn()
        finally:
            _workflow_thread_semaphore.release()

    threading.Thread(target=_guarded, daemon=True, name=name).start()

from supabase_client import supabase
from model_quota_manager import get_quota_manager
from provider_api_keys import get_provider_api_key
from error_notifier import notify_error, ErrorType

MAINTENANCE_FLAG = Path(__file__).parent / ".maintenance_mode"

def _is_maintenance_mode() -> bool:
    return MAINTENANCE_FLAG.exists()

logger = logging.getLogger(__name__)

RETRY_BACKOFF_SECONDS = {
    'quota_exceeded': 300,
    'rate_limit': 60,
    'timeout': 30,
    'invalid_key': 120,
    'no_api_key': 120,
    'generic_api_error': 180,
    'api_error': 180,
}
DEFAULT_RETRY_BACKOFF = 180

class WorkflowRetryManager:
    def __init__(self):
        self.retry_interval = 300
        self.max_retries = 5
        self.running = False
        self.loop: Optional[asyncio.AbstractEventLoop] = None
    
    async def start(self):
        self.running = True
        logger.info("Workflow retry manager started")
        
        while self.running:
            try:
                await self.retry_pending_workflows()
                await self.retry_stale_pending_workflows()
            except Exception as e:
                logger.error(f"Retry loop error: {e}", exc_info=True)
            
            await asyncio.sleep(self.retry_interval)
    
    async def stop(self):
        self.running = False
        logger.info("Workflow retry manager stopped")
    
    async def retry_stale_pending_workflows(self):
        """
        Sweep for fresh workflow jobs (status=pending) that have been sitting untouched
        for more than 2 minutes — meaning the realtime INSERT event was missed.
        These are re-triggered as brand-new executions (not resumes).
        """
        if _is_maintenance_mode():
            return

        try:
            from datetime import timezone, timedelta
            stale_cutoff = (datetime.utcnow() - timedelta(minutes=2)).isoformat()

            jobs_response = supabase.table('jobs')\
                .select('*')\
                .eq('status', 'pending')\
                .eq('job_type', 'workflow')\
                .is_('blocked_by_job_id', 'null')\
                .lt('created_at', stale_cutoff)\
                .execute()

            jobs = jobs_response.data if jobs_response.data else []

            if not jobs:
                logger.debug("No stale pending workflow jobs found")
                return

            logger.info(f"Found {len(jobs)} stale pending workflow job(s) — re-triggering")

            for job in jobs:
                try:
                    job_id = job['job_id']
                    workflow_id = job.get('model')
                    user_id = job.get('user_id')
                    meta = job.get('metadata', {}) or {}
                    image_url = job.get('image_url') or meta.get('input_image_url')

                    # Skip jobs stuck on key errors — those wait for API key insertion only
                    error_msg = (job.get('error_message') or '').lower()
                    _key_markers = ('no api key', 'invalid key', 'api key rotation failed',
                                    'authentication', 'unauthorized')
                    if any(k in error_msg for k in _key_markers):
                        logger.debug(f"Stale pending workflow {job_id} has key error — skipping, waiting for key insertion")
                        continue

                    logger.info(f"Re-triggering stale pending workflow {workflow_id} for job {job_id}")

                    def _run(wf_id=workflow_id, img=image_url, uid=user_id, jid=job_id):
                        from workflow_manager import get_workflow_manager
                        import asyncio as _asyncio
                        loop = _asyncio.new_event_loop()
                        _asyncio.set_event_loop(loop)
                        try:
                            wm = get_workflow_manager()
                            loop.run_until_complete(wm.execute_workflow(
                                workflow_id=wf_id,
                                input_data=img,
                                user_id=uid,
                                job_id=jid
                            ))
                        except Exception as _e:
                            logger.error(f"Stale workflow re-trigger failed for {jid}: {_e}")
                        finally:
                            loop.close()

                    _spawn_workflow_thread(_run, name=f"StaleWF-{job_id}")

                except Exception as e:
                    logger.error(f"Error re-triggering stale workflow job {job.get('job_id')}: {e}")

        except Exception as e:
            logger.error(f"Error sweeping stale pending workflows: {e}", exc_info=True)

    async def retry_pending_workflows(self):
        if _is_maintenance_mode():
            logger.info("Skipping workflow retry - maintenance mode active")
            return

        try:
            # Query jobs with pending_retry status.
            # Exclude coordinator-blocked jobs (blocked_by_job_id IS NOT NULL) — those are
            # handled exclusively by job_coordinator.process_next_queued_job() when the
            # blocking job finishes.  Processing them here as well would cause double
            # execution attempts and incorrect retry-count increments.
            jobs_response = supabase.table('jobs')\
                .select('*')\
                .eq('status', 'pending_retry')\
                .eq('job_type', 'workflow')\
                .is_('blocked_by_job_id', 'null')\
                .execute()
            
            jobs = jobs_response.data if jobs_response.data else []
            
            if not jobs:
                logger.debug("No pending retry workflows found")
                return
            
            # Get job IDs
            job_ids = [job['job_id'] for job in jobs]
            
            # Query workflow_executions for these jobs
            executions_response = supabase.table('workflow_executions')\
                .select('*')\
                .in_('job_id', job_ids)\
                .execute()
            
            executions_data = executions_response.data if executions_response.data else []
            
            # Create a map of job_id to execution
            executions_map = {exec['job_id']: exec for exec in executions_data}
            
            logger.info(f"Found {len(jobs)} workflows pending retry")
            
            for job in jobs:
                try:
                    job_id = job['job_id']
                    execution = executions_map.get(job_id)
                    
                    if not execution:
                        logger.warning(f"Job {job_id} has no execution record")
                        continue
                    
                    retry_count = execution.get('retry_count', 0)
                    
                    if retry_count >= self.max_retries:
                        logger.warning(f"Max retries reached for job {job_id}")
                        notify_error(
                            ErrorType.JOB_PROCESSING_ERROR,
                            f"Workflow job exceeded max retries ({self.max_retries}) — marked as failed",
                            context={"job_id": job_id, "retry_count": retry_count}
                        )
                        await self._mark_failed(job_id, "Maximum retry attempts exceeded")
                        continue
                    
                    can_retry = await self._can_retry(execution)
                    
                    if can_retry:
                        logger.info(f"Retrying workflow for job {job_id}")
                        # Spawn a thread rather than awaiting directly.
                        # Awaiting _resume_workflow() blocks the retry loop's event
                        # loop for the entire duration of the workflow execution
                        # (potentially many minutes), causing all other pending_retry
                        # jobs to starve until the slow workflow finishes.
                        exec_id = execution['id']
                        def _do_resume(eid=exec_id, jid=job_id):
                            import asyncio as _asyncio
                            loop = _asyncio.new_event_loop()
                            _asyncio.set_event_loop(loop)
                            try:
                                loop.run_until_complete(self._resume_workflow(eid, jid))
                            finally:
                                loop.close()
                        _spawn_workflow_thread(_do_resume, name=f"RetryWF-{job_id}")
                    else:
                        logger.debug(f"Conditions not met for retry: {job_id}")
                
                except Exception as e:
                    logger.error(f"Error processing job {job.get('job_id')}: {e}", exc_info=True)
        
        except Exception as e:
            logger.error(f"Error fetching pending retry jobs: {e}", exc_info=True)
    
    async def _can_retry(self, execution: dict) -> bool:
        error_info = execution.get('error_info', {}) or {}
        error_type = error_info.get('error_type')
        last_attempt = error_info.get('last_attempt')

        required_backoff = RETRY_BACKOFF_SECONDS.get(error_type, DEFAULT_RETRY_BACKOFF)
        if last_attempt:
            try:
                last_attempt_dt = datetime.fromisoformat(last_attempt.replace('Z', '+00:00'))
                elapsed = (datetime.utcnow() - last_attempt_dt.replace(tzinfo=None)).total_seconds()
                if elapsed < required_backoff:
                    logger.debug(f"Backoff not elapsed for {error_type}: {elapsed:.0f}s / {required_backoff}s")
                    return False
            except Exception:
                pass

        if error_type == 'quota_exceeded':
            # Quota buckets reset daily. Retrying sooner than 24 hours wastes a
            # retry slot and burns through max_retries (5 × 5 min = 25 min) before
            # the quota has had a chance to reset — permanently failing the workflow.
            if last_attempt:
                try:
                    import asyncio as _asyncio
                    last_attempt_dt = datetime.fromisoformat(last_attempt.replace('Z', '+00:00'))
                    elapsed_24h = (datetime.utcnow() - last_attempt_dt.replace(tzinfo=None)).total_seconds()
                    if elapsed_24h < 86400:  # 24 hours
                        logger.debug(
                            f"Quota-exceeded workflow retry skipped — last attempt was "
                            f"{elapsed_24h/3600:.1f}h ago, waiting for 24h quota reset"
                        )
                        return False
                except Exception:
                    pass  # if parse fails, fall through to quota check
            model = error_info.get('model')
            return await self._check_quota_available(model)

        elif error_type == 'invalid_key':
            # Never retry on the periodic timer — only the api_key_realtime_listener
            # will re-trigger this job when a replacement key is inserted.
            provider = error_info.get('provider')
            logger.debug(
                f"Workflow pending_retry skipped in timer loop (invalid_key for '{provider}') "
                f"— will retry only on API key insertion"
            )
            return False

        elif error_type == 'no_api_key':
            # Same as invalid_key: must wait for key insertion, not periodic timer.
            provider = error_info.get('provider')
            logger.debug(
                f"Workflow pending_retry skipped in timer loop (no_api_key for '{provider}') "
                f"— will retry only on API key insertion"
            )
            return False

        elif error_type == 'rate_limit':
            retry_after = error_info.get('retry_after', 60)
            if last_attempt:
                try:
                    last_attempt_dt = datetime.fromisoformat(last_attempt.replace('Z', '+00:00'))
                    elapsed = (datetime.utcnow() - last_attempt_dt.replace(tzinfo=None)).total_seconds()
                    return elapsed >= retry_after
                except Exception:
                    return True
            return True

        elif error_type == 'timeout':
            return True

        elif error_type in ('generic_api_error', 'api_error'):
            return True

        logger.warning(f"Unknown or missing workflow error_type '{error_type}' - allowing retry attempt")
        return True
    
    async def _check_quota_available(self, model: Optional[str]) -> bool:
        if not model:
            return True
        
        try:
            quota_manager = get_quota_manager()
            return quota_manager.has_quota(model)
        except:
            return True
    
    async def _check_api_key_valid(self, provider: Optional[str]) -> bool:
        if not provider:
            return True
        
        try:
            key = get_provider_api_key(provider)
            return key is not None
        except:
            return True
    
    async def _resume_workflow(self, execution_id: str, job_id: str):
        from workflow_manager import get_workflow_manager
        
        workflow_manager = get_workflow_manager()
        
        try:
            await workflow_manager.resume_workflow(
                execution_id=execution_id,
                job_id=job_id
            )
        except Exception as e:
            logger.error(f"Failed to resume workflow {execution_id}: {e}", exc_info=True)
    
    async def _mark_failed(self, job_id: str, reason: str):
        try:
            supabase.table('jobs').update({
                'status': 'failed',
                'error_message': reason,
                'updated_at': datetime.utcnow().isoformat()
            }).eq('job_id', job_id).execute()
            
            logger.info(f"Marked job {job_id} as failed: {reason}")
        except Exception as e:
            logger.error(f"Failed to mark job {job_id} as failed: {e}")
    
    def process_pending_workflows(self) -> int:
        """Process pending workflows (called at startup). Returns count of workflows processed."""
        try:
            from workflow_manager import get_workflow_manager
            
            # Only process unblocked pending workflows.  Coordinator-blocked jobs
            # (blocked_by_job_id IS NOT NULL) will be triggered by the coordinator
            # once their blocking job finishes — re-triggering them here as well
            # would cause duplicate execution attempts.
            response = supabase.table('jobs')\
                .select('*')\
                .eq('job_type', 'workflow')\
                .eq('status', 'pending')\
                .is_('blocked_by_job_id', 'null')\
                .execute()
            
            jobs = response.data if response.data else []
            
            if not jobs:
                return 0
            
            logger.info(f"Found {len(jobs)} pending workflow jobs")
            print(f"📋 Found {len(jobs)} pending workflow job(s)")
            
            workflow_manager = get_workflow_manager()
            count = 0
            
            for job in jobs:
                try:
                    job_id = job['job_id']
                    workflow_id = job.get('model')
                    user_id = job.get('user_id')
                    metadata = job.get('metadata', {}) or {}
                    image_url = job.get('image_url') or metadata.get('input_image_url')

                    # Check whether this workflow actually requires an input image.
                    # Defaults to True for all current workflows; future workflows may
                    # set requires_input_image=False in their config.
                    workflow_config = workflow_manager.get_workflow(workflow_id) or {}
                    requires_image = workflow_config.get('requires_input_image', True)

                    if requires_image and not image_url:
                        print(f"   ⚠️  Failing workflow {workflow_id} for job {job_id} - no input image required by workflow config")
                        supabase.table('jobs').update({
                            'status': 'failed',
                            'error_message': 'No input image provided for workflow'
                        }).eq('job_id', job_id).execute()
                        continue

                    # Full input validation (catches missing checkpoint outputs for resumed jobs)
                    try:
                        from job_worker_realtime import validate_job_inputs
                        if not validate_job_inputs(job):
                            print(f"   ⚠️  Skipping workflow {job_id} - input validation failed")
                            continue
                    except Exception as _val_err:
                        print(f"   ⚠️  validate_job_inputs error for {job_id}: {_val_err} — proceeding anyway")
                    
                    img_preview = image_url[:50] if image_url else "(no image)"
                    print(f"   Starting workflow {workflow_id} for job {job_id} with image: {img_preview}...")
                    
                    def run_workflow(wf_id=workflow_id, img=image_url, uid=user_id, jid=job_id):
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            loop.run_until_complete(
                                workflow_manager.execute_workflow(
                                    workflow_id=wf_id,
                                    input_data=img,
                                    user_id=uid,
                                    job_id=jid
                                )
                            )
                        except Exception as e:
                            logger.error(f"Workflow execution error for job {jid}: {e}")
                        finally:
                            loop.close()
                    
                    _spawn_workflow_thread(run_workflow, name=f"PendingWF-{job_id}")
                    count += 1
                    
                except Exception as e:
                    logger.error(f"Error processing pending workflow job {job.get('job_id')}: {e}")
            
            return count
            
        except Exception as e:
            logger.error(f"Error fetching pending workflow jobs: {e}", exc_info=True)
            return 0
    
    def process_retryable_workflows(self) -> int:
        """Process pending_retry workflows (called at startup). Returns count of workflows processed.
        
        Respects the same backoff/can_retry logic as the periodic retry loop so that
        quota-exceeded or rate-limited workflows are not hammered immediately on restart.
        """
        try:
            # Get pending_retry workflow jobs that are NOT coordinator-blocked.
            # Blocked jobs are handled by the coordinator when their blocking job completes.
            jobs_response = supabase.table('jobs')\
                .select('*')\
                .eq('job_type', 'workflow')\
                .eq('status', 'pending_retry')\
                .is_('blocked_by_job_id', 'null')\
                .execute()
            
            jobs = jobs_response.data if jobs_response.data else []
            
            if not jobs:
                return 0
            
            # Get workflow_executions for these jobs
            job_ids = [job['job_id'] for job in jobs]
            executions_response = supabase.table('workflow_executions')\
                .select('*')\
                .in_('job_id', job_ids)\
                .execute()
            
            executions_map = {ex['job_id']: ex for ex in (executions_response.data or [])}
            
            # Attach executions to jobs
            for job in jobs:
                job['workflow_executions'] = executions_map.get(job['job_id'])
            
            logger.info(f"Found {len(jobs)} workflows pending retry")
            print(f"🔄 Found {len(jobs)} workflow job(s) pending retry")
            
            count = 0
            
            for job in jobs:
                try:
                    job_id = job['job_id']
                    execution = job.get('workflow_executions')
                    
                    if not execution:
                        logger.warning(f"Job {job_id} has no execution record")
                        continue
                    
                    if isinstance(execution, list):
                        execution = execution[0] if execution else None
                    
                    if not execution:
                        continue
                    
                    execution_id = execution.get('id')

                    # Respect backoff — same logic as the periodic retry loop.
                    # Prevents hammering quota-exceeded / rate-limited providers on restart.
                    try:
                        _check_loop = asyncio.new_event_loop()
                        can_retry = _check_loop.run_until_complete(self._can_retry(execution))
                        _check_loop.close()
                    except Exception as _check_err:
                        logger.warning(f"_can_retry check failed for {job_id}: {_check_err} — allowing retry")
                        can_retry = True

                    if not can_retry:
                        print(f"   ⏳ Skipping {job_id} — backoff not elapsed yet (will retry in periodic loop)")
                        continue

                    print(f"   Retrying workflow for job {job_id}")
                    
                    # Run async resume in thread
                    def resume(exec_id=execution_id, jid=job_id):
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            loop.run_until_complete(
                                self._resume_workflow(exec_id, jid)
                            )
                        finally:
                            loop.close()
                    
                    _spawn_workflow_thread(resume, name=f"RetryWF-{job_id}")
                    count += 1
                    
                except Exception as e:
                    logger.error(f"Error processing retry workflow {job.get('job_id')}: {e}")
            
            return count
            
        except Exception as e:
            logger.error(f"Error fetching pending_retry workflows: {e}", exc_info=True)
            return 0

_retry_manager_instance: Optional[WorkflowRetryManager] = None
_retry_thread: Optional[threading.Thread] = None

def get_retry_manager() -> WorkflowRetryManager:
    global _retry_manager_instance
    if _retry_manager_instance is None:
        _retry_manager_instance = WorkflowRetryManager()
    return _retry_manager_instance

def start_retry_manager():
    global _retry_thread
    
    if _retry_thread is not None and _retry_thread.is_alive():
        logger.info("Retry manager already running")
        return
    
    manager = get_retry_manager()
    
    def run_async_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        manager.loop = loop
        try:
            loop.run_until_complete(manager.start())
        finally:
            loop.close()
    
    _retry_thread = threading.Thread(target=run_async_loop, daemon=True, name="WorkflowRetryManager")
    _retry_thread.start()
    
    logger.info("Workflow retry manager thread started")
    return manager

def stop_retry_manager():
    manager = get_retry_manager()
    
    if manager.loop:
        manager.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(manager.stop()))
    
    logger.info("Workflow retry manager stop requested")
