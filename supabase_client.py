"""
Supabase Client Configuration with Auto-Failover
Provides connection to Supabase database and storage
Automatically switches to backup account on rate limits
"""

import os
from supabase import Client
from dotenv_vault import load_dotenv
from supabase_failover import get_failover_manager, get_supabase_client

# Load environment variables
load_dotenv()

# Initialize failover manager
_failover_manager = get_failover_manager()

# Get active Supabase client (dynamically switches between main/backup)
@property
def supabase() -> Client:
    """Get current active Supabase client (main or backup)"""
    return get_supabase_client()

# For backward compatibility, create a client proxy
class _SupabaseProxy:
    """Proxy that dynamically returns the active Supabase client"""
    
    def __getattr__(self, name):
        return getattr(get_supabase_client(), name)
    
    def __call__(self, *args, **kwargs):
        return get_supabase_client()(*args, **kwargs)

# Export as 'supabase' for backward compatibility
supabase = _SupabaseProxy()

print(f"[OK] Supabase client with auto-failover initialized")
