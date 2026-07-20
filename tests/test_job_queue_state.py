"""
Test script to verify job_queue_state table exists and is accessible
"""
import os
from supabase import create_client
from envvault import load_env
load_env()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

print("=" * 80)
print("Testing job_queue_state table access")
print("=" * 80)
print(f"Supabase URL: {SUPABASE_URL}")
print(f"Using SERVICE_ROLE_KEY: {SUPABASE_KEY[:20]}...")
print()

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

try:
    print("1️⃣  Testing READ access to job_queue_state...")
    response = supabase.table('job_queue_state').select('*').eq('id', 1).single().execute()
    
    if response.data:
        print("   ✅ SUCCESS - Table exists and is readable")
        print(f"   📊 Current state: {response.data}")
    else:
        print("   ❌ FAIL - No data returned")
    print()
    
except Exception as e:
    print(f"   ❌ FAIL - Error reading table: {e}")
    print()

try:
    print("2️⃣  Testing UPDATE access to job_queue_state...")
    response = supabase.table('job_queue_state').update({
        'last_updated': 'now()'
    }).eq('id', 1).execute()
    
    print("   ✅ SUCCESS - Table is writable")
    print()
    
except Exception as e:
    print(f"   ❌ FAIL - Error updating table: {e}")
    print()

try:
    print("3️⃣  Testing setting active job...")
    test_job_id = "test-job-123"
    response = supabase.table('job_queue_state').update({
        'active_job_id': test_job_id,
        'active_job_type': 'normal',
        'active_models': ['test-model']
    }).eq('id', 1).execute()
    
    print(f"   ✅ SUCCESS - Set active_job_id to {test_job_id}")
    print()
    
except Exception as e:
    print(f"   ❌ FAIL - Error setting active job: {e}")
    print()

try:
    print("4️⃣  Testing clearing active job...")
    response = supabase.table('job_queue_state').update({
        'active_job_id': None,
        'active_job_type': None,
        'active_models': []
    }).eq('id', 1).execute()
    
    print("   ✅ SUCCESS - Cleared active job")
    print()
    
except Exception as e:
    print(f"   ❌ FAIL - Error clearing active job: {e}")
    print()

try:
    print("5️⃣  Final state check...")
    response = supabase.table('job_queue_state').select('*').eq('id', 1).single().execute()
    print(f"   📊 Final state: {response.data}")
    print()
    
except Exception as e:
    print(f"   ❌ FAIL - Error reading final state: {e}")
    print()

print("=" * 80)
print("Test complete")
print("=" * 80)
