"""
Test script to verify queue coordination setup
Tests both Worker1 and Main database configurations
"""
import os
from supabase import create_client
from dotenv_vault import load_dotenv

load_dotenv()

# Worker1 credentials (coordination database)
WORKER_1_URL = os.getenv("WORKER_1_URL")
WORKER_1_KEY = os.getenv("WORKER_1_SERVICE_ROLE_KEY")

# Main credentials (jobs database)
MAIN_URL = os.getenv("SUPABASE_URL")
MAIN_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

print("=" * 80)
print("QUEUE COORDINATION SETUP TEST")
print("=" * 80)
print()

# Test Worker1
print("📋 TESTING WORKER1 DATABASE")
print(f"   URL: {WORKER_1_URL}")
print()

worker1 = create_client(WORKER_1_URL, WORKER_1_KEY)

# Test job_queue_state
try:
    print("1️⃣  Testing job_queue_state table...")
    response = worker1.table('job_queue_state').select('*').eq('id', 1).single().execute()
    if response.data:
        print("   ✅ SUCCESS - job_queue_state exists")
        print(f"   📊 State: {response.data}")
    else:
        print("   ❌ FAIL - No data")
except Exception as e:
    print(f"   ❌ FAIL - {e}")
print()

# Test job_queue_log
try:
    print("2️⃣  Testing job_queue_log table...")
    response = worker1.table('job_queue_log').select('count', count='exact').execute()
    print(f"   ✅ SUCCESS - job_queue_log exists (entries: {response.count})")
except Exception as e:
    print(f"   ❌ FAIL - {e}")
print()

print("=" * 80)
print("📋 TESTING MAIN DATABASE")
print(f"   URL: {MAIN_URL}")
print()

main = create_client(MAIN_URL, MAIN_KEY)

# Test blocked_by_job_id column
try:
    print("3️⃣  Testing blocked_by_job_id column...")
    response = main.table('jobs').select('blocked_by_job_id').limit(1).execute()
    print("   ✅ SUCCESS - blocked_by_job_id column exists")
except Exception as e:
    error_str = str(e)
    if 'blocked_by_job_id' in error_str and 'does not exist' in error_str:
        print(f"   ❌ FAIL - Column doesn't exist")
        print(f"   💡 Run add_blocked_by_column_main.sql in Main database")
    else:
        print(f"   ❌ FAIL - {e}")
print()

print("=" * 80)
print("SUMMARY")
print("=" * 80)
print("If all tests pass, your queue coordination is properly set up!")
print("If any tests fail, run the corresponding SQL file:")
print("  - Worker1 errors → create_queue_state_worker1.sql")
print("  - Main errors → add_blocked_by_column_main.sql")
print("=" * 80)
