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
from flask import Flask, jsonify
from dotenv_vault import load_dotenv
from postgrest.exceptions import APIError
from multi_endpoint_manager import generate, get_endpoint_type
from provider_api_keys import get_api_key_for_job, increment_usage_count, get_worker1_client, map_model_to_provider
from api_key_rotation import handle_api_key_rotation, log_rotation_attempt
from error_notifier import notify_error, ErrorType
from model_quota_manager import ensure_quota_manager_started, get_quota_manager
from cloudinary_manager import get_cloudinary_manager

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
    if provider_active_jobs.get(provider_key) == job_id:
        provider_active_jobs[provider_key] = None
        print(f"[CONCURRENCY] Provider {provider_key} now FREE")
        process_next_queued_job(provider_key)
    else:
        print(f"[CONCURRENCY] Warning: Job {job_id} tried to free {provider_key} but it's not the active job")

def enqueue_job(provider_key, job):
    """Add job to provider's queue"""
    if provider_key not in provider_job_queues:
        provider_job_queues[provider_key] = []
    
    provider_job_queues[provider_key].append(job)
    queue_length = len(provider_job_queues[provider_key])
    job_id = job.get("job_id") or job.get("id")
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
    except Exception as e:
        print(f"[FAIL] Exception while marking job {job_id} as failed: {e}")
        return False


def reset_job_to_pending(job_id, provider_key, error_message):
    """
    Mark a job as pending in the database so it can be retried.
    Handles edge cases where provider_key is None or reset fails.
    """
    if not job_id:
        print(f"[RESET] Cannot reset job: job_id is missing")
        return False
    
    print(f"[RESET] Marking job {job_id} as pending...")
    
    try:
        payload = {
            "message": error_message or "Unknown error",
            "provider_key": provider_key or "unknown"
        }
        
        response = requests.post(
            f"{BACKEND_URL}/worker/job/{job_id}/reset",
            json=payload,
            timeout=10,
            verify=VERIFY_SSL
        )
        
        if response.status_code == 200:
            print(f"[RESET] Job {job_id} successfully marked as pending")
            return True
        else:
            error_text = response.text[:200] if response.text else "No response body"
            print(f"[RESET] Failed to reset job {job_id}: {response.status_code} - {error_text}")
            return False
            
    except requests.exceptions.Timeout:
        print(f"[ERROR] Timeout while resetting job {job_id}")
        return False
    except requests.exceptions.RequestException as req_error:
        print(f"[ERROR] Network error while resetting job {job_id}: {req_error}")
        return False
    except Exception as reset_error:
        print(f"[ERROR] Unexpected error while resetting job {job_id}: {reset_error}")
        import traceback
        traceback.print_exc()
        return False


