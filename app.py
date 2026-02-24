import os
import time
import threading
import signal
import sys
from pathlib import Path
from datetime import datetime

import requests
from flask import Flask, jsonify, request, Response, stream_with_context
from flask_cors import CORS
from dotenv_vault import load_dotenv

# Import ngrok for public URL tunneling (optional)
try:
    from pyngrok import ngrok
    NGROK_AVAILABLE = True
except ImportError:
    NGROK_AVAILABLE = False

# Import our new modules
from auth import send_magic_link, verify_magic_link, logout, get_user_from_token
from jobs import create_job, get_job, get_user_jobs, update_job_status, update_job_result, cancel_job, get_job_stats, get_next_pending_job
from storage import upload_image_from_path, get_image_url, delete_image
from middleware import require_auth, get_current_user, extract_token
from supabase_client import supabase
from cloudinary_manager import get_cloudinary_manager
from realtime_manager import ensure_realtime_started, get_realtime_manager
import monetag_api  # MoneyTag API integration
from monetag_postback_manager import (
    get_postback_url, log_postback_received, get_postback_stats,
    get_recent_postbacks, clear_postback_cache, get_postback_url_config,
    format_postback_log
)
from provider_trials import (
    get_user_provider_trials, check_provider_trial_available,
    use_provider_trial, get_provider_by_model
)
from error_notifier import notify_error, ErrorType
from model_quota_manager import ensure_quota_manager_started, get_quota_manager
from slider_captcha import get_captcha_manager
from slider_captcha_verify import verify_captcha_token

app = Flask(__name__)

# Global sync status tracker for Koyeb health checks
sync_status = {
    "running": False,
    "completed": False,
    "error": None,
    "started_at": None
}

# Global flag for graceful shutdown
shutdown_requested = False

def graceful_shutdown(signum, frame):
    """Handle graceful shutdown signal from graceful_shutdown.py script"""
    global shutdown_requested
    shutdown_requested = True
    print("\n" + "="*60)
    print("GRACEFUL SHUTDOWN SIGNAL RECEIVED")
    print("="*60)
    print(f"Signal: {signum}")
    print("Flask app will shut down gracefully...")
    print("Background threads will complete current operations...")
    print("="*60)
    # Exit gracefully
    sys.exit(0)

# Register signal handlers for graceful shutdown
signal.signal(signal.SIGTERM, graceful_shutdown)
signal.signal(signal.SIGINT, graceful_shutdown)

# Start shared Realtime connection manager on app startup
ensure_realtime_started()

# Initialize quota manager
ensure_quota_manager_started()

# Ping worker accounts to prevent auto-pause
from worker_health import ping_all_workers_async
ping_all_workers_async()

# Start workflow retry manager for auto-retry of failed workflows
from workflow_retry_manager import start_retry_manager
start_retry_manager()

# Configure CORS to allow requests from frontend
# This fixes the "No 'Access-Control-Allow-Origin' header" error
load_dotenv()

# Get environment URLs for CORS
BACKEND_URL = os.getenv("BACKEND_URL", "https://api.rasenai.qzz.io")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://rasenai.qzz.io")

# Configure CORS to allow requests from frontend
# This fixes the "No 'Access-Control-Allow-Origin' header" error
# Filter out None values to prevent Flask-CORS errors
allowed_origins = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
    "https://rasenai.qzz.io",
    "https://api.rasenai.qzz.io",
    "https://api.rasenai.qzz.io:8080",
    "https://free.wispbyte.com",
    "https://atool.pages.dev",
    FRONTEND_URL,
    BACKEND_URL,
    os.getenv("KOYEB_PUBLIC_URL"), # Auto-detect Koyeb URL if available
]
# Remove None values
allowed_origins = [origin for origin in allowed_origins if origin is not None]

CORS(app, resources={
    r"/*": {
        "origins": allowed_origins,
        "methods": ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        "allow_headers": ["Content-Type", "Authorization", "X-Monetag-Signature", "ngrok-skip-browser-warning"],
        "supports_credentials": True,
        "expose_headers": ["Content-Type", "Authorization"]
    }
})

# Add ngrok bypass header to all responses to skip warning page
@app.after_request
def add_ngrok_header(response):
    response.headers['ngrok-skip-browser-warning'] = 'true'
    return response


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} environment variable is required.")
    return value


# Discord Configuration
BOT_TOKEN = _require_env("DISCORD_BOT_TOKEN")
CHANNEL_ID = _require_env("DISCORD_CHANNEL_ID")

# hCaptcha Configuration
HCAPTCHA_SECRET_KEY = os.getenv("HCAPTCHA_SECRET_KEY")
HCAPTCHA_SITE_KEY = os.getenv("HCAPTCHA_SITE_KEY")
HCAPTCHA_VERIFY_URL = "https://api.hcaptcha.com/siteverify"

# Frontend URL Configuration (for share links)
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

