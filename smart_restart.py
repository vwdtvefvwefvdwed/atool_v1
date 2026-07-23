"""
Smart Auto-Restart on persistent Supabase Realtime 1001 ("going away") failures.

Problem (see docs/SMART_RESTART_AND_MONITOR_PLAN.md):
  The shared Realtime WebSocket (realtime_manager.py) can enter a poisoned
  state where EVERY reconnect attempt fails with close code 1001. In-process
  reconnection cannot recover because the broken state lives inside the
  realtime-py/websockets layer. The only reliable cure is a full process
  restart — on Render, restarting the service deploy.

State machine:
  HEALTHY -> DETECTED -> DRAINING -> RESTART_TRIGGERED -> (process dies,
  Render restarts fresh) -> HEALTHY

Flow (backend is the SINGLE decision-maker; worker only records & reports):
  1. DETECTED:  >= ERROR_THRESHOLD 1001 errors within WINDOW_SECONDS while
     Realtime has no healthy event traffic (locally, OR reported by the
     worker via its /monitor/status).
  2. Enable maintenance mode on BOTH services (local flag file + HTTP to the
     worker's /monitor/maintenance) so NO new jobs start.
  3. DRAINING: poll Supabase `jobs` (status='running' must be 0) AND the
     worker's /monitor/status (active_jobs must be 0). NEVER kill running
     jobs. Abort + alert on drain timeout (unless force=True).
  4. RESTART_TRIGGERED: audit row to `system_restarts`, then restart the
     Render deploys via the Render API (deploy-hook fallback): worker FIRST,
     backend LAST (backend restarts itself last since it orchestrates).
  5. The .maintenance_mode flag lives on Render's ephemeral filesystem, so it
     auto-clears after restart — services come back accepting jobs with zero
     manual steps.

Safety rails:
  - Cooldown: max 1 auto-restart per RESTART_COOLDOWN seconds.
  - Daily cap: max DAILY_RESTART_CAP auto-restarts per UTC day (beyond that
    only alerts — prevents restart storms during a Supabase-side outage).
  - SMART_RESTART_ENABLED=false disables the automatic trigger entirely
    (manual /monitor/restart still works).
"""

import os
import time
import threading
from collections import deque
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

from envvault import load_env
load_env()

# ─────────────────────────────────────────────────────────────────────────────
# Tunables (env-overridable)
# ─────────────────────────────────────────────────────────────────────────────
WINDOW_SECONDS = int(os.getenv("SMART_RESTART_WINDOW_SECONDS", "300"))          # 1001 sliding window
ERROR_THRESHOLD = int(os.getenv("SMART_RESTART_ERROR_THRESHOLD", "6"))          # 1001s in window to trigger
MIN_EVENT_SILENCE = int(os.getenv("SMART_RESTART_MIN_EVENT_SILENCE", "120"))    # seconds without realtime events
EVAL_INTERVAL = int(os.getenv("SMART_RESTART_EVAL_INTERVAL", "15"))             # evaluator loop period
DRAIN_POLL_INTERVAL = int(os.getenv("SMART_RESTART_DRAIN_POLL", "10"))          # drain poll period
DRAIN_TIMEOUT = int(os.getenv("SMART_RESTART_DRAIN_TIMEOUT", "900"))            # 15 min max drain wait
RESTART_COOLDOWN = int(os.getenv("SMART_RESTART_COOLDOWN", "1800"))             # 30 min between restarts
DAILY_RESTART_CAP = int(os.getenv("SMART_RESTART_DAILY_CAP", "4"))              # max auto-restarts / UTC day
WORKER_UNREACHABLE_TOLERANCE = 3   # consecutive failed worker polls => treat worker as down (safe to restart)
# Running jobs older than this are treated as zombies and EXCLUDED from the
# drain count (a crashed job stuck at status='running' must not block every
# future restart forever). Real image/workflow jobs finish in minutes.
STALE_JOB_HOURS = float(os.getenv("SMART_RESTART_STALE_JOB_HOURS", "2"))

MAINTENANCE_FLAG_FILE = Path(__file__).parent / ".maintenance_mode"

RENDER_API_BASE = "https://api.render.com/v1"

