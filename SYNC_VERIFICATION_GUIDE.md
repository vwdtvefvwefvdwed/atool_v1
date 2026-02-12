# Sync Verification Guide

## Overview
The `verify_sync_integrity.py` script compares all data between OLD and NEW Supabase accounts to ensure no data loss during migration/sync.

## Features

### ✅ What It Does
- **Compares all tables**: users, jobs, sessions, usage_logs, ad_sessions, shared_results
- **Counts records**: Total count in both accounts
- **Identifies missing records**: Records in OLD but not in NEW
- **Identifies extra records**: Records in NEW but not in OLD (newer data)
- **Generates detailed report**: Saved to `sync_integrity_report.txt`
- **Provides fix instructions**: SQL queries to export/import missing data

### 📊 What It Checks
- Record counts match
- All IDs from OLD account exist in NEW account
- No data loss during sync/migration

---

## Usage

### Step 1: Run Verification

```bash
cd C:\Users\RDP\Documents\Atool\backend
python verify_sync_integrity.py
```

### Step 2: Review Results

The script will output:
- ✅ **Green checkmarks**: Table is perfectly synced
- ❌ **Red X marks**: Table has missing/extra records
- 📊 **Summary table**: Record counts comparison

### Step 3: Check Report File

Review `sync_integrity_report.txt` for:
- Detailed missing record IDs
- SQL queries to fix issues
- Export/import instructions

---

## Example Output

```
================================================================================
  SYNC INTEGRITY VERIFICATION - 2026-01-27T10:30:00+00:00
================================================================================

✅ OLD account connected
✅ NEW account connected

📊 Verifying table: users
--------------------------------------------------------------------------------
   Counting records...
   OLD account: 1250 records
   NEW account: 1250 records
   Fetching IDs from OLD account...
   Fetched 1250 IDs total         
   Fetching IDs from NEW account...
   Fetched 1250 IDs total         
✅ users: All records match! (1250 records)

📊 Verifying table: jobs
--------------------------------------------------------------------------------
   Counting records...
   OLD account: 5420 records
   NEW account: 5380 records
   Fetching IDs from OLD account...
   Fetched 5420 IDs total         
   Fetching IDs from NEW account...
   Fetched 5380 IDs total         
⚠️  jobs: Found discrepancies
❌    Missing 40 records in NEW account
   Sample missing record IDs:
      1. abc-123-def-456
      2. xyz-789-ghi-012
      ... and 38 more

================================================================================
  VERIFICATION SUMMARY
================================================================================

Tables Verified: 6
Tables OK: 5
Tables with Issues: 1

Total Records in OLD: 12450
Total Records in NEW: 12410
Total Missing Records: 40
Total Extra Records: 0

Table-by-Table Status:
--------------------------------------------------------------------------------
Table                      OLD        NEW    Missing      Extra     Status
--------------------------------------------------------------------------------
users                     1250       1250          0          0         ✅
jobs                      5420       5380         40          0         ❌
sessions                   850        850          0          0         ✅
usage_logs                3200       3200          0          0         ✅
ad_sessions               1500       1500          0          0         ✅
shared_results             230        230          0          0         ✅
--------------------------------------------------------------------------------

✅ Detailed report saved to: sync_integrity_report.txt
```

---

## Fixing Missing Data

### Option 1: Run Hourly Sync (Recommended)

```bash
python smart_hourly_sync.py
```

This will sync all missing records automatically.

### Option 2: Manual CSV Export/Import

1. Review `sync_integrity_report.txt` for SQL queries
2. Run queries in OLD Supabase SQL Editor
3. Download results as CSV
4. Import using:
   ```bash
   python import_csv_to_new.py
   ```

### Option 3: Use Gap Transfer Tool

```bash
python transfer_gap_csv.py
```

This generates SQL queries to export all missing data since last sync.

---

## Understanding Results

### ✅ All Records Match
- Both accounts have identical data
- No action needed
- Safe to proceed with migration

### ❌ Missing Records
- Records exist in OLD but not in NEW
- **Action Required**: Sync or import missing data
- **Risk**: Data loss if not fixed

