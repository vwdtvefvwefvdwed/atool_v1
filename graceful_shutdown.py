"""
Graceful Shutdown Script
Stops backend from accepting new jobs, waits for current jobs to finish, then terminates
"""

import os
import sys
import time
import psutil
import signal
from pathlib import Path
from dotenv_vault import load_dotenv

load_dotenv()

MAINTENANCE_FLAG_FILE = Path(__file__).parent / ".maintenance_mode"
CHECK_INTERVAL = 5

def set_maintenance_mode():
    """Create maintenance flag file to stop new jobs"""
    MAINTENANCE_FLAG_FILE.touch()
    print("=" * 60)
    print("MAINTENANCE MODE ACTIVATED")
    print("=" * 60)
    print("Backend will no longer accept new jobs.")
    print("Waiting for current jobs to complete...\n")

def clear_maintenance_mode():
    """Remove maintenance flag file"""
    if MAINTENANCE_FLAG_FILE.exists():
        MAINTENANCE_FLAG_FILE.unlink()
        print("\nMaintenance mode cleared.")

def check_pending_jobs():
    """Check if there are any pending or in-progress jobs"""
    try:
        # Import here to avoid circular imports
        import sys
        backend_dir = Path(__file__).parent
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
        
        from supabase_client import supabase
        
        print("   Querying database for active jobs...")
        
        # Check for jobs that are running (ignore pending during shutdown)
        response = supabase.table("jobs").select("job_id, status, job_type, model").eq("status", "running").execute()
        
        if response.data:
            jobs = response.data
            print(f"   Found {len(jobs)} active job(s) in database")
            return len(jobs), jobs
        else:
            print("   No active jobs found")
            return 0, []
            
    except Exception as e:
        print(f"   [ERROR] Error checking jobs: {e}")
        import traceback
        traceback.print_exc()
        print("   [WARN] Cannot verify job status - proceeding with caution")
        print("   [WARN] Manual verification recommended!")
        # Return -1 to indicate error state
        return -1, []

def find_process_by_script(script_name):
    """Find process ID by script name"""
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmdline = proc.info.get('cmdline', [])
            if cmdline and any(script_name in arg for arg in cmdline):
                return proc.info['pid']
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None

def terminate_process(pid, name, timeout=30):
    """Gracefully terminate a process"""
    try:
        proc = psutil.Process(pid)
        print(f"\nTerminating {name} (PID: {pid})...")
        
        # Send SIGTERM (graceful shutdown)
        proc.send_signal(signal.SIGTERM)
        
        # Wait for process to terminate
        try:
            proc.wait(timeout=timeout)
            print(f"[OK] {name} terminated gracefully")
            return True
        except psutil.TimeoutExpired:
            print(f"[WARN] {name} didn't stop gracefully, force killing...")
            proc.kill()
            proc.wait(timeout=5)
            print(f"[OK] {name} force killed")
            return True
            
    except psutil.NoSuchProcess:
        print(f"[OK] {name} already stopped")
        return True
    except Exception as e:
        print(f"[ERROR] Error terminating {name}: {e}")
        return False

def main():
    print("\n" + "=" * 60)
    print("GRACEFUL SHUTDOWN SCRIPT")
    print("=" * 60)
    
    # Initial check
    print("\nInitial System Status:")
    print("-" * 60)
    
    # Check current job status
    initial_count, initial_jobs = check_pending_jobs()
    if initial_count == -1:
        print("Cannot connect to database - shutdown may not be safe!")
        user_input = input("\nContinue anyway? (yes/no): ").strip().lower()
        if user_input != 'yes':
            print("Shutdown cancelled.")
            sys.exit(0)
    elif initial_count > 0:
        print(f"   {initial_count} active job(s) detected")
        for job in initial_jobs[:5]:
            print(f"   - {job.get('job_id')}: {job.get('status')} | {job.get('job_type')} | {job.get('model')}")
    else:
        print("[OK] No active jobs")
    
    # Check running processes
    app_pid = find_process_by_script("app.py")
    worker_pid = find_process_by_script("job_worker_realtime.py")
    
    if app_pid:
        print(f"[OK] app.py running (PID: {app_pid})")
    else:
        print("[--] app.py not running")
    
    if worker_pid:
        print(f"[OK] job_worker_realtime.py running (PID: {worker_pid})")
    else:
        print("[--] job_worker_realtime.py not running")
    
    print("-" * 60)
    user_input = input("\nProceed with graceful shutdown? (yes/no): ").strip().lower()
    if user_input != 'yes':
        print("Shutdown cancelled by user.")
        sys.exit(0)
    
    # Step 1: Set maintenance mode
    set_maintenance_mode()
    
    # Step 2: Wait for all jobs to complete
    print("\nMonitoring active jobs...")
    wait_count = 0
    max_wait = 600  # 10 minutes maximum wait
    
    while wait_count < max_wait:
        job_count, jobs = check_pending_jobs()
        
        # Error checking jobs
        if job_count == -1:
            print("\nCannot verify job status due to database error!")
            user_input = input("Continue with shutdown anyway? (yes/no): ").strip().lower()
            if user_input != 'yes':
                print("Shutdown cancelled by user.")
                clear_maintenance_mode()
                sys.exit(0)
            else:
                print("Proceeding with shutdown despite error...")
                break
        
        # No jobs running
        if job_count == 0:
            print("[OK] All jobs completed!")
            break
        
        # Jobs still running
        print(f"[WAIT] {job_count} job(s) still running... (waited {wait_count}s)")
        for job in jobs[:5]:  # Show first 5 jobs
            print(f"   - Job {job.get('job_id', 'unknown')}: {job.get('status', 'unknown')} | {job.get('job_type', 'unknown')} | {job.get('model', 'unknown')}")
        
        time.sleep(CHECK_INTERVAL)
        wait_count += CHECK_INTERVAL
    
    if wait_count >= max_wait:
        print(f"\nTimeout reached ({max_wait}s)!")
        print(f"{job_count} job(s) still running but max wait time exceeded")
        user_input = input("Force shutdown anyway? (yes/no): ").strip().lower()
        if user_input != 'yes':
            print("Shutdown cancelled by user.")
            clear_maintenance_mode()
            sys.exit(0)
        else:
            print("Forcing shutdown...")
    
    # Step 3: Find and terminate processes
    print("\n" + "=" * 60)
    print("TERMINATING PROCESSES")
    print("=" * 60)
    
    # Find app.py
    app_pid = find_process_by_script("app.py")
    if app_pid:
        terminate_process(app_pid, "app.py")
    else:
        print("[OK] app.py not running")
    
    # Find job_worker_realtime.py
    worker_pid = find_process_by_script("job_worker_realtime.py")
    if worker_pid:
        terminate_process(worker_pid, "job_worker_realtime.py")
    else:
        print("[OK] job_worker_realtime.py not running")
    
    # Step 4: Clear maintenance mode
    clear_maintenance_mode()
    
    print("\n" + "=" * 60)
    print("SHUTDOWN COMPLETE")
    print("=" * 60)
    print("Backend services have been stopped.")
    print("To restart: Run 'python app.py' and 'python job_worker_realtime.py'\n")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠ Shutdown interrupted by user")
        clear_maintenance_mode()
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Error during shutdown: {e}")
        import traceback
        traceback.print_exc()
        clear_maintenance_mode()
        sys.exit(1)
