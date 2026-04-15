"""
Job Worker with Supabase Realtime WebSocket
Routes to Replicate or FAL AI based on provider
"""

import os
import sys
import time
import base64
import asyncio
import threading
import requests
import logging
from datetime import datetime
from typing import Optional
from flask import Flask, jsonify
from dotenv_vault import load_dotenv
from postgrest.exceptions import APIError
from multi_endpoint_manager import generate, get_endpoint_type
from provider_api_keys import get_api_key_for_job, increment_usage_count, get_worker1_client, map_model_to_provider, get_all_api_keys_for_provider
from api_key_rotation import handle_api_key_rotation, handle_roundrobin_rotation, log_rotation_attempt
from provider_constants import NO_DELETE_ROTATE_PROVIDERS
from error_notifier import notify_error, ErrorType
from model_quota_manager import ensure_quota_manager_started, get_quota_manager
from cloudinary_manager import get_cloudinary_manager
from job_coordinator import get_job_coordinator

if sys.platform == "win32":
    try:
        import codecs
        sys.stdout = codecs.getwriter("utf-8")(sys.stdout.detach())
        sys.stderr = codecs.getwriter("utf-8")(sys.stderr.detach())
    except Exception:
        pass

logging.getLogger('websockets').setLevel(logging.CRITICAL)
logging.getLogger('websockets.protocol').setLevel(logging.CRITICAL)
logging.getLogger('realtime').setLevel(logging.WARNING)
logging.getLogger('root').setLevel(logging.WARNING)

load_dotenv()

# Flask app for health checks (Koyeb requirement)
app = Flask(__name__)

# Global worker status tracker
worker_status = {
    "running": False,
    "ready": False,
    "startup_complete": False,
    "jobs_processed": 0,
    "started_at": None,
    "last_heartbeat": None,
    "backlog_processed": False
}

BACKEND_URL = os.getenv("WORKER_BACKEND_URL") or os.getenv("BACKEND_URL", "http://localhost:5000")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
VERIFY_SSL = os.getenv("VERIFY_SSL", "False").lower() == "true"

# PostgreSQL connection for LISTEN/NOTIFY (more stable than Realtime WebSocket on Render)
# Format: postgresql://postgres.[REF].[REF]:[PASSWORD]@db.[REF].supabase.co:5432/postgres?sslmode=require
# IMPORTANT: Must use port 5432 (Session mode), NOT 6543 (Transaction mode)
# pgbouncer in transaction mode (6543) does NOT support LISTEN/NOTIFY
DATABASE_URL = os.getenv("DATABASE_URL")

# Health check endpoint for Koyeb
@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint - returns 200 immediately so Koyeb knows worker is running"""
    return jsonify({
        "status": "healthy",
        "worker": "running",
        "ready": worker_status["ready"],
        "startup_complete": worker_status["startup_complete"],
        "backlog_processed": worker_status["backlog_processed"],
        "jobs_processed": worker_status["jobs_processed"],
        "started_at": worker_status["started_at"],
        "last_heartbeat": worker_status["last_heartbeat"]
    }), 200

@app.route("/", methods=["GET"])
def index():
    """Root endpoint"""
    return jsonify({
        "service": "Atool Job Worker",
        "status": "running",
        "health_check": "/health"
    }), 200

print("=" * 60)
print("JOB WORKER STARTING (MULTI-ENDPOINT MODE)")
print("=" * 60)
print(f"Backend URL: {BACKEND_URL}")
print(f"Supabase URL: {SUPABASE_URL}")
print("Providers: Replicate (vision-nova, cinematic-nova)")
print("           FAL AI (vision-atlas, vision-flux, cinematic-pro, cinematic-x)")
print("=" * 60)
print()
sys.stdout.flush()

# Ping worker accounts to prevent auto-pause
from worker_health import ping_all_workers_async
ping_all_workers_async()

# Initialize quota manager
ensure_quota_manager_started()
print("[QUOTA] Quota manager initialized")

MODELS_REQUIRING_INPUT_IMAGE = [
    'nano-banana-pro-leonardo',
    'AP123/IllusionDiffusion',
    'ultra-fast-nano',
    'ultra-fast-nano-banana-2',
    'black-forest-labs/flux-kontext-pro',
    'topazlabs/image-upscale',
    'sczhou/CodeFormer',
    'finegrain/finegrain-image-enhancer',
    'tencentarc/gfpgan',
    'picsart-ultra-upscale',
    'picsart-upscale',
    'clipdrop-upscale',
    'clipdrop-expand',
    'remove-bg',
    'bria_gen_fill',
    'bria_erase',
    'bria_remove_background',
    'bria_replace_background',
    'bria_blur_background',
    'bria_erase_foreground',
    'bria_expand',
    'bria_enhance',
    'stability-upscale-fast',
    'gemini-25-flash-aicc',
]

MODELS_REQUIRING_INPUT_VIDEO = [
    'luma/reframe-video',
]

MODELS_REQUIRING_INPUT_IMAGE_FOR_VIDEO = [
    'minimax/video-01',
    'kling-2.6',
    'motion-2.0',
    'motion-2.0-fast',
    'hailuo-2.3-fast',
    'wan22-i2v-plus-aicc',
]

# Priority lock - when True, only Priority 1 jobs are processed
_priority_lock_active = False

# Global semaphore: limits the total number of concurrent job processing threads.
# Prevents unbounded thread creation under high load.
_job_thread_semaphore = threading.BoundedSemaphore(40)

def _load_priority_lock_from_db():
    """Load priority_lock flag from Supabase on startup"""
    global _priority_lock_active
    try:
        from supabase_client import supabase
        result = supabase.table("system_flags").select("value").eq("key", "priority_lock").single().execute()
        if result.data:
            _priority_lock_active = result.data.get("value", False)
            print(f"[PRIORITY LOCK] Loaded from DB: {'ACTIVE - only P1 jobs will run' if _priority_lock_active else 'inactive'}")
    except Exception as e:
        print(f"[PRIORITY LOCK] Could not load flag from DB (defaulting to inactive): {e}")

# Provider-level concurrency control
provider_active_jobs = {}
provider_job_queues = {}
provider_locks = {}

def get_provider_lock(provider_key):
    """Get or create a lock for a provider"""
    if provider_key not in provider_locks:
        provider_locks[provider_key] = threading.Lock()
    return provider_locks[provider_key]

def is_provider_busy(provider_key):
    """Check if provider is currently processing a job"""
    return provider_key in provider_active_jobs and provider_active_jobs[provider_key] is not None

def mark_provider_busy(provider_key, job_id):
    """Mark provider as busy with a job"""
    provider_active_jobs[provider_key] = job_id
    print(f"[CONCURRENCY] Provider {provider_key} now BUSY with job {job_id}")

def mark_provider_free(provider_key, job_id):
    """Mark provider as free and process next queued job"""
    lock = get_provider_lock(provider_key)
    freed = False
    with lock:
        if provider_active_jobs.get(provider_key) == job_id:
            provider_active_jobs[provider_key] = None
            freed = True
            print(f"[CONCURRENCY] Provider {provider_key} now FREE")
        else:
            print(f"[CONCURRENCY] Warning: Job {job_id} tried to free {provider_key} but it's not the active job")
    if freed:
        process_next_queued_job(provider_key)

def enqueue_job(provider_key, job):
    """Add job to provider's queue. MUST be called while holding the provider lock."""
    if provider_key not in provider_job_queues:
        provider_job_queues[provider_key] = []

    job_id = job.get("job_id") or job.get("id")

    already_queued = any(
        (j.get("job_id") or j.get("id")) == job_id
        for j in provider_job_queues[provider_key]
    )
    if already_queued:
        print(f"[QUEUE] Job {job_id} already in queue for {provider_key} — skipping duplicate")
        return

    provider_job_queues[provider_key].append(job)
    queue_length = len(provider_job_queues[provider_key])
    print(f"[QUEUE] Job {job_id} queued for provider {provider_key} (queue length: {queue_length})")

def process_next_queued_job(provider_key):
    """Process the next job in provider's queue if any"""
    lock = get_provider_lock(provider_key)
    
    with lock:
        if provider_key not in provider_job_queues or len(provider_job_queues[provider_key]) == 0:
            print(f"[QUEUE] No queued jobs for provider {provider_key}")
            return
        
        if is_provider_busy(provider_key):
            print(f"[QUEUE] Provider {provider_key} still busy, not processing next job")
            return
        
        next_job = provider_job_queues[provider_key].pop(0)
        job_id = next_job.get("job_id") or next_job.get("id")
        queue_remaining = len(provider_job_queues[provider_key])
        
        print(f"[QUEUE] Processing next queued job {job_id} for {provider_key} ({queue_remaining} remaining)")
        
        job_thread = threading.Thread(
            target=process_job_with_concurrency_control,
            args=(next_job,),
            daemon=True
        )
        job_thread.start()


def compress_image_to_size(image_data: bytes, max_size_mb: float = 8) -> bytes:
    """
    Compress image to fit within max_size_mb.
    Uses quality reduction and dimension scaling if needed.
    
    Args:
        image_data: Original image bytes
        max_size_mb: Target maximum size in MB
        
    Returns:
        Compressed image bytes
    """
    from PIL import Image
    import io
    
    max_size_bytes = int(max_size_mb * 1024 * 1024)
    
    try:
        img = Image.open(io.BytesIO(image_data))
        original_format = img.format or "JPEG"
        original_size = len(image_data)
        
        print(f"[COMPRESS] Original: {img.size}, Format: {original_format}, Size: {original_size / 1024 / 1024:.2f} MB")
        
        quality = 85
        scale = 1.0
        
        while True:
            output = io.BytesIO()
            
            if scale < 1.0:
                new_width = int(img.width * scale)
                new_height = int(img.height * scale)
                resized_img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                print(f"[COMPRESS] Scaled to {resized_img.size}")
            else:
                resized_img = img
            
            resized_img.save(output, format=original_format, quality=quality, optimize=True)
            compressed_data = output.getvalue()
            compressed_size = len(compressed_data)
            
            if compressed_size <= max_size_bytes:
                print(f"[COMPRESS] Success! Quality: {quality}, Size: {compressed_size / 1024 / 1024:.2f} MB")
                return compressed_data
            
            if quality > 20:
                quality -= 5
                print(f"[COMPRESS] Trying quality: {quality}")
            elif scale > 0.5:
                scale -= 0.1
                print(f"[COMPRESS] Trying scale: {scale:.1f}")
            else:
                print(f"[COMPRESS] Cannot reduce further, returning best effort ({compressed_size / 1024 / 1024:.2f} MB)")
                return compressed_data
                
    except Exception as e:
        print(f"[COMPRESS] Error: {str(e)}, returning original data")
        return image_data


def mark_job_failed(job_id, error_message):
    """
    Mark a job as failed in the database (not retryable).
    Used for validation errors, user input errors, etc.
    """
    if not job_id:
        print(f"[FAIL] Cannot fail job: job_id is missing")
        return False
    
    print(f"[FAIL] Marking job {job_id} as failed...")
    
    try:
        payload = {
            "error": error_message or "Unknown error"
        }
        
        response = requests.post(
            f"{BACKEND_URL}/worker/job/{job_id}/fail",
            json=payload,
            timeout=10,
            verify=VERIFY_SSL
        )
        
        if response.status_code == 200:
            print(f"[FAIL] Job {job_id} successfully marked as failed")
            return True
        else:
            error_text = response.text[:200] if response.text else "No response body"
            print(f"[FAIL] Failed to mark job {job_id} as failed: {response.status_code} - {error_text}")
            return False
    except (requests.exceptions.Timeout, requests.exceptions.RequestException) as req_error:
        print(f"[FAIL] HTTP call failed ({req_error}) — falling back to direct DB update")
        try:
            from supabase_client import supabase as _sb
            from datetime import datetime as _dt
            _sb.table("jobs").update({
                "status": "failed",
                "error_message": error_message or "Unknown error",
                "completed_at": _dt.utcnow().isoformat()
            }).eq("job_id", job_id).execute()
            print(f"[FAIL] Direct DB fallback succeeded for job {job_id}")
            return True
        except Exception as db_err:
            print(f"[FAIL] Direct DB fallback also failed: {db_err}")
            return False
    except Exception as e:
        print(f"[FAIL] Exception while marking job {job_id} as failed: {e}")
        return False


MAX_PENDING_RETRIES = 2
MAX_PENDING_RETRIES_AICC = 5  # vision-aicc and cinematic-aicc get more retries
_AICC_PROVIDERS = ("vision-aicc", "cinematic-aicc")


_NO_API_KEY_MARKERS = (
    "no api key",
    "no api key available",
    "api key rotation failed",
    "invalid key",
    "authentication",
    "unauthorized",
)

def _is_key_error(error_message: str) -> bool:
    """Return True if the error is an API-key availability problem (not a transient network error)."""
    msg = (error_message or "").lower()
    return any(marker in msg for marker in _NO_API_KEY_MARKERS)

def _is_quota_error(error_message: str) -> bool:
    """Return True if the error is a quota/credit exhaustion problem.
    Quota-exceeded jobs must NOT be retried with a 30s deferred loop — they should
    wait for the periodic retry sweep (10 min) which fires after the quota has had
    time to reset.  Immediate retries would burn through pending_retry_count in
    ~2.5 minutes (5 × 30s) and permanently fail the job before the quota resets.
    """
    return "quota_exceeded" in (error_message or "").lower()


