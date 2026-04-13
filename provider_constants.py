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

NO_DELETE_COOLDOWN_SECONDS = 25 * 3600