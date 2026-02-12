"""
Provider Trials Module
Handles provider trial availability checking and usage tracking
"""

from supabase_client import supabase


def get_user_provider_trials(user_id: str) -> dict:
    """
    Get all providers with user's trial availability status.
    Called on first visit - frontend caches this locally.
    
    Returns:
        dict with providers list and their availability
    """
    try:
        result = supabase.rpc(
            'get_user_provider_trials_status',
            {'p_user_id': user_id}
        ).execute()
        
        if result.data:
            providers = {}
            for p in result.data:
                providers[p['provider_key']] = {
                    'name': p['provider_name'],
                    'type': p['provider_type'],
                    'free_trial_available': p['free_trial_available']
                }
            return {
                'success': True,
                'providers': providers
            }
        
        return {
            'success': True,
            'providers': {}
        }
        
    except Exception as e:
        print(f"Error getting provider trials: {e}")
        return {
            'success': False,
            'error': str(e)
        }


def check_provider_trial_available(user_id: str, provider_key: str) -> bool:
    """
    Check if user has free trial available for a specific provider.
    
    Returns:
        True if trial available, False otherwise
    """
    try:
        result = supabase.rpc(
            'check_provider_trial_available',
            {
                'p_user_id': user_id,
                'p_provider_key': provider_key
            }
        ).execute()
        
        return result.data if result.data is not None else False
        
    except Exception as e:
        print(f"Error checking provider trial: {e}")
        return False


def use_provider_trial(user_id: str, provider_key: str, job_id: str = None) -> dict:
    """
    Mark a provider trial as used for a user.
    Called after generation completes successfully.
    
    Returns:
        dict with success status and updated provider info
    """
    try:
        result = supabase.rpc(
            'use_provider_trial',
            {
                'p_user_id': user_id,
                'p_provider_key': provider_key,
                'p_job_id': job_id
            }
        ).execute()
        
        if result.data:
            return {
                'success': True,
                'provider_key': provider_key,
                'free_trial_available': False
            }
        
        return {
            'success': False,
            'error': 'Provider not found or already used'
        }
        
    except Exception as e:
        print(f"Error using provider trial: {e}")
        return {
            'success': False,
            'error': str(e)
        }


def get_provider_by_model(model: str) -> str:
    """
    Map model name to provider key.
    Add your model-to-provider mappings here.
    """
    MODEL_TO_PROVIDER = {
        'flux-schnell': 'flux_schnell',
        'flux-dev': 'flux_dev',
        'flux1-schnell-fp8.safetensors': 'flux_schnell',
        'flux1-dev.safetensors': 'flux_dev',
        'flux1-krea-dev.safetensors': 'flux_dev',
        'sdxl-turbo': 'sdxl_turbo',
        'stable-diffusion-3': 'stable_diffusion_3',
        'runway-gen3': 'runway_gen3',
        'kling': 'kling_ai',
        'kling-ai': 'kling_ai',
        'bria_image_generate': 'vision_bria',
    }
    
    model_lower = model.lower() if model else ''
    return MODEL_TO_PROVIDER.get(model_lower, model_lower.replace('-', '_'))