def reset_job_to_pending(job_id, provider_key, error_message):
    """
    Mark a job as pending in the database so it can be retried.
    After MAX_PENDING_RETRIES attempts, marks the job as permanently failed
    to prevent infinite pending loops caused by persistent API errors.

    For non-key errors (network, timeout, Cloudinary) a deferred retry thread is
    spawned so the job is re-processed within ~30 s instead of waiting up to 10 min
    for the periodic retry sweep.

    For key-related errors the job stays pending silently — it will only be picked
    up when a matching API key is inserted (api_key_realtime_listener).
    """
    if not job_id:
        print(f"[RESET] Cannot reset job: job_id is missing")
        return False

    _retry_count_ok = False
    _max_retries = MAX_PENDING_RETRIES_AICC if provider_key in _AICC_PROVIDERS else MAX_PENDING_RETRIES
    try:
        # The jobs table lives in the MAIN Supabase DB — use supabase_client.supabase,
        # not get_worker1_client() which points to the Worker1 DB (different database).
        # Using the wrong client causes every .table("jobs") query to silently fail or
        # return empty results, meaning pending_retry_count is never read or incremented
        # and MAX_PENDING_RETRIES is never enforced — allowing infinite retry loops.
        from supabase_client import supabase as _main_sb
        job_resp = _main_sb.table("jobs").select("metadata").eq("job_id", job_id).execute()
        if job_resp.data:
            meta = job_resp.data[0].get("metadata") or {}
            retry_count = meta.get("pending_retry_count", 0)
            if retry_count >= _max_retries:
                print(f"[RESET] Job {job_id} has reached max retries ({_max_retries}) - marking as FAILED")
                mark_job_failed(
                    job_id,
                    f"Job failed after {_max_retries} retry attempts. Last error: {error_message}"
                )
                return False
            meta["pending_retry_count"] = retry_count + 1
            import datetime as _dt
            meta["retry_after"] = (_dt.datetime.utcnow() + _dt.timedelta(seconds=30)).isoformat()
            _main_sb.table("jobs").update({"metadata": meta}).eq("job_id", job_id).execute()
            print(f"[RESET] Job {job_id} pending retry {retry_count + 1}/{_max_retries}")
            _retry_count_ok = True
    except Exception as count_err:
        print(f"[RESET] Warning: could not check/update retry count for job {job_id}: {count_err}")

    # If the count read/write failed, still enforce the cap via a direct metadata check
    # so a persistent DB issue cannot allow infinite retries.
    if not _retry_count_ok:
        try:
            from supabase_client import supabase as _main_sb2
            _r = _main_sb2.table("jobs").select("metadata").eq("job_id", job_id).execute()
            if _r and _r.data:
                _meta = _r.data[0].get("metadata") or {}
                if _meta.get("pending_retry_count", 0) >= _max_retries:
                    print(f"[RESET] Job {job_id} retry cap hit on fallback check — marking FAILED")
                    mark_job_failed(
                        job_id,
                        f"Job failed after {_max_retries} retry attempts. Last error: {error_message}"
                    )
                    return False
        except Exception:
            pass

    print(f"[RESET] Marking job {job_id} as pending...")
    
    try:
        _store_provider = provider_key if provider_key not in (None, "unknown", "vision-nova", "cinematic-nova") else None
        payload = {
            "message": error_message or "Unknown error",
            "provider_key": _store_provider or "unknown"
        }
        
        response = requests.post(
            f"{BACKEND_URL}/worker/job/{job_id}/reset",
            json=payload,
            timeout=10,
            verify=VERIFY_SSL
        )
        
        if response.status_code == 200:
            print(f"[RESET] Job {job_id} successfully marked as pending")

            if _is_key_error(error_message):
                print(f"[RESET] Key-related error — job {job_id} will wait for API key insertion")
            elif _is_quota_error(error_message):
                # Quota-exceeded: do NOT spawn a 30s deferred retry — the quota won't
                # reset in 30s.  The periodic retry sweep (retry_transient_errors, every
                # 10 min) will pick it up after the quota has had time to reset.
                print(f"[RESET] Quota error — job {job_id} will wait for periodic sweep (quota reset)")
            else:
                # Non-key, non-quota transient error (network, timeout, Cloudinary):
                # schedule a short-delay retry so the job is re-processed in ~30 s
                # rather than waiting up to 10 min for the periodic retry sweep.
                def _deferred_retry(jid=job_id):
                    time.sleep(30)
                    try:
                        from supabase_client import supabase as _sb
                        row = _sb.table("jobs").select("*").eq("job_id", jid).single().execute()
                        if row.data and row.data.get("status") == "pending":
                            print(f"[RESET-RETRY] Re-triggering job {jid} after 30s delay")
                            process_job_with_concurrency_control(row.data)
                        else:
                            print(f"[RESET-RETRY] Job {jid} no longer pending — skipping deferred retry")
                    except Exception as _e:
                        print(f"[RESET-RETRY] Deferred retry failed for {jid}: {_e}")

                threading.Thread(target=_deferred_retry, daemon=True).start()

            return True
        else:
            error_text = response.text[:200] if response.text else "No response body"
            print(f"[RESET] Failed to reset job {job_id}: {response.status_code} - {error_text}")
            return False
            
    except (requests.exceptions.Timeout, requests.exceptions.RequestException) as req_error:
        print(f"[RESET] HTTP call failed ({req_error}) — falling back to direct DB update")
        try:
            from supabase_client import supabase as _sb
            _sb.table("jobs").update({
                "status": "pending",
                "error_message": error_message or "Unknown error",
                "progress": 0
            }).eq("job_id", job_id).execute()
            print(f"[RESET] Direct DB fallback succeeded for job {job_id}")
            if not _is_key_error(error_message) and not _is_quota_error(error_message):
                def _deferred_retry_fb(jid=job_id):
                    time.sleep(30)
                    try:
                        row = _sb.table("jobs").select("*").eq("job_id", jid).single().execute()
                        if row.data and row.data.get("status") == "pending":
                            process_job_with_concurrency_control(row.data)
                    except Exception as _e:
                        print(f"[RESET-RETRY-FB] Deferred retry failed for {jid}: {_e}")
                threading.Thread(target=_deferred_retry_fb, daemon=True).start()
            return True
        except Exception as db_err:
            print(f"[RESET] Direct DB fallback also failed: {db_err}")
            return False
    except Exception as reset_error:
        print(f"[ERROR] Unexpected error while resetting job {job_id}: {reset_error}")
        import traceback
        traceback.print_exc()
        return False


def _extract_valid_url(value) -> Optional[str]:
    """
    Extract the first valid HTTP URL from a checkpoint output value.
    - If value is a URL string  → return it directly.
    - If value is a dict        → return the first string value that starts with 'http'.
    - Otherwise                 → return None.
    """
    if isinstance(value, str) and value.startswith("http"):
        return value
    if isinstance(value, dict):
        for v in value.values():
            if isinstance(v, str) and v.startswith("http"):
                return v
    return None


def validate_job_inputs(job) -> bool:
    """
    Validate that a job has all required inputs before dispatching it.

    Image / video jobs
    ------------------
    Checks model-specific required input image / video URLs.

    Workflow pending
    ----------------
    The original user-uploaded image (jobs.image_url or metadata.input_image_url)
    must be a valid HTTP URL.

    Workflow pending_retry (step N fails, resume from step N)
    ---------------------------------------------------------
    The INPUT for step N is the LAST OUTPUT from step N-1.
    - step 0 : input is the original user image  → checkpoints['_input'] / jobs.image_url
    - step N>0: input is the generated image     → checkpoints[N-1]['output'] (dict or URL)

    In both cases the value must resolve to a real HTTP URL — a non-None dict
    with all-None URL fields is treated the same as missing.

    Marks the job as FAILED immediately when inputs are missing and returns False.
    Returns True when the job is safe to submit.
    """
    job_id = job.get("job_id") or job.get("id")
    job_type = job.get("job_type", "image")
    model = job.get("model", "")
    metadata = job.get("metadata", {}) or {}

    # ── Image / Video jobs ────────────────────────────────────────────────────
    if job_type in ("image", "video"):
        input_image_url = metadata.get("input_image_url") or job.get("image_url")
        input_video_url = metadata.get("video_url") or metadata.get("input_image_url")

        if model in MODELS_REQUIRING_INPUT_IMAGE and not input_image_url:
            msg = ("⚠️ This tool requires an input image. "
                   "Please upload a reference image and try again.")
            print(f"[VALIDATE] Job {job_id} missing required input image for model '{model}'")
            mark_job_failed(job_id, msg)
            return False

        if model in MODELS_REQUIRING_INPUT_VIDEO and not input_video_url:
            msg = ("⚠️ This tool requires an input video. "
                   "Please upload a video and try again.")
            print(f"[VALIDATE] Job {job_id} missing required input video for model '{model}'")
            mark_job_failed(job_id, msg)
            return False

        if model in MODELS_REQUIRING_INPUT_IMAGE_FOR_VIDEO and not input_image_url:
            msg = ("⚠️ This video tool requires an input image. "
                   "Please upload a reference image and try again.")
            print(f"[VALIDATE] Job {job_id} missing required input image for video model '{model}'")
            mark_job_failed(job_id, msg)
            return False

        return True

    # ── Workflow jobs ─────────────────────────────────────────────────────────
    if job_type == "workflow":
        status = job.get("status", "pending")

        if status == "pending":
            raw = job.get("image_url") or metadata.get("input_image_url")
            if not _extract_valid_url(raw):
                msg = ("⚠️ No input image was found for this workflow. "
                       "Please try again with a new image.")
                print(f"[VALIDATE] Workflow job {job_id} missing original input image")
                mark_job_failed(job_id, msg)
                return False
            return True

        if status == "pending_retry":
            try:
                from supabase_client import supabase

                exec_resp = supabase.table("workflow_executions")\
                    .select("current_step, checkpoints")\
                    .eq("job_id", job_id)\
                    .single()\
                    .execute()

                if not exec_resp.data:
                    msg = ("⚠️ Workflow execution record not found. "
                           "Please try submitting again.")
                    print(f"[VALIDATE] Workflow job {job_id} has no execution record")
                    mark_job_failed(job_id, msg)
                    return False

                current_step = exec_resp.data.get("current_step", 0)
                checkpoints = exec_resp.data.get("checkpoints", {}) or {}

                if current_step == 0:
                    # Step 0 input = original user-uploaded image
                    raw = (
                        checkpoints.get("_input") or
                        job.get("image_url") or
                        metadata.get("input_image_url")
                    )
                    input_url = _extract_valid_url(raw)
                    if not input_url:
                        msg = ("⚠️ The original input image for this workflow could not be "
                               "found. Please try again with a new image.")
                        print(f"[VALIDATE] Workflow job {job_id}: missing/invalid original "
                              f"input image at step 0 resume (got: {raw!r})")
                        mark_job_failed(job_id, msg)
                        return False

                else:
                    # Step N>0 input = LAST OUTPUT from step N-1 (the generated image)
                    prev_checkpoint = checkpoints.get(str(current_step - 1))
                    if not isinstance(prev_checkpoint, dict):
                        msg = ("⚠️ The output from a previous workflow step is missing. "
                               "Please try again.")
                        print(f"[VALIDATE] Workflow job {job_id}: no checkpoint record for "
                              f"step {current_step - 1} (needed as input for step {current_step})")
                        mark_job_failed(job_id, msg)
                        return False

                    prev_output = prev_checkpoint.get("output")
                    last_output_url = _extract_valid_url(prev_output)

                    if not last_output_url:
                        msg = ("⚠️ The generated image from a previous workflow step is "
                               "missing or invalid. Please try again.")
                        print(f"[VALIDATE] Workflow job {job_id}: checkpoint output for "
                              f"step {current_step - 1} has no valid URL "
                              f"(got: {prev_output!r}) — cannot use as input for step {current_step}")
                        mark_job_failed(job_id, msg)
                        return False

            except Exception as e:
                print(f"[VALIDATE] Error validating workflow job {job_id}: {e} — allowing anyway")

        return True

    return True


def retry_transient_errors():
    """
    Retry pending AND pending_retry jobs that failed with transient errors (Cloudinary, network, timeout).
    Called periodically by the worker main loop every 10 minutes.

    NOTE: API-key errors (no key, invalid key, rotation failed) are intentionally excluded.
    Those jobs must only be re-triggered when a matching key is inserted via
    api_key_realtime_listener → handle_api_key_insertion().
    """
    try:
        print("[RETRY] Checking for pending/pending_retry jobs with transient errors...")

        from supabase_client import supabase as _retry_sb

        # Query PENDING jobs (non-workflow) with error messages
        pending_result = _retry_sb.table("jobs") \
            .select("job_id, model, error_message, created_at, updated_at, job_type") \
            .eq("status", "pending") \
            .neq("job_type", "workflow") \
            .is_("blocked_by_job_id", "null") \
            .not_.is_("error_message", "null") \
            .order("created_at", desc=False) \
            .limit(50) \
            .execute()
        
        pending_jobs = pending_result.data if pending_result and hasattr(pending_result, 'data') else []

        # Query PENDING_RETRY jobs (non-workflow) with error messages
        pending_retry_result = _retry_sb.table("jobs") \
            .select("job_id, model, error_message, created_at, updated_at, job_type, metadata") \
            .eq("status", "pending_retry") \
            .neq("job_type", "workflow") \
            .is_("blocked_by_job_id", "null") \
            .order("created_at", desc=False) \
            .limit(50) \
            .execute()
        
        pending_retry_jobs = pending_retry_result.data if pending_retry_result and hasattr(pending_retry_result, 'data') else []

        jobs = pending_jobs + pending_retry_jobs
        
        if not jobs:
            print("[RETRY] No pending/pending_retry jobs with errors found")
            return
        
        print(f"[RETRY] Found {len(pending_jobs)} pending + {len(pending_retry_jobs)} pending_retry jobs with errors")
        
        retryable_count = 0
        for job in jobs:
            error_msg = (job.get("error_message") or "").lower()
            job_status = job.get("status", "pending")
            job_id = job.get("job_id")

            # Skip API-key errors — these MUST only retry on key insertion, not on timer
            if _is_key_error(error_msg):
                print(f"[RETRY] Skipping job {job_id} — key-related error, waiting for API key insertion")
                continue
            
            # For pending_retry jobs: check retry_after timestamp and max retries
            if job_status == "pending_retry":
                metadata = job.get("metadata", {}) or {}
                retry_count = metadata.get("pending_retry_count", 0)
                
                # Determine max retries based on provider
                _job_provider = metadata.get("provider_key") or job.get("provider_key")
                _max_retries = MAX_PENDING_RETRIES_AICC if _job_provider in _AICC_PROVIDERS else MAX_PENDING_RETRIES
                
                if retry_count >= _max_retries:
                    print(f"[RETRY] Skipping pending_retry job {job_id} — max retries ({_max_retries}) reached")
                    continue
                
                # Check retry_after timestamp - wait before retrying
                retry_after_str = metadata.get("retry_after")
                if retry_after_str:
                    try:
                        import datetime as _dt
                        retry_after = _dt.datetime.fromisoformat(retry_after_str)
                        if _dt.datetime.utcnow() < retry_after:
                            _wait = (retry_after - _dt.datetime.utcnow()).total_seconds()
                            print(f"[RETRY] Skipping pending_retry job {job_id} — retry_after not reached ({_wait:.0f}s remaining)")
                            continue
                    except Exception:
                        pass
            
            # Only retry genuinely transient infrastructure errors.
            # quota_exceeded is included so that quota-reset jobs are picked up here
            # (they are intentionally excluded from the 30s deferred retry in
            # reset_job_to_pending() to avoid burning through retry attempts before
            # the quota resets).
            is_transient = (
                "cloudinary" in error_msg or
                "timeout" in error_msg or
                "timed out" in error_msg or
                "connection" in error_msg or
                "network" in error_msg or
                "httpsconnectionpool" in error_msg or
                "unreachable" in error_msg or
                "quota_exceeded" in error_msg
            )

            # Quota-exceeded jobs: only retry once the last attempt was ≥24h ago.
            # Quota buckets reset daily; retrying sooner just wastes a retry slot.
            if "quota_exceeded" in error_msg:
                from datetime import timedelta
                _last_ts = job.get("updated_at") or job.get("created_at")
                if _last_ts:
                    try:
                        _last_dt = datetime.fromisoformat(_last_ts.replace("Z", "+00:00"))
                        _elapsed = (datetime.utcnow() - _last_dt.replace(tzinfo=None)).total_seconds()
                        if _elapsed < 86400:  # 24 hours
                            print(f"[RETRY] Skipping quota-exceeded job {job_id} — "
                                  f"last attempt {_elapsed/3600:.1f}h ago, waiting for 24h quota reset")
                            continue
                    except Exception:
                        pass  # if parse fails, allow the retry

            if is_transient:
                retryable_count += 1
                print(f"[RETRY] Retrying job {job_id} ({job.get('model')}) - Error: {job.get('error_message', 'Unknown')[:80]}")
                
                # Fetch full job data and trigger processing
                try:
                    full_job_result = _retry_sb.table("jobs") \
                        .select("*") \
                        .eq("job_id", job_id) \
                        .single() \
                        .execute()
                    
                    if full_job_result and hasattr(full_job_result, 'data') and full_job_result.data:
                        retry_thread = threading.Thread(
                            target=process_job_with_concurrency_control,
                            args=(full_job_result.data,),
                            daemon=True
                        )
                        retry_thread.start()
                    else:
                        print(f"[RETRY] Could not fetch full job data for {job_id}")
                except Exception as api_err:
                    print(f"[RETRY] API error fetching job {job_id}: {api_err}")
        
        if retryable_count > 0:
            print(f"[RETRY] Triggered retry for {retryable_count} jobs with transient errors")
        else:
            print("[RETRY] No transient errors found to retry")
            
    except Exception as e:
        print(f"[RETRY] Error during retry check: {e}")
        import traceback
        traceback.print_exc()


