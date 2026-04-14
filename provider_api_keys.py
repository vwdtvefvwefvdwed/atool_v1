"""
Provider API Keys Module
Fetches API keys from Worker1 Supabase account for AI generation providers.

This module is used by the job worker to get API keys when processing generation requests.
Main Supabase account handles users, auth, jobs - Worker1 handles API keys.
"""

import os
from typing import Optional, Dict, Any
from supabase import create_client, Client
from dotenv_vault import load_dotenv
import api_key_round_robin
import api_key_status_manager
from provider_constants import NO_DELETE_ROTATE_PROVIDERS

load_dotenv()

WORKER_1_URL = os.getenv("WORKER_1_URL")
WORKER_1_SERVICE_KEY = os.getenv("WORKER_1_SERVICE_ROLE_KEY")

_worker1_client: Optional[Client] = None


def get_worker1_client() -> Optional[Client]:
    """Get or create Worker1 Supabase client singleton"""
    global _worker1_client
    
    if _worker1_client is not None:
        return _worker1_client
    
    if not WORKER_1_URL or not WORKER_1_SERVICE_KEY:
        print("[WARN] Worker1 credentials not configured. API keys will not be fetched from Worker1.")
        print("       Set WORKER_1_URL and WORKER_1_SERVICE_ROLE_KEY in .env")
        return None
    
    try:
        _worker1_client = create_client(WORKER_1_URL, WORKER_1_SERVICE_KEY)
        print(f"[OK] Worker1 client initialized: {WORKER_1_URL}")
        return _worker1_client
    except Exception as e:
        print(f"[ERROR] Failed to create Worker1 client: {e}")
        return None


def get_provider_api_key(provider_key: str) -> Optional[Dict[str, Any]]:
    """
    Get API key for a provider from Worker1 database.
    Uses round-robin rotation to evenly distribute API key usage.
    
    Args:
        provider_key: Provider identifier (e.g., 'vision-nova', 'cinematic-nova')
        
    Returns:
        Dict with api_key, id, or None if not found
    """
    client = get_worker1_client()
    
    if not client:
        return None
    
    try:
        provider_result = client.table("providers")\
            .select("id")\
            .eq("provider_name", provider_key)\
            .limit(1)\
            .execute()
        
        if not provider_result.data:
            print(f"[WARN] Provider '{provider_key}' not found in providers table")
            return None
        
        provider_id = provider_result.data[0]["id"]
        
        next_row = api_key_round_robin.get_next_row_for_provider(provider_key, provider_id, client)
        
        keys_result = client.table("provider_api_keys")\
            .select("id, api_key, key_number")\
            .eq("provider_id", provider_id)\
            .order("key_number")\
            .execute()
        
        if keys_result.data and len(keys_result.data) > 0:
            total_keys = len(keys_result.data)
            # For NO_DELETE providers, check cooldown status
            # For delete-on-error providers, skip cooldown check (keys are deleted on error)
            use_cooldown_check = provider_key in NO_DELETE_ROTATE_PROVIDERS
            
            attempts = 0
            while attempts < total_keys:
                row = (next_row + attempts) % total_keys
                key_candidate = keys_result.data[row]
                key_num = int(key_candidate['key_number'])
                
                if use_cooldown_check:
                    # Skip keys in cooldown for NO_DELETE providers
                    if api_key_status_manager.is_key_in_cooldown(provider_key, key_num):
                        attempts += 1
                        continue
                
                print(f"[OK] Found API key for provider '{provider_key}' (key #{key_num})")
                api_key_round_robin.mark_row_used(provider_key, row)
                return key_candidate
            
            if use_cooldown_check:
                print(f"[WARN] All API keys for provider '{provider_key}' are in cooldown")
            else:
                print(f"[WARN] No API keys available for provider '{provider_key}'")
            return None
        else:
            print(f"[WARN] No API keys found for provider '{provider_key}'")
            return None
            
    except Exception as e:
        print(f"[ERROR] Failed to fetch API key for '{provider_key}': {e}")
        import traceback
        traceback.print_exc()
        return None


def update_api_key_usage(api_key_id: str) -> bool:
    """
    Update usage statistics for an API key after it's used.
    Note: The simplified schema (021 migration) doesn't track usage statistics.
    This function is kept for backward compatibility.
    
    Args:
        api_key_id: UUID of the API key record
        
    Returns:
        True (no-op)
    """
    # No-op: usage tracking removed in simplified schema
    return True


