"""
Test script for ntfy error notification system
Sends test notifications to all configured topics
"""

import os
from dotenv_vault import load_dotenv

# Load environment variables
load_dotenv()

from error_notifier import notify_error, ErrorType

def test_all_topics():
    """Test notifications across all topics/categories"""
    
    print("="*60)
    print("TESTING NTFY ERROR NOTIFICATION SYSTEM")
    print("="*60)
    print()
    
    test_cases = [
        (ErrorType.REALTIME_LISTENER_CRASHED, "Test: Critical system error", 
         {"test": True, "category": "critical"}),
        
        (ErrorType.NO_API_KEY_FOR_PROVIDER, "Test: API key management error", 
         {"provider": "test-provider", "test": True}),
        
        (ErrorType.REPLICATE_API_ERROR, "Test: Provider endpoint error", 
         {"provider": "replicate", "test": True}),
        
        (ErrorType.CLOUDINARY_UPLOAD_FAILED, "Test: Storage error", 
         {"error": "test upload failure", "test": True}),
        
        (ErrorType.JOB_THREAD_CRASHED, "Test: Worker processing error", 
         {"job_id": "test-job-123", "test": True}),
    ]
    
    for i, (error_type, message, context) in enumerate(test_cases, 1):
        print(f"\n[{i}/{len(test_cases)}] Testing {error_type.name}...")
        notify_error(error_type, message, context)
        print(f"    Sent to topic: {error_type.value[0]}")
    
    print("\n" + "="*60)
    print("TEST COMPLETE")
    print("="*60)
    print("\nCheck your mobile device for 5 test notifications:")
    print("  1. Critical system error (atool-critical-xyz5656)")
    print("  2. API key error (atool-api-keys-5757)")
    print("  3. Provider error (atool-providers-5858)")
    print("  4. Storage error (atool-storage-5959)")
    print("  5. Worker error (atool-worker-6060)")
    print()

if __name__ == "__main__":
    test_all_topics()
