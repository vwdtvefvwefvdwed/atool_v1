import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, Optional, Callable
from abc import ABC, abstractmethod

from workflows.errors import RetryableError, HardError, WorkflowError
from supabase_client import supabase
from jobs import update_job_status
from error_notifier import notify_error, ErrorType

logger = logging.getLogger(__name__)

class BaseWorkflow(ABC):
    def __init__(self, config: Dict):
        self.config = config
        self.steps = config.get('steps', [])
        self.execution_id: Optional[str] = None
        self.checkpoints: Dict[str, Any] = {}
    
    async def execute(
        self, 
        input_data: Any, 
        user_id: str, 
        job_id: str, 
        resume: bool = False,
        progress_callback: Optional[Callable] = None
    ) -> Any:
        execution = await self._get_or_create_execution(job_id, user_id, resume, input_data)
        self.execution_id = execution['id']
        self.checkpoints = execution.get('checkpoints', {})
        
        start_step = execution['current_step'] if resume else 0
        
        logger.info(f"Starting workflow {self.config['id']} from step {start_step}")

        try:
            result = input_data
            
            await self._update_job_status(job_id, 'running', {})
            
            for i in range(start_step, len(self.steps)):
                step = self.steps[i]
                step_name = step.get('name', f'step_{i}')
                
                await self._update_execution(execution['id'], {
                    'current_step': i,
                    'status': 'running'
                })
                
                if progress_callback:
                    await progress_callback({
                        'step': i,
                        'step_name': step_name,
                        'total_steps': len(self.steps),
                        'progress': int((i / len(self.steps)) * 100),
                        'message': f"Executing {step_name}..."
                    })
                
                try:
                    logger.info(f"Executing step {i}: {step_name}")
                    result = await self._execute_step(step, i, execution, result)
                    
                    checkpoint_data = {
                        'step_name': step_name,
                        'step_type': step.get('type'),
                        'status': 'completed',
                        'output': result,
                        'started_at': datetime.utcnow().isoformat(),
                        'completed_at': datetime.utcnow().isoformat()
                    }
                    
                    await self._save_checkpoint(execution['id'], i, checkpoint_data)
                    
                    if step.get('type') == 'generation':
                        step_provider = step.get('provider')
                        step_model = (
                            result.get('model_used') if isinstance(result, dict) else None
                        ) or step.get('model') or step.get('default_model')
                        if step_provider and step_model:
                            try:
                                from model_quota_manager import get_quota_manager
                                quota_manager = get_quota_manager()
                                quota_result = quota_manager.increment_quota(step_provider, step_model)
                                if quota_result.get('success'):
                                    logger.info(f"[QUOTA] Incremented quota for {step_provider}:{step_model} after step '{step_name}'")
                                else:
                                    logger.warning(f"[QUOTA] Failed to increment quota for {step_provider}:{step_model}: {quota_result.get('reason', 'unknown')}")
                            except Exception as quota_err:
                                logger.warning(f"[QUOTA] Error incrementing quota after step '{step_name}': {quota_err}")
                    
                    if progress_callback:
                        await progress_callback({
                            'step': i,
                            'step_name': step_name,
                            'status': 'completed',
                            'progress': int(((i + 1) / len(self.steps)) * 100),
                            'message': f"Completed {step_name}"
                        })
                    
                except RetryableError as e:
                    logger.warning(f"Retryable error in step {i}: {e}")
                    
                    notify_error(
                        ErrorType.PROVIDER_GENERATION_FAILED,
                        f"Workflow step '{step_name}' failed (retryable) — will retry",
                        context={
                            "job_id": job_id,
                            "step": step_name,
                            "error_type": e.error_type,
                            "model": e.model,
                            "provider": e.provider,
                            "error": str(e)
                        }
                    )
                    
                    checkpoint_data = {
                        'step_name': step_name,
                        'step_type': step.get('type'),
                        'status': 'failed_retryable',
                        'error': str(e),
                        'error_type': e.error_type,
                        'retry_count': e.retry_count,
                        'last_attempt': datetime.utcnow().isoformat(),
                        'started_at': datetime.utcnow().isoformat()
                    }
                    
                    await self._save_checkpoint(execution['id'], i, checkpoint_data)
                    
                    error_info = {
                        'error': str(e),
                        'error_type': e.error_type,
                        'failed_step': step_name,
                        'failed_step_index': i,
                        'model': e.model,
                        'provider': e.provider,
                        'last_attempt': datetime.utcnow().isoformat()
                    }
                    
                    await self._update_execution(execution['id'], {
                        'status': 'pending_retry',
                        'error_info': error_info,
                        'retry_count': execution.get('retry_count', 0) + 1
                    })
                    
                    await self._update_job_status(job_id, 'pending_retry', {
                        'error': str(e),
                        'failed_step': step_name,
                        'can_resume': True,
                        'retryable': True
                    })
                    
                    if progress_callback:
                        await progress_callback({
                            'step': i,
                            'step_name': step_name,
                            'status': 'failed_retryable',
                            'error': str(e),
                            'can_retry': True
                        })
                    
                    raise
                
                except HardError as e:
                    logger.error(f"Hard error in step {i}: {e}")
                    
                    notify_error(
                        ErrorType.JOB_PROCESSING_ERROR,
                        f"Workflow step '{step_name}' failed permanently (hard error)",
                        context={
                            "job_id": job_id,
                            "step": step_name,
                            "error": str(e)
                        }
                    )
                    
                    checkpoint_data = {
                        'step_name': step_name,
                        'step_type': step.get('type'),
                        'status': 'failed_permanent',
                        'error': str(e),
                        'started_at': datetime.utcnow().isoformat()
                    }
                    
                    await self._save_checkpoint(execution['id'], i, checkpoint_data)
                    
                    await self._update_execution(execution['id'], {
                        'status': 'failed',
                        'error_info': {
                            'error': str(e),
                            'failed_step': step_name,
                            'failed_step_index': i
                        }
                    })
                    
                    await self._update_job_status(job_id, 'failed', {
                        'error': str(e),
                        'failed_step': step_name
                    })
                    
                    if progress_callback:
                        await progress_callback({
                            'step': i,
                            'step_name': step_name,
                            'status': 'failed',
                            'error': str(e),
                            'can_retry': False
                        })
                    
                    raise
            
            await self._update_execution(execution['id'], {
                'status': 'completed',
                'current_step': len(self.steps)
            })
            
            await self._update_job_status(job_id, 'completed', {'result': result})
            
            if progress_callback:
                await progress_callback({
                    'status': 'completed',
                    'progress': 100,
                    'result': result
                })
            
            logger.info(f"Workflow {self.config['id']} completed successfully")
            return result
            
        except Exception as e:
            logger.error(f"Workflow execution failed: {e}")
            raise
    
    async def _execute_step(self, step: Dict, step_index: int, execution: Dict, input_data: Any) -> Any:
        from workflows.errors import RetryableError, HardError
        
        step_type = step.get('type')
        step_name = step.get('name', f'step_{step_index}')
        
        method_name = f"step_{step_name}"
        
        if hasattr(self, method_name):
            method = getattr(self, method_name)
            
            if step_index > 0:
                prev_output = await self._get_checkpoint_output(execution['id'], step_index - 1)
                if prev_output:
                    input_data = prev_output
            elif input_data is None:
                stored_input = (execution.get('checkpoints') or {}).get('_input')
                if stored_input:
                    logger.info(f"Step 0 resume: loading original input from stored checkpoint")
                    input_data = stored_input
            
            if step_type == 'generation':
                step_provider = step.get('provider', 'unknown')
                step_model = step.get('model') or step.get('default_model', 'unknown')
                try:
                    from model_quota_manager import get_quota_manager
                    quota_manager = get_quota_manager()
                    if not quota_manager.check_quota_available(step_provider, step_model):
                        logger.warning(f"[QUOTA] Quota exceeded for {step_provider}:{step_model} — marking step '{step_name}' as pending_retry")
                        raise RetryableError(
                            f"Quota limit reached for {step_model}. Workflow will retry when quota resets.",
                            error_type='quota_exceeded',
                            retry_count=0,
                            model=step_model,
                            provider=step_provider
                        )
                except RetryableError:
                    raise
                except Exception as quota_check_err:
                    logger.warning(f"[QUOTA] Could not check quota for step '{step_name}': {quota_check_err}")
            
            try:
                return await method(input_data, step)
            except Exception as e:
                error_msg = str(e)
                error_msg_lower = error_msg.lower()
                
                step_config = step.get('config', step)
                model = step_config.get('model', step_config.get('default_model', 'unknown'))
                provider = step_config.get('provider', 'unknown')
                
                if 'INVALID_IMAGE_FORMAT:' in error_msg:
                    user_message = error_msg.split('INVALID_IMAGE_FORMAT:', 1)[-1].strip()
                    logger.error(f"Unsupported image format in step {step_name}: {user_message}")
                    raise HardError(f"⚠️ {user_message}")

                elif 'IMAGE_NOT_SUPPORTED:' in error_msg:
                    user_message = error_msg.split('IMAGE_NOT_SUPPORTED:', 1)[-1].strip()
                    logger.error(f"Image input not supported in step {step_name}: {user_message}")
                    raise HardError(f"⚠️ {user_message}")

                elif 'no_api_key_available' in error_msg_lower:
                    logger.warning(f"No API keys available for provider {provider}: {e}")
                    notify_error(
                        ErrorType.NO_API_KEY_FOR_PROVIDER,
                        f"No API keys available for workflow provider '{provider}'",
                        context={"job_id": step_name, "provider": provider, "model": model}
                    )
                    raise RetryableError(
                        f"No API keys available for {provider}. Workflow will retry when keys are added.",
                        error_type='no_api_key',
                        retry_count=0,
                        model=model,
                        provider=provider
                    )
                elif 'quota' in error_msg_lower or 'limit' in error_msg_lower or 'credits' in error_msg_lower or 'payment_required' in error_msg_lower:
                    logger.warning(f"Quota/Credits exceeded for {model}: {e}")
                    notify_error(
                        ErrorType.PROVIDER_RATE_LIMIT,
                        f"Quota/credits exceeded for workflow model '{model}'",
                        context={"provider": provider, "model": model, "error": error_msg[:200]}
                    )
                    raise RetryableError(
                        f"Insufficient credits for {model}. Please add credits to your provider account.",
                        error_type='quota_exceeded',
                        retry_count=0,
                        model=model,
                        provider=provider
                    )
                elif 'timeout' in error_msg_lower or 'timed out' in error_msg_lower:
                    logger.warning(f"Request timed out for {model}: {e}")
                    raise RetryableError(
                        f"Request timed out for {model}",
                        error_type='timeout',
                        retry_count=0,
                        model=model,
                        provider=provider
                    )
                elif 'api key' in error_msg_lower or 'authentication' in error_msg_lower or 'unauthorized' in error_msg_lower:
                    logger.warning(f"API key issue for {provider}: {e}")
                    raise RetryableError(
                        "Invalid or missing API key",
                        error_type='invalid_key',
                        retry_count=0,
                        model=model,
                        provider=provider
                    )
                else:
                    logger.error(f"Unhandled exception in step {step_name} for {provider}: {e}")
                    raise RetryableError(
                        f"Unexpected error in step '{step_name}': {error_msg}",
                        error_type='generic_api_error',
                        retry_count=0,
                        model=model,
                        provider=provider
                    )
        else:
            raise HardError(f"Step method {method_name} not implemented")
    
    async def _get_or_create_execution(self, job_id: str, user_id: str, resume: bool, input_data: Any = None) -> Dict:
        # Always check for an existing execution first.
        # This handles crash-recovery: a job reset from 'running' → 'pending' by the
        # startup reset still has completed checkpoints — we must resume from them,
        # not create a fresh execution that throws away the saved progress.
        try:
            existing = supabase.table('workflow_executions')\
                .select('*')\
                .eq('job_id', job_id)\
                .single()\
                .execute()
            
            if existing.data:
                existing_exec = existing.data
                checkpoints = existing_exec.get('checkpoints') or {}
                has_progress = any(
                    v.get('status') == 'completed'
                    for v in checkpoints.values()
                    if isinstance(v, dict)
                )
                if resume or has_progress:
                    logger.info(
                        f"Resuming existing execution for job {job_id} "
                        f"from step {existing_exec.get('current_step', 0)} "
                        f"(completed checkpoints: {sum(1 for v in checkpoints.values() if isinstance(v, dict) and v.get('status') == 'completed')})"
                    )
                    # Backfill original input if it was never stored (e.g. old executions)
                    if input_data is not None and '_input' not in checkpoints:
                        checkpoints['_input'] = input_data
                        supabase.table('workflow_executions')\
                            .update({'checkpoints': checkpoints})\
                            .eq('id', existing_exec['id'])\
                            .execute()
                        existing_exec['checkpoints'] = checkpoints
                        logger.info(f"Backfilled original input for existing execution {existing_exec['id']}")
                    return existing_exec
        except Exception as lookup_err:
            logger.warning(f"Could not look up existing execution for job {job_id}: {lookup_err}")

        initial_checkpoints = {}
        if input_data is not None:
            initial_checkpoints['_input'] = input_data

        execution_data = {
            'job_id': job_id,
            'workflow_id': self.config['id'],
            'user_id': user_id,
            'current_step': 0,
            'total_steps': len(self.steps),
            'status': 'pending',
            'checkpoints': initial_checkpoints,
            'retry_count': 0
        }
        
        response = supabase.table('workflow_executions')\
            .insert(execution_data)\
            .execute()
        
        return response.data[0]
    
    async def _update_execution(self, execution_id: str, updates: Dict):
        supabase.table('workflow_executions')\
            .update(updates)\
            .eq('id', execution_id)\
            .execute()
    
    async def _save_checkpoint(self, execution_id: str, step_index: int, checkpoint_data: Dict):
        response = supabase.table('workflow_executions')\
            .select('checkpoints')\
            .eq('id', execution_id)\
            .single()\
            .execute()
        
        checkpoints = response.data.get('checkpoints', {}) if response.data else {}
        checkpoints[str(step_index)] = checkpoint_data
        
        supabase.table('workflow_executions')\
            .update({'checkpoints': checkpoints})\
            .eq('id', execution_id)\
            .execute()
        
        logger.info(f"Checkpoint saved for step {step_index}: {checkpoint_data['status']}")
    
    async def _get_checkpoint_output(self, execution_id: str, step_index: int) -> Optional[Any]:
        response = supabase.table('workflow_executions')\
            .select('checkpoints')\
            .eq('id', execution_id)\
            .single()\
            .execute()
        
        if not response.data:
            return None
        
        checkpoints = response.data.get('checkpoints', {})
        checkpoint = checkpoints.get(str(step_index))
        
        if checkpoint and checkpoint.get('status') == 'completed':
            return checkpoint.get('output')
        
        return None
    
    async def _update_job_status(self, job_id: str, status: str, metadata: Dict = None):
        update_data = {'status': status}

        if status == 'failed' and metadata and 'error' in metadata:
            update_data['error_message'] = metadata['error']
        
        if metadata:
            update_data['workflow_metadata'] = metadata
            
            # Extract result fields to main job record for frontend display
            if 'result' in metadata and isinstance(metadata['result'], dict):
                result = metadata['result']
                
                # If workflow result has video_url, set it in main job record
                if 'video_url' in result:
                    update_data['video_url'] = result['video_url']
                
                # If workflow result has edited_image_url (intermediate), set as image_url
                if 'edited_image_url' in result:
                    update_data['image_url'] = result['edited_image_url']
                
                # If workflow result has input_image (final step input), use that
                elif 'input_image' in result:
                    update_data['image_url'] = result['input_image']
        
        supabase.table('jobs')\
            .update(update_data)\
            .eq('job_id', job_id)\
            .execute()
    
    @abstractmethod
    async def step_upload(self, input_data: Any, step_config: Dict) -> Any:
        pass
