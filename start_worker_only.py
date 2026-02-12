"""
Start job worker WITHOUT Flask health check server
Use this when running worker alongside app.py in same container
"""
import sys
from job_worker_realtime import start_realtime

if __name__ == "__main__":
    print("="*60)
    print("STARTING JOB WORKER (NO HTTP SERVER)")
    print("="*60)
    print("Running in same container as app.py")
    print("Health checks handled by app.py on port 5000")
    print("="*60)
    
    try:
        start_realtime()
    except KeyboardInterrupt:
        print("\nWorker stopped")
        sys.exit(0)
