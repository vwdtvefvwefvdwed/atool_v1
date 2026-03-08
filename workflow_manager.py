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
        
        # Check with coordinator if workflow can start
        start_result = coordinator.on_job_start(job_id, "workflow", required_models)
        
        if not start_result['allowed']:
            # Workflow is blocked — coordinator already set blocked_by_job_id on the jobs row.
            # DO NOT try to UPDATE workflow_executions here: the execution record does not exist
            # yet (it is created inside base_workflow.execute). An UPDATE on a non-existent row
            # is a silent no-op that leaves the job permanently stuck when the blocking job
            # finishes and the coordinator tries to resume via execution_id.
            logger.warning(f"Workflow {job_id} blocked: {start_result['reason']}")
            raise RuntimeError(f"Workflow queued: {start_result['reason']}")
        
        logger.info(f"Workflow {job_id} allowed to start: {start_result['reason']}")
        
        # Wrap everything after on_job_start() in try/finally so the coordinator
        # slot is ALWAYS released — even if workflow_class() instantiation or the
        # workflow_executions UPDATE raises before the inner try block is reached.
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
            raise
        finally:
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
                if current_status not in ('pending_retry', 'pending'):
                    logger.info(
                        f"[RESUME] Skipping resume for {job_id} — "
                        f"status is '{current_status}', not resumable"
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
        
        # Check with coordinator if workflow can resume
        start_result = coordinator.on_job_start(job_id, "workflow", required_models)
        
        if not start_result['allowed']:
            # Workflow is blocked
            logger.warning(f"Workflow resume blocked for {job_id}: {start_result['reason']}")
            
            # Update workflow_executions table with blocking info
            try:
                supabase.table('workflow_executions').update({
                    'required_models': required_models,
                    'blocked_by_job_id': start_result.get('blocked_by'),
                    'status': 'pending_retry'
                }).eq('id', execution_id).execute()
            except Exception as e:
                logger.error(f"Failed to update workflow execution: {e}")
            
            # Raise exception to indicate workflow is queued
            raise RuntimeError(f"Workflow resume queued: {start_result['reason']}")
        
        logger.info(f"Workflow {job_id} allowed to resume: {start_result['reason']}")

        # From here, the coordinator slot is claimed. Wrap everything in
        # try/finally so on_job_complete() is guaranteed to run even if an
        # exception occurs before the inner try block (e.g. DB lookup failure,
        # workflow class instantiation error).
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
            raise
        finally:
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
