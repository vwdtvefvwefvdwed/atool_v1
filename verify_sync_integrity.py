"""
Sync Integrity Verification Script
Compares data between OLD and NEW Supabase accounts to ensure no data loss
Checks all tables for missing records and reports discrepancies
"""

import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Set, Tuple
from dotenv_vault import load_dotenv
from supabase import create_client, Client

load_dotenv()

# OLD account configuration (source)
OLD_SUPABASE_URL = os.getenv('SUPABASE_URL')
OLD_SUPABASE_KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY')

# NEW account configuration (destination)
NEW_SUPABASE_URL = os.getenv('NEW_SUPABASE_URL')
NEW_SUPABASE_KEY = os.getenv('NEW_SUPABASE_SERVICE_ROLE_KEY') or os.getenv('NEW_SUPABASE_ANON_KEY')

# Tables to verify
TABLES = ['users', 'jobs', 'sessions', 'usage_logs', 'ad_sessions', 'shared_results']

# ID field mapping for each table
ID_FIELDS = {
    'users': 'id',
    'jobs': 'job_id',
    'sessions': 'session_id',
    'usage_logs': 'id',
    'ad_sessions': 'id',
    'shared_results': 'id'
}

# Batch size for fetching records
BATCH_SIZE = 1000


def print_header(text: str):
    """Print formatted header"""
    print("\n" + "=" * 80)
    print(f"  {text}")
    print("=" * 80)


def print_success(text: str):
    """Print success message"""
    print(f"✅ {text}")


def print_error(text: str):
    """Print error message"""
    print(f"❌ {text}")


def print_warning(text: str):
    """Print warning message"""
    print(f"⚠️  {text}")


def print_info(text: str):
    """Print info message"""
    print(f"ℹ️  {text}")


def get_all_ids(client: Client, table_name: str, id_field: str) -> Set[str]:
    """
    Fetch all IDs from a table
    
    Args:
        client: Supabase client
        table_name: Name of the table
        id_field: Name of the ID field
        
    Returns:
        Set of all IDs as strings
    """
    try:
        all_ids = set()
        offset = 0
        
        while True:
            # Fetch batch of IDs
            result = client.table(table_name)\
                .select(id_field)\
                .range(offset, offset + BATCH_SIZE - 1)\
                .execute()
            
            if not result.data:
                break
            
            # Extract IDs
            batch_ids = {str(record[id_field]) for record in result.data}
            all_ids.update(batch_ids)
            
            # Check if we got fewer records than batch size (end of data)
            if len(result.data) < BATCH_SIZE:
                break
            
            offset += BATCH_SIZE
            print(f"   Fetched {len(all_ids)} IDs so far...", end='\r')
        
        print(f"   Fetched {len(all_ids)} IDs total         ")
        return all_ids
        
    except Exception as e:
        print_error(f"Error fetching IDs from {table_name}: {e}")
        return set()


def get_record_count(client: Client, table_name: str) -> int:
    """
    Get total record count for a table
    
    Args:
        client: Supabase client
        table_name: Name of the table
        
    Returns:
        Total record count
    """
    try:
        result = client.table(table_name).select('*', count='exact').limit(1).execute()
        return result.count if hasattr(result, 'count') else 0
    except Exception as e:
        print_error(f"Error counting records in {table_name}: {e}")
        return 0


def get_sample_missing_records(client: Client, table_name: str, id_field: str, 
                                missing_ids: Set[str], sample_size: int = 5) -> List[Dict]:
    """
    Fetch sample missing records for detailed inspection
    
    Args:
        client: Supabase client (OLD account)
        table_name: Name of the table
        id_field: Name of the ID field
        missing_ids: Set of missing IDs
        sample_size: Number of samples to fetch
        
    Returns:
        List of sample records
    """
    try:
        # Get first N missing IDs
        sample_ids = list(missing_ids)[:sample_size]
        
        result = client.table(table_name)\
            .select('*')\
            .in_(id_field, sample_ids)\
            .execute()
        
        return result.data if result.data else []
        
    except Exception as e:
        print_error(f"Error fetching sample records: {e}")
        return []


