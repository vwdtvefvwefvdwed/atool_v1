"""
Gap Data Transfer - CSV Export Helper
Generates SQL queries for manual CSV export from OLD Supabase account
Use this to avoid API usage when transferring gap data
"""

import os
from datetime import datetime, timezone
from dotenv_vault import load_dotenv
from supabase import create_client

load_dotenv()

NEW_SUPABASE_URL = os.getenv('NEW_SUPABASE_URL')
NEW_SUPABASE_KEY = os.getenv('NEW_SUPABASE_SERVICE_ROLE_KEY')

TABLES = ['users', 'jobs', 'sessions', 'usage_logs', 'ad_sessions', 'shared_results']


def print_header(text: str):
    print("\n" + "=" * 80)
    print(f"  {text}")
    print("=" * 80)


def print_info(text: str):
    print(f"ℹ️  {text}")


def get_last_sync_time():
    """Get last successful sync timestamp from NEW account"""
    try:
        new_client = create_client(NEW_SUPABASE_URL, NEW_SUPABASE_KEY)
        result = new_client.table('sync_metadata')\
            .select('last_sync_timestamp')\
            .eq('sync_type', 'hourly')\
            .order('last_sync_timestamp', desc=True)\
            .limit(1)\
            .execute()
        
        if result.data and len(result.data) > 0:
            return result.data[0]['last_sync_timestamp']
        else:
            raise Exception("No sync metadata found")
    except Exception as e:
        raise Exception(f"Could not get last sync time: {e}")


