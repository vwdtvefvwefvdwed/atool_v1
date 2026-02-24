import asyncio
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

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
            except Exception as e:
                logger.error(f"Retry loop error: {e}", exc_info=True)
            
            await asyncio.sleep(self.retry_interval)
    
    async def stop(self):
        self.running = False
        logger.info("Workflow retry manager stopped")
    
    async def retry_pending_workflows(self):
        if _is_maintenance_mode():
            logger.info("Skipping workflow retry - maintenance mode active")
            return

        try:
            # Query jobs with pending_retry status
            jobs_response = supabase.table('jobs')\
                .select('*')\
                .eq('status', 'pending_retry')\
                .eq('job_type', 'workflow')\
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
                        await self._resume_workflow(execution['id'], job_id)
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
            model = error_info.get('model')
            return await self._check_quota_available(model)

        elif error_type == 'invalid_key':
            provider = error_info.get('provider')
            has_key = await self._check_api_key_valid(provider)
            if not has_key:
                notify_error(
                    ErrorType.API_KEY_ROTATION_FAILED,
                    f"Workflow retry blocked: invalid/no key for provider '{provider}'",
                    context={"provider": provider}
                )
            return has_key

        elif error_type == 'no_api_key':
            provider = error_info.get('provider')
            has_key = await self._check_api_key_valid(provider)
            if not has_key:
                notify_error(
                    ErrorType.NO_API_KEY_FOR_PROVIDER,
                    f"Workflow retry blocked: no API key for provider '{provider}'",
                    context={"provider": provider}
                )
            return has_key

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

        logger.warning(f"Unknown workflow error_type '{error_type}' - will NOT auto-retry")
        return False
    
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
            
            response = supabase.table('jobs')\
                .select('*')\
                .eq('job_type', 'workflow')\
                .eq('status', 'pending')\
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
                    
                    # Skip workflows that require input but don't have it
                    if not image_url:
                        print(f"   ⚠️  Skipping workflow {workflow_id} for job {job_id} - no input image")
                        # Mark as failed since we can't process without input
                        supabase.table('jobs').update({
                            'status': 'failed',
                            'error_message': 'No input image provided for workflow'
                        }).eq('job_id', job_id).execute()
                        continue
                    
                    print(f"   Starting workflow {workflow_id} for job {job_id} with image: {image_url[:50]}...")
                    
                    def run_workflow():
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            loop.run_until_complete(
                                workflow_manager.execute_workflow(
                                    workflow_id=workflow_id,
                                    input_data=image_url,
                                    user_id=user_id,
                                    job_id=job_id
                                )
                            )
                        except Exception as e:
                            logger.error(f"Workflow execution error for job {job_id}: {e}")
                        finally:
                            loop.close()
                    
                    thread = threading.Thread(target=run_workflow, daemon=True)
                    thread.start()
                    count += 1
                    
                except Exception as e:
                    logger.error(f"Error processing pending workflow job {job.get('job_id')}: {e}")
            
            return count
            
        except Exception as e:
            logger.error(f"Error fetching pending workflow jobs: {e}", exc_info=True)
            return 0
    
    def process_retryable_workflows(self) -> int:
        """Process pending_retry workflows (called at startup). Returns count of workflows processed."""
        try:
            # Get pending_retry workflow jobs
            jobs_response = supabase.table('jobs')\
                .select('*')\
                .eq('job_type', 'workflow')\
                .eq('status', 'pending_retry')\
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
                    
                    print(f"   Retrying workflow for job {job_id}")
                    
                    # Run async resume in thread
                    def resume():
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            loop.run_until_complete(
                                self._resume_workflow(execution_id, job_id)
                            )
                        finally:
                            loop.close()
                    
                    thread = threading.Thread(target=resume, daemon=True)
                    thread.start()
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
