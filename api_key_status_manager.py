"""
API Key Status Manager
Provides helpers for reading/writing cooldown and error information for each API key.
All functions use the Worker‑1 Supabase client (same as provider_api_keys).
"""

import os
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
from supabase import create_client, Client
from dotenv_vault import load_dotenv

load_dotenv()

WORKER_1_URL = os.getenv("WORKER_1_URL")
WORKER_1_SERVICE_KEY = os.getenv("WORKER_1_SERVICE_ROLE_KEY")

_status_client: Optional[Client] = None


def _get_status_client() -> Optional[Client]:
    """Get or create Worker1 Supabase client singleton (avoid circular import)."""
    global _status_client
    
    if _status_client is not None:
        return _status_client
    
    if not WORKER_1_URL or not WORKER_1_SERVICE_KEY:
        print("[WARN] Worker1 credentials not configured for status manager.")
        return None
    
    try:
        _status_client = create_client(WORKER_1_URL, WORKER_1_SERVICE_KEY)
        return _status_client
    except Exception as e:
        print(f"[ERROR] Failed to create Worker1 client for status: {e}")
        return None


def _now_utc() -> datetime:
    """Current UTC timestamp used for all DB writes."""
    return datetime.now(timezone.utc)


def _get_provider_id(provider_key: str) -> Optional[int]:
    client = _get_status_client()
    if not client:
        return None
    result = (
        client.table("providers")
        .select("id")
        .eq("provider_name", provider_key)
        .limit(1)
        .execute()
    )
    if result.data:
        return result.data[0]["id"]
    return None


def _fetch_status(provider_id: int, key_number: int) -> Optional[Dict[str, Any]]:
    client = _get_status_client()
    if not client:
        return None
    result = (
        client.table("api_key_status")
        .select("*")
        .eq("provider_id", provider_id)
        .eq("key_number", key_number)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def is_key_in_cooldown(provider_key: str, key_number: int) -> bool:
    """Return True if ``cooldown_until`` is in the future."""
    provider_id = _get_provider_id(provider_key)
    if provider_id is None:
        return False
    status = _fetch_status(provider_id, key_number)
    if not status:
        return False
    cd = status.get("cooldown_until")
    if not cd:
        return False
    # Supabase returns ISO strings; compare with current UTC.
    try:
        cooldown_ts = datetime.fromisoformat(cd)
    except Exception:
        return False
    return cooldown_ts > _now_utc()


def record_key_error(
    provider_key: str,
    key_number: int,
    error_type: str,
    error_message: str,
    cooldown_seconds: int = 0,
) -> bool:
    """Save an error for a specific key and set a cooldown.

    ``cooldown_seconds`` determines how long the key is blocked from reuse.
    """
    client = _get_status_client()
    if not client:
        return False

    provider_id = _get_provider_id(provider_key)
    if provider_id is None:
        return False

    now = _now_utc()
    cooldown_until = (now + timedelta(seconds=cooldown_seconds)).isoformat() if cooldown_seconds else None

    existing = _fetch_status(provider_id, key_number)
    if existing:
        # Increment counters based on existing row.
        consecutive = (existing.get("consecutive_errors") or 0) + 1
        total = (existing.get("total_errors") or 0) + 1
        update_data = {
            "last_error_type": error_type,
            "last_error_message": error_message,
            "last_error_at": now.isoformat(),
            "cooldown_until": cooldown_until,
            "cooldown_duration_seconds": cooldown_seconds,
            "consecutive_errors": consecutive,
            "total_errors": total,
        }
        client.table("api_key_status").update(update_data).eq("provider_id", provider_id).eq("key_number", key_number).execute()
    else:
        # First error for this key – create the row.
        insert_data = {
            "provider_id": provider_id,
            "key_number": key_number,
            "last_error_type": error_type,
            "last_error_message": error_message,
            "last_error_at": now.isoformat(),
            "cooldown_until": cooldown_until,
            "cooldown_duration_seconds": cooldown_seconds,
            "consecutive_errors": 1,
            "total_errors": 1,
        }
        client.table("api_key_status").insert(insert_data).execute()
    return True


def clear_key_success(provider_key: str, key_number: int) -> bool:
    """Mark a key as successfully used.

    Resets error counters and removes any cooldown.
    """
    client = _get_status_client()
    if not client:
        return False
    provider_id = _get_provider_id(provider_key)
    if provider_id is None:
        return False
    now = _now_utc()
    # Update if row exists; otherwise do nothing.
    existing = _fetch_status(provider_id, key_number)
    if not existing:
        return True
    update_data = {
        "last_success_at": now.isoformat(),
        "cooldown_until": None,
        "cooldown_duration_seconds": None,
        "consecutive_errors": 0,
    }
    client.table("api_key_status").update(update_data).eq("provider_id", provider_id).eq("key_number", key_number).execute()
    return True


def get_next_available_key(provider_key: str, provider_id: int, client) -> Optional[int]:
    """Return the next *key_number* (0‑based index) that is not in cooldown.

    Used by ``provider_api_keys`` before returning a key.
    """
    # Fetch all keys for the provider (ordered by key_number).
    keys_res = (
        client.table("provider_api_keys")
        .select("key_number")
        .eq("provider_id", provider_id)
        .order("key_number")
        .execute()
    )
    if not keys_res.data:
        return None
    key_numbers = [row["key_number"] for row in keys_res.data]

    # Load status rows for these keys.
    status_res = (
        client.table("api_key_status")
        .select("key_number,cooldown_until")
        .eq("provider_id", provider_id)
        .in_("key_number", key_numbers)
        .execute()
    )
    cooldown_map = {row["key_number"]: row.get("cooldown_until") for row in (status_res.data or [])}

    for idx, kn in enumerate(key_numbers):
        cd = cooldown_map.get(kn)
        if cd:
            try:
                ts = datetime.fromisoformat(cd)
                if ts > _now_utc():
                    continue  # still in cooldown
            except Exception:
                pass
        return idx  # index in the ordered list
    return None

"""Exported symbols"""
__all__ = [
    "is_key_in_cooldown",
    "record_key_error",
    "clear_key_success",
    "get_next_available_key",
]