def on_new_job(payload):
    try:
        print()
        print("REALTIME EVENT RECEIVED!")
        print(f"Payload: {payload}")
        
        record = payload.get("record") or payload.get("new") or payload
        
        if not record:
            print("No record in payload")
            return
        
        if record.get("status") != "pending":
            print(f"Skipping job with status: {record.get('status')}")
            return
        
        job_id = record.get("job_id") or record.get("id")
        metadata = record.get("metadata", {})
        priority = metadata.get("priority", "N/A")
        
        print()
        print("=" * 60)
        print(f"NEW JOB: {job_id}")
        print("=" * 60)
        print(f"User: {record.get('user_id')}")
        print(f"Prompt: {record.get('prompt')}")
        print(f"Model: {record.get('model')}")
        print(f"Aspect Ratio: {record.get('aspect_ratio')}")
        print(f"Priority: {priority}")
        print("=" * 60)
        print()
        
        process_job(record)
        
    except Exception as e:
        print(f"Error in realtime callback: {e}")
        import traceback
        traceback.print_exc()


def process_job_with_concurrency_control(job):
    """
    Wrapper for process_job that enforces provider-level and coordinator-level
    concurrency control. Also throttles total concurrent threads via semaphore.
    """
    job_id = job.get("job_id") or job.get("id")
    
    # Check maintenance mode
    from pathlib import Path
    maintenance_flag = Path(__file__).parent / ".maintenance_mode"
    if maintenance_flag.exists():
        print(f"[MAINTENANCE] Skipping job {job_id} - maintenance mode active")
        return None

    # Global thread throttle — avoid creating unbounded threads under load
    if not _job_thread_semaphore.acquire(timeout=10):
        print(f"[THREAD LIMIT] Job {job_id} could not acquire thread slot within 10s — "
              f"job remains pending in DB and will be retried by the periodic sweep")
        return None

    try:
        return _process_job_with_concurrency_control_inner(job)
    finally:
        _job_thread_semaphore.release()


def _process_job_with_concurrency_control_inner(job):
    """Inner implementation of concurrency-controlled job processing."""
    job_id = job.get("job_id") or job.get("id")

    # Enforce retry_after delay — realtime fires instantly when job resets to
    # pending, but we want a ~30s gap before the next attempt.
    _meta_check = job.get("metadata") or {}
    _retry_after_str = _meta_check.get("retry_after")
    if _retry_after_str:
        try:
            import datetime as _dt
            _retry_after = _dt.datetime.fromisoformat(_retry_after_str)
            _now = _dt.datetime.utcnow()
            if _now < _retry_after:
                _wait = (_retry_after - _now).total_seconds()
                print(f"[RETRY-DELAY] Job {job_id} not ready yet — waiting {_wait:.1f}s before retry")
                time.sleep(_wait)
        except Exception:
            pass

    # Check priority lock mode - only allow Priority 1 jobs
    if _priority_lock_active:
        metadata = job.get("metadata") or {}
        priority = metadata.get("priority", 1)
        if priority not in [None, 1]:
            print(f"[PRIORITY LOCK] Skipping priority {priority} job {job_id} - lock active, only P1 allowed")
            return None

    # Single authoritative input validation — runs exactly once regardless of
    # which path (realtime, backlog, retry, coordinator re-trigger) dispatched
    # this job. Removed from handle_new_job() and process_all_pending_jobs() to
    # prevent duplicate mark_job_failed() calls and duplicate SSE events.
    if not validate_job_inputs(job):
        print(f"[VALIDATE] Job {job_id} failed input validation — marked as failed, aborting")
        return None

    job_type = job.get("job_type", "image")
    model = job.get("model", "")

    metadata = job.get("metadata", {})

    video_indicators = ["video", "wan", "minimax", "luma", "topaz"]
    if job_type == "image" and model and any(v in model.lower() for v in video_indicators):
        job_type = "video"

    _default_providers = {"vision-nova", "cinematic-nova"}
    if model and job_type != "workflow":
        model_provider = map_model_to_provider(model, job_type=job_type)
        if model_provider not in _default_providers:
            provider_key = model_provider
        else:
            provider_key = metadata.get("provider_key") or job.get("provider_key") or model_provider
    else:
        provider_key = metadata.get("provider_key") or job.get("provider_key")
        if not provider_key:
            provider_key = map_model_to_provider(model, job_type=job_type)

    required_models = [] if job_type == "workflow" else ([model] if model else [])
    coordinator = get_job_coordinator()

    lock = get_provider_lock(provider_key)
    coordinator_started = False

    with lock:
        if is_provider_busy(provider_key):
            print(f"[CONCURRENCY] Provider {provider_key} is BUSY, queueing job {job_id}")
            enqueue_job(provider_key, job)
            return None

        if required_models:
            print(f"[COORDINATOR] Checking if job {job_id} can start - Models: {required_models}")
            start_result = coordinator.on_job_start(job_id, "normal", required_models)
            if not start_result['allowed']:
                print(f"[COORDINATOR] Job {job_id} blocked: {start_result['reason']}")
                print(f"[COORDINATOR] Job will be automatically processed when blocking job completes")
                return None
            print(f"[COORDINATOR] Job {job_id} allowed to start: {start_result['reason']}")
            coordinator_started = True

        mark_provider_busy(provider_key, job_id)

    try:
        result = process_job(job)
        return result
    finally:
        if coordinator_started:
            print(f"[COORDINATOR] Job {job_id} completed, notifying coordinator...")
            coordinator.on_job_complete(job_id, "normal")
        mark_provider_free(provider_key, job_id)


def process_job(job):
    job_id = job.get("job_id") or job.get("id")
    
    # Check maintenance mode - skip pending jobs
    from pathlib import Path
    maintenance_flag = Path(__file__).parent / ".maintenance_mode"
    if maintenance_flag.exists():
        print(f"[MAINTENANCE] Skipping job {job_id} - maintenance mode active")
        return None

    # Guard against processing jobs that were cancelled/failed while waiting in the queue
    try:
        from supabase_client import supabase as _sb
        _status_resp = _sb.table("jobs").select("status").eq("job_id", job_id).single().execute()
        if _status_resp.data:
            _current_status = _status_resp.data.get("status")
            if _current_status not in ("pending",):
                print(f"[SKIP] Job {job_id} status is '{_current_status}' — no longer pending, skipping")
                return None
    except Exception as _status_err:
        print(f"[SKIP-CHECK] Could not verify status for job {job_id}: {_status_err} — proceeding")

    job_type = job.get("job_type", "image")
    model = job.get("model", "")
    
    metadata = job.get("metadata", {})
    _meta_provider = metadata.get("provider_key") or job.get("provider_key")

    _default_providers = {"vision-nova", "cinematic-nova"}
    _model_provider = map_model_to_provider(model, job_type=job_type) if model else None
    if _model_provider and _model_provider not in _default_providers:
        provider_key = _model_provider
    else:
        provider_key = _meta_provider or _model_provider

    quota_manager = get_quota_manager()
    if not quota_manager.check_quota_available(provider_key, model):
        error_msg = f"QUOTA_EXCEEDED:{provider_key}:{model}"
        print(f"[QUOTA] Model quota exceeded for {provider_key}:{model} — resetting to pending for retry")
        reset_job_to_pending(job_id, provider_key, error_msg)
        return None
    
    video_indicators = ["video", "wan", "minimax", "luma", "topaz"]
    if job_type == "image" and any(v in model.lower() for v in video_indicators):
        job_type = "video"
        print(f"Detected VIDEO job based on model: {model}")
    
    print(f"\n{'='*60}")
    print(f"PROCESSING {job_type.upper()} JOB")
    print(f"{'='*60}")
    print(f"Job ID: {job_id}")
    
    endpoint_type = get_endpoint_type(provider_key, model)
    print(f"Provider: {provider_key}")
    print(f"Endpoint: {endpoint_type.upper()}")
    print(f"{'='*60}\n")
    
    if job_type == "video":
        return process_video_job(job)
    else:
        return process_image_job(job)


