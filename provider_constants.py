"""
Shared constants for API key rotation and provider management.
"""

NO_DELETE_ROTATE_PROVIDERS = {
    "vision-infip", "vision-a4f", "vision-frenix", "vision-aicc", "cinematic-aicc", "aicc",
    "vision-clipdrop", "vision-felo", "felo", "vision-custom", "custom",
    "vision-gemini", "vision-geminiwebapi", "geminiwebapi", "vision-ondemand", "ondemand"
}

CREDIT_EXCEEDED_DELETE_PROVIDERS = {
    "vision-aicc", "cinematic-aicc", "aicc", "vision-ondemand", "ondemand"
}

NO_API_KEY_PROVIDERS = set()

# Cool‑down applied to every error for NO‑DELETE providers (25 hours)
NO_DELETE_COOLDOWN_SECONDS = 25 * 3600


def validate_no_delete_coverage():
    """Validate that all NO_DELETE_ROTATE_PROVIDERS have error patterns defined.

    Call this at startup to warn about providers that may get aggressive
    cooldown on every error due to missing error patterns.

    Returns:
        List of provider keys that are in NO_DELETE_ROTATE_PROVIDERS but
        NOT covered by ERROR_PATTERNS (after mapping).
    """
    # Import here to avoid circular dependency at module load time
    try:
        from api_key_rotation import PROVIDER_KEY_MAPPING, ERROR_PATTERNS
    except ImportError:
        return []  # module not loaded yet

    uncovered = []
    for provider_key in NO_DELETE_ROTATE_PROVIDERS:
        actual_provider = PROVIDER_KEY_MAPPING.get(provider_key, provider_key)
        if actual_provider not in ERROR_PATTERNS:
            uncovered.append(provider_key)

    if uncovered:
        print(f"[WARN] NO_DELETE_ROTATE_PROVIDERS without error patterns: {uncovered}")
        print(f"[WARN] These providers will get 25-hour cooldown on ANY error (including generic errors)")

    return uncovered