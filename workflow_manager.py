import logging
from typing import Dict, List, Optional, Any, Callable
import asyncio

from workflows import get_all_workflows, get_workflow_class, reload_workflows
from supabase_client import supabase
from job_coordinator import get_job_coordinator

logger = logging.getLogger(__name__)

class WorkflowManager:
    def __init__(self):
        self.workflows = {}
        self._load_workflows()
    
    def _load_workflows(self):
        workflows = reload_workflows()
        self.workflows = workflows
        logger.info(f"Loaded {len(workflows)} workflows")
    
    def list_workflows(self) -> List[Dict]:
        return get_all_workflows()
    
    def get_workflow(self, workflow_id: str) -> Optional[Dict]:
        workflows = get_all_workflows()
        for workflow in workflows:
            if workflow['id'] == workflow_id:
                return workflow
        return None
    
    async def execute_workflow(
        self, 
        workflow_id: str, 
        input_data: Any, 
        user_id: str, 
        job_id: str,
        progress_callback: Optional[Callable] = None
    ) -> Any:
        workflow_class = get_workflow_class(workflow_id)
        
        if not workflow_class:
            raise ValueError(f"Workflow {workflow_id} not found")
        
        workflow_config = self.get_workflow(workflow_id)
        
        # Extract required models from workflow config
        coordinator = get_job_coordinator()
        required_models = coordinator.get_workflow_models(workflow_config)
        
        logger.info(f"Executing workflow {workflow_id} for job {job_id} - Models: {required_models}")

        coordinator_slot_claimed = False  # set True only after on_job_start succeeds
        # Check with coordinator if workflow can start
        start_result = coordinator.on_job_start(job_id, "workflow", required_models)

        if not start_result['allowed']:
            # Workflow is blocked — coordinator already set blocked_by_job_id on the jobs row
            # and reset status running→pending so process_next_queued_job() can find it.
            # DO NOT call on_job_complete here: no slot was claimed, so releasing would be
            # a no-op and would trigger a spurious process_next_queued_job() call.
            logger.warning(f"Workflow {job_id} blocked: {start_result['reason']}")
            raise RuntimeError(f"Workflow queued: {start_result['reason']}")

        logger.info(f"Workflow {job_id} allowed to start: {start_result['reason']}")

        # Slot is now claimed.  Wrap the rest in try/finally so the slot is
        # ALWAYS released — even if instantiation or DB writes raise before execute().
        coordinator_slot_claimed = True
        try:
            # Store required_models in workflow_executions table
            try:
                supabase.table('workflow_executions').update({
                    'required_models': required_models,
                    'blocked_by_job_id': None
                }).eq('job_id', job_id).execute()
            except Exception as e:
                logger.warning(f"Failed to update workflow execution models: {e}")
            
            workflow_instance = workflow_class(workflow_config)
            
            result = await workflow_instance.execute(
                input_data=input_data,
                user_id=user_id,
                job_id=job_id,
                resume=False,
                progress_callback=progress_callback
            )
            
            logger.info(f"Workflow {job_id} completed, notifying coordinator...")
            return result
        except Exception as e:
            logger.error(f"Workflow {job_id} failed: {e}")
            # Infrastructure failure (e.g. socket error, DNS failure) happened
            # before the workflow instance could even start executing.
            # Reset the job to pending_retry so the periodic retry sweep picks it up.
            try:
                from jobs import update_job_status
                update_job_status(job_id, 'pending_retry', {
                    'error': f"Infrastructure error before execution: {str(e)[:200]}",
                    'can_resume': True,
                    'retryable': True
                })
                logger.warning(f"[WORKFLOW_MANAGER] Job {job_id} reset to pending_retry after pre-execution error: {e}")
            except Exception as _status_err:
                logger.error(f"[WORKFLOW_MANAGER] Could not reset job {job_id} to pending_retry: {_status_err}")
            raise
        finally:
            if coordinator_slot_claimed:
                coordinator.on_job_complete(job_id, "workflow")
    
    async def resume_workflow(
        self, 
        execution_id: str, 
        job_id: str,
        progress_callback: Optional[Callable] = None
    ) -> Any:
        # Guard against duplicate resume calls (can happen when the periodic retry
        # loop and the coordinator trigger fire at nearly the same time).
        # Only resume if the job is still in a resumable state.
        try:
            job_check = supabase.table('jobs')\
                .select('status')\
                .eq('job_id', job_id)\
                .single()\
                .execute()
            if job_check.data:
                current_status = job_check.data.get('status')
                if current_status in ('completed', 'failed'):
                    logger.info(
                        f"[RESUME] Skipping resume for {job_id} — "
                        f"status is '{current_status}', already terminal"
                    )
                    return None
        except Exception as status_err:
            logger.warning(f"[RESUME] Could not verify status for {job_id}: {status_err} — proceeding")

        response = supabase.table('workflow_executions')\
            .select('*')\
            .eq('id', execution_id)\
            .single()\
            .execute()
        
        if not response.data:
            raise ValueError(f"Execution {execution_id} not found")
        
        execution = response.data
        workflow_id = execution['workflow_id']
        user_id = execution['user_id']
        
        workflow_class = get_workflow_class(workflow_id)
        
        if not workflow_class:
            raise ValueError(f"Workflow {workflow_id} not found")
        
        workflow_config = self.get_workflow(workflow_id)
        
        # Extract required models from workflow config
        coordinator = get_job_coordinator()
        required_models = execution.get('required_models')
        
        # If not stored, extract from config
        if not required_models:
            required_models = coordinator.get_workflow_models(workflow_config)
        
        logger.info(f"Resuming workflow {workflow_id} from execution {execution_id} - Models: {required_models}")

        coordinator_slot_claimed = False  # set True only after on_job_start succeeds
        # Check with coordinator if workflow can resume
        start_result = coordinator.on_job_start(job_id, "workflow", required_models)

        if not start_result['allowed']:
            # Workflow resume is blocked — coordinator already set blocked_by_job_id and
            # reset status running→pending.  Do NOT call on_job_complete (no slot was claimed).
            logger.warning(f"Workflow resume blocked for {job_id}: {start_result['reason']}")

            try:
                supabase.table('workflow_executions').update({
                    'required_models':   required_models,
                    'blocked_by_job_id': start_result.get('blocked_by'),
                    'status':            'pending_retry'
                }).eq('id', execution_id).execute()
            except Exception as e:
                logger.error(f"Failed to update workflow execution: {e}")

            raise RuntimeError(f"Workflow resume queued: {start_result['reason']}")

        logger.info(f"Workflow {job_id} allowed to resume: {start_result['reason']}")

        # Slot claimed — wrap in try/finally so on_job_complete() always fires.
        coordinator_slot_claimed = True
        try:
            return await self._execute_resume(
                execution_id=execution_id,
                job_id=job_id,
                execution=execution,
                workflow_id=workflow_id,
                user_id=user_id,
                workflow_class=workflow_class,
                workflow_config=workflow_config,
                required_models=required_models,
                progress_callback=progress_callback,
            )
        except Exception as e:
            logger.error(f"Workflow {job_id} resume failed: {e}")
            # Same infrastructure failure guard — reset to pending_retry
            try:
                from jobs import update_job_status
                update_job_status(job_id, 'pending_retry', {
                    'error': f"Infrastructure error during resume: {str(e)[:200]}",
                    'can_resume': True,
                    'retryable': True
                })
                logger.warning(f"[WORKFLOW_MANAGER] Job {job_id} reset to pending_retry after resume error: {e}")
            except Exception as _status_err:
                logger.error(f"[WORKFLOW_MANAGER] Could not reset job {job_id} to pending_retry: {_status_err}")
            raise
        finally:
            if coordinator_slot_claimed:
                coordinator.on_job_complete(job_id, "workflow")

    async def _execute_resume(
        self,
        execution_id: str,
        job_id: str,
        execution: dict,
        workflow_id: str,
        user_id: str,
        workflow_class,
        workflow_config: dict,
        required_models,
        progress_callback=None,
    ):
        """Inner resume logic — called from resume_workflow() inside a try/finally that
        guarantees coordinator.on_job_complete() always fires."""
        # Load the original user input (image URL) from the jobs table so that
        # step 0 can use it if the workflow is resuming from the very first step.
        # base_workflow stores it in checkpoints['_input'] on first run and loads
        # it automatically on resume, but we pass it here as an extra safety net
        # for old executions that pre-date the checkpoints['_input'] storage.
        original_input = None
        try:
            job_row = supabase.table('jobs')\
                .select('image_url, metadata')\
                .eq('job_id', job_id)\
                .single()\
                .execute()
            if job_row.data:
                job_meta = job_row.data.get('metadata') or {}
                original_input = (
                    job_row.data.get('image_url') or
                    job_meta.get('input_image_url')
                )
        except Exception as fetch_err:
            logger.warning(f"Could not fetch original input for job {job_id}: {fetch_err}")

        workflow_instance = workflow_class(workflow_config)
        
        result = await workflow_instance.execute(
            input_data=original_input,
            user_id=user_id,
            job_id=job_id,
            resume=True,
            progress_callback=progress_callback
        )
        
        logger.info(f"Workflow {job_id} completed successfully")
        return result
    
    def reload(self):
        self._load_workflows()

_workflow_manager_instance: Optional[WorkflowManager] = None

def get_workflow_manager() -> WorkflowManager:
    global _workflow_manager_instance
    if _workflow_manager_instance is None:
        _workflow_manager_instance = WorkflowManager()
    return _workflow_manager_instance