def verify_table(old_client: Client, new_client: Client, table_name: str) -> Dict:
    """
    Verify a single table between OLD and NEW accounts
    
    Args:
        old_client: Supabase client for OLD account
        new_client: Supabase client for NEW account
        table_name: Name of table to verify
        
    Returns:
        Dictionary with verification results
    """
    print(f"\n📊 Verifying table: {table_name}")
    print("-" * 80)
    
    id_field = ID_FIELDS.get(table_name, 'id')
    
    # Get record counts
    print("   Counting records...")
    old_count = get_record_count(old_client, table_name)
    new_count = get_record_count(new_client, table_name)
    
    print(f"   OLD account: {old_count} records")
    print(f"   NEW account: {new_count} records")
    
    # Get all IDs
    print("   Fetching IDs from OLD account...")
    old_ids = get_all_ids(old_client, table_name, id_field)
    
    print("   Fetching IDs from NEW account...")
    new_ids = get_all_ids(new_client, table_name, id_field)
    
    # Find missing records
    missing_ids = old_ids - new_ids
    extra_ids = new_ids - old_ids
    
    result = {
        'table': table_name,
        'old_count': old_count,
        'new_count': new_count,
        'old_ids_count': len(old_ids),
        'new_ids_count': len(new_ids),
        'missing_count': len(missing_ids),
        'extra_count': len(extra_ids),
        'missing_ids': missing_ids,
        'extra_ids': extra_ids,
        'status': 'OK' if len(missing_ids) == 0 and len(extra_ids) == 0 else 'MISMATCH'
    }
    
    # Print results
    if result['status'] == 'OK':
        print_success(f"{table_name}: All records match! ({new_count} records)")
    else:
        print_warning(f"{table_name}: Found discrepancies")
        
        if missing_ids:
            print_error(f"   Missing {len(missing_ids)} records in NEW account")
            
            # Show sample missing records
            if len(missing_ids) > 0:
                print("   Sample missing record IDs:")
                sample_ids = list(missing_ids)[:10]
                for i, missing_id in enumerate(sample_ids, 1):
                    print(f"      {i}. {missing_id}")
                
                if len(missing_ids) > 10:
                    print(f"      ... and {len(missing_ids) - 10} more")
        
        if extra_ids:
            print_warning(f"   Found {len(extra_ids)} extra records in NEW account (not in OLD)")
            sample_ids = list(extra_ids)[:10]
            for i, extra_id in enumerate(sample_ids, 1):
                print(f"      {i}. {extra_id}")
            
            if len(extra_ids) > 10:
                print(f"      ... and {len(extra_ids) - 10} more")
    
    return result


def generate_fix_report(results: List[Dict]) -> str:
    """
    Generate a detailed report with instructions to fix missing data
    
    Args:
        results: List of verification results
        
    Returns:
        Report as string
    """
    report = []
    report.append("\n" + "=" * 80)
    report.append("  DATA SYNC FIX REPORT")
    report.append("=" * 80)
    
    has_issues = False
    
    for result in results:
        if result['status'] == 'MISMATCH':
            has_issues = True
            report.append(f"\n📋 {result['table'].upper()}")
            report.append("-" * 80)
            
            if result['missing_count'] > 0:
                report.append(f"Missing Records: {result['missing_count']}")
                report.append(f"\nTo fix, run this query in OLD Supabase SQL Editor:")
                report.append("```sql")
                
                id_field = ID_FIELDS.get(result['table'], 'id')
                missing_ids_str = ", ".join([f"'{id}'" for id in list(result['missing_ids'])[:50]])
                
                if len(result['missing_ids']) <= 50:
                    report.append(f"SELECT * FROM {result['table']}")
                    report.append(f"WHERE {id_field} IN ({missing_ids_str});")
                else:
                    report.append(f"-- Too many missing records ({result['missing_count']})")
                    report.append(f"-- Recommend running full table sync instead")
                    report.append(f"SELECT * FROM {result['table']};")
                
                report.append("```")
                report.append("\nThen download as CSV and import to NEW account using:")
                report.append(f"python import_csv_to_new.py")
            
            if result['extra_count'] > 0:
                report.append(f"\nExtra Records in NEW: {result['extra_count']}")
                report.append("(These records exist in NEW but not in OLD - may be newer data)")
    
    if not has_issues:
        report.append("\n✅ All tables are perfectly synced!")
        report.append("No action needed.")
    
    return "\n".join(report)