def process_video_job(job):
    job_id = job.get("job_id") or job.get("id")
    
    print(f"\n{'='*70}")
    print(f"PROCESSING VIDEO JOB")
    print(f"{'='*70}")
    print(f"Job ID: {job_id}")
    print(f"User ID: {job.get('user_id', 'N/A')}")
    print(f"Prompt: {job.get('prompt', 'N/A')}")
    print(f"Model: {job.get('model', 'N/A')}")
    print(f"Aspect Ratio: {job.get('aspect_ratio', '16:9')}")
    print(f"{'='*70}\n")
    sys.stdout.flush()
    
    api_key_id = None
    provider_key = None
    
    try:
        requests.post(
            f"{BACKEND_URL}/worker/job/{job_id}/progress",
            json={"progress": 10, "message": "Starting video generation..."},
            timeout=10,
            verify=VERIFY_SSL
        )
        
        metadata = job.get("metadata", {})
        input_image_url = metadata.get("input_image_url") or metadata.get("video_url")
        mask_url = metadata.get("mask_url")
        duration = metadata.get("duration", 5)
        _meta_provider_v = metadata.get("provider_key") or job.get("provider_key")
        
        job_model = job.get("model", "minimax/video-01")
        
        if job_model in MODELS_REQUIRING_INPUT_IMAGE_FOR_VIDEO and not input_image_url:
            print(f"[MISSING INPUT IMAGE] Video model {job_model} requires an input image but none was provided")
            print(f"[MISSING INPUT IMAGE] Marking job {job_id} as FAILED")
            mark_job_failed(job_id, "⚠️ This video tool requires an input image. Please upload a reference image and try again.")
            return
        
        if job_model in MODELS_REQUIRING_INPUT_VIDEO and not input_image_url:
            print(f"[MISSING INPUT VIDEO] Model {job_model} requires an input video but none was provided")
            print(f"[MISSING INPUT VIDEO] Marking job {job_id} as FAILED")
            mark_job_failed(job_id, "⚠️ This tool requires an input video. Please upload a video and try again.")
            return
        
        _default_providers = {"vision-nova", "cinematic-nova"}
        _model_provider_v = map_model_to_provider(job_model, job_type="video")
        if _model_provider_v and _model_provider_v not in _default_providers:
            provider_key = _model_provider_v
        else:
            provider_key = _meta_provider_v or _model_provider_v
        print(f"Determined provider from model: {provider_key}")
        
        # If rotation already selected the next key, use it directly (skip re-fetch)
        if job.get("_rotated_api_key"):
            provider_api_key = job.pop("_rotated_api_key")
            api_key_id = job.pop("_rotated_api_key_id", None)
            api_key_number = job.pop("_rotated_api_key_number", None)
            print(f"[ROTATION] Using pre-rotated API key (id={api_key_id}, key_number={api_key_number}) for provider: {provider_key}")
        else:
            api_key_data = get_api_key_for_job(job_model, provider_key, job_type="video")

            provider_api_key = None

            if api_key_data:
                api_key_id = api_key_data.get("id")
                api_key_number = api_key_data.get("key_number")
                provider_api_key = api_key_data.get("api_key")
                if api_key_data.get("provider_key"):
                    provider_key = api_key_data.get("provider_key")
                print(f"Using API key from provider: {provider_key} (key #{api_key_number})")
            else:
                print(f"No API key found for provider: {provider_key}, marking job as pending")
                notify_error(
                    ErrorType.NO_API_KEY_FOR_PROVIDER,
                    f"No API keys available for {provider_key}",
                    context={"provider": provider_key, "job_id": job_id, "job_type": "video"}
                )
                reset_job_to_pending(job_id, provider_key, f"No API key available for provider: {provider_key}")
                return
        
        aspect_ratio = job.get("aspect_ratio", "16:9")
        
        print(f"Calling generate() for video...")
        print(f"  Model: {job_model}")
        print(f"  Duration: {duration}s")
        print(f"  Aspect: {aspect_ratio}")
        print(f"  Input Image/Video: {input_image_url}")
        if mask_url:
            print(f"  Mask URL: {mask_url}")
        
        result = generate(
            prompt=job.get("prompt"),
            model=job_model,
            aspect_ratio=aspect_ratio,
            api_key=provider_api_key,
            provider_key=provider_key,
            input_image_url=input_image_url,
            job_type="video",
            duration=duration,
            mask_url=mask_url
        )
        
        if not result.get("success"):
            error_msg = result.get("error", "Generation failed")
            raise Exception(error_msg)
        
        requests.post(
            f"{BACKEND_URL}/worker/job/{job_id}/progress",
            json={"progress": 50, "message": "Video generated, uploading..."},
            timeout=10,
            verify=VERIFY_SSL
        )
        
        import tempfile
        if result.get("is_base64"):
            print(f"Generation returned base64 data, saving directly...")
            b64_data = result.get("data")
            
            # Handle data URI prefix if present
            if isinstance(b64_data, str) and b64_data.startswith('data:'):
                b64_data = b64_data.split(',')[1]
            
            # Remove any whitespace/newlines from base64
            b64_data = b64_data.replace('\n', '').replace('\r', '').replace(' ', '')
            
            print(f"[Base64] Data length: {len(b64_data)} chars")
            try:
                video_data = base64.b64decode(b64_data)
                print(f"[Base64] Successfully decoded to {len(video_data)} bytes")
            except Exception as decode_error:
                print(f"[Base64] Decode error: {str(decode_error)}")
                print(f"[Base64] First 100 chars: {b64_data[:100]}")
                raise Exception(f"Failed to decode base64 data: {str(decode_error)}")
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp_file:
                tmp_file.write(video_data)
                video_path = tmp_file.name
        else:
            video_url = result.get("url")
            print(f"Generation successful: {video_url}")
            
            print(f"Downloading video from: {video_url}")
            video_response = requests.get(video_url, timeout=120)
            
            if video_response.status_code != 200:
                raise Exception(f"Failed to download video: {video_response.status_code}")
            
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4") as tmp_file:
                tmp_file.write(video_response.content)
                video_path = tmp_file.name
        
        print(f"Uploading to Cloudinary...")
        video_size_mb = os.path.getsize(video_path) / (1024 * 1024)
        print(f"Video size: {video_size_mb:.2f} MB")
        
        max_cloudinary_video_mb = 500
        if video_size_mb > max_cloudinary_video_mb:
            print(f"[WARNING] Video size is {video_size_mb:.2f} MB (exceeds recommended: {max_cloudinary_video_mb} MB)")
        
        try:
            from cloudinary_manager import get_cloudinary_manager
            cloudinary = get_cloudinary_manager()
            
            final_url = cloudinary.upload_video(video_path, job_id)
            print(f"Uploaded: {final_url}")
        except Exception as upload_error:
            print(f"[Cloudinary] Video upload error: {str(upload_error)}")
            notify_error(
                ErrorType.CLOUDINARY_UPLOAD_FAILED,
                f"Cloudinary video upload failed for job {job_id}",
                context={"job_id": job_id, "error": str(upload_error)[:200]}
            )
            raise Exception(f"Cloudinary video upload error: {str(upload_error)}")
        finally:
            import os as os_module
            try:
                os_module.unlink(video_path)
            except:
                pass
        
        _video_completed = False
        try:
            complete_response = requests.post(
                f"{BACKEND_URL}/worker/job/{job_id}/complete",
                json={"image_url": final_url, "video_url": final_url, "success": True},
                timeout=10,
                verify=VERIFY_SSL
            )
            if complete_response.status_code == 200:
                _video_completed = True
            else:
                error_msg = f"Failed to mark job complete: {complete_response.status_code} - {complete_response.text[:200]}"
                print(f"[COMPLETION ERROR] {error_msg}")
        except Exception as _http_err:
            print(f"[COMPLETION ERROR] HTTP call to /complete failed: {_http_err}")

        if not _video_completed:
            print(f"[COMPLETION FALLBACK] HTTP call failed — writing result directly to DB to preserve video URL")
            try:
                from jobs import update_job_result as _update_job_result
                _fb = _update_job_result(job_id, image_url=final_url, video_url=final_url)
                if _fb.get("success"):
                    print(f"[COMPLETION FALLBACK] Direct DB update succeeded for video job {job_id}")
                    _video_completed = True
                else:
                    raise Exception(_fb.get("error", "unknown"))
            except Exception as _fb_err:
                print(f"[COMPLETION FALLBACK] Direct DB fallback also failed: {_fb_err}")
                raise Exception(f"Video generated but could not save result: {_fb_err}")

        if _video_completed:
            print(f"Video job {job_id} completed successfully!")
            worker_status["jobs_processed"] += 1

            if api_key_id:
                increment_usage_count(api_key_id)

            # Clear cooldown/error status for NO_DELETE provider keys after successful use
            if api_key_number is not None and provider_key:
                from provider_api_keys import clear_api_key_status
                clear_api_key_status(provider_key, int(api_key_number))
            
            # Increment quota after successful completion
            from model_quota_manager import get_quota_manager
            quota_manager = get_quota_manager()
            job_model = job.get("model", "minimax/video-01")
            print(f"[QUOTA] Incrementing after completion - provider_key='{provider_key}', model='{job_model}'")
            quota_result = quota_manager.increment_quota(provider_key, job_model)
            if quota_result.get('success'):
                print(f"[QUOTA] ✓ Successfully incremented quota for {provider_key}:{job_model}")
            else:
                print(f"[QUOTA] ✗ Failed to increment: {quota_result.get('reason', 'unknown')}")
        
    except Exception as e:
        error_message = str(e)
        print(f"Error processing video job: {error_message}")
        
        # ========================================================================
        # ERROR HANDLING LOGIC:
        # - VALIDATION ERRORS (user input): Mark job as FAILED (not retryable)
        # - CLOUDINARY/NETWORK/TIMEOUT: Reset to PENDING (retryable, no key rotation)
        # - API ERRORS: Attempt key rotation, then reset to PENDING if no keys
        # - ALL OTHER ERRORS: Reset to PENDING for retry
        # ========================================================================
        
        is_image_format_error = "INVALID_IMAGE_FORMAT:" in error_message
        is_image_not_supported_error = "IMAGE_NOT_SUPPORTED:" in error_message

        is_completion_error = "failed to mark job complete" in error_message.lower()
        is_input_image_error = (
            "failed to upload reference images to leonardo" in error_message.lower() or
            "failed to upload input image to leonardo" in error_message.lower() or
            "failed to download input image" in error_message.lower()
        )
        is_cloudinary_error = "cloudinary" in error_message.lower() and not is_input_image_error
        is_timeout_error = any(x in error_message.lower() for x in ["timeout", "timed out", "read timeout", "connection timeout", "504"])
        is_network_error = any(x in error_message.lower() for x in ["connection", "httpsconnectionpool", "unable to connect", "network", "unreachable"])
        is_vercel_payload_error = "413" in error_message or "function_payload_too_large" in error_message.lower()
        is_provider_fetch_error = any(x in error_message.lower() for x in [
            "fail_to_fetch_task", "datainspection", "invalidparameter.datainspection",
            "unable to download the media resource",
        ])

        # Only mark as validation error if it's specifically about user input (not API errors)
        # Be very specific to avoid false positives with API errors
        is_validation_error = (
            ("requires a video input" in error_message.lower()) or
            ("requires a image input" in error_message.lower()) or
            ("requires either a mask_video or prompt" in error_message.lower()) or
            ("requires a prompt" in error_message.lower()) or
            ("requires key_points parameter" in error_message.lower()) or
            ("requires an input image" in error_message.lower())
        )

        if is_image_format_error:
            print(f"[IMAGE FORMAT ERROR] Unsupported image format detected - NOT rotating API key")
            user_message = error_message.split("INVALID_IMAGE_FORMAT:", 1)[-1].strip()
            print(f"[IMAGE FORMAT ERROR] Marking job {job_id} as FAILED (not retryable): {user_message}")
            mark_job_failed(job_id, f"⚠️ {user_message}")
            return

        if is_image_not_supported_error:
            print(f"[IMAGE NOT SUPPORTED] Image input not supported by this endpoint - NOT rotating API key")
            user_message = error_message.split("IMAGE_NOT_SUPPORTED:", 1)[-1].strip()
            print(f"[IMAGE NOT SUPPORTED] Marking job {job_id} as FAILED (not retryable): {user_message}")
            mark_job_failed(job_id, f"⚠️ {user_message}")
            return

        if is_vercel_payload_error:
            print(f"[VERCEL PAYLOAD ERROR] Request/response payload too large (413) - NOT rotating API key")
            mark_job_failed(job_id, "⚠️ The generated content exceeded the size limit. Please try a shorter duration or simpler prompt.")
            return

        if is_completion_error:
            print(f"[COMPLETION ERROR] Job completion API call failed - marking as FAILED to prevent reprocessing")
            print(f"[COMPLETION ERROR] Video was already generated but DB update failed")
            print(f"[COMPLETION ERROR] Error: {error_message}")
            mark_job_failed(job_id, f"COMPLETION_FAILED: Video generated but status update failed - {error_message}")
            return
        
        if is_input_image_error:
            print(f"[INPUT IMAGE ERROR] User input image could not be fetched/uploaded - NOT rotating API key")
            print(f"[INPUT IMAGE ERROR] Marking job {job_id} as FAILED (not retryable)")
            mark_job_failed(job_id, "⚠️ Your uploaded image could not be loaded. Please re-upload your image and try again.")
            return
        
        if is_cloudinary_error:
            print(f"[CLOUDINARY ERROR] Cloudinary upload error detected - NOT rotating API key")
            print(f"[CLOUDINARY ERROR] Resetting job to PENDING for retry")
            reset_job_to_pending(job_id, provider_key, f"Cloudinary upload error: {error_message}")
            return
        
        if is_timeout_error or is_network_error:
            print(f"[NETWORK ERROR] Network/Timeout error detected - NOT rotating API key")
            print(f"[NETWORK ERROR] Resetting job to PENDING for retry")
            reset_job_to_pending(job_id, provider_key, f"Network/Timeout error: {error_message}")
            return

        if is_provider_fetch_error:
            print(f"[FETCH ERROR] Provider could not download input media - NOT rotating API key")
            print(f"[FETCH ERROR] Resetting job to PENDING for retry")
            reset_job_to_pending(job_id, provider_key, f"Provider media fetch error: {error_message}")
            return

        if is_validation_error:
            print(f"[VALIDATION ERROR] User input validation failed - NOT rotating API key")
            print(f"[VALIDATION ERROR] Error: {error_message}")
            print(f"[VALIDATION ERROR] Marking job {job_id} as FAILED (not retryable)")
            mark_job_failed(job_id, error_message)
            return
        
        # All other errors (API errors, provider errors, etc.) - attempt key rotation and retry
        if api_key_id and provider_key:
            print(f"[API ERROR] Provider API error detected - attempting key rotation")
            if provider_key in NO_DELETE_ROTATE_PROVIDERS:
                rr_count = job.get("_rr_rotation_count", 0)
                total_keys = len(get_all_api_keys_for_provider(provider_key)) if provider_key else 0
                max_attempts = max(total_keys * 2, 4)
                print(f"[RR-ROTATION] No-delete provider '{provider_key}' | attempt {rr_count + 1} of {max_attempts} max")
                if rr_count >= max_attempts:
                    print(f"[RR-ROTATION] Max cycles reached for '{provider_key}' - failing job")
                    mark_job_failed(job_id, f"All API keys for {provider_key} failed after {max_attempts} rotation attempts. Please try again later.")
                    return
                rotation_success, next_key = handle_roundrobin_rotation(
                    provider_key, error_message, job_id, current_api_key_id=api_key_id
                )
                log_rotation_attempt(job_id, provider_key, api_key_id,
                                   next_key.get("id") if next_key else None,
                                   error_message, rotation_success)
                if rotation_success and next_key:
                    job["_rr_rotation_count"] = rr_count + 1
                    job["_rotated_api_key"] = next_key.get("api_key")
                    job["_rotated_api_key_id"] = next_key.get("id")
                    job["_rotated_api_key_number"] = next_key.get("key_number")
                    print(f"[RR-ROTATION] Retrying video job with key #{next_key.get('key_number')} (attempt {rr_count + 1}/{max_attempts})...")
                    return process_video_job(job)
                else:
                    print(f"[RR-ROTATION] No keys available for '{provider_key}' - failing job")
                    mark_job_failed(job_id, f"No API keys available for provider: {provider_key}")
                    return
            else:
                print(f"[ROTATION] Attempting to rotate API key...")
                rotation_success, next_key = handle_api_key_rotation(
                    api_key_id,
                    provider_key,
                    error_message,
                    job_id
                )
                log_rotation_attempt(job_id, provider_key, api_key_id,
                                   next_key.get("id") if next_key else None,
                                   error_message, rotation_success)

                if rotation_success and next_key:
                    print(f"[RETRY] Retrying video job with new API key...")
                    return process_video_job(job)
                else:
                    print(f"[ERROR] API key rotation failed or no keys available")
                    notify_error(
                        ErrorType.API_KEY_ROTATION_FAILED,
                        f"API key rotation failed for {provider_key}",
                        context={"provider": provider_key, "job_id": job_id, "error": error_message}
                    )
                    reset_job_to_pending(job_id, provider_key, f"No API key available for provider: {provider_key}")
                    return
        
        # Catch-all for any other errors (reset to pending for retry)
        print(f"[UNKNOWN ERROR] Unhandled error type - resetting to PENDING for retry")
        print(f"[UNKNOWN ERROR] Error: {error_message}")
        reset_job_to_pending(job_id, provider_key, error_message)
        return