def verify_hcaptcha(token: str, remoteip: str = None) -> dict:
    """Verify hCaptcha token"""
    if not HCAPTCHA_SECRET_KEY:
        return {"success": False, "error": "hCaptcha not configured"}
    
    if not token:
        return {"success": False, "error": "hCaptcha token missing"}
    
    payload = {
        "secret": HCAPTCHA_SECRET_KEY,
        "response": token
    }
    
    if remoteip:
        payload["remoteip"] = remoteip
    
    try:
        response = requests.post(HCAPTCHA_VERIFY_URL, data=payload, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        return {
            "success": data.get("success", False),
            "challenge_ts": data.get("challenge_ts"),
            "hostname": data.get("hostname"),
            "error_codes": data.get("error-codes", [])
        }
    except Exception as e:
        return {"success": False, "error": f"hCaptcha verification failed: {str(e)}"}

# ============================================
# Slider CAPTCHA Endpoints
# ============================================

@app.route("/captcha/challenge", methods=["GET"])
def captcha_challenge():
    try:
        client_ip = request.remote_addr
        captcha_manager = get_captcha_manager()
        challenge_id, challenge_data = captcha_manager.generate_challenge(client_ip)
        
        return jsonify({
            "success": True,
            "challenge_id": challenge_data['challenge_id'],
            "image_seed": challenge_data['image_seed'],
            "correct_x": challenge_data['correct_x'],
            "correct_y": challenge_data['correct_y']
        }), 200
    except Exception as e:
        error_msg = str(e)
        print(f"❌ Error generating CAPTCHA challenge: {error_msg}")
        
        # Check if it's a cooldown error
        is_cooldown = "wait" in error_msg.lower() and "before trying again" in error_msg.lower()
        
        return jsonify({
            "success": False,
            "error": error_msg,
            "cooldown": is_cooldown
        }), 429 if is_cooldown else 500

@app.route("/captcha/verify", methods=["POST"])
def captcha_verify():
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({
                "success": False,
                "error": "No data provided"
            }), 400
        
        challenge_id = data.get('challengeId')
        final_x = data.get('finalX')
        movements = data.get('movements', [])
        duration = data.get('duration')
        
        print(f"🔍 CAPTCHA verify request: challengeId={challenge_id}, finalX={final_x}, duration={duration}, movements={len(movements)}")
        
        if not all([challenge_id, final_x is not None, duration is not None]):
            return jsonify({
                "success": False,
                "error": "Missing required fields"
            }), 400
        
        client_ip = request.remote_addr
        captcha_manager = get_captcha_manager()
        
        result = captcha_manager.verify_challenge(
            challenge_id,
            int(final_x),
            movements,
            int(duration),
            client_ip
        )
        
        print(f"🔍 Verification result: {result}")
        
        if result['success']:
            return jsonify(result), 200
        else:
            return jsonify(result), 400
            
    except Exception as e:
        print(f"❌ Error verifying CAPTCHA: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@app.route("/captcha/image", methods=["GET"])
def captcha_image():
    from PIL import Image, ImageDraw, ImageFilter
    import io
    from flask import send_file, make_response
    
    try:
        seed = request.args.get('seed', 'default')
        print(f"🖼️ Generating CAPTCHA image with seed: {seed}")
        width = 320
        height = 160
        
        import random
        random.seed(seed)
        
        img = Image.new('RGB', (width, height))
        draw = ImageDraw.Draw(img)
        
        for _ in range(100):
            x1 = random.randint(0, width)
            y1 = random.randint(0, height)
            x2 = x1 + random.randint(20, 60)
            y2 = y1 + random.randint(20, 60)
            color = (
                random.randint(100, 200),
                random.randint(100, 200),
                random.randint(150, 255)
            )
            draw.rectangle([x1, y1, x2, y2], fill=color)
        
        img = img.filter(ImageFilter.GaussianBlur(radius=2))
        
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        
        print(f"✅ CAPTCHA image generated successfully")
        response = make_response(send_file(buf, mimetype='image/png'))
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        return response
    except Exception as e:
        print(f"❌ Error generating CAPTCHA image: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

# Cache for the latest URL (deprecated - kept for backward compatibility)
cached_url = None
cached_url_timestamp = None
cache_invalidation_flag = False


@app.route("/get-url", methods=["GET"])
def get_url():
    """Deprecated: Modal/ComfyUI endpoints removed. Now using Replicate and FAL AI."""
    return jsonify({
        "success": True,
        "url": "deprecated",
        "message": "Modal/ComfyUI endpoints removed. Using Replicate and FAL AI APIs directly.",
        "deprecated": True
    }), 200


@app.route("/invalidate-cache", methods=["POST"])
def invalidate_cache():
    """Deprecated: No longer needed with Replicate/FAL AI routing."""
    return jsonify({
        "success": True,
        "message": "Cache invalidation deprecated. Using direct API routing."
    }), 200


@app.route("/generate", methods=["POST"])
def generate():
    """Deprecated: Generation now handled by job worker with Replicate/FAL AI routing."""
    return jsonify({
        "success": False,
        "error": "Direct generation deprecated. Use /jobs endpoint to create generation jobs.",
        "deprecated": True
    }), 410


@app.route("/list-models", methods=["GET"])
def list_models():
    """Return available models for each provider. Vision-xeven (FREE) shown first, then vision-atlas, vision-nova (Replicate), and other providers."""
    models = {
        "vision-xeven": [
            {"name": "sdxl-lightning-xeven", "displayName": "SDXL Lightning", "type": "image"},
            {"name": "flux-schnell-2", "displayName": "Flux Schnell (2)", "type": "image"},
            {"name": "sdxl-xeven", "displayName": "SDXL Base", "type": "image"},
            {"name": "phoenix-2", "displayName": "Phoenix (2)", "type": "image"},
            {"name": "lucid-origin", "displayName": "Lucid Origin", "type": "image"},
        ],
        "vision-atlas": [
            {"name": "fal-ai/flux-2-pro", "displayName": "FLUX 2 Pro", "type": "image"},
            {"name": "fal-ai/nano-banana-pro", "displayName": "Nano Banana Pro", "type": "image"},
            {"name": "fal-ai/gpt-image-1.5", "displayName": "GPT Image 1.5", "type": "image"},
            {"name": "fal-ai/bytedance/seedream/v4/text-to-image", "displayName": "SeeDream v4", "type": "image"},
        ],
        "vision-nova": [
            {"name": "google/imagen-4", "displayName": "Google Imagen 4", "type": "image"},
            {"name": "black-forest-labs/flux-kontext-pro", "displayName": "FLUX Kontext Pro", "type": "image"},
            {"name": "ideogram-ai/ideogram-v3-turbo", "displayName": "Ideogram v3 Turbo", "type": "image"},
            {"name": "black-forest-labs/flux-1.1-pro", "displayName": "FLUX 1.1 Pro", "type": "image"},
            {"name": "black-forest-labs/flux-dev", "displayName": "FLUX Dev", "type": "image"},
        ],
        "vision-flux": [
            {"name": "fal-ai/flux-2-pro", "displayName": "FLUX 2 Pro", "type": "image"},
            {"name": "fal-ai/nano-banana-pro", "displayName": "Nano Banana Pro", "type": "image"},
            {"name": "fal-ai/gpt-image-1.5", "displayName": "GPT Image 1.5", "type": "image"},
            {"name": "fal-ai/bytedance/seedream/v4/text-to-image", "displayName": "SeeDream v4", "type": "image"},
        ],
        "vision-ultrafast": [
            {"name": "ultra-fast-nano", "displayName": "Ultra Fast Nano Banana", "type": "image"},
            {"name": "ultra-fast-nano-banana-2", "displayName": "Ultra Fast Nano Banana 2", "type": "image"},
        ],
        "vision-bria": [
            {"name": "bria_image_generate", "displayName": "Bria Image Generate", "type": "image"},
            {"name": "bria_image_generate_lite", "displayName": "Bria Image Generate Lite", "type": "image"},
            {"name": "bria_gen_fill", "displayName": "Bria Generative Fill", "type": "image"},
            {"name": "bria_erase", "displayName": "Bria Erase", "type": "image"},
            {"name": "bria_remove_background", "displayName": "Bria Remove Background", "type": "image"},
            {"name": "bria_replace_background", "displayName": "Bria Replace Background", "type": "image"},
            {"name": "bria_blur_background", "displayName": "Bria Blur Background", "type": "image"},
            {"name": "bria_erase_foreground", "displayName": "Bria Erase Foreground", "type": "image"},
            {"name": "bria_expand", "displayName": "Bria Expand", "type": "image"},
            {"name": "bria_enhance", "displayName": "Bria Enhance", "type": "image"},
        ],
        "vision-infip": [
            {"name": "z-image-turbo", "displayName": "Z-Image Turbo", "type": "image"},
            {"name": "qwen", "displayName": "Qwen", "type": "image"},
            {"name": "flux2-klein-9b", "displayName": "FLUX 2 Klein", "type": "image"},
            {"name": "flux2-dev", "displayName": "FLUX 2 Dev", "type": "image"},
        ],
        "vision-deapi": [
            {"name": "z-image-turbo-deapi", "displayName": "Z-Image Turbo (2)", "type": "image"},
            {"name": "flux-schnell-deapi", "displayName": "Flux Schnell (3)", "type": "image"},
        ],
        "cinematic-nova": [
            {"name": "minimax/video-01", "displayName": "Minimax Video-01", "type": "video"},
            {"name": "luma/reframe-video", "displayName": "Luma Reframe Video", "type": "video"},
            {"name": "topazlabs/video-upscale", "displayName": "Topaz Video Upscale", "type": "video"},
        ],
        "cinematic-pro": [
            {"name": "fal-ai/kling-video/v2.5-turbo/pro/image-to-video", "displayName": "Kling Video v2.5 Turbo Pro", "type": "video"},
            {"name": "fal-ai/minimax/hailuo-02-fast/image-to-video", "displayName": "Hailuo 02 Fast", "type": "video"},
            {"name": "fal-ai/minimax/hailuo-02/standard/image-to-video", "displayName": "Hailuo 02 Standard", "type": "video"},
            {"name": "fal-ai/bytedance/seedance/v1/lite/text-to-video", "displayName": "SeeDance v1 Lite", "type": "video"},
        ],
    }
    
    provider = request.args.get("provider")
    if provider and provider in models:
        return jsonify({"success": True, "models": models[provider]}), 200
    
    return jsonify({"success": True, "models": models}), 200


@app.route("/list-video-models", methods=["GET"])
def list_video_models():
    """Return available video models for each provider. Cinematic-nova models shown first."""
    models = {
        "cinematic-nova": [
            {"name": "minimax/video-01", "displayName": "Minimax Video-01", "type": "video"},
            {"name": "luma/reframe-video", "displayName": "Luma Reframe Video", "type": "video"},
            {"name": "topazlabs/video-upscale", "displayName": "Topaz Video Upscale", "type": "video"},
        ],
        "cinematic-pro": [
            {"name": "fal-ai/kling-video/v2.5-turbo/pro/image-to-video", "displayName": "Kling Video v2.5 Turbo Pro", "type": "video"},
            {"name": "fal-ai/minimax/hailuo-02-fast/image-to-video", "displayName": "Hailuo 02 Fast", "type": "video"},
            {"name": "fal-ai/minimax/hailuo-02/standard/image-to-video", "displayName": "Hailuo 02 Standard", "type": "video"},
            {"name": "fal-ai/bytedance/seedance/v1/lite/text-to-video", "displayName": "SeeDance v1 Lite", "type": "video"},
        ],
    }
    
    provider = request.args.get("provider")
    if provider and provider in models:
        return jsonify({"success": True, "models": models[provider]}), 200
    
    return jsonify({"success": True, "models": models}), 200


@app.route("/generate-video", methods=["POST"])
def generate_video():
    """Deprecated: Video generation now handled by job worker with Replicate/FAL AI routing."""
    return jsonify({
        "success": False,
        "error": "Direct video generation deprecated. Use /jobs endpoint with job_type='video'.",
        "deprecated": True
    }), 410


# ============================================
# Authentication Endpoints
# ============================================

@app.route("/auth/magic-link", methods=["POST"])
def auth_send_magic_link():
    """Send magic link to user's email"""
    data = request.get_json()
    email = data.get("email")

    if not email:
        return jsonify({
            "success": False,
            "error": "Email is required"
        }), 400

    result = send_magic_link(email)

    if result["success"]:
        return jsonify(result), 200
    else:
        # Return 503 for maintenance errors, 400 for other errors
        status_code = 503 if result.get("maintenance") else 400
        return jsonify(result), status_code


@app.route("/auth/verify", methods=["GET"])
def auth_verify_magic_link():
    """Verify magic link token"""
    token = request.args.get("token")

    if not token:
        return jsonify({
            "success": False,
            "error": "Token is required"
        }), 400

    # Get client IP for abuse prevention
    client_ip = request.remote_addr
    
    result = verify_magic_link(token, client_ip=client_ip)

    if result["success"]:
        return jsonify(result), 200
    else:
        return jsonify(result), 400


@app.route("/auth/me", methods=["GET"])
@require_auth
def auth_get_current_user():
    """Get current user info with full details from database"""
    user_context = get_current_user()

    if not user_context or not user_context.get("success"):
        return jsonify({
            "success": False,
            "error": "Not authenticated"
        }), 401

    try:
        # Get full user data from database
        user_response = supabase.table("users").select("*").eq("id", user_context["user_id"]).execute()

        if not user_response.data:
            return jsonify({
                "success": False,
                "error": "User not found"
            }), 404

        user = user_response.data[0]

        return jsonify({
            "success": True,
            "user": {
                "id": user["id"],
                "email": user["email"],
                "credits": user["credits"],
                "created_at": user["created_at"],
                "last_login": user.get("last_login"),
                "is_active": user.get("is_active", True)
            }
        }), 200
    except Exception as e:
        print(f"❌ Error getting user: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/auth/logout", methods=["POST"])
@require_auth
def auth_logout():
    """Logout current user"""
    token = extract_token(request)

    if token:
        result = logout(token)
        return jsonify(result), 200
    else:
        return jsonify({
            "success": False,
            "error": "No token provided"
        }), 400


# ============================================
# PROVIDER TRIALS ENDPOINTS
# ============================================

@app.route("/providers/trials", methods=["GET"])
@require_auth
def get_provider_trials():
    """
    Get all providers with user's trial availability status.
    Called on first visit - frontend caches this locally.
    """
    user_context = get_current_user()
    
    if not user_context or not user_context.get("success"):
        return jsonify({
            "success": False,
            "error": "Not authenticated"
        }), 401
    
    result = get_user_provider_trials(user_context["user_id"])
    
    if result["success"]:
        return jsonify(result), 200
    else:
        return jsonify(result), 500


@app.route("/providers/check/<provider_key>", methods=["GET"])
@require_auth
def check_provider_trial(provider_key):
    """Check if user has free trial available for a specific provider."""
    user_context = get_current_user()
    
    if not user_context or not user_context.get("success"):
        return jsonify({
            "success": False,
            "error": "Not authenticated"
        }), 401
    
    available = check_provider_trial_available(user_context["user_id"], provider_key)
    
    return jsonify({
        "success": True,
        "provider_key": provider_key,
        "free_trial_available": available
    }), 200


# ============================================
# Share Endpoints
# ============================================

@app.route("/share", methods=["POST"])
@require_auth
def share_create():
    """Create a shareable link for a result"""
    user = get_current_user()
    
    if not user or not user.get("success"):
        return jsonify({
            "success": False,
            "error": "Not authenticated"
        }), 401
    
    try:
        data = request.get_json()
        
        # Validate required fields
        if not data.get("prompt"):
            return jsonify({
                "success": False,
                "error": "Prompt is required"
            }), 400
        
        if not data.get("job_type") or data["job_type"] not in ["image", "video"]:
            return jsonify({
                "success": False,
                "error": "Valid job_type (image/video) is required"
            }), 400
        
        if not data.get("image_url") and not data.get("video_url"):
            return jsonify({
                "success": False,
                "error": "Either image_url or video_url is required"
            }), 400
        
        # Generate unique share_id
        max_attempts = 10
        share_id = None
        
        for _ in range(max_attempts):
            # Call the generate_share_id function from database
            result = supabase.rpc("generate_share_id").execute()
            potential_id = result.data
            
            # Check if it's unique
            existing = supabase.table("shared_results").select("id").eq("share_id", potential_id).execute()
            if not existing.data:
                share_id = potential_id
                break
        
        if not share_id:
            return jsonify({
                "success": False,
                "error": "Failed to generate unique share ID"
            }), 500
        
        # Create shared result
        shared_result = {
            "share_id": share_id,
            "user_id": user["user_id"],
            "job_id": data.get("job_id"),
            "prompt": data["prompt"],
            "image_url": data.get("image_url"),
            "video_url": data.get("video_url"),
            "job_type": data["job_type"],
            "is_public": data.get("is_public", True),
            "metadata": data.get("metadata", {})
        }
        
        response = supabase.table("shared_results").insert(shared_result).execute()
        
        if not response.data:
            return jsonify({
                "success": False,
                "error": "Failed to create shared result"
            }), 500
        
        created_share = response.data[0]
        share_url = f"{FRONTEND_URL}/shared/{share_id}"
        
        return jsonify({
            "success": True,
            "share_id": share_id,
            "share_url": share_url,
            "data": created_share
        }), 201
        
    except Exception as e:
        print(f"❌ Error creating share: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/share/<share_id>", methods=["GET"])
def share_get(share_id):
    """Get shared result by share_id (public endpoint)"""
    try:
        # Fetch the shared result
        response = supabase.table("shared_results").select("*").eq("share_id", share_id).eq("is_public", True).execute()
        
        if not response.data:
            return jsonify({
                "success": False,
                "error": "Shared result not found"
            }), 404
        
        shared_result = response.data[0]
        
        # Increment view count (async, don't wait)
        try:
            supabase.rpc("increment_share_view", {"p_share_id": share_id}).execute()
        except Exception as view_error:
            print(f"⚠️ Failed to increment view count: {view_error}")
        
        return jsonify({
            "success": True,
            "data": {
                "share_id": shared_result["share_id"],
                "prompt": shared_result["prompt"],
                "image_url": shared_result.get("image_url"),
                "video_url": shared_result.get("video_url"),
                "job_type": shared_result["job_type"],
                "created_at": shared_result["created_at"],
                "view_count": shared_result.get("view_count", 0)
            }
        }), 200
        
    except Exception as e:
        print(f"❌ Error fetching share: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/share/<share_id>/click", methods=["POST"])
def share_click(share_id):
    """Track 'Create Your Own' button click"""
    try:
        # Increment click count
        supabase.rpc("increment_share_click", {"p_share_id": share_id}).execute()
        
        return jsonify({
            "success": True,
            "message": "Click tracked"
        }), 200
        
    except Exception as e:
        print(f"❌ Error tracking click: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/share/<share_id>/conversion", methods=["POST"])
@require_auth
def share_conversion(share_id):
    """Track conversion (signup/login from shared link)"""
    try:
        # Increment conversion count
        supabase.rpc("increment_share_conversion", {"p_share_id": share_id}).execute()
        
        return jsonify({
            "success": True,
            "message": "Conversion tracked"
        }), 200
        
    except Exception as e:
        print(f"❌ Error tracking conversion: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/share/my-shares", methods=["GET"])
@require_auth
def share_list():
    """Get current user's shared results"""
    user = get_current_user()
    
    if not user or not user.get("success"):
        return jsonify({
            "success": False,
            "error": "Not authenticated"
        }), 401
    
    try:
        response = supabase.table("shared_results").select("*").eq("user_id", user["user_id"]).order("created_at", desc=True).execute()
        
        return jsonify({
            "success": True,
            "data": response.data
        }), 200
        
    except Exception as e:
        print(f"❌ Error fetching user shares: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ============================================
# Job Endpoints
# ============================================

@app.route("/jobs", methods=["POST"])
@require_auth
def jobs_create():
    """Create a new job"""
    user = get_current_user()

    # Check file-based maintenance mode
    from pathlib import Path
    maintenance_flag = Path(__file__).parent / ".maintenance_mode"
    if maintenance_flag.exists():
        return jsonify({
            "success": False,
            "error": "System is in maintenance mode. Please try again later."
        }), 503
    
    # Check failover-based maintenance mode
    from supabase_failover import get_failover_manager
    failover_manager = get_failover_manager()
    if failover_manager.is_maintenance_mode:
        status = failover_manager.get_status()
        return jsonify({
            "success": False,
            "error": "System is under maintenance due to service limits. Existing jobs can be viewed but new jobs are temporarily disabled.",
            "maintenance_reason": status.get("failover_reason"),
            "failover_time": status.get("failover_time")
        }), 503

    # Verify custom slider CAPTCHA token
    captcha_token = None
    if request.content_type and 'multipart/form-data' in request.content_type:
        captcha_token = request.form.get("captcha_token")
    else:
        data = request.get_json()
        captcha_token = data.get("captcha_token") if data else None
    
    captcha_result = verify_captcha_token(captcha_token)
    if not captcha_result["success"]:
        error_msg = captcha_result.get("error", "CAPTCHA verification failed")
        print(f"❌ CAPTCHA verification failed: {error_msg}")
        return jsonify({
            "success": False,
            "error": "Please complete the CAPTCHA verification"
        }), 400
    print(f"✅ CAPTCHA verified successfully")

    # Check for active jobs (pending, running, or pending_retry for workflows)
    try:
        active_jobs_response = supabase.table("jobs").select("job_id, status, job_type").eq(
            "user_id", user["user_id"]
        ).in_(
            "status", ["pending", "running", "pending_retry"]
        ).limit(1).execute()
        
        if active_jobs_response.data and len(active_jobs_response.data) > 0:
            active_job = active_jobs_response.data[0]
            print(f"⚠️ User {user['user_id']} has active job: {active_job['job_id']} (status={active_job['status']}, type={active_job.get('job_type')})")
            return jsonify({
                "success": False,
                "error": "Run one job at a time"
            }), 400
    except Exception as e:
        print(f"⚠️ Error checking active jobs: {e}")

    # Handle both JSON and multipart/form-data
    if request.content_type and 'multipart/form-data' in request.content_type:
        # Form data with file upload
        print(f"\n📋 Received multipart/form-data request")
        print(f"   Form keys: {list(request.form.keys())}")
        print(f"   File keys: {list(request.files.keys())}")

        prompt = request.form.get("prompt")
        model = request.form.get("model", "flux-dev")
        aspect_ratio = request.form.get("aspect_ratio", "1:1")
        negative_prompt = request.form.get("negative_prompt", "")
        job_type = request.form.get("job_type", "image")
        duration = int(request.form.get("duration", 5))  # Duration in seconds for videos

        # Handle uploaded image(s) - supports single or multiple images
        uploaded_images = request.files.getlist("images") or ([request.files.get("image")] if request.files.get("image") else [])
        image_url = None
        image_urls = []

        if uploaded_images and uploaded_images[0]:
            print(f"\n📸 Processing {len(uploaded_images)} uploaded file(s)")
            
            for idx, uploaded_image in enumerate(uploaded_images, 1):
                # Detect if this is a video or image file
                filename = uploaded_image.filename.lower()
                is_video = filename.endswith(('.mp4', '.mov', '.avi', '.webm', '.mkv', '.flv', '.wmv'))
                
                file_type = "video" if is_video else "image"
                print(f"\n📸 {file_type.title()} file {idx}/{len(uploaded_images)} received:")
                print(f"   Filename: {uploaded_image.filename}")
                print(f"   Size: {len(uploaded_image.read())} bytes")
                uploaded_image.seek(0)  # Reset file pointer after reading size

                try:
                    # Save temporarily and upload to Cloudinary
                    import tempfile
                    import uuid
                    temp_dir = tempfile.gettempdir()
                    temp_filename = f"{uuid.uuid4()}_{uploaded_image.filename}"
                    temp_path = os.path.join(temp_dir, temp_filename)

                    uploaded_image.save(temp_path)
                    print(f"✅ Saved uploaded {file_type} to: {temp_path}")

                    # Upload to Cloudinary using appropriate method
                    storage = get_cloudinary_manager()
                    print(f"☁️  Uploading {file_type} to Cloudinary...")
                    
                    if is_video:
                        cloudinary_result = storage.upload_video(temp_path, folder_name="user_uploads")
                    else:
                        cloudinary_result = storage.upload_image(temp_path, folder_name="user_uploads")
                    
                    print(f"   Result: {cloudinary_result}")

                    # Handle both string URLs and dict responses from Cloudinary
                    if isinstance(cloudinary_result, str):
                        uploaded_url = cloudinary_result
                    else:
                        uploaded_url = cloudinary_result.get('secure_url') or cloudinary_result.get('url')
                    
                    if uploaded_url:
                        print(f"✅ Uploaded {file_type} to Cloudinary: {uploaded_url}")
                        image_urls.append(uploaded_url)
                    else:
                        print(f"❌ No URL in Cloudinary result: {cloudinary_result}")

                    # Clean up temp file
                    os.remove(temp_path)

                except Exception as e:
                    import traceback
                    print(f"❌ Error handling uploaded {file_type}: {e}")
                    print(f"   Traceback: {traceback.format_exc()}")
                    return jsonify({
                        "success": False,
                        "error": f"Failed to process uploaded {file_type}: {str(e)}"
                    }), 400
            
            # Set image_url to single URL or array based on count
            if len(image_urls) == 1:
                image_url = image_urls[0]
            elif len(image_urls) > 1:
                image_url = image_urls
            
            print(f"✅ Total uploaded: {len(image_urls)} file(s)")
        else:
            print(f"⚠️  No image file in request.files")

        # Handle uploaded mask
        uploaded_mask = request.files.get("mask")
        mask_url = None

        if uploaded_mask:
            print(f"\n🎭 Mask file received:")
            print(f"   Filename: {uploaded_mask.filename}")
            print(f"   Size: {len(uploaded_mask.read())} bytes")
            uploaded_mask.seek(0)  # Reset file pointer after reading size

            try:
                # Save temporarily and upload to Cloudinary
                import tempfile
                import uuid
                temp_dir = tempfile.gettempdir()
                temp_filename = f"{uuid.uuid4()}_{uploaded_mask.filename}"
                temp_path = os.path.join(temp_dir, temp_filename)

                uploaded_mask.save(temp_path)
                print(f"✅ Saved uploaded mask to: {temp_path}")

                # Upload to Cloudinary
                storage = get_cloudinary_manager()
                print(f"☁️  Uploading mask to Cloudinary...")
                cloudinary_result = storage.upload_image(temp_path, folder_name="user_uploads/masks")
                print(f"   Result: {cloudinary_result}")

                mask_url = cloudinary_result.get('secure_url') or cloudinary_result.get('url')
                if mask_url:
                    print(f"✅ Uploaded mask to Cloudinary: {mask_url}")
                else:
                    print(f"❌ No URL in Cloudinary result: {cloudinary_result}")

                # Clean up temp file
                os.remove(temp_path)

            except Exception as e:
                import traceback
                print(f"❌ Error handling uploaded mask: {e}")
                print(f"   Traceback: {traceback.format_exc()}")
                return jsonify({
                    "success": False,
                    "error": f"Failed to process uploaded mask: {str(e)}"
                }), 400
        else:
            print(f"⚠️  No mask file in request.files")
    else:
        # Regular JSON request
        data = request.get_json()
        prompt = data.get("prompt")
        model = data.get("model", "flux-dev")
        aspect_ratio = data.get("aspect_ratio", "1:1")
        negative_prompt = data.get("negative_prompt", "")
        job_type = data.get("job_type", "image")
        duration = int(data.get("duration", 5))  # Duration in seconds for videos
        # Support both single URL (string) and multiple URLs (array) via image_url or image_urls
        image_url = data.get("image_urls") or data.get("image_url", None)
        mask_url = data.get("mask_url", None)  # For passing existing mask URLs

    if not prompt:
        return jsonify({
            "success": False,
            "error": "Prompt is required"
        }), 400

    # Check for NSFW content in prompt
    try:
        from nsfw_moderator import get_moderator
        moderator = get_moderator()
        moderation_result = moderator.check_text(prompt)
        
        if moderation_result.get("is_nsfw"):
            print(f"🚫 NSFW content detected in prompt")
            print(f"   Confidence: {moderation_result.get('confidence')}")
            print(f"   Categories: {moderation_result.get('categories')}")
            return jsonify({
                "success": False,
                "error": "Your prompt contains inappropriate content. Please revise and try again.",
                "moderation_details": {
                    "flagged": True,
                    "categories": moderation_result.get("categories", {}),
                    "profanity_matches": moderation_result.get("profanity_matches", [])
                }
            }), 400
        else:
            print(f"✅ Prompt passed NSFW moderation check")
    except Exception as e:
        print(f"⚠️ NSFW moderation check failed: {e}")

    # Debug logging
    user_id = user["user_id"]
    print(f"📋 Creating job:")
    print(f"   Job Type: {job_type}")
    print(f"   Model: {model}")
    print(f"   Duration: {duration}s")
    if isinstance(image_url, list):
        print(f"   Image URLs: {len(image_url)} images")
        for idx, url in enumerate(image_url, 1):
            print(f"     {idx}. {url[:80]}...")
    else:
        print(f"   Image URL: {image_url}")
    print(f"   Mask URL: {mask_url}")

    result = create_job(
        user_id=user_id,
        prompt=prompt,
        model=model,
        aspect_ratio=aspect_ratio,
        negative_prompt=negative_prompt,
        job_type=job_type,
        duration=duration,
        image_url=image_url,
        mask_url=mask_url
    )

    if result["success"]:
        return jsonify(result), 201
    else:
        # Return 503 for maintenance errors, 400 for other errors
        status_code = 503 if result.get("maintenance") else 400
        return jsonify(result), status_code


@app.route("/jobs", methods=["GET"])
@require_auth
def jobs_get_all():
    """Get all jobs for current user"""
    user = get_current_user()
    status = request.args.get("status")
    limit = int(request.args.get("limit", 50))
    job_type = request.args.get("job_type")

    result = get_user_jobs(user["user_id"], status, limit, job_type)

    if result["success"]:
        return jsonify(result), 200
    else:
        return jsonify(result), 400


@app.route("/jobs/<job_id>", methods=["GET"])
@require_auth
def jobs_get_one(job_id):
    """Get specific job"""
    result = get_job(job_id)

    if result["success"]:
        return jsonify(result), 200
    else:
        return jsonify(result), 404


@app.route("/jobs/<job_id>", methods=["DELETE"])
@require_auth
def jobs_cancel(job_id):
    """Cancel a job"""
    user = get_current_user()
    result = cancel_job(job_id, user["user_id"])

    if result["success"]:
        return jsonify(result), 200
    else:
        return jsonify(result), 400


@app.route("/jobs/stats", methods=["GET"])
@require_auth
def jobs_get_stats():
    """Get job statistics"""
    user = get_current_user()
    result = get_job_stats(user["user_id"])

    if result["success"]:
        return jsonify(result), 200
    else:
        return jsonify(result), 400


@app.route("/api/model-quotas", methods=["GET"])
def get_model_quotas():
    """
    Get current model quotas from cache.
    Returns quota status for all tracked models.
    No authentication required - public information.
    """
    try:
        quota_manager = get_quota_manager()
        quotas = quota_manager.get_quotas_for_frontend()
        
        return jsonify({
            "success": True,
            "quotas": quotas
        }), 200
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "quotas": {}
        }), 500


@app.route("/jobs/in-progress", methods=["GET"])
@require_auth
def jobs_get_in_progress():
    """Get user's last pending or running job (for resume after refresh/login)"""
    user = get_current_user()
    job_type = request.args.get("job_type", "image")  # Default to image

    print(f"📥 Fetching in-progress job for user {user['user_id']}, type: {job_type}")

    try:
        # Query for last pending or running job for this user and job type
        response = supabase.table("jobs").select("*").eq(
            "user_id", user["user_id"]
        ).eq(
            "job_type", job_type
        ).in_(
            "status", ["pending", "running"]
        ).order("created_at", desc=True).limit(1).execute()

        if response.data and len(response.data) > 0:
            job = response.data[0]
            print(f"   ✅ Found in-progress job: {job['job_id']} (status: {job['status']})")
            return jsonify({
                "success": True,
                "job": job
            }), 200
        else:
            print(f"   💤 No in-progress jobs")
            return jsonify({
                "success": False,
                "message": "No in-progress jobs found"
            }), 200
    except Exception as e:
        print(f"   ❌ Error fetching in-progress job: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/jobs/<job_id>/stream", methods=["GET"])
@require_auth
def jobs_stream_status(job_id):
    """Stream job status updates via Server-Sent Events (SSE)"""
    from flask import Response, stream_with_context
    import json
    import queue

    # Guard against frontend sending undefined/null before job_id is known
    if not job_id or job_id in ("undefined", "null", ""):
        print(f"❌ SSE rejected: invalid job_id='{job_id}'")
        return jsonify({"success": False, "error": "Invalid job ID"}), 400

    print(f"\n{'='*80}")
    print(f"🚀🚀🚀 SSE ENDPOINT REACHED 🚀🚀🚀")
    print(f"Job ID: {job_id}")
    print(f"{'='*80}\n")

    user = get_current_user()

    print(f"📡 SSE stream requested for job {job_id} by user {user['user_id']}")

    # Verify user owns this job
    try:
        job_response = supabase.table("jobs").select("*").eq("job_id", job_id).single().execute()
        if not job_response.data or job_response.data.get("user_id") != user["user_id"]:
            print(f"❌ Job not found or unauthorized: {job_id}")
            return jsonify({"success": False, "error": "Job not found or unauthorized"}), 404
    except Exception as e:
        print(f"❌ Error fetching job: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

    current_job = job_response.data
    print(f"✅ SSE stream authorized for job {job_id} (current status: {current_job.get('status')})")

    # Create queue for this client
    client_queue = queue.Queue(maxsize=100)

    # Subscribe BEFORE checking current state to avoid missing events between the two
    realtime_manager = get_realtime_manager()
    print(f"   Realtime manager running: {realtime_manager.running}")
    realtime_manager.subscribe_to_job(job_id, client_queue)
    print(f"   Subscription registered\n")

    def generate():
        """Generate SSE events from shared realtime connection"""
        try:
            # Send initial connection event
            yield f"event: connected\ndata: {json.dumps({'type': 'connected', 'job_id': job_id})}\n\n"
            print(f"📡 SSE connection event sent for job {job_id}")

            # Immediately send current job state (catch-up: handles already-completed jobs)
            yield f"event: update\ndata: {json.dumps({'type': 'update', 'event': 'UPDATE', 'job': current_job})}\n\n"
            print(f"📤 SSE catch-up state sent: {job_id} status={current_job.get('status')}")
            if current_job.get("status") in ("completed", "failed", "cancelled"):
                print(f"✅ Job {job_id} already finished ({current_job.get('status')}), sending complete and closing")
                yield f"event: complete\ndata: {json.dumps({'type': 'complete', 'job': current_job})}\n\n"
                return

            # Stream updates from queue
            while True:
                try:
                    # Wait for update with timeout (30s keepalive)
                    payload = client_queue.get(timeout=30)
                    print(f"📥 SSE generator received payload: {type(payload)} - keys: {list(payload.keys()) if isinstance(payload, dict) else 'N/A'}")

                    # Check for error
                    if isinstance(payload, dict) and "error" in payload:
                        print(f"⚠️ Realtime error: {payload['error']}")
                        yield f"event: error\ndata: {json.dumps({'type': 'error', 'error': payload['error']})}\n\n"
                        break

                    # Extract job data from realtime payload (try multiple keys)
                    job_data = (
                        payload.get('new') if isinstance(payload, dict) else None
                    ) or (
                        payload.get('record') if isinstance(payload, dict) else None
                    )

                    if job_data:
                        print(f"📤 SSE update: {job_id} - status: {job_data.get('status')} - progress: {job_data.get('progress')}%")

                        event_data = {
                            'type': 'update',
                            'event': payload.get('eventType', 'UPDATE'),
                            'job': job_data
                        }

                        # Send with both event name and data line for compatibility
                        yield f"event: update\ndata: {json.dumps(event_data)}\n\n"
                        print(f"📤 SSE event 'update' sent for job {job_id} with status: {job_data.get('status')}")

                        # Close stream if job is complete
                        if job_data.get('status') in ['completed', 'failed', 'cancelled']:
                            print(f"✅ Job {job_id} finished with status: {job_data.get('status')}")
                            # Send final completion event
                            yield f"event: complete\ndata: {json.dumps({'type': 'complete', 'job': job_data})}\n\n"
                            break
                    else:
                        print(f"⚠️ SSE generator: No job_data found in payload")

                except queue.Empty:
                    # Send keepalive ping (comment only, no event name)
                    yield f": keepalive\n\n"
                    print(f"💓 Keepalive sent for job {job_id}")

        except GeneratorExit:
            print(f"🔌 Client disconnected from job {job_id} stream")
        finally:
            # Unsubscribe from shared manager
            realtime_manager.unsubscribe_from_job(job_id, client_queue)

    return Response(
        stream_with_context(generate()),
        status=200,
        mimetype='text/event-stream; charset=utf-8',
        content_type='text/event-stream; charset=utf-8',
        headers={
            'Cache-Control': 'no-cache, no-transform',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
            'X-Ngrok-Skip-Browser-Warning': 'true',
            'ngrok-skip-browser-warning': 'true',
            'Access-Control-Allow-Origin': '*',
            'X-Content-Type-Options': 'nosniff',
            'Content-Encoding': 'none'
        }
    )


# ============================================
# Worker Endpoints (Internal API for workers)
# ============================================

@app.route("/worker/next-job", methods=["GET"])
def worker_get_next_job():
    """Get the next pending job for worker to process"""
    result = get_next_pending_job()

    print(f"🔍 Worker requesting next job...")
    print(f"   Result: {result}")

    # get_next_pending_job() already returns {"success": True, "job": {...}}
    if result.get("success") and result.get("job"):
        job = result["job"]
        job_id = job.get("job_id")
        print(f"   ✅ Job found: {job_id}")
        print(f"   📝 Prompt: {job.get('prompt', '')[:50]}...")
        return jsonify(result), 200
    else:
        print(f"   💤 No pending jobs")
        return jsonify({
            "success": False,
            "message": "No pending jobs"
        }), 200


@app.route("/worker/pending-jobs", methods=["GET"])
def worker_get_pending_jobs():
    """Get ALL pending jobs for backlog catch-up"""
    try:
        print(f"📥 Worker requesting all pending jobs...")

        # Query for all pending jobs from database
        response = supabase.table("jobs").select("*").eq("status", "pending").order("created_at", desc=False).execute()

        if response.data:
            jobs = response.data
            print(f"   ✅ Found {len(jobs)} pending job(s)")
            return jsonify({
                "success": True,
                "jobs": jobs,
                "count": len(jobs)
            }), 200
        else:
            print(f"   💤 No pending jobs")
            return jsonify({
                "success": True,
                "jobs": [],
                "count": 0
            }), 200
    except Exception as e:
        print(f"   ❌ Error fetching pending jobs: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "jobs": [],
            "count": 0
        }), 500


@app.route("/worker/job/<job_id>/progress", methods=["POST"])
def worker_update_progress(job_id):
    """Update job progress from worker"""
    data = request.get_json()
    progress = data.get("progress", 0)
    message = data.get("message", "")

    print(f"📊 Worker progress update: job_id={job_id}, progress={progress}, message={message}")

    # Update job with progress (note: error_message is for errors, not status messages)
    result = update_job_status(
        job_id,
        status="running",
        progress=progress
    )

    if result.get("success"):
        # Manually dispatch SSE event to connected clients
        try:
            realtime_manager = get_realtime_manager()
            
            # Get updated job data for SSE dispatch
            updated_job_response = supabase.table("jobs").select("*").eq("job_id", job_id).execute()
            if updated_job_response.data:
                updated_job = updated_job_response.data[0]
                
                # Dispatch to SSE clients
                sse_payload = {
                    "eventType": "UPDATE",
                    "new": updated_job,
                    "old": {},
                    "data": {
                        "record": updated_job,
                        "type": "UPDATE"
                    }
                }
                
                realtime_manager._dispatch_event(job_id, sse_payload)
                print(f"📡 Manually dispatched progress event to SSE clients for job {job_id}")
        except Exception as sse_error:
            print(f"⚠️ Error dispatching SSE event: {sse_error}")
        
        return jsonify({"success": True}), 200
    else:
        return jsonify({"success": False, "error": "Failed to update progress"}), 500


@app.route("/worker/job/<job_id>/complete", methods=["POST"])
def worker_complete_job(job_id):
    """Mark job as complete with image/video URL"""
    data = request.get_json()
    image_url = data.get("image_url")
    thumbnail_url = data.get("thumbnail_url")
    video_url = data.get("video_url")

    print(f"🎉 Worker marking job complete: {job_id}")
    print(f"   Image URL: {image_url}")
    print(f"   Thumbnail URL: {thumbnail_url}")
    print(f"   Video URL: {video_url}")

    if not image_url:
        return jsonify({"success": False, "error": "image_url required"}), 400

    result = update_job_result(
        job_id,
        image_url=image_url,
        thumbnail_url=thumbnail_url,
        video_url=video_url
    )

    provider_update = None
    if result.get("success"):
        try:
            job_response = supabase.table("jobs").select("user_id, model").eq("job_id", job_id).execute()
            if job_response.data:
                job_data = job_response.data[0]
                user_id = job_data["user_id"]
                model = job_data.get("model", "")
                
                provider_key = get_provider_by_model(model)
                if provider_key:
                    trial_result = use_provider_trial(user_id, provider_key, job_id)
                    if trial_result.get("success"):
                        provider_update = {
                            "provider_key": provider_key,
                            "free_trial_available": False
                        }
                        print(f"✅ Marked provider trial used: {provider_key} for user {user_id}")
        except Exception as e:
            print(f"⚠️ Error updating provider trial: {e}")
        
        try:
            meta_resp = supabase.table("jobs").select("metadata").eq("job_id", job_id).execute()
            if meta_resp.data:
                meta = meta_resp.data[0].get("metadata") or {}
                if "pending_retry_count" in meta:
                    meta.pop("pending_retry_count")
                    supabase.table("jobs").update({"metadata": meta}).eq("job_id", job_id).execute()
        except Exception as meta_err:
            print(f"⚠️ Could not clear retry count for job {job_id}: {meta_err}")

        # IMPORTANT: Manually dispatch SSE event to connected clients
        # Don't rely only on Supabase Realtime (can be delayed)
        try:
            realtime_manager = get_realtime_manager()
            
            # Get updated job data for SSE dispatch
            updated_job_response = supabase.table("jobs").select("*").eq("job_id", job_id).execute()
            if updated_job_response.data:
                updated_job = updated_job_response.data[0]
                
                # Dispatch to SSE clients
                sse_payload = {
                    "eventType": "UPDATE",
                    "new": updated_job,
                    "old": {},
                    "data": {
                        "record": updated_job,
                        "type": "UPDATE"
                    }
                }
                
                realtime_manager._dispatch_event(job_id, sse_payload)
                print(f"📡 Manually dispatched completion event to SSE clients for job {job_id}")
        except Exception as sse_error:
            print(f"⚠️ Error dispatching SSE event: {sse_error}")

    if result.get("success"):
        response = {"success": True}
        if provider_update:
            response["provider_update"] = provider_update
        return jsonify(response), 200
    else:
        return jsonify({"success": False, "error": "Failed to complete job"}), 500


@app.route("/worker/job/<job_id>/fail", methods=["POST"])
def worker_fail_job(job_id):
    """Mark job as failed"""
    data = request.get_json()
    error_message = data.get("error", "Unknown error")

    success = update_job_status(
        job_id,
        status="failed",
        error_message=error_message
    )

    if success:
        # Manually dispatch SSE event to connected clients
        try:
            realtime_manager = get_realtime_manager()
            
            # Get updated job data for SSE dispatch
            updated_job_response = supabase.table("jobs").select("*").eq("job_id", job_id).execute()
            if updated_job_response.data:
                updated_job = updated_job_response.data[0]
                
                # Dispatch to SSE clients
                sse_payload = {
                    "eventType": "UPDATE",
                    "new": updated_job,
                    "old": {},
                    "data": {
                        "record": updated_job,
                        "type": "UPDATE"
                    }
                }
                
                realtime_manager._dispatch_event(job_id, sse_payload)
                print(f"📡 Manually dispatched failure event to SSE clients for job {job_id}")
        except Exception as sse_error:
            print(f"⚠️ Error dispatching SSE event: {sse_error}")
        
        return jsonify({"success": True}), 200
    else:
        return jsonify({"success": False, "error": "Failed to mark as failed"}), 500


@app.route("/worker/job/<job_id>/reset", methods=["POST"])
def worker_reset_job(job_id):
    """Reset job to pending status with optional provider_key tracking"""
    data = request.get_json() or {}
    message = data.get("message", "Job reset to pending")
    provider_key = data.get("provider_key")
    
    try:
        if provider_key:
            job_response = supabase.table("jobs").select("metadata").eq("job_id", job_id).execute()
            
            if job_response.data:
                current_metadata = job_response.data[0].get("metadata", {}) or {}
                current_metadata["provider_key"] = provider_key
                
                supabase.table("jobs").update({
                    "metadata": current_metadata
                }).eq("job_id", job_id).execute()
                
                print(f"Updated job {job_id} metadata with provider_key: {provider_key}")
    except Exception as e:
        print(f"Error updating job metadata: {e}")

    success = update_job_status(
        job_id,
        status="pending",
        progress=0,
        error_message=message
    )

    if success:
        # Manually dispatch SSE event to connected clients
        try:
            realtime_manager = get_realtime_manager()
            
            # Get updated job data for SSE dispatch
            updated_job_response = supabase.table("jobs").select("*").eq("job_id", job_id).execute()
            if updated_job_response.data:
                updated_job = updated_job_response.data[0]
                
                # Dispatch to SSE clients
                sse_payload = {
                    "eventType": "UPDATE",
                    "new": updated_job,
                    "old": {},
                    "data": {
                        "record": updated_job,
                        "type": "UPDATE"
                    }
                }
                
                realtime_manager._dispatch_event(job_id, sse_payload)
                print(f"📡 Manually dispatched reset event to SSE clients for job {job_id}")
        except Exception as sse_error:
            print(f"⚠️ Error dispatching SSE event: {sse_error}")
        
        return jsonify({"success": True}), 200
    else:
        return jsonify({"success": False, "error": "Failed to reset job"}), 500


@app.route("/worker/upload", methods=["POST"])
def worker_upload_image():
    """Upload image to Supabase storage (called by worker)"""
    import base64
    from io import BytesIO

    data = request.get_json()
    job_id = data.get("job_id")
    image_data_b64 = data.get("image_data")

    if not job_id or not image_data_b64:
        return jsonify({"success": False, "error": "job_id and image_data required"}), 400

    try:
        # Decode base64 image
        image_data = base64.b64decode(image_data_b64)

        # Create temporary file
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
            tmp.write(image_data)
            tmp_path = tmp.name

        # Upload to Supabase
        from storage import upload_image
        result = upload_image(job_id, tmp_path)

        # Clean up temp file
        os.unlink(tmp_path)

        if result["success"]:
            return jsonify({
                "success": True,
                "image_url": result["image_url"],
                "thumbnail_url": result.get("thumbnail_url")
            }), 200
        else:
            return jsonify({"success": False, "error": result.get("error")}), 500

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# MEGA ENDPOINTS DISABLED - No longer using Mega storage


@app.route("/cloudinary/upload-image", methods=["POST"])
def cloudinary_upload_image():
    """
    Upload image to Cloudinary cloud storage and return public URL

    Accepts:
    - JSON with base64 encoded image: {"image_data": "base64...", "file_name": "image.png"}
    - Or multipart/form-data with file upload

    Returns:
    - {"success": true, "secure_url": "https://res.cloudinary.com/...", "public_url": "..."}
    """
    import base64

    print("\n" + "="*60)
    print("☁️ CLOUDINARY UPLOAD REQUEST")
    print("="*60)

    try:
        cloudinary_storage = get_cloudinary_manager()

        # Check if it's JSON with base64 data or file upload
        if request.is_json:
            data = request.get_json()
            image_data_b64 = data.get("image_data")
            file_name = data.get("file_name", f"image_{int(time.time())}.png")
            metadata = data.get("metadata")  # Get metadata if provided

            if not image_data_b64:
                print("❌ No image_data provided in JSON")
                return jsonify({
                    "success": False,
                    "error": "image_data (base64) is required"
                }), 400

            print(f"📦 Decoding base64 image data...")
            print(f"📝 File name: {file_name}")
            if metadata:
                print(f"📋 Metadata: {list(metadata.keys())}")

            # Decode base64 image
            image_bytes = base64.b64decode(image_data_b64)

            # Upload from bytes with metadata
            result = cloudinary_storage.upload_image_from_bytes(image_bytes, file_name, metadata=metadata)

        else:
            # Handle file upload
            if 'file' not in request.files:
                print("❌ No file in request")
                return jsonify({
                    "success": False,
                    "error": "No file provided"
                }), 400

            file = request.files['file']

            if file.filename == '':
                print("❌ Empty filename")
                return jsonify({
                    "success": False,
                    "error": "No file selected"
                }), 400

            print(f"📁 Received file: {file.filename}")

            # Save to temporary file
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file.filename).suffix) as tmp:
                file.save(tmp.name)
                tmp_path = tmp.name

            # Upload the file
            result = cloudinary_storage.upload_image(tmp_path)

            # Clean up temp file
            try:
                os.unlink(tmp_path)
            except:
                pass

        if result["success"]:
            print(f"✅ Upload successful!")
            print(f"🔗 Secure URL: {result['secure_url']}")
            print("="*60 + "\n")
            return jsonify(result), 200
        else:
            print(f"❌ Upload failed: {result.get('error')}")
            notify_error(
                ErrorType.CLOUDINARY_UPLOAD_FAILED,
                "Cloudinary upload failed in /cloudinary/upload-image",
                context={"error": result.get('error')}
            )
            print("="*60 + "\n")
            return jsonify(result), 500

    except Exception as e:
        print(f"❌ Exception during upload: {str(e)}")
        notify_error(
            ErrorType.CLOUDINARY_UPLOAD_FAILED,
            f"Cloudinary upload exception: {str(e)[:100]}",
            context={"error": str(e)[:200]}
        )
        import traceback
        traceback.print_exc()
        print("="*60 + "\n")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/mega/proxy", methods=["GET"])
