"""
Quick script to check for active jobs in the database
Useful before running graceful shutdown
"""

import sys
from pathlib import Path
from dotenv_vault import load_dotenv

load_dotenv()

# Add backend to path
backend_dir = Path(__file__).parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from supabase_client import supabase

def check_active_jobs():
    """Check for pending and in-progress jobs"""
    print("\n" + "=" * 60)
    print("ACTIVE JOBS CHECK")
    print("=" * 60)
    
    try:
        # Get all pending jobs
        pending = supabase.table("jobs").select("*").eq("status", "pending").execute()
        pending_jobs = pending.data if pending.data else []
        
        # Get all running jobs
        running = supabase.table("jobs").select("*").eq("status", "running").execute()
        running_jobs = running.data if running.data else []
        
        total = len(pending_jobs) + len(running_jobs)
        
        print(f"\nSummary:")
        print(f"   Pending: {len(pending_jobs)}")
        print(f"   Running: {len(running_jobs)}")
        print(f"   Total Active: {total}")
        
        if total == 0:
            print("\nNo active jobs - safe to shutdown")
        else:
            print(f"\n{total} active job(s) found:")
            print("\n" + "-" * 60)
            
            if pending_jobs:
                print("\nPENDING JOBS:")
                for job in pending_jobs:
                    print(f"   ID: {job.get('job_id')}")
                    print(f"   Type: {job.get('job_type')}")
                    print(f"   Model: {job.get('model')}")
                    print(f"   User: {job.get('user_id')}")
                    print(f"   Created: {job.get('created_at')}")
                    print()
            
            if running_jobs:
                print("RUNNING JOBS:")
                for job in running_jobs:
                    print(f"   ID: {job.get('job_id')}")
                    print(f"   Type: {job.get('job_type')}")
                    print(f"   Model: {job.get('model')}")
                    print(f"   User: {job.get('user_id')}")
                    print(f"   Started: {job.get('updated_at')}")
                    print()
        
        print("=" * 60)
        
        return total
        
    except Exception as e:
        print(f"\nError checking jobs: {e}")
        import traceback
        traceback.print_exc()
        return -1

if __name__ == "__main__":
    check_active_jobs()