# States
HEALTHY = "HEALTHY"
DETECTED = "DETECTED"
DRAINING = "DRAINING"
RESTART_TRIGGERED = "RESTART_TRIGGERED"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SmartRestartManager:
    """Singleton — 1001 detection, drain orchestration, Render restarts."""

    _instance = None
    _instance_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self.role = "unknown"
        self.enabled = os.getenv("SMART_RESTART_ENABLED", "true").lower() == "true"

        # 1001 sliding window
        self._error_times = deque(maxlen=500)
        self._error_lock = threading.Lock()
        self._total_1001 = 0

        # State machine
        self.state = HEALTHY
        self.state_since = _now_iso()
        self._state_lock = threading.Lock()

        # Restart bookkeeping
        self.last_restart_at = 0.0        # epoch of last restart trigger (this process)
        self.last_abort_at = 0.0          # epoch of last aborted drain
        self.last_restart_reason = None
        self._sequence_lock = threading.Lock()   # only one drain/restart sequence at a time

        # Evaluator thread
        self._evaluator_thread = None
        self._stop_event = threading.Event()

    # ── 1001 recording (called by monitor_api.MonitorLogHandler) ──────────
    def record_1001(self):
        now = time.time()
        with self._error_lock:
            self._error_times.append(now)
            self._total_1001 += 1

    def count_1001(self, window_seconds: int = WINDOW_SECONDS) -> int:
        cutoff = time.time() - window_seconds
        with self._error_lock:
            return sum(1 for t in self._error_times if t >= cutoff)

    # ── Status for /monitor/status ─────────────────────────────────────────
    def get_state(self) -> dict:
        cooldown_remaining = max(0, int(RESTART_COOLDOWN - (time.time() - self.last_restart_at))) \
            if self.last_restart_at else 0
        return {
            "enabled": self.enabled,
            "role": self.role,
            "state": self.state,
            "state_since": self.state_since,
            "errors_1001_window": self.count_1001(),
            "errors_1001_total": self._total_1001,
            "window_seconds": WINDOW_SECONDS,
            "threshold": ERROR_THRESHOLD,
            "detected": self.count_1001() >= ERROR_THRESHOLD,
            "cooldown_remaining_seconds": cooldown_remaining,
            "daily_cap": DAILY_RESTART_CAP,
            "restarts_today": self._restarts_today(),
            "last_restart_reason": self.last_restart_reason,
        }

    def _set_state(self, new_state: str):
        with self._state_lock:
            self.state = new_state
            self.state_since = _now_iso()
        print(f"[SMART-RESTART] State -> {new_state}")

    # ── Startup ────────────────────────────────────────────────────────────
    def start(self, role: str):
        """Start the manager. Evaluator thread runs on the BACKEND only."""
        self.role = role
        if role != "backend":
            print(f"[SMART-RESTART] Initialized in report-only mode (role={role})")
            return
        if self._evaluator_thread and self._evaluator_thread.is_alive():
            return
        self._evaluator_thread = threading.Thread(
            target=self._evaluator_loop, daemon=True, name="SmartRestartEvaluator"
        )
        self._evaluator_thread.start()
        print(f"[SMART-RESTART] Evaluator started (enabled={self.enabled}, "
              f"threshold={ERROR_THRESHOLD}/{WINDOW_SECONDS}s, cooldown={RESTART_COOLDOWN}s)")

    # ── Automatic evaluation (backend only) ────────────────────────────────
    def _evaluator_loop(self):
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=EVAL_INTERVAL)
            if self._stop_event.is_set():
                break
            try:
                # Watchdog: if a restart was triggered but this process is
                # STILL alive 15 min later, the platform restart failed —
                # reset to HEALTHY so auto-recovery is not disabled forever.
                if (self.state == RESTART_TRIGGERED and self.last_restart_at
                        and time.time() - self.last_restart_at > 900):
                    print("[SMART-RESTART] Restart triggered 15+ min ago but process "
                          "still alive — resetting state to HEALTHY")
                    self._set_maintenance_local(False)
                    self._set_state(HEALTHY)

                if not self.enabled or self.state != HEALTHY:
                    continue
                reason, scope = self._check_triggers()
                if reason:
                    if not self._cooldown_ok():
                        print("[SMART-RESTART] Trigger detected but in cooldown — skipping")
                        continue
                    if not self._daily_cap_ok():
                        self._notify(
                            f"1001 loop detected ({reason}) but DAILY restart cap "
                            f"({DAILY_RESTART_CAP}) reached — NOT restarting. Manual action needed."
                        )
                        # Back off for a while so we don't spam alerts
                        self._stop_event.wait(timeout=RESTART_COOLDOWN)
                        continue
                    # Scope-aware: restart ONLY the service where the 1001 loop
                    # was detected (backend loop => backend restart only).
                    self._run_restart_sequence(reason=reason, scope=scope,
                                               force=False, triggered_by="auto")
            except Exception as e:
                print(f"[SMART-RESTART] Evaluator error: {e}")

    def _check_triggers(self):
        """Return (reason, scope) if a restart should be triggered, else (None, None).

        Scope is the service where the 1001 loop lives — the fix is a process
        restart of THAT service only. The observed failure mode is the shared
        Realtime connection in app.py (backend); the worker's own realtime
        listener can independently hit the same loop, in which case only the
        worker is restarted.
        """
        # Local (backend) detection: 1001 storm + no healthy realtime traffic
        local_count = self.count_1001()
        if local_count >= ERROR_THRESHOLD and self._realtime_is_silent():
            return (f"backend realtime 1001 loop ({local_count} errors in {WINDOW_SECONDS}s)",
                    "backend")

        # Worker-reported detection (worker records 1001s, backend decides)
        worker_state = self._fetch_worker_status()
        if worker_state:
            sr = worker_state.get("smart_restart") or {}
            if sr.get("detected"):
                return (f"worker realtime 1001 loop "
                        f"({sr.get('errors_1001_window')} errors in {sr.get('window_seconds')}s)",
                        "worker")
        return None, None

    def _realtime_is_silent(self) -> bool:
        """True when Realtime has NO healthy event traffic.

        last_event_age == 0.0 means no events since (re)connect — combined
        with a 1001 storm that means the connection is dead. Any age above
        MIN_EVENT_SILENCE also counts as silent.
        """
        try:
            from realtime_manager import get_realtime_manager
            age = get_realtime_manager()._get_last_event_age()
            return age == 0.0 or age >= MIN_EVENT_SILENCE
        except Exception:
            # Cannot read realtime state — rely on the error storm alone
            return True

    # ── Safety rails ───────────────────────────────────────────────────────
    def _cooldown_ok(self) -> bool:
        now = time.time()
        if self.last_restart_at and now - self.last_restart_at < RESTART_COOLDOWN:
            return False
        if self.last_abort_at and now - self.last_abort_at < RESTART_COOLDOWN / 2:
            return False
        # Cross-restart cooldown: check the durable audit table
        last_row_ts = self._last_restart_from_db()
        if last_row_ts and now - last_row_ts < RESTART_COOLDOWN:
            return False
        return True

    def _restarts_today(self) -> int:
        # Cached for 60s — /monitor/status polls every 5s on both services and
        # must not hammer Supabase with count queries.
        now = time.time()
        cached = getattr(self, "_restarts_today_cache", None)
        if cached is not None and now - cached[0] < 60:
            return cached[1]
        try:
            from supabase_client import supabase
            today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00+00:00")
            resp = (
                supabase.table("system_restarts").select("id", count="exact")
                .eq("triggered_by", "auto").gte("triggered_at", today_iso)
                .limit(1).execute()
            )
            count = resp.count or 0
        except Exception:
            count = cached[1] if cached else 0
        self._restarts_today_cache = (now, count)
        return count

    def _daily_cap_ok(self) -> bool:
        return self._restarts_today() < DAILY_RESTART_CAP

    def _last_restart_from_db(self):
        try:
            from supabase_client import supabase
            resp = (
                supabase.table("system_restarts").select("triggered_at")
                .order("triggered_at", desc=True).limit(1).execute()
            )
            if resp.data:
                ts = resp.data[0]["triggered_at"]
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                return dt.timestamp()
        except Exception:
            pass
        return None

    # ── Manual trigger (from /monitor/restart) ─────────────────────────────
    def trigger_manual(self, scope: str = "all", force: bool = False):
        """Trigger the drain-and-restart sequence manually (dashboard button).

        Returns (accepted: bool, message: str). Runs in a background thread.
        """
        if self.role != "backend":
            # Worker received a manual restart request for itself only
            if scope in ("worker", "all"):
                threading.Thread(
                    target=self._run_restart_sequence,
                    kwargs={"reason": "manual (worker-local)", "scope": "worker",
                            "force": force, "triggered_by": "manual"},
                    daemon=True, name="SmartRestartManual",
                ).start()
                return True, "Worker restart sequence started"
            return False, "This service is the worker; use scope=worker or call the backend"

        if self.state != HEALTHY:
            return False, f"Restart sequence already in progress (state={self.state})"

        threading.Thread(
            target=self._run_restart_sequence,
            kwargs={"reason": "manual (dashboard)", "scope": scope,
                    "force": force, "triggered_by": "manual"},
            daemon=True, name="SmartRestartManual",
        ).start()
        return True, f"Drain-and-restart sequence started (scope={scope}, force={force})"

    # ── The sequence: DETECTED -> DRAINING -> RESTART_TRIGGERED ───────────
    def _run_restart_sequence(self, reason: str, scope: str = "all",
                              force: bool = False, triggered_by: str = "auto"):
        if not self._sequence_lock.acquire(blocking=False):
            print("[SMART-RESTART] Sequence already running — skipping duplicate trigger")
            return False
        try:
            errors_in_window = self.count_1001()
            self._set_state(DETECTED)
            self._notify(
                f"DETECTED: {reason}. Enabling maintenance mode and draining jobs "
                f"(scope={scope}, force={force}, by={triggered_by})."
            )

            # Step 1 — maintenance mode on BOTH services (block new jobs)
            self._set_maintenance_local(True)
            self._set_maintenance_worker(True)

            # Step 2 — drain: wait until NO jobs are running anywhere
            self._set_state(DRAINING)
            drained = self._drain(force=force)
            if not drained and not force:
                # Abort: never kill running jobs
                self._set_maintenance_local(False)
                self._set_maintenance_worker(False)
                self.last_abort_at = time.time()
                self._set_state(HEALTHY)
                self._notify(
                    f"Drain TIMEOUT after {DRAIN_TIMEOUT}s — jobs still running. "
                    f"Restart ABORTED, maintenance cleared. Will re-evaluate later."
                )
                return False

            # Step 3 — restart
            self._set_state(RESTART_TRIGGERED)
            self.last_restart_at = time.time()
            self.last_restart_reason = reason
            self._log_restart(reason, errors_in_window, triggered_by, scope)
            self._notify(f"RESTARTING Render deploy(s) now (scope={scope}). Reason: {reason}")

            ok_worker = True
            ok_backend = True
            # Worker FIRST, backend LAST (backend orchestrates / restarts itself last)
            if scope in ("all", "worker"):
                ok_worker = self._restart_render_service(
                    os.getenv("RENDER_WORKER_SERVICE_ID"),
                    os.getenv("RENDER_WORKER_DEPLOY_HOOK"),
                    label="worker",
                )
            else:
                # Worker is NOT being restarted, so its ephemeral filesystem is
                # NOT wiped — its .maintenance_mode flag must be cleared
                # explicitly or it would skip jobs forever.
                self._set_maintenance_worker(False)
            if scope in ("all", "backend"):
                ok_backend = self._restart_render_service(
                    os.getenv("RENDER_BACKEND_SERVICE_ID"),
                    os.getenv("RENDER_BACKEND_DEPLOY_HOOK"),
                    label="backend",
                )

            if not (ok_worker and ok_backend):
                # Restart call(s) failed — recover to a working state
                self._set_maintenance_local(False)
                self._set_maintenance_worker(False)
                self._set_state(HEALTHY)
                self._notify(
                    "Render restart call FAILED (check RENDER_API_KEY / service IDs / "
                    "deploy hooks). Maintenance cleared, system left running."
                )
                return False

            # Best-effort local cleanup. On Render the ephemeral filesystem is
            # wiped by the restart anyway; this matters for local dev runs.
            self._set_maintenance_local(False)

            # If THIS process's own service was NOT part of the restart scope,
            # it keeps running — resume monitoring immediately instead of
            # waiting for the 15-min watchdog (cooldown prevents re-triggers).
            own_service_restarted = (
                (self.role == "backend" and scope in ("all", "backend")) or
                (self.role == "worker" and scope in ("all", "worker"))
            )
            if not own_service_restarted:
                self._set_state(HEALTHY)
                print("[SMART-RESTART] Own service not in restart scope — resuming monitoring")
            else:
                print("[SMART-RESTART] Restart triggered successfully — waiting for platform restart")
            return True
        except Exception as e:
            print(f"[SMART-RESTART] Sequence error: {e}")
            import traceback
            traceback.print_exc()
            self._set_maintenance_local(False)
            self._set_maintenance_worker(False)
            self._set_state(HEALTHY)
            self._notify(f"Restart sequence ERROR: {e}. Maintenance cleared.")
            return False
        finally:
            self._sequence_lock.release()

    # ── Drain helpers ──────────────────────────────────────────────────────
    def _drain(self, force: bool = False) -> bool:
        """Wait until zero running jobs in Supabase AND zero worker in-flight jobs.

        Returns True when fully drained, False on timeout.
        Non-interactive port of graceful_shutdown.py's wait loop.
        """
        deadline = time.time() + DRAIN_TIMEOUT
        worker_unreachable_streak = 0

        while time.time() < deadline:
            db_running = self._count_running_jobs_db()
            worker_active = self._fetch_worker_active_jobs()

            if worker_active is None:
                worker_unreachable_streak += 1
            else:
                worker_unreachable_streak = 0

            worker_clear = (
                worker_active == 0
                or worker_unreachable_streak >= WORKER_UNREACHABLE_TOLERANCE  # worker down => nothing running there
            )

            if db_running == 0 and worker_clear:
                print("[SMART-RESTART] Drain complete — no running jobs anywhere")
                return True

            print(f"[SMART-RESTART] Draining… db_running={db_running}, "
                  f"worker_active={worker_active}, "
                  f"deadline_in={int(deadline - time.time())}s")
            time.sleep(DRAIN_POLL_INTERVAL)

        if force:
            print("[SMART-RESTART] Drain timeout but force=True — proceeding")
            return True
        return False

    def _count_running_jobs_db(self) -> int:
        """Count genuinely-running jobs, excluding zombies stuck at 'running'.

        A crashed job left at status='running' would otherwise block EVERY
        drain forever (each attempt aborting at the 15-min timeout).
        """
        try:
            from supabase_client import supabase
            resp = (
                supabase.table("jobs").select("job_id, created_at")
                .eq("status", "running").limit(100).execute()
            )
            rows = resp.data or []
            cutoff = datetime.now(timezone.utc) - timedelta(hours=STALE_JOB_HOURS)
            active = 0
            stale_ids = []
            for row in rows:
                try:
                    created = datetime.fromisoformat(
                        str(row.get("created_at", "")).replace("Z", "+00:00")
                    )
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    if created >= cutoff:
                        active += 1
                    else:
                        stale_ids.append(row.get("job_id"))
                except Exception:
                    # Unparseable timestamp -> count as active (safe side)
                    active += 1
            if stale_ids:
                print(f"[SMART-RESTART] Ignoring {len(stale_ids)} stale 'running' job(s) "
                      f"older than {STALE_JOB_HOURS}h in drain count: {stale_ids[:5]}")
            return active
        except Exception as e:
            print(f"[SMART-RESTART] Could not count running jobs: {e}")
            return -1  # unknown => never treated as drained (drain waits/times out)

    # ── Cross-service HTTP (backend -> worker) ─────────────────────────────
    def _worker_url(self):
        return (os.getenv("WORKER_URL") or "").rstrip("/") or None

    def _monitor_headers(self):
        token = os.getenv("MONITOR_TOKEN") or os.getenv("ADMIN_SECRET") or ""
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _fetch_worker_status(self):
        url = self._worker_url()
        if not url:
            return None
        try:
            resp = requests.get(f"{url}/monitor/status", headers=self._monitor_headers(), timeout=10)
            if resp.status_code == 200:
                return resp.json()
        except requests.exceptions.RequestException:
            pass
        return None

    def _fetch_worker_active_jobs(self):
        status = self._fetch_worker_status()
        if status is None:
            return None
        return status.get("active_jobs", 0)

    def _set_maintenance_local(self, enable: bool):
        try:
            if enable:
                MAINTENANCE_FLAG_FILE.touch()
            elif MAINTENANCE_FLAG_FILE.exists():
                MAINTENANCE_FLAG_FILE.unlink()
            print(f"[SMART-RESTART] Local maintenance {'ENABLED' if enable else 'DISABLED'}")
        except Exception as e:
            print(f"[SMART-RESTART] Could not toggle local maintenance flag: {e}")

    def _set_maintenance_worker(self, enable: bool):
        url = self._worker_url()
        if not url:
            print("[SMART-RESTART] WORKER_URL not set — cannot toggle worker maintenance")
            return
        try:
            resp = requests.post(
                f"{url}/monitor/maintenance",
                headers=self._monitor_headers(),
                json={"enable": enable},
                timeout=10,
            )
            print(f"[SMART-RESTART] Worker maintenance {'ENABLE' if enable else 'DISABLE'} "
                  f"-> {resp.status_code}")
        except requests.exceptions.RequestException as e:
            print(f"[SMART-RESTART] Could not toggle worker maintenance: {e}")

    # ── Render restart ─────────────────────────────────────────────────────
    def _restart_render_service(self, service_id, deploy_hook, label="service") -> bool:
        """Restart a Render service: API restart preferred, deploy hook fallback."""
        api_key = os.getenv("RENDER_API_KEY")

        # Option 1: Render API restart (true restart, no rebuild)
        if api_key and service_id:
            try:
                resp = requests.post(
                    f"{RENDER_API_BASE}/services/{service_id}/restart",
                    headers={"Authorization": f"Bearer {api_key}",
                             "Accept": "application/json"},
                    timeout=30,
                )
                if 200 <= resp.status_code < 300:
                    print(f"[SMART-RESTART] Render API restart OK for {label} ({service_id})")
                    return True
                print(f"[SMART-RESTART] Render API restart FAILED for {label}: "
                      f"{resp.status_code} {resp.text[:200]}")
            except requests.exceptions.RequestException as e:
                print(f"[SMART-RESTART] Render API restart error for {label}: {e}")

        # Option 2: Deploy hook fallback (triggers a fresh deploy)
        if deploy_hook:
            try:
                resp = requests.post(deploy_hook, timeout=30)
                if 200 <= resp.status_code < 300:
                    print(f"[SMART-RESTART] Deploy hook triggered for {label}")
                    return True
                print(f"[SMART-RESTART] Deploy hook FAILED for {label}: {resp.status_code}")
            except requests.exceptions.RequestException as e:
                print(f"[SMART-RESTART] Deploy hook error for {label}: {e}")

        print(f"[SMART-RESTART] No working restart method for {label} "
              f"(need RENDER_API_KEY + service ID, or a deploy hook)")
        return False

    # ── Audit + alerting ───────────────────────────────────────────────────
    def _log_restart(self, reason, errors_in_window, triggered_by, scope):
        try:
            from supabase_client import supabase
            supabase.table("system_restarts").insert({
                "service": scope,
                "reason": reason[:500],
                "errors_in_window": errors_in_window,
                "triggered_by": triggered_by,
                "triggered_at": _now_iso(),
            }).execute()
        except Exception as e:
            print(f"[SMART-RESTART] Could not log restart to system_restarts: {e}")

    def _notify(self, message: str):
        print(f"[SMART-RESTART] {message}")
        try:
            from error_notifier import notify_error, ErrorType
            notify_error(ErrorType.SMART_RESTART, message, context={
                "role": self.role,
                "state": self.state,
                "errors_1001_window": self.count_1001(),
            })
        except Exception as e:
            print(f"[SMART-RESTART] Alert notification failed: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Module-level accessors
# ─────────────────────────────────────────────────────────────────────────────
_manager = SmartRestartManager()


def get_smart_restart_manager() -> SmartRestartManager:
    return _manager


def init_smart_restart(role: str):
    """Start the smart-restart manager for this service ('backend' | 'worker')."""
    _manager.start(role)