def mega_proxy():
    """
    Proxy endpoint to serve images from Mega.nz
    This allows embedding Mega images in <img> tags

    Usage: /mega/proxy?url=https://mega.nz/#!...
    """
    mega_url = request.args.get("url")

    if not mega_url:
        return jsonify({
            "success": False,
            "error": "Missing 'url' parameter"
        }), 400

    print(f"\n{'='*60}")
    print(f"MEGA PROXY REQUEST")
    print(f"{'='*60}")
    print(f"URL: {mega_url}")

    try:
        from mega_storage import download_from_mega_url

        # Download the file from Mega
        file_data = download_from_mega_url(mega_url)

        if not file_data:
            print(f"Failed to download from Mega")
            print(f"{'='*60}\n")
            return jsonify({
                "success": False,
                "error": "Failed to download from Mega"
            }), 500

        print(f"Successfully proxied {len(file_data)} bytes")
        print(f"{'='*60}\n")

        # Return the image with appropriate headers
        from flask import Response
        return Response(
            file_data,
            mimetype='image/png',
            headers={
                'Content-Type': 'image/png',
                'Cache-Control': 'public, max-age=31536000',  # Cache for 1 year
                'Access-Control-Allow-Origin': '*'
            }
        )

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        print(f"{'='*60}\n")

        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint - returns 200 immediately so Koyeb knows server is running"""
    return jsonify({
        "status": "healthy",
        "server": "running",
        "sync": {
            "running": sync_status["running"],
            "completed": sync_status["completed"],
            "error": sync_status["error"],
            "started_at": sync_status["started_at"]
        },
        "cached_url": cached_url,
        "has_url": cached_url is not None
    }), 200


@app.route("/maintenance-status", methods=["GET"])
def maintenance_status():
    """Check if system is in maintenance mode"""
    from supabase_failover import get_failover_manager
    
    failover_manager = get_failover_manager()
    status = failover_manager.get_status()
    
    return jsonify({
        "maintenance_mode": status["maintenance_mode"],
        "using_backup": status["using_backup"],
        "message": "System under maintenance - new jobs temporarily disabled" if status["maintenance_mode"] else "System operational"
    }), 200


@app.route("/failover-status", methods=["GET"])
def failover_status():
    """Get detailed failover status (admin endpoint)"""
    from supabase_failover import get_failover_manager
    
    failover_manager = get_failover_manager()
    return jsonify(failover_manager.get_status()), 200


# Telegram endpoints removed - using direct Monetag postback only


@app.route("/clear-cache", methods=["POST"])
def clear_cache():
    """Clear the cached URL and force fresh fetch on next request"""
    global cached_url, cached_url_timestamp
    cached_url = None
    cached_url_timestamp = None
    print("🗑️ Cache cleared - next request will fetch fresh URL from Discord")
    return jsonify({
        "success": True,
        "message": "Cache cleared - next request will fetch fresh URL"
    }), 200


# ============================================================================
# AD SESSION ENDPOINTS (Monetag Verified)
# ============================================================================

@app.route("/ads/start-session", methods=["POST"])
@require_auth
def start_ad_session():
    """
    Start a new ad session - creates tracking record before showing ad

    Request body:
        zone_id: Monetag zone ID (required)
        ad_type: Type of ad (default: 'onclick')

    Returns:
        200: Success with session_id and monetag_click_id
        401: Unauthorized
        402: Daily limit reached
        500: Server error
    """
    try:
        user = get_current_user()
        if not user:
            return jsonify({"error": "Unauthorized"}), 401

        user_id = user["user_id"]
        data = request.get_json() or {}

        zone_id = data.get('zone_id', monetag_api.MONETAG_ZONE_ID)
        ad_type = data.get('ad_type', 'onclick')

        print(f"\n{'='*60}")
        print(f"📺 STARTING AD SESSION")
        print(f"{'='*60}")
        print(f"User ID: {user_id}")
        print(f"Zone ID: {zone_id}")
        print(f"Ad Type: {ad_type}")

        # Generate unique click ID for Monetag tracking
        monetag_click_id = monetag_api.generate_monetag_click_id(user_id)

        # Create ad session in database
        import uuid
        from datetime import datetime
        session_id = str(uuid.uuid4())

        session_data = {
            'id': session_id,
            'user_id': user_id,
            'monetag_click_id': monetag_click_id,
            'zone_id': zone_id,
            'ad_type': ad_type,
            'status': 'pending',
            'monetag_verified': False,
            'created_at': datetime.utcnow().isoformat(),
            'ip_address': request.remote_addr,
            'user_agent': request.headers.get('User-Agent')
        }

        # Insert into ad_sessions table
        response = supabase.table('ad_sessions').insert(session_data).execute()

        if not response.data:
            print(f"❌ Failed to create ad session")
            return jsonify({
                "success": False,
                "error": "Failed to create ad session"
            }), 500

        print(f"✅ Ad session created: {session_id}")
        print(f"🆔 Monetag click ID: {monetag_click_id}")
        print(f"{'='*60}\n")

        return jsonify({
            "success": True,
            "session_id": session_id,
            "monetag_click_id": monetag_click_id
        }), 200

    except Exception as e:
        print(f"❌ Error in /ads/start-session: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/ads/check-session/<session_id>", methods=["GET"])
@require_auth
def check_ad_session(session_id):
    """
    Check if an ad session has been verified by Monetag

    Returns:
        200: Status of the session (verified or not)
        404: Session not found
        401: Unauthorized
    """
    try:
        user = get_current_user()
        if not user:
            return jsonify({"error": "Unauthorized"}), 401

        # Fetch session
        response = supabase.table('ad_sessions').select('*').eq('id', session_id).execute()

        if not response.data:
            return jsonify({
                "success": False,
                "error": "Session not found"
            }), 404

        session = response.data[0]

        # Verify user owns this session
        if session['user_id'] != user['user_id']:
            return jsonify({
                "success": False,
                "error": "Unauthorized"
            }), 401

        return jsonify({
            "success": True,
            "session_id": session_id,
            "status": session['status'],
            "verified": session.get('monetag_verified', False),
            "created_at": session['created_at']
        }), 200

    except Exception as e:
        print(f"❌ Error in /ads/check-session: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/ads/check-postback-status", methods=["POST"])
@require_auth
def check_postback_status():
    """
    Check if Monetag postback has been received for an ad session
    Use this to poll and wait for postback without claiming reward yet

    Request body:
        session_id: Ad session ID (required)

    Returns:
        200: {postback_received: true} - Ready to claim reward
        202: {postback_received: false, waiting: true} - Still waiting
    """
    try:
        user = get_current_user()
        if not user:
            return jsonify({"error": "Unauthorized"}), 401

        user_id = user["user_id"]
        data = request.get_json() or {}
        session_id = data.get('session_id')

        if not session_id:
            return jsonify({
                "success": False,
                "error": "session_id is required"
            }), 400

        # Fetch session
        response = supabase.table('ad_sessions').select('*').eq('id', session_id).execute()

        if not response.data:
            return jsonify({
                "success": False,
                "error": "Session not found"
            }), 404

        session = response.data[0]

        # Verify user owns this session
        if session['user_id'] != user_id:
            return jsonify({
                "success": False,
                "error": "Unauthorized"
            }), 401

        # Check postback status
        postback_received = session.get('monetag_verified', False)

        if postback_received:
            return jsonify({
                "success": True,
                "postback_received": True,
                "message": "Postback received - ready to claim reward",
                "session_id": session_id
            }), 200
        else:
            return jsonify({
                "success": True,
                "postback_received": False,
                "waiting": True,
                "message": "Waiting for Monetag postback",
                "session_id": session_id
            }), 202

    except Exception as e:
        print(f"❌ Error in /ads/check-postback-status: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


# ============================================================================
# MONETAG API INTEGRATION ENDPOINTS
# ============================================================================

@app.route("/api/monetag/postback", methods=["POST", "GET"])
def monetag_postback():
    """
    Direct Monetag postback handler - receives & validates ad completions

    Configure this URL in your Monetag dashboard under Settings → Postback URL:
    https://friendly-potato-g4jv5rrr69953p9xv-5000.app.github.dev/api/monetag/postback?ymid={ymid}&revenue={estimated_price}&reward_event_type={reward_event_type}

    Accepts both:
    1. Query parameters (from Monetag macros):
        - ymid: Unique click identifier (mapped to click_id)
        - revenue or estimated_price: Revenue from ad
        - reward_event_type: 'valued' (completed) or 'not_valued' (failed)
        - zone_id: Optional ad zone identifier (for validation)

    2. JSON/Form data (for manual testing):
        - ymid or click_id: Unique click identifier
        - estimated_price or revenue: Revenue generated from ad
        - reward_event_type or status: 'valued'/'not_valued' or 'completed'/'failed'
        - zone_id: Optional ad zone identifier

    Returns:
        200: Success - ad recorded
        403: Forbidden - invalid signature
        400: Bad request - missing required fields
    """
    try:
        # Extract data from query parameters, form, or JSON body
        # Priority: Query params > Form > JSON
        if request.args:
            data = request.args.to_dict()
        elif request.is_json:
            data = request.json or {}
        else:
            data = request.form.to_dict()

        signature = request.headers.get('X-Monetag-Signature', '')

        print(f"\n{'='*80}")
        print(f"💰 MONETAG POSTBACK RECEIVED")
        print(f"{'='*80}")
        print(f"📨 Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"📦 Raw Data: {data}")
        print(f"📍 Request Type: {'Query Parameters' if request.args else 'JSON/Form'}")
        if signature:
            print(f"🔐 Signature: {signature[:20]}...")

        # Map Monetag macro parameters to internal format
        # Monetag sends: ymid, estimated_price, reward_event_type
        # We expect: click_id, revenue, status
        click_id = data.get('click_id') or data.get('ymid')
        zone_id = data.get('zone_id')
        user_id = data.get('user_id')

        # Handle revenue (could be 'revenue' or 'estimated_price')
        revenue_str = data.get('revenue') or data.get('estimated_price', '0')
        try:
            revenue = float(revenue_str)
        except (ValueError, TypeError):
            revenue = 0.0

        # Handle status (could be 'status', 'reward_event_type', 'yes'/'no' or 'valued'/'not_valued')
        status = data.get('status') or data.get('reward_event_type', 'completed')
        # Convert Monetag status format to our format
        # Monetag uses: 'valued' (user completed) or 'not_valued' (user skipped)
        # Legacy format: 'yes'/'true'/'1' or 'no'/'false'/'0'
        # NOTE: All postbacks = participation, so all = completed
        if status in ['yes', 'true', '1', 'valued', 'no', 'false', '0', 'not_valued']:
            status = 'completed'  # All postbacks = participation completed

        # Validation: Require click_id
        if not click_id:
            print(f"❌ REJECTED: Missing click_id/ymid")
            print(f"{'='*80}\n")
            return jsonify({"error": "Missing click_id or ymid"}), 400

        print(f"\n📋 Data Extraction:")
        print(f"   ✓ Click ID: {click_id}")
        print(f"   ✓ Zone ID: {zone_id}")
        print(f"   ✓ Revenue: ${revenue}")
        print(f"   ✓ Status: {status}")

        # 1. Signature verification (optional - skip in dev)
        if signature:
            is_valid = monetag_api.verify_monetag_signature(data, signature)
            if not is_valid:
                print(f"\n🔐 Signature Validation: FAILED")
                print(f"{'='*80}\n")
                return jsonify({"error": "Invalid signature"}), 403
            else:
                print(f"✅ Signature Validation: PASSED")
        else:
            print(f"⚠️  No signature provided (dev/test mode)")

        # 2. Validate zone ID if configured
        if zone_id:
            is_valid_zone = monetag_api.validate_zone_id(zone_id)
            if not is_valid_zone:
                print(f"\n❌ Zone Validation: FAILED - Invalid zone_id: {zone_id}")
                print(f"{'='*80}\n")
                return jsonify({"error": "Invalid zone_id"}), 400
            else:
                print(f"✅ Zone Validation: PASSED")

        # 3. Try to find and update session in database
        ad_processed = False
        try:
            print(f"\n🔗 DATABASE LOOKUP:")
            print(f"   Looking for session with monetag_click_id: {click_id}")

            session_response = supabase.table('ad_sessions').select('*').eq('monetag_click_id', click_id).execute()

            if session_response.data:
                session = session_response.data[0]
                print(f"   ✅ FOUND matching session")
                print(f"   Session ID: {session['id']}")

                # Update session status
                update_data = {
                    'monetag_verified': True,
                    'monetag_revenue': revenue,
                    'completed_at': datetime.utcnow().isoformat()
                }
                # Mark session as completed (regardless of valued/non_valued)
                # User will be rewarded for participation
                update_data['status'] = 'completed'
                print(f"   ✅ Ad postback received - status is {status}")
                print(f"   Session marked as completed")

                try:
                    update_response = supabase.table('ad_sessions').update(update_data).eq('monetag_click_id', click_id).execute()
                    print(f"   ✅ Database updated with monetag_verified=true")
                    print(f"   💰 Revenue recorded: ${revenue}")
                    print(f"   📝 Update response: {update_response.data}")
                    ad_processed = True
                except Exception as update_error:
                    print(f"   ❌ ERROR during database update: {update_error}")
                    import traceback
                    traceback.print_exc()
                    print(f"   ⚠️  Will still accept postback but session not updated")
            else:
                print(f"   ❌ NO matching session found")
                print(f"   ⚠️  Checking all ad_sessions to help debug...")

                # Debug: show all recent sessions
                all_sessions = supabase.table('ad_sessions').select('id, monetag_click_id, user_id').limit(5).execute()
                if all_sessions.data:
                    print(f"   📊 Recent sessions in database:")
                    for sess in all_sessions.data:
                        print(f"      - {sess['id']}: monetag_click_id={sess.get('monetag_click_id', 'NULL')}")

                print(f"   💡 Will still accept postback (session may be created later)")


                # Create new ad record if needed
                try:
                    supabase.table('ad_completions').insert({
                        'click_id': click_id,
                        'zone_id': zone_id,
                        'user_id': user_id,
                        'revenue': revenue,
                        'status': status,
                        'received_at': time.time()
                    }).execute()
                    print(f"   ✅ New ad_completions record created")
                    ad_processed = True
                except Exception as insert_err:
                    print(f"   ⚠️  Could not create record: {insert_err}")
                    ad_processed = True  # Still accept the postback

        except Exception as db_error:
            print(f"\n⚠️  Database error: {db_error}")
            print(f"   💡 Will still accept postback (validation passed)")
            ad_processed = True  # Accept regardless

        # 4. Success response
        print(f"\n✅ POSTBACK ACCEPTED & PROCESSED")
        print(f"{'='*80}\n")

        # Log to temporary cache
        log_postback_received(click_id, revenue, status)

        return jsonify({
            "success": True,
            "message": "Postback received and validated",
            "click_id": click_id,
            "revenue": revenue,
            "processed": ad_processed
        }), 200

    except Exception as e:
        print(f"\n❌ ERROR in /api/monetag/postback: {e}")
        import traceback
        traceback.print_exc()
        print(f"{'='*80}\n")
        return jsonify({"error": str(e)}), 500


@app.route("/api/monetag/verify/<click_id>", methods=["GET"])
@require_auth
def monetag_verify_click(click_id):
    """
    Verify ad completion with MoneyTag API

    This endpoint queries the MoneyTag API to verify if an ad was completed.
    Used as a double-check in addition to the postback.

    Args:
        click_id: The MoneyTag click ID to verify

    Returns:
        200: Verification result with completion status
        401: Unauthorized
        500: Server error
    """
    try:
        user = get_current_user()
        if not user:
            return jsonify({"error": "Unauthorized"}), 401

        print(f"\n🔍 Verifying MoneyTag ad completion for click_id: {click_id}")

        # Query MoneyTag API
        verification = monetag_api.verify_ad_completion_with_api(click_id)

        if verification:
            print(f"✅ MoneyTag verification result: {verification}")
            return jsonify({
                "success": True,
                "verified": verification['completed'],
                "revenue": verification['revenue'],
                "status": verification['status'],
                "timestamp": verification['timestamp']
            }), 200
        else:
            print(f"⚠️ MoneyTag API verification failed or timed out")
            return jsonify({
                "success": False,
                "error": "Failed to verify with MoneyTag API"
            }), 500

    except Exception as e:
        print(f"❌ Error in /api/monetag/verify: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/api/monetag/stats", methods=["GET"])
@require_auth
def monetag_get_stats():
    """
    Get MoneyTag statistics for date range

    Query params:
        date_from: Start date (YYYY-MM-DD), defaults to today
        date_to: End date (YYYY-MM-DD), defaults to today

    Returns:
        200: Statistics from MoneyTag
        401: Unauthorized
        500: Server error
    """
    try:
        user = get_current_user()
        if not user:
            return jsonify({"error": "Unauthorized"}), 401

        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')

        print(f"\n📊 Fetching MoneyTag statistics from {date_from} to {date_to}")

        stats = monetag_api.get_monetag_statistics(date_from, date_to)

        if stats:
            return jsonify({
                "success": True,
                "stats": stats
            }), 200
        else:
            return jsonify({
                "success": False,
                "error": "Failed to fetch statistics from MoneyTag"
            }), 500

    except Exception as e:
        print(f"❌ Error in /api/monetag/stats: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/api/monetag/config", methods=["GET"])
def monetag_get_config():
    """
    Get MoneyTag configuration status (public endpoint for frontend)

    Returns:
        200: Configuration status
    """
    try:
        config = monetag_api.check_monetag_config()

        # Add zone ID for frontend
        config['zone_id'] = monetag_api.MONETAG_ZONE_ID

        return jsonify({
            "success": True,
            "config": config
        }), 200

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/workflows/list", methods=["GET"])
def list_workflows():
    """
    List all available workflows
    
    Returns:
        200: List of workflows with metadata
    """
    try:
        from workflow_manager import get_workflow_manager
        
        workflow_manager = get_workflow_manager()
        workflows = workflow_manager.list_workflows()
        
        return jsonify({
            "success": True,
            "workflows": workflows
        }), 200
    
    except Exception as e:
        print(f"Failed to list workflows: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/workflows/execute", methods=["POST"])
@require_auth
def execute_workflow():
    """
    Execute a workflow
    
    Request:
        - workflow_id: Workflow ID
        - file: Input file (multipart/form-data)
        - options: Optional workflow options (JSON)
    
    Returns:
        200: Job ID and execution ID
    """
    try:
        from workflow_manager import get_workflow_manager
        from jobs import create_job
        import asyncio

        maintenance_flag = Path(__file__).parent / ".maintenance_mode"
        if maintenance_flag.exists():
            return jsonify({
                "success": False,
                "error": "System is in maintenance mode. Please try again later."
            }), 503

        from supabase_failover import get_failover_manager
        failover_manager = get_failover_manager()
        if failover_manager.is_maintenance_mode:
            return jsonify({
                "success": False,
                "error": "System is under maintenance. New workflow jobs are temporarily disabled."
            }), 503

        current_user = get_current_user()
        user_id = current_user['user_id']
        
        print(f"📋 Workflow execution request - Form data: {dict(request.form)}")
        print(f"📋 Files: {list(request.files.keys())}")
        
        workflow_id = request.form.get('workflow_id') or request.json.get('workflow_id')
        
        if not workflow_id:
            return jsonify({
                "success": False,
                "error": "workflow_id is required"
            }), 400
        
        workflow_manager = get_workflow_manager()
        workflow = workflow_manager.get_workflow(workflow_id)
        
        if not workflow:
            return jsonify({
                "success": False,
                "error": f"Workflow {workflow_id} not found"
            }), 404
        
        input_file = None
        input_image_url = None
        
        if 'file' in request.files:
            input_file = request.files['file']
            
            # Upload file to Cloudinary and store URL for resume capability
            from cloudinary_manager import get_cloudinary_manager
            cloudinary = get_cloudinary_manager()
            
            try:
                # Read file bytes from FileStorage object
                file_bytes = input_file.read()
                file_name = input_file.filename or 'workflow_input.jpg'
                
                upload_result = cloudinary.upload_image_from_bytes(
                    file_bytes, 
                    file_name,
                    folder_name="workflow-inputs"
                )
                
                if upload_result.get('success') is False:
                    raise Exception(upload_result.get('error', 'Upload failed'))
                
                input_image_url = upload_result['secure_url']
                print(f"📤 Uploaded workflow input image: {input_image_url}")
            except Exception as upload_error:
                print(f"⚠️ Failed to upload input image: {upload_error}")
                # Reset file pointer for workflow to use
                input_file.seek(0)
        
        job_result = create_job(
            user_id=user_id,
            prompt=f"Workflow: {workflow['name']}",
            model=workflow_id,
            job_type='workflow',
            image_url=input_image_url,
            aspect_ratio='1:1'
        )
        
        if not job_result.get('success'):
            return jsonify({
                "success": False,
                "error": job_result.get('error', 'Failed to create job')
            }), 400
        
        job = job_result['job']
        job_id = job['id']
        
        def run_workflow():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    workflow_manager.execute_workflow(
                        workflow_id=workflow_id,
                        input_data=input_image_url or input_file,
                        user_id=user_id,
                        job_id=job_id
                    )
                )
            except Exception as e:
                print(f"❌ Workflow execution error: {e}")
            finally:
                loop.close()
        
        import threading
        thread = threading.Thread(target=run_workflow, daemon=True)
        thread.start()
        
        return jsonify({
            "success": True,
            "job_id": job_id,
            "stream_url": f"/jobs/{job_id}/stream"
        }), 200
    
    except Exception as e:
        print(f"❌ Failed to execute workflow: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/workflows/retry/<job_id>", methods=["POST"])
@require_auth
def retry_workflow(job_id):
    """
    Retry a failed workflow from last checkpoint
    
    Returns:
        200: Success message
    """
    try:
        from workflow_manager import get_workflow_manager
        import asyncio

        maintenance_flag = Path(__file__).parent / ".maintenance_mode"
        if maintenance_flag.exists():
            return jsonify({
                "success": False,
                "error": "System is in maintenance mode. Please try again later."
            }), 503

        current_user = get_current_user()
        user_id = current_user['user_id']
        
        response = supabase.table('workflow_executions')\
            .select('*')\
            .eq('job_id', job_id)\
            .eq('user_id', user_id)\
            .single()\
            .execute()
        
        if not response.data:
            return jsonify({
                "success": False,
                "error": "Workflow execution not found"
            }), 404
        
        execution = response.data
        
        workflow_manager = get_workflow_manager()
        
        def run_retry():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    workflow_manager.resume_workflow(
                        execution_id=execution['id'],
                        job_id=job_id
                    )
                )
            except Exception as e:
                print(f"Workflow retry error: {e}")
            finally:
                loop.close()
        
        import threading
        thread = threading.Thread(target=run_retry, daemon=True)
        thread.start()
        
        return jsonify({
            "success": True,
            "message": "Workflow retry started",
            "execution_id": execution['id'],
            "resume_from_step": execution['current_step']
        }), 200
    
    except Exception as e:
        print(f"Failed to retry workflow: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/workflows/execution/<job_id>", methods=["GET"])
@require_auth
def get_workflow_execution(job_id):
    """
    Get workflow execution details
    
    Returns:
        200: Execution details with checkpoints
    """
    try:
        current_user = get_current_user()
        user_id = current_user['user_id']
        
        response = supabase.table('workflow_executions')\
            .select('*')\
            .eq('job_id', job_id)\
            .eq('user_id', user_id)\
            .single()\
            .execute()
        
        if not response.data:
            return jsonify({
                "success": False,
                "error": "Workflow execution not found"
            }), 404
        
        execution = response.data
        
        can_retry = execution['status'] in ['pending_retry', 'failed']
        
        return jsonify({
            "success": True,
            "execution": {
                **execution,
                "can_retry": can_retry
            }
        }), 200
    
    except Exception as e:
        print(f"Failed to get workflow execution: {e}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


def startup_sync_worker():
    """
    Background worker for startup sync (non-blocking)
    Allows Flask to start immediately while sync runs in background
    """
    global sync_status
    
    sync_status["running"] = True
    sync_status["started_at"] = datetime.now().isoformat()
    
    print("\n" + "="*60)
    print("[STARTUP] Running data transfer from OLD to NEW account...")
    print("="*60)
    
    try:
        from startup_sync import run_startup_sync
        run_startup_sync()
        sync_status["completed"] = True
        print("[STARTUP] Startup sync completed successfully")
    except Exception as e:
        sync_status["error"] = str(e)
        print(f"[STARTUP] Startup sync failed: {e}")
        print("[STARTUP] Continuing with app operation...")
    finally:
        sync_status["running"] = False


def hourly_sync_worker():
    """
    Background worker that runs hourly Supabase sync
    Runs once on startup, then every hour
    """
    from smart_hourly_sync import run_sync
    
    enable_sync = os.getenv('ENABLE_HOURLY_SYNC', 'false').lower() == 'true'
    
    if not enable_sync:
        print("[SYNC] Hourly sync is DISABLED (set ENABLE_HOURLY_SYNC=true in .env)")
        return
    
    print("\n" + "="*60)
    print("[SYNC] Hourly Sync Worker Started")
    print("="*60)
    
    # Run sync immediately on startup
    print("[SYNC] Running initial sync on startup...")
    try:
        run_sync()
        print("[SYNC] Initial sync completed")
    except Exception as e:
        print(f"[SYNC] Initial sync failed: {e}")
    
    # Then run every hour
    while True:
        try:
            # Wait 1 hour (3600 seconds)
            time.sleep(3600)
            
            print("\n" + "="*60)
            print(f"[SYNC] Running hourly sync at {datetime.now().isoformat()}")
            print("="*60)
            
            run_sync()
            
            print(f"[SYNC] Hourly sync completed at {datetime.now().isoformat()}")
            
        except Exception as e:
            print(f"[SYNC] Hourly sync error: {e}")
            # Continue running even if one sync fails
            continue


if __name__ == "__main__":
    print("\n" + "="*60)
    print("FLASK BACKEND STARTING")
    print("="*60)
    print(f"Discord Channel ID: {CHANNEL_ID}")
    masked_token = f"{BOT_TOKEN[:4]}...{BOT_TOKEN[-4:]}"
    print(f"Bot Token: {masked_token}")
    print(f"CORS enabled for frontend access")
    
    # Use PORT from environment (Koyeb) or default to 5000
    port = int(os.getenv("PORT", 5000))
    host = "0.0.0.0"
    
    # Start ngrok tunnel if NGROK_TUNNEL is enabled
    ngrok_url = None
    ngrok_tunnel_enabled = os.getenv("NGROK_TUNNEL", "false").lower() == "true"
    
    if ngrok_tunnel_enabled and NGROK_AVAILABLE:
        ngrok_token = os.getenv("NGROK_AUTH_TOKEN")
        if ngrok_token:
            try:
                print("\n🔗 Starting ngrok tunnel...")
                # Set ngrok auth token
                ngrok.set_auth_token(ngrok_token)
                # Start tunnel pointing to local Flask server
                public_url = ngrok.connect(port, "http")
                ngrok_url = public_url.public_url
                print(f"✅ Ngrok tunnel started successfully!")
            except Exception as e:
                print(f"⚠️  Ngrok tunnel failed to start: {e}")
                print("   Continuing with localhost only...")
        else:
            print("⚠️  NGROK_TUNNEL=true but NGROK_AUTH_TOKEN not found")
            print("   Continuing with localhost only...")
    elif ngrok_tunnel_enabled and not NGROK_AVAILABLE:
        print("⚠️  NGROK_TUNNEL=true but pyngrok not installed")
        print("   Install with: pip install pyngrok")
        print("   Continuing with localhost only...")
    
    # Display server info
    backend_url = os.getenv("BACKEND_URL", f"http://localhost:{port}")
    print("\n" + "="*60)
    print("🌐 SERVER CONFIGURATION")
    print("="*60)
    print(f"📍 Local Server: http://localhost:{port}")
    if ngrok_url:
        print(f"🌐 Public Ngrok URL: {ngrok_url}")
    print(f"🌐 Backend URL: {backend_url}")
    print("="*60)
    print("\nAvailable Endpoints:")
    print(f"   CORE:")
    print(f"   - GET  /health          : Health check (for Koyeb)")
    print(f"   - GET  /list-models     : List available AI models")
    print(f"\n   AUTHENTICATION:")
    print(f"   - POST /auth/magic-link : Send magic link to email")
    print(f"   - GET  /auth/verify     : Verify magic link token")
    print(f"   - GET  /auth/me         : Get current user info")
    print(f"   - POST /auth/logout     : Logout current user")
    print(f"\n   JOBS:")
    print(f"   - POST   /jobs          : Create new job")
    print(f"   - GET    /jobs          : Get user's jobs")
    print(f"   - GET    /jobs/<id>     : Get specific job")
    print(f"   - PATCH  /jobs/<id>     : Update job status")
    print(f"   - DELETE /jobs/<id>     : Cancel job")
    print(f"   - GET    /jobs/stats    : Get job statistics")
    print(f"\n   WORKER (Internal):")
    print(f"   - GET  /worker/next-job       : Get next pending job")
    print(f"   - POST /worker/job/<id>/complete : Mark job complete")
    print(f"\n   MEGA STORAGE:")
    print(f"   - POST /mega/upload-image     : Upload image to Mega cloud")
    print("="*60)
    print("Debug mode enabled - all requests will be logged")
    print("="*60 + "\n")

    # Debug endpoints for Monetag postback
    @app.route("/api/monetag/postback-url", methods=["GET"])
    def get_postback_url_endpoint():
        """Get the configured Monetag postback URL"""
        return jsonify(get_postback_url_config()), 200

    @app.route("/api/monetag/stats", methods=["GET"])
    def get_postback_stats_endpoint():
        """Get postback statistics and recent activity"""
        return jsonify(get_postback_stats()), 200

    @app.route("/api/monetag/recent-postbacks", methods=["GET"])
    def get_recent_postbacks_endpoint():
        """Get list of recent postbacks received"""
        limit = request.args.get('limit', 20, type=int)
        return jsonify({
            "postbacks": get_recent_postbacks(limit),
            "count": len(get_recent_postbacks(limit))
        }), 200

    @app.route("/api/monetag/debug-log", methods=["GET"])
    def get_debug_log_endpoint():
        """Get formatted debug log of postback activity"""
        return format_postback_log(), 200, {'Content-Type': 'text/plain; charset=utf-8'}

    @app.route("/api/monetag/clear-cache", methods=["POST"])
    def clear_postback_cache_endpoint():
        """Clear postback cache (for testing)"""
        stats = clear_postback_cache()
        return jsonify({
            "message": "Postback cache cleared",
            "previous_stats": stats
        }), 200

    @app.route("/api/notify-frontend-error", methods=["POST"])
    def notify_frontend_error():
        """
        Endpoint for frontend to report critical errors for instant notifications
        Used when users encounter server maintenance or unavailable errors
        """
        try:
            data = request.get_json() or {}
            error_type = data.get("error_type", "unknown")
            user_action = data.get("user_action", "unknown")
            
            if error_type == "server_unavailable":
                notify_error(
                    ErrorType.SUPABASE_CONNECTION_DOWN,
                    f"User encountered server unavailable error while attempting: {user_action}",
                    context={
                        "user_action": user_action,
                        "error_type": error_type,
                        "source": "frontend"
                    }
                )
            elif error_type == "maintenance_mode":
                notify_error(
                    ErrorType.REALTIME_LISTENER_CRASHED,
                    f"User blocked by maintenance mode during: {user_action}",
                    context={
                        "user_action": user_action,
                        "error_type": error_type,
                        "source": "frontend"
                    }
                )
            
            return jsonify({"success": True, "message": "Error notification sent"}), 200
        except Exception as e:
            print(f"❌ Frontend error notification failed: {e}")
            return jsonify({"success": False, "error": str(e)}), 500

    # ============================================
    # Admin Endpoints
    # ============================================
    
    @app.route("/admin/priority-lock", methods=["POST"])
    def admin_priority_lock():
        """
        Enable/disable priority lock mode remotely.
        When enabled, job_worker_realtime.py will only process Priority 1 jobs.
        Priority 2 and 3 jobs stay pending until lock is disabled.
        Requires SECRET_KEY in Authorization header.
        Body: {"enable": true/false}
        """
        try:
            secret_key = os.getenv("SECRET_KEY")
            if not secret_key:
                return jsonify({"success": False, "error": "SECRET_KEY not configured"}), 500

            auth_header = request.headers.get("Authorization")
            if not auth_header or not auth_header.startswith("Bearer "):
                return jsonify({"success": False, "error": "Unauthorized"}), 401

            provided_secret = auth_header.replace("Bearer ", "")
            if provided_secret != secret_key:
                return jsonify({"success": False, "error": "Invalid secret key"}), 403

            data = request.get_json() or {}
            enable = data.get("enable", True)

            supabase.table("system_flags").update({
                "value": enable,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("key", "priority_lock").execute()

            if enable:
                print("=" * 60)
                print("PRIORITY LOCK ACTIVATED")
                print("=" * 60)
                print("Worker will only process Priority 1 jobs")
                print("Priority 2 and 3 jobs held until lock is disabled")
                print("=" * 60)
                return jsonify({
                    "success": True,
                    "message": "Priority lock enabled. Only P1 jobs will be processed.",
                    "mode": "enabled"
                }), 200
            else:
                print("=" * 60)
                print("PRIORITY LOCK DISABLED")
                print("=" * 60)
                print("Worker will resume processing all priority jobs")
                print("Pending P2/P3 jobs will be flushed automatically by worker")
                print("=" * 60)
                return jsonify({
                    "success": True,
                    "message": "Priority lock disabled. All jobs will be processed.",
                    "mode": "disabled"
                }), 200

        except Exception as e:
            print(f"❌ Admin priority lock error: {e}")
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/admin/maintenance", methods=["POST"])
    def admin_maintenance():
        """
        Enable/disable maintenance mode remotely (for Koyeb deployment)
        Requires ADMIN_SECRET in Authorization header
        Body: {"enable": true/false}
        """
        try:
            admin_secret = os.getenv("ADMIN_SECRET")
            if not admin_secret:
                return jsonify({"success": False, "error": "Admin endpoint not configured"}), 500
            
            auth_header = request.headers.get("Authorization")
            if not auth_header or not auth_header.startswith("Bearer "):
                return jsonify({"success": False, "error": "Unauthorized"}), 401
            
            provided_secret = auth_header.replace("Bearer ", "")
            if provided_secret != admin_secret:
                return jsonify({"success": False, "error": "Invalid admin secret"}), 403
            
            data = request.get_json() or {}
            enable = data.get("enable", True)
            
            maintenance_flag = Path(__file__).parent / ".maintenance_mode"
            
            if enable:
                maintenance_flag.touch()
                print("="*60)
                print("MAINTENANCE MODE ACTIVATED")
                print("="*60)
                print("Service will block new jobs but remain running")
                print("="*60)
                return jsonify({
                    "success": True,
                    "message": "Maintenance mode enabled. New jobs blocked.",
                    "mode": "enabled"
                }), 200
            else:
                if maintenance_flag.exists():
                    maintenance_flag.unlink()
                print("="*60)
                print("MAINTENANCE MODE DISABLED")
                print("="*60)
                print("Service accepting new jobs")
                print("="*60)
                return jsonify({
                    "success": True,
                    "message": "Maintenance mode disabled. Accepting new jobs.",
                    "mode": "disabled"
                }), 200
            
        except Exception as e:
            print(f"❌ Admin maintenance error: {e}")
            return jsonify({"success": False, "error": str(e)}), 500
    
    # ============================================
    # Contact Form Endpoint
    # ============================================
    
    @app.route("/contact", methods=["POST"])
    def contact_form():
        """
        Handle contact form submissions and send to Telegram
        """
        try:
            data = request.get_json()
            
            if not data:
                return jsonify({"success": False, "error": "No data provided"}), 400
            
            name = data.get("name", "").strip()
            email = data.get("email", "").strip()
            subject = data.get("subject", "").strip()
            message = data.get("message", "").strip()
            user_id = data.get("user_id", "Not logged in")
            
            if not all([name, email, subject, message]):
                return jsonify({"success": False, "error": "All fields are required"}), 400
            
            telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
            telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")
            
            if not telegram_bot_token or not telegram_chat_id:
                print("❌ Telegram not configured")
                return jsonify({"success": False, "error": "Contact system not configured"}), 500
            
            telegram_message = f"""📬 *New Contact Form Submission*