def generate_standalone_query(last_sync: str) -> str:
    """Generate a single SQL query that exports all tables into one CSV"""
    return f"""-- STANDALONE QUERY: Export all gap data from all tables
-- Run this in OLD Supabase SQL Editor, then download as 'all_data.csv'
SELECT 
    'users' as table_name,
    id::text,
    email,
    created_at::text,
    last_login::text,
    credits::text,
    is_active::text,
    metadata::text,
    generation_count::text,
    registration_ip::text,
    is_flagged::text,
    NULL as user_id,
    NULL as job_id,
    NULL as status,
    NULL as prompt,
    NULL as model,
    NULL as aspect_ratio,
    NULL as image_url,
    NULL as thumbnail_url,
    NULL as video_url,
    NULL as error_message,
    NULL as progress,
    NULL as width,
    NULL as height,
    NULL as job_type,
    NULL as started_at,
    NULL as completed_at,
    NULL as token,
    NULL as expires_at,
    NULL as last_activity,
    NULL as user_agent,
    NULL as ip_address,
    NULL as action,
    NULL as credits_used,
    NULL as monetag_click_id,
    NULL as zone_id,
    NULL as ad_type,
    NULL as monetag_verified,
    NULL as monetag_revenue,
    NULL as updated_at,
    NULL as share_id,
    NULL as view_count,
    NULL as click_count,
    NULL as conversion_count,
    NULL as is_public,
    NULL as last_viewed_at
FROM users
WHERE created_at > '{last_sync}'

UNION ALL

SELECT 
    'jobs' as table_name,
    job_id::text,
    NULL as email,
    created_at::text,
    NULL as last_login,
    NULL as credits,
    NULL as is_active,
    metadata::text,
    NULL as generation_count,
    NULL as registration_ip,
    NULL as is_flagged,
    user_id::text,
    NULL as job_id,
    status,
    prompt,
    model,
    aspect_ratio,
    image_url,
    thumbnail_url,
    video_url,
    error_message,
    progress::text,
    width::text,
    height::text,
    job_type,
    started_at::text,
    completed_at::text,
    NULL as token,
    NULL as expires_at,
    NULL as last_activity,
    NULL as user_agent,
    NULL as ip_address,
    NULL as action,
    NULL as credits_used,
    NULL as monetag_click_id,
    NULL as zone_id,
    NULL as ad_type,
    NULL as monetag_verified,
    NULL as monetag_revenue,
    NULL as updated_at,
    NULL as share_id,
    NULL as view_count,
    NULL as click_count,
    NULL as conversion_count,
    NULL as is_public,
    NULL as last_viewed_at
FROM jobs
WHERE created_at > '{last_sync}'

UNION ALL

SELECT 
    'sessions' as table_name,
    session_id::text,
    NULL as email,
    created_at::text,
    NULL as last_login,
    NULL as credits,
    NULL as is_active,
    NULL as metadata,
    NULL as generation_count,
    NULL as registration_ip,
    NULL as is_flagged,
    user_id::text,
    NULL as job_id,
    NULL as status,
    NULL as prompt,
    NULL as model,
    NULL as aspect_ratio,
    NULL as image_url,
    NULL as thumbnail_url,
    NULL as video_url,
    NULL as error_message,
    NULL as progress,
    NULL as width,
    NULL as height,
    NULL as job_type,
    NULL as started_at,
    NULL as completed_at,
    token,
    expires_at::text,
    last_activity::text,
    user_agent,
    ip_address::text,
    NULL as action,
    NULL as credits_used,
    NULL as monetag_click_id,
    NULL as zone_id,
    NULL as ad_type,
    NULL as monetag_verified,
    NULL as monetag_revenue,
    NULL as updated_at,
    NULL as share_id,
    NULL as view_count,
    NULL as click_count,
    NULL as conversion_count,
    NULL as is_public,
    NULL as last_viewed_at
FROM sessions
WHERE created_at > '{last_sync}'

UNION ALL

SELECT 
    'usage_logs' as table_name,
    id::text,
    NULL as email,
    created_at::text,
    NULL as last_login,
    NULL as credits,
    NULL as is_active,
    metadata::text,
    NULL as generation_count,
    NULL as registration_ip,
    NULL as is_flagged,
    user_id::text,
    job_id::text,
    NULL as status,
    NULL as prompt,
    NULL as model,
    NULL as aspect_ratio,
    NULL as image_url,
    NULL as thumbnail_url,
    NULL as video_url,
    NULL as error_message,
    NULL as progress,
    NULL as width,
    NULL as height,
    NULL as job_type,
    NULL as started_at,
    NULL as completed_at,
    NULL as token,
    NULL as expires_at,
    NULL as last_activity,
    NULL as user_agent,
    NULL as ip_address,
    action,
    credits_used::text,
    NULL as monetag_click_id,
    NULL as zone_id,
    NULL as ad_type,
    NULL as monetag_verified,
    NULL as monetag_revenue,
    NULL as updated_at,
    NULL as share_id,
    NULL as view_count,
    NULL as click_count,
    NULL as conversion_count,
    NULL as is_public,
    NULL as last_viewed_at
FROM usage_logs
WHERE created_at > '{last_sync}'

UNION ALL

SELECT 
    'ad_sessions' as table_name,
    id::text,
    NULL as email,
    created_at::text,
    NULL as last_login,
    NULL as credits,
    NULL as is_active,
    NULL as metadata,
    NULL as generation_count,
    NULL as registration_ip,
    NULL as is_flagged,
    user_id::text,
    NULL as job_id,
    status,
    NULL as prompt,
    NULL as model,
    NULL as aspect_ratio,
    NULL as image_url,
    NULL as thumbnail_url,
    NULL as video_url,
    NULL as error_message,
    NULL as progress,
    NULL as width,
    NULL as height,
    NULL as job_type,
    NULL as started_at,
    completed_at::text,
    NULL as token,
    NULL as expires_at,
    NULL as last_activity,
    user_agent,
    ip_address,
    NULL as action,
    NULL as credits_used,
    monetag_click_id,
    zone_id,
    ad_type,
    monetag_verified::text,
    monetag_revenue::text,
    updated_at::text,
    NULL as share_id,
    NULL as view_count,
    NULL as click_count,
    NULL as conversion_count,
    NULL as is_public,
    NULL as last_viewed_at
FROM ad_sessions
WHERE created_at > '{last_sync}'

UNION ALL

SELECT 
    'shared_results' as table_name,
    id::text,
    NULL as email,
    created_at::text,
    NULL as last_login,
    NULL as credits,
    NULL as is_active,
    metadata::text,
    NULL as generation_count,
    NULL as registration_ip,
    NULL as is_flagged,
    user_id::text,
    job_id::text,
    NULL as status,
    prompt,
    NULL as model,
    NULL as aspect_ratio,
    image_url,
    NULL as thumbnail_url,
    video_url,
    NULL as error_message,
    NULL as progress,
    NULL as width,
    NULL as height,
    job_type,
    NULL as started_at,
    NULL as completed_at,
    NULL as token,
    NULL as expires_at,
    NULL as last_activity,
    NULL as user_agent,
    NULL as ip_address,
    NULL as action,
    NULL as credits_used,
    NULL as monetag_click_id,
    NULL as zone_id,
    NULL as ad_type,
    NULL as monetag_verified,
    NULL as monetag_revenue,
    updated_at::text,
    share_id,
    view_count::text,
    click_count::text,
    conversion_count::text,
    is_public::text,
    last_viewed_at::text
FROM shared_results
WHERE created_at > '{last_sync}'

ORDER BY table_name, created_at;"""