def increment_usage_count(api_key_id: str) -> bool:
    """
    Increment usage count for an API key.
    Note: The simplified schema (021 migration) doesn't track usage statistics.
    This function is kept for backward compatibility.
    
    Args:
        api_key_id: UUID of the API key record
        
    Returns:
        True (no-op)
    """
    # No-op: usage tracking removed in simplified schema
    return True


def get_all_active_providers() -> list:
    """
    Get all providers from Worker1 that have at least one API key.
    
    Returns:
        List of provider records with their names
    """
    client = get_worker1_client()
    
    if not client:
        return []
    
    try:
        result = client.table("providers")\
            .select("id, provider_name")\
            .order("provider_name")\
            .execute()
        
        return result.data if result.data else []
        
    except Exception as e:
        print(f"[ERROR] Failed to fetch providers: {e}")
        return []


def map_model_to_provider(model_name: str, job_type: str = "image") -> Optional[str]:
    """
    Map a model name to a provider key.
    
    This mapping is based on the providers configured in HomeNew.jsx.
    Provider routing:
    - vision-nova, cinematic-nova -> Replicate API
    - vision-pixazo -> Pixazo API
    - vision-huggingface -> Hugging Face Inference API
    - vision-ultrafast -> RapidAPI (Ultra Fast Nano Banana)
    - vision-atlas -> A4F API
    - vision-flux -> KIE AI API
    - vision-removebg -> Remove.bg API
    - vision-bria, cinematic-bria -> Bria AI API
    - vision-leonardo, cinematic-leonardo -> Leonardo AI API
    - vision-vercel, cinematic-vercel -> Vercel AI Gateway API
    
    Args:
        model_name: Name of the AI model
        job_type: Type of job ('image' or 'video')
        
    Returns:
        Provider key or None if no mapping found
    """
    # Image providers
    image_providers = {
        "vision-custom": [
            "flux-fast-custom",
            "sdxl-fast-custom",
            "flux2-klein-custom",
            "flux2-klein-9b-custom",
            "flux-dev-custom",
            "flux-pro-custom",
            "sdxl-custom",
            "leonardo-custom",
            "phoenix-custom",
        ],
        "vision-nova": [
            "google/imagen-4",
            "black-forest-labs/flux-kontext-pro",
            "ideogram-ai/ideogram-v3-turbo",
            "black-forest-labs/flux-1.1-pro",
            "black-forest-labs/flux-dev",
            "topazlabs/image-upscale",
            "sczhou/codeformer",
            "tencentarc/gfpgan",
        ],
        "vision-pixazo": [
            "flux-1-schnell",
        ],
        "vision-huggingface": [
            "AP123/IllusionDiffusion",
            "finegrain/finegrain-image-enhancer",
            "sczhou/CodeFormer",
        ],
        "vision-ultrafast": [
            "ultra-fast-nano",
            "ultra-fast-nano-banana-2",
        ],
        "vision-atlas": [
            "imagen-3",
            "imagen-3.5",
            "imagen-4",
            "flux-schnell",
            "sdxl-lite",
            "phoenix",
            "firefrost",
            "z-image",
        ],
        "vision-flux": [
            "nano-banana-pro",
            "flux-2-pro",
        ],
        "vision-removebg": [
            "remove-bg",
        ],
        "vision-bria": [
            "bria_image_generate",
            "bria_image_generate_lite",
            "bria_structured_prompt",
            "bria_gen_fill",
            "bria_erase",
            "bria_remove_background",
            "bria_replace_background",
            "bria_blur_background",
            "bria_erase_foreground",
            "bria_expand",
            "bria_enhance",
        ],
        "vision-infip": [
            "z-image-turbo",
            "qwen",
            "flux2-klein-9b",
            "flux2-dev",
            "phoenix-infip",
            "lucid-origin",
            "sdxl-infip",
            "sdxl-lite-infip",
            "img3",
            "img4",
            "flux-schnell-infip",
        ],
        "vision-deapi": [
            "z-image-turbo-deapi",
            "flux-schnell-deapi",
        ],
        "vision-leonardo": [
            "ideogram-3.0",
            "nano-banana-pro-leonardo",
        ],
        "vision-vercel": [
            "grok-imagine-image",
        ],
        "vision-picsart": [
            "picsart-ultra-upscale",
            "picsart-upscale",
        ],
        "vision-clipdrop": [
            "clipdrop-upscale",
            "clipdrop-expand",
        ],
        "vision-frenix": [
            "frenix-dirtberry",
            "frenix-flux-2-pro",
            "frenix-z-image",
            "frenix-imagen-2",
            "frenix-imagen-4",
            "frenix-flux-2-flex",
            "frenix-flux-2-dev",
            "frenix-flux-klein-4b",
            "frenix-flux-klein-9b",
        ],
        "vision-aicc": [
            "gemini-25-flash-aicc",
        ],
        "vision-felo": [
            "nano-banana-2",
        ],
        "vision-gemini": [
            "gemini-2.5-flash-image",
        ],
        "vision-geminiwebapi": [
            "gemini-2.5-flash-image-web",
            "gemini-3.1-flash-image-web",
            "gemini-1.5-flash-web",
            "gemini-2.0-flash-web",
            "gemini-2.5-pro-web",
            "gemini-3-pro-web",
        ],
    "vision-ondemand": [
        "nano-banana-ondemand",
        "nano-banana-2-ondemand",
    ],
}
    
    # Video providers
    video_providers = {
        "cinematic-nova": [
            "minimax/video-01",
            "luma/reframe-video",
            "topazlabs/video-upscale",
        ],
        "cinematic-pro": [
            "kling-2.6",
            "grok-text-to-video",
            "grok-image-to-video",
        ],
        "cinematic-bria": [
            "bria_video_erase",
            "bria_video_upscale",
            "bria_video_remove_bg",
            "bria_video_mask_prompt",
            "bria_video_mask_keypoints",
            "bria_video_foreground_mask",
        ],
        "cinematic-leonardo": [
            "seedance-1.0-pro-fast",
        ],
        "cinematic-vercel": [
            "grok-text-to-video-2",
            "grok-image-to-video-2",
        ],
        "cinematic-deapi": [
            "ltx2-19b-dist-fp8-deapi",
            "ltx2-3-22b-dist-int8-deapi",
        ],
        "cinematic-aicc": [
            "wan22-i2v-plus-aicc",
        ],
    }
    
    providers = video_providers if job_type == "video" else image_providers
    
    for provider_key, models in providers.items():
        if model_name in models:
            return provider_key
    
    # Default providers if no specific match
    if job_type == "video":
        return "cinematic-nova"
    else:
        return "vision-nova"


