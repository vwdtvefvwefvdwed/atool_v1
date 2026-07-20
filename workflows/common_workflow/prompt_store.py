"""
PromptStore — LIVE per-image prompt resolution for the common workflow
(Gallery Remix), fetched directly from the SHARED gallery Supabase project.

Env (backend/.env):
  GALLERY_SUPABASE_URL       the shared gallery project URL (same project the
                             frontend reaches via VITE_GALLERY_SUPABASE_URL)
  GALLERY_SUPABASE_ANON_KEY  that project's ANON key (RLS read-only feed views;
                             NEVER the service_role key)

Design:
  * The MAIN (`SUPABASE_URL`) and NEW (`NEW_SUPABASE_URL`) backend accounts are
    intentionally NOT involved — no failover proxy, no `workflow_gallery_prompts`
    table, and no local gallery_prompts.json snapshot.
  * Every generation resolves the prompt by pool image id via one live query,
    softened by a small in-memory TTL cache so popular remixes don't hammer
    the gallery project.
  * Pool ids >= GEN_ID_OFFSET belong to `generated_feed` (query id - offset);
    all other ids belong to `gallery_feed`. MUST match GEN_ID_OFFSET in
    scripts/generate-gallery-json.js and src/hooks/useLikes.js.
  * A DB/config failure NEVER raises into the request path — resolve() returns
    the PROMPT_LOOKUP_UNAVAILABLE sentinel so the caller can degrade to the
    workflow's default prompts. A confirmed missing id returns None (=> 410).
"""

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

# MUST match GEN_ID_OFFSET in scripts/generate-gallery-json.js.
GEN_ID_OFFSET = 2_000_000_000

_CACHE_TTL_SECONDS = 300.0            # found entries
_NEGATIVE_CACHE_TTL_SECONDS = 60.0    # confirmed "id not found"
_CACHE_MAX_ENTRIES = 5000

# Sentinel: the lookup could not be performed (missing env / gallery DB down).
# Distinct from None, which means "the id definitively does not exist".
PROMPT_LOOKUP_UNAVAILABLE = object()


def _display_name(name, prompt):
    """Caption fallback — mirrors displayName() in generate-gallery-json.js."""
    n = (name or '').strip()
    if n:
        return n if len(n) <= 60 else n[:57] + '…'
    p = ' '.join((prompt or '').split())
    if not p:
        return 'AI Creation'
    words = ' '.join(p.split(' ')[:8])
    short = words if len(words) <= 60 else words[:57] + '…'
    return short[:1].upper() + short[1:]


class PromptStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._client = None
        self._config_warned = False
        # int pool id -> (expires_at_monotonic, entry_dict_or_None)
        self._cache = {}

    # -- internal -----------------------------------------------------------
    def _get_client(self):
        """Lazy singleton client for the GALLERY project only (anon key)."""
        if self._client is not None:
            return self._client
        url = os.getenv('GALLERY_SUPABASE_URL')
        key = os.getenv('GALLERY_SUPABASE_ANON_KEY')
        if not url or not key:
            if not self._config_warned:
                logger.warning(
                    "GALLERY_SUPABASE_URL / GALLERY_SUPABASE_ANON_KEY not set — "
                    "gallery prompt lookups are disabled; Gallery Remix will "
                    "fall back to default prompts."
                )
                self._config_warned = True
            return None
        try:
            from supabase import create_client
            with self._lock:
                if self._client is None:
                    self._client = create_client(url, key)
        except Exception as e:
            logger.error("Failed to create gallery Supabase client: %s", e)
            return None
        return self._client

    def _fetch(self, image_id: int):
        """One live query against the gallery project. Never raises."""
        client = self._get_client()
        if client is None:
            return PROMPT_LOOKUP_UNAVAILABLE
        if image_id >= GEN_ID_OFFSET:
            view, row_id = 'generated_feed', image_id - GEN_ID_OFFSET
        else:
            view, row_id = 'gallery_feed', image_id
        try:
            resp = (
                client.table(view)
                .select('prompt,name')
                .eq('id', row_id)
                .limit(1)
                .execute()
            )
        except Exception as e:
            logger.warning(
                "Gallery prompt lookup failed for id %s (%s): %s",
                image_id, view, e,
            )
            return PROMPT_LOOKUP_UNAVAILABLE
        if not resp.data:
            return None
        row = resp.data[0]
        prompt = (row.get('prompt') or '').strip() or None
        return {
            'prompt': prompt,
            'name': _display_name(row.get('name'), row.get('prompt')),
        }

    # -- public -------------------------------------------------------------
    def resolve(self, image_id):
        """Resolve a pool image id to {'prompt': str|None, 'name': str}.

        Returns:
          dict  — the entry (id exists in the gallery project);
          None  — the id definitively does not exist (deleted / stale pool);
          PROMPT_LOOKUP_UNAVAILABLE — env missing or gallery DB unreachable.
        """
        try:
            image_id = int(image_id)
        except (TypeError, ValueError):
            return None

        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(image_id)
            if hit is not None and hit[0] > now:
                return hit[1]

        result = self._fetch(image_id)
        if result is PROMPT_LOOKUP_UNAVAILABLE:
            return PROMPT_LOOKUP_UNAVAILABLE  # never cache failures

        ttl = _CACHE_TTL_SECONDS if result is not None else _NEGATIVE_CACHE_TTL_SECONDS
        with self._lock:
            if len(self._cache) >= _CACHE_MAX_ENTRIES:
                self._cache.clear()  # simple bound; rebuilt from live queries
            self._cache[image_id] = (now + ttl, result)
        return result

    def configured(self) -> bool:
        """True when the gallery project credentials are present."""
        return bool(
            os.getenv('GALLERY_SUPABASE_URL')
            and os.getenv('GALLERY_SUPABASE_ANON_KEY')
        )


_store = None
_store_init_lock = threading.Lock()


def get_prompt_store() -> PromptStore:
    global _store
    if _store is None:
        with _store_init_lock:
            if _store is None:
                _store = PromptStore()
    return _store