def process_image_job(job):
    job_id = job.get("job_id") or job.get("id")
    
    print(f"\n{'='*70}")
    print(f"PROCESSING IMAGE JOB")
    print(f"{'='*70}")
    print(f"Job ID: {job_id}")
    print(f"User ID: {job.get('user_id', 'N/A')}")
    print(f"Prompt: {job.get('prompt', 'N/A')}")
    print(f"Model: {job.get('model', 'N/A')}")
    print(f"Aspect Ratio: {job.get('aspect_ratio', '1:1')}")
    print(f"{'='*70}\n")
    sys.stdout.flush()
    
    api_key_id = None
    provider_key = None
    
    try:
        requests.post(
            f"{BACKEND_URL}/worker/job/{job_id}/progress",
            json={"progress": 10, "message": "Starting generation..."},
            timeout=10,
            verify=VERIFY_SSL
        )
        
        model_name = job.get("model", "openflux1-v0.1.0-fp8.safetensors")
        metadata = job.get("metadata", {}) or {}
        input_image_url = metadata.get("input_image_url") or job.get("image_url")
        mask_url = metadata.get("mask_url")
        _meta_provider_i = metadata.get("provider_key") or job.get("provider_key")

        _default_providers = {"vision-nova", "cinematic-nova"}
        _model_provider_i = map_model_to_provider(model_name, job_type="image")
        if _model_provider_i and _model_provider_i not in _default_providers:
            provider_key = _model_provider_i
        else:
            provider_key = _meta_provider_i or _model_provider_i
        print(f"Determined provider from model: {provider_key}")

        if model_name in MODELS_REQUIRING_INPUT_IMAGE and not input_image_url:
            print(f"[MISSING INPUT IMAGE] Model {model_name} requires an input image but none was provided")
            print(f"[MISSING INPUT IMAGE] Marking job {job_id} as FAILED")
            mark_job_failed(job_id, "⚠️ This tool requires an input image. Please upload a reference image and try again.")
            return
        
        # If rotation already selected the next key, use it directly (skip re-fetch)
        if job.get("_rotated_api_key"):
            provider_api_key = job.pop("_rotated_api_key")
            api_key_id = job.pop("_rotated_api_key_id", None)
            api_key_number = job.pop("_rotated_api_key_number", None)
            print(f"[ROTATION] Using pre-rotated API key (id={api_key_id}, key_number={api_key_number}) for provider: {provider_key}")
        else:
            api_key_data = get_api_key_for_job(model_name, provider_key, job_type="image")

            provider_api_key = None

            if api_key_data:
                api_key_id = api_key_data.get("id")
                api_key_number = api_key_data.get("key_number")
                provider_api_key = api_key_data.get("api_key")
                if api_key_data.get("provider_key"):
                    provider_key = api_key_data.get("provider_key")
                print(f"Using API key from provider: {provider_key} (key #{api_key_number})")
            else:
                print(f"No API key found for provider: {provider_key}, marking job as pending")
                notify_error(
                    ErrorType.NO_API_KEY_FOR_PROVIDER,
                    f"No API keys available for {provider_key}",
                    context={"provider": provider_key, "job_id": job_id, "job_type": "image"}
                )
                reset_job_to_pending(job_id, provider_key, f"No API key available for provider: {provider_key}")
                return
        
        print(f"Calling generate() for image...")
        print(f"  Model: {model_name}")
        print(f"  Provider: {provider_key}")
        print(f"  Aspect: {job.get('aspect_ratio', '1:1')}")
        if mask_url:
            print(f"  Mask URL: {mask_url}")
        
        result = generate(
            prompt=job.get("prompt"),
            model=model_name,
            aspect_ratio=job.get("aspect_ratio", "1:1"),
            api_key=provider_api_key,
            provider_key=provider_key,
            input_image_url=input_image_url,
            job_type="image",
            mask_url=mask_url,
            job_id=job_id
        )
        
        if not result.get("success"):
            error_msg = result.get("error", "Generation failed")
            raise Exception(error_msg)
        
        # Handle webhook mode - job will be completed via webhook callback
        if result.get("status") == "queued":
            print(f"[WEBHOOK] Job {job_id} queued, waiting for webhook delivery...")
            print(f"[WEBHOOK] Execution ID: {result.get('execution_id')}")
            from supabase_client import supabase
            existing_meta = job.get("metadata") or {}
            existing_meta["execution_id"] = result.get("execution_id")
            existing_meta["webhook_pending"] = True
            supabase.table("jobs").update({
                "status": "running",
                "metadata": existing_meta
            }).eq("job_id", job_id).execute()
            print(f"[WEBHOOK] Job {job_id} marked as running, exiting worker...")
            return
        
        if result.get("is_raw_bytes"):
            print(f"Generation returned raw bytes, using directly...")
            image_data = result.get("data")
            print(f"[RawBytes] Data length: {len(image_data)} bytes")
        elif result.get("is_base64"):
            print(f"Generation returned base64 data, uploading directly...")
            b64_data = result.get("data")
            
            # Handle data URI prefix if present
            if isinstance(b64_data, str) and b64_data.startswith('data:'):
                b64_data = b64_data.split(',')[1]
            
            # Remove any whitespace/newlines from base64
            b64_data = b64_data.replace('\n', '').replace('\r', '').replace(' ', '')
            
            print(f"[Base64] Data length: {len(b64_data)} chars")
            try:
                image_data = base64.b64decode(b64_data)
                print(f"[Base64] Successfully decoded to {len(image_data)} bytes")
            except Exception as decode_error:
                print(f"[Base64] Decode error: {str(decode_error)}")
                print(f"[Base64] First 100 chars: {b64_data[:100]}")
                raise Exception(f"Failed to decode base64 data: {str(decode_error)}")
        else:
            image_url = result.get("url")
            print(f"Generation successful: {image_url}")
            image_data = None
        
        # Truncate prompt to avoid Cloudinary context field length limits (max ~1000 chars)
        prompt_text = job.get("prompt", "")
        if len(prompt_text) > 1000:
            prompt_text = prompt_text[:997] + "..."
            print(f"[WARNING] Prompt truncated from {len(job.get('prompt', ''))} to 1000 characters for Cloudinary")
        
        upload_metadata = {
            "prompt": prompt_text,
            "model": job.get("model", ""),
            "aspect_ratio": job.get("aspect_ratio", ""),
            "job_id": job_id,
            "user_id": job.get("user_id", "")
        }
        
        print(f"Uploading to Cloudinary (direct)...")
        try:
            cloudinary_manager = get_cloudinary_manager()
            
            if image_data is None and image_url:
                print(f"[Cloudinary] Attempting URL-based upload (Cloudinary fetches from source)...")
                upload_result = cloudinary_manager.upload_image_from_url(
                    image_url=image_url,
                    file_name=f"job_{job_id}.png",
                    folder_name="ai-generated-images",
                    metadata=upload_metadata
                )
                if not upload_result.get("success"):
                    print(f"[Cloudinary] URL upload failed, falling back to download: {upload_result.get('error')}")
                    print(f"Downloading image from: {image_url}")
                    img_response = requests.get(image_url, timeout=60)
                    if img_response.status_code != 200:
                        raise Exception(f"Failed to download image: {img_response.status_code}")
                    image_data = img_response.content
            
            if image_data is not None:
                image_size_mb = len(image_data) / (1024 * 1024)
                print(f"Image data: {len(image_data)} bytes ({image_size_mb:.2f} MB)")
                if image_size_mb > 10:
                    print(f"[COMPRESSION] Compressing image from {image_size_mb:.2f} MB...")
                    image_data = compress_image_to_size(image_data, max_size_mb=8)
                    print(f"[COMPRESSION] Compressed to {len(image_data) / (1024*1024):.2f} MB")
                upload_result = cloudinary_manager.upload_image_from_bytes(
                    image_bytes=image_data,
                    file_name=f"job_{job_id}.png",
                    folder_name="ai-generated-images",
                    metadata=upload_metadata
                )
            
            if not upload_result.get("success"):
                error_msg = upload_result.get("error", "Unknown error")
                print(f"[Cloudinary] Upload failed: {error_msg}")
                notify_error(
                    ErrorType.CLOUDINARY_UPLOAD_FAILED,
                    f"Cloudinary direct upload failed for job {job_id}",
                    context={"job_id": job_id, "error": error_msg}
                )
                raise Exception(f"Cloudinary upload failed: {error_msg}")
            
            final_url = upload_result.get('secure_url')
            print(f"[Cloudinary] Direct upload successful!")
            print(f"Uploaded: {final_url}")
        except Exception as upload_error:
            print(f"[Cloudinary] Upload error: {str(upload_error)}")
            raise Exception(f"Cloudinary upload error: {str(upload_error)}")
        
        _image_completed = False
        try:
            complete_response = requests.post(
                f"{BACKEND_URL}/worker/job/{job_id}/complete",
                json={"image_url": final_url, "thumbnail_url": final_url},
                timeout=10,
                verify=VERIFY_SSL
            )
            if complete_response.status_code == 200:
                _image_completed = True
            else:
                error_msg = f"Failed to mark job complete: {complete_response.status_code} - {complete_response.text[:200]}"
                print(f"[COMPLETION ERROR] {error_msg}")
        except Exception as _http_err:
            print(f"[COMPLETION ERROR] HTTP call to /complete failed: {_http_err}")

        if not _image_completed:
            print(f"[COMPLETION FALLBACK] HTTP call failed — writing result directly to DB to preserve image URL")
            try:
                from jobs import update_job_result as _update_job_result
                _fb = _update_job_result(job_id, image_url=final_url, thumbnail_url=final_url)
                if _fb.get("success"):
                    print(f"[COMPLETION FALLBACK] Direct DB update succeeded for image job {job_id}")
                    _image_completed = True
                else:
                    raise Exception(_fb.get("error", "unknown"))
            except Exception as _fb_err:
                print(f"[COMPLETION FALLBACK] Direct DB fallback also failed: {_fb_err}")
                raise Exception(f"Image generated but could not save result: {_fb_err}")

        if _image_completed:
            print(f"Job {job_id} completed successfully!")
            worker_status["jobs_processed"] += 1
            if api_key_id:
                increment_usage_count(api_key_id)

            # Clear cooldown/error status for NO_DELETE provider keys after successful use
            if api_key_number is not None and provider_key:
                from provider_api_keys import clear_api_key_status
                clear_api_key_status(provider_key, int(api_key_number))
            
            # Increment quota after successful completion
            from model_quota_manager import get_quota_manager
            quota_manager = get_quota_manager()
            print(f"[QUOTA] Incrementing after completion - provider_key='{provider_key}', model='{model_name}'")
            quota_result = quota_manager.increment_quota(provider_key, model_name)
            if quota_result.get('success'):
                print(f"[QUOTA] ✓ Successfully incremented quota for {provider_key}:{model_name}")
            else:
                print(f"[QUOTA] ✗ Failed to increment: {quota_result.get('reason', 'unknown')}")
            
    except Exception as e:
        error_message = str(e)
        print(f"Error processing image job: {error_message}")
        
        # ========================================================================
        # ERROR HANDLING LOGIC:
        # - VALIDATION ERRORS (user input): Mark job as FAILED (not retryable)
        # - IMAGE QUALITY ERRORS (removebg foreground): Mark job as FAILED (not retryable)
        # - CLOUDINARY/NETWORK/TIMEOUT: Reset to PENDING (retryable, no key rotation)
        # - API ERRORS: Attempt key rotation, then reset to PENDING if no keys
        # - ALL OTHER ERRORS: Reset to PENDING for retry
        # ========================================================================
        
        # Check for Remove.bg foreground detection error (user-facing, non-retryable)
        is_removebg_foreground_error = "REMOVEBG_FOREGROUND_ERROR" in error_message

        is_image_format_error = "INVALID_IMAGE_FORMAT:" in error_message
        is_image_not_supported_error = "IMAGE_NOT_SUPPORTED:" in error_message

        is_completion_error = "failed to mark job complete" in error_message.lower()
        is_input_image_error = (
            "failed to upload reference images to leonardo" in error_message.lower() or
            "failed to upload input image to leonardo" in error_message.lower() or
            "failed to download input image" in error_message.lower()
        )
        is_cloudinary_error = "cloudinary" in error_message.lower() and not is_input_image_error
        is_timeout_error = any(x in error_message.lower() for x in ["timeout", "timed out", "read timeout", "connection timeout", "504"])
        is_network_error = any(x in error_message.lower() for x in ["connection", "httpsconnectionpool", "unable to connect", "network", "unreachable"])
        is_vercel_payload_error = "413" in error_message or "function_payload_too_large" in error_message.lower()
        is_hf_space_error = ".hf.space" in error_message.lower() or "huggingface.co/models" in error_message.lower()
        is_model_not_found_error = (
            ("is not found for api version" in error_message.lower()) or
            ("not supported for generatecontent" in error_message.lower()) or
            ("model not found" in error_message.lower())
        )

        # Only mark as validation error if it's specifically about user input (not API errors)
        # Be very specific to avoid false positives with API errors
        is_validation_error = (
            ("requires a video input" in error_message.lower()) or
            ("requires a image input" in error_message.lower()) or
            ("requires either a mask_video or prompt" in error_message.lower()) or
            ("requires a prompt" in error_message.lower()) or
            ("requires key_points parameter" in error_message.lower()) or
            ("requires an input image" in error_message.lower())
        )

        if is_image_format_error:
            print(f"[IMAGE FORMAT ERROR] Unsupported image format detected - NOT rotating API key")
            user_message = error_message.split("INVALID_IMAGE_FORMAT:", 1)[-1].strip()
            print(f"[IMAGE FORMAT ERROR] Marking job {job_id} as FAILED (not retryable): {user_message}")
            mark_job_failed(job_id, f"⚠️ {user_message}")
            return

        if is_image_not_supported_error:
            print(f"[IMAGE NOT SUPPORTED] Image input not supported by this endpoint - NOT rotating API key")
            user_message = error_message.split("IMAGE_NOT_SUPPORTED:", 1)[-1].strip()
            print(f"[IMAGE NOT SUPPORTED] Marking job {job_id} as FAILED (not retryable): {user_message}")
            mark_job_failed(job_id, f"⚠️ {user_message}")
            return

        if is_vercel_payload_error:
            print(f"[VERCEL PAYLOAD ERROR] Request/response payload too large (413) - NOT rotating API key")
            mark_job_failed(job_id, "⚠️ The generated image exceeded the size limit. Please try a different prompt.")
            return

        if is_removebg_foreground_error:
            print(f"[IMAGE QUALITY ERROR] Remove.bg could not identify foreground - NOT rotating API key")
            # Extract the user-friendly message (remove the prefix)
            user_message = error_message.replace("Remove.bg generation failed: REMOVEBG_FOREGROUND_ERROR: ", "")
            print(f"[IMAGE QUALITY ERROR] User message: {user_message}")
            print(f"[IMAGE QUALITY ERROR] Marking job {job_id} as FAILED (not retryable)")
            mark_job_failed(job_id, user_message)
            return
        
        if is_completion_error:
            print(f"[COMPLETION ERROR] Job completion API call failed - marking as FAILED to prevent reprocessing")
            print(f"[COMPLETION ERROR] Image was already generated but DB update failed")
            print(f"[COMPLETION ERROR] Error: {error_message}")
            mark_job_failed(job_id, f"COMPLETION_FAILED: Image generated but status update failed - {error_message}")
            return
        
        if is_input_image_error:
            print(f"[INPUT IMAGE ERROR] User input image could not be fetched/uploaded - NOT rotating API key")
            print(f"[INPUT IMAGE ERROR] Marking job {job_id} as FAILED (not retryable)")
            mark_job_failed(job_id, "⚠️ Your uploaded image could not be loaded. Please re-upload your image and try again.")
            return
        
        if is_cloudinary_error:
            print(f"[CLOUDINARY ERROR] Cloudinary upload error detected - NOT rotating API key")
            print(f"[CLOUDINARY ERROR] Resetting job to PENDING for retry")
            reset_job_to_pending(job_id, provider_key, f"Cloudinary upload error: {error_message}")
            return
        
        if is_timeout_error or is_network_error:
            print(f"[NETWORK ERROR] Network/Timeout error detected - NOT rotating API key")
            print(f"[NETWORK ERROR] Resetting job to PENDING for retry")
            reset_job_to_pending(job_id, provider_key, f"Network/Timeout error: {error_message}")
            return
        
        if is_hf_space_error:
            print(f"[HF SPACE ERROR] HuggingFace Space error - NOT rotating API key")
            print(f"[HF SPACE ERROR] Resetting job to PENDING for retry")
            reset_job_to_pending(job_id, provider_key, f"HuggingFace Space error: {error_message}")
            return
        
        if is_validation_error:
            print(f"[VALIDATION ERROR] User input validation failed - NOT rotating API key")
            print(f"[VALIDATION ERROR] Error: {error_message}")
            print(f"[VALIDATION ERROR] Marking job {job_id} as FAILED (not retryable)")
            mark_job_failed(job_id, error_message)
            return

        if is_model_not_found_error and provider_key in ("vision-aicc", "cinematic-aicc"):
            print(f"[MODEL NOT FOUND] AICC model unavailable after all inner retries - NOT rotating API key")
            print(f"[MODEL NOT FOUND] Resetting job to PENDING for outer periodic retry")
            reset_job_to_pending(job_id, provider_key, f"Model not found error: {error_message}")
            return

        # All other errors (API errors, provider errors, etc.) - attempt key rotation and retry
        if api_key_id and provider_key:
            print(f"[API ERROR] Provider API error detected - attempting key rotation")

            if provider_key in NO_DELETE_ROTATE_PROVIDERS:
                # No-delete round-robin rotation with 2-cycle limit (per job)
                rr_count = job.get("_rr_rotation_count", 0)
                all_keys = get_all_api_keys_for_provider(provider_key)
                total_keys = max(len(all_keys), 1)
                max_attempts = total_keys * 2

                print(f"[RR-ROTATION] No-delete provider '{provider_key}' | attempt {rr_count + 1} of {max_attempts} max")

                if rr_count >= max_attempts:
                    print(f"[RR-ROTATION] 2 full cycles completed for '{provider_key}' - failing job")
                    mark_job_failed(job_id, f"All API keys for {provider_key} failed after 2 full rotation cycles. Please try again later.")
                    return

                rotation_success, next_key = handle_roundrobin_rotation(
                    provider_key,
                    error_message,
                    job_id,
                    current_api_key_id=api_key_id,
                )
                log_rotation_attempt(job_id, provider_key, api_key_id,
                                   next_key.get("id") if next_key else None,
                                   error_message, rotation_success)

                if rotation_success and next_key:
                    job["_rr_rotation_count"] = rr_count + 1
                    job["_rotated_api_key"] = next_key.get("api_key")
                    job["_rotated_api_key_id"] = next_key.get("id")
                    job["_rotated_api_key_number"] = next_key.get("key_number")
                    print(f"[RR-ROTATION] Retrying image job with key #{next_key.get('key_number')} (attempt {rr_count + 1}/{max_attempts})...")
                    return process_image_job(job)
                else:
                    print(f"[RR-ROTATION] No keys available for '{provider_key}' - failing job")
                    mark_job_failed(job_id, f"No API keys available for provider: {provider_key}")
                    return

            else:
                # Standard rotation: delete failing key, get next (all other providers unchanged)
                print(f"[ROTATION] Attempting to rotate API key...")
                rotation_success, next_key = handle_api_key_rotation(
                    api_key_id,
                    provider_key,
                    error_message,
                    job_id
                )
                log_rotation_attempt(job_id, provider_key, api_key_id,
                                   next_key.get("id") if next_key else None,
                                   error_message, rotation_success)

                if rotation_success and next_key:
                    print(f"[RETRY] Retrying image job with new API key...")
                    return process_image_job(job)
                else:
                    print(f"[ERROR] API key rotation failed or no keys available")
                    notify_error(
                        ErrorType.API_KEY_ROTATION_FAILED,
                        f"API key rotation failed for {provider_key}",
                        context={"provider": provider_key, "job_id": job_id, "error": error_message}
                    )
                    reset_job_to_pending(job_id, provider_key, f"No API key available for provider: {provider_key}")
                    return

        # Catch-all for any other errors (reset to pending for retry)
        print(f"[UNKNOWN ERROR] Unhandled error type - resetting to PENDING for retry")
        print(f"[UNKNOWN ERROR] Error: {error_message}")
        reset_job_to_pending(job_id, provider_key, error_message)
        return


