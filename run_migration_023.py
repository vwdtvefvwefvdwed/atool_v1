"""
Run Migration 023: Create sync_metadata table on NEW Supabase account
This migration adds the sync_metadata table for dual-account sync system
"""

import os
from dotenv_vault import load_dotenv
from supabase import create_client, Client

load_dotenv()

# NEW account configuration (where sync_metadata will be created)
NEW_SUPABASE_URL = os.getenv("NEW_SUPABASE_URL")
NEW_SUPABASE_SERVICE_ROLE_KEY = os.getenv("NEW_SUPABASE_SERVICE_ROLE_KEY")

if not NEW_SUPABASE_URL or not NEW_SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("Missing NEW_SUPABASE_URL or NEW_SUPABASE_SERVICE_ROLE_KEY in .env")

def run_migration():
    """Execute the migration SQL"""
    print("=" * 80)
    print("MIGRATION 023: Create sync_metadata table")
    print("=" * 80)
    print(f"\n🔗 Target Account: {NEW_SUPABASE_URL}")
    
    # Read migration file
    migration_path = os.path.join(os.path.dirname(__file__), "migrations", "023_create_sync_metadata_table.sql")
    
    with open(migration_path, 'r', encoding='utf-8') as f:
        migration_sql = f.read()
    
    try:
        print("\n📋 Migration Steps:")
        print("  ✓ Create sync_metadata table")
        print("  ✓ Add indexes for performance")
        print("  ✓ Enable RLS policies")
        print("  ✓ Add table and column comments")
        
        print("\n" + "=" * 80)
        print("⚠️  MANUAL EXECUTION REQUIRED")
        print("=" * 80)
        print("\nExecute this SQL in Supabase SQL Editor:")
        print("\n1. Go to: https://supabase.com/dashboard")
        print("2. Select your NEW account project")
        print(f"3. Project URL: {NEW_SUPABASE_URL}")
        print("4. Navigate to: SQL Editor")
        print("5. Copy and paste the SQL below (or from the file)")
        print("6. Click 'Run'\n")
        
        print("=" * 80)
        print("SQL TO EXECUTE:")
        print("=" * 80)
        print(migration_sql)
        print("=" * 80)
        
        print(f"\nOr copy from file: {migration_path}\n")
        
        # Verify if table already exists
        try:
            supabase = create_client(NEW_SUPABASE_URL, NEW_SUPABASE_SERVICE_ROLE_KEY)
            result = supabase.table('sync_metadata').select('*').limit(1).execute()
            
            print("=" * 80)
            print("⚠️  TABLE ALREADY EXISTS")
            print("=" * 80)
            print("✅ sync_metadata table already exists in this account")
            print("   You can skip this migration or run it to ensure all indexes/policies exist")
            
            count_result = supabase.table('sync_metadata').select('id', count='exact').execute()
            record_count = count_result.count if hasattr(count_result, 'count') else len(count_result.data)
            print(f"📊 Current records in sync_metadata: {record_count}\n")
            
        except Exception as verify_error:
            if 'does not exist' in str(verify_error).lower() or 'not found' in str(verify_error).lower():
                print("\n✅ Ready to create sync_metadata table (table does not exist yet)")
            else:
                print(f"\n⚠️  Could not verify table existence: {verify_error}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error reading migration file: {e}")
        return False

if __name__ == "__main__":
    success = run_migration()
    
    if success:
        print("=" * 80)
        print("NEXT STEPS AFTER RUNNING SQL:")
        print("=" * 80)
        print("1. Run: python setup_sync.py")
        print("2. Set ENABLE_HOURLY_SYNC=true in .env")
        print("3. Monitor with: python sync_status.py")
        print("4. See SYNC_SYSTEM_GUIDE.md for complete migration instructions")
        print("=" * 80)
        print("\n✅ Migration instructions provided successfully!")
    else:
        print("\n❌ Migration failed!")
        exit(1)
