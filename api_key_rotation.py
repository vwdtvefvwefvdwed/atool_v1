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
    # vision-* image providers (from multi_endpoint_manager.py)
    "vision-nova": "replicate",
    "vision-pixazo": "pixazo",
    "vision-huggingface": "huggingface",
    "vision-ultrafast": "rapidapi",
    "vision-atlas": "a4f",
    "vision-flux": "kie",
    "vision-removebg": "removebg",
    "vision-bria": "bria_vision",
    "vision-xeven": "xeven",
    "vision-infip": "infip",
    "vision-deapi": "deapi",
    "vision-leonardo": "leonardo",
    "vision-stabilityai": "stabilityai",
    # cinematic-* video providers (from multi_endpoint_manager.py)
    "cinematic-nova": "replicate",
    "cinematic-pro": "kie",
    "cinematic-bria": "bria_cinematic",
    "cinematic-leonardo": "leonardo",
    # bare provider names (used by image/video job worker directly)
    "replicate": "replicate",
    "pixazo": "pixazo",
    "huggingface": "huggingface",
    "leonardo": "leonardo",
    "stabilityai": "stabilityai",
    "stability": "stabilityai",
    "rapidapi": "rapidapi",
    "a4f": "a4f",
    "kie": "kie",
    "removebg": "removebg",
    "bria": "bria_vision",
    "bria_vision": "bria_vision",
    "bria_cinematic": "bria_cinematic",
    "xeven": "xeven",
    "infip": "infip",
    "deapi": "deapi",
    "openai": "openai",
    "fal": "fal",
    "runway": "runway",
    "kling": "kling",
    "luma": "luma",
    "pika": "pika",
}

_COMMON_LIMIT_PATTERNS = [
    r"rate limit",
    r"rate_limit",
    r"ratelimit",
    r"rate_limit_exceeded",
    r"quota exceeded",
    r"quota_exceeded",
    r"quota_error",
    r"limit exceeded",
    r"limit_exceeded",
    r"too many requests",
    r"throttled",
    r"429",
    r"monthly limit",
    r"month limit",
    r"period limit",
    r"usage limit",
    r"daily limit",
    r"request limit",
    r"concurrency limit",
    r"capacity exceeded",
]

_COMMON_CREDIT_PATTERNS = [
    r"insufficient",
    r"insufficient credit",
    r"insufficient_credit",
    r"not enough credit",
    r"payment required",
    r"payment_required",
    r"billing",
    r"subscription",
    r"expired",
    r"invalid token",
    r"invalid_token",
    r"unauthorized",
    r"invalid api key",
    r"invalid_api_key",
    r"api key invalid",
    r"access denied",
    r"forbidden",
    r"401",
    r"402",
    r"403",
]

_COMMON_ENTRY = {
    "limit_reached": _COMMON_LIMIT_PATTERNS,
    "credit_exceeded": _COMMON_CREDIT_PATTERNS,
}

NO_API_KEY_PROVIDERS = {"xeven"}

