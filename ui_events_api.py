"""
ui_events_api.py
-----------------
Adaptive-UI telemetry + layout decision endpoints.

Strict scope:
  * Only reads/writes the new tables introduced by 032_adaptive_ui.sql
    (ui_events, cohort_attention_map, layout_decisions, layout_history).
  * Never touches jobs / providers / workers / queues.
  * No background threads. No new processes. Pure request handlers.

Routes:
  POST /api/ui/events          - batched telemetry ingest
  GET  /api/ui/layout/<place>  - returns the locked layout JSON for the
                                  caller's (cohort, region, place).

The layout JSON contains:
  - "slot_order": ordered list of slot ids the client should render
  - "ad_slots":  { slot_id -> "<keep|move:<target>>" }  // never "remove"
  - "compact":   bool
  - "version":   int
  - "locked_until": ISO timestamp

Ad invariant: the decider may only output 'keep' or 'move'. The total
count of ad components rendered by the client equals the count in the
default static layout. Removal is unrepresentable.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, request

try:
    from supabase_client import supabase
except Exception:  # pragma: no cover
    supabase = None

logger = logging.getLogger(__name__)

ui_events_bp = Blueprint("ui_events", __name__, url_prefix="/api/ui")

# ---- constants ------------------------------------------------------------
MAX_BATCH = 100
MIN_SAMPLE_FOR_DECISION = int(os.getenv("UI_MIN_SAMPLE", "500"))
LOCK_DAYS = int(os.getenv("UI_LOCK_DAYS", "7"))
ALLOWED_EVENT_TYPES = {
    "view_enter", "view_exit", "click",
    "ad_view", "ad_click", "ad_dismiss",
    "scroll_25", "scroll_50", "scroll_75", "scroll_100",
    "gen_start", "gen_complete", "gen_abandon",
}

# Default layout per place. This MUST mirror the static order in the
# corresponding React page. The ad invariant: every "ad:*" entry here is
# preserved in every decided layout — only reordered, never dropped.
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
    return f"{device}_default"


def _validate_event(ev: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    et = ev.get("event_type")
    if et not in ALLOWED_EVENT_TYPES:
        return None
    slot_id = ev.get("slot_id")
    place = ev.get("place")
    if not slot_id or not place:
        return None
    session_id = ev.get("session_id")
    if not session_id:
        return None
    return {
        "user_id": ev.get("user_id"),
        "session_id": str(session_id)[:64],
        "cohort_key": _detect_cohort(ev.get("cohort_key")),
        "region": (ev.get("region") or _detect_region())[:2].upper(),
        "place": str(place)[:48],
        "slot_id": str(slot_id)[:48],
        "event_type": et,
        "payload": ev.get("payload") or {},
    }


# ---- decision logic (pure, no AI) -----------------------------------------
def _decide_layout(cohort: str, region: str, place: str) -> Dict[str, Any]:
    """
    Build a layout for (cohort, region, place).
    - Always returns the default layout's full set of ad slots (count
      preserved).
    - Reorders content sections and ad slots based on per-slot
      interaction_rate × dwell_avg_ms from cohort_attention_map.
    - Falls back to default whenever data is insufficient.
    """
    default = DEFAULT_LAYOUTS.get(place) or DEFAULT_LAYOUTS["home"]
    default_order: List[str] = list(default["slot_order"])
    result: Dict[str, Any] = {
        "slot_order": default_order,
        "compact": default.get("compact", False),
        "version": default.get("version", 1),
        "locked_until": None,
        "source": "default",
    }

    if supabase is None:
        return result

    try:
        # 1. Honor a locked decision if one exists and is still in window.
        locked = (
            supabase.table("layout_decisions")
            .select("config, version, locked_until")
            .eq("cohort_key", cohort).eq("region", region).eq("place", place)
            .limit(1).execute().data
        )
        if locked:
            row = locked[0]
            try:
                until = datetime.fromisoformat(str(row["locked_until"]).replace("Z", "+00:00"))
            except Exception:
                until = None
            if until and until > datetime.now(timezone.utc):
                cfg = row.get("config") or {}
                cfg["version"] = row.get("version", 1)
                cfg["locked_until"] = row["locked_until"]
                cfg["source"] = "locked"
                # Repair: ensure ad count matches default (invariant).
                cfg = _enforce_ad_invariant(cfg, default_order)
                return cfg

        # 2. Otherwise, compute a fresh ranking from the materialized view.
        rows = (
            supabase.table("cohort_attention_map")
            .select("slot_id, sample_size, interaction_rate, dwell_avg_ms, dismiss_rate")
            .eq("cohort_key", cohort).eq("region", region).eq("place", place)
            .execute().data or []
        )
        total_sample = sum(int(r.get("sample_size") or 0) for r in rows)
        if total_sample < MIN_SAMPLE_FOR_DECISION:
            return result  # not enough data — stay on default

        # Score = interaction_rate * log(1 + dwell_ms).
        # Higher score => move ads adjacent to this slot.
        scores: Dict[str, float] = {}
        for r in rows:
            sid = r["slot_id"]
            ir = float(r.get("interaction_rate") or 0)
            dw = float(r.get("dwell_avg_ms") or 0)
            import math
            scores[sid] = ir * math.log1p(dw)

        content_slots = [s for s in default_order if not s.startswith("ad:")]
        ad_slots = [s for s in default_order if s.startswith("ad:")]

        # Rank content sections by score, fall back to original order on ties.
        ranked_content = sorted(
            content_slots,
            key=lambda s: (-scores.get(s, 0), default_order.index(s)),
        )

        # Place ads AFTER the top-N highest-attention content blocks. Ad
        # count == len(ad_slots) is preserved by construction.
        new_order: List[str] = []
        ad_iter = iter(ad_slots)
        for i, content in enumerate(ranked_content):
            new_order.append(content)
            try:
                if i < len(ad_slots):
                    new_order.append(next(ad_iter))
            except StopIteration:
                pass
        # Append any remaining ads at the bottom (still kept!).
        new_order.extend(list(ad_iter))

        decided = {
            "slot_order": new_order,
            "compact": result["compact"],
            "version": result["version"],
            "locked_until": None,
            "source": "decided",
        }
        return _enforce_ad_invariant(decided, default_order)
    except Exception as e:
        logger.warning("[ui] decide_layout fallback to default: %s", e)
        return result


def _enforce_ad_invariant(cfg: Dict[str, Any], default_order: List[str]) -> Dict[str, Any]:
    """Guarantee that every ad:* slot from default appears in cfg.slot_order."""
    order = list(cfg.get("slot_order") or [])
    have = set(order)
    for s in default_order:
        if s.startswith("ad:") and s not in have:
            order.append(s)  # never drop an ad
    cfg["slot_order"] = order
    return cfg


# ---- routes ---------------------------------------------------------------
@ui_events_bp.route("/events", methods=["POST"])
def ingest_events():
    if supabase is None:
        return jsonify({"ok": False, "reason": "no_db"}), 200  # silent ok
    try:
        body = request.get_json(silent=True) or {}
        events = body.get("events") or []
        if not isinstance(events, list):
            return jsonify({"ok": False, "reason": "events_not_list"}), 400
        if len(events) > MAX_BATCH:
            events = events[:MAX_BATCH]
        rows = list(filter(None, (_validate_event(e) for e in events)))
        if not rows:
            return jsonify({"ok": True, "inserted": 0}), 200
        supabase.table("ui_events").insert(rows).execute()
        return jsonify({"ok": True, "inserted": len(rows)}), 200
    except Exception as e:
        logger.exception("[ui] event ingest failed: %s", e)
        # Telemetry must never break the user. Always return 200.
        return jsonify({"ok": False, "error": str(e)[:120]}), 200


@ui_events_bp.route("/layout/<place>", methods=["GET"])
def get_layout(place: str):
    cohort = _detect_cohort(request.args.get("cohort"))
    region = (request.args.get("region") or _detect_region())[:2].upper()
    cfg = _decide_layout(cohort, region, str(place)[:48])
    resp = jsonify({
        "ok": True,
        "cohort": cohort,
        "region": region,
        "place": place,
        "config": cfg,
        "served_at": _now_iso(),
    })
    # Edge cache for 10 minutes per (cohort, region, place).
    resp.headers["Cache-Control"] = "public, max-age=600, s-maxage=600"
    resp.headers["Vary"] = "CF-IPCountry"
    return resp, 200


@ui_events_bp.route("/health", methods=["GET"])
def ui_health():
    return jsonify({"ok": True, "service": "adaptive_ui"}), 200
