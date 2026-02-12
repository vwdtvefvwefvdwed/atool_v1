"""
Test Resend API Key Rotation System
Simulates quota exceeded error and tests backup rotation
"""

from resend_manager import resend_manager


def test_rotation():
    """Test the rotation system"""
    
    print("=== Resend API Key Rotation Test ===\n")
    
    # Check initial status
    status = resend_manager.get_status()
    print(f"Initial Status:")
    print(f"  - Current Account: {status['current_account']}")
    print(f"  - Using Backup: {status['is_using_backup']}")
    print(f"  - Backup Configured: {status['has_backup_configured']}")
    print()
    
    # Simulate quota exceeded error
    print("Simulating quota exceeded error...")
    
    # Test 1: Check if error is detected
    test_error_1 = Exception("API Error 429: monthly_quota_exceeded - You have reached your monthly email quota.")
    is_quota_error = resend_manager.is_quota_exceeded_error(test_error_1)
    print(f"  - Error detection (429 + monthly_quota_exceeded): {'[PASS]' if is_quota_error else '[FAIL]'}")
    
    test_error_2 = Exception("You have reached your monthly email quota.")
    is_quota_error_2 = resend_manager.is_quota_exceeded_error(test_error_2)
    print(f"  - Error detection (monthly email quota): {'[PASS]' if is_quota_error_2 else '[FAIL]'}")
    
    test_error_3 = Exception("Some other error")
    is_not_quota_error = not resend_manager.is_quota_exceeded_error(test_error_3)
    print(f"  - Non-quota error ignored: {'[PASS]' if is_not_quota_error else '[FAIL]'}")
    print()
    
    # Test 2: Rotation
    print("Testing backup rotation...")
    success = resend_manager.rotate_to_backup()
    print(f"  - Rotation to backup: {'[SUCCESS]' if success else '[FAILED]'}")
    print()
    
    # Check final status
    final_status = resend_manager.get_status()
    print(f"Final Status:")
    print(f"  - Current Account: {final_status['current_account']}")
    print(f"  - Using Backup: {final_status['is_using_backup']}")
    print()
    
    # Test 3: Try rotating again (should fail)
    print("Testing double rotation (should fail)...")
    second_rotation = resend_manager.rotate_to_backup()
    print(f"  - Second rotation prevented: {'[PASS]' if not second_rotation else '[FAIL]'}")
    print()
    
    print("=== Test Complete ===")
    print("\nNOTE: After backend restart, it will automatically use the primary account again.")
    print("To complete setup:")
    print("  1. Add your backup Resend API key to .env: RESEND_API_KEY_BACKUP=re_YourBackupKey")
    print("  2. Subscribe to ntfy topic 'atool-api-keys-5757' to receive notifications")
    print("  3. Restart backend to reset to primary account")


if __name__ == "__main__":
    test_rotation()
