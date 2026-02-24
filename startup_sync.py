"""
Startup Sync - Full Data Transfer on Backend Startup
Ensures NEW account is fully synced with OLD account before backend starts
Runs automatically when app.py starts
"""

import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional
from dotenv_vault import load_dotenv
from supabase import create_client, Client

load_dotenv()

# OLD account configuration (source)
OLD_SUPABASE_URL = os.getenv('SUPABASE_URL')
OLD_SUPABASE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

# NEW account configuration (destination)
NEW_SUPABASE_URL = os.getenv('NEW_SUPABASE_URL')
NEW_SUPABASE_KEY = os.getenv('NEW_SUPABASE_SERVICE_ROLE_KEY') or os.getenv('NEW_SUPABASE_ANON_KEY')

# Enable/disable startup sync
ENABLE_STARTUP_SYNC = os.getenv('ENABLE_STARTUP_SYNC', 'true').lower() == 'true'

# Tables to sync (in dependency order)
SYNC_TABLES = ['users', 'jobs', 'workflow_executions', 'sessions', 'usage_logs', 'ad_sessions', 'shared_results']
BATCH_SIZE = 100


def print_info(text: str):
    """Print info message"""
    print(f"[STARTUP-SYNC] {text}")


def print_success(text: str):
    """Print success message"""
    print(f"[STARTUP-SYNC] ✅ {text}")


def print_error(text: str):
    """Print error message"""
    print(f"[STARTUP-SYNC] ❌ {text}")


def print_warning(text: str):
    """Print warning message"""
    print(f"[STARTUP-SYNC] ⚠️  {text}")


