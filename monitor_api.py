"""
Monitor API — shared observability blueprint for BOTH services:
  - app.py                 (Render web service,  role="backend")
  - job_worker_realtime.py (Render worker service, role="worker")

Provides (all token-protected except the dashboard HTML itself):
  GET  /monitor                  -> serves monitor.html dashboard
  GET  /monitor/status           -> service health, realtime state, smart-restart state
  GET  /monitor/jobs             -> running / pending / completed / failed jobs from Supabase
  GET  /monitor/logs             -> in-memory ring buffer of recent log records
  GET  /monitor/errors           -> in-memory ring buffer of recent ERROR/CRITICAL records
  GET  /monitor/requests         -> in-memory ring buffer of recent HTTP requests
  GET  /monitor/restart-history  -> rows from system_restarts table
  POST /monitor/maintenance      -> {enable: bool} toggle local .maintenance_mode flag
  POST /monitor/restart          -> {scope, force} trigger smart drain-and-restart

Design notes:
  - Ring buffers are collections.deque(maxlen=...) guarded by a lock.
    Zero external dependencies. Lost on restart (durable history lives in
    Supabase: `jobs` + `system_restarts`).
  - The SAME logging handler feeds the log buffers AND the 1001 detector in
    smart_restart.py (one handler, two consumers).
  - CORS: monitor.html may be opened from disk (Origin: null) or served by the
    backend while calling the worker cross-origin. A blueprint-level
    after_request forces `Access-Control-Allow-Origin: *` on /monitor/*
    responses only. Data is protected by the bearer token, never by origin.
  - Authorization header values are redacted before entering the log buffer.
"""

import os
import re
import time
import threading
import logging
import traceback
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request, Response, g

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────
LOG_RING_MAX = 500        # last N log records kept in memory
ERROR_RING_MAX = 200      # last N error records kept in memory
REQUEST_RING_MAX = 300    # last N HTTP requests kept in memory

MAINTENANCE_FLAG_FILE = Path(__file__).parent / ".maintenance_mode"
MONITOR_HTML_FILE = Path(__file__).parent / "monitor.html"

# Paths never recorded in the request ring buffer (polling noise)
_REQUEST_SKIP_PREFIXES = ("/monitor",)
_REQUEST_SKIP_EXACT = ("/health", "/favicon.ico")

# ─────────────────────────────────────────────────────────────────────────────
# Module state
# ─────────────────────────────────────────────────────────────────────────────
_service_role = "unknown"      # "backend" | "worker"
_started_at = time.time()
_extra_status_fn = None        # optional callable returning a dict merged into /monitor/status
_initialized = False

_buf_lock = threading.Lock()
LOG_BUFFER = deque(maxlen=LOG_RING_MAX)
ERROR_BUFFER = deque(maxlen=ERROR_RING_MAX)
REQUEST_BUFFER = deque(maxlen=REQUEST_RING_MAX)

# Worker in-flight job gauge (incremented around job processing)
_active_jobs = 0
_active_jobs_lock = threading.Lock()

_REDACT_RE = re.compile(
    r"(Bearer\s+)[A-Za-z0-9\-_\.=]+"
    r"|((?:api[_-]?key|apikey|token|secret|password|authorization)['\"]?\s*[:=]\s*['\"]?)[^\s'\",}]+",
    re.IGNORECASE,
)


def _redact(text: str) -> str:
    """Mask bearer tokens / api keys / secrets before storing in buffers."""
    try:
        return _REDACT_RE.sub(lambda m: (m.group(1) or m.group(2) or "") + "[REDACTED]", text)
    except Exception:
        return text


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Worker active-jobs gauge (public helpers)
# ─────────────────────────────────────────────────────────────────────────────
def job_started():
    """Increment the in-flight job gauge (called by the worker job wrapper)."""
    global _active_jobs
    with _active_jobs_lock:
        _active_jobs += 1


def job_finished():
    """Decrement the in-flight job gauge (called by the worker job wrapper)."""
    global _active_jobs
    with _active_jobs_lock:
        _active_jobs = max(0, _active_jobs - 1)


def get_active_jobs() -> int:
    with _active_jobs_lock:
        return _active_jobs


