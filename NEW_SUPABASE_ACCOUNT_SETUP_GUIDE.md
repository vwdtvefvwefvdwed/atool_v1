# Complete Guide: Fresh Supabase Account Setup

## 📋 Overview

This guide walks you through creating a **brand new Supabase account** from scratch and migrating your entire system to it. Perfect for when your current account approaches free-tier limits (500MB database, 50k API requests/month).

**What This Guide Covers:**
- ✅ Create new Supabase project
- ✅ Set up complete database schema
- ✅ Enable realtime for job updates
- ✅ Deploy edge function for queue routing
- ✅ Configure backend and frontend
- ✅ Test everything before going live
- ✅ Switch from old to new account

**Time Required:** 30-45 minutes

---

## 🚀 Step 1: Create New Supabase Project

### 1.1 Sign Up or Login
1. Go to [supabase.com](https://supabase.com)
2. Sign in or create account
3. Click **"New Project"**

### 1.2 Project Configuration
- **Organization**: Select or create new
- **Name**: `atool-new` (or your preferred name)
- **Database Password**: Generate strong password and **save it securely**
- **Region**: Choose closest to your users (e.g., `us-east-1`, `eu-central-1`)
- **Pricing Plan**: Free

Click **"Create new project"** and wait ~2 minutes for initialization.

### 1.3 Copy Project Credentials

Once project is ready, go to **Settings → API**:

```env
# Save these values - you'll need them later
NEW_SUPABASE_URL=https://xxxxxxxxxxxxx.supabase.co
NEW_SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
NEW_SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

Also note your **Project Reference ID** from **Settings → General**:
```
NEW_PROJECT_REF=xxxxxxxxxxxxx
```

---

## 🗄️ Step 2: Set Up Database Schema

### 2.1 Open SQL Editor
1. In Supabase Dashboard, go to **SQL Editor**
2. Click **"New Query"**

### 2.2 Run Complete Schema
Copy the entire contents of `backend/migrations/000_clean_schema_no_coins.sql` and paste into SQL editor.

**Or manually open and copy:**
```bash
# In your project directory
cat backend/migrations/000_clean_schema_no_coins.sql
```

Click **"Run"** or press `Ctrl+Enter`

### 2.3 Verify Tables Created
Go to **Table Editor** in Supabase Dashboard. You should see:

**Core Tables:**
- ✅ `users`
- ✅ `jobs`
- ✅ `sessions`
- ✅ `magic_links`
- ✅ `usage_logs`

**Queue Tables:**
- ✅ `priority1_queue`
- ✅ `priority2_queue`
- ✅ `priority3_queue`

**System Tables:**
- ✅ `sync_metadata`
- ✅ `modal_deployments`
- ✅ `ad_sessions`
- ✅ `flagged_ips`
- ✅ `provider_api_keys`
- ✅ `deleted_api_keys`
- ✅ `shared_results`
- ✅ `provider_trials`
- ✅ `model_quotas`

**Total:** 17 tables should be created.

---

## 🔔 Step 3: Enable Realtime

### 3.1 Verify Realtime Configuration in Schema
The `000_clean_schema_no_coins.sql` already includes:
```sql
ALTER TABLE jobs REPLICA IDENTITY FULL;
```

This is **already done** from Step 2, but let's verify:

### 3.2 Enable Realtime Publication (CRITICAL)
Go to **Database → Replication** in Supabase Dashboard:

1. Find **"supabase_realtime"** publication
2. Click **"Edit"**
3. Enable replication for **`jobs`** table
4. Select these events:
   - ✅ INSERT
   - ✅ UPDATE
   - ✅ DELETE
5. Click **"Save"**

**Alternative: Enable via SQL**
```sql
-- Run this in SQL Editor if the above doesn't work
ALTER PUBLICATION supabase_realtime ADD TABLE jobs;

-- Grant permissions for realtime
GRANT SELECT ON jobs TO anon;
GRANT SELECT ON jobs TO authenticated;
```

### 3.3 Verify Realtime is Enabled
```sql
-- Check publication includes jobs table
SELECT tablename 
FROM pg_publication_tables 
WHERE pubname = 'supabase_realtime';
```

Expected output should include `jobs`.

---

## ⚡ Step 4: Deploy Edge Function

### 4.1 Install Supabase CLI
```bash
# Install globally
npm install -g supabase

# Verify installation
supabase --version
```

### 4.2 Login to Supabase
```bash
supabase login
```

This opens browser for authentication. Click **"Authorize"**.

### 4.3 Deploy Edge Function
```bash
cd backend/supabase/functions
supabase functions deploy route-queue --project-ref <NEW_PROJECT_REF>
```

Replace `<NEW_PROJECT_REF>` with your project reference ID from Step 1.3.

**Expected Output:**
```
Deploying function route-queue...
✓ Function route-queue deployed successfully
Function URL: https://xxxxxxxxxxxxx.supabase.co/functions/v1/route-queue
```

**Save this URL** - you'll need it for backend configuration.

### 4.4 Set Worker Secrets

The edge function needs credentials for the 3 worker accounts. Set them as environment variables:

```bash
# Worker 1 credentials
supabase secrets set WORKER_1_URL=https://gmhpbeqvqpuoctaqgnum.supabase.co --project-ref <NEW_PROJECT_REF>
supabase secrets set WORKER_1_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImdtaHBiZXF2cXB1b2N0YXFnbnVtIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjM5ODI4NzAsImV4cCI6MjA3OTU1ODg3MH0.I-6DUCSsjtSIRij3pRdRw9Ws0IVtJQcMDnX92IGGheA --project-ref <NEW_PROJECT_REF>

# Worker 2 credentials
supabase secrets set WORKER_2_URL=https://weuavzmjqyfjlfzcybtr.supabase.co --project-ref <NEW_PROJECT_REF>
supabase secrets set WORKER_2_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6IndldWF2em1qcXlmamxmemN5YnRyIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjM5NTA0NjgsImV4cCI6MjA3OTUyNjQ2OH0.Ypp-KOZNMichYXIw5qNbHWmdg-9rRUfloXhSqAkalUs --project-ref <NEW_PROJECT_REF>

# Worker 3 credentials
supabase secrets set WORKER_3_URL=https://zqicjcipwhtovgzxjgjn.supabase.co --project-ref <NEW_PROJECT_REF>
supabase secrets set WORKER_3_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InpxaWNqY2lwd2h0b3ZnenhqZ2puIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjM5ODI5MDksImV4cCI6MjA3OTU1ODkwOX0.qC6Q0pHJIyIu_IFqu8KYpMwlB8VSYusfw7UFDNjTjTU --project-ref <NEW_PROJECT_REF>
```

### 4.5 Verify Secrets
```bash
supabase secrets list --project-ref <NEW_PROJECT_REF>
```

Expected output:
```
WORKER_1_URL
WORKER_1_ANON_KEY
WORKER_2_URL
WORKER_2_ANON_KEY
WORKER_3_URL
WORKER_3_ANON_KEY
```

All 6 secrets should be present.

---

## ⚙️ Step 5: Configure Backend Environment

### 5.1 Update `backend/.env`

Open `backend/.env` and update these values:

```env
# ============================================================
# NEW SUPABASE ACCOUNT (Primary)
# ============================================================
SUPABASE_URL=https://xxxxxxxxxxxxx.supabase.co
SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

# Edge Function URL (from Step 4.3)
EDGE_FUNCTION_URL=https://xxxxxxxxxxxxx.supabase.co/functions/v1/route-queue

# ============================================================
# OLD SUPABASE ACCOUNT (Backup - for data migration only)
# ============================================================
# Only needed if syncing data from old account
# NEW_SUPABASE_URL=https://old-project.supabase.co
# NEW_SUPABASE_ANON_KEY=old-anon-key
# NEW_SUPABASE_SERVICE_ROLE_KEY=old-service-role-key

# Sync settings (disable for fresh account)
ENABLE_STARTUP_SYNC=false
ENABLE_HOURLY_SYNC=false
```

**Key Changes:**
1. Replace `SUPABASE_URL` with NEW account URL
2. Replace `SUPABASE_ANON_KEY` with NEW account anon key
3. Replace `SUPABASE_SERVICE_ROLE_KEY` with NEW account service role key
4. Update `EDGE_FUNCTION_URL` to NEW edge function URL
5. Disable sync settings (no old data to sync for fresh account)

### 5.2 Keep Other Settings
Leave these unchanged:
```env
# Cloudinary (stays the same)
CLOUDINARY_CLOUD_NAME=your-cloud-name
CLOUDINARY_API_KEY=your-api-key
CLOUDINARY_API_SECRET=your-api-secret

# Discord, Telegram, MoneyTag, etc. (stays the same)
# ... all other settings ...
```

---

## 🎨 Step 6: Configure Frontend Environment

### 6.1 Update Root `.env`

Open `.env` in project root and update:

```env
# Backend API URL (stays the same)
VITE_API_URL=https://api.rasenai.qzz.io

# ============================================================
# NEW SUPABASE ACCOUNT (Frontend - Primary)
# ============================================================
VITE_SUPABASE_URL=https://xxxxxxxxxxxxx.supabase.co
VITE_SUPABASE_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

# ============================================================
# OLD SUPABASE ACCOUNT (Frontend - Backup for failover)
# ============================================================
# IMPORTANT: Configure this for automatic failover support
# If backend switches to OLD account due to rate limits,
# frontend will automatically reconnect within 30 seconds
VITE_BACKUP_SUPABASE_URL=https://old-project.supabase.co
VITE_BACKUP_SUPABASE_ANON_KEY=old-anon-key-here
```

**Important:** 
- Use the same `SUPABASE_URL` and `SUPABASE_ANON_KEY` from backend config
- Set `VITE_BACKUP_SUPABASE_URL` to your OLD account for automatic failover
- This enables frontend to follow backend if it switches accounts

### 6.2 Enable Automatic Failover (Recommended)

Add failover monitor to automatically reconnect if backend switches accounts:

**File:** `src/App.jsx`

Add import at top:
```jsx
import FailoverMonitor from './components/FailoverMonitor';
```

Add component inside your App (near the top):
```jsx
function App() {
  return (
    <div className="App">
      <FailoverMonitor /> {/* Add this for automatic failover */}
      
      {/* Rest of your app */}
      <Router>
        {/* ... */}
      </Router>
    </div>
  );
}
```

This enables frontend to automatically detect and reconnect when backend switches Supabase accounts.

**See:** `FRONTEND_FAILOVER_GUIDE.md` for details.

### 6.3 Verify Frontend Supabase Client

Check `src/utils/supabaseClient.js`:
```javascript
import { getSupabaseClient } from '../hooks/useSupabaseFailover';
export const supabase = getSupabaseClient();
```

The client now supports automatic failover and uses `.env` values.

---

## 🧪 Step 7: Test Everything

### 7.1 Test Worker Connectivity
```bash
cd backend
python test_workers.py
```

**Expected Output:**
```
================================================================================
  TESTING EDGE FUNCTION ROUTING
================================================================================
✅ Worker 1 responding
✅ Worker 2 responding
✅ Worker 3 responding
✅ All workers operational via edge function
```

### 7.2 Test Backend Connection
```bash
python -c "from supabase_client import supabase; print('✅ Backend connected to:', supabase.supabase_url)"
```

Expected: Should print your NEW Supabase URL.

### 7.3 Test Realtime Connection
```bash
python test_realtime.py
```

This creates a test job and listens for realtime updates.

**Expected Output:**
```
🔌 Connecting to Supabase Realtime...
✅ Subscribed to job updates
📝 Created test job: 12345678-1234-1234-1234-123456789abc
🔔 Received realtime update: {'status': 'pending', ...}
✅ Realtime working!
```

---

## 🔄 Step 8: Start Services

### 8.1 Stop Old Services (if running)
```bash
# Windows
taskkill /F /IM python.exe

# Linux/Mac
pkill -f "python app.py"
pkill -f "python job_worker_realtime.py"
```

### 8.2 Start Backend
```bash
cd backend
python app.py
```

**Look for these startup messages:**
```
[OK] Supabase client with auto-failover initialized
[OK] Main Supabase: https://xxxxxxxxxxxxx.supabase.co
🔌 Realtime Connection Manager initialized
✅ Realtime Connection Manager started
[QUOTA] Quota manager initialized
 * Running on http://127.0.0.1:5000
```

### 8.3 Start Job Worker (New Terminal)
```bash
cd backend
python job_worker_realtime.py
```

**Look for these startup messages:**
```
================================================================================
JOB WORKER STARTING (MULTI-ENDPOINT MODE)
================================================================================
Backend URL: http://localhost:5000
Supabase URL: https://xxxxxxxxxxxxx.supabase.co
Providers: Replicate (vision-nova, cinematic-nova)
           FAL AI (vision-atlas, vision-flux, cinematic-pro, cinematic-x)
================================================================================
🔌 Connecting to Supabase Realtime (shared connection)...
✅ Subscribed to ALL job updates (shared connection active)
[BACKLOG] Processing any pending jobs...
```

### 8.4 Start Frontend (New Terminal)
```bash
npm run dev
```

Expected:
```
VITE v5.x.x  ready in 500 ms

➜  Local:   http://localhost:5173/
➜  Network: use --host to expose
```

---

## ✅ Step 9: Verification Checklist

### 9.1 Database Verification
Go to Supabase Dashboard → **Table Editor**:
- [ ] 17 tables exist
- [ ] `jobs` table has correct schema
- [ ] Realtime enabled for `jobs` table

### 9.2 Edge Function Verification
```bash
# Should return worker assignments
curl -X POST https://xxxxxxxxxxxxx.supabase.co/functions/v1/route-queue \
  -H "Content-Type: application/json" \
  -H "apikey: YOUR_NEW_ANON_KEY" \
  -d '{"operation": "select", "table": "priority1_queue", "filters": {"limit": 1}}'
```

Expected: `{"success": true, "worker": "worker-1", ...}`

### 9.3 Backend API Verification
```bash
# Test health endpoint
curl http://localhost:5000/health

# Expected response
{"status": "ok", "database": "connected", "realtime": "active"}
```

### 9.4 Frontend Verification
Open browser: `http://localhost:5173`

1. **Test Auth:**
   - [ ] Sign up with email
   - [ ] Receive magic link
   - [ ] Login successful

2. **Test Generation:**
   - [ ] Create image generation job
   - [ ] See "pending" status immediately
   - [ ] Status updates in real-time (no page refresh)
   - [ ] Job completes successfully

3. **Test Realtime:**
   - [ ] Open browser console
   - [ ] Look for: `WebSocket connected to wss://xxxxxxxxxxxxx.supabase.co/realtime/v1`
   - [ ] No connection errors

---

## 🔄 Step 10: Migration from Old Account (Optional)

If you have **existing data in an old account** that you want to migrate:

### 10.1 Configure Dual-Account Sync

Update `backend/.env`:
```env
# Primary (NEW account - keep as is)
SUPABASE_URL=https://new-project.supabase.co
SUPABASE_ANON_KEY=new-anon-key
SUPABASE_SERVICE_ROLE_KEY=new-service-role-key

# Backup (OLD account for migration)
NEW_SUPABASE_URL=https://old-project.supabase.co
NEW_SUPABASE_ANON_KEY=old-anon-key
NEW_SUPABASE_SERVICE_ROLE_KEY=old-service-role-key

# Enable sync
ENABLE_STARTUP_SYNC=true
ENABLE_HOURLY_SYNC=true
```

### 10.2 Run Initial Sync
```bash
cd backend
python smart_hourly_sync.py
```

This copies all data from OLD → NEW account.

### 10.3 Set Up Automated Hourly Sync

**Windows (Task Scheduler):**
1. Task Scheduler → Create Basic Task
2. Trigger: Daily, repeat every 1 hour
3. Action: `python smart_hourly_sync.py`
4. Start in: `C:\path\to\backend`

**Linux (Cron):**
```bash
crontab -e
# Add: 0 * * * * cd /path/to/backend && python smart_hourly_sync.py
```

**See `SYNC_SYSTEM_GUIDE.md` for detailed migration instructions.**

---

## 🚨 Troubleshooting

### Issue: "Realtime not working"
**Symptoms:** Jobs don't update in real-time, need page refresh

**Solution:**
1. Check Supabase Dashboard → Database → Replication
2. Ensure `jobs` table is in `supabase_realtime` publication
3. Run: `ALTER PUBLICATION supabase_realtime ADD TABLE jobs;`
4. Restart backend services

### Issue: "Edge function returning 401"
**Symptoms:** Queue operations fail with authentication error

**Solution:**
1. Verify `EDGE_FUNCTION_URL` in `backend/.env` is correct
2. Check `SUPABASE_SERVICE_ROLE_KEY` matches NEW account
3. Re-deploy edge function: `supabase functions deploy route-queue --project-ref <REF>`

### Issue: "Workers not distributing"
**Symptoms:** All jobs go to worker-1, workers 2-3 inactive

**Solution:**
1. Workers auto-pause after 1 week inactivity
2. Run: `python worker_health.py`
3. This pings all workers to wake them up

### Issue: "Database schema mismatch"
**Symptoms:** Backend errors mentioning missing tables/columns

**Solution:**
1. Re-run `000_clean_schema_no_coins.sql` in SQL Editor
2. Verify all 17 tables exist
3. Check column names match expected schema

### Issue: "Frontend can't connect"
**Symptoms:** Network errors, CORS issues

**Solution:**
1. Verify `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` in root `.env`
2. Check browser console for actual error
3. Ensure backend is running on correct port (5000)

---

## 📊 Monitoring

### Check System Status
```bash
cd backend

# Check sync status (if migrating)
python sync_status.py

# Check job worker health
python worker_health.py

# View recent jobs
python -c "from supabase_client import supabase; print(supabase.table('jobs').select('*').limit(5).execute())"
```

### Supabase Dashboard Metrics
Monitor these in Dashboard → **Project Settings → Usage**:
- **Database Size:** Should start at ~0MB
- **API Requests:** Watch for rate limit (50k/month free tier)
- **Realtime Connections:** Should be 2-3 active connections

---

## 🎯 Success Criteria

Your setup is complete when:

- ✅ All 17 database tables exist
- ✅ Realtime enabled for `jobs` table
- ✅ Edge function deployed and responding
- ✅ Backend connects to NEW Supabase
- ✅ Frontend connects to NEW Supabase
- ✅ Job worker listens for realtime events
- ✅ Test job completes end-to-end
- ✅ Real-time updates working (no refresh needed)
- ✅ Queue operations distribute across 3 workers

**🎉 Congratulations! Your new Supabase account is fully operational!**

---

## 📚 Related Documentation

- **Edge Function Migration:** `backend/EDGE_FUNCTION_MIGRATION.md`
- **Sync System Guide:** `backend/SYNC_SYSTEM_GUIDE.md`
- **Realtime Setup:** `backend/migrations/003_enable_realtime.sql`
- **Complete Schema:** `backend/migrations/000_clean_schema_no_coins.sql`

---

## 🆘 Need Help?

If you encounter issues not covered in troubleshooting:

1. Check backend logs: `backend/backend_logs.txt`
2. Check worker logs: Look for errors in terminal running `job_worker_realtime.py`
3. Check Supabase Dashboard → Logs → Edge Functions
4. Check browser console for frontend errors

**Common Log Locations:**
- Backend: Terminal running `python app.py`
- Worker: Terminal running `python job_worker_realtime.py`
- Frontend: Browser DevTools → Console
- Edge Function: Supabase Dashboard → Edge Functions → Logs
