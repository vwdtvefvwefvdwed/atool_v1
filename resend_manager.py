"""
Resend API Key Rotation Manager
Automatically switches to backup API key when quota is exceeded
"""

import os
import resend
from dotenv_vault import load_dotenv
from error_notifier import notify_error, ErrorType

load_dotenv()

RESEND_API_KEY_PRIMARY = os.getenv("RESEND_API_KEY")
RESEND_API_KEY_BACKUP = os.getenv("RESEND_API_KEY_BACKUP")


class ResendManager:
    """Manages Resend API key rotation"""
    
    def __init__(self):
        self.current_key = RESEND_API_KEY_PRIMARY
        self.is_using_backup = False
        resend.api_key = self.current_key
    
    def rotate_to_backup(self):
        """Switch to backup API key when primary quota is exceeded"""
        if self.is_using_backup:
            print("[RESEND] Already using backup key, cannot rotate further")
            self._notify_backup_failed()
            return False
        
        if not RESEND_API_KEY_BACKUP or RESEND_API_KEY_BACKUP == "re_BACKUP_KEY_HERE":
            print("[RESEND] No backup API key configured")
            self._notify_backup_failed()
            return False
        
        print("[RESEND] Rotating to backup API key...")
        self.current_key = RESEND_API_KEY_BACKUP
        self.is_using_backup = True
        resend.api_key = self.current_key
        
        self._notify_backup_activated()
        print("[RESEND] Successfully rotated to backup API key")
        return True
    
    def is_quota_exceeded_error(self, error) -> bool:
        """Check if error is due to monthly quota exceeded"""
        error_str = str(error).lower()
        
        if "429" in error_str and "monthly_quota_exceeded" in error_str:
            return True
        
        if "monthly email quota" in error_str:
            return True
        
        if "you have reached your monthly email quota" in error_str:
            return True
        
        return False
    
    def handle_resend_error(self, error):
        """
        Handle Resend API errors and rotate if quota exceeded
        
        Returns:
            True if error was quota exceeded and rotation attempted
            False if error is not quota-related
        """
        if self.is_quota_exceeded_error(error):
            print(f"[RESEND] Quota exceeded: {error}")
            self._notify_quota_exceeded()
            return self.rotate_to_backup()
        
        return False
    
    def _notify_quota_exceeded(self):
        """Send notification when quota is exceeded"""
        notify_error(
            ErrorType.RESEND_QUOTA_EXCEEDED,
            "Resend primary account quota exceeded (3,000 emails/month limit reached)",
            context={
                "account": "Primary",
                "action": "Attempting rotation to backup",
                "limit": "3,000 emails/month"
            }
        )
    
    def _notify_backup_activated(self):
        """Send notification when backup is activated"""
        notify_error(
            ErrorType.RESEND_BACKUP_ACTIVATED,
            "Resend backup account activated successfully",
            context={
                "previous_account": "Primary",
                "current_account": "Backup",
                "reason": "Primary quota exceeded"
            }
        )
    
    def _notify_backup_failed(self):
        """Send notification when backup fails"""
        notify_error(
            ErrorType.RESEND_BACKUP_FAILED,
            "CRITICAL: Both Resend accounts unavailable",
            context={
                "status": "No backup key configured or already in use",
                "action_required": "Add backup API key or wait for monthly reset"
            }
        )
    
    def get_status(self) -> dict:
        """Get current status of Resend manager"""
        return {
            "is_using_backup": self.is_using_backup,
            "current_account": "Backup" if self.is_using_backup else "Primary",
            "has_backup_configured": bool(RESEND_API_KEY_BACKUP and RESEND_API_KEY_BACKUP != "re_BACKUP_KEY_HERE")
        }


resend_manager = ResendManager()
