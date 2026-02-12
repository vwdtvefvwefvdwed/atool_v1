"""
Model Quota Manager
Tracks and enforces usage quotas for specific model/provider combinations.
Uses Supabase realtime to keep quota cache synchronized.
"""

import os
import asyncio
import threading
from typing import Dict, Optional, Any
from dotenv_vault import load_dotenv
from provider_api_keys import get_worker1_client

load_dotenv()


class ModelQuotaManager:
    """
    Manages model quotas with realtime synchronization.
    - Subscribes to model_quotas table changes via Supabase realtime
    - Maintains in-memory cache for fast quota checks
    - Atomically increments quotas in database
    """
    
    def __init__(self):
        self.quotas_cache: Dict[str, Dict[str, int]] = {}
        self.is_subscribed = False
        self.worker1_client = get_worker1_client()
        self._lock = threading.Lock()
        self._channel = None
        self._async_client = None
        self._thread = None
        self._loop = None
        self._stop_event = threading.Event()
        self._running = False
        
    def _load_quotas(self, clear_cache=False):
        """Load all quotas into cache on startup"""
        if not self.worker1_client:
            print("[QUOTA] Worker1 client not available - quota system disabled")
            return
            
        try:
            result = self.worker1_client.table('model_quotas')\
                .select('*')\
                .eq('enabled', True)\
                .execute()
                
            with self._lock:
                if clear_cache:
                    self.quotas_cache.clear()
                    print("[QUOTA] Cache cleared before reload")
                    
                for row in result.data:
                    key = f"{row['provider_name']}:{row['model_name']}"
                    self.quotas_cache[key] = {
                        'used': row['quota_used'],
                        'limit': row['quota_limit'],
                        'enabled': row['enabled']
                    }
                    print(f"[QUOTA] Loaded: {key} -> {row['quota_used']}/{row['quota_limit']}")
                    
            print(f"[QUOTA] Loaded {len(self.quotas_cache)} quota entries from Worker1")
        except Exception as e:
            print(f"[QUOTA] Error loading quotas: {e}")
            
    def start_realtime(self):
        """
        Subscribe to Supabase realtime for model_quotas table.
        Called once on application startup.
        """
        if self._running:
            print("[QUOTA] Already running")
            return
            
        if not self.worker1_client:
            print("[QUOTA] Worker1 client not available - realtime disabled")
            return
        
        try:
            self._load_quotas()
            
            self._running = True
            self._stop_event.clear()
            
            self._thread = threading.Thread(target=self._run_async_loop, daemon=True)
            self._thread.start()
            
            print("[QUOTA] Realtime listener started in background thread")
            self.is_subscribed = True
        except Exception as e:
            print(f"[QUOTA] ERROR starting realtime: {e}")
            self._running = False
            
    def _run_async_loop(self):
        """Run async event loop in background thread"""
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._realtime_listener())
        except Exception as e:
            print(f"[QUOTA] Async loop error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if self._loop:
                self._loop.close()
    
    async def _realtime_listener(self):
        """Async listener for model_quotas table changes"""
        from supabase import acreate_client
        
        try:
            worker1_url = os.getenv("WORKER_1_URL")
            worker1_key = os.getenv("WORKER_1_SERVICE_ROLE_KEY")
            
            if not worker1_url or not worker1_key:
                print("[QUOTA] Worker1 credentials not found")
                return
            
            self._async_client = await acreate_client(worker1_url, worker1_key)
            print("[QUOTA] Connecting to realtime...")
            
            def handle_quota_change(payload):
                """Callback for quota changes"""
                try:
                    # Supabase realtime structure: payload.data.type and payload.data.record
                    data_wrapper = payload.get('data', {})
                    event = data_wrapper.get('type', 'UPDATE')
                    record = data_wrapper.get('record', {})
                    old_record = data_wrapper.get('old_record', {})
                    
                    print(f"[QUOTA RT] Event: {event}, record: {record}, old_record: {old_record}")
                    
                    data = record or old_record
                    if not data:
                        print(f"[QUOTA RT] No data in payload, skipping")
                        return
                    
                    # For DELETE, old_data might only have 'id', need to find by id
                    if event == 'DELETE' and 'provider_name' not in data:
                        # DELETE only provides id, can't construct key
                        deleted_id = data.get('id')
                        print(f"[QUOTA RT] DELETE event with only id={deleted_id}, reloading cache from database")
                        # Reload all quotas from database (this will clear deleted items)
                        self._load_quotas(clear_cache=True)
                        return
                    
                    provider_name = data.get('provider_name')
                    model_name = data.get('model_name')
                    
                    if not provider_name or not model_name:
                        print(f"[QUOTA RT] Missing provider_name or model_name, skipping")
                        return
                    
                    key = f"{provider_name}:{model_name}"
                    
                    with self._lock:
                        if event == 'DELETE':
                            if key in self.quotas_cache:
                                del self.quotas_cache[key]
                                print(f"[QUOTA RT] Removed: {key}")
                            else:
                                print(f"[QUOTA RT] DELETE: {key} not found in cache")
                        else:
                            self.quotas_cache[key] = {
                                'used': data.get('quota_used', 0),
                                'limit': data.get('quota_limit', 0),
                                'enabled': data.get('enabled', True)
                            }
                            print(f"[QUOTA RT] Updated: {key} -> {data.get('quota_used')}/{data.get('quota_limit')}")
                except Exception as e:
                    print(f"[QUOTA RT] Error handling change: {e}")
                    import traceback
                    traceback.print_exc()
            
            self._channel = self._async_client.channel('model-quotas-realtime')
            await self._channel.on_postgres_changes(
                event='*',
                schema='public',
                table='model_quotas',
                callback=handle_quota_change
            ).subscribe()
            
            print("[QUOTA] Subscribed to model_quotas realtime updates")
            
            while not self._stop_event.is_set():
                await asyncio.sleep(1)
            
            await self._channel.unsubscribe()
            print("[QUOTA] Unsubscribed from realtime")
            
        except Exception as e:
            print(f"[QUOTA] Realtime listener error: {e}")
            import traceback
            traceback.print_exc()
            
    def get_quotas_for_frontend(self) -> Dict[str, Dict[str, int]]:
        """
        Return cache formatted for frontend.
        Returns dict with model keys and their quota status.
        """
        with self._lock:
            return {
                model_key: {
                    'used': quota['used'],
                    'limit': quota['limit'],
                    'available': quota['limit'] - quota['used'],
                    'enabled': quota.get('enabled', True)
                }
                for model_key, quota in self.quotas_cache.items()
            }
            
    def check_quota_available(self, provider: str, model: str) -> bool:
        """
        Check if quota is available for a model/provider combination.
        Returns True if:
        - No quota entry exists (no enforcement)
        - Quota exists and is not exceeded
        
        Returns False if quota is exceeded or disabled.
        """
        key = f"{provider}:{model}"
        
        with self._lock:
            if key not in self.quotas_cache:
                return True
                
            quota = self.quotas_cache[key]
            
            if not quota.get('enabled', True):
                print(f"[QUOTA] {key} is DISABLED")
                return False
                
            available = quota['used'] < quota['limit']
            
            if not available:
                print(f"[QUOTA] {key} EXCEEDED: {quota['used']}/{quota['limit']}")
            
            return available
            
    def increment_quota(self, provider: str, model: str) -> Dict[str, Any]:
        """
        Atomically increment quota in database.
        The realtime callback will update the cache automatically.
        
        Returns result dict with success status.
        """
        key = f"{provider}:{model}"
        
        print(f"[QUOTA] increment_quota called with provider='{provider}', model='{model}', key='{key}'")
        
        if key not in self.quotas_cache:
            print(f"[QUOTA] No quota tracking for {key} - allowing without increment")
            return {'success': True, 'reason': 'no_quota_tracking'}
            
        if not self.worker1_client:
            print(f"[QUOTA] Worker1 client unavailable - allowing {key}")
            return {'success': True, 'reason': 'no_client'}
            
        try:
            print(f"[QUOTA] Calling RPC increment_quota with p_provider='{provider}', p_model='{model}'")
            result = self.worker1_client.rpc('increment_quota', {
                'p_provider': provider,
                'p_model': model
            }).execute()
            
            if result.data:
                success = result.data.get('success', False)
                if success:
                    print(f"[QUOTA] Incremented {key}: {result.data.get('quota_used')}/{result.data.get('quota_limit')}")
                    with self._lock:
                        if key in self.quotas_cache:
                            self.quotas_cache[key]['used'] = result.data.get('quota_used')
                else:
                    reason = result.data.get('reason', 'unknown')
                    print(f"[QUOTA] Failed to increment {key}: {reason}")
                    
                return result.data
            else:
                return {'success': False, 'reason': 'no_response'}
                
        except Exception as e:
            print(f"[QUOTA] ERROR incrementing {key}: {e}")
            return {'success': False, 'reason': 'exception', 'error': str(e)}
            
    def check_and_increment(self, provider: str, model: str) -> bool:
        """
        Check quota and increment if available.
        This is the main method called before job creation.
        
        Returns:
        - True: Quota available and incremented (or no quota tracking)
        - False: Quota exceeded, job should not be created
        """
        if not self.check_quota_available(provider, model):
            return False
            
        result = self.increment_quota(provider, model)
        return result.get('success', False)
        
    def get_quota_status(self, provider: str, model: str) -> Optional[Dict[str, int]]:
        """Get current quota status for a specific model/provider"""
        key = f"{provider}:{model}"
        
        with self._lock:
            if key not in self.quotas_cache:
                return None
                
            quota = self.quotas_cache[key]
            return {
                'used': quota['used'],
                'limit': quota['limit'],
                'available': quota['limit'] - quota['used'],
                'enabled': quota.get('enabled', True)
            }


_quota_manager_instance = None
_quota_manager_lock = threading.Lock()


def get_quota_manager() -> ModelQuotaManager:
    """Get or create quota manager singleton"""
    global _quota_manager_instance
    
    if _quota_manager_instance is not None:
        return _quota_manager_instance
        
    with _quota_manager_lock:
        if _quota_manager_instance is None:
            _quota_manager_instance = ModelQuotaManager()
            print("[QUOTA] ModelQuotaManager singleton created")
            
        return _quota_manager_instance


def ensure_quota_manager_started():
    """
    Ensure quota manager is started and subscribed to realtime.
    Call this on application startup.
    """
    manager = get_quota_manager()
    if not manager.is_subscribed:
        manager.start_realtime()
        print("[QUOTA] OK Quota manager started")
    return manager


if __name__ == "__main__":
    print("Testing Model Quota Manager...")
    
    manager = get_quota_manager()
    manager.start_realtime()
    
    print("\nTesting quota check for cinematic-pro:kling-2.6")
    available = manager.check_quota_available('cinematic-pro', 'kling-2.6')
    print(f"Quota available: {available}")
    
    if available:
        print("\nTesting quota increment...")
        result = manager.increment_quota('cinematic-pro', 'kling-2.6')
        print(f"Increment result: {result}")
    
    print("\nCurrent quotas:")
    quotas = manager.get_quotas_for_frontend()
    for key, status in quotas.items():
        print(f"  {key}: {status}")
    
    print("\nKeeping connection alive for 30 seconds to test realtime...")
    import time
    time.sleep(30)
