# Dual-Account Sync System Guide

## Overview

The dual-account sync system allows seamless migration between Supabase accounts when approaching free-tier limits (500MB database, 50k API requests/month).

**Strategy**: Incrementally sync NEW data from OLD account to NEW account every hour, so when OLD account hits limits, 99% of data is already transferred.

---

## How It Works

### Architecture

```
OLD Supabase Account (Primary → Backup)
├─ All existing data
├─ Reaches limit at day 30
└─ Becomes READ-ONLY

        ↓ Hourly Sync (smart_hourly_sync.py)

NEW Supabase Account (Backup → Primary)
├─ Receives incremental updates every hour
├─ 99% synced by day 30
└─ Becomes new primary after OLD hits limit
```

### Smart State-Based Sync

Instead of hardcoded "sync last 1 hour", the system:

1. **Queries NEW account**: "What was last successful sync timestamp?"
2. **Fetches from OLD account**: "Get all data created AFTER that timestamp"
3. **Transfers to NEW account**: Using API (upsert = insert or update)
4. **Updates NEW account**: "Last sync timestamp = NOW()"

**Benefits**:
- ✅ Self-healing: If sync fails at 2 PM, next sync at 3 PM fetches 2 hours of data
- ✅ No data loss even if syncs are skipped
- ✅ Resumable from any point
- ✅ Audit trail of all sync operations

---

## Setup Instructions

### Step 1: Create New Supabase Account

