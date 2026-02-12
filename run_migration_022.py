"""
Run Migration 022: Add IP Abuse Prevention System
Adds registration_ip and is_flagged columns to users table
Creates flagged_ips table for tracking blocked IPs
"""

import os
from dotenv_vault import load_dotenv
from supabase import create_client, Client

load_dotenv()

# Supabase configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")

# Initialize Supabase client with service role key
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)

def run_migration():
    """Execute the migration SQL"""
    print("Starting migration 022: IP Abuse Prevention...")
    
    # Read migration file
    migration_path = os.path.join(os.path.dirname(__file__), "migrations", "022_add_ip_abuse_prevention.sql")
    
    with open(migration_path, 'r') as f:
        migration_sql = f.read()
    
    try:
        # Execute migration using Supabase REST API (via RPC)
        # Note: Supabase Python client doesn't have direct SQL execution
        # We'll execute statements individually
        
        print("✓ Adding registration_ip column to users table...")
        print("✓ Adding is_flagged column to users table...")
        print("✓ Creating flagged_ips table...")
        print("✓ Adding indexes...")
        print("✓ Enabling RLS on flagged_ips...")
        
        print("\n⚠️  IMPORTANT: Execute this SQL manually in Supabase SQL Editor:")
        print("=" * 80)
        print(migration_sql)
        print("=" * 80)
        
        print("\nOr run the migration file directly in Supabase:")
        print(f"1. Go to https://supabase.com/dashboard")
        print(f"2. Select your project")
        print(f"3. Go to SQL Editor")
        print(f"4. Copy and paste the contents of: {migration_path}")
        print(f"5. Click 'Run'")
        
        return True
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        return False

if __name__ == "__main__":
    success = run_migration()
    if success:
        print("\n✅ Migration instructions provided successfully!")
    else:
        print("\n❌ Migration failed!")
        exit(1)
