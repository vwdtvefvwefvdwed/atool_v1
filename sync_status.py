"""
Sync Status Monitor - View Sync History and Statistics
Shows sync operations, success rate, and data transfer stats
"""

import os
from datetime import datetime, timedelta, timezone
from dotenv_vault import load_dotenv
from supabase import create_client

load_dotenv()

# NEW account (migration target - where sync_metadata is stored)
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


def show_latest_sync(client):
    """Display latest sync operation"""
    print_header("Latest Sync Operation")
    
    try:
        result = client.table('sync_metadata')\
            .select('*')\
            .order('created_at', desc=True)\
            .limit(1)\
            .execute()
        
        if not result.data or len(result.data) == 0:
            print_info("No sync operations found")
            return
        
        latest = result.data[0]
        
        print(f"\n📅 Timestamp: {latest['created_at']}")
        print(f"🔄 Type: {latest['sync_type']}")
        print(f"📊 Status: {latest['sync_status']}")
        print(f"⏰ Last Sync: {latest['last_sync_timestamp']}")
        
        if latest['records_synced']:
            print(f"\n📦 Records Synced:")
            for table, count in latest['records_synced'].items():
                print(f"   • {table}: {count}")
        
        if latest['error_message']:
            print(f"\n❌ Error: {latest['error_message']}")
        
        # Calculate time since last sync
        last_sync_time = datetime.fromisoformat(latest['created_at'].replace('Z', '+00:00'))
        time_since = datetime.now(timezone.utc) - last_sync_time
        print(f"\n⏱️  Time since last sync: {format_timedelta(time_since)}")
        
    except Exception as e:
        print_error(f"Could not fetch latest sync: {e}")


def show_sync_history(client, limit=10):
    """Display sync history"""
    print_header(f"Sync History (Last {limit} Operations)")
    
    try:
        result = client.table('sync_metadata')\
            .select('*')\
            .order('created_at', desc=True)\
            .limit(limit)\
            .execute()
        
        if not result.data or len(result.data) == 0:
            print_info("No sync history found")
            return
        
        print("\n{:<20} {:<15} {:<12} {:<10}".format(
            "Timestamp", "Status", "Type", "Total Records"
        ))
        print("-" * 80)
        
        for sync in result.data:
            timestamp = sync['created_at'][:19]  # Trim to YYYY-MM-DD HH:MM:SS
            status = sync['sync_status']
            sync_type = sync['sync_type']
            
            # Calculate total records
            total = 0
            if sync['records_synced']:
                for count in sync['records_synced'].values():
                    if isinstance(count, int):
                        total += count
            
            # Status emoji
            status_icon = {
                'completed': '✅',
                'failed': '❌',
                'in_progress': '🔄'
            }.get(status, '❓')
            
            print("{:<20} {:<15} {:<12} {:<10}".format(
                timestamp,
                f"{status_icon} {status}",
                sync_type,
                str(total)
            ))
        
    except Exception as e:
        print_error(f"Could not fetch sync history: {e}")


def show_sync_statistics(client):
    """Display sync statistics"""
    print_header("Sync Statistics")
    
    try:
        # Get all sync records
        result = client.table('sync_metadata')\
            .select('*')\
            .order('created_at', desc=False)\
            .execute()
        
        if not result.data or len(result.data) == 0:
            print_info("No sync data available")
            return
        
        syncs = result.data
        
        # Calculate stats
        total_syncs = len(syncs)
        completed = len([s for s in syncs if s['sync_status'] == 'completed'])
        failed = len([s for s in syncs if s['sync_status'] == 'failed'])
        in_progress = len([s for s in syncs if s['sync_status'] == 'in_progress'])
        
        success_rate = (completed / total_syncs * 100) if total_syncs > 0 else 0
        
        # Calculate total records synced
        total_records = {}
        for sync in syncs:
            if sync['records_synced']:
                for table, count in sync['records_synced'].items():
                    if isinstance(count, int):
                        total_records[table] = total_records.get(table, 0) + count
        
        # First and last sync
        first_sync = syncs[0]['created_at']
        last_sync = syncs[-1]['created_at']
        
        # Display stats
        print(f"\n📊 Total Sync Operations: {total_syncs}")
        print(f"✅ Completed: {completed}")
        print(f"❌ Failed: {failed}")
        print(f"🔄 In Progress: {in_progress}")
        print(f"📈 Success Rate: {success_rate:.1f}%")
        
        print(f"\n📅 First Sync: {first_sync}")
        print(f"📅 Last Sync: {last_sync}")
        
        if total_records:
            print(f"\n📦 Total Records Synced by Table:")
            for table, count in sorted(total_records.items()):
                print(f"   • {table}: {count:,}")
        
    except Exception as e:
        print_error(f"Could not calculate statistics: {e}")


