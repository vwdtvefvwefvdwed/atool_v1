# Edge Function Migration Guide

## Overview
When migrating from OLD Supabase account to NEW account, the edge function must be redeployed to the NEW account. The edge function is stateless and routes queue operations to the 3 worker accounts via round-robin.

---

## Prerequisites

1. **Supabase CLI installed**
   ```bash
   npm install -g supabase
   ```

2. **Login to Supabase**
   ```bash
   supabase login
   ```

3. **Get NEW account project reference**
   - Go to NEW Supabase project dashboard
   - Settings → General → Reference ID
   - Example: `abcdefghijklmnop`

---

## Migration Steps

### Step 1: Deploy Edge Function to NEW Account

```bash
cd backend/supabase/functions
supabase functions deploy route-queue --project-ref <NEW_PROJECT_REF>
```

**Expected Output:**
```
Deploying function route-queue...
Function route-queue deployed successfully
Function URL: https://<NEW_PROJECT_REF>.supabase.co/functions/v1/route-queue
```

---

### Step 2: Set Worker Secrets on NEW Account

The edge function needs worker credentials as environment variables. Set them using Supabase CLI:

```bash
# Worker 1 secrets
supabase secrets set WORKER_1_URL=https://gmhpbeqvqpuoctaqgnum.supabase.co --project-ref <NEW_PROJECT_REF>
supabase secrets set WORKER_1_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImdtaHBiZXF2cXB1b2N0YXFnbnVtIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjM5ODI4NzAsImV4cCI6MjA3OTU1ODg3MH0.I-6DUCSsjtSIRij3pRdRw9Ws0IVtJQcMDnX92IGGheA --project-ref <NEW_PROJECT_REF>

# Worker 2 secrets
supabase secrets set WORKER_2_URL=https://weuavzmjqyfjlfzcybtr.supabase.co --project-ref <NEW_PROJECT_REF>
supabase secrets set WORKER_2_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6IndldWF2em1qcXlmamxmemN5YnRyIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjM5NTA0NjgsImV4cCI6MjA3OTUyNjQ2OH0.Ypp-KOZNMichYXIw5qNbHWmdg-9rRUfloXhSqAkalUs --project-ref <NEW_PROJECT_REF>

# Worker 3 secrets
supabase secrets set WORKER_3_URL=https://zqicjcipwhtovgzxjgjn.supabase.co --project-ref <NEW_PROJECT_REF>
supabase secrets set WORKER_3_ANON_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InpxaWNqY2lwd2h0b3ZnenhqZ2puIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjM5ODI5MDksImV4cCI6MjA3OTU1ODkwOX0.qC6Q0pHJIyIu_IFqu8KYpMwlB8VSYusfw7UFDNjTjTU --project-ref <NEW_PROJECT_REF>
```

**Verify secrets were set:**
```bash
supabase secrets list --project-ref <NEW_PROJECT_REF>
```

---

### Step 3: Update Backend Configuration

Update `backend/.env` to point to NEW edge function:

```env
# OLD (comment out):
# EDGE_FUNCTION_URL=https://gtgnwrwbcxvasgetfzby.supabase.co/functions/v1/route-queue

# NEW:
EDGE_FUNCTION_URL=https://<NEW_PROJECT_REF>.supabase.co/functions/v1/route-queue
```

---

### Step 4: Test Edge Function

Run the worker test script to verify edge function works:

```bash
cd backend
python test_workers.py
```

**Expected Output:**
```
Testing Edge Function Round-Robin Distribution
Worker used: worker-1
Worker used: worker-2
Worker used: worker-3
✅ All workers operational via edge function
```

---

### Step 5: Restart Application Services

Restart Flask backend and job workers to use new edge function URL:

```bash
# Stop existing services
pkill -f "python app.py"
pkill -f "python job_worker_realtime.py"

# Start services
python app.py &
python job_worker_realtime.py &
```

---

## Important Notes

### Worker Accounts Don't Change
- The 3 worker accounts (`gmhpbeqvqpuoctaqgnum`, `weuavzmjqyfjlfzcybtr`, `zqicjcipwhtovgzxjgjn`) remain the same
- They still store queue tables (priority1_queue, priority2_queue, priority3_queue)
- No changes needed on worker accounts

### Edge Function Is Stateless
- No data stored in edge function
- Just routes traffic to workers via round-robin
- Safe to redeploy without data loss

### Migration Timeline
- Deploy edge function **after** syncing data to NEW account
- Deploy **before** switching SUPABASE_URL in `.env`
- Test thoroughly before production switchover

---

## Troubleshooting

### Error: "Function not found"
- Verify you're deploying to correct project: `supabase projects list`
- Check project reference matches NEW account

### Error: "Worker connection failed"
- Verify secrets were set: `supabase secrets list --project-ref <NEW_PROJECT_REF>`
- Check worker URLs/keys are correct

### Error: "401 Unauthorized"
- Verify SUPABASE_SERVICE_ROLE_KEY in `.env` matches NEW account
- Check edge function URL is correct

### Round-robin not distributing evenly
- Workers 2 and 3 might be paused due to inactivity
- Run `python worker_health.py` to ping them
- Check Supabase dashboard for worker status

---

## Verification Checklist

- [ ] Edge function deployed to NEW account
- [ ] All 6 worker secrets set (3 URLs + 3 keys)
- [ ] `.env` updated with new EDGE_FUNCTION_URL
- [ ] `test_workers.py` passes all tests
- [ ] Backend services restarted
- [ ] Job creation working (creates jobs in main DB)
- [ ] Queue operations distributing across workers

---

## Rollback Plan

If edge function migration fails:

1. **Revert `.env`:**
   ```env
   EDGE_FUNCTION_URL=https://gtgnwrwbcxvasgetfzby.supabase.co/functions/v1/route-queue
   ```

2. **Restart services:**
   ```bash
   pkill -f "python app.py"
   pkill -f "python job_worker_realtime.py"
   python app.py &
   python job_worker_realtime.py &
   ```

3. **System reverts to OLD account edge function**

---

## Additional Resources

- Edge function source: `backend/supabase/functions/route-queue/index.ts`
- Worker client: `backend/worker_client.py`
- Worker health check: `backend/worker_health.py`
- Sync system guide: `backend/SYNC_SYSTEM_GUIDE.md`
