"""
Test PostgREST API access to job_queue_state table
(This mimics how the coordinator accesses the table)
"""
import os
import requests
from dotenv_vault import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

print("=" * 80)
print("Testing PostgREST API access to job_queue_state")
print("=" * 80)
print(f"URL: {SUPABASE_URL}/rest/v1/job_queue_state")
print()

headers = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

url = f"{SUPABASE_URL}/rest/v1/job_queue_state?id=eq.1"

print("🔍 Testing GET request (like coordinator does)...")
try:
    response = requests.get(url, headers=headers)
    print(f"   Status Code: {response.status_code}")
    
    if response.status_code == 200:
        print("   ✅ SUCCESS - PostgREST can access the table")
        print(f"   📊 Data: {response.json()}")
    else:
        print(f"   ❌ FAIL - HTTP {response.status_code}")
        print(f"   Error: {response.text}")
        
except Exception as e:
    print(f"   ❌ FAIL - Exception: {e}")

print()
print("=" * 80)
print("If you see a 404 or PGRST205 error, run this SQL in Supabase:")
print("   NOTIFY pgrst, 'reload schema';")
print("   ALTER TABLE job_queue_state DISABLE ROW LEVEL SECURITY;")
print("=" * 80)
