# Startup Sync - Automatic Data Transfer on Backend Restart

## Overview

The **Startup Sync** feature automatically transfers all recent data from OLD to NEW Supabase account every time the backend starts. This ensures the NEW account is always up-to-date before the application begins serving requests.

## How It Works

### 1. Backend Startup Sequence

```
1. Flask backend starts
2. ✅ Startup sync runs (transfers all data since last sync)
3. ✅ Hourly sync worker starts (continues syncing every hour)
4. ✅ Flask app starts accepting requests
```

### 2. What Gets Synced

All tables are synced in dependency order:
- ✅ `users` (parent table first)
- ✅ `jobs`
- ✅ `sessions`
- ✅ `usage_logs`
- ✅ `ad_sessions`
- ✅ `shared_results` (NEW!)

### 3. Sync Behavior

- **Syncs data created after last sync timestamp**
- **Automatically syncs missing parent users** (if jobs/sessions reference users not in NEW)
- **Uses upsert** (inserts new records, updates existing ones)
- **Batched processing** (100 records at a time)
- **Non-blocking** (app starts even if sync fails)

---

## Configuration

### Enable Startup Sync

Add to `backend/.env`:

```bash
# Enable startup sync (recommended for production)
ENABLE_STARTUP_SYNC=true

# OLD Account (source - current production)
SUPABASE_URL=https://old-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=old-service-role-key-here

# NEW Account (destination - migration target)
NEW_SUPABASE_URL=https://new-project.supabase.co
NEW_SUPABASE_SERVICE_ROLE_KEY=new-service-role-key-here
```

### Disable Startup Sync

```bash
# Disable if you don't need dual-account sync
ENABLE_STARTUP_SYNC=false
```

---

## Example Startup Log

```
====================================================================
[STARTUP] Running data transfer from OLD to NEW account...
====================================================================
[STARTUP-SYNC] ========================================
[STARTUP-SYNC] STARTUP SYNC - Transferring data from OLD to NEW account
[STARTUP-SYNC] ========================================
[STARTUP-SYNC] Connecting to OLD account...
[STARTUP-SYNC] ✅ OLD account connected
[STARTUP-SYNC] Connecting to NEW account...
[STARTUP-SYNC] ✅ NEW account connected
[STARTUP-SYNC] Syncing data created after: 2026-01-27T10:00:00+00:00
[STARTUP-SYNC] Syncing users...
[STARTUP-SYNC] users: No new records to sync
[STARTUP-SYNC] Syncing jobs...
[STARTUP-SYNC] jobs: Found 15 records to sync
[STARTUP-SYNC] ✅ jobs: 15/15 records synced
[STARTUP-SYNC] Syncing sessions...
[STARTUP-SYNC] sessions: Found 8 records to sync
[STARTUP-SYNC] ✅ sessions: 8/8 records synced
[STARTUP-SYNC] Syncing usage_logs...
[STARTUP-SYNC] usage_logs: No new records to sync
[STARTUP-SYNC] Syncing ad_sessions...
[STARTUP-SYNC] ad_sessions: Found 3 records to sync
[STARTUP-SYNC] ✅ ad_sessions: 3/3 records synced
[STARTUP-SYNC] Syncing shared_results...
[STARTUP-SYNC] shared_results: Found 6 records to sync
[STARTUP-SYNC] ✅ shared_results: 6/6 records synced
[STARTUP-SYNC] ========================================
[STARTUP-SYNC] ✅ STARTUP SYNC COMPLETED - 32 records synced
[STARTUP-SYNC] Details: {'users': 0, 'jobs': 15, 'sessions': 8, 'usage_logs': 0, 'ad_sessions': 3, 'shared_results': 6}
[STARTUP-SYNC] ========================================
[SYNC] Background sync worker thread started
```

---

## Benefits

### ✅ Data Integrity
- NEW account always has latest data before accepting requests
- No data loss between backend restarts
- Catches any missed hourly syncs

### ✅ Zero Downtime
- Sync happens before app starts
- Users always see up-to-date data
- Automatic recovery from sync failures

### ✅ Safety Net
- If hourly sync fails overnight, startup sync catches it
- Syncs missing parent users automatically
- Continues app startup even if sync fails

---

## Use Cases

### 1. Development Workflow

```bash
# Make changes to code
# Stop backend: Ctrl+C
# Restart backend: python app.py

# Startup sync runs automatically
# ✅ NEW account updated with latest data
# Backend starts with fresh data
```

### 2. Production Deployment

```bash
# Deploy new backend version
# Backend restarts automatically

# Startup sync transfers all recent data
# ✅ NEW account synchronized
# Zero downtime for users
```

### 3. Recovery from Sync Failures

```bash
# Hourly sync fails at 3am (network issue)
# Gap of 1 hour of data not synced

# Backend restarts at 8am
# Startup sync runs and syncs missing data from 3am-8am
# ✅ Gap filled automatically
```

---

## Monitoring

### Check Sync Status

View sync metadata in NEW Supabase account:

```sql
SELECT 
    sync_type,
    sync_status,
    last_sync_timestamp,
    records_synced,
    created_at
FROM sync_metadata
WHERE sync_type = 'startup'
ORDER BY created_at DESC
LIMIT 10;
```

### Check Last Startup Sync