def get_last_sync_time(new_client: Client) -> Optional[str]:
    """Get last successful sync timestamp from NEW account"""
    try:
        result = new_client.table('sync_metadata')\
            .select('last_sync_timestamp')\
            .eq('sync_type', 'hourly')\
            .order('last_sync_timestamp', desc=True)\
            .limit(1)\
            .execute()
        
        if result.data and len(result.data) > 0:
            return result.data[0]['last_sync_timestamp']
        else:
            # No sync history - sync all data from 1 week ago
            return (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            
    except Exception as e:
        print_warning(f"Could not get last sync time: {e}")
        # Default to 1 week ago
        return (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()


def update_sync_metadata(new_client: Client, status: str, last_sync_time: str, 
                        sync_counts: Optional[Dict] = None, error_message: Optional[str] = None):
    """Update sync status in NEW account"""
    try:
        update_data = {
            'sync_type': 'startup',
            'sync_status': status,
            'last_sync_timestamp': last_sync_time,
            'updated_at': datetime.now(timezone.utc).isoformat()
        }
        
        if sync_counts:
            update_data['records_synced'] = sync_counts
        
        if error_message:
            update_data['error_message'] = error_message
        
        new_client.table('sync_metadata').insert(update_data).execute()
        
    except Exception as e:
        print_warning(f"Could not update sync metadata: {e}")


def sync_user_dependencies(old_client: Client, new_client: Client, user_ids: set) -> int:
    """Sync missing users that are referenced by other tables"""
    if not user_ids:
        return 0
    
    try:
        print_info(f"   Syncing {len(user_ids)} missing parent users...")
        
        # Fetch users from OLD account
        users_data = old_client.table('users')\
            .select('*')\
            .in_('id', list(user_ids))\
            .execute()
        
        if not users_data.data or len(users_data.data) == 0:
            return 0
        
        # Upsert to NEW account
        new_client.table('users').upsert(users_data.data).execute()
        synced = len(users_data.data)
        print_info(f"   Synced {synced} missing users")
        return synced
        
    except Exception as e:
        print_error(f"   Error syncing missing users: {e}")
        return 0


def sync_table(old_client: Client, new_client: Client, table_name: str, 
               last_sync_time: str, batch_size: int = BATCH_SIZE) -> int:
    """Sync a single table from OLD to NEW account"""
    try:
        print_info(f"Syncing {table_name}...")
        
        # jobs and workflow_executions use updated_at to catch status/step changes on existing rows
        timestamp_field = 'updated_at' if table_name in ('jobs', 'workflow_executions') else 'created_at'
        
        data = old_client.table(table_name)\
            .select('*')\
            .gte(timestamp_field, last_sync_time)\
            .execute()
        
        if not data.data or len(data.data) == 0:
            print_info(f"{table_name}: No new records to sync")
            return 0
        
        total_records = len(data.data)
        print_info(f"{table_name}: Found {total_records} records to sync")
        
        # Check for missing parent users
        if table_name in ['jobs', 'sessions', 'ad_sessions', 'shared_results', 'workflow_executions'] and total_records > 0:
            user_ids = set(record.get('user_id') for record in data.data if record.get('user_id'))
            
            if user_ids:
                existing_users = new_client.table('users')\
                    .select('id')\
                    .in_('id', list(user_ids))\
                    .execute()
                
                existing_user_ids = set(u['id'] for u in existing_users.data) if existing_users.data else set()
                missing_user_ids = user_ids - existing_user_ids
                
                if missing_user_ids:
                    sync_user_dependencies(old_client, new_client, missing_user_ids)
        
        # Check for missing parent jobs for workflow_executions (FK: job_id -> jobs.job_id)
        if table_name == 'workflow_executions' and total_records > 0:
            job_ids = set(record.get('job_id') for record in data.data if record.get('job_id'))
            if job_ids:
                existing_jobs = new_client.table('jobs')\
                    .select('job_id')\
                    .in_('job_id', list(job_ids))\
                    .execute()
                existing_job_ids = set(j['job_id'] for j in existing_jobs.data) if existing_jobs.data else set()
                missing_job_ids = job_ids - existing_job_ids
                if missing_job_ids:
                    print_warning(f"   Found {len(missing_job_ids)} parent jobs not in NEW account, syncing...")
                    try:
                        missing_jobs_data = old_client.table('jobs')\
                            .select('*')\
                            .in_('job_id', list(missing_job_ids))\
                            .execute()
                        if missing_jobs_data.data:
                            new_client.table('jobs').upsert(missing_jobs_data.data).execute()
                            print_info(f"   Synced {len(missing_jobs_data.data)} missing parent jobs")
                    except Exception as job_sync_err:
                        print_error(f"   Failed to sync missing parent jobs: {job_sync_err}")
        
        # Sync in batches
        synced_count = 0
        for i in range(0, total_records, batch_size):
            batch = data.data[i:i + batch_size]
            
            try:
                new_client.table(table_name).upsert(batch).execute()
                synced_count += len(batch)
                
                if total_records > batch_size:
                    print_info(f"   Batch {i//batch_size + 1}: {len(batch)} records synced")
                    
            except Exception as batch_error:
                print_error(f"   Batch {i//batch_size + 1} failed: {batch_error}")
                continue
        
        print_success(f"{table_name}: {synced_count}/{total_records} records synced")
        return synced_count
        
    except Exception as e:
        print_error(f"Error syncing {table_name}: {e}")
        return 0


def run_startup_sync() -> bool:
    """
    Main startup sync function
    Returns True if sync completed successfully or was skipped
    Returns False if sync failed
    """
    # Check if startup sync is enabled
    if not ENABLE_STARTUP_SYNC:
        print_info("Startup sync is DISABLED (set ENABLE_STARTUP_SYNC=true in .env)")
        return True  # Not an error, just disabled
    
    # Check if we have both accounts configured
    if not OLD_SUPABASE_URL or not OLD_SUPABASE_KEY:
        print_info("OLD account not configured - skipping startup sync")
        return True  # Not an error if OLD account not configured
    
    if not NEW_SUPABASE_URL or not NEW_SUPABASE_KEY:
        print_info("NEW account not configured - skipping startup sync")
        return True  # Not an error if NEW account not configured
    
    print_info("========================================")
    print_info("STARTUP SYNC - Transferring data from OLD to NEW account")
    print_info("========================================")
    
    try:
        # Connect to both accounts
        print_info("Connecting to OLD account...")
        old_client = create_client(OLD_SUPABASE_URL, OLD_SUPABASE_KEY)
        old_client.table('users').select('id').limit(1).execute()
        print_success("OLD account connected")
        
        print_info("Connecting to NEW account...")
        new_client = create_client(NEW_SUPABASE_URL, NEW_SUPABASE_KEY)
        new_client.table('users').select('id').limit(1).execute()
        print_success("NEW account connected")
        
        # Get last sync time
        last_sync_time = get_last_sync_time(new_client)
        current_time = datetime.now(timezone.utc).isoformat()
        
        print_info(f"Syncing data created after: {last_sync_time}")
        
        # Mark sync as in-progress
        update_sync_metadata(new_client, 'in_progress', last_sync_time)
        
        # Sync each table
        sync_counts = {}
        total_synced = 0
        
        for table_name in SYNC_TABLES:
            try:
                count = sync_table(old_client, new_client, table_name, last_sync_time)
                sync_counts[table_name] = count
                total_synced += count
            except Exception as table_error:
                print_error(f"Failed to sync {table_name}: {table_error}")
                sync_counts[table_name] = 0
                continue
        
        # Update sync metadata
        update_sync_metadata(new_client, 'completed', current_time, sync_counts)
        
        print_info("========================================")
        print_success(f"STARTUP SYNC COMPLETED - {total_synced} records synced")
        print_info(f"Details: {sync_counts}")
        print_info("========================================")
        
        return True
        
    except Exception as e:
        print_error(f"Startup sync failed: {e}")
        try:
            new_client = create_client(NEW_SUPABASE_URL, NEW_SUPABASE_KEY)
            last_sync_time = get_last_sync_time(new_client)
            update_sync_metadata(new_client, 'failed', last_sync_time, error_message=str(e))
        except:
            pass
        
        # Don't block app startup on sync failure
        print_warning("Continuing with app startup despite sync failure")
        return True


if __name__ == "__main__":
    success = run_startup_sync()
    sys.exit(0 if success else 1)
