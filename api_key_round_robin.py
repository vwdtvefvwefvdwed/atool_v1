"""
Round-Robin API Key Rotation Manager
Maintains per-provider rotation state to evenly distribute API key usage.
Uses in-memory state with optional JSON persistence for restart recovery.
"""

import os
import json
import threading
from typing import Dict, Optional
from pathlib import Path

STATE_FILE = Path(__file__).parent / "api_rotation_state.json"

rotation_state: Dict[str, Dict[str, int]] = {}

provider_locks: Dict[str, threading.Lock] = {}


def get_provider_lock(provider_key: str) -> threading.Lock:
    """Get or create a lock for a provider"""
    if provider_key not in provider_locks:
        provider_locks[provider_key] = threading.Lock()
    return provider_locks[provider_key]


def load_rotation_state():
    """Load rotation state from JSON file (if exists)"""
    global rotation_state
    
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r') as f:
                rotation_state = json.load(f)
            print(f"[ROTATION] Loaded state from {STATE_FILE}")
            print(f"[ROTATION] Current state: {rotation_state}")
        except Exception as e:
            print(f"[ROTATION] Failed to load state: {e}")
            rotation_state = {}
    else:
        rotation_state = {}
        print("[ROTATION] No saved state found, starting fresh")


def save_rotation_state():
    """Save rotation state to JSON file"""
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(rotation_state, f, indent=2)
    except Exception as e:
        print(f"[ROTATION] Failed to save state: {e}")


def count_keys_for_provider(provider_id: str, supabase_client) -> int:
    """
    Query database to get total number of API keys for a provider.
    
    Args:
        provider_id: UUID of the provider
        supabase_client: Supabase client instance
        
    Returns:
        Total count of API keys for this provider
    """
    try:
        result = supabase_client.table("provider_api_keys")\
            .select("id", count="exact")\
            .eq("provider_id", provider_id)\
            .execute()
        
        total = result.count if hasattr(result, 'count') and result.count is not None else 0
        return total
    except Exception as e:
        print(f"[ROTATION] Failed to count keys for provider {provider_id}: {e}")
        return 0


def get_next_row_for_provider(provider_key: str, provider_id: str, supabase_client) -> int:
    """
    Get the next row number (key_number) to use for this provider.
    Uses round-robin rotation with live count query.
    
    Args:
        provider_key: Provider name (e.g., 'vision-atlas')
        provider_id: UUID of the provider
        supabase_client: Supabase client instance
        
    Returns:
        Next row number (0-based index)
    """
    lock = get_provider_lock(provider_key)
    
    with lock:
        total_keys = count_keys_for_provider(provider_id, supabase_client)
        
        if total_keys == 0:
            print(f"[ROTATION] No keys found for provider '{provider_key}'")
            return 0
        
        if provider_key not in rotation_state:
            rotation_state[provider_key] = {'current_row': 0}
            print(f"[ROTATION] Initialized provider '{provider_key}' at row 0")
        
        current_row = rotation_state[provider_key]['current_row']
        
        if current_row >= total_keys:
            current_row = 0
            rotation_state[provider_key]['current_row'] = 0
            print(f"[ROTATION] Provider '{provider_key}' wrapped around to row 0")
        
        next_row = current_row
        print(f"[ROTATION] Provider '{provider_key}' using row {next_row} (total: {total_keys})")
        
        return next_row


def mark_row_used(provider_key: str, row_number: int, save_to_disk: bool = True):
    """
    Mark a row as used and increment to the next row for this provider.
    
    Args:
        provider_key: Provider name (e.g., 'vision-atlas')
        row_number: The row that was just used
        save_to_disk: Whether to persist state to JSON file
    """
    lock = get_provider_lock(provider_key)
    
    with lock:
        if provider_key not in rotation_state:
            rotation_state[provider_key] = {'current_row': 0}
        
        rotation_state[provider_key]['current_row'] = row_number + 1
        
        print(f"[ROTATION] Provider '{provider_key}' incremented: row {row_number} -> {row_number + 1}")
        
        if save_to_disk:
            save_rotation_state()


def reset_provider(provider_key: str):
    """
    Reset provider's rotation back to row 0.
    Used when all keys have been exhausted or for manual reset.
    
    Args:
        provider_key: Provider name (e.g., 'vision-atlas')
    """
    lock = get_provider_lock(provider_key)
    
    with lock:
        rotation_state[provider_key] = {'current_row': 0}
        print(f"[ROTATION] Provider '{provider_key}' reset to row 0")
        save_rotation_state()


def get_current_state() -> Dict[str, Dict[str, int]]:
    """
    Get a copy of the current rotation state.
    
    Returns:
        Dictionary mapping provider_key to {current_row}
    """
    return rotation_state.copy()


load_rotation_state()