def reset_running_jobs_to_pending():
    """
    Reset all 'running' jobs to 'pending' on worker startup.
    This handles jobs that were interrupted by worker crash/restart.
    """
    try:
        from supabase_client import supabase
        
        print("\n" + "="*60)
        print("STARTUP: Resetting stale 'running' jobs to 'pending'")
        print("="*60)
        
        running_jobs_response = supabase.table("jobs").select("job_id, user_id, model, job_type").eq("status", "running").execute()
        
        if not running_jobs_response.data or len(running_jobs_response.data) == 0:
            print("No 'running' jobs found - system is clean")
            print("="*60 + "\n")
            return 0
        
        running_jobs = running_jobs_response.data
        print(f"Found {len(running_jobs)} 'running' job(s) - resetting to 'pending'...")
        
        reset_count = 0
        for job in running_jobs:
            job_id = job.get("job_id")
            job_type = job.get("job_type", "image")
            try:
                # Workflow jobs reset to pending_retry so the retry manager resumes
                # them from their last saved checkpoint (current_step in
                # workflow_executions).  Resetting to plain 'pending' would cause
                # process_pending_workflows() to call execute_workflow(resume=False)
                # which restarts from step 0, wasting already-completed API calls.
                new_status = "pending_retry" if job_type == "workflow" else "pending"
                supabase.table("jobs").update({
                    "status": new_status,
                    "progress": 0,
                    "error_message": "Worker restarted - job reset to pending"
                }).eq("job_id", job_id).execute()
                
                print(f"  ✅ Reset job {job_id} ({job_type}) to {new_status}")
                reset_count += 1
            except Exception as update_error:
                print(f"  ❌ Failed to reset job {job_id}: {update_error}")
        
        print(f"\n✅ Successfully reset {reset_count} job(s) to pending")
        
        if reset_count > 0:
            try:
                from job_coordinator import get_job_coordinator
                coordinator = get_job_coordinator()
                coordinator.clear_active_job()
                coordinator.process_next_queued_job()
                print("✅ Cleared stale job_queue_state after worker restart and triggered next queued job")
            except Exception as coord_error:
                print(f"⚠️  Failed to clear job_queue_state: {coord_error}")
        
        print("="*60 + "\n")
        return reset_count
        
    except Exception as e:
        print(f"❌ Error resetting running jobs: {e}")
        notify_error(
            ErrorType.RUNNING_JOBS_RESET_FAILED,
            "Failed to reset running jobs on worker startup - Supabase error",
            context={"error": str(e)[:200]}
        )
        import traceback
        traceback.print_exc()
        print("="*60 + "\n")
        return 0


def validate_job_queue_state_on_startup():
    """
    On startup, check if the active job in job_queue_state is still actually running.
    If the job is completed, failed, cancelled, or pending (already reset), clear the lock.
    This handles cases where the worker restarted after a job finished but before the lock was cleared.
    """
    try:
        from job_coordinator import get_job_coordinator
        from supabase_client import supabase

        print("\n" + "="*60)
        print("STARTUP: Validating job_queue_state...")
        print("="*60)

        coordinator = get_job_coordinator()
        state = coordinator.get_active_job_state()

        # Support both legacy single-slot (active_job_id) and new multi-slot (active_jobs) formats
        active_jobs_list = state.get('active_jobs', []) if state else []
        legacy_job_id    = state.get('active_job_id') if state else None

        # Collect all job IDs currently held in coordinator slots
        slot_job_ids = [s.get('job_id') for s in (active_jobs_list or []) if s.get('job_id')]
        if not slot_job_ids and legacy_job_id:
            slot_job_ids = [legacy_job_id]

        if not slot_job_ids:
            print("job_queue_state is clean (no active jobs)")
            print("="*60 + "\n")
            return

        print(f"Found {len(slot_job_ids)} active slot(s) in queue state: {slot_job_ids}")

        stale_found = False
        for active_job_id in slot_job_ids:
            job_response = supabase.table("jobs").select("job_id, status").eq("job_id", active_job_id).execute()

            if not job_response.data:
                print(f"Job {active_job_id} not found in main DB - releasing stale slot")
                coordinator.release_slot(active_job_id)
                stale_found = True
                continue

            job_status = job_response.data[0].get("status")
            print(f"Job {active_job_id} actual status: {job_status}")

            if job_status in ("completed", "failed", "cancelled", "pending", "pending_retry"):
                print(f"Job {active_job_id} is '{job_status}' - not running. Releasing stale slot...")
                coordinator.release_slot(active_job_id)
                stale_found = True
            else:
                print(f"Job {active_job_id} is still '{job_status}' - will be reset by reset_running_jobs_to_pending()")

        if stale_found:
            coordinator.process_next_queued_job()
            print("✅ Cleared stale slot(s) and triggered next queued job")

        print("="*60 + "\n")

    except Exception as e:
        print(f"⚠️  Error validating job_queue_state on startup: {e}")


def fetch_all_pending_jobs():
    """
    Fetch all pending image/video jobs for backlog catch-up.
    Tries the backend HTTP API first; falls back to a direct Supabase query
    if the backend is unavailable (e.g. race condition during deployment startup).
    """
    try:
        print("Fetching all pending jobs from database...")
        response = requests.get(
            f"{BACKEND_URL}/worker/pending-jobs",
            timeout=10,
            verify=VERIFY_SSL
        )
        
        if response.status_code == 200:
            data = response.json()
            jobs = data.get("jobs", [])
            print(f"Found {len(jobs)} pending job(s) via backend API")
            return jobs
        else:
            print(f"Backend API returned {response.status_code} — falling back to direct DB query")
    except Exception as e:
        print(f"[BACKLOG] Backend API unavailable ({e}) — falling back to direct DB query")

    # Direct Supabase fallback — used when the backend HTTP server is not yet
    # reachable (common at startup when both services start simultaneously).
    # Exclude coordinator-blocked jobs — re-submitting them overwrites queued_at
    # and corrupts FIFO ordering (same issue as Issues #14, #18, #20).
    try:
        from supabase_client import supabase as _sb
        result = _sb.table("jobs")\
            .select("*")\
            .eq("status", "pending")\
            .neq("job_type", "workflow")\
            .is_("blocked_by_job_id", "null")\
            .order("created_at", desc=False)\
            .limit(200)\
            .execute()
        jobs = result.data if result and result.data else []
        print(f"[BACKLOG] Found {len(jobs)} pending job(s) via direct DB fallback")
        return jobs
    except Exception as db_err:
        print(f"[BACKLOG] Direct DB fallback also failed: {db_err}")
        return []


def process_all_pending_jobs():
    print("\n" + "="*60)
    print("BACKLOG CATCH-UP: Processing pending image/video jobs")
    print("="*60)
    
    pending_jobs = fetch_all_pending_jobs()
    
    if not pending_jobs:
        print("No pending image/video jobs in backlog")
        print("="*60 + "\n")
        return
    
    # Filter out workflow jobs (they're handled separately)
    non_workflow_jobs = [job for job in pending_jobs if job.get("job_type") != "workflow"]
    workflow_jobs = [job for job in pending_jobs if job.get("job_type") == "workflow"]
    
    if workflow_jobs:
        print(f"⚠️  Skipping {len(workflow_jobs)} workflow job(s) - handled by workflow manager")
    
    if not non_workflow_jobs:
        print("No pending image/video jobs in backlog")
        print("="*60 + "\n")
        return
    
    print(f"Processing {len(non_workflow_jobs)} pending image/video job(s) with per-provider concurrency...\n")
    
    for idx, job in enumerate(non_workflow_jobs, 1):
        job_id = job.get("job_id")
        job_type = job.get("job_type", "image")
        prompt = job.get("prompt", "")[:50]
        
        print(f"[{idx}/{len(non_workflow_jobs)}] Submitting job {job_id} ({job_type})")
        print(f"   Prompt: {prompt}...")
        
        try:
            job_thread = threading.Thread(
                target=process_job_with_concurrency_control,
                args=(job,),
                daemon=True
            )
            job_thread.start()
            print(f"   Job {job_id} submitted to concurrency-controlled queue\n")
        except Exception as e:
            print(f"   Job {job_id} submission failed: {e}\n")
            continue
        
        # Small stagger: lets the coordinator lock settle between submissions so
        # blocked jobs record their queued_at in order instead of all colliding
        # simultaneously.  100ms × N jobs = negligible overhead for any realistic
        # backlog size.
        time.sleep(0.1)
    
    print("="*60)
    print("Image/video backlog catch-up completed (jobs queued per provider)")
    print("="*60 + "\n")


def process_all_pending_workflow_jobs():
    """Process pending and pending_retry workflow jobs on startup"""
    print("\n" + "="*60)
    print("BACKLOG CATCH-UP: Processing pending workflow jobs")
    print("="*60)
    
    try:
        from workflow_retry_manager import get_retry_manager
        
        retry_manager = get_retry_manager()
        
        # Process both pending and pending_retry workflows
        pending_count = retry_manager.process_pending_workflows()
        retry_count = retry_manager.process_retryable_workflows()
        
        total_count = pending_count + retry_count
        
        if total_count > 0:
            print(f"✅ Processed {total_count} workflow job(s) ({pending_count} pending, {retry_count} retries)")
        else:
            print("No pending workflow jobs in backlog")
        
        print("="*60 + "\n")
        
    except Exception as e:
        print(f"❌ Error processing pending workflow jobs: {e}")
        import traceback
        traceback.print_exc()
        print("="*60 + "\n")