def main():
    print_header("GAP DATA TRANSFER - CSV Export Helper")
    
    try:
        # Get last sync time
        last_sync = get_last_sync_time()
        current_time = datetime.now(timezone.utc).isoformat()
        
        print_info(f"Last sync timestamp: {last_sync}")
        print_info(f"Current time: {current_time}")
        
        # Calculate gap
        last_sync_dt = datetime.fromisoformat(last_sync.replace('Z', '+00:00'))
        current_dt = datetime.now(timezone.utc)
        gap = current_dt - last_sync_dt
        print_info(f"Data gap: {gap}")
        
        print_header("OPTION 1: STANDALONE SQL QUERY (RECOMMENDED)")
        print("\nCopy this SINGLE query and run in OLD Supabase SQL Editor:")
        print("=" * 80)
        print(generate_standalone_query(last_sync))
        print("=" * 80)
        print("\nThen:")
        print("1. Click 'Download CSV' button")
        print("2. Save as: all_data.csv")
        print("3. Run: python import_csv_to_new.py")
        
        print_header("OPTION 2: SEPARATE QUERIES (5 FILES)")
        print("\nCopy these queries and run them in OLD Supabase account SQL Editor:")
        print("(Then export results as CSV)\n")
        
        for table in TABLES:
            print(f"\n-- {table.upper()} (data created after last sync)")
            print(f"SELECT * FROM {table}")
            print(f"WHERE created_at > '{last_sync}'")
            print(f"ORDER BY created_at ASC;")
        
        print_header("MANUAL CSV EXPORT INSTRUCTIONS")
        print("""
1. Go to OLD Supabase Dashboard → SQL Editor
2. Run each query above (one at a time)
3. Click "Download as CSV" button for each result
4. Save files as: users.csv, jobs.csv, sessions.csv, etc.

5. Go to NEW Supabase Dashboard → Table Editor
6. For each table:
   - Open table (e.g., "users")
   - Click "Insert" → "Import from CSV"
   - Upload the CSV file
   - Map columns (should auto-match)
   - Click "Import"
   - Repeat for all tables

7. IMPORTANT: Import in this order to avoid foreign key errors:
   - users.csv (first - parent table)
   - jobs.csv
   - sessions.csv
   - usage_logs.csv
   - ad_sessions.csv
   - shared_results.csv

8. After import, verify record counts match
        """)
        
        print("\n✅ Instructions generated successfully!")
        print(f"ℹ️  Last sync: {last_sync}")
        print(f"ℹ️  Gap period: {gap}")
        
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    main()
