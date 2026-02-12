"""
Setup Sync System - Initialize Dual-Account Sync
Run this script ONCE when setting up a new Supabase account for syncing
"""

import os
from datetime import datetime, timezone, timedelta
from dotenv_vault import load_dotenv
from supabase import create_client

load_dotenv()

# OLD account (current production - source for sync)
OLD_SUPABASE_URL = os.getenv('SUPABASE_URL')
OLD_SUPABASE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

# NEW account (migration target - destination for sync)
NEW_SUPABASE_URL = os.getenv('NEW_SUPABASE_URL')
NEW_SUPABASE_KEY = os.getenv('NEW_SUPABASE_SERVICE_ROLE_KEY') or os.getenv('NEW_SUPABASE_ANON_KEY')


def print_header(text):
    """Print formatted header"""
    print("\n" + "=" * 80)
    print(f"  {text}")
    print("=" * 80)


def print_success(text):
    """Print success message"""
    print(f"✅ {text}")


def print_error(text):
    """Print error message"""
    print(f"❌ {text}")


def print_info(text):
    """Print info message"""
    print(f"ℹ️  {text}")


def setup_sync_metadata_table():
    """
    Create sync_metadata table in NEW account if it doesn't exist
    """
    print_header("Setting Up Sync Metadata Table")
    
    if not NEW_SUPABASE_URL or not NEW_SUPABASE_KEY:
        print_error("NEW account credentials not found in .env")
        print_info("Make sure SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are set")
        return False
    
    try:
        client = create_client(NEW_SUPABASE_URL, NEW_SUPABASE_KEY)
        
        # Test connection
        print_info("Testing connection to NEW account...")
        client.table('users').select('id').limit(1).execute()
        print_success("Connected to NEW account successfully")
        
        # Check if sync_metadata table exists
        print_info("Checking for sync_metadata table...")
        try:
            result = client.table('sync_metadata').select('*').limit(1).execute()
            print_success("sync_metadata table already exists")
            
            # Count existing records
            count_result = client.table('sync_metadata').select('id', count='exact').execute()
            record_count = count_result.count if hasattr(count_result, 'count') else len(count_result.data)
            print_info(f"Table has {record_count} existing sync records")
            
        except Exception as table_error:
            print_error(f"sync_metadata table not found: {table_error}")
            print_info("Please run the full schema migration (000_clean_schema_no_coins.sql)")
            print_info("Or create the table manually using Supabase SQL Editor")
            return False
        
        return True
        
    except Exception as e:
        print_error(f"Setup failed: {e}")
        return False


def initialize_first_sync_record():
    """
    Create the first sync metadata record to establish baseline
    """
    print_header("Initializing First Sync Record")
    
    try:
        client = create_client(NEW_SUPABASE_URL, NEW_SUPABASE_KEY)
        
        # Check if there's already a record
        existing = client.table('sync_metadata')\
            .select('*')\
            .eq('sync_type', 'hourly')\
            .execute()
        
        if existing.data and len(existing.data) > 0:
            print_info(f"Found {len(existing.data)} existing sync records")
            latest = max(existing.data, key=lambda x: x['created_at'])
            print_info(f"Latest sync: {latest['last_sync_timestamp']}")
            print_info(f"Status: {latest['sync_status']}")
            
            # Ask user if they want to create a new baseline
            print("\nOptions:")
            print("1. Keep existing records (recommended)")
            print("2. Create new baseline record")
            
            choice = input("\nEnter choice (1 or 2): ").strip()
            
            if choice != "2":
                print_info("Keeping existing records")
                return True
        
        # Create initial sync record
        # Use current time - 24 hours as baseline (will sync last 24 hours on first run)
        baseline_time = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        
        print_info(f"Creating baseline sync record with timestamp: {baseline_time}")
        
        initial_record = {
            'sync_type': 'hourly',
            'last_sync_timestamp': baseline_time,
            'sync_status': 'completed',
            'records_synced': {'info': 'Initial baseline record'},
            'error_message': None
        }
        
        client.table('sync_metadata').insert(initial_record).execute()
        print_success("Initial sync record created successfully")
        print_info("Next sync will fetch data created after: " + baseline_time)
        
        return True
        
    except Exception as e:
        print_error(f"Failed to initialize sync record: {e}")
        return False