async def realtime_listener():
    """
    Listen for new jobs using PostgreSQL LISTEN/NOTIFY.
    
    This replaces the fragile Supabase Realtime WebSocket with a robust raw TCP
    connection to PostgreSQL. Much more stable on Render because:
      - No WebSocket idle timeout from load balancers
      - No Phoenix relay server closing connections (1001 errors)
      - Native PostgreSQL NOTIFY protocol — direct, instant, reliable
    
    How it works:
      1. A database trigger on `jobs` table fires on INSERT (status='pending')
      2. The trigger calls pg_notify('job_events', job_data)
      3. This asyncpg connection receives the notification instantly
      4. The notification is fed into the SAME on_new_job() callback as before
    
    What is NOT changed:
      - Workflow jobs are still skipped (same job_type check)
      - UPDATE events are still ignored (trigger only fires on INSERT)
      - The _deferred_retry, retry_transient_errors, and coordinator paths are unchanged
      - process_job_with_concurrency_control() is the same entry point
      - system_flags realtime subscription remains (priority lock monitoring)
      - provider_api_keys realtime listener remains (API key rotation)
    """
    import json
    import asyncpg

    try:
        if not DATABASE_URL:
            raise ValueError(
                "DATABASE_URL is not set. "
                "Get it from Supabase Dashboard -> Settings -> Database -> Connection string (Session mode, port 5432). "
                "IMPORTANT: Use port 5432 (Session mode), NOT 6543 (Transaction mode). "
                "pgbouncer in transaction mode does NOT support LISTEN/NOTIFY. "
                "Format: postgresql://postgres.[REF].[REF]:[PASSWORD]@db.[REF].supabase.co:5432/postgres?sslmode=require"
            )

        print(f"[LISTEN/NOTIFY] Connecting to PostgreSQL...")
        # statement_cache_size=0 is required for pgbouncer (Supabase port 5432)
        # which doesn't support prepared statements in transaction pool mode.
        conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)
        print(f"[LISTEN/NOTIFY] Connected successfully")

        # ── Job notification handler ──────────────────────────────────────────
        def handle_job_notification(connection, pid, channel, payload):
            """
            Called by asyncpg when the database sends a NOTIFY on 'job_events' channel.
            Payload is JSON with: job_id, status, job_type, model, user_id, prompt
            """
            try:
                data = json.loads(payload)
                job_id = data.get("job_id")
                job_type = data.get("job_type", "image")
                status = data.get("status")

                # Same workflow check as the old realtime listener — workflow jobs
                # are ALWAYS owned by app.py (/workflows/execute).
                if job_type == "workflow":
                    print(
                        f"[LISTEN/NOTIFY] Workflow job {job_id} — "
                        f"execution owned by app.py, skipping worker dispatch"
                    )
                    sys.stdout.flush()
                    return

                # Build the same record format that the old realtime callback used.
                # This is fed directly into on_new_job() which calls process_job().
                record = {
                    "job_id": job_id,
                    "status": status,
                    "job_type": job_type,
                    "model": data.get("model"),
                    "user_id": data.get("user_id"),
                    "prompt": data.get("prompt"),
                }

                print(f"\n{'='*70}")
                print(f"NEW JOB RECEIVED VIA LISTEN/NOTIFY!")
                print(f"{'='*70}")
                print(f"Job ID: {job_id}")
                print(f"Type: {job_type}")
                print(f"Prompt: {record.get('prompt', '')[:50]}...")
                print(f"{'='*70}\n")
                sys.stdout.flush()

                def process_in_thread():
                    try:
                        process_job_with_concurrency_control(record)

                        print(f"\n{'='*70}")
                        print(f"LISTEN/NOTIFY JOB COMPLETED: {job_id}")
                        print(f"{'='*70}\n")
                        sys.stdout.flush()
                    except Exception as thread_err:
                        print(f"\n{'='*70}")
                        print(f"ERROR PROCESSING JOB IN THREAD")
                        print(f"{'='*70}")
                        print(f"Error: {thread_err}")

                        metadata = record.get("metadata", {}) or {}
                        provider_key = metadata.get("provider_key") or record.get("provider_key")

                        notify_error(
                            ErrorType.JOB_THREAD_CRASHED,
                            f"Job processing thread crashed for job {job_id}",
                            context={"job_id": job_id, "provider": provider_key, "error": str(thread_err)[:200]}
                        )

                        import traceback
                        traceback.print_exc()
                        print(f"{'='*70}\n")
                        sys.stdout.flush()

                        reset_job_to_pending(job_id, provider_key, f"Thread error: {str(thread_err)}")

                job_thread = threading.Thread(target=process_in_thread, daemon=True)
                job_thread.start()
                print(f"Job processing started in background thread")
                sys.stdout.flush()

            except Exception as e:
                print(f"\n{'='*70}")
                print(f"ERROR IN LISTEN/NOTIFY CALLBACK")
                print(f"{'='*70}")
                print(f"Error: {e}")
                import traceback
                traceback.print_exc()
                print(f"{'='*70}\n")
                sys.stdout.flush()

        # Register the listener on the 'job_events' channel
        await conn.add_listener('job_events', handle_job_notification)

        # ── Keep system_flags realtime subscription for priority lock ───────
        # This stays as Supabase Realtime WebSocket (separate channel, separate purpose).
        # Priority lock changes are infrequent, so WebSocket instability here is not critical.
        try:
            from supabase import acreate_client
            async_client = await acreate_client(SUPABASE_URL, SUPABASE_KEY)

            def handle_flag_change(payload):
                global _priority_lock_active
                try:
                    data = payload.get("data", {})
                    record = data.get("record", payload.get("new", payload.get("record", {})))

                    if not record or record.get("key") != "priority_lock":
                        return

                    new_value = record.get("value", False)
                    old_value = _priority_lock_active
                    _priority_lock_active = new_value

                    if old_value and not new_value:
                        print("\n" + "=" * 60)
                        print("[PRIORITY LOCK] Lock DISABLED via remote script")
                        print("[PRIORITY LOCK] Flushing pending P2/P3 jobs now...")
                        print("=" * 60 + "\n")
                        sys.stdout.flush()
                        threading.Thread(target=process_all_pending_jobs, daemon=True, name="PriorityLockFlush").start()
                    elif not old_value and new_value:
                        print("\n" + "=" * 60)
                        print("[PRIORITY LOCK] Lock ENABLED via remote script")
                        print("[PRIORITY LOCK] Only Priority 1 jobs will be processed")
                        print("=" * 60 + "\n")
                        sys.stdout.flush()

                except Exception as e:
                    print(f"[PRIORITY LOCK] Error handling flag change: {e}")

            flags_channel = async_client.channel("system-flags-watcher")
            await flags_channel.on_postgres_changes(
                event="UPDATE",
                schema="public",
                table="system_flags",
                callback=handle_flag_change
            ).subscribe()
            print("[LISTEN/NOTIFY] Subscribed to system_flags UPDATE (priority lock via Realtime)")
        except Exception as flag_err:
            print(f"[LISTEN/NOTIFY] Warning: system_flags subscription failed ({flag_err})")
            print(f"[LISTEN/NOTIFY] Priority lock will be checked at operation time instead")

        # NOTE: add_listener() automatically issues the LISTEN command internally.
        # Do NOT call conn.execute("LISTEN ...") — it conflicts with pgbouncer.

        print()
        print("=" * 60)
        print("LISTENING FOR NEW JOBS (PostgreSQL LISTEN/NOTIFY)")
        print("=" * 60)
        print("Jobs are delivered via native PostgreSQL NOTIFY.")
        print("This is more stable than Realtime WebSocket on Render.")
        print()
        print("NOTE: If events don't arrive, verify the trigger exists:")
        print("   SELECT trigger_name FROM information_schema.triggers WHERE trigger_name = 'job_insert_notify';")
        print("=" * 60)
        print()
        sys.stdout.flush()

        # Keep the connection alive — asyncpg handles heartbeats internally
        while True:
            await asyncio.sleep(1)

    except Exception as e:
        print(f"Realtime listener error: {e}")
        notify_error(
            ErrorType.REALTIME_LISTENER_CRASHED,
            f"LISTEN/NOTIFY listener crashed - no new jobs being processed",
            context={"error": str(e)[:200]}
        )
        import traceback
        traceback.print_exc()


def fetch_pending_jobs_for_provider(provider_key: str) -> list:
    """
    Query ALL pending image/video jobs (not workflow) for a specific provider.
    Matches by explicit provider_key in metadata OR by model-to-provider mapping.

    Args:
        provider_key: Provider identifier (e.g., 'vision-nova', 'cinematic-nova')

    Returns:
        List of non-workflow job records that need to be reprocessed
    """
    try:
        from supabase_client import supabase

        print(f"[API_KEY_INSERT] Querying pending image/video jobs for provider: {provider_key}")

        # Exclude coordinator-blocked jobs (blocked_by_job_id IS NOT NULL).
        # Those are queued behind another running job and must be triggered by
        # coordinator.process_next_queued_job() — not by the key-insertion handler.
        # Re-triggering them here causes a redundant coordinator block and
        # incorrect queued_at overwrite.
        response = supabase.table("jobs")\
            .select("*")\
            .eq("status", "pending")\
            .neq("job_type", "workflow")\
            .is_("blocked_by_job_id", "null")\
            .execute()

        if not response.data:
            print(f"[API_KEY_INSERT] No pending image/video jobs found")
            return []

        _api_key_keywords = ['no api key', 'api key', 'authentication', 'unauthorized', 'invalid key', 'no key']

        # Jobs with no error message are included only if they were created recently
        # (within 30 min).  Old no-error pending jobs are likely stuck for unrelated
        # reasons and should not be re-triggered on every key insertion event.
        from datetime import timedelta
        recent_cutoff = (datetime.utcnow() - timedelta(minutes=30)).isoformat()

        matching_jobs = []
        for job in response.data:
            error_msg = (job.get("error_message") or "").lower()
            if error_msg:
                if not any(k in error_msg for k in _api_key_keywords):
                    continue  # has a non-key error — skip
            else:
                # No error message yet — only include if the job is recent
                if (job.get("created_at") or "") < recent_cutoff:
                    continue

            metadata = job.get("metadata", {}) or {}
            job_provider_key = metadata.get("provider_key") or job.get("provider_key")

            if job_provider_key == provider_key:
                matching_jobs.append(job)
                continue

            if not job_provider_key:
                model = job.get("model", "")
                job_type = job.get("job_type", "image")
                if map_model_to_provider(model, job_type) == provider_key:
                    matching_jobs.append(job)

        print(f"[API_KEY_INSERT] Found {len(matching_jobs)} pending image/video job(s) for provider {provider_key}")
        return matching_jobs

    except Exception as e:
        print(f"[API_KEY_INSERT] Error querying pending jobs: {e}")
        import traceback
        traceback.print_exc()
        return []


def fetch_pending_retry_workflow_jobs_for_provider(provider_key: str) -> list:
    """
    Query pending_retry workflow jobs where the CURRENT FAILING STEP uses a model
    from the given provider.

    Checks execution.error_info.provider first (set when the step failed).
    Falls back to mapping execution.error_info.model -> provider if provider not recorded.

    Args:
        provider_key: Provider identifier (e.g., 'vision-nova', 'cinematic-nova')

    Returns:
        List of workflow job records with their _execution attached
    """
    try:
        from supabase_client import supabase

        print(f"[API_KEY_INSERT] Querying pending_retry workflow jobs for provider: {provider_key}")

        # Exclude coordinator-blocked workflow jobs — they are exclusively handled
        # by coordinator.process_next_queued_job() when their blocker finishes.
        jobs_response = supabase.table("jobs")\
            .select("*")\
            .eq("status", "pending_retry")\
            .eq("job_type", "workflow")\
            .is_("blocked_by_job_id", "null")\
            .execute()

        jobs = jobs_response.data if jobs_response.data else []

        if not jobs:
            print(f"[API_KEY_INSERT] No pending_retry workflow jobs found")
            return []

        job_ids = [job["job_id"] for job in jobs]
        executions_response = supabase.table("workflow_executions")\
            .select("*")\
            .in_("job_id", job_ids)\
            .execute()

        executions_map = {ex["job_id"]: ex for ex in (executions_response.data or [])}

        matching_jobs = []
        for job in jobs:
            job_id = job["job_id"]
            execution = executions_map.get(job_id)

            if not execution:
                print(f"[API_KEY_INSERT] Workflow job {job_id} has no execution record, skipping")
                continue

            error_info = execution.get("error_info", {}) or {}

            failing_provider = error_info.get("provider")
            if failing_provider:
                if failing_provider == provider_key:
                    job["_execution"] = execution
                    matching_jobs.append(job)
                continue

            failing_model = error_info.get("model")
            if failing_model:
                if (map_model_to_provider(failing_model, "image") == provider_key or
                        map_model_to_provider(failing_model, "video") == provider_key):
                    job["_execution"] = execution
                    matching_jobs.append(job)

        print(f"[API_KEY_INSERT] Found {len(matching_jobs)} pending_retry workflow job(s) for provider {provider_key}")
        return matching_jobs

    except Exception as e:
        print(f"[API_KEY_INSERT] Error querying pending_retry workflow jobs: {e}")
        import traceback
        traceback.print_exc()
        return []


def fetch_pending_workflow_jobs_for_provider(provider_key: str) -> list:
    """
    Query status=pending workflow jobs that have a key-error message and whose
    model maps to provider_key.  These are missed by:
      - fetch_pending_jobs_for_provider()  (explicitly excludes workflow jobs)
      - fetch_pending_retry_workflow_jobs_for_provider()  (requires pending_retry status)
      - retry_stale_pending_workflows()  (explicitly skips key-error jobs)
    They will only start when a matching key is inserted, which is what this
    function enables.
    """
    try:
        from supabase_client import supabase as _sb

        print(f"[API_KEY_INSERT] Querying pending workflow jobs (key-error) for provider: {provider_key}")

        # Exclude coordinator-blocked jobs — they must only be triggered by
        # coordinator.process_next_queued_job() when their blocker finishes.
        # Re-triggering them here causes execute_workflow() → on_job_start() to
        # re-block them, overwriting queued_at and corrupting FIFO ordering.
        response = _sb.table("jobs")\
            .select("*")\
            .eq("status", "pending")\
            .eq("job_type", "workflow")\
            .is_("blocked_by_job_id", "null")\
            .execute()

        jobs = response.data if response.data else []

        _key_markers = ('no api key', 'invalid key', 'api key rotation failed',
                        'authentication', 'unauthorized')

        matching = []
        for job in jobs:
            error_msg = (job.get("error_message") or "").lower()
            if not any(k in error_msg for k in _key_markers):
                continue
            model = job.get("model", "")
            if (map_model_to_provider(model, "image") == provider_key or
                    map_model_to_provider(model, "video") == provider_key):
                matching.append(job)

        print(f"[API_KEY_INSERT] Found {len(matching)} pending workflow job(s) with key-error for {provider_key}")
        return matching

    except Exception as e:
        print(f"[API_KEY_INSERT] Error querying pending workflow jobs: {e}")
        import traceback
        traceback.print_exc()
        return []