👤 *Name:* {name}
📧 *Email:* {email}
🆔 *User ID:* {user_id}

📌 *Subject:* {subject}

💬 *Message:*
{message}

---
_Sent from Atool Contact Form_"""
            
            telegram_url = f"https://api.telegram.org/bot{telegram_bot_token}/sendMessage"
            payload = {
                "chat_id": telegram_chat_id,
                "text": telegram_message,
                "parse_mode": "Markdown"
            }
            
            response = requests.post(telegram_url, json=payload, timeout=10)
            response_data = response.json()
            
            if response_data.get("ok"):
                print(f"✅ Contact form sent to Telegram from {email}")
                return jsonify({"success": True, "message": "Message sent successfully"}), 200
            else:
                print(f"❌ Telegram API error: {response_data}")
                return jsonify({"success": False, "error": "Failed to send message"}), 500
                
        except Exception as e:
            print(f"❌ Contact form error: {e}")
            return jsonify({"success": False, "error": str(e)}), 500

    # Fix corrupted jobs (pending status but already has results)
    def fix_corrupted_jobs():
        """Fix jobs that have image_url/video_url but status is still 'pending'"""
        try:
            print("\n" + "="*60)
            print("🔧 CHECKING FOR CORRUPTED JOBS")
            print("="*60)
            
            # Find jobs with pending status but have image_url or video_url
            response = supabase.table("jobs").select("job_id, image_url, video_url, status").eq("status", "pending").execute()
            
            if response.data:
                corrupted_jobs = [job for job in response.data if job.get("image_url") or job.get("video_url")]
                
                if corrupted_jobs:
                    print(f"Found {len(corrupted_jobs)} corrupted job(s) - fixing...")
                    
                    for job in corrupted_jobs:
                        job_id = job["job_id"]
                        try:
                            # Update status to completed
                            supabase.table("jobs").update({
                                "status": "completed",
                                "progress": 100,
                                "error_message": None
                            }).eq("job_id", job_id).execute()
                            
                            print(f"  ✅ Fixed job {job_id}")
                        except Exception as fix_error:
                            print(f"  ❌ Failed to fix job {job_id}: {fix_error}")
                    
                    print(f"✅ Fixed {len(corrupted_jobs)} corrupted job(s)")
                else:
                    print("✅ No corrupted jobs found")
            else:
                print("✅ No pending jobs found")
            
            print("="*60 + "\n")
        except Exception as e:
            print(f"❌ Error checking for corrupted jobs: {e}")
            import traceback
            traceback.print_exc()
    
    # Run cleanup on startup
    fix_corrupted_jobs()
    
    # Check if startup sync is enabled
    enable_startup_sync = os.getenv("ENABLE_STARTUP_SYNC", "true").lower() == "true"
    
    if enable_startup_sync:
        # Run startup sync in background thread (non-blocking) - Flask starts immediately!
        startup_thread = threading.Thread(target=startup_sync_worker, daemon=True, name="StartupSync")
        startup_thread.start()
        print("[SYNC] Startup sync worker thread started (non-blocking)")
    else:
        print("[SYNC] Startup sync disabled via ENABLE_STARTUP_SYNC=false")
    
    # Start hourly sync worker in background thread
    sync_thread = threading.Thread(target=hourly_sync_worker, daemon=True, name="HourlySyncWorker")
    sync_thread.start()
    print("[SYNC] Hourly sync worker thread started")

    # Flask starts IMMEDIATELY - no blocking operations before this!
    print("\n" + "="*60)
    print(f"🚀 FLASK SERVER STARTING ON PORT {port}")
    print("="*60)
    app.run(host="0.0.0.0", port=port, debug=False)