```bash
cd backend
python -c "from supabase import create_client; import os; from dotenv import load_dotenv; load_dotenv(); client = create_client(os.getenv('NEW_SUPABASE_URL'), os.getenv('NEW_SUPABASE_SERVICE_ROLE_KEY')); result = client.table('sync_metadata').select('*').eq('sync_type', 'startup').order('created_at', desc=True).limit(1).execute(); print(result.data)"
```

---

## Troubleshooting

### Startup Sync Skipped

**Symptom**: No startup sync messages in logs

**Cause**: `ENABLE_STARTUP_SYNC` not set to `true`

**Fix**:
```bash
# Add to .env
ENABLE_STARTUP_SYNC=true
```

### Startup Sync Failed

**Symptom**: Error message in logs, app continues to start

**Cause**: Connection issues, missing credentials, or table schema mismatch

**Fix**:
1. Check `.env` has both OLD and NEW account credentials
2. Verify NEW account has all tables created
3. Run manual sync: `python startup_sync.py`
4. Check logs for specific error

### Missing Records After Startup

**Symptom**: Some records not synced even after startup

**Cause**: Records created before last sync timestamp

**Fix**:
1. Run verification: `python verify_sync_integrity.py`
2. Check missing records
3. Run gap transfer: `python transfer_gap_csv.py`
4. Restart backend to sync again

### Slow Startup

**Symptom**: Backend takes long to start

**Cause**: Large amount of data to sync

**Expected**: 100-1000 records = 5-30 seconds

**Fix**:
- Normal for large data gaps
- Consider running hourly sync more frequently
- Use `BATCH_SIZE` adjustment in `startup_sync.py` (default 100)

---

## Advanced Configuration

### Adjust Batch Size

Edit `backend/startup_sync.py` line 26:

```python
BATCH_SIZE = 50  # Smaller = slower but more stable
BATCH_SIZE = 200 # Larger = faster but more memory
```

### Customize Sync Tables

Edit `backend/startup_sync.py` line 25:

```python
# Sync only specific tables
SYNC_TABLES = ['users', 'jobs', 'shared_results']
```

### Change Sync Lookback Period

Edit `backend/startup_sync.py` line 70:

```python
# Default: Sync data from last 7 days if no sync history
return (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()  # 14 days
```

---

## Integration with Hourly Sync

### Combined Workflow

```
Backend Restart:
1. Startup Sync runs → Syncs data since last sync
2. Hourly Sync starts → Continues syncing every hour

Timeline:
- T+0:    Startup sync (syncs last 6 hours of missed data)
- T+1hr:  Hourly sync (syncs last hour)
- T+2hr:  Hourly sync (syncs last hour)
- T+3hr:  Hourly sync (syncs last hour)
```

### Recommended Settings

**Development**:
```bash
ENABLE_STARTUP_SYNC=true   # Always sync on restart
ENABLE_HOURLY_SYNC=false   # Optional for dev
```

**Production**:
```bash
ENABLE_STARTUP_SYNC=true   # Critical for production
ENABLE_HOURLY_SYNC=true    # Keep data synced continuously
```

---

## Performance Impact

### Startup Time

| Records to Sync | Startup Delay |
|----------------|---------------|
| 0-100          | +2-5 seconds  |
| 100-1000       | +5-30 seconds |
| 1000-10000     | +30-120 seconds |

### API Call Usage

- ~6-8 API calls per table
- ~40-50 API calls total per startup
- Minimal impact on Supabase free tier (100k/month)

---

## Security Considerations

- Uses `SERVICE_ROLE_KEY` (full database access)
- Runs in backend only (never exposed to frontend)
- No data modification in OLD account (read-only)
- NEW account uses upsert (safe for re-runs)

---

## Best Practices

### ✅ DO
- Enable startup sync in production
- Monitor sync logs for errors
- Run `verify_sync_integrity.py` weekly
- Keep both accounts' schemas synchronized

### ❌ DON'T
- Disable startup sync without hourly sync
- Modify OLD account during sync
- Run multiple backends simultaneously (race conditions)
- Ignore sync failure warnings

---

## Testing

### Test Startup Sync Manually

```bash
cd backend
python startup_sync.py
```

### Verify Results

```bash
python verify_sync_integrity.py
```

### Simulate Backend Restart

```bash
# Stop backend
Ctrl+C

# Restart
python app.py

# Watch for startup sync messages
```

---

## FAQ

**Q: Will startup sync slow down my backend?**  
A: Only during startup. Adds 2-30 seconds depending on data volume.

**Q: What if startup sync fails?**  
A: Backend continues to start. Check logs and run manual sync.

**Q: Can I run startup sync manually?**  
A: Yes: `python startup_sync.py`

**Q: Does it sync ALL data every time?**  
A: No, only data created after last successful sync timestamp.

**Q: What happens if I restart backend frequently?**  
A: Each restart syncs only new data since last sync. Safe to restart anytime.

**Q: Can I sync in reverse (NEW → OLD)?**  
A: Not recommended. OLD is source of truth. NEW is backup/migration target.

---

## Related Scripts

- `startup_sync.py` - Main startup sync script
- `smart_hourly_sync.py` - Hourly background sync
- `verify_sync_integrity.py` - Check data integrity
- `transfer_gap_csv.py` - Manual gap data export
- `sync_status.py` - View sync history

---

## Support

For issues or questions:
1. Check sync logs in console
2. Run verification: `python verify_sync_integrity.py`
3. Review `sync_integrity_report.txt`
4. Check `sync_metadata` table in NEW account