# ─────────────────────────────────────────────────────────────────────────────
# Log capture handler — feeds ring buffers AND the 1001 detector
# ─────────────────────────────────────────────────────────────────────────────
class MonitorLogHandler(logging.Handler):
    """Captures log records into ring buffers and feeds the 1001 detector."""

    def emit(self, record):
        try:
            msg = record.getMessage()
        except Exception:
            return

        # Skip the dashboard's own polling noise (werkzeug access-log lines
        # for /monitor/* and /health) so real logs are not drowned out.
        if record.name == "werkzeug" and ("/monitor" in msg or "/health" in msg):
            return

        try:
            entry = {
                "ts": _now_iso(),
                "level": record.levelname,
                "logger": record.name,
                "message": _redact(msg)[:1000],
            }

            with _buf_lock:
                LOG_BUFFER.append(entry)
                if record.levelno >= logging.ERROR:
                    err_entry = dict(entry)
                    if record.exc_info:
                        try:
                            err_entry["traceback"] = _redact(
                                "".join(traceback.format_exception(*record.exc_info))
                            )[:2000]
                        except Exception:
                            pass
                    ERROR_BUFFER.append(err_entry)

            # ── 1001 "going away" detection (Supabase Realtime WebSocket) ──
            if record.levelno >= logging.ERROR and record.name.startswith("realtime"):
                lower = msg.lower()
                if "1001" in lower or "going away" in lower:
                    try:
                        from smart_restart import get_smart_restart_manager
                        get_smart_restart_manager().record_1001()
                    except Exception:
                        pass
        except Exception:
            # A monitoring handler must NEVER break the application
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Token auth
# ─────────────────────────────────────────────────────────────────────────────
def _get_monitor_token() -> str:
    return os.getenv("MONITOR_TOKEN") or os.getenv("ADMIN_SECRET") or ""


def _extract_request_token() -> str:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    return request.args.get("token", "")


def require_monitor_token(func):
    """Decorator: require Authorization: Bearer $MONITOR_TOKEN (or ?token=)."""
    from functools import wraps

    @wraps(func)
    def wrapper(*args, **kwargs):
        expected = _get_monitor_token()
        if not expected:
            return jsonify({
                "success": False,
                "error": "Monitor disabled: MONITOR_TOKEN / ADMIN_SECRET not configured"
            }), 503
        provided = _extract_request_token()
        if not provided or provided != expected:
            return jsonify({"success": False, "error": "Invalid or missing monitor token"}), 401
        return func(*args, **kwargs)

    return wrapper


# ─────────────────────────────────────────────────────────────────────────────
# Blueprint
# ─────────────────────────────────────────────────────────────────────────────
monitor_bp = Blueprint("monitor", __name__)


@monitor_bp.after_request
def _monitor_cors(response):
    """Force permissive CORS on /monitor/* only (data is token-protected)."""
    try:
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        # '*' origin is incompatible with credentials — monitor UI never uses them
        response.headers.pop("Access-Control-Allow-Credentials", None)
    except Exception:
        pass
    return response


@monitor_bp.route("/monitor", methods=["GET"])
def monitor_dashboard():
    """Serve the single-file dashboard (page itself asks for the token)."""
    try:
        html = MONITOR_HTML_FILE.read_text(encoding="utf-8")
        return Response(html, mimetype="text/html")
    except Exception as e:
        return jsonify({"success": False, "error": f"monitor.html not found: {e}"}), 404


@monitor_bp.route("/monitor/status", methods=["GET"])
@require_monitor_token
def monitor_status():
    """Service health + realtime state + smart-restart state."""
    memory_mb = None
    try:
        import psutil
        memory_mb = round(psutil.Process().memory_info().rss / (1024 * 1024), 1)
    except Exception:
        pass

    realtime = {"available": False}
    if _service_role == "backend":
        try:
            from realtime_manager import get_realtime_manager
            m = get_realtime_manager()
            realtime = {
                "available": True,
                "running": m.running,
                "last_event_age_seconds": round(m._get_last_event_age(), 1),
                "subscriptions": len(m.subscriptions),
            }
        except Exception as e:
            realtime = {"available": False, "error": str(e)}

    smart_restart_state = {}
    try:
        from smart_restart import get_smart_restart_manager
        smart_restart_state = get_smart_restart_manager().get_state()
    except Exception as e:
        smart_restart_state = {"error": str(e)}

    extra = {}
    if _extra_status_fn:
        try:
            extra = _extra_status_fn() or {}
        except Exception as e:
            extra = {"extra_status_error": str(e)}

    return jsonify({
        "success": True,
        "service": _service_role,
        "role": _service_role,
        "time": _now_iso(),
        "started_at": datetime.fromtimestamp(_started_at, timezone.utc).isoformat(),
        "uptime_seconds": round(time.time() - _started_at, 1),
        "memory_mb": memory_mb,
        "maintenance_mode": MAINTENANCE_FLAG_FILE.exists(),
        "active_jobs": get_active_jobs(),
        "realtime": realtime,
        "smart_restart": smart_restart_state,
        "extra": extra,
    }), 200