### ⚠️ Extra Records
- Records exist in NEW but not in OLD
- Usually means NEW account has newer data
- **Normal**: If app is running against NEW account
- **Warning**: If OLD account is still primary

---

## Best Practices

### Before Migration
1. Run verification to establish baseline
2. Note any discrepancies
3. Fix missing data
4. Re-run verification to confirm

### During Migration
1. Run verification every hour
2. Monitor for new missing records
3. Keep both accounts in sync

### After Migration
1. Final verification run
2. Ensure 0 missing records
3. Document any expected extras (newer data in NEW)
4. Archive OLD account

---

## Troubleshooting

### Error: "Connection failed"
- Check `.env` file has both OLD and NEW credentials
- Verify Supabase URLs are correct
- Ensure API keys have correct permissions

### Error: "Table not found"
- Ensure NEW account has all tables created
- Run migration SQL files first
- Check table names match exactly

### Missing Records Not Syncing
1. Check `sync_metadata` table for last sync time
2. Verify records were created before last sync
3. Run manual sync: `python smart_hourly_sync.py`
4. Re-run verification

### Script Running Slowly
- Normal for large datasets (>10k records per table)
- Each table fetches all IDs in batches of 1000
- Consider running during off-peak hours

---

## Advanced Usage

### Verify Specific Tables Only

Edit `verify_sync_integrity.py` line 17:

```python
# Verify only specific tables
TABLES = ['users', 'jobs']  # Instead of all 6 tables
```

### Change Batch Size

Edit line 30:

```python
BATCH_SIZE = 500  # Smaller batches = slower but more stable
```

### Export Missing IDs to File

The script saves all missing IDs to `sync_integrity_report.txt`.

You can also export programmatically:

```python
# After running verification
with open('missing_jobs.txt', 'w') as f:
    for result in results:
        if result['table'] == 'jobs' and result['missing_count'] > 0:
            for missing_id in result['missing_ids']:
                f.write(f"{missing_id}\n")
```

---

## Workflow Example

### Complete Verification & Fix Workflow

```bash
# 1. Run verification
python verify_sync_integrity.py

# 2. If issues found, sync missing data
python smart_hourly_sync.py

# 3. Verify again to confirm fix
python verify_sync_integrity.py

# 4. Check report
cat sync_integrity_report.txt
```

### Expected Result
```
================================================================================
  VERIFICATION SUCCESSFUL
================================================================================
✅ All data is perfectly synced between OLD and NEW accounts!
✅ No missing or extra records found
```

---

## Integration with CI/CD

### Automated Verification

Add to your deployment pipeline:

```yaml
# Example GitHub Actions
- name: Verify Sync Integrity
  run: |
    cd backend
    python verify_sync_integrity.py
    if [ $? -ne 0 ]; then
      echo "Sync verification failed!"
      exit 1
    fi
```

### Scheduled Verification

Windows Task Scheduler:
```
Task: Hourly Sync Verification
Program: python
Arguments: C:\Users\RDP\Documents\Atool\backend\verify_sync_integrity.py
Trigger: Daily at 11:30 PM
```

---

## Performance

### Benchmarks (Approximate)

| Records | Tables | Duration |
|---------|--------|----------|
| 1,000   | 6      | 5-10s    |
| 10,000  | 6      | 30-60s   |
| 100,000 | 6      | 5-10min  |
| 1M+     | 6      | 30-60min |

### Optimization Tips
- Run during off-peak hours
- Use faster internet connection
- Consider verifying tables one at a time
- Increase `BATCH_SIZE` for faster fetching (but higher memory)

---

## Security Notes

- Uses SERVICE_ROLE_KEY (full access)
- Read-only operations (no data modification)
- Safe to run multiple times
- No impact on production data
- Report file contains record IDs (consider sensitive)

---

## Support

If verification fails or shows unexpected results:

1. Check `sync_integrity_report.txt` for details
2. Review last sync time: `python sync_status.py`
3. Manually inspect sample missing records in Supabase dashboard
4. Re-run hourly sync: `python smart_hourly_sync.py`
5. Re-verify: `python verify_sync_integrity.py`
