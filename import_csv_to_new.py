"""
Import CSV to NEW Account
Imports manually downloaded CSV files from OLD account into NEW account
Uses API only on NEW account (OLD account requires zero API requests)
"""

import os
import csv
import sys
from datetime import datetime, timezone
from dotenv_vault import load_dotenv
from supabase import create_client

load_dotenv()

NEW_SUPABASE_URL = os.getenv('NEW_SUPABASE_URL')
NEW_SUPABASE_KEY = os.getenv('NEW_SUPABASE_SERVICE_ROLE_KEY')

# Import order (important for foreign keys)
IMPORT_ORDER = ['users', 'jobs', 'sessions', 'usage_logs', 'ad_sessions', 'shared_results']
BATCH_SIZE = 100


def print_header(text: str):
    print("\n" + "=" * 80)
    print(f"  {text}")
    print("=" * 80)


def print_info(text: str):
    print(f"ℹ️  {text}")


def print_success(text: str):
    print(f"✅ {text}")


def print_error(text: str):
    print(f"❌ {text}")


def print_warning(text: str):
    print(f"⚠️  {text}")


def read_csv_file(filename: str) -> list:
    """Read CSV file and return list of dictionaries"""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            data = list(reader)
            return data
    except FileNotFoundError:
        return None
    except Exception as e:
        print_error(f"Error reading {filename}: {e}")
        return None


def is_empty_value(value):
    """Check if value is empty, null, or None"""
    if value is None:
        return True
    if isinstance(value, str):
        v_lower = value.lower().strip()
        return v_lower in ('', 'null', 'none')
    return False


def parse_standalone_csv(filename: str) -> dict:
    """Parse standalone CSV (with table_name column) into separate table data"""
    try:
        print_info(f"Reading standalone CSV: {filename}")
        data = read_csv_file(filename)
        
        if not data:
            return {}
        
        # Check if this is a standalone CSV (has table_name column)
        if 'table_name' not in data[0]:
            return {}
        
        # Group records by table_name
        tables_data = {}
        for row in data:
            table = row.pop('table_name')
            
            # Remove NULL/empty columns
            cleaned_row = {k: v for k, v in row.items() if not is_empty_value(v)}
            
            # Convert text fields back to proper types
            if table == 'users':
                cleaned_row['id'] = cleaned_row.get('id')
                if 'credits' in cleaned_row and not is_empty_value(cleaned_row['credits']):
                    try:
                        cleaned_row['credits'] = int(cleaned_row['credits'])
                    except:
                        cleaned_row['credits'] = 0
                if 'generation_count' in cleaned_row and not is_empty_value(cleaned_row['generation_count']):
                    try:
                        cleaned_row['generation_count'] = int(cleaned_row['generation_count'])
                    except:
                        cleaned_row['generation_count'] = 0
                if 'is_active' in cleaned_row:
                    cleaned_row['is_active'] = str(cleaned_row['is_active']).lower() == 'true'
                if 'is_flagged' in cleaned_row:
                    cleaned_row['is_flagged'] = str(cleaned_row['is_flagged']).lower() == 'true'
            
            elif table == 'jobs':
                # Rename id column to job_id for jobs table
                if 'id' in cleaned_row:
                    cleaned_row['job_id'] = cleaned_row.pop('id')
                if 'progress' in cleaned_row and not is_empty_value(cleaned_row['progress']):
                    try:
                        cleaned_row['progress'] = int(cleaned_row['progress'])
                    except:
                        cleaned_row['progress'] = 0
                if 'width' in cleaned_row and not is_empty_value(cleaned_row['width']):
                    try:
                        cleaned_row['width'] = int(cleaned_row['width'])
                    except:
                        del cleaned_row['width']
                if 'height' in cleaned_row and not is_empty_value(cleaned_row['height']):
                    try:
                        cleaned_row['height'] = int(cleaned_row['height'])
                    except:
                        del cleaned_row['height']
            
            elif table == 'sessions':
                # Rename id column to session_id for sessions table
                if 'id' in cleaned_row:
                    cleaned_row['session_id'] = cleaned_row.pop('id')
            
            elif table == 'usage_logs':
                if 'credits_used' in cleaned_row and not is_empty_value(cleaned_row['credits_used']):
                    try:
                        cleaned_row['credits_used'] = int(cleaned_row['credits_used'])
                    except:
                        cleaned_row['credits_used'] = 0
            
            elif table == 'ad_sessions':
                if 'monetag_verified' in cleaned_row:
                    cleaned_row['monetag_verified'] = str(cleaned_row['monetag_verified']).lower() == 'true'
                if 'monetag_revenue' in cleaned_row and not is_empty_value(cleaned_row['monetag_revenue']):
                    try:
                        cleaned_row['monetag_revenue'] = float(cleaned_row['monetag_revenue'])
                    except:
                        cleaned_row['monetag_revenue'] = 0.0
            
            elif table == 'shared_results':
                if 'view_count' in cleaned_row and not is_empty_value(cleaned_row['view_count']):
                    try:
                        cleaned_row['view_count'] = int(cleaned_row['view_count'])
                    except:
                        cleaned_row['view_count'] = 0
                if 'click_count' in cleaned_row and not is_empty_value(cleaned_row['click_count']):
                    try:
                        cleaned_row['click_count'] = int(cleaned_row['click_count'])
                    except:
                        cleaned_row['click_count'] = 0
                if 'conversion_count' in cleaned_row and not is_empty_value(cleaned_row['conversion_count']):
                    try:
                        cleaned_row['conversion_count'] = int(cleaned_row['conversion_count'])
                    except:
                        cleaned_row['conversion_count'] = 0
                if 'is_public' in cleaned_row:
                    cleaned_row['is_public'] = str(cleaned_row['is_public']).lower() == 'true'
            
            # Add to table data
            if table not in tables_data:
                tables_data[table] = []
            tables_data[table].append(cleaned_row)
        
        # Print summary
        print_success("Standalone CSV parsed successfully:")
        for table, records in tables_data.items():
            print_info(f"  • {table}: {len(records)} records")
        
        return tables_data
        
    except Exception as e:
        print_error(f"Error parsing standalone CSV: {e}")
        import traceback
        traceback.print_exc()
        return {}


