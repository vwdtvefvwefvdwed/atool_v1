"""
API Key Rotation Module
Detects errors from different providers and handles automatic key rotation.
When a provider returns an error (limit reached, credit exceeded, etc.),
the current API key is deleted and the next available key is fetched.
"""

import re
from typing import Optional, Dict, Any, Tuple
from provider_api_keys import delete_api_key, get_next_api_key_for_provider, get_all_api_keys_for_provider


PROVIDER_KEY_MAPPING = {
    "vision-nova": "replicate",
    "cinematic-nova": "replicate",
    "vision-pixazo": "pixazo",
    "vision-huggingface": "huggingface",
    "replicate": "replicate",
}


ERROR_PATTERNS = {
    "replicate": {
        "limit_reached": [
            r"rate limit",
            r"rate_limit",
            r"quota exceeded",
            r"quota_exceeded",
            r"limit exceeded",
            r"limit_exceeded",
            r"too many requests",
            r"429",
        ],
        "credit_exceeded": [
            r"insufficient",
            r"insufficient credit",
            r"not enough credit",
            r"payment required",
            r"billing",
            r"subscription",
            r"expired",
            r"invalid token",
            r"unauthorized",
            r"401",
            r"402",
            r"403",
        ],
        "monthly_limit": [
            r"monthly limit",
            r"month limit",
            r"period limit",
            r"usage limit",
        ]
    },
    "pixazo": {
        "limit_reached": [
            r"rate limit",
            r"rate_limit",
            r"too many requests",
            r"429",
        ],
        "credit_exceeded": [
            r"insufficient",
            r"insufficient credit",
            r"not enough credit",
            r"unauthorized",
            r"invalid subscription key",
            r"401",
            r"403",
        ],
        "monthly_limit": [
            r"monthly limit",
            r"usage limit",
        ]
    },
    "huggingface": {
        "limit_reached": [
            r"rate limit",
            r"too many requests",
            r"429",
        ],
        "credit_exceeded": [
            r"unauthorized",
            r"invalid token",
            r"401",
            r"403",
        ],
        "generic": [
            r"error",
            r"failed",
        ]
    }
}


def detect_error_type(error_message: str, provider: str) -> Optional[str]:
    """
    Detect the type of error from error message for a specific provider.
    
    Args:
        error_message: Error message string from the provider
        provider: Provider name (replicate, pixazo, huggingface)
        
    Returns:
        Error type string or None if no match
    """
    if not error_message or not provider:
        return None
    
    error_msg_lower = str(error_message).lower()
    
    actual_provider = PROVIDER_KEY_MAPPING.get(provider.lower(), provider.lower())
    provider_patterns = ERROR_PATTERNS.get(actual_provider, {})
    
    for error_type, patterns in provider_patterns.items():
        for pattern in patterns:
            if re.search(pattern, error_msg_lower, re.IGNORECASE):
                return error_type
    
    return "generic_error"


def should_rotate_key(error_message: str, provider: str) -> bool:
    """
    Determine if an API key should be rotated based on error message.
    
    Args:
        error_message: Error message from provider
        provider: Provider name
        
    Returns:
        True if key should be rotated, False otherwise
    """
    error_type = detect_error_type(error_message, provider)
    
    if error_type in ["limit_reached", "credit_exceeded", "monthly_limit"]:
        return True
    
    return False


def handle_api_key_rotation(
    current_api_key_id: int,
    provider_key: str,
    error_message: str,
    job_id: str
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    Handle API key rotation when an error occurs.
    Deletes the current API key and fetches the next one.
    
    Args:
        current_api_key_id: ID of the API key that failed
        provider_key: Provider identifier (e.g., 'vision-nova')
        error_message: Error message from the provider
        job_id: Job ID for logging
        
    Returns:
        Tuple of (success, next_api_key_data)
        - success: True if rotation succeeded, False otherwise
        - next_api_key_data: Dict with next API key or None
    """
    error_type = detect_error_type(error_message, provider_key)
    
    print(f"\n{'='*70}")
    print(f"API KEY ROTATION TRIGGERED")
    print(f"{'='*70}")
    print(f"Job ID: {job_id}")
    print(f"Provider: {provider_key}")
    print(f"Error Type: {error_type}")
    print(f"Error Message: {error_message}")
    print(f"Current API Key ID: {current_api_key_id}")
    print(f"{'='*70}\n")
    
    if should_rotate_key(error_message, provider_key):
        print(f"[ROTATION] Deleting failed API key {current_api_key_id}...")
        
        deleted = delete_api_key(current_api_key_id, error_message)
        
        if not deleted:
            print(f"[ERROR] Failed to delete API key {current_api_key_id}")
            return False, None
        
        print(f"[ROTATION] Fetching next API key for provider '{provider_key}'...")
        
        next_key = get_next_api_key_for_provider(provider_key)
        
        if next_key:
            available_keys = get_all_api_keys_for_provider(provider_key)
            print(f"[ROTATION] Success! Got next API key (key #{next_key.get('key_number')})")
            print(f"[ROTATION] Remaining keys for provider: {len(available_keys)}")
            return True, next_key
        else:
            print(f"[ERROR] No more API keys available for provider '{provider_key}'")
            return False, None
    else:
        print(f"[ROTATION] Error type '{error_type}' doesn't require key rotation")
        return False, None


def log_rotation_attempt(
    job_id: str,
    provider_key: str,
    old_api_key_id: int,
    new_api_key_id: Optional[int],
    error_message: str,
    success: bool
):
    """
    Log API key rotation attempt for debugging.
    
    Args:
        job_id: Job ID
        provider_key: Provider key
        old_api_key_id: ID of the API key that failed
        new_api_key_id: ID of the new API key (if rotation succeeded)
        error_message: Error message that triggered rotation
        success: Whether rotation succeeded
    """
    status = "SUCCESS" if success else "FAILED"
    new_key_info = f"new_key_id={new_api_key_id}" if new_api_key_id else "no_key_available"
    
    print(f"[LOG] ROTATION {status}: job={job_id}, provider={provider_key}, "
          f"old_key={old_api_key_id}, {new_key_info}, error='{error_message[:50]}...'")