def retry_transient_errors():
    """
    Retry pending jobs that failed with transient errors (Cloudinary, network, timeout, or no API key).
    Called periodically by the worker main loop.
    """
    try:
        print("[RETRY] Checking for pending jobs with transient errors...")
        
        supabase = get_worker1_client()
        if not supabase:
            print("[RETRY] No Supabase client available")
            return
        
        # Query pending jobs with error messages
        try:
            result = supabase.table("image_generation_requests") \
                .select("id, model, error_message, created_at") \
                .eq("status", "pending") \
                .not_.is_("error_message", "null") \
                .order("created_at", desc=False) \
                .limit(50) \
                .execute()
        except APIError as api_err:
            error_data = api_err.args[0] if api_err.args else {}
            if isinstance(error_data, dict):
                error_code = error_data.get('code', '')
                if error_code == 'PGRST205':
                    print(f"[RETRY] Table not found in schema cache, skipping retry check")
                    return
            raise
        
        jobs = result.data if result and hasattr(result, 'data') else []
        
        if not jobs:
            print("[RETRY] No pending jobs with errors found")
            return
        
        print(f"[RETRY] Found {len(jobs)} pending jobs with errors")
        
        retryable_count = 0
        for job in jobs:
            error_msg = (job.get("error_message") or "").lower()
            
            # Check if this is a transient error that should be retried
            is_transient = (
                "cloudinary" in error_msg or
                "timeout" in error_msg or
                "timed out" in error_msg or
                "connection" in error_msg or
                "network" in error_msg or
                "no api key available" in error_msg or
                "httpsconnectionpool" in error_msg or
                "unreachable" in error_msg
            )
            
            if is_transient:
                retryable_count += 1
                print(f"[RETRY] Retrying job {job['id']} ({job.get('model')}) - Error: {job.get('error_message', 'Unknown')[:80]}")
                
                # Fetch full job data and trigger processing
                try:
                    full_job_result = supabase.table("image_generation_requests") \
                        .select("*") \
                        .eq("id", job["id"]) \
                        .single() \
                        .execute()
                    
                    if full_job_result and hasattr(full_job_result, 'data') and full_job_result.data:
                        on_new_job({"record": full_job_result.data})
                    else:
                        print(f"[RETRY] Could not fetch full job data for {job['id']}")
                except APIError as api_err:
                    error_data = api_err.args[0] if api_err.args else {}
                    error_msg = error_data.get('message', str(error_data)) if isinstance(error_data, dict) else str(error_data)
                    print(f"[RETRY] API error fetching job {job['id']}: {error_msg}")
        
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
    Wrapper for process_job that enforces per-provider concurrency limits
    - Checks if provider is busy
    - Queues job if busy
    - Processes immediately if available
    """
    job_id = job.get("job_id") or job.get("id")
    
    # Check maintenance mode
    from pathlib import Path
    maintenance_flag = Path(__file__).parent / ".maintenance_mode"
    if maintenance_flag.exists():
        print(f"[MAINTENANCE] Skipping job {job_id} - maintenance mode active")
        return None
    
    # Determine provider
    metadata = job.get("metadata", {})
    provider_key = metadata.get("provider_key") or job.get("provider_key")
    model = job.get("model", "")
    
    if not provider_key:
        job_type = job.get("job_type", "image")
        video_indicators = ["video", "wan", "minimax", "luma", "topaz"]
        if job_type == "image" and any(v in model.lower() for v in video_indicators):
            job_type = "video"
        provider_key = map_model_to_provider(model, job_type=job_type)
    
    lock = get_provider_lock(provider_key)
    
    with lock:
        if is_provider_busy(provider_key):
            print(f"[CONCURRENCY] Provider {provider_key} is BUSY, queueing job {job_id}")
            enqueue_job(provider_key, job)
            return None
        else:
            mark_provider_busy(provider_key, job_id)
    
    try:
        return process_job(job)
    finally:
        mark_provider_free(provider_key, job_id)


def process_job(job):
    job_id = job.get("job_id") or job.get("id")
    
    # Check maintenance mode - skip pending jobs
    from pathlib import Path
    maintenance_flag = Path(__file__).parent / ".maintenance_mode"
    if maintenance_flag.exists():
        print(f"[MAINTENANCE] Skipping job {job_id} - maintenance mode active")
        return None
    
    job_type = job.get("job_type", "image")
    model = job.get("model", "")
    
    metadata = job.get("metadata", {})
    provider_key = metadata.get("provider_key") or job.get("provider_key")
    
    if not provider_key:
        provider_key = map_model_to_provider(model, job_type=job_type)
    
    quota_manager = get_quota_manager()
    if not quota_manager.check_quota_available(provider_key, model):
        error_msg = f"QUOTA_EXCEEDED:{provider_key}:{model}"
        print(f"[QUOTA] Model quota exceeded for {provider_key}:{model}")
        mark_job_failed(job_id, error_msg)
        return None
    
    video_indicators = ["video", "wan", "minimax", "luma", "topaz"]
    if job_type == "image" and any(v in model.lower() for v in video_indicators):
        job_type = "video"
        print(f"Detected VIDEO job based on model: {model}")
    
    print(f"\n{'='*60}")
    print(f"PROCESSING {job_type.upper()} JOB")
    print(f"{'='*60}")
    print(f"Job ID: {job_id}")
    
    metadata = job.get("metadata", {})
    provider_key = metadata.get("provider_key") or job.get("provider_key")
    
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
        provider_key = metadata.get("provider_key") or job.get("provider_key")
        
        job_model = job.get("model", "minimax/video-01")
        
        if not provider_key:
            provider_key = map_model_to_provider(job_model, job_type="video")
            print(f"Determined provider from model: {provider_key}")
        
        # vision-xeven is a FREE API that doesn't require an API key (image only)
        if provider_key == "vision-xeven":
            print(f"Using FREE Xeven API - no API key required")
            provider_api_key = None
            api_key_id = None
        else:
            api_key_data = get_api_key_for_job(job_model, provider_key, job_type="video")
            
            provider_api_key = None
            
            if api_key_data:
                api_key_id = api_key_data.get("id")
                provider_api_key = api_key_data.get("api_key")
                if api_key_data.get("provider_key"):
                    provider_key = api_key_data.get("provider_key")
                print(f"Using API key from provider: {provider_key}")
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
        
        requests.post(
            f"{BACKEND_URL}/worker/job/{job_id}/complete",
            json={"image_url": final_url, "video_url": final_url, "success": True},
            timeout=10,
            verify=VERIFY_SSL
        )
        
        if api_key_id:
            increment_usage_count(api_key_id)
        
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
        
        print(f"Video job {job_id} completed successfully!")
        worker_status["jobs_processed"] += 1
        
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
        
        is_cloudinary_error = "cloudinary" in error_message.lower()
        is_timeout_error = any(x in error_message.lower() for x in ["timeout", "timed out", "read timeout", "connection timeout"])
        is_network_error = any(x in error_message.lower() for x in ["connection", "httpsconnectionpool", "unable to connect", "network", "unreachable"])
        
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
        
        if is_validation_error:
            print(f"[VALIDATION ERROR] User input validation failed - NOT rotating API key")
            print(f"[VALIDATION ERROR] Error: {error_message}")
            print(f"[VALIDATION ERROR] Marking job {job_id} as FAILED (not retryable)")
            mark_job_failed(job_id, error_message)
            return
        
        # All other errors (API errors, provider errors, etc.) - attempt key rotation and retry
        if api_key_id and provider_key:
            print(f"[API ERROR] Provider API error detected - attempting key rotation")
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
        provider_key = metadata.get("provider_key") or job.get("provider_key")
        
        if not provider_key:
            provider_key = map_model_to_provider(model_name, job_type="image")
            print(f"Determined provider from model: {provider_key}")
        
        # vision-xeven is a FREE API that doesn't require an API key
        if provider_key == "vision-xeven":
            print(f"Using FREE Xeven API - no API key required")
            provider_api_key = None
            api_key_id = None
        else:
            api_key_data = get_api_key_for_job(model_name, provider_key, job_type="image")
            
            provider_api_key = None
            
            if api_key_data:
                api_key_id = api_key_data.get("id")
                provider_api_key = api_key_data.get("api_key")
                if api_key_data.get("provider_key"):
                    provider_key = api_key_data.get("provider_key")
                print(f"Using API key from provider: {provider_key}")
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
            mask_url=mask_url
        )
        
        if not result.get("success"):
            error_msg = result.get("error", "Generation failed")
            raise Exception(error_msg)
        
        if result.get("is_base64"):
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
            
            print(f"Downloading image from: {image_url}")
            img_response = requests.get(image_url, timeout=60)
            
            if img_response.status_code != 200:
                raise Exception(f"Failed to download image: {img_response.status_code}")
            
            image_data = img_response.content
        
        image_size_mb = len(image_data) / (1024 * 1024)
        print(f"Image data: {len(image_data)} bytes ({image_size_mb:.2f} MB)")
        
        max_cloudinary_size_mb = 10
        if image_size_mb > max_cloudinary_size_mb:
            print(f"[INFO] Image size ({image_size_mb:.2f} MB) exceeds Cloudinary limit ({max_cloudinary_size_mb} MB)")
            print(f"[COMPRESSION] Compressing image...")
            image_data = compress_image_to_size(image_data, max_size_mb=8)
            new_size_mb = len(image_data) / (1024 * 1024)
            print(f"[COMPRESSION] Compressed to {new_size_mb:.2f} MB")
        elif image_size_mb > 50:
            print(f"[WARNING] Image size is {image_size_mb:.2f} MB")
        
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
        
        complete_response = requests.post(
            f"{BACKEND_URL}/worker/job/{job_id}/complete",
            json={"image_url": final_url, "thumbnail_url": final_url},
            timeout=10,
            verify=VERIFY_SSL
        )
        
        if complete_response.status_code == 200:
            print(f"Job {job_id} completed successfully!")
            worker_status["jobs_processed"] += 1
            if api_key_id:
                increment_usage_count(api_key_id)
            
            # Increment quota after successful completion
            from model_quota_manager import get_quota_manager
            quota_manager = get_quota_manager()
            print(f"[QUOTA] Incrementing after completion - provider_key='{provider_key}', model='{model_name}'")
            quota_result = quota_manager.increment_quota(provider_key, model_name)
            if quota_result.get('success'):
                print(f"[QUOTA] ✓ Successfully incremented quota for {provider_key}:{model_name}")
            else:
                print(f"[QUOTA] ✗ Failed to increment: {quota_result.get('reason', 'unknown')}")
        else:
            print(f"Failed to mark job complete: {complete_response.status_code}")
            
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
        
        is_cloudinary_error = "cloudinary" in error_message.lower()
        is_timeout_error = any(x in error_message.lower() for x in ["timeout", "timed out", "read timeout", "connection timeout"])
        is_network_error = any(x in error_message.lower() for x in ["connection", "httpsconnectionpool", "unable to connect", "network", "unreachable"])
        
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
        
        if is_removebg_foreground_error:
            print(f"[IMAGE QUALITY ERROR] Remove.bg could not identify foreground - NOT rotating API key")
            # Extract the user-friendly message (remove the prefix)
            user_message = error_message.replace("Remove.bg generation failed: REMOVEBG_FOREGROUND_ERROR: ", "")
            print(f"[IMAGE QUALITY ERROR] User message: {user_message}")
            print(f"[IMAGE QUALITY ERROR] Marking job {job_id} as FAILED (not retryable)")
            mark_job_failed(job_id, user_message)
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
        
        if is_validation_error:
            print(f"[VALIDATION ERROR] User input validation failed - NOT rotating API key")
            print(f"[VALIDATION ERROR] Error: {error_message}")
            print(f"[VALIDATION ERROR] Marking job {job_id} as FAILED (not retryable)")
            mark_job_failed(job_id, error_message)
            return
        
        # All other errors (API errors, provider errors, etc.) - attempt key rotation and retry
        if api_key_id and provider_key:
            print(f"[API ERROR] Provider API error detected - attempting key rotation")
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
        
        running_jobs_response = supabase.table("jobs").select("job_id, user_id, model").eq("status", "running").execute()
        
        if not running_jobs_response.data or len(running_jobs_response.data) == 0:
            print("No 'running' jobs found - system is clean")
            print("="*60 + "\n")
            return 0
        
        running_jobs = running_jobs_response.data
        print(f"Found {len(running_jobs)} 'running' job(s) - resetting to 'pending'...")
        
        reset_count = 0
        for job in running_jobs:
            job_id = job.get("job_id")
            try:
                supabase.table("jobs").update({
                    "status": "pending",
                    "progress": 0,
                    "error_message": "Worker restarted - job reset to pending"
                }).eq("job_id", job_id).execute()
                
                print(f"  ✅ Reset job {job_id} to pending")
                reset_count += 1
            except Exception as update_error:
                print(f"  ❌ Failed to reset job {job_id}: {update_error}")
        
        print(f"\n✅ Successfully reset {reset_count} job(s) to pending")
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


def fetch_all_pending_jobs():
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
            print(f"Found {len(jobs)} pending job(s)")
            return jobs
        else:
            print(f"Failed to fetch pending jobs: {response.status_code}")
            return []
    except Exception as e:
        print(f"Error fetching pending jobs: {e}")
        return []


def process_all_pending_jobs():
    print("\n" + "="*60)
    print("BACKLOG CATCH-UP: Processing pending jobs")
    print("="*60)
    
    pending_jobs = fetch_all_pending_jobs()
    
    if not pending_jobs:
        print("No pending jobs in backlog")
        print("="*60 + "\n")
        return
    
    print(f"Processing {len(pending_jobs)} pending job(s) with per-provider concurrency...\n")
    
    for idx, job in enumerate(pending_jobs, 1):
        job_id = job.get("job_id")
        job_type = job.get("job_type", "image")
        prompt = job.get("prompt", "")[:50]
        
        print(f"[{idx}/{len(pending_jobs)}] Submitting job {job_id} ({job_type})")
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
    
    print("="*60)
    print("Backlog catch-up completed (jobs queued per provider)")
    print("="*60 + "\n")


async def realtime_listener():
    """
    Listen to INSERT events on jobs table.
    Only processes newly created jobs to prevent infinite loops
    when jobs are reset to pending status.
    """
    from supabase import acreate_client
    
    try:
        print("Connecting to Supabase Realtime...")
        async_client = await acreate_client(SUPABASE_URL, SUPABASE_KEY)
        
        def handle_new_job(payload):
            try:
                data = payload.get("data", {})
                record = data.get("record", payload.get("new", payload.get("record", {})))
                
                if not record:
                    print(f"No record found in payload: {payload}")
                    sys.stdout.flush()
                    return
                
                status = record.get("status")
                
                if status != "pending":
                    return
                
                job_id = record.get("job_id")
                job_type = record.get("job_type", "image")
                
                print(f"\n{'='*70}")
                print(f"NEW JOB RECEIVED VIA REALTIME!")
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
                        print(f"REALTIME JOB COMPLETED: {job_id}")
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
                print(f"ERROR IN REALTIME CALLBACK")
                print(f"{'='*70}")
                print(f"Error: {e}")
                import traceback
                traceback.print_exc()
                print(f"{'='*70}\n")
                sys.stdout.flush()
        
        channel = async_client.channel("job-worker-pending")

        subscription_result = await channel.on_postgres_changes(
            event="INSERT",
            schema="public",
            table="jobs",
            callback=handle_new_job
        ).subscribe()
        
        print(f"Subscription result: {subscription_result}")
        print("Subscribed to new job INSERT events (Realtime active)")
        print()
        print("NOTE: If events don't arrive, check Supabase Dashboard:")
        print("   Database -> Replication -> Enable Realtime for 'jobs' table")
        print()
        print("=" * 60)
        print("LISTENING FOR NEW JOBS...")
        print("=" * 60)
        print()
        sys.stdout.flush()
        
        while True:
            await asyncio.sleep(1)
        
    except Exception as e:
        print(f"Realtime listener error: {e}")
        notify_error(
            ErrorType.REALTIME_LISTENER_CRASHED,
            "Realtime listener crashed - no new jobs being processed",
            context={"error": str(e)[:200]}
        )
        import traceback
        traceback.print_exc()


def fetch_pending_jobs_for_provider(provider_key: str):
    """
    Query pending jobs with 'No API key available' error for a specific provider.
    
    Args:
        provider_key: Provider identifier (e.g., 'vision-nova', 'cinematic-nova')
        
    Returns:
        List of job records that need to be reprocessed
    """
    try:
        from supabase_client import supabase
        
        print(f"[API_KEY_INSERT] Querying pending jobs for provider: {provider_key}")
        
        response = supabase.table("jobs")\
            .select("*")\
            .eq("status", "pending")\
            .like("error_message", "%No API key available%")\
            .execute()
        
        if not response.data:
            print(f"[API_KEY_INSERT] No pending jobs found")
            return []
        
        matching_jobs = []
        for job in response.data:
            metadata = job.get("metadata", {}) or {}
            job_provider_key = metadata.get("provider_key") or job.get("provider_key")
            
            if job_provider_key == provider_key:
                matching_jobs.append(job)
        
        print(f"[API_KEY_INSERT] Found {len(matching_jobs)} pending job(s) for provider {provider_key}")
        return matching_jobs
        
    except Exception as e:
        print(f"[API_KEY_INSERT] Error querying pending jobs: {e}")
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
        
        if not pending_jobs:
            print(f"[API_KEY_INSERT] No pending jobs to reprocess for {provider_key}")
            return
        
        print(f"\n{'='*70}")
        print(f"REPROCESSING {len(pending_jobs)} PENDING JOB(S) FOR {provider_key}")
        print(f"{'='*70}\n")
        
        for idx, job in enumerate(pending_jobs, 1):
            job_id = job.get("job_id") or job.get("id")
            job_type = job.get("job_type", "image")
            prompt = job.get("prompt", "")[:50]
            
            print(f"[{idx}/{len(pending_jobs)}] Reprocessing job {job_id} ({job_type})")
            print(f"   Prompt: {prompt}...")
            
            try:
                requests.post(
                    f"{BACKEND_URL}/worker/job/{job_id}/progress",
                    json={"progress": 5, "message": f"API key now available, starting generation..."},
                    timeout=10,
                    verify=VERIFY_SSL
                )
                
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
        
        print(f"{'='*70}")
        print(f"REPROCESSING COMPLETED FOR {provider_key}")
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


def run_async_listener():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    async def run_both_listeners():
        await asyncio.gather(
            realtime_listener(),
            api_key_realtime_listener()
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
        
        # Reset stale running jobs
        reset_running_jobs_to_pending()
        
        # Process backlog
        print("\n" + "="*60)
        print("WORKER STARTUP: Initial backlog catch-up")
        print("="*60)
        process_all_pending_jobs()
        print("Initial backlog processed!\n")
        
        worker_status["backlog_processed"] = True
        worker_status["startup_complete"] = True
        worker_status["ready"] = True
        
        print("=" * 60)
        print("JOB WORKER READY")
        print("=" * 60)
        print("Switching to REALTIME mode (no more polling)")
        print("Will receive instant notifications for new jobs")
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
        RETRY_INTERVAL = 300  # 5 minutes - retry pending jobs with transient errors
        
        while True:
            time.sleep(5)
            
            # Heartbeat every 30 seconds
            if time.time() - last_heartbeat >= 30:
                worker_status["last_heartbeat"] = datetime.now().isoformat()
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Worker alive, listening for jobs...")
                sys.stdout.flush()
                last_heartbeat = time.time()
            
            # Retry pending jobs with transient errors every 5 minutes
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