def handle_api_key_insertion(payload):
    """
    Handle API key insertion/update event from Worker1 database.
    When a new API key is inserted or updated, automatically reprocess pending jobs
    that were waiting for this provider's API key.
    
    Args:
        payload: Realtime event payload from provider_api_keys table
    """
    try:
        event_type = payload.get("eventType", "INSERT")
        
        print("\n" + "="*70)
        print(f"API KEY {event_type} EVENT DETECTED!")
        print("="*70)
        
        data = payload.get("data", {})
        record = data.get("record", payload.get("new", payload.get("record", {})))
        
        if not record:
            print(f"No record found in payload: {payload}")
            print("="*70 + "\n")
            return
        
        provider_id = record.get("provider_id")
        key_number = record.get("key_number", "?")
        
        if not provider_id:
            print(f"No provider_id in record")
            print("="*70 + "\n")
            return
        
        print(f"Provider ID: {provider_id}")
        print(f"Key Number: #{key_number}")
        
        worker1_client = get_worker1_client()
        if not worker1_client:
            print("[ERROR] Worker1 client not available")
            notify_error(
                ErrorType.WORKER1_CLIENT_UNAVAILABLE,
                "Worker1 database client not available - cannot query API keys",
                context={"event": "API_KEY_INSERT"}
            )
            print("="*70 + "\n")
            return
        
        provider_result = worker1_client.table("providers")\
            .select("provider_name")\
            .eq("id", provider_id)\
            .limit(1)\
            .execute()
        
        if not provider_result.data:
            print(f"[ERROR] Provider not found for ID: {provider_id}")
            notify_error(
                ErrorType.PROVIDER_NOT_FOUND,
                f"Provider not found in Worker1 database",
                context={"provider_id": provider_id, "event": "API_KEY_INSERT"}
            )
            print("="*70 + "\n")
            return
        
        provider_key = provider_result.data[0]["provider_name"]
        print(f"Provider Name: {provider_key}")
        print("="*70 + "\n")

        pending_jobs = fetch_pending_jobs_for_provider(provider_key)
        workflow_jobs = fetch_pending_retry_workflow_jobs_for_provider(provider_key)
        pending_workflow_jobs = fetch_pending_workflow_jobs_for_provider(provider_key)

        total = len(pending_jobs) + len(workflow_jobs) + len(pending_workflow_jobs)

        if total == 0:
            print(f"[API_KEY_INSERT] No jobs to reprocess for {provider_key}")
            return

        # --- Image / Video jobs (status=pending) ---
        if pending_jobs:
            print(f"\n{'='*70}")
            print(f"REPROCESSING {len(pending_jobs)} PENDING IMAGE/VIDEO JOB(S) FOR {provider_key}")
            print(f"{'='*70}\n")

            for idx, job in enumerate(pending_jobs, 1):
                job_id = job.get("job_id") or job.get("id")
                job_type = job.get("job_type", "image")
                prompt = job.get("prompt", "")[:50]

                print(f"[{idx}/{len(pending_jobs)}] Reprocessing {job_type} job {job_id}")
                print(f"   Prompt: {prompt}...")

                try:
                    from supabase_client import supabase as _sb_check
                    _status_check = _sb_check.table("jobs").select("status").eq("job_id", job_id).single().execute()
                    if _status_check.data:
                        _current = _status_check.data.get("status")
                        if _current not in ("pending",):
                            print(f"   ⏭ Job {job_id} status is '{_current}' — already being processed, skipping\n")
                            continue
                except Exception as _sc_err:
                    print(f"   [WARN] Could not verify status for {job_id}: {_sc_err} — proceeding anyway")

                try:
                    job_thread = threading.Thread(
                        target=process_job_with_concurrency_control,
                        args=(job,),
                        daemon=True
                    )
                    job_thread.start()

                    print(f"   ✅ Job {job_id} submitted for reprocessing\n")
                except Exception as e:
                    print(f"   ❌ Job {job_id} reprocessing failed: {e}\n")
                    reset_job_to_pending(job_id, provider_key, f"Reprocessing failed: {str(e)}")
                    continue

        # --- Workflow jobs (status=pending_retry) ---
        if workflow_jobs:
            print(f"\n{'='*70}")
            print(f"REPROCESSING {len(workflow_jobs)} PENDING_RETRY WORKFLOW JOB(S) FOR {provider_key}")
            print(f"{'='*70}\n")

            from workflow_retry_manager import get_retry_manager
            retry_manager = get_retry_manager()

            for idx, job in enumerate(workflow_jobs, 1):
                job_id = job.get("job_id") or job.get("id")
                execution = job.get("_execution", {})
                execution_id = execution.get("id")
                error_info = execution.get("error_info", {}) or {}
                failing_step = error_info.get("failed_step_index", error_info.get("step_index", "?"))

                print(f"[{idx}/{len(workflow_jobs)}] Resuming workflow job {job_id} "
                      f"(failing step: {failing_step}, provider: {provider_key})")

                if not execution_id:
                    print(f"   ❌ No execution_id for workflow job {job_id}, skipping\n")
                    continue

                if not validate_job_inputs(job):
                    print(f"   ❌ Workflow job {job_id} missing required inputs — marked as failed, skipping\n")
                    continue

                # Atomic claim — prevents duplicate execution if periodic retry loop
                # fires at the same time as this API key insertion handler.
                from supabase_client import supabase as _sb_claim
                claim = _sb_claim.table('jobs').update({'status': 'running'}) \
                    .eq('job_id', job_id).eq('status', 'pending_retry').execute()
                if not claim.data:
                    print(f"   ⚠️ Workflow job {job_id} already claimed by another process — skipping\n")
                    continue

                try:
                    def resume_workflow(exec_id=execution_id, jid=job_id):
                        import asyncio as _asyncio
                        loop = _asyncio.new_event_loop()
                        _asyncio.set_event_loop(loop)
                        try:
                            loop.run_until_complete(
                                retry_manager._resume_workflow(exec_id, jid)
                            )
                        finally:
                            loop.close()

                    from workflow_retry_manager import _spawn_workflow_thread
                    _spawn_workflow_thread(resume_workflow, name=f"KeyInsertResumeWF-{job_id}")

                    print(f"   ✅ Workflow job {job_id} submitted for resume\n")
                except Exception as e:
                    print(f"   ❌ Workflow job {job_id} resume failed: {e}\n")
                    continue

        # --- Workflow jobs (status=pending) with key errors ---
        if pending_workflow_jobs:
            print(f"\n{'='*70}")
            print(f"REPROCESSING {len(pending_workflow_jobs)} PENDING WORKFLOW JOB(S) "
                  f"WITH KEY-ERROR FOR {provider_key}")
            print(f"{'='*70}\n")

            for idx, job in enumerate(pending_workflow_jobs, 1):
                job_id = job.get("job_id") or job.get("id")
                workflow_id = job.get("model")
                user_id = job.get("user_id")
                meta = job.get("metadata", {}) or {}
                image_url = job.get("image_url") or meta.get("input_image_url")

                print(f"[{idx}/{len(pending_workflow_jobs)}] Re-triggering pending workflow {job_id}")

                if not validate_job_inputs(job):
                    print(f"   ❌ Workflow job {job_id} missing required inputs — marked as failed, skipping\n")
                    continue

                try:
                    def _start_fresh_wf(wf_id=workflow_id, img=image_url, uid=user_id, jid=job_id):
                        import asyncio as _asyncio
                        from workflow_manager import get_workflow_manager
                        loop = _asyncio.new_event_loop()
                        _asyncio.set_event_loop(loop)
                        try:
                            wm = get_workflow_manager()
                            loop.run_until_complete(wm.execute_workflow(
                                workflow_id=wf_id,
                                input_data=img,
                                user_id=uid,
                                job_id=jid
                            ))
                        except Exception as _e:
                            print(f"   [WORKFLOW] Fresh re-trigger failed for {jid}: {_e}")
                        finally:
                            loop.close()

                    from workflow_retry_manager import _spawn_workflow_thread
                    _spawn_workflow_thread(_start_fresh_wf, name=f"KeyInsertFreshWF-{job_id}")
                    print(f"   ✅ Pending workflow job {job_id} submitted for fresh execution\n")
                except Exception as e:
                    print(f"   ❌ Pending workflow job {job_id} re-trigger failed: {e}\n")
                    continue

        print(f"{'='*70}")
        print(f"REPROCESSING COMPLETED FOR {provider_key} "
              f"({len(pending_jobs)} image/video, {len(workflow_jobs)} pending_retry workflow, "
              f"{len(pending_workflow_jobs)} pending workflow)")
        print(f"{'='*70}\n")
        
    except Exception as e:
        print(f"[ERROR] Error handling API key insertion: {e}")
        import traceback
        traceback.print_exc()


async def api_key_realtime_listener():
    """
    Listen to INSERT/UPDATE events on provider_api_keys table in Worker1 database.
    When a new API key is inserted or updated, automatically reprocess pending jobs
    that were waiting for that provider's key.
    """
    from supabase import acreate_client
    
    try:
        worker1_url = os.getenv("WORKER_1_URL")
        worker1_key = os.getenv("WORKER_1_SERVICE_ROLE_KEY")
        
        if not worker1_url or not worker1_key:
            print("[WARN] Worker1 credentials not configured. API key monitoring disabled.")
            return
        
        print("="*60)
        print("Connecting to Worker1 Realtime for API key monitoring...")
        print("="*60)
        
        async_worker1_client = await acreate_client(worker1_url, worker1_key)
        
        channel = async_worker1_client.channel("provider-api-keys-monitor")
        
        await channel.on_postgres_changes(
            event="*",
            schema="public",
            table="provider_api_keys",
            callback=lambda payload: threading.Thread(
                target=handle_api_key_insertion,
                args=(payload,),
                daemon=True
            ).start()
        ).subscribe()
        
        print("✅ Subscribed to provider_api_keys INSERT/UPDATE events")
        print("   Monitoring for API key insertions and updates...")
        print("="*60 + "\n")
        sys.stdout.flush()
        
        while True:
            await asyncio.sleep(1)
        
    except Exception as e:
        print(f"[ERROR] API key realtime listener error: {e}")
        notify_error(
            ErrorType.API_KEY_LISTENER_ERROR,
            "API key realtime listener crashed - won't auto-process pending jobs",
            context={"error": str(e)[:200]}
        )
        import traceback
        traceback.print_exc()


async def _run_with_reconnect(listener_fn, name: str):
    """
    Run an async listener function with automatic exponential-backoff reconnection.
    If the listener crashes (network drop, Supabase maintenance, etc.) it is
    restarted after a short delay so that realtime events are never permanently
    missed for the lifetime of the worker process.
    """
    delay = 5
    max_delay = 60
    while True:
        _started_at = asyncio.get_event_loop().time()
        try:
            await listener_fn()
            print(f"[RECONNECT] {name} exited cleanly, restarting in {delay}s...")
        except Exception as e:
            print(f"[RECONNECT] {name} crashed: {e} — restarting in {delay}s...")
            notify_error(
                ErrorType.REALTIME_LISTENER_CRASHED,
                f"{name} crashed and will reconnect",
                context={"error": str(e)[:200], "retry_delay": delay}
            )
        _ran_for = asyncio.get_event_loop().time() - _started_at
        if _ran_for >= max_delay:
            delay = 5
        else:
            delay = min(delay * 2, max_delay)
        await asyncio.sleep(delay)


def run_async_listener():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    async def run_both_listeners():
        await asyncio.gather(
            _run_with_reconnect(realtime_listener, "RealtimeJobListener"),
            _run_with_reconnect(api_key_realtime_listener, "ApiKeyRealtimeListener")
        )
    
    loop.run_until_complete(run_both_listeners())


def worker_startup_tasks():
    """
    Background worker for startup tasks (non-blocking)
    Handles DB cleanup and backlog processing while Flask serves health checks
    """
    global worker_status
    
    try:
        print("\n" + "="*60)
        print("[STARTUP] Running background initialization...")
        print("="*60)
        
        # Load priority lock state from Supabase
        _load_priority_lock_from_db()

        # Validate job_queue_state - clear if active job is already done/reset
        validate_job_queue_state_on_startup()

        # Reset stale running jobs
        reset_running_jobs_to_pending()
        
        # Process backlog
        print("\n" + "="*60)
        print("WORKER STARTUP: Initial backlog catch-up")
        print("="*60)
        process_all_pending_jobs()
        process_all_pending_workflow_jobs()
        print("Initial backlog processed!\n")
        
        # Start workflow retry manager for periodic retry of pending_retry workflows
        # This runs every 5 minutes and handles workflow jobs with pending_retry status
        from workflow_retry_manager import start_retry_manager
        start_retry_manager()
        print("[STARTUP] Workflow retry manager started (5-min periodic loop)\n")
        
        worker_status["backlog_processed"] = True
        worker_status["startup_complete"] = True
        worker_status["ready"] = True
        
        print("=" * 60)
        print("JOB WORKER READY")
        print("=" * 60)
        print("Switching to REALTIME mode (no more polling)")
        print("Will receive instant notifications for new jobs")
        print("Periodic retry: every 10 min for normal jobs, 5 min for workflows")
        print("=" * 60)
        print()
        sys.stdout.flush()
        
    except Exception as e:
        print(f"[STARTUP] Startup tasks failed: {e}")
        print("[STARTUP] Worker will continue but may miss backlog jobs")
        worker_status["startup_complete"] = True
        worker_status["ready"] = True
        import traceback
        traceback.print_exc()


def start_realtime():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Missing Supabase credentials!")
        print("Set SUPABASE_URL and SUPABASE_ANON_KEY in .env")
        sys.exit(1)
    
    global worker_status
    worker_status["running"] = True
    worker_status["started_at"] = datetime.now().isoformat()
    
    # Start realtime listener immediately
    realtime_thread = threading.Thread(target=run_async_listener, daemon=True, name="RealtimeListener")
    realtime_thread.start()
    print("[REALTIME] Listener thread started")
    
    # Start startup tasks in background (non-blocking)
    startup_thread = threading.Thread(target=worker_startup_tasks, daemon=True, name="StartupTasks")
    startup_thread.start()
    print("[STARTUP] Background tasks thread started (non-blocking)")
    
    print("\nWorker heartbeat every 30 seconds...")
    print("   Press Ctrl+C to stop")
    print()
    sys.stdout.flush()
    
    try:
        last_heartbeat = time.time()
        last_retry_check = time.time()
        RETRY_INTERVAL = 600  # 10 minutes - retry pending jobs with transient errors
        
        while True:
            time.sleep(5)
            
            # Heartbeat every 30 seconds
            if time.time() - last_heartbeat >= 30:
                worker_status["last_heartbeat"] = datetime.now().isoformat()
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Worker alive, listening for jobs...")
                sys.stdout.flush()
                last_heartbeat = time.time()
            
            # Retry pending jobs with transient errors every 10 minutes
            if time.time() - last_retry_check >= RETRY_INTERVAL:
                print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Running periodic retry check...")
                retry_transient_errors()
                last_retry_check = time.time()
                
    except KeyboardInterrupt:
        print("\n\nWorker stopped by user (Ctrl+C)")
        sys.exit(0)

if __name__ == "__main__":
    # Use PORT from environment (Koyeb) or default to 5000
    port = int(os.getenv("PORT", 5000))
    
    # Start job worker in background thread
    worker_thread = threading.Thread(target=start_realtime, daemon=True, name="JobWorker")
    worker_thread.start()
    print("[FLASK] Worker thread started in background")
    
    # Start Flask HTTP server for health checks (foreground - Koyeb requirement)
    print("\n" + "="*60)
    print(f"🚀 STARTING FLASK HEALTH CHECK SERVER ON PORT {port}")
    print("="*60)
    print(f"Health endpoint: http://0.0.0.0:{port}/health")
    print("="*60 + "\n")
    
    # Flask runs in main thread - keeps process alive and responds to Koyeb health checks
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
