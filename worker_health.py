"""
Worker Health Check - Keep Worker Accounts Active
Pings all 3 worker Supabase accounts to prevent auto-pause
"""

import os
import time
import uuid
import threading
from datetime import datetime
from dotenv_vault import load_dotenv
from supabase import create_client

load_dotenv()

# Worker configurations
WORKERS = [
    {
        'id': 'worker-1',
        'url': os.getenv('WORKER_1_URL'),
        'key': os.getenv('WORKER_1_ANON_KEY'),
    },
    {
        'id': 'worker-2',
        'url': os.getenv('WORKER_2_URL'),
        'key': os.getenv('WORKER_2_ANON_KEY'),
    },
    {
        'id': 'worker-3',
        'url': os.getenv('WORKER_3_URL'),
        'key': os.getenv('WORKER_3_ANON_KEY'),
    }
]


def ping_worker(worker_config):
    """
    Ping a single worker to keep it active
    Uses INSERT then DELETE to create minimal activity
    """
    try:
        client = create_client(worker_config['url'], worker_config['key'])
        
        # Create a temporary health check entry
        health_data = {
            'user_id': str(uuid.uuid4()),
            'job_id': str(uuid.uuid4()),
            'request_payload': {
                'health_check': True,
                'timestamp': datetime.utcnow().isoformat()
            }
        }
        
        # Insert into priority1_queue
        insert_response = client.table('priority1_queue').insert(health_data).execute()
        
        if insert_response.data:
            # Keep the record for 5 seconds before deleting (ensures Supabase counts it as activity)
            queue_id = insert_response.data[0].get('queue_id')
            if queue_id:
                time.sleep(5)  # Wait 5 seconds to ensure activity is registered
                client.table('priority1_queue').delete().eq('queue_id', queue_id).execute()
            
            print(f"✅ {worker_config['id']} health check successful")
            return True
        else:
            print(f"⚠️  {worker_config['id']} health check - no data returned")
            return False
            
    except Exception as e:
        print(f"❌ {worker_config['id']} health check failed: {str(e)}")
        return False


def ping_all_workers():
    """
    Ping all workers to keep them active
    Runs synchronously on startup
    """
    print("\n🏥 Worker Health Check - Pinging all workers...")
    
    for worker in WORKERS:
        if worker['url'] and worker['key']:
            ping_worker(worker)
        else:
            print(f"⚠️  {worker['id']} not configured - skipping")
    
    print("🏥 Worker health check complete\n")


def periodic_ping_workers():
    """
    Periodically ping all workers every 24 hours
    Runs in background thread to keep workers active continuously
    """
    while True:
        try:
            time.sleep(24 * 60 * 60)  # Wait 24 hours
            print(f"\n⏰ Scheduled health check (every 24 hours)")
            ping_all_workers()
        except Exception as e:
            print(f"❌ Periodic health check error: {str(e)}")


def ping_all_workers_async():
    """
    Ping all workers in background thread (non-blocking)
    Use this for startup to avoid delaying application launch
    Also starts the periodic 24-hour health check scheduler
    """
    # Initial ping on startup
    thread = threading.Thread(target=ping_all_workers, daemon=True)
    thread.start()
    
    # Start periodic pinging every 24 hours
    scheduler_thread = threading.Thread(target=periodic_ping_workers, daemon=True)
    scheduler_thread.start()


if __name__ == "__main__":
    # Test the health check
    ping_all_workers()
