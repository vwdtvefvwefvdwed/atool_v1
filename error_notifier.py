"""
Error Notification System via ntfy
Sends instant mobile alerts for critical system errors
"""

import requests
import os
from datetime import datetime
from collections import defaultdict
from enum import Enum

NTFY_SERVER = os.getenv("NTFY_SERVER", "https://ntfy.sh")
NTFY_CRITICAL = os.getenv("NTFY_CRITICAL", "atool-critical-xyz5656")
NTFY_API_KEYS = os.getenv("NTFY_API_KEYS", "atool-api-keys-5757")
NTFY_PROVIDERS = os.getenv("NTFY_PROVIDERS", "atool-providers-5858")
NTFY_STORAGE = os.getenv("NTFY_STORAGE", "atool-storage-5959")
NTFY_WORKER = os.getenv("NTFY_WORKER", "atool-worker-6060")

error_occurrences = defaultdict(list)


class ErrorType(Enum):
    NO_API_KEYS_ALL_PROVIDERS = ("critical", "fire,rotating_light")
    SUPABASE_CONNECTION_DOWN = ("critical", "fire,skull")
    WORKER1_DATABASE_DOWN = ("critical", "fire,no_entry")
    WORKER_STARTUP_FAILED = ("critical", "fire,warning")
    REALTIME_LISTENER_CRASHED = ("critical", "fire,boom")
    ALL_PROVIDERS_DOWN = ("critical", "fire,x")
    
    NO_API_KEY_FOR_PROVIDER = ("api_keys", "key,warning")
    API_KEY_ROTATION_FAILED = ("api_keys", "key,x")
    PROVIDER_NOT_FOUND = ("api_keys", "key,question")
    WORKER1_CLIENT_UNAVAILABLE = ("api_keys", "key,no_entry")
    API_KEY_EXHAUSTION_WARNING = ("api_keys", "key,hourglass")
    RESEND_QUOTA_EXCEEDED = ("api_keys", "envelope,x,warning")
    RESEND_BACKUP_ACTIVATED = ("api_keys", "envelope,arrows_counterclockwise")
    RESEND_BACKUP_FAILED = ("critical", "fire,envelope,x")
    
    REPLICATE_API_ERROR = ("providers", "link,x")
    FAL_AI_ERROR = ("providers", "link,warning")
    PROVIDER_GENERATION_FAILED = ("providers", "link,construction")
    VIDEO_DOWNLOAD_FAILED = ("providers", "arrow_down,x")
    IMAGE_DOWNLOAD_FAILED = ("providers", "arrow_down,warning")
    BASE64_DECODE_ERROR = ("providers", "1234,x")
    PROVIDER_TIMEOUT = ("providers", "hourglass,warning")
    PROVIDER_RATE_LIMIT = ("providers", "pause_button,warning")
    
    CLOUDINARY_UPLOAD_FAILED = ("storage", "cloud,x")
    CLOUDINARY_TIMEOUT = ("storage", "cloud,hourglass")
    CLOUDINARY_SIZE_ERROR = ("storage", "cloud,chart_with_upwards_trend")
    CLOUDINARY_COMPRESSION_FAILED = ("storage", "cloud,construction")
    CLOUDINARY_REQUEST_ERROR = ("storage", "cloud,warning")
    UPLOAD_IMAGE_NO_URL = ("storage", "cloud,question")
    
    JOB_PROCESSING_ERROR = ("worker", "gear,x")
    JOB_THREAD_CRASHED = ("worker", "gear,fire")
    JOB_RESET_FAILED = ("worker", "gear,arrows_counterclockwise")
    JOB_COMPLETE_UPDATE_FAILED = ("worker", "gear,white_check_mark,x")
    PENDING_JOBS_FETCH_FAILED = ("worker", "gear,inbox_tray,x")
    RUNNING_JOBS_RESET_FAILED = ("worker", "gear,warning")
    REALTIME_CALLBACK_ERROR = ("worker", "gear,lightning")
    API_KEY_LISTENER_ERROR = ("worker", "gear,key,x")


def get_topic_for_category(category: str) -> str:
    """Map category to ntfy topic"""
    topics = {
        "critical": NTFY_CRITICAL,
        "api_keys": NTFY_API_KEYS,
        "providers": NTFY_PROVIDERS,
        "storage": NTFY_STORAGE,
        "worker": NTFY_WORKER
    }
    return topics.get(category, NTFY_WORKER)


def should_notify(error_type: str, window_minutes: int = 15) -> bool:
    """
    Rate limiting to prevent notification spam
    Allows 1 notification per error type per window_minutes
    """
    from datetime import timedelta
    
    now = datetime.now()
    cutoff = now - timedelta(minutes=window_minutes)
    
    error_occurrences[error_type] = [
        ts for ts in error_occurrences[error_type] 
        if ts > cutoff
    ]
    
    if len(error_occurrences[error_type]) >= 1:
        return False
    
    error_occurrences[error_type].append(now)
    return True


def notify_error(error_type: ErrorType, message: str, context: dict = None):
    """
    Send instant error notification via ntfy
    
    Args:
        error_type: ErrorType enum
        message: Human-readable error message
        context: Optional dict with job_id, provider_key, error details, etc.
    
    Example:
        notify_error(
            ErrorType.NO_API_KEY_FOR_PROVIDER,
            "No API keys available for Replicate",
            context={"provider": "replicate", "job_id": "abc123"}
        )
    """
    category, tags = error_type.value
    topic = get_topic_for_category(category)
    
    if not should_notify(error_type.name):
        print(f"[NOTIFY] Skipping duplicate notification for {error_type.name} (rate limited)")
        return
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_message = f"🚨 {message}\n\n"
    
    if context:
        full_message += "📋 Details:\n"
        for key, value in context.items():
            if value is not None:
                value_str = str(value)[:200]
                full_message += f"• {key}: {value_str}\n"
    
    full_message += f"\n⏰ {timestamp}"
    
    try:
        response = requests.post(
            f"{NTFY_SERVER}/{topic}",
            data=full_message.encode('utf-8'),
            headers={
                "Title": f"[{error_type.name}] Atool Alert",
                "Priority": "urgent",
                "Tags": tags
            },
            timeout=5
        )
        
        if response.status_code == 200:
            print(f"[NOTIFY] Alert sent: {error_type.name} -> {topic}")
        else:
            print(f"[NOTIFY] Failed to send alert: {response.status_code} - {response.text[:100]}")
    except requests.exceptions.Timeout:
        print(f"[NOTIFY] Notification timeout for {error_type.name}")
    except Exception as e:
        print(f"[NOTIFY] Notification error: {e}")


def notify_test():
    """Send test notification to verify setup"""
    test_message = "🧪 Test notification - system is working!"
    
    try:
        response = requests.post(
            f"{NTFY_SERVER}/{NTFY_WORKER}",
            data=test_message.encode('utf-8'),
            headers={
                "Title": "Atool Test Notification",
                "Priority": "default",
                "Tags": "white_check_mark,tada"
            },
            timeout=5
        )
        
        if response.status_code == 200:
            print("[NOTIFY] Test notification sent successfully")
            return True
        else:
            print(f"[NOTIFY] Test failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"[NOTIFY] Test error: {e}")
        return False
