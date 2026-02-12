# New Supabase Account Migration Checklist

Quick reference checklist for migrating to a new Supabase account. Print this and check off items as you complete them.

---

## 📋 Pre-Migration Preparation

- [ ] **Read full guide:** `NEW_SUPABASE_ACCOUNT_SETUP_GUIDE.md`
- [ ] **Backup current `.env` files** (both root and `backend/.env`)
- [ ] **Note down current Supabase URL** for rollback if needed
- [ ] **Have Supabase CLI installed:** `npm install -g supabase`

---

## 🆕 Step 1: Create New Project (5 minutes)

- [ ] Go to [supabase.com](https://supabase.com) and sign in
- [ ] Click **"New Project"**
- [ ] Configure:
  - Name: `atool-new`
  - Region: Closest to users
  - Database password: **Save securely**
- [ ] Wait for project initialization (~2 minutes)
- [ ] Copy and save these values:

```env
NEW_SUPABASE_URL=https://_____________.supabase.co
NEW_SUPABASE_ANON_KEY=eyJ_________________________
NEW_SUPABASE_SERVICE_ROLE_KEY=eyJ_________________________
NEW_PROJECT_REF=_____________
```

---

## 🗄️ Step 2: Database Setup (5 minutes)

- [ ] Go to **SQL Editor** in Supabase Dashboard
- [ ] Copy entire `backend/migrations/000_clean_schema_no_coins.sql`
- [ ] Paste into SQL Editor and click **"Run"**
- [ ] Verify 17 tables created in **Table Editor**

**Expected Tables:**
```
✓ users             ✓ priority1_queue      ✓ ad_sessions
✓ jobs              ✓ priority2_queue      ✓ flagged_ips
✓ sessions          ✓ priority3_queue      ✓ sync_metadata
✓ magic_links       ✓ modal_deployments    ✓ provider_api_keys
✓ usage_logs        ✓ shared_results       ✓ deleted_api_keys
                    ✓ provider_trials      ✓ model_quotas
```

---

## 🔔 Step 3: Enable Realtime (3 minutes)

- [ ] Go to **Database → Replication** in Dashboard
- [ ] Find `supabase_realtime` publication
- [ ] Click **"Edit"**
- [ ] Enable replication for **`jobs`** table
- [ ] Check: **INSERT**, **UPDATE**, **DELETE** events
- [ ] Click **"Save"**

**Verify via SQL:**
```sql
SELECT tablename FROM pg_publication_tables WHERE pubname = 'supabase_realtime';
```
Expected: Should include `jobs`

---

## ⚡ Step 4: Deploy Edge Function (10 minutes)

- [ ] Open terminal and run: `supabase login`
- [ ] Navigate to functions: `cd backend/supabase/functions`
- [ ] Deploy function:
```bash
supabase functions deploy route-queue --project-ref <NEW_PROJECT_REF>
```
- [ ] Copy the function URL from output
- [ ] Set worker secrets (run all 6 commands):

```bash
supabase secrets set WORKER_1_URL=https://gmhpbeqvqpuoctaqgnum.supabase.co --project-ref <NEW_PROJECT_REF>
supabase secrets set WORKER_1_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImdtaHBiZXF2cXB1b2N0YXFnbnVtIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjM5ODI4NzAsImV4cCI6MjA3OTU1ODg3MH0.I-6DUCSsjtSIRij3pRdRw9Ws0IVtJQcMDnX92IGGheA --project-ref <NEW_PROJECT_REF>

supabase secrets set WORKER_2_URL=https://weuavzmjqyfjlfzcybtr.supabase.co --project-ref <NEW_PROJECT_REF>
supabase secrets set WORKER_2_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6IndldWF2em1qcXlmamxmemN5YnRyIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjM5NTA0NjgsImV4cCI6MjA3OTUyNjQ2OH0.Ypp-KOZNMichYXIw5qNbHWmdg-9rRUfloXhSqAkalUs --project-ref <NEW_PROJECT_REF>

supabase secrets set WORKER_3_URL=https://zqicjcipwhtovgzxjgjn.supabase.co --project-ref <NEW_PROJECT_REF>
supabase secrets set WORKER_3_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InpxaWNqY2lwd2h0b3ZnenhqZ2puIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjM5ODI5MDksImV4cCI6MjA4Mzg5NDUwMn0.qC6Q0pHJIyIu_IFqu8KYpMwlB8VSYusfw7UFDNjTjTU --project-ref <NEW_PROJECT_REF>
```

- [ ] Verify secrets: `supabase secrets list --project-ref <NEW_PROJECT_REF>`

Expected: 6 secrets (WORKER_1_URL, WORKER_1_ANON_KEY, WORKER_2_URL, WORKER_2_ANON_KEY, WORKER_3_URL, WORKER_3_ANON_KEY)

---

## ⚙️ Step 5: Configure Backend (3 minutes)

- [ ] Open `backend/.env`
- [ ] Update these values with NEW account credentials:

```env
SUPABASE_URL=https://_____________.supabase.co
SUPABASE_ANON_KEY=eyJ_________________________
SUPABASE_SERVICE_ROLE_KEY=eyJ_________________________
EDGE_FUNCTION_URL=https://_____________.supabase.co/functions/v1/route-queue
```

- [ ] If fresh account (no migration), set:
```env
ENABLE_STARTUP_SYNC=false
ENABLE_HOURLY_SYNC=false
```

- [ ] If migrating from old account, set:
```env
# Keep NEW account as primary (above)
# Add OLD account for sync:
NEW_SUPABASE_URL=https://old-project.supabase.co
NEW_SUPABASE_ANON_KEY=old-anon-key
NEW_SUPABASE_SERVICE_ROLE_KEY=old-service-role-key
ENABLE_STARTUP_SYNC=true
ENABLE_HOURLY_SYNC=true
```

---

## 🎨 Step 6: Configure Frontend (3 minutes)

- [ ] Open `.env` in project root
- [ ] Update primary credentials:

```env
VITE_SUPABASE_URL=https://_____________.supabase.co
VITE_SUPABASE_ANON_KEY=eyJ_________________________
```

- [ ] Add backup credentials for automatic failover:

```env
VITE_BACKUP_SUPABASE_URL=https://old-account.supabase.co
VITE_BACKUP_SUPABASE_ANON_KEY=eyJ_________________________
```

- [ ] Open `src/App.jsx`
- [ ] Add import: `import FailoverMonitor from './components/FailoverMonitor';`
- [ ] Add component: `<FailoverMonitor />` inside App component

**⚠️ Important:** 
- Primary URL/key must match backend
- Backup URL/key enables automatic frontend failover

**📝 Note:** If deploying via `deploy.ps1`, the script will prompt for these values automatically.

---

## 🧪 Step 7: Testing (5 minutes)

- [ ] Test workers: `cd backend && python test_workers.py`
  - Expected: ✅ All 3 workers responding

- [ ] Test backend connection:
```bash
python -c "from supabase_client import supabase; print('✅ Connected:', supabase.supabase_url)"
```
  - Expected: Should print NEW Supabase URL

- [ ] Test realtime: `python test_realtime.py`
  - Expected: ✅ Realtime working!

---

## 🚀 Step 8: Start Services (3 minutes)

### Stop old services:
```bash
# Windows
taskkill /F /IM python.exe

# Linux/Mac
pkill -f "python app.py"
pkill -f "python job_worker_realtime.py"
```

### Start new services:

**Terminal 1 - Backend:**
```bash
cd backend
python app.py
```
- [ ] Look for: `✅ Realtime Connection Manager started`
- [ ] Look for: `Running on http://127.0.0.1:5000`

**Terminal 2 - Job Worker:**
```bash
cd backend
python job_worker_realtime.py
```
- [ ] Look for: `✅ Subscribed to ALL job updates`

**Terminal 3 - Frontend:**
```bash
npm run dev
```
- [ ] Look for: `Local: http://localhost:5173/`

---

## ✅ Step 9: End-to-End Verification (5 minutes)

### Database Check:
- [ ] Go to Supabase Dashboard → **Table Editor**
- [ ] Verify all 17 tables exist
- [ ] Go to **Database → Replication**
- [ ] Verify `jobs` table has realtime enabled

### Backend Check:
- [ ] Open: `http://localhost:5000/health`
- [ ] Expected: `{"status": "ok", "database": "connected", "realtime": "active"}`

### Frontend Check:
- [ ] Open: `http://localhost:5173`
- [ ] Sign up with test email
- [ ] Check email for magic link
- [ ] Login successfully
- [ ] Create test image generation
- [ ] Watch status update in real-time (no page refresh)
- [ ] Open browser console (F12)
- [ ] Look for: `WebSocket connected to wss://...supabase.co/realtime/v1`

---

## 🔄 Step 10: Data Migration (Optional, if migrating)

**Only if you have existing data in old account:**

- [ ] Configure dual-account sync (Step 5 alternative)
- [ ] Run initial sync: `python smart_hourly_sync.py`
- [ ] Set up automated hourly sync (see `SYNC_SYSTEM_GUIDE.md`)
- [ ] Monitor sync: `python sync_status.py`

---

## ✨ Final Verification

Your migration is complete when ALL these are true:

- ✅ Backend starts without errors
- ✅ Job worker connects to Supabase realtime
- ✅ Frontend loads without console errors
- ✅ Can create user account
- ✅ Can generate images/videos
- ✅ Status updates in real-time
- ✅ Workers distribute load (check logs for worker-1, worker-2, worker-3)
- ✅ No 401/403/429 errors

---

## 🚨 Troubleshooting Quick Reference

### Realtime not working?
```bash
# In SQL Editor:
ALTER PUBLICATION supabase_realtime ADD TABLE jobs;
GRANT SELECT ON jobs TO anon;
GRANT SELECT ON jobs TO authenticated;
```

### Edge function 401 error?
- Check `EDGE_FUNCTION_URL` in `backend/.env` matches deployed function
- Verify `SUPABASE_SERVICE_ROLE_KEY` is correct
- Re-deploy: `supabase functions deploy route-queue --project-ref <REF>`

### Workers not responding?
```bash
cd backend
python worker_health.py
```

### Database connection error?
- Double-check `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`
- Ensure database password is correct
- Check Supabase Dashboard for service status

---

## 📊 Post-Migration Monitoring

**Daily Checks (Week 1):**
- [ ] Check Supabase Usage: Dashboard → **Project Settings → Usage**
- [ ] Monitor database size (should grow slowly)
- [ ] Monitor API requests (should be under 50k/month)
- [ ] Check backend logs for errors

**Weekly Checks:**
- [ ] Run `python worker_health.py` to keep workers active
- [ ] Review job completion rate
- [ ] Check for failed jobs: `SELECT * FROM jobs WHERE status = 'failed'`

---

## 🔙 Rollback Plan (Emergency)

If something goes wrong, revert to old account:

1. **Stop services:**
```bash
pkill -f "python app.py"
pkill -f "python job_worker_realtime.py"
```

2. **Restore `backend/.env`:**
```env
# Use OLD account credentials
SUPABASE_URL=https://old-project.supabase.co
SUPABASE_ANON_KEY=old-anon-key
SUPABASE_SERVICE_ROLE_KEY=old-service-role-key
EDGE_FUNCTION_URL=https://old-project.supabase.co/functions/v1/route-queue
```

3. **Restore root `.env`:**
```env
VITE_SUPABASE_URL=https://old-project.supabase.co
VITE_SUPABASE_ANON_KEY=old-anon-key
```

4. **Restart services:**
```bash
cd backend
python app.py &
python job_worker_realtime.py &
```

---

## 📚 Reference Links

- **Full Guide:** `NEW_SUPABASE_ACCOUNT_SETUP_GUIDE.md`
- **Edge Function Guide:** `EDGE_FUNCTION_MIGRATION.md`
- **Sync Guide:** `SYNC_SYSTEM_GUIDE.md`
- **Schema SQL:** `migrations/000_clean_schema_no_coins.sql`
- **Realtime SQL:** `migrations/003_enable_realtime.sql`

---

**🎉 Migration Complete!**

Total estimated time: **30-45 minutes**

Print this checklist and work through it step-by-step. Check off items as you go.