ERROR_PATTERNS = {
    "replicate": {
        "limit_reached": _COMMON_LIMIT_PATTERNS + [
            r"prediction failed to start",
            r"model is currently processing",
        ],
        "credit_exceeded": _COMMON_CREDIT_PATTERNS + [
            r"invalid api token",
            r"unauthenticated",
            r"out of gpu time",
            r"account has been suspended",
        ],
    },
    "pixazo": {
        "limit_reached": _COMMON_LIMIT_PATTERNS + [
            r"rate limit is exceeded",
            r"quota has been exceeded",
        ],
        "credit_exceeded": _COMMON_CREDIT_PATTERNS + [
            r"invalid subscription key",
            r"missing subscription key",
            r"access denied due to",
            r"subscription key is invalid",
        ],
    },
    "rapidapi": {
        "limit_reached": _COMMON_LIMIT_PATTERNS + [
            r"rate limit is exceeded",
            r"you are not subscribed",
        ],
        "credit_exceeded": _COMMON_CREDIT_PATTERNS + [
            r"invalid api key",
            r"api key not found",
            r"blocked",
        ],
    },
    "a4f": {
        "limit_reached": _COMMON_LIMIT_PATTERNS + [
            r"insufficient_quota",
            r"model_not_available",
        ],
        "credit_exceeded": _COMMON_CREDIT_PATTERNS + [
            r"insufficient_quota",
            r"no_api_key",
            r"invalid key",
        ],
    },
    "kie": {
        "limit_reached": _COMMON_LIMIT_PATTERNS,
        "credit_exceeded": _COMMON_CREDIT_PATTERNS + [
            r"access permissions",
            r"do not have access",
            r"you do not have",
            r'"code"\s*:\s*401',
            r'"code"\s*:\s*403',
        ],
    },
    "removebg": {
        "limit_reached": _COMMON_LIMIT_PATTERNS + [
            r"rate limit exceeded",
        ],
        "credit_exceeded": _COMMON_CREDIT_PATTERNS + [
            r"no credits",
            r"credits remaining",
            r"out of credits",
            r"invalid api key",
            r"api key is invalid",
        ],
    },
    "bria_vision": {
        "limit_reached": _COMMON_LIMIT_PATTERNS + [
            r"too many concurrent",
            r"concurrent requests",
        ],
        "credit_exceeded": _COMMON_CREDIT_PATTERNS + [
            r"not authenticated",
            r"credentials were not provided",
            r"authentication credentials",
            r"api_token",
            r"monthly credit limit",
            r"credit limit reached",
        ],
    },
    "bria_cinematic": {
        "limit_reached": _COMMON_LIMIT_PATTERNS + [
            r"too many concurrent",
            r"concurrent requests",
        ],
        "credit_exceeded": _COMMON_CREDIT_PATTERNS + [
            r"not authenticated",
            r"credentials were not provided",
            r"authentication credentials",
            r"api_token",
            r"monthly credit limit",
            r"credit limit reached",
        ],
    },
    "bria": {
        "limit_reached": _COMMON_LIMIT_PATTERNS + [
            r"too many concurrent",
            r"concurrent requests",
        ],
        "credit_exceeded": _COMMON_CREDIT_PATTERNS + [
            r"not authenticated",
            r"credentials were not provided",
            r"authentication credentials",
            r"api_token",
            r"monthly credit limit",
            r"credit limit reached",
        ],
    },
    "stabilityai": {
        "limit_reached": _COMMON_LIMIT_PATTERNS + [
            r"credits per",
            r"monthly api limit",
        ],
        "credit_exceeded": _COMMON_CREDIT_PATTERNS + [
            r"invalid credentials",
            r"api key is required",
            r"does not have enough credits",
            r"current balance",
            r"insufficient credits",
        ],
    },
    "stability": {
        "limit_reached": _COMMON_LIMIT_PATTERNS + [
            r"credits per",
            r"monthly api limit",
        ],
        "credit_exceeded": _COMMON_CREDIT_PATTERNS + [
            r"invalid credentials",
            r"api key is required",
            r"does not have enough credits",
            r"current balance",
            r"insufficient credits",
        ],
    },
    "leonardo": {
        "limit_reached": _COMMON_LIMIT_PATTERNS + [
            r"api limit exceeded",
            r"api_limit_exceeded",
            r"rate_limit",
        ],
        "credit_exceeded": _COMMON_CREDIT_PATTERNS + [
            r"insufficient tokens",
            r"insufficient_tokens",
            r"not enough tokens",
            r"token balance",
            r"graphql.*unauthorized",
        ],
    },
    "infip": {
        "limit_reached": _COMMON_LIMIT_PATTERNS + [
            r"insufficient_quota",
            r"requests per",
        ],
        "credit_exceeded": _COMMON_CREDIT_PATTERNS + [
            r"insufficient_quota",
            r"quota_exceeded",
            r"invalid api key",
        ],
    },
    "deapi": {
        "limit_reached": _COMMON_LIMIT_PATTERNS + [
            r"too many requests",
            r"limit exceeded",
        ],
        "credit_exceeded": _COMMON_CREDIT_PATTERNS + [
            r"api key not valid",
            r"invalid bearer",
            r"no valid api",
        ],
    },
    "huggingface":    _COMMON_ENTRY,
    "openai":         _COMMON_ENTRY,
    "fal":            _COMMON_ENTRY,
    "runway":         _COMMON_ENTRY,
    "kling":          _COMMON_ENTRY,
    "luma":           _COMMON_ENTRY,
    "pika":           _COMMON_ENTRY,
    "xeven":          _COMMON_ENTRY,
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
    
    Returns True for:
    - Known quota/credit/limit errors (always rotate)
    - Generic API errors from known providers (try next key, current one may be broken)
    
    Returns False for:
    - Network/timeout errors (not a key problem)
    - Unknown providers with no patterns registered
    - Providers that don't use API keys (e.g., xeven free API)
    """
    actual_provider = PROVIDER_KEY_MAPPING.get(provider.lower(), provider.lower())

    if actual_provider in NO_API_KEY_PROVIDERS:
        return False

    error_type = detect_error_type(error_message, provider)
    
    if error_type in ["limit_reached", "credit_exceeded", "monthly_limit"]:
        return True
    
    if error_type == "generic_error":
        if actual_provider in ERROR_PATTERNS:
            error_msg_lower = str(error_message).lower()
            is_network = any(x in error_msg_lower for x in [
                "timeout", "timed out", "connection", "network", "unreachable",
                "httpsconnectionpool", "unable to connect"
            ])
            if not is_network:
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
