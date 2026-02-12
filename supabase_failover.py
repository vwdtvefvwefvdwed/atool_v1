"""
Supabase Failover Manager
Automatically switches to backup Supabase account when rate limits are hit
Maintenance mode blocks new job creation until backend restart
"""

import os
import threading
from typing import Optional, Any
from datetime import datetime, timezone
from supabase import create_client, Client
from dotenv_vault import load_dotenv

load_dotenv()

# Account configurations
MAIN_SUPABASE_URL = os.getenv("SUPABASE_URL")
MAIN_SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

BACKUP_SUPABASE_URL = os.getenv("NEW_SUPABASE_URL")
BACKUP_SUPABASE_KEY = os.getenv("NEW_SUPABASE_SERVICE_ROLE_KEY") or os.getenv("NEW_SUPABASE_ANON_KEY")


def is_supabase_maintenance_window() -> bool:
    """
    Check if current time is within Supabase official maintenance window
    Maintenance Window: Jan 16, 2026 02:30-03:00 UTC
    """
    now = datetime.now(timezone.utc)
    maintenance_start = datetime(2026, 1, 16, 2, 30, tzinfo=timezone.utc)
    maintenance_end = datetime(2026, 1, 16, 3, 0, tzinfo=timezone.utc)
    return maintenance_start <= now <= maintenance_end


def is_maintenance_error(error: Exception, response=None) -> bool:
    """
    Detect if error is from Supabase official maintenance (NOT rate limit)
    
    Maintenance errors:
    - HTTP 503 Service Unavailable
    - "maintenance" in error message
    - "Error sending confirmation mail" (Auth email issue)
    
    Args:
        error: Exception object
        response: HTTP response object (if available)
        
    Returns:
        True if this is a maintenance error (don't switch accounts)
    """
    error_str = str(error).lower()
    
    # Check if in maintenance window
    if is_supabase_maintenance_window():
        # Check for maintenance-specific patterns
        if "503" in error_str or "service unavailable" in error_str:
            return True
        if "maintenance" in error_str:
            return True
        if "error sending confirmation mail" in error_str:
            return True
        if "unexpected_failure" in error_str and "mail" in error_str:
            return True
    
    # Check response status if available
    if response is not None:
        if hasattr(response, 'status_code') and response.status_code == 503:
            return True
    
    return False


