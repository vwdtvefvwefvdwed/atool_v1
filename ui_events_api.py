"""
ui_events_api.py
-----------------
Adaptive-UI layout endpoint.

ZERO SUPABASE USAGE.
====================
This module does NOT touch the Supabase REST API or any Supabase client.
All telemetry storage and reads happen on Cloudflare Analytics Engine (AE)
via its SQL HTTP endpoint. Layout decisions are cached in-process and
persisted to a small local JSON file for restart durability.

Routes:
  GET  /api/ui/layout/<place>  - returns the layout config for caller
  GET  /api/ui/health          - liveness check

Note: telemetry ingest (POST /api/ui/events) no longer lives here.
Browsers post events directly to the Cloudflare Worker
(see ui-telemetry/) which writes them into Analytics Engine.

Environment:
  CF_ACCOUNT_ID                - Cloudflare account id (required to read AE)
  CF_API_TOKEN                 - Token with `Account Analytics: Read`
  UI_AE_DATASET                - AE dataset name (default: ui_events)
  UI_MIN_SAMPLE                - sessions threshold (default 500)
  UI_LOCK_DAYS                 - decision lock window (default 7)
  UI_LAYOUT_CACHE_TTL_SEC      - in-memory cache TTL (default 600)
  UI_DECISIONS_FILE            - JSON file path for persisted decisions
                                 (default: ./ui_decisions.json next to this
                                 module). Resilient across restarts.

Ad invariant (unchanged): every ad:* slot in the default layout must be
present in every returned layout — only reordered, never dropped.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

ui_events_bp = Blueprint("ui_events", __name__, url_prefix="/api/ui")

# ---- constants ------------------------------------------------------------
MIN_SAMPLE_FOR_DECISION = int(os.getenv("UI_MIN_SAMPLE", "500"))
# Extra anti-botting gate: require a minimum total view_count AND a minimum
# number of distinct days of activity. Cheap to mint sessions, harder to
# spread fake activity across many days.
MIN_VIEWS_FOR_DECISION  = int(os.getenv("UI_MIN_VIEWS", "1500"))
MIN_DAYS_FOR_DECISION   = int(os.getenv("UI_MIN_DAYS", "3"))
LOCK_DAYS               = int(os.getenv("UI_LOCK_DAYS", "7"))
LAYOUT_CACHE_TTL_SEC    = int(os.getenv("UI_LAYOUT_CACHE_TTL_SEC", "600"))
AE_DATASET              = os.getenv("UI_AE_DATASET", "ui_events")
CF_ACCOUNT_ID           = os.getenv("CF_ACCOUNT_ID", "")
CF_API_TOKEN            = os.getenv("CF_API_TOKEN", "")
DECISIONS_FILE          = os.getenv(
    "UI_DECISIONS_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui_decisions.json"),
)

PINNED_ADS = {"ad:hero_top"}

# Whitelist regex for any string we interpolate into AE SQL (no parameter
# binding is available, so we hard-restrict to a safe charset).
_SAFE = re.compile(r"^[A-Za-z0-9_:.\-]{1,64}$")

# Bound the in-process layout cache so an attacker cannot grow it without
# limit by spamming unique cohort/region/place tuples.
LAYOUT_CACHE_MAX = int(os.getenv("UI_LAYOUT_CACHE_MAX", "2000"))

# Bumping this integer (or setting UI_LAYOUT_CACHE_VERSION env var)
# invalidates every entry in the in-process layout cache without a
# process restart. Bump it whenever DEFAULT_LAYOUTS, scoring weights, or
# the decider algorithm change in a way the 10-min cache could mask.
LAYOUT_CACHE_VERSION = os.getenv("UI_LAYOUT_CACHE_VERSION", "1")


DEFAULT_LAYOUTS: Dict[str, Dict[str, Any]] = {
    "home": {
        "slot_order": [
            "ad:hero_top",
            "section:how_it_works",
            "ad:after_how",
            "section:why_choose",
            "ad:after_why",
            "section:who_for",
            "ad:after_who",
            "section:faqs",
        ],
        "compact": False,
        "version": 1,
    },
}


# ---- helpers --------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _detect_region() -> str:
    return (
        request.headers.get("CF-IPCountry")
        or request.cookies.get("region")
        or "XX"
    ).upper()[:2]


def _detect_cohort(payload_cohort: Optional[str] = None) -> str:
    if payload_cohort:
        return str(payload_cohort)[:64]
    ua = (request.headers.get("User-Agent") or "").lower()
    device = "mobile" if any(k in ua for k in ("mobi", "android", "iphone")) else "desktop"
    return f"{device}_new"


def _safe(value: str) -> str:
    """Return value if it matches the SQL allowlist, else empty string."""
    if value and _SAFE.match(value):
        return value
    return ""


# ---- Analytics Engine SQL client (no Supabase) ----------------------------
def _ae_sql(sql: str) -> List[Dict[str, Any]]:
    """Run a SQL query against Cloudflare Analytics Engine. Returns the
    `data` array from the response (list of dict rows). On any failure
    returns []."""
    if not CF_ACCOUNT_ID or not CF_API_TOKEN:
        logger.debug("[ui] AE credentials missing; returning no rows")
        return []
    try:
        url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/analytics_engine/sql"
        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {CF_API_TOKEN}"},
            data=sql,
            timeout=8,
        )
        if r.status_code != 200:
            logger.debug("[ui] AE SQL non-200 %s: %s", r.status_code, r.text[:200])
            return []
        body = r.json() or {}
        return body.get("data") or []
    except Exception as e:
        logger.debug("[ui] AE SQL request failed: %s", e)
        return []


def _attention_map(cohort: str, region: str, place: str) -> List[Dict[str, Any]]:
    """Aggregate per-slot attention metrics for the given (cohort, region,
    place) over the last 14 days, computed entirely inside Analytics
    Engine. Returns rows shaped like:
        { slot_id, sample_size, view_count, click_count, ad_view_count,
          dwell_avg_ms, interaction_rate, dismiss_rate }
    """
    cohort_s = _safe(cohort)
    region_s = _safe(region)
    place_s  = _safe(place)
    if not (cohort_s and region_s and place_s):
        return []

    sql = f"""
      SELECT
        blob2                                                AS slot_id,
        uniqExact(index1)                                    AS sample_size,
        countIf(blob1 = 'view_enter')                        AS view_count,
        countIf(blob1 IN ('click', 'ad_click'))              AS click_count,
        countIf(blob1 = 'ad_view')                           AS ad_view_count,
        countIf(blob1 = 'ad_dismiss')                        AS ad_dismiss_count,
        avg(if(double1 > 0, double1, NULL))                  AS dwell_avg_ms,
        uniqExact(toDate(timestamp))                         AS active_days
      FROM {AE_DATASET}
      WHERE timestamp > NOW() - INTERVAL '14' DAY
        AND blob3 = '{place_s}'
        AND blob4 = '{cohort_s}'
        AND blob5 = '{region_s}'
      GROUP BY slot_id
      LIMIT 200
    """

    rows = _ae_sql(sql)
    out: List[Dict[str, Any]] = []
    for r in rows:
        view_count = int(r.get("view_count") or 0)
        click_count = int(r.get("click_count") or 0)
        ad_view = int(r.get("ad_view_count") or 0)
        ad_dismiss = int(r.get("ad_dismiss_count") or 0)
        out.append({
            "slot_id":          str(r.get("slot_id") or "")[:64],
            "sample_size":      int(r.get("sample_size") or 0),
            "view_count":       view_count,
            "click_count":      click_count,
            "ad_view_count":    ad_view,
            "ad_dismiss_count": ad_dismiss,
            "dwell_avg_ms":     float(r.get("dwell_avg_ms") or 0.0),
            "active_days":      int(r.get("active_days") or 0),
            "interaction_rate": (click_count / view_count) if view_count else 0.0,
            "dismiss_rate":     (ad_dismiss / ad_view) if ad_view else 0.0,
        })
    return out


# ---- decision persistence (local JSON file, no DB) ------------------------
_DECISIONS_LOCK = threading.Lock()
_DECISIONS_CACHE: Optional[Dict[str, Dict[str, Any]]] = None


def _decisions_load() -> Dict[str, Dict[str, Any]]:
    global _DECISIONS_CACHE
    if _DECISIONS_CACHE is not None:
        return _DECISIONS_CACHE
    try:
        if os.path.exists(DECISIONS_FILE):
            with open(DECISIONS_FILE, "r", encoding="utf-8") as f:
                _DECISIONS_CACHE = json.load(f) or {}
        else:
            _DECISIONS_CACHE = {}
    except Exception as e:
        logger.warning("[ui] decisions file unreadable: %s", e)
        _DECISIONS_CACHE = {}
    return _DECISIONS_CACHE


def _decisions_save(data: Dict[str, Dict[str, Any]]) -> None:
    try:
        tmp = DECISIONS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
        os.replace(tmp, DECISIONS_FILE)
    except Exception as e:
        logger.warning("[ui] decisions file write failed: %s", e)


def _decision_key(cohort: str, region: str, place: str) -> str:
    return f"{cohort}|{region}|{place}"


def _get_locked_decision(cohort: str, region: str, place: str) -> Optional[Dict[str, Any]]:
    decs = _decisions_load()
    row = decs.get(_decision_key(cohort, region, place))
    if not row:
        return None
    try:
        until = datetime.fromisoformat(str(row.get("locked_until", "")).replace("Z", "+00:00"))
    except Exception:
        return None
    if until <= datetime.now(timezone.utc):
        return None
    return row


def _persist_decision(cohort: str, region: str, place: str, cfg: Dict[str, Any]) -> str:
    """Persist decided layout into the local JSON file. Returns the
    locked_until ISO string (or empty string on disk failure)."""
    locked_until = (datetime.now(timezone.utc) + timedelta(days=LOCK_DAYS)).isoformat()
    payload = {
        "cohort_key":   cohort,
        "region":       region,
        "place":        place,
        "config": {
            "slot_order": cfg.get("slot_order", []),
            "compact":    bool(cfg.get("compact", False)),
        },
        "version":      int(cfg.get("version", 1)),
        "locked_until": locked_until,
        "promoted_at":  _now_iso(),
    }
    with _DECISIONS_LOCK:
        # Write a COPY to disk first; only commit to in-memory cache on
        # success. Prevents memory/disk divergence if the disk write fails.
        decs = _decisions_load()
        candidate = dict(decs)
        candidate[_decision_key(cohort, region, place)] = payload
        try:
            tmp = DECISIONS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(candidate, f, separators=(",", ":"))
            os.replace(tmp, DECISIONS_FILE)
        except Exception as e:
            logger.warning("[ui] decisions file write failed: %s", e)
            return ""
        decs[_decision_key(cohort, region, place)] = payload
    return locked_until


# ---- in-process layout cache (bounded LRU) -------------------------------
# Key includes LAYOUT_CACHE_VERSION so deploys can invalidate the entire
# cache without restarting the process, simply by bumping the env var.
_LAYOUT_CACHE: "OrderedDict[Tuple[str, str, str, str], Tuple[Dict[str, Any], float]]" = OrderedDict()
_LAYOUT_CACHE_LOCK = threading.Lock()


def _cached_layout(cohort: str, region: str, place: str) -> Dict[str, Any]:
    key = (LAYOUT_CACHE_VERSION, cohort, region, place)
    now_ts = time.time()
    with _LAYOUT_CACHE_LOCK:
        hit = _LAYOUT_CACHE.get(key)
        if hit and hit[1] > now_ts:
            _LAYOUT_CACHE.move_to_end(key)
            return hit[0]
        cfg = _decide_layout(cohort, region, place)
        _LAYOUT_CACHE[key] = (cfg, now_ts + LAYOUT_CACHE_TTL_SEC)
        _LAYOUT_CACHE.move_to_end(key)
        while len(_LAYOUT_CACHE) > LAYOUT_CACHE_MAX:
            _LAYOUT_CACHE.popitem(last=False)
        return cfg


# ---- decision logic -------------------------------------------------------
def _enforce_ad_invariant(cfg: Dict[str, Any], default_order: List[str]) -> Dict[str, Any]:
    """Guarantee that every ad:* slot from default appears in cfg.slot_order,
    and that no UNKNOWN slot ids leak through from a stale persisted
    decision (e.g. a section that was later renamed in DEFAULT_LAYOUTS).
    Missing ads are re-inserted at their original default index instead of
    being appended at the end (which would clump them after content)."""
    known = set(default_order)
    order = [s for s in (cfg.get("slot_order") or []) if s in known]
    have = set(order)
    for s in default_order:
        if s.startswith("ad:") and s not in have:
            idx = default_order.index(s)
            insert_at = min(idx, len(order))
            order.insert(insert_at, s)
            have.add(s)
    cfg["slot_order"] = order
    return cfg


def _decide_layout(cohort: str, region: str, place: str) -> Dict[str, Any]:
    """Pure-Python decider: read AE aggregates, rank, interleave, persist."""
    default = DEFAULT_LAYOUTS.get(place)
    if default is None:
        # Unknown place — return empty so client falls back to its static layout
        return {
            "slot_order":   [],
            "compact":      False,
            "version":      0,
            "locked_until": None,
            "source":       "unknown_place",
        }
    default_order: List[str] = list(default["slot_order"])
    fallback: Dict[str, Any] = {
        "slot_order":   default_order,
        "compact":      default.get("compact", False),
        "version":      default.get("version", 1),
        "locked_until": None,
        "source":       "default",
    }

    try:
        # 1. Honor a locked decision if one exists and is still in window.
        locked = _get_locked_decision(cohort, region, place)
        if locked:
            cfg = dict(locked.get("config") or {})
            cfg["version"]      = int(locked.get("version", 1))
            cfg["locked_until"] = locked.get("locked_until")
            cfg["source"]       = "locked"
            cfg.setdefault("compact", default.get("compact", False))
            return _enforce_ad_invariant(cfg, default_order)

        # 2. Compute a fresh ranking from Analytics Engine.
        rows = _attention_map(cohort, region, place)
        max_sample = max((int(r.get("sample_size") or 0) for r in rows), default=0)
        total_views = sum(int(r.get("view_count") or 0) for r in rows)
        max_days = max((int(r.get("active_days") or 0) for r in rows), default=0)
        # All three gates must pass — single-burst botting that mints many
        # session IDs in minutes will fail the days/views gates.
        if (max_sample < MIN_SAMPLE_FOR_DECISION
                or total_views < MIN_VIEWS_FOR_DECISION
                or max_days < MIN_DAYS_FOR_DECISION):
            return fallback

        scores: Dict[str, float] = {}
        for r in rows:
            sid = r["slot_id"]
            ir = float(r.get("interaction_rate") or 0)
            dw = float(r.get("dwell_avg_ms") or 0)
            scores[sid] = ir * math.log1p(dw)

        content_slots = [s for s in default_order if not s.startswith("ad:")]
        ad_slots      = [s for s in default_order if s.startswith("ad:")]
        pinned_ads    = [s for s in ad_slots if s in PINNED_ADS]
        movable_ads   = [s for s in ad_slots if s not in PINNED_ADS]

        ranked_content = sorted(
            content_slots,
            key=lambda s: (-scores.get(s, 0), default_order.index(s)),
        )

        new_order: List[str] = []
        ad_iter = iter(movable_ads)
        for i, content in enumerate(ranked_content):
            new_order.append(content)
            if i < len(movable_ads):
                try:
                    new_order.append(next(ad_iter))
                except StopIteration:
                    pass
        new_order.extend(list(ad_iter))

        for pinned in pinned_ads:
            idx = default_order.index(pinned)
            insert_at = min(idx, len(new_order))
            new_order = [s for s in new_order if s != pinned]
            new_order.insert(insert_at, pinned)

        decided = {
            "slot_order":   new_order,
            "compact":      fallback["compact"],
            "version":      fallback["version"],
            "locked_until": None,
            "source":       "decided",
        }
        decided = _enforce_ad_invariant(decided, default_order)

        # 3. Persist locally so subsequent reads use the locked branch.
        locked_until_iso = _persist_decision(cohort, region, place, decided)
        if locked_until_iso:
            decided["locked_until"] = locked_until_iso
        return decided
    except Exception as e:
        logger.warning("[ui] decide_layout fallback to default: %s", e)
        return fallback


# ---- routes ---------------------------------------------------------------
@ui_events_bp.route("/layout/<place>", methods=["GET"])
def get_layout(place: str):
    cohort = _detect_cohort(request.args.get("cohort"))
    region_arg = (request.args.get("region") or "").upper()
    # Treat 'XX' or empty as 'no client signal' and fall back to CF header.
    region = region_arg if region_arg and region_arg != "XX" else _detect_region()
    region = region[:2].upper()
    place_safe = str(place)[:48]

    # Validate keys BEFORE they are used as cache keys / SQL params. Unsafe
    # values fall back to safe defaults so callers cannot grow the LRU
    # cache with arbitrary garbage.
    if not _SAFE.match(cohort):
        cohort = "desktop_new"
    if not re.match(r"^[A-Z]{2}$", region):
        region = "XX"
    if not _SAFE.match(place_safe):
        place_safe = "unknown"

    cfg = _cached_layout(cohort, region, place_safe)

    resp = jsonify({
        "ok":        True,
        "cohort":    cohort,
        "region":    region,
        "place":     place_safe,
        "config":    cfg,
        "served_at": _now_iso(),
    })
    # Per-user cache; never share decided layouts across browsers.
    resp.headers["Cache-Control"] = "private, max-age=600"
    # Decisions depend on cohort/region — prevent intermediary caches from
    # collapsing different users' responses together.
    resp.headers["Vary"] = "Cookie, CF-IPCountry"
    return resp, 200


# Cached AE liveness probe so /health stays cheap even under polling.
_AE_PROBE_LOCK = threading.Lock()
_AE_PROBE_CACHE: Dict[str, Any] = {"ok": False, "checked_at": 0.0}
_AE_PROBE_TTL = 60.0


def _ae_probe() -> bool:
    if not (CF_ACCOUNT_ID and CF_API_TOKEN):
        return False
    now_ts = time.time()
    with _AE_PROBE_LOCK:
        if (now_ts - float(_AE_PROBE_CACHE.get("checked_at") or 0)) < _AE_PROBE_TTL:
            return bool(_AE_PROBE_CACHE.get("ok"))
        try:
            rows = _ae_sql("SELECT 1 AS ok LIMIT 1")
            ok = bool(rows)
        except Exception:
            ok = False
        _AE_PROBE_CACHE["ok"] = ok
        _AE_PROBE_CACHE["checked_at"] = now_ts
        return ok


@ui_events_bp.route("/health", methods=["GET"])
def ui_health():
    return jsonify({
        "ok":             True,
        "service":        "adaptive_ui",
        "ae_configured":  bool(CF_ACCOUNT_ID and CF_API_TOKEN),
        "ae_reachable":   _ae_probe(),
    }), 200
