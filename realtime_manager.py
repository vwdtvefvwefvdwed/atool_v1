"""
Shared Supabase Realtime Connection Manager

Provides a SINGLE global WebSocket connection to Supabase Realtime
that all SSE clients share, eliminating the need for per-user connections.

Architecture:
- ONE background thread with async event loop
- ONE Supabase Realtime WebSocket connection
- Event routing to multiple SSE clients via in-memory queues
- Automatic cleanup when clients disconnect
- Automatic reconnection on disconnect with exponential backoff

Performance:
- 100 users = 1 connection (instead of 100)
- 1 thread (instead of 100)
- Minimal memory overhead
"""

import os
import asyncio
import threading
import queue
import time
from typing import Dict, Set
from dotenv_vault import load_dotenv

load_dotenv()

# Constants
MAX_RECONNECT_DELAY = 30      # Max seconds between reconnect attempts
INITIAL_RECONNECT_DELAY = 1   # Initial reconnect delay in seconds
HEARTBEAT_INTERVAL = 30       # Check connection health every 30s
HEARTBEAT_TIMEOUT = 90        # Consider connection dead if no events for 90s
QUEUE_PUT_TIMEOUT = 5         # Seconds to wait when putting item in queue


class RealtimeConnectionManager:
    """
    Singleton manager for shared Supabase Realtime connection
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True

        # Job ID to queues mapping: {job_id: set(queue1, queue2, ...)}
        self.subscriptions: Dict[str, Set[queue.Queue]] = {}
        self.subscriptions_lock = threading.Lock()

        # Async client and channel
        self.async_client = None
        self.channel = None

        # Background thread control
        self.thread = None
        self.loop = None
        self.stop_event = threading.Event()
        self.running = False

        # Health monitoring
        self.last_event_time = 0.0
        self.last_event_time_lock = threading.Lock()
        self._heartbeat_task = None

        print("🔌 Realtime Connection Manager initialized")

    def start(self):
        """Start the background Realtime connection thread"""
        if self.running:
            print("⚠️ Realtime manager already running")
            return

        self.running = True
        self.stop_event.clear()

        # Start background thread
        self.thread = threading.Thread(
            target=self._run_async_loop, daemon=True, name="RealtimeManager"
        )
        self.thread.start()

        print("✅ Realtime Connection Manager started")

    def stop(self):
        """Stop the background thread and cleanup"""
        if not self.running:
            return

        print("🛑 Stopping Realtime Connection Manager...")
        self.stop_event.set()
        self.running = False

        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)

        print("✅ Realtime Connection Manager stopped")

    def subscribe_to_job(self, job_id: str, client_queue: queue.Queue):
        """
        Subscribe a client queue to job updates

        Args:
            job_id: Job UUID to watch
            client_queue: Queue to receive updates
        """
        with self.subscriptions_lock:
            if job_id not in self.subscriptions:
                self.subscriptions[job_id] = set()

            self.subscriptions[job_id].add(client_queue)
            count = len(self.subscriptions[job_id])
            is_running = self.running

            print(f"📥 Client subscribed to job {job_id} ({count} total subscribers)")
            print(f"   Realtime manager running: {is_running}")
            print(f"   Current subscriptions: {list(self.subscriptions.keys())}")

    def unsubscribe_from_job(self, job_id: str, client_queue: queue.Queue):
        """
        Unsubscribe a client queue from job updates

        Args:
            job_id: Job UUID
            client_queue: Queue to remove
        """
        with self.subscriptions_lock:
            if job_id in self.subscriptions:
                self.subscriptions[job_id].discard(client_queue)

                # Clean up empty subscription sets
                if not self.subscriptions[job_id]:
                    del self.subscriptions[job_id]
                    print(f"🗑️ No more subscribers for job {job_id}, cleaned up")
                else:
                    count = len(self.subscriptions[job_id])
                    print(f"📤 Client unsubscribed from job {job_id} ({count} remaining)")

    def _record_event(self):
        """Record that an event was received (for health monitoring)"""
        with self.last_event_time_lock:
            self.last_event_time = time.time()

    def _get_last_event_age(self) -> float:
        """Get seconds since last event was received"""
        with self.last_event_time_lock:
            if self.last_event_time == 0.0:
                return 0.0
            return time.time() - self.last_event_time

    def _dispatch_event(self, job_id: str, payload: dict):
        """
        Dispatch event to all subscribers of a job

        Args:
            job_id: Job UUID
            payload: Event data from Realtime
        """
        self._record_event()

        with self.subscriptions_lock:
            if job_id not in self.subscriptions:
                # Log when no subscribers (helps debug race conditions)
                print(f"⚠️ No subscribers for job {job_id}, event not dispatched. Current subscriptions: {list(self.subscriptions.keys())}")
                return

            subscribers = list(self.subscriptions[job_id])
            subscriber_count = len(subscribers)

        # Send to all subscriber queues (outside lock to prevent blocking)
        print(f"📢 Dispatching event to {subscriber_count} subscriber(s) for job {job_id}")
        failed_queues = 0
        for client_queue in subscribers:
            try:
                # Use timeout instead of put_nowait to prevent silent drops
                client_queue.put(payload, timeout=QUEUE_PUT_TIMEOUT)
                print(f"✅ Event queued for job {job_id}")
            except queue.Full:
                failed_queues += 1
                print(f"⚠️ Queue full for job {job_id} after {QUEUE_PUT_TIMEOUT}s timeout")
            except Exception as e:
                failed_queues += 1
                print(f"❌ Error dispatching to queue: {e}")

        if failed_queues > 0:
            print(f"⚠️ {failed_queues}/{subscriber_count} queue(s) failed for job {job_id}")

    def _run_async_loop(self):
        """Run async event loop in background thread with automatic reconnection"""
        reconnect_delay = INITIAL_RECONNECT_DELAY
        attempt = 0

        while not self.stop_event.is_set():
            try:
                # Create new event loop for each connection attempt
                self.loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self.loop)

                attempt += 1
                if attempt > 1:
                    print(f"🔄 Realtime reconnect attempt #{attempt} (delay: {reconnect_delay}s)")
                    # Sleep with interrupt check
                    self.stop_event.wait(timeout=reconnect_delay)
                    if self.stop_event.is_set():
                        break

                # Reset event timestamp for fresh connection
                self.last_event_time = 0.0

                # Run the realtime listener — blocks until disconnect or error
                self.loop.run_until_complete(self._realtime_listener())

            except Exception as e:
                print(f"❌ Async loop error: {e}")
                import traceback
                traceback.print_exc()
            finally:
                if self.loop:
                    self.loop.close()
                    self.loop = None

            # Calculate exponential backoff for reconnect
            if not self.stop_event.is_set():
                print(f"⚠️ Realtime connection lost, reconnecting in {reconnect_delay}s...")
                # Exponential backoff: 1s -> 2s -> 4s -> 8s -> ... -> max 30s
                reconnect_delay = min(reconnect_delay * 2, MAX_RECONNECT_DELAY)

        # Final cleanup
        print("🔌 Realtime manager thread exited")
        self.running = False

    async def _heartbeat_checker(self):
        """Periodic task to detect dead connections"""
        while not self.stop_event.is_set():
            await asyncio.sleep(HEARTBEAT_INTERVAL)

            # Check if we've received events recently
            last_event_age = self._get_last_event_age()

            # Only check if we've had at least one event (avoid false positive on fresh connect)
            if last_event_age > 0 and last_event_age > HEARTBEAT_TIMEOUT:
                print(
                    f"❌ Realtime connection appears dead "
                    f"(no events for {last_event_age:.0f}s, threshold: {HEARTBEAT_TIMEOUT}s). "
                    f"Forcing reconnect."
                )
                # Force the listener to exit by signaling stop
                # The reconnect loop in _run_async_loop will handle reconnection
                raise ConnectionError("Realtime heartbeat timeout")

    async def _realtime_listener(self):
        """
        Async listener for ALL job updates

        Subscribes to public:jobs:* (all changes to jobs table)
        and routes events to the appropriate client queues
        """
        from supabase import acreate_client

        try:
            # Create async Supabase client
            supabase_url = os.getenv("SUPABASE_URL")
            supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
            self.async_client = await acreate_client(supabase_url, supabase_key)

            print("🔌 Connecting to Supabase Realtime (shared connection)...")

            def handle_job_change(payload):
                """Callback for ANY job change (supports multiple payload shapes)"""
                try:
                    # Support both legacy and new realtime payloads
                    data = payload.get("data") if isinstance(payload, dict) else None

                    # Determine event type
                    event_type = (
                        (payload.get("eventType") if isinstance(payload, dict) else None) or
                        (data.get("type") if isinstance(data, dict) else None) or
                        "UPDATE"
                    )

                    # Extract records from multiple possible keys
                    if isinstance(data, dict):
                        new_record = data.get("record") or data.get("new") or {}
                        old_record = data.get("old_record") or data.get("old") or {}
                    else:
                        new_record = payload.get("new", payload.get("record", {})) if isinstance(payload, dict) else {}
                        old_record = payload.get("old", {}) if isinstance(payload, dict) else {}

                    # Try multiple extraction paths - completion updates often have job_id in old
                    job_id = (
                        (new_record.get("job_id") if isinstance(new_record, dict) else None) or
                        (old_record.get("job_id") if isinstance(old_record, dict) else None) or
                        (payload.get("job_id") if isinstance(payload, dict) else None)
                    )

                    if not job_id:
                        # Silently skip - these are usually metadata-only updates
                        return

                    print(f"🔔 Job {job_id} updated: {event_type}")
                    print(f"   new_record status: {new_record.get('status') if isinstance(new_record, dict) else 'N/A'}")

                    # Build normalized payload ensuring 'new' key always exists
                    if isinstance(payload, dict) and 'new' in payload:
                        # Already has 'new' key, use as-is
                        normalized_payload = payload
                    else:
                        # Build a normalized copy with 'new' key for SSE
                        normalized_payload = dict(payload) if isinstance(payload, dict) else {}
                        if isinstance(new_record, dict) and new_record:
                            normalized_payload['new'] = new_record
                        if isinstance(old_record, dict) and old_record:
                            normalized_payload['old'] = old_record
                        if 'eventType' not in normalized_payload:
                            normalized_payload['eventType'] = event_type

                    print(f"   normalized payload keys: {list(normalized_payload.keys()) if isinstance(normalized_payload, dict) else 'N/A'}")

                    # Dispatch to all clients watching this job
                    self._dispatch_event(job_id, normalized_payload)

                except Exception as e:
                    print(f"❌ Error in realtime callback: {e}")
                    import traceback
                    traceback.print_exc()

            # Ensure old channel is fully cleaned up before creating new one
            if self.channel:
                try:
                    await self.channel.unsubscribe()
                except Exception:
                    pass
                self.channel = None

            # Subscribe to ALL job changes (wildcard filter)
            self.channel = self.async_client.channel("jobs-all")
            await self.channel.on_postgres_changes(
                event="*",  # All events (INSERT, UPDATE, DELETE)
                schema="public",
                table="jobs",
                callback=handle_job_change
            ).subscribe()

            print("✅ Subscribed to ALL job updates (shared connection active)")

            # Start heartbeat checker in background
            self._heartbeat_task = asyncio.create_task(self._heartbeat_checker())

            try:
                # Keep connection alive until stop signal or heartbeat timeout
                while not self.stop_event.is_set():
                    await asyncio.sleep(1)
            finally:
                # Cancel heartbeat task
                if self._heartbeat_task:
                    self._heartbeat_task.cancel()
                    try:
                        await self._heartbeat_task
                    except asyncio.CancelledError:
                        pass
                    self._heartbeat_task = None

            # Cleanup
            try:
                await self.channel.unsubscribe()
            except Exception:
                pass
            print("🔌 Unsubscribed from Supabase Realtime")

        except ConnectionError as e:
            # Heartbeat timeout — this is expected, reconnect will happen
            print(f"⚠️ Realtime connection lost: {e}")
            raise
        except Exception as e:
            print(f"❌ Realtime listener error: {e}")
            import traceback
            traceback.print_exc()
            raise  # Re-raise to trigger reconnect loop


# Global singleton instance
_realtime_manager = RealtimeConnectionManager()


def get_realtime_manager() -> RealtimeConnectionManager:
    """Get the global Realtime manager instance"""
    return _realtime_manager


def ensure_realtime_started():
    """Ensure the Realtime manager is running (call this on app startup)"""
    manager = get_realtime_manager()
    if not manager.running:
        manager.start()