# Whitelist of job fields exposed to the dashboard (no payloads / prompts)
_JOB_FIELDS = (
    "job_id", "status", "job_type", "model", "user_id",
    "created_at", "queued_at", "started_at", "completed_at",
    "error", "priority",
)


def _slim_job(job: dict) -> dict:
    slim = {k: job.get(k) for k in _JOB_FIELDS if k in job}
    meta = job.get("metadata") or {}
    if isinstance(meta, dict):
        for key in ("workflow_id", "current_step", "workflow_step", "provider_key"):
            if meta.get(key) is not None:
                slim[key] = meta[key]
    err = slim.get("error")
    if isinstance(err, str) and len(err) > 300:
        slim["error"] = err[:300] + "…"
    return slim


@monitor_bp.route("/monitor/jobs", methods=["GET"])
@require_monitor_token
def monitor_jobs():
    """Job overview from Supabase: running, pending, completed, failed, today's totals."""
    try:
        from supabase_client import supabase

        running_resp = (
            supabase.table("jobs").select("*")
            .eq("status", "running")
            .order("created_at", desc=True).limit(50).execute()
        )
        pending_resp = (
            supabase.table("jobs").select("job_id", count="exact")
            .in_("status", ["pending", "pending_retry"])
            .limit(1).execute()
        )
        completed_resp = (
            supabase.table("jobs").select("*")
            .eq("status", "completed")
            .order("created_at", desc=True).limit(20).execute()
        )
        failed_resp = (
            supabase.table("jobs").select("*")
            .eq("status", "failed")
            .order("created_at", desc=True).limit(20).execute()
        )

        today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00+00:00")

        def _count_today(status):
            try:
                r = (
                    supabase.table("jobs").select("job_id", count="exact")
                    .eq("status", status).gte("created_at", today_iso)
                    .limit(1).execute()
                )
                return r.count or 0
            except Exception:
                return None

        return jsonify({
            "success": True,
            "service": _service_role,
            "time": _now_iso(),
            "running": [_slim_job(j) for j in (running_resp.data or [])],
            "running_count": len(running_resp.data or []),
            "pending_count": pending_resp.count or 0,
            "completed_recent": [_slim_job(j) for j in (completed_resp.data or [])],
            "failed_recent": [_slim_job(j) for j in (failed_resp.data or [])],
            "today": {
                "completed": _count_today("completed"),
                "failed": _count_today("failed"),
            },
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


def _ring_response(buffer, limit_default):
    try:
        limit = min(int(request.args.get("limit", limit_default)), LOG_RING_MAX)
    except (TypeError, ValueError):
        limit = limit_default
    with _buf_lock:
        items = list(buffer)[-limit:]
    return jsonify({
        "success": True,
        "service": _service_role,
        "time": _now_iso(),
        "count": len(items),
        "items": items,
    }), 200


@monitor_bp.route("/monitor/logs", methods=["GET"])
@require_monitor_token
def monitor_logs():
    """Recent log records (ring buffer, max 500)."""
    return _ring_response(LOG_BUFFER, 200)


@monitor_bp.route("/monitor/errors", methods=["GET"])
@require_monitor_token
def monitor_errors():
    """Recent ERROR/CRITICAL records incl. traceback head."""
    return _ring_response(ERROR_BUFFER, 100)


@monitor_bp.route("/monitor/requests", methods=["GET"])
@require_monitor_token
def monitor_requests():
    """Recent HTTP requests (method, path, status, duration ms, ip)."""
    return _ring_response(REQUEST_BUFFER, 100)


@monitor_bp.route("/monitor/maintenance", methods=["POST"])
@require_monitor_token
def monitor_maintenance():
    """Toggle the LOCAL .maintenance_mode flag on this service.

    Body: {"enable": true|false}
    Used by the dashboard AND by smart_restart.py on the backend to
    coordinate the worker remotely during a drain.
    """
    try:
        data = request.get_json(silent=True) or {}
        enable = bool(data.get("enable", True))
        if enable:
            MAINTENANCE_FLAG_FILE.touch()
            print(f"[MONITOR] Maintenance mode ENABLED on {_service_role}")
        else:
            if MAINTENANCE_FLAG_FILE.exists():
                MAINTENANCE_FLAG_FILE.unlink()
            print(f"[MONITOR] Maintenance mode DISABLED on {_service_role}")
        return jsonify({
            "success": True,
            "service": _service_role,
            "maintenance_mode": MAINTENANCE_FLAG_FILE.exists(),
        }), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@monitor_bp.route("/monitor/restart", methods=["POST"])
@require_monitor_token
def monitor_restart():
    """Manually trigger the smart drain-and-restart sequence.

    Body: {"scope": "all"|"backend"|"worker", "force": false}
    force=false (default) ALWAYS drains running jobs first and aborts on
    drain timeout. force=true restarts after the drain timeout even if jobs
    are still running (use with care).
    """
    try:
        data = request.get_json(silent=True) or {}
        scope = data.get("scope", "all")
        force = bool(data.get("force", False))
        if scope not in ("all", "backend", "worker"):
            return jsonify({"success": False, "error": "scope must be all|backend|worker"}), 400

        from smart_restart import get_smart_restart_manager
        mgr = get_smart_restart_manager()
        accepted, message = mgr.trigger_manual(scope=scope, force=force)
        status_code = 202 if accepted else 409
        return jsonify({
            "success": accepted,
            "message": message,
            "scope": scope,
            "force": force,
        }), status_code
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@monitor_bp.route("/monitor/restart-history", methods=["GET"])
@require_monitor_token
def monitor_restart_history():
    """Durable restart audit log from the system_restarts table."""
    try:
        from supabase_client import supabase
        resp = (
            supabase.table("system_restarts").select("*")
            .order("triggered_at", desc=True).limit(50).execute()
        )
        return jsonify({"success": True, "items": resp.data or []}), 200
    except Exception as e:
        # Table may not exist yet — degrade gracefully
        return jsonify({"success": True, "items": [], "warning": str(e)}), 200


# ─────────────────────────────────────────────────────────────────────────────
# Initialization (called once by each service at startup)
# ─────────────────────────────────────────────────────────────────────────────
def init_monitor(flask_app, service_role: str, extra_status_fn=None):
    """Install log capture, request recording hooks, and the /monitor blueprint.

    Args:
        flask_app:       the service's Flask app
        service_role:    "backend" or "worker"
        extra_status_fn: optional callable returning a dict merged into
                         /monitor/status (e.g. the worker's worker_status)
    """
    global _service_role, _extra_status_fn, _initialized
    _service_role = service_role
    _extra_status_fn = extra_status_fn

    if not _initialized:
        _initialized = True

        # Attach the capture handler to the ROOT logger so records from
        # httpx / werkzeug / realtime / workflows are all captured.
        handler = MonitorLogHandler()
        handler.setLevel(logging.INFO)
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        if root_logger.level > logging.INFO or root_logger.level == logging.NOTSET:
            root_logger.setLevel(logging.INFO)

    # ── Request recording hooks ──
    @flask_app.before_request
    def _monitor_before_request():
        g._monitor_t0 = time.time()

    @flask_app.after_request
    def _monitor_after_request(response):
        try:
            path = request.path or ""
            if path in _REQUEST_SKIP_EXACT or any(path.startswith(p) for p in _REQUEST_SKIP_PREFIXES):
                return response
            duration_ms = None
            t0 = getattr(g, "_monitor_t0", None)
            if t0 is not None:
                duration_ms = round((time.time() - t0) * 1000, 1)
            full_path = path
            if request.query_string:
                full_path = f"{path}?{request.query_string.decode('utf-8', 'ignore')[:200]}"
            with _buf_lock:
                REQUEST_BUFFER.append({
                    "ts": _now_iso(),
                    "method": request.method,
                    "path": _redact(full_path)[:300],
                    "status": response.status_code,
                    "duration_ms": duration_ms,
                    "ip": request.headers.get("X-Forwarded-For", request.remote_addr or "")[:64],
                })
        except Exception:
            pass
        return response

    flask_app.register_blueprint(monitor_bp)
    print(f"[MONITOR] Monitor API initialized (role={service_role}) — dashboard at /monitor")
