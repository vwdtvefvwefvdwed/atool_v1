"""
Test script to mark a job as complete in Supabase
Helps identify if there are any issues with the database update
"""

import os
import sys
from datetime import datetime
from dotenv_vault import load_dotenv

load_dotenv()

try:
    from supabase_client import supabase
except ImportError:
    print("❌ Could not import supabase_client")
    print("Make sure supabase_client.py exists and Supabase is configured")
    sys.exit(1)

def test_mark_complete(job_id: str, test_image_url: str = None):
    """
    Test marking a job as complete
    
    Args:
        job_id: The job ID to mark as complete
        test_image_url: Optional test image URL (uses existing one if not provided)
    """
    print("\n" + "="*70)
    print("🧪 TEST: MARK JOB AS COMPLETE")
    print("="*70)
    print(f"Job ID: {job_id}")
    print("="*70 + "\n")
    
    try:
        # Step 1: Fetch current job status
        print("📥 Step 1: Fetching current job data...")
        response = supabase.table("jobs").select("*").eq("job_id", job_id).execute()
        
        if not response.data:
            print(f"❌ Job {job_id} not found in database")
            return False
        
        job = response.data[0]
        print(f"✅ Job found!")
        print(f"   Current Status: {job.get('status')}")
        print(f"   Current Progress: {job.get('progress')}")
        print(f"   Image URL: {job.get('image_url')}")
        print(f"   Video URL: {job.get('video_url')}")
        print(f"   Error Message: {job.get('error_message')}")
        print(f"   Created At: {job.get('created_at')}")
        print(f"   Completed At: {job.get('completed_at')}")
        
        # Step 2: Prepare update data
        print("\n📝 Step 2: Preparing update data...")
        
        # Use existing image_url if available, or test URL
        image_url = test_image_url or job.get('image_url') or "https://res.cloudinary.com/dczhbssip/image/upload/v1/test/test-image.jpg"
        
        update_data = {
            "status": "completed",
            "progress": 100,
            "completed_at": datetime.utcnow().isoformat(),
            "error_message": None
        }
        
        # Only update image_url if we have one
        if image_url:
            update_data["image_url"] = image_url
            update_data["thumbnail_url"] = image_url
        
        print(f"Update data: {update_data}")
        
        # Step 3: Execute update
        print("\n🔄 Step 3: Updating job in Supabase...")
        update_response = supabase.table("jobs").update(update_data).eq("job_id", job_id).execute()
        
        if not update_response.data:
            print(f"❌ Update failed - no data returned")
            return False
        
        print(f"✅ Update successful!")
        
        # Step 4: Verify update
        print("\n🔍 Step 4: Verifying update...")
        verify_response = supabase.table("jobs").select("*").eq("job_id", job_id).execute()
        
        if verify_response.data:
            updated_job = verify_response.data[0]
            print(f"✅ Job verified!")
            print(f"   New Status: {updated_job.get('status')}")
            print(f"   New Progress: {updated_job.get('progress')}")
            print(f"   Image URL: {updated_job.get('image_url')}")
            print(f"   Error Message: {updated_job.get('error_message')}")
            print(f"   Completed At: {updated_job.get('completed_at')}")
            
            # Check if update was successful
            if updated_job.get('status') == 'completed':
                print("\n" + "="*70)
                print("✅ SUCCESS: Job marked as completed!")
                print("="*70)
                return True
            else:
                print("\n" + "="*70)
                print(f"⚠️ WARNING: Status is '{updated_job.get('status')}' instead of 'completed'")
                print("="*70)
                return False
        else:
            print(f"❌ Could not verify update")
            return False
            
    except Exception as e:
        print(f"\n❌ Error during test: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_completion_endpoint(job_id: str, backend_url: str = None):
    """
    Test the /worker/job/{job_id}/complete endpoint
    
    Args:
        job_id: The job ID to mark as complete
        backend_url: Backend URL (defaults to env var)
    """
    import requests
    
    backend_url = backend_url or os.getenv("BACKEND_URL", "http://localhost:5000")
    
    print("\n" + "="*70)
    print("🧪 TEST: COMPLETION ENDPOINT")
    print("="*70)
    print(f"Job ID: {job_id}")
    print(f"Backend URL: {backend_url}")
    print("="*70 + "\n")
    
    try:
        # Get current job data
        print("📥 Fetching current job data...")
        response = supabase.table("jobs").select("*").eq("job_id", job_id).execute()
        
        if not response.data:
            print(f"❌ Job {job_id} not found")
            return False
        
        job = response.data[0]
        image_url = job.get('image_url') or "https://res.cloudinary.com/dczhbssip/image/upload/v1/test/test-image.jpg"
        
        print(f"✅ Job found - testing completion endpoint...")
        
        # Call completion endpoint
        endpoint = f"{backend_url}/worker/job/{job_id}/complete"
        payload = {
            "image_url": image_url,
            "thumbnail_url": image_url
        }
        
        print(f"\n🔗 Calling: POST {endpoint}")
        print(f"📦 Payload: {payload}")
        
        api_response = requests.post(endpoint, json=payload, timeout=10)
        
        print(f"\n📊 Response Status: {api_response.status_code}")
        print(f"📄 Response Body: {api_response.text}")
        
        if api_response.status_code == 200:
            print("\n✅ Endpoint returned success!")
            
            # Verify in database
            print("\n🔍 Verifying in database...")
            verify_response = supabase.table("jobs").select("status, progress, completed_at").eq("job_id", job_id).execute()
            
            if verify_response.data:
                updated_job = verify_response.data[0]
                print(f"   Status: {updated_job.get('status')}")
                print(f"   Progress: {updated_job.get('progress')}")
                print(f"   Completed At: {updated_job.get('completed_at')}")
                
                if updated_job.get('status') == 'completed':
                    print("\n" + "="*70)
                    print("✅ SUCCESS: Endpoint test passed!")
                    print("="*70)
                    return True
                else:
                    print("\n" + "="*70)
                    print(f"⚠️ WARNING: Endpoint succeeded but status is '{updated_job.get('status')}'")
                    print("="*70)
                    return False
        else:
            print(f"\n❌ Endpoint failed with status {api_response.status_code}")
            return False
            
    except Exception as e:
        print(f"\n❌ Error during endpoint test: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    # Default job ID from the logs
    default_job_id = "5ac6fd78-76a5-4fb7-80ee-307869f7bf24"
    
    # Get job_id from command line or use default
    if len(sys.argv) > 1:
        job_id = sys.argv[1]
    else:
        job_id = default_job_id
        print(f"ℹ️  No job_id provided, using default: {job_id}")
        print(f"   Usage: python test_mark_complete.py <job_id>\n")
    
    # Run both tests
    print("\n🚀 Starting tests...\n")
    
    # Test 1: Direct database update
    result1 = test_mark_complete(job_id)
    
    # Test 2: API endpoint
    result2 = test_completion_endpoint(job_id)
    
    # Summary
    print("\n" + "="*70)
    print("📊 TEST SUMMARY")
    print("="*70)
    print(f"Direct DB Update: {'✅ PASSED' if result1 else '❌ FAILED'}")
    print(f"API Endpoint Test: {'✅ PASSED' if result2 else '❌ FAILED'}")
    print("="*70 + "\n")
    
    if result1 and result2:
        print("🎉 All tests passed!")
        sys.exit(0)
    else:
        print("⚠️ Some tests failed - check output above for details")
        sys.exit(1)