def get_api_key_for_job(model_name: str, provider_key: Optional[str] = None, job_type: str = "image") -> Optional[Dict[str, Any]]:
    """
    Get API key for a job based on model name or provider key.
    
    This is the main function to be called by job_worker_realtime.py.
    
    Args:
        model_name: Name of the AI model being used
        provider_key: Optional provider key (if already known from frontend)
        job_type: Type of job ('image' or 'video')
        
    Returns:
        Dict with api_key, additional_config, id, or None
    """
    # If provider_key is not specified, map from model name
    if not provider_key:
        provider_key = map_model_to_provider(model_name, job_type)
    
    if not provider_key:
        print(f"[WARN] Could not determine provider for model '{model_name}'")
        return None
    
    print(f"[INFO] Looking up API key for provider: {provider_key}")
    
    api_key_data = get_provider_api_key(provider_key)
    
    if api_key_data:
        # Include provider_key in the response for reference
        api_key_data["provider_key"] = provider_key
    
    return api_key_data


def delete_api_key(api_key_id: int, error_message: str | None = None) -> bool:
    """
    Delete/disable an API key when it returns an error.
    Archives the key in deleted_api_keys table before deletion.
    Used for API key rotation when a key hits limits or returns errors.
    
    Args:
        api_key_id: ID of the API key to delete
        error_message: Optional error message that caused the deletion
        
    Returns:
        True if successful, False otherwise
    """
    client = get_worker1_client()
    
    if not client:
        print(f"[ERROR] Cannot delete API key - no Worker1 client")
        return False
    
    try:
        # First, get the API key details before deleting
        key_result = client.table("provider_api_keys")\
            .select("*, providers(provider_name)")\
            .eq("id", api_key_id)\
            .execute()
        
        if not key_result.data or len(key_result.data) == 0:
            print(f"[ERROR] API key {api_key_id} not found")
            return False
        
        key_data = key_result.data[0]
        
        # Archive the deleted key
        archive_data = {
            "provider_id": key_data["provider_id"],
            "key_number": key_data["key_number"],
            "api_key": key_data["api_key"],
            "error_message": error_message or "No error message provided",
            "original_key_id": api_key_id
        }
        
        archive_result = client.table("deleted_api_keys")\
            .insert(archive_data)\
            .execute()
        
        print(f"[ARCHIVE] API key {api_key_id} archived to deleted_api_keys")
        
        # Now delete from provider_api_keys
        result = client.table("provider_api_keys")\
            .delete()\
            .eq("id", api_key_id)\
            .execute()
        
        print(f"[OK] API key {api_key_id} deleted successfully")
        return True
        
    except Exception as e:
        print(f"[ERROR] Failed to delete API key {api_key_id}: {e}")
        return False