def verify_old_account():
    """
    Verify OLD account connection and show stats
    """
    print_header("Verifying OLD Account")
    
    if not OLD_SUPABASE_URL or not OLD_SUPABASE_KEY:
        print_error("OLD account credentials not found in .env")
        print_info("Set OLD_SUPABASE_URL and OLD_SUPABASE_SERVICE_ROLE_KEY to enable sync")
        print_info("If you don't have an OLD account, you can skip this step")
        return False
    
    try:
        client = create_client(OLD_SUPABASE_URL, OLD_SUPABASE_KEY)
        
        # Test connection
        print_info("Testing connection to OLD account...")
        client.table('users').select('id').limit(1).execute()
        print_success("Connected to OLD account successfully")
        
        # Show stats
        print_info("\nOLD Account Statistics:")
        
        tables = ['users', 'jobs', 'sessions', 'usage_logs', 'ad_sessions', 'shared_results']
        for table in tables:
            try:
                result = client.table(table).select('id', count='exact').execute()
                count = result.count if hasattr(result, 'count') else len(result.data)
                print(f"  • {table}: {count} records")
            except:
                print(f"  • {table}: Unable to fetch count")
        
        return True
        
    except Exception as e:
        print_error(f"OLD account verification failed: {e}")
        print_info("Make sure OLD_SUPABASE_URL and OLD_SUPABASE_SERVICE_ROLE_KEY are correct")
        return False


def show_next_steps():
    """
    Display next steps after setup
    """
    print_header("Setup Complete - Next Steps")
    
    print("\n📋 To enable hourly sync:")
    print("   1. Add to .env file:")
    print("      ENABLE_HOURLY_SYNC=true")
    print("")
    print("   2. Run sync manually to test:")
    print("      python smart_hourly_sync.py")
    print("")
    print("   3. Set up Windows Task Scheduler (or cron on Linux):")
    print("      - Task: python smart_hourly_sync.py")
    print("      - Trigger: Every 1 hour")
    print("      - Start directory: backend/")
    print("")
    print("   4. Monitor sync status:")
    print("      python sync_status.py")
    print("")
    
    print("\n📊 Sync will transfer:")
    print("   • New users created since last sync")
    print("   • New jobs created since last sync")
    print("   • New sessions created since last sync")
    print("   • Updated usage logs")
    print("   • New ad sessions")
    print("   • New shared results")
    print("")
    
    print("\n⚠️  Important Notes:")
    print("   • Hourly sync uses API calls (minimal - ~8 per sync)")
    print("   • Keeps NEW account 99% synced before OLD account hits limit")
    print("   • Use CLI (pg_dump) for final migration when OLD account hits limit")
    print("")


def main():
    """Main setup workflow"""
    print_header("DUAL-ACCOUNT SYNC SETUP")
    print("This script will set up the sync system between OLD and NEW Supabase accounts")
    
    # Step 1: Setup sync_metadata table in NEW account
    if not setup_sync_metadata_table():
        print_error("\nSetup failed at Step 1")
        return False
    
    # Step 2: Initialize first sync record
    if not initialize_first_sync_record():
        print_error("\nSetup failed at Step 2")
        return False
    
    # Step 3: Verify OLD account (optional)
    print("\n")
    verify_old = input("Do you want to verify OLD account connection? (y/n): ").strip().lower()
    if verify_old == 'y':
        verify_old_account()
    
    # Step 4: Show next steps
    show_next_steps()
    
    print_header("SETUP SUCCESSFUL")
    print_success("Sync system is ready to use!")
    
    return True


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