def save_detailed_report(results: List[Dict], filename: str = "sync_integrity_report.txt"):
    """
    Save detailed verification report to file
    
    Args:
        results: List of verification results
        filename: Output filename
    """
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write(f"  SYNC INTEGRITY VERIFICATION REPORT\n")
            f.write(f"  Generated: {datetime.now(timezone.utc).isoformat()}\n")
            f.write("=" * 80 + "\n\n")
            
            # Summary
            f.write("SUMMARY\n")
            f.write("-" * 80 + "\n")
            total_missing = sum(r['missing_count'] for r in results)
            total_extra = sum(r['extra_count'] for r in results)
            tables_ok = sum(1 for r in results if r['status'] == 'OK')
            tables_mismatch = sum(1 for r in results if r['status'] == 'MISMATCH')
            
            f.write(f"Tables Verified: {len(results)}\n")
            f.write(f"Tables OK: {tables_ok}\n")
            f.write(f"Tables with Issues: {tables_mismatch}\n")
            f.write(f"Total Missing Records: {total_missing}\n")
            f.write(f"Total Extra Records: {total_extra}\n\n")
            
            # Detailed results
            f.write("DETAILED RESULTS\n")
            f.write("=" * 80 + "\n\n")
            
            for result in results:
                f.write(f"Table: {result['table']}\n")
                f.write("-" * 80 + "\n")
                f.write(f"Status: {result['status']}\n")
                f.write(f"OLD Account: {result['old_count']} records\n")
                f.write(f"NEW Account: {result['new_count']} records\n")
                f.write(f"Missing in NEW: {result['missing_count']}\n")
                f.write(f"Extra in NEW: {result['extra_count']}\n")
                
                if result['missing_count'] > 0:
                    f.write(f"\nMissing IDs (first 100):\n")
                    for i, missing_id in enumerate(list(result['missing_ids'])[:100], 1):
                        f.write(f"  {i}. {missing_id}\n")
                    
                    if result['missing_count'] > 100:
                        f.write(f"  ... and {result['missing_count'] - 100} more\n")
                
                f.write("\n")
            
            # Fix instructions
            f.write(generate_fix_report(results))
        
        print_success(f"Detailed report saved to: {filename}")
        
    except Exception as e:
        print_error(f"Error saving report: {e}")


def main():
    """Main verification workflow"""
    print_header(f"SYNC INTEGRITY VERIFICATION - {datetime.now(timezone.utc).isoformat()}")
    
    # Validate configuration
    if not OLD_SUPABASE_URL or not OLD_SUPABASE_KEY:
        print_error("OLD account credentials not configured")
        print_info("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env")
        sys.exit(1)
    
    if not NEW_SUPABASE_URL or not NEW_SUPABASE_KEY:
        print_error("NEW account credentials not configured")
        print_info("Set NEW_SUPABASE_URL and NEW_SUPABASE_SERVICE_ROLE_KEY in .env")
        sys.exit(1)
    
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
        
        # Verify each table
        results = []
        for table in TABLES:
            try:
                result = verify_table(old_client, new_client, table)
                results.append(result)
            except Exception as e:
                print_error(f"Error verifying {table}: {e}")
                results.append({
                    'table': table,
                    'status': 'ERROR',
                    'error': str(e),
                    'old_count': 0,
                    'new_count': 0,
                    'missing_count': 0,
                    'extra_count': 0
                })
        
        # Print summary
        print_header("VERIFICATION SUMMARY")
        
        total_old = sum(r['old_count'] for r in results)
        total_new = sum(r['new_count'] for r in results)
        total_missing = sum(r['missing_count'] for r in results)
        total_extra = sum(r['extra_count'] for r in results)
        tables_ok = sum(1 for r in results if r['status'] == 'OK')
        tables_mismatch = sum(1 for r in results if r['status'] == 'MISMATCH')
        
        print(f"\nTables Verified: {len(results)}")
        print(f"Tables OK: {tables_ok}")
        print(f"Tables with Issues: {tables_mismatch}")
        print(f"\nTotal Records in OLD: {total_old}")
        print(f"Total Records in NEW: {total_new}")
        print(f"Total Missing Records: {total_missing}")
        print(f"Total Extra Records: {total_extra}")
        
        # Table-by-table summary
        print("\nTable-by-Table Status:")
        print("-" * 80)
        print(f"{'Table':<20} {'OLD':>10} {'NEW':>10} {'Missing':>10} {'Extra':>10} {'Status':>10}")
        print("-" * 80)
        
        for result in results:
            status_symbol = "✅" if result['status'] == 'OK' else "❌"
            print(f"{result['table']:<20} {result['old_count']:>10} {result['new_count']:>10} "
                  f"{result['missing_count']:>10} {result['extra_count']:>10} {status_symbol:>10}")
        
        print("-" * 80)
        
        # Save detailed report
        save_detailed_report(results)
        
        # Print fix instructions if needed
        if total_missing > 0 or total_extra > 0:
            print(generate_fix_report(results))
            print_header("ACTION REQUIRED")
            print_warning(f"Found {total_missing} missing records in NEW account")
            print_info("Review sync_integrity_report.txt for detailed fix instructions")
            print_info("\nRecommended Actions:")
            print("1. Run: python smart_hourly_sync.py")
            print("2. Or manually export/import missing data using transfer_gap_csv.py")
            print("3. Re-run this verification script to confirm")
            return False
        else:
            print_header("VERIFICATION SUCCESSFUL")
            print_success("All data is perfectly synced between OLD and NEW accounts!")
            print_success("No missing or extra records found")
            return True
        
    except Exception as e:
        print_error(f"Verification failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