def show_sync_health(client):
    """Check sync system health"""
    print_header("Sync Health Check")
    
    try:
        # Get latest sync
        result = client.table('sync_metadata')\
            .select('*')\
            .order('created_at', desc=True)\
            .limit(1)\
            .execute()
        
        if not result.data or len(result.data) == 0:
            print_error("No sync operations found - system may not be initialized")
            return
        
        latest = result.data[0]
        last_sync_time = datetime.fromisoformat(latest['created_at'].replace('Z', '+00:00'))
        time_since = datetime.now(timezone.utc) - last_sync_time
        
        # Health checks
        health_issues = []
        
        # Check 1: Recent sync
        if time_since > timedelta(hours=2):
            health_issues.append(f"⚠️  Last sync was {format_timedelta(time_since)} ago (expected < 1 hour)")
        else:
            print_success(f"Last sync was {format_timedelta(time_since)} ago")
        
        # Check 2: Sync status
        if latest['sync_status'] == 'failed':
            health_issues.append(f"❌ Last sync FAILED: {latest.get('error_message', 'Unknown error')}")
        elif latest['sync_status'] == 'in_progress':
            health_issues.append(f"⚠️  Sync stuck in 'in_progress' state (may have crashed)")
        else:
            print_success("Last sync completed successfully")
        
        # Check 3: Recent sync rate
        recent_syncs = client.table('sync_metadata')\
            .select('*')\
            .gte('created_at', (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat())\
            .execute()
        
        if recent_syncs.data:
            daily_sync_count = len(recent_syncs.data)
            print_success(f"{daily_sync_count} syncs in last 24 hours")
            
            if daily_sync_count < 20:  # Expecting ~24 per day (hourly)
                health_issues.append(f"⚠️  Low sync frequency: {daily_sync_count} syncs in 24h (expected ~24)")
        
        # Overall health
        if health_issues:
            print(f"\n⚠️  Health Issues Found:")
            for issue in health_issues:
                print(f"   {issue}")
        else:
            print(f"\n✅ Sync system is healthy!")
        
    except Exception as e:
        print_error(f"Health check failed: {e}")


def format_timedelta(td):
    """Format timedelta in human-readable format"""
    if td.days > 0:
        return f"{td.days}d {td.seconds//3600}h"
    elif td.seconds >= 3600:
        return f"{td.seconds//3600}h {(td.seconds%3600)//60}m"
    elif td.seconds >= 60:
        return f"{(td.seconds%3600)//60}m"
    else:
        return f"{td.seconds}s"


def main():
    """Main status monitoring function"""
    print_header("SYNC STATUS MONITOR")
    
    if not NEW_SUPABASE_URL or not NEW_SUPABASE_KEY:
        print_error("NEW account credentials not configured")
        return False
    
    try:
        client = create_client(NEW_SUPABASE_URL, NEW_SUPABASE_KEY)
        
        # Show different views
        show_sync_health(client)
        show_latest_sync(client)
        show_sync_statistics(client)
        show_sync_history(client, limit=10)
        
        print_header("END OF REPORT")
        return True
        
    except Exception as e:
        print_error(f"Status check failed: {e}")
        return False


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