1. Go to [supabase.com](https://supabase.com)
2. Create new project (e.g., "atool-backup")
3. Wait for project initialization (~2 minutes)
4. Copy credentials:
   - URL: `https://xxx.supabase.co`
   - Anon key: Settings → API → anon public
   - Service role key: Settings → API → service_role (secret!)

### Step 2: Run Schema on NEW Account

```bash
# Open Supabase Dashboard → SQL Editor
# Copy contents of backend/migrations/000_clean_schema_no_coins.sql
# Paste and click "Run"
```

This creates:
- All tables (users, jobs, sessions, etc.)
- **sync_metadata table** (tracks sync operations)
- Indexes and RLS policies

### Step 3: Configure Environment Variables

Add to `backend/.env`:

```env
# NEW account (current main becomes this)
SUPABASE_URL=https://new-project.supabase.co
SUPABASE_KEY=new-anon-key
SUPABASE_SERVICE_ROLE_KEY=new-service-role-key

# OLD account (when migrating)
OLD_SUPABASE_URL=https://old-project.supabase.co
OLD_SUPABASE_KEY=old-anon-key
OLD_SUPABASE_SERVICE_ROLE_KEY=old-service-role-key

# Enable sync
ENABLE_HOURLY_SYNC=true
```

### Step 4: Initialize Sync System

```bash
cd backend
python setup_sync.py
```

This will:
- Verify both accounts are accessible
- Check sync_metadata table exists
- Create initial baseline sync record
- Show account statistics

### Step 5: Test Manual Sync

```bash
python smart_hourly_sync.py
```

Expected output:
```
================================================================================
  SMART HOURLY SYNC - 2024-01-13T15:00:00
================================================================================
✅ OLD account connection verified
✅ NEW account connection verified
ℹ️  Last sync timestamp from database: 2024-01-13T14:00:00
ℹ️  Current time: 2024-01-13T15:00:00

📊 Syncing table: users
   Found 5 new records
✅ users: 5/5 records synced successfully

📊 Syncing table: jobs
   Found 12 new records
✅ jobs: 12/12 records synced successfully

================================================================================
  SYNC COMPLETED SUCCESSFULLY
================================================================================
✅ Total records synced: 17
ℹ️  Next sync will fetch data after: 2024-01-13T15:00:00
```

### Step 6: Set Up Automated Hourly Sync

#### Windows (Task Scheduler)

1. Open Task Scheduler
2. Create Basic Task:
   - **Name**: "Atool Hourly Sync"
   - **Trigger**: Daily, repeat every 1 hour for 24 hours
   - **Action**: Start a program
   - **Program**: `python`
   - **Arguments**: `smart_hourly_sync.py`
   - **Start in**: `C:\Users\YourUser\Documents\Atool\backend`
3. Enable "Run whether user is logged on or not"

#### Linux (Cron)

```bash
# Edit crontab
crontab -e

# Add hourly sync (runs at minute 0 of every hour)
0 * * * * cd /path/to/Atool/backend && python smart_hourly_sync.py >> /var/log/sync.log 2>&1
```

---

## Monitoring

### Check Sync Status

```bash
python sync_status.py
```

Shows:
- Latest sync operation details
- Sync history (last 10)
- Success rate statistics
- Health check (last sync time, status, frequency)

### Monitor via Supabase Dashboard

Query `sync_metadata` table:

```sql
SELECT 
    created_at,
    sync_status,
    last_sync_timestamp,
    records_synced
FROM sync_metadata
ORDER BY created_at DESC
LIMIT 20;
```

---

## Migration Timeline

### Phase 1: Preparation (Day 1-5)

- ✅ Create NEW account
- ✅ Run schema
- ✅ Configure .env
- ✅ Test manual sync
- ✅ Set up hourly automation

**Status**: Both accounts active, OLD is primary, NEW is backup

### Phase 2: Continuous Sync (Day 6-29)

- 🔄 Hourly sync runs automatically
- 📊 NEW account stays 99% synced
- ⚠️ Monitor OLD account usage

**Status**: OLD approaching limits, NEW catches up daily

### Phase 3: OLD Account Hits Limit (Day 30)

1. **OLD becomes READ-ONLY** (500MB database limit reached)
2. **Transfer gap data** - Choose one method:

#### Option A: Manual CSV Export/Import (Zero API to OLD) ✅ Recommended

When OLD account hits limit, it can't accept API requests but you can still download data manually.

**Step 1: Get last sync timestamp**
```bash
cd backend
python transfer_gap_csv.py
```

This shows the last sync time and generates SQL queries.

**Step 2: Export from OLD Account (Manual)**
1. Go to OLD Supabase Dashboard → Table Editor
2. For each table (users, jobs, sessions, usage_logs, ad_sessions):
   - Open the table
   - Click "Export" → "CSV"
   - Or use SQL Editor with the generated queries
3. Save files as: `users.csv`, `jobs.csv`, `sessions.csv`, `usage_logs.csv`, `ad_sessions.csv`
4. Place CSV files in `backend/` directory

**Step 3: Import to NEW Account (Automatic)**
```bash
cd backend
python import_csv_to_new.py
```

This script:
- Reads CSV files from current directory
- Imports to NEW account using API (in correct order)
- Updates sync metadata
- Handles foreign key dependencies

**Expected Output**:
```
================================================================================
  CSV IMPORT TO NEW ACCOUNT
================================================================================
✅ Found: users.csv
✅ Found: jobs.csv
⚠️  Proceed with import to NEW account? (yes/no): yes

📊 Importing users...
✅ users: 7/7 records imported

📊 Importing jobs...
✅ jobs: 455/455 records imported

✅ Total records imported: 592
```

**API Usage**: 
- Zero API requests to OLD account (manual download)
- ~10-15 API requests to NEW account only

#### Option B: Automatic Transfer via API

```bash
cd backend
python transfer_gap_data.py
```

**API Usage**: ~16-20 requests (one-time)

**Expected Output**:
```
✅ users: 2 records transferred
✅ jobs: 15 records transferred
✅ Total records transferred: 17
```

3. **Verify data transfer**:

```bash
python sync_status.py
```

4. **Switch to NEW account**:

```env
# Update .env - swap credentials
# OLD account values become comments
# SUPABASE_URL=https://gtgnwrwbcxvasgetfzby.supabase.co
# SUPABASE_ANON_KEY=old-anon-key
# SUPABASE_SERVICE_ROLE_KEY=old-service-key

# NEW account values become primary
SUPABASE_URL=https://anpxbqnyrribcdnbnmai.supabase.co
SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImFucHhicW55cnJpYmNkbmJubWFpIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjgzMTg1MDIsImV4cCI6MjA4Mzg5NDUwMn0.Qs1BkrbfEMPg4wSwB4YoJVqGePhsu0wCpHVLMTuNdAY
SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImFucHhicW55cnJpYmNkbmJubWFpIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc2ODMxODUwMiwiZXhwIjoyMDgzODk0NTAyfQ.ER0DJJ0bIbytXhtK2KrysqNt5QgPnUuucbmiEtXnYv8

# Turn off hourly sync (no longer needed)
ENABLE_HOURLY_SYNC=false
```

5. **Deploy edge function to NEW account**:

```bash
# See EDGE_FUNCTION_MIGRATION.md for detailed steps
# Edge function routes queue operations to worker accounts
```

6. **Restart backend**:

```bash
# Stop services
pkill -f "python app.py"
pkill -f "python job_worker_realtime.py"

# Start with NEW account
python app.py &
python job_worker_realtime.py &
```

### Phase 4: Post-Migration (Day 31+)

- ✅ NEW account is now primary
- ✅ OLD account can be archived or deleted
- ⚠️ Monitor NEW account usage
- 📊 Repeat process when NEW approaches limits

---

## Troubleshooting

### Sync Not Running

**Check**:
```bash
python smart_hourly_sync.py
```

**Common issues**:
- `ENABLE_HOURLY_SYNC=false` in .env
- OLD credentials not set
- sync_metadata table missing
- Network connectivity issues

### Sync Failing

**Check error message**:
```bash
python sync_status.py
```

**Common causes**:
- API rate limits exceeded (too many syncs)
- Table schema mismatch (NEW vs OLD)
- Network timeout on large data transfer
- RLS policies blocking access

**Solution**:
- Reduce sync frequency if hitting rate limits
- Verify schemas match
- Increase batch size for large transfers
- Use service role keys (bypass RLS)

### Data Mismatch

**Verify sync**:
```sql
-- Count records in OLD account
SELECT 'users' as table_name, COUNT(*) FROM users
UNION ALL
SELECT 'jobs', COUNT(*) FROM jobs;

-- Compare with NEW account
-- Counts should be similar (±1 hour of data)
```

**Fix**:
- Run manual sync
- Check sync_metadata for errors
- Verify last_sync_timestamp is recent

---

## API Usage

Each hourly sync uses approximately:

- 1 SELECT from NEW (get last_sync_time)
- 5 SELECT from OLD (users, jobs, sessions, usage_logs, ad_sessions)
- 5 UPSERT to NEW (same tables)
- 1 INSERT to NEW (update sync_metadata)

**Total: ~12 API calls per sync**

**Monthly**: 12 calls × 24 hours × 30 days = **8,640 API calls** (~17% of 50k limit)

Very sustainable for free tier!

---

## File Reference

| File | Purpose |
|------|---------|
| `smart_hourly_sync.py` | Main sync script (run hourly) |
| `transfer_gap_csv.py` | Generate SQL queries for manual CSV export from OLD account |
| `import_csv_to_new.py` | **Import CSV files to NEW account (zero API to OLD) - Recommended** |
| `transfer_gap_data.py` | API-based gap data transfer (uses ~20 API requests to both accounts) |
| `setup_sync.py` | Initialize sync system (run once) |
| `sync_status.py` | Monitor sync health and history |
| `000_clean_schema_no_coins.sql` | Database schema (includes sync_metadata) |
| `.env` | Configuration (credentials, flags) |

---

## Best Practices

1. **Monitor regularly**: Run `sync_status.py` weekly
2. **Test before automating**: Run manual sync successfully first
3. **Keep logs**: Enable logging in task scheduler/cron
4. **Backup credentials**: Save OLD account credentials even after migration
5. **Plan ahead**: Start syncing when at 60-70% of limits, not 95%

---

## FAQ

**Q: What if I miss multiple hourly syncs?**  
A: No problem! Next sync will catch up automatically from last successful timestamp.

**Q: Can I sync more frequently than hourly?**  
A: Yes, but watch API limits. Every 30 minutes = ~17k API calls/month.

**Q: What if OLD account is paused before I can migrate?**  
A: Wake it up by visiting dashboard, then run CLI export immediately.

**Q: Do I need to sync ALL tables?**  
A: No, edit `SYNC_TABLES` in `smart_hourly_sync.py` to exclude non-critical tables.

**Q: Can I use this for ongoing multi-account operation?**  
A: Not recommended. This is designed for one-time migration. For permanent multi-account, use the worker system.

**Q: How can I avoid API requests to OLD account when it hits the limit?**  
A: Use the CSV workflow:
1. Manually export CSV from OLD account dashboard (zero API)
2. Run `import_csv_to_new.py` to import to NEW account (API to NEW only)
This makes zero API requests to OLD account and ~10-15 requests to NEW account only.

---

## Support

If you encounter issues:

1. Check `sync_status.py` for error messages
2. Review sync_metadata table in NEW account
3. Test manual sync: `python smart_hourly_sync.py`
4. Verify credentials in `.env`
5. Check Supabase dashboard for account health

---

**Last Updated**: 2024-01-13  
**System Version**: 1.0