def import_table(client, table_name: str, csv_file: str) -> int:
    """Import CSV data to Supabase table"""
    try:
        print(f"\n📊 Importing {table_name}...")
        print(f"   Reading: {csv_file}")
        
        # Read CSV
        data = read_csv_file(csv_file)
        
        if data is None:
            print_warning(f"   {csv_file} not found - skipping")
            return 0
        
        if len(data) == 0:
            print_info(f"   {csv_file} is empty - skipping")
            return 0
        
        total = len(data)
        print_info(f"   Found {total} records")
        
        # Import in batches
        imported = 0
        for i in range(0, total, BATCH_SIZE):
            batch = data[i:i + BATCH_SIZE]
            
            try:
                client.table(table_name).upsert(batch).execute()
                imported += len(batch)
                
                if total > BATCH_SIZE:
                    print_info(f"   Batch {i//BATCH_SIZE + 1}: {len(batch)} records imported")
            except Exception as batch_error:
                print_error(f"   Batch {i//BATCH_SIZE + 1} failed: {batch_error}")
                continue
        
        print_success(f"{table_name}: {imported}/{total} records imported")
        return imported
        
    except Exception as e:
        print_error(f"Error importing {table_name}: {e}")
        return 0


def import_from_data(client, table_name: str, data: list) -> int:
    """Import data list directly to Supabase table"""
    try:
        if not data:
            return 0
        
        total = len(data)
        imported = 0
        
        # Import in batches
        for i in range(0, total, BATCH_SIZE):
            batch = data[i:i + BATCH_SIZE]
            
            try:
                client.table(table_name).upsert(batch).execute()
                imported += len(batch)
                
                if total > BATCH_SIZE:
                    print_info(f"   Batch {i//BATCH_SIZE + 1}: {len(batch)} records imported")
            except Exception as batch_error:
                print_error(f"   Batch {i//BATCH_SIZE + 1} failed: {batch_error}")
                continue
        
        return imported
        
    except Exception as e:
        print_error(f"Error importing data: {e}")
        return 0