class SupabaseFailoverManager:
    """Manages automatic failover between main and backup Supabase accounts"""
    
    def __init__(self):
        self._lock = threading.Lock()
        self._using_backup = False
        self._maintenance_mode = False
        self._failover_time: Optional[datetime] = None
        self._failover_reason: Optional[str] = None
        
        # Initialize clients
        if not MAIN_SUPABASE_URL or not MAIN_SUPABASE_KEY:
            raise ValueError("Main Supabase credentials not found in environment variables")
        
        self._main_client: Client = create_client(MAIN_SUPABASE_URL, MAIN_SUPABASE_KEY)
        
        if BACKUP_SUPABASE_URL and BACKUP_SUPABASE_KEY:
            self._backup_client: Client = create_client(BACKUP_SUPABASE_URL, BACKUP_SUPABASE_KEY)
            print(f"[OK] Supabase failover initialized: Main + Backup ready")
        else:
            self._backup_client = None
            print(f"[WARN] Backup Supabase not configured - failover disabled")
        
        print(f"[OK] Main Supabase: {MAIN_SUPABASE_URL}")
        if self._backup_client:
            print(f"[OK] Backup Supabase: {BACKUP_SUPABASE_URL}")
    
    @property
    def client(self) -> Client:
        """Get current active Supabase client"""
        with self._lock:
            if self._using_backup and self._backup_client:
                return self._backup_client
            return self._main_client
    
    @property
    def is_maintenance_mode(self) -> bool:
        """Check if system is in maintenance mode"""
        with self._lock:
            return self._maintenance_mode
    
    @property
    def is_using_backup(self) -> bool:
        """Check if currently using backup account"""
        with self._lock:
            return self._using_backup
    
    def get_status(self) -> dict:
        """Get current failover status"""
        with self._lock:
            return {
                "using_backup": self._using_backup,
                "maintenance_mode": self._maintenance_mode,
                "failover_time": self._failover_time.isoformat() if self._failover_time else None,
                "failover_reason": self._failover_reason,
                "backup_available": self._backup_client is not None,
                "main_url": MAIN_SUPABASE_URL,
                "backup_url": BACKUP_SUPABASE_URL if self._backup_client else None
            }
    
    def trigger_failover(self, reason: str):
        """
        Trigger failover to backup account
        Activates maintenance mode to block new job creation
        Broadcasts event to all connected frontend clients
        """
        if not self._backup_client:
            print(f"[ERROR] Cannot failover - no backup account configured")
            return False
        
        with self._lock:
            if self._using_backup:
                print(f"[INFO] Already using backup account")
                return True
            
            self._using_backup = True
            self._maintenance_mode = True
            self._failover_time = datetime.utcnow()
            self._failover_reason = reason
            
            print(f"\n{'='*80}")
            print(f"⚠️  FAILOVER TRIGGERED")
            print(f"{'='*80}")
            print(f"Reason: {reason}")
            print(f"Time: {self._failover_time}")
            print(f"Switched to: {BACKUP_SUPABASE_URL}")
            print(f"Maintenance Mode: ACTIVE")
            print(f"{'='*80}\n")
            
            # Broadcast failover event to all connected frontend clients
            try:
                from failover_broadcast import broadcast_failover_event, get_connected_client_count
                
                client_count = get_connected_client_count()
                print(f"[BROADCAST] Notifying {client_count} connected clients of failover")
                
                broadcast_failover_event({
                    "event": "failover",
                    "using_backup": True,
                    "main_url": MAIN_SUPABASE_URL,
                    "backup_url": BACKUP_SUPABASE_URL,
                    "failover_time": self._failover_time.isoformat(),
                    "failover_reason": reason,
                    "timestamp": datetime.utcnow().isoformat()
                })
                
                print(f"[BROADCAST] ✅ Failover event broadcast complete")
            except Exception as e:
                print(f"[BROADCAST] ⚠️ Failed to broadcast failover event: {e}")
                # Don't fail the failover if broadcast fails
            
            return True
    
    def detect_rate_limit_error(self, error: Exception, response=None) -> bool:
        """
        Detect if error is a rate limit error from Supabase
        Returns True if rate limit detected and failover triggered
        Returns False if maintenance error (no failover)
        
        Args:
            error: Exception object
            response: HTTP response object (optional)
        """
        error_str = str(error).lower()
        error_dict = {}
        
        # FIRST: Check if this is a maintenance error (DON'T trigger failover)
        if is_maintenance_error(error, response):
            print(f"[MAINTENANCE] Supabase official maintenance detected - NOT triggering failover")
            print(f"[MAINTENANCE] Error: {error}")
            return False
        
        # Try to parse error as dict if it has one
        if hasattr(error, '__dict__'):
            error_dict = error.__dict__
        
        # Check for rate limit patterns
        is_rate_limit = False
        reason = ""
        
        # 1. Auth (GoTrue) rate limits
        if "over_request_rate_limit" in error_str or "rate limit exceeded" in error_str:
            is_rate_limit = True
            reason = "Auth API rate limit exceeded"
        
        # 2. Database (PostgREST) rate limits
        elif "429" in error_str and "too many requests" in error_str:
            is_rate_limit = True
            reason = "Database API rate limit exceeded (429)"
        
        # 3. Edge Functions rate limits
        elif "ef009" in error_str or "ef047" in error_str:
            is_rate_limit = True
            reason = "Edge Functions rate limit exceeded"
        
        # 4. Management API rate limits
        elif "rate limit" in error_str and ("management" in error_str or "api" in error_str):
            is_rate_limit = True
            reason = "Management API rate limit exceeded"
        
        # 5. Generic 429 status code
        elif "429" in error_str:
            is_rate_limit = True
            reason = "HTTP 429 - Too Many Requests"
        
        if is_rate_limit:
            print(f"[DETECT] Rate limit error detected: {reason}")
            print(f"[DETECT] Error details: {error}")
            self.trigger_failover(reason)
            return True
        
        return False
    
    def wrap_request(self, func, *args, **kwargs) -> Any:
        """
        Wrap a Supabase request with automatic failover detection
        Usage: result = failover_manager.wrap_request(lambda: supabase.table('jobs').select('*').execute())
        """
        try:
            return func(*args, **kwargs)
        except Exception as e:
            # Check if it's a rate limit error
            if self.detect_rate_limit_error(e):
                # Retry with backup account
                try:
                    print(f"[RETRY] Retrying request with backup account...")
                    return func(*args, **kwargs)
                except Exception as retry_error:
                    print(f"[ERROR] Retry with backup failed: {retry_error}")
                    raise retry_error
            else:
                # Not a rate limit error, re-raise
                raise


# Global singleton instance
_failover_manager: Optional[SupabaseFailoverManager] = None


def get_failover_manager() -> SupabaseFailoverManager:
    """Get or create global failover manager instance"""
    global _failover_manager
    if _failover_manager is None:
        _failover_manager = SupabaseFailoverManager()
    return _failover_manager


def get_supabase_client() -> Client:
    """Get current active Supabase client (main or backup)"""
    return get_failover_manager().client


def safe_supabase_request(operation_func):
    """
    Decorator to wrap Supabase operations with automatic failover detection
    
    Usage:
        @safe_supabase_request
        def my_db_operation():
            return supabase.table('users').select('*').execute()
    """
    def wrapper(*args, **kwargs):
        try:
            return operation_func(*args, **kwargs)
        except Exception as e:
            manager = get_failover_manager()
            if manager.detect_rate_limit_error(e):
                # Failover triggered, retry with backup
                try:
                    print(f"[RETRY] Retrying operation with backup account...")
                    return operation_func(*args, **kwargs)
                except Exception as retry_error:
                    print(f"[ERROR] Retry failed: {retry_error}")
                    raise retry_error
            else:
                # Not a rate limit error, re-raise
                raise
    return wrapper


def execute_with_failover(operation_lambda):
    """
    Execute a Supabase operation with automatic failover detection
    
    Usage:
        result = execute_with_failover(
            lambda: supabase.table('users').select('*').execute()
        )
    """
    try:
        return operation_lambda()
    except Exception as e:
        manager = get_failover_manager()
        if manager.detect_rate_limit_error(e):
            # Failover triggered, retry with backup
            try:
                print(f"[RETRY] Retrying operation with backup account...")
                return operation_lambda()
            except Exception as retry_error:
                print(f"[ERROR] Retry failed: {retry_error}")
                raise retry_error
        else:
            # Not a rate limit error, re-raise
            raise
