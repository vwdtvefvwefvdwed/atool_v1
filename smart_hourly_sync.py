"""
Smart Hourly Sync - State-Based Backup System
Syncs data from OLD Supabase account to NEW account incrementally
Uses last sync timestamp to ensure no data loss even if syncs are missed
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, List
from dotenv_vault import load_dotenv
from supabase import create_client, Client

load_dotenv()

# OLD account configuration (source - current production)
OLD_SUPABASE_URL = os.getenv('SUPABASE_URL')
OLD_SUPABASE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

# NEW account configuration (destination - migration target)
NEW_SUPABASE_URL = os.getenv('NEW_SUPABASE_URL')
NEW_SUPABASE_KEY = os.getenv('NEW_SUPABASE_SERVICE_ROLE_KEY') or os.getenv('NEW_SUPABASE_ANON_KEY')

# Sync configuration
ENABLE_SYNC = os.getenv('ENABLE_HOURLY_SYNC', 'false').lower() == 'true'
SYNC_TABLES = ['users', 'jobs', 'workflow_executions', 'sessions', 'usage_logs', 'ad_sessions', 'shared_results']
BATCH_SIZE = 100  # Process records in batches to avoid memory issues


def print_header(text: str):
    """Print formatted header"""
    print("\n" + "=" * 80)
    print(f"  {text}")
    print("=" * 80)


def print_success(text: str):
    """Print success message"""
    print(f"[OK] {text}")


def print_error(text: str):
    """Print error message"""
    print(f"[ERROR] {text}")


def print_info(text: str):
    """Print info message"""
    print(f"[INFO] {text}")


def print_warning(text: str):
    """Print warning message"""
    print(f"[WARN] {text}")


def get_last_sync_time(new_client: Client) -> Optional[str]:
    """
    Get last successful sync timestamp from NEW account's sync_metadata table
    
    Args:
        new_client: Supabase client for NEW account
        
    Returns:
        ISO timestamp string or None if not found
    """
    try:
        result = new_client.table('sync_metadata')\
            .select('last_sync_timestamp')\
            .eq('sync_type', 'hourly')\
            .order('last_sync_timestamp', desc=True)\
            .limit(1)\
            .execute()
        
        if result.data and len(result.data) > 0:
            timestamp = result.data[0]['last_sync_timestamp']
            print_info(f"Last sync timestamp from database: {timestamp}")
            return timestamp
        else:
            # First run - start from 24 hours ago
            fallback_time = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            print_warning(f"No sync metadata found. Starting from 24 hours ago: {fallback_time}")
            return fallback_time
            
    except Exception as e:
        print_error(f"Could not get last sync time: {e}")
        # Fallback to 1 hour ago
        fallback_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        print_warning(f"Using fallback time (1 hour ago): {fallback_time}")
        return fallback_time


def update_sync_metadata(new_client: Client, status: str, last_sync_time: str, 
                        sync_counts: Optional[Dict] = None, error_message: Optional[str] = None):
    """
    Update sync status in NEW account
    
    Args:
        new_client: Supabase client for NEW account
        status: Sync status (in_progress, completed, failed)
        last_sync_time: Timestamp to use as checkpoint (required for NOT NULL constraint)
        sync_counts: Dictionary of table names and record counts
        error_message: Error message if status is failed
    """
    try:
        update_data = {
            'sync_type': 'hourly',
            'sync_status': status,
            'last_sync_timestamp': last_sync_time,  # Always required (NOT NULL)
            'updated_at': datetime.now(timezone.utc).isoformat()
        }
        
        if sync_counts:
            update_data['records_synced'] = sync_counts
        
        if error_message:
            update_data['error_message'] = error_message
        
        # Insert new record (keeps history)
        new_client.table('sync_metadata').insert(update_data).execute()
        
    except Exception as e:
        print_warning(f"Could not update sync metadata: {e}")


def sync_user_dependencies(old_client: Client, new_client: Client, user_ids: set) -> int:
    """
    Sync missing users that are referenced by jobs/sessions but don't exist in NEW account
    
    Args:
        old_client: Supabase client for OLD account
        new_client: Supabase client for NEW account
        user_ids: Set of user IDs to sync
        
    Returns:
        Number of users synced
    """
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
            print_warning(f"   Could not find users in OLD account: {user_ids}")
            return 0
        
        # Upsert to NEW account
        new_client.table('users').upsert(users_data.data).execute()
        synced = len(users_data.data)
        print_success(f"   Synced {synced} missing users")
        return synced
        
    except Exception as e:
        print_error(f"   Error syncing missing users: {e}")
        return 0


def sync_table(old_client: Client, new_client: Client, table_name: str, 
               last_sync_time: str, batch_size: int = BATCH_SIZE) -> int:
    """
    Sync a single table from OLD to NEW account
    
    Args:
        old_client: Supabase client for OLD account
        new_client: Supabase client for NEW account
        table_name: Name of table to sync
        last_sync_time: ISO timestamp to fetch records after
        batch_size: Number of records to process per batch
        
    Returns:
        Number of records synced
    """
    try:
        print(f"\n[SYNC] Syncing table: {table_name}")
        print(f"       Fetching records created/updated after: {last_sync_time}")
        
        # jobs and workflow_executions use updated_at to catch status/step changes on existing rows
        timestamp_field = 'updated_at' if table_name in ('jobs', 'workflow_executions') else 'created_at'
        
        # Fetch all matching records (Supabase handles pagination internally)
        data = old_client.table(table_name)\
            .select('*')\
            .gte(timestamp_field, last_sync_time)\
            .execute()
        
        if not data.data or len(data.data) == 0:
            print_info(f"{table_name}: No new records to sync")
            return 0
        
        total_records = len(data.data)
        print_info(f"{table_name}: Found {total_records} new/updated records")
        
        # Check for missing parent users if syncing jobs, sessions, ad_sessions, shared_results, or workflow_executions
        if table_name in ['jobs', 'sessions', 'ad_sessions', 'shared_results', 'workflow_executions'] and total_records > 0:
            # Extract unique user_ids from the data
            user_ids = set(record.get('user_id') for record in data.data if record.get('user_id'))
            
            if user_ids:
                # Check which users don't exist in NEW account
                existing_users = new_client.table('users')\
                    .select('id')\
                    .in_('id', list(user_ids))\
                    .execute()
                
                existing_user_ids = set(u['id'] for u in existing_users.data) if existing_users.data else set()
                missing_user_ids = user_ids - existing_user_ids
                
                if missing_user_ids:
                    print_warning(f"   Found {len(missing_user_ids)} users not in NEW account")
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
                            print_success(f"   Synced {len(missing_jobs_data.data)} missing parent jobs")
                    except Exception as job_sync_err:
                        print_error(f"   Failed to sync missing parent jobs: {job_sync_err}")
        
        # Process in batches to avoid memory issues
        synced_count = 0
        for i in range(0, total_records, batch_size):
            batch = data.data[i:i + batch_size]
            
            try:
                # Upsert to NEW account (insert or update if exists)
                # This handles both new records and updates to existing ones
                new_client.table(table_name).upsert(batch).execute()
                synced_count += len(batch)
                
                if total_records > batch_size:
                    print_info(f"   Batch {i//batch_size + 1}: {len(batch)} records synced")
                    
            except Exception as batch_error:
                print_error(f"   Batch {i//batch_size + 1} failed: {batch_error}")
                # Continue with next batch instead of failing entire sync
                continue
        
        print_success(f"{table_name}: {synced_count}/{total_records} records synced successfully")
        return synced_count
        
    except Exception as e:
        print_error(f"Error syncing {table_name}: {e}")
        return 0


def verify_sync_setup(old_client: Client, new_client: Client) -> bool:
    """
    Verify that both accounts are accessible and sync_metadata table exists in NEW account
    
    Returns:
        True if setup is valid, False otherwise
    """
    try:
        # Test OLD account connection
        old_client.table('users').select('id').limit(1).execute()
        print_success("OLD account connection verified")
        
        # Test NEW account connection
        new_client.table('users').select('id').limit(1).execute()
        print_success("NEW account connection verified")
        
        # Check if sync_metadata table exists in NEW account
        new_client.table('sync_metadata').select('*').limit(1).execute()
        print_success("sync_metadata table exists in NEW account")
        
        return True
        
    except Exception as e:
        print_error(f"Sync setup verification failed: {e}")
        print_info("Make sure:")
        print_info("  1. OLD_SUPABASE_URL and OLD_SUPABASE_KEY are set in .env")
        print_info("  2. NEW account has sync_metadata table (run setup_sync.py first)")
        print_info("  3. Both accounts are accessible")
        return False


def run_sync() -> bool:
    """
    Main sync function - runs the complete sync process
    
    Returns:
        True if sync completed successfully, False otherwise
    """
    print_header(f"SMART HOURLY SYNC - {datetime.now(timezone.utc).isoformat()}")
    
    # Check if sync is enabled
    if not ENABLE_SYNC:
        print_warning("Hourly sync is DISABLED (set ENABLE_HOURLY_SYNC=true in .env)")
        return False
    
    # Validate configuration
    if not OLD_SUPABASE_URL or not OLD_SUPABASE_KEY:
        print_error("OLD_SUPABASE_URL or OLD_SUPABASE_KEY not configured")
        print_info("Set these in .env to enable dual-account sync")
        return False
    
    if not NEW_SUPABASE_URL or not NEW_SUPABASE_KEY:
        print_error("SUPABASE_URL or SUPABASE_KEY not configured")
        return False
    
    try:
        # Connect to both accounts
        print_info("Connecting to OLD account...")
        old_client = create_client(OLD_SUPABASE_URL, OLD_SUPABASE_KEY)
        
        print_info("Connecting to NEW account...")
        new_client = create_client(NEW_SUPABASE_URL, NEW_SUPABASE_KEY)
        
        # Verify setup
        if not verify_sync_setup(old_client, new_client):
            return False
        
        # Automatically determine if this is an initial transfer or incremental sync
        # Check if we have any sync metadata - if not, it's an initial transfer
        try:
            result = new_client.table('sync_metadata')\
                .select('last_sync_timestamp')\
                .eq('sync_type', 'hourly')\
                .order('last_sync_timestamp', desc=True)\
                .limit(1)\
                .execute()
            
            if result.data and len(result.data) > 0:
                # We have sync history - do incremental sync
                last_sync_time = get_last_sync_time(new_client)
                if not last_sync_time:
                    print_error("Could not determine last sync time")
                    return False
                print_info("INCREMENTAL SYNC MODE: Syncing only new/updated records")
            else:
                # No sync history - do initial transfer
                last_sync_time = '1970-01-01T00:00:00+00:00'
                print_warning("INITIAL TRANSFER MODE: Will sync ALL data from old account (first run)")
                
        except Exception as e:
            # Error checking metadata - assume initial transfer
            last_sync_time = '1970-01-01T00:00:00+00:00'
            print_warning(f"Could not check sync metadata: {e}")
            print_warning("INITIAL TRANSFER MODE: Will sync ALL data from old account")
        
        # Use timezone-aware current time
        current_time_dt = datetime.now(timezone.utc)
        current_time = current_time_dt.isoformat()
        
        # Parse last_sync_time with timezone handling
        last_sync_dt = datetime.fromisoformat(last_sync_time.replace('Z', '+00:00'))
        time_diff = current_time_dt - last_sync_dt
        
        print_info(f"Time since last sync: {time_diff}")
        print_info(f"Current time: {current_time}")
        
        # Mark sync as in-progress (use last_sync_time as checkpoint)
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
                # Continue with other tables
                continue
        
        # Update last sync timestamp to NOW (only if at least one table synced)
        if total_synced > 0 or all(count == 0 for count in sync_counts.values()):
            # Successful sync (even if no new records)
            update_sync_metadata(new_client, 'completed', current_time, sync_counts)
            
            print_header("SYNC COMPLETED SUCCESSFULLY")
            print_success(f"Total records synced: {total_synced}")
            print_info(f"Details: {sync_counts}")
            print_info(f"Next sync will fetch data after: {current_time}")
            return True
        else:
            # Partial failure - keep last_sync_time unchanged
            update_sync_metadata(new_client, 'failed', last_sync_time, sync_counts, 
                               "Some tables failed to sync")
            
            print_header("SYNC COMPLETED WITH ERRORS")
            print_warning(f"Total records synced: {total_synced}")
            print_info(f"Details: {sync_counts}")
            return False
        
    except Exception as e:
        print_error(f"Sync failed with exception: {e}")
        try:
            new_client = create_client(NEW_SUPABASE_URL, NEW_SUPABASE_KEY)
            # Use last known sync time for failed syncs
            last_sync_time = get_last_sync_time(new_client)
            if last_sync_time:
                update_sync_metadata(new_client, 'failed', last_sync_time, error_message=str(e))
        except:
            pass
        return False


def main():
    """Entry point for hourly sync script"""
    success = run_sync()
    
    if success:
        print("\n[OK] Sync completed successfully!")
        sys.exit(0)
    else:
        print("\n[ERROR] Sync failed or skipped!")
        sys.exit(1)


if __name__ == "__main__":
    main()