def main():
    print_header("CSV IMPORT TO NEW ACCOUNT")
    
    # Validate configuration
    if not NEW_SUPABASE_URL or not NEW_SUPABASE_KEY:
        print_error("NEW_SUPABASE_URL or NEW_SUPABASE_SERVICE_ROLE_KEY not configured")
        sys.exit(1)
    
    # Check for standalone CSV first
    print_info("Looking for CSV files in current directory...")
    standalone_file = "all_data.csv"
    use_standalone = False
    tables_data = {}
    
    if os.path.exists(standalone_file):
        print_success(f"Found standalone CSV: {standalone_file}")
        tables_data = parse_standalone_csv(standalone_file)
        
        if tables_data:
            use_standalone = True
            print_success("Will use standalone CSV for import")
        else:
            print_warning("Standalone CSV found but invalid, checking for separate files...")
    
    # If no standalone, check for separate CSV files
    csv_files = {}
    missing_files = []
    
    if not use_standalone:
        for table in IMPORT_ORDER:
            filename = f"{table}.csv"
            if os.path.exists(filename):
                csv_files[table] = filename
                print_success(f"Found: {filename}")
            else:
                missing_files.append(filename)
                print_warning(f"Missing: {filename}")
        
        if not csv_files:
            print_error("No CSV files found in current directory")
            print_info("Expected files: all_data.csv OR users.csv, jobs.csv, sessions.csv, usage_logs.csv, ad_sessions.csv, shared_results.csv")
            sys.exit(1)
    
    # Confirm import
    print("\n" + "=" * 80)
    if use_standalone:
        total_records = sum(len(records) for records in tables_data.values())
        print(f"Standalone CSV with {total_records} records across {len(tables_data)} tables")
    else:
        print(f"Found {len(csv_files)} separate CSV files to import")
        if missing_files:
            print(f"Missing {len(missing_files)} files (will be skipped): {', '.join(missing_files)}")
    
    response = input("⚠️  Proceed with import to NEW account? (yes/no): ").strip().lower()
    print("=" * 80)
    
    if response not in ['yes', 'y']:
        print_info("Import cancelled")
        sys.exit(0)
    
    try:
        # Connect to NEW account
        print_info("Connecting to NEW account...")
        client = create_client(NEW_SUPABASE_URL, NEW_SUPABASE_KEY)
        
        # Verify connection
        client.table('users').select('id').limit(1).execute()
        print_success("NEW account connected")
        
        # Import data
        total_imported = 0
        results = {}
        
        if use_standalone:
            # Import from standalone CSV (already parsed)
            for table in IMPORT_ORDER:
                if table in tables_data:
                    print(f"\n📊 Importing {table}...")
                    print_info(f"   Found {len(tables_data[table])} records")
                    count = import_from_data(client, table, tables_data[table])
                    results[table] = count
                    total_imported += count
                    print_success(f"{table}: {count}/{len(tables_data[table])} records imported")
                else:
                    results[table] = 0
        else:
            # Import from separate CSV files
            for table in IMPORT_ORDER:
                if table in csv_files:
                    count = import_table(client, table, csv_files[table])
                    results[table] = count
                    total_imported += count
                else:
                    results[table] = 0
        
        # Update sync metadata
        current_time = datetime.now(timezone.utc).isoformat()
        update_data = {
            'sync_type': 'csv_import',
            'sync_status': 'completed',
            'last_sync_timestamp': current_time,
            'records_synced': results,
            'updated_at': current_time
        }
        
        try:
            client.table('sync_metadata').insert(update_data).execute()
            print_info("Sync metadata updated")
        except Exception as e:
            print_warning(f"Could not update sync_metadata: {e}")
        
        # Summary
        print_header("CSV IMPORT COMPLETED")
        print_success(f"Total records imported: {total_imported}")
        print_info(f"Details: {results}")
        
        print("\n✅ CSV import successful!")
        
    except Exception as e:
        print_error(f"Import failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