def get_next_api_key_for_provider(provider_key: str) -> Optional[Dict[str, Any]]:
    """
    Get the next available API key for a provider using round-robin rotation.
    Used when current API key fails with error.
    
    Args:
        provider_key: Provider identifier (e.g., 'vision-nova')
        
    Returns:
        Dict with api_key, id, key_number, or None if no other keys available
    """
    client = get_worker1_client()
    
    if not client:
        return None
    
    try:
        provider_result = client.table("providers")\
            .select("id")\
            .eq("provider_name", provider_key)\
            .limit(1)\
            .execute()
        
        if not provider_result.data:
            print(f"[WARN] Provider '{provider_key}' not found")
            return None
        
        provider_id = provider_result.data[0]["id"]
        
        next_row = api_key_round_robin.get_next_row_for_provider(provider_key, provider_id, client)
        
        keys_result = client.table("provider_api_keys")\
            .select("id, api_key, key_number")\
            .eq("provider_id", provider_id)\
            .order("key_number")\
            .execute()
        
        if keys_result.data and len(keys_result.data) > 0:
            total_keys = len(keys_result.data)
            use_cooldown_check = provider_key in NO_DELETE_ROTATE_PROVIDERS
            
            attempts = 0
            while attempts < total_keys:
                row = (next_row + attempts) % total_keys
                key_candidate = keys_result.data[row]
                key_num = int(key_candidate['key_number'])
                
                if use_cooldown_check:
                    if api_key_status_manager.is_key_in_cooldown(provider_key, key_num):
                        attempts += 1
                        continue
                
                print(f"[OK] Got next API key for provider '{provider_key}' (key #{key_num})")
                api_key_round_robin.mark_row_used(provider_key, row)
                return key_candidate
            
            if use_cooldown_check:
                print(f"[WARN] All API keys for provider '{provider_key}' are in cooldown")
            else:
                print(f"[WARN] No API keys available for provider '{provider_key}'")
            return None
        else:
            print(f"[WARN] No API keys found for provider '{provider_key}'")
            return None
            
    except Exception as e:
        print(f"[ERROR] Failed to get next API key for '{provider_key}': {e}")
        return None


def get_all_api_keys_for_provider(provider_key: str) -> list:
    """
    Get all API keys for a provider.
    Used to check how many keys are available for rotation.
    
    Args:
        provider_key: Provider identifier
        
    Returns:
        List of API key records
    """
    client = get_worker1_client()
    
    if not client:
        return []
    
    try:
        provider_result = client.table("providers")\
            .select("id")\
            .eq("provider_name", provider_key)\
            .limit(1)\
            .execute()
        
        if not provider_result.data:
            return []
        
        provider_id = provider_result.data[0]["id"]
        
        keys_result = client.table("provider_api_keys")\
            .select("id, api_key, key_number")\
            .eq("provider_id", provider_id)\
            .execute()
        
        return keys_result.data if keys_result.data else []
            
    except Exception as e:
        print(f"[ERROR] Failed to get all API keys for '{provider_key}': {e}")
        return []


def clear_api_key_status(provider_key: str, key_number: int) -> bool:
    """
    Clear cooldown and reset error counters for a key after successful use.
    
    Note: Only call this for providers in NO_DELETE_ROTATE_PROVIDERS.
    Delete-on-error providers don't use cooldown tracking.
    
    Args:
        provider_key: Provider identifier
        key_number: Key number within the provider
        
    Returns:
        True if successful, False otherwise
    """
    if provider_key not in NO_DELETE_ROTATE_PROVIDERS:
        return True
    return api_key_status_manager.clear_key_success(provider_key, key_number)
