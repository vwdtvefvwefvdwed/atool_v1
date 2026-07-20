import os
import json
import uuid
import requests
import re
from typing import Dict, Any, Optional, List, Union

CHAT_URL = "https://api.on-demand.io/chat/v1"
EXECUTE_URL = CHAT_URL  # backward-compat alias
MEDIA_URL = "https://api.on-demand.io/media/v1"

# Defaults (mirroring agentforme's agent/ondemand.py + its config defaults).
# Serverless app endpoints are NOT used anymore — everything goes through the
# chat orchestrator with these defaults when credentials don't override them.
DEFAULT_AGENT_IDS = ["agent-1776826082"]
DEFAULT_ENDPOINT_ID = "predefined-gemini-3.5-flash"
DEFAULT_REASONING_MODE = "gemini-3-flash"

# Detects a safety/moderation refusal inside a chat-orchestrator answer that
# returned no image URL (e.g. "rejected by the safety moderation system ...
# public-figure safety policy").
_MODERATION_RE = re.compile(
    r"safety|moderat(?:ion|ed)|public.?figure|content\s+(?:filter|policy)|"
    r"policy\s+violat|blocked\s+the\s+request|rejected\s+by",
    re.IGNORECASE)


def _parse_ondemand_agent_credentials(raw: str) -> Dict[str, Any]:
    """Parse provider credentials for the Agent (chat orchestrator) API.

    Accepted formats:
      - JSON object: {"api_key": ..., "agent_ids": [...], "endpoint_id": ...,
        "reasoning_mode": ...} — only ``api_key`` is required; the rest fall
        back to the defaults above.
      - Plain string: treated as the API key itself.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        data = {"api_key": raw.strip()}
    if not isinstance(data, dict):
        raise ValueError("On-Demand agent credentials must be a JSON object or a plain API key string")
    api_key = data.get("api_key")
    if not api_key:
        raise ValueError("On-Demand agent credentials missing 'api_key'")
    agent_ids = data.get("agent_ids") or list(DEFAULT_AGENT_IDS)
    if isinstance(agent_ids, str):
        agent_ids = [agent_ids]
    return {
        "api_key": api_key,
        "agent_ids": agent_ids,
        "endpoint_id": data.get("endpoint_id") or DEFAULT_ENDPOINT_ID,
        "reasoning_mode": data.get("reasoning_mode") or DEFAULT_REASONING_MODE,
    }

def _create_chat_session(api_key: str, external_user_id: str, agent_ids: List[str], context_metadata: List[Dict[str, str]]) -> str:
    url = f"{EXECUTE_URL}/sessions"
    headers = {"apikey": api_key, "Content-Type": "application/json"}
    body = {
        "agentIds": agent_ids,
        "externalUserId": external_user_id,
        "contextMetadata": context_metadata,
    }
    print(f"[OnDemand Agent] Creating session: {url}")
    resp = requests.post(url, json=body, headers=headers, timeout=30)
    print(f"[OnDemand Agent] Session response: {resp.status_code}")
    if resp.status_code != 201:
        raise RuntimeError(f"Failed to create session: {resp.status_code} {resp.text}")
    session_id = resp.json()["data"]["id"]
    print(f"[OnDemand Agent] Session ID: {session_id}")
    return session_id

def _upload_media_file(api_key: str, file_url: str, file_name: str, file_agent_ids: List[str]) -> Optional[Dict[str, Any]]:
    """Upload an image to the Media endpoint for i2i.
    Returns the media metadata dict (contains id, url, etc.).
    """
    # Download the image first
    try:
        img_resp = requests.get(file_url, timeout=30)
        if img_resp.status_code != 200:
            raise RuntimeError(f"Failed to download input image: {img_resp.status_code}")
    except Exception as e:
        raise RuntimeError(f"Error fetching input image: {e}")
    upload_url = f"{MEDIA_URL}/public/file/raw"
    headers = {"apikey": api_key}
    files = {"file": (file_name, img_resp.content)}
    data = {
        "responseMode": "sync",
        "agents": file_agent_ids,
        "name": file_name,
    }
    print(f"[OnDemand Agent] Uploading media file {file_name} for i2i")
    resp = requests.post(upload_url, headers=headers, files=files, data=data)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Media upload failed: {resp.status_code} {resp.text}")
    media_data = resp.json()["data"]
    print(f"[OnDemand Agent] Media uploaded: {media_data.get('id')}")
    return media_data

def _parse_sse_event(data_str: str) -> Dict[str, Any]:
    try:
        return json.loads(data_str)
    except (json.JSONDecodeError, ValueError):
        return {}

def _submit_query_stream(api_key: str, session_id: str, endpoint_id: str, query: str,
                         agent_ids: List[str], reasoning_mode: Optional[str],
                         timeout: int = 240) -> Dict[str, Any]:
    """Submit a chat query in STREAM mode and reassemble the answer from the
    SSE event stream (sync mode returns HTTP 500 model_error on OnDemand's
    fulfillment layer as of 2026-07, while the identical request in stream mode
    succeeds). Returns a payload shaped like a sync response:
    ``{"data": {"answer": <str>, "sessionId": ..., "messageId": ...,
    "status": "completed"}}`` so ``_extract_image_url`` works unchanged."""
    url = f"{CHAT_URL}/sessions/{session_id}/query"
    headers = {"apikey": api_key, "Content-Type": "application/json"}
    body = {
        "endpointId": endpoint_id,
        "query": query,
        "agentIds": agent_ids,
        "responseMode": "stream",
        "reasoningMode": reasoning_mode or DEFAULT_REASONING_MODE,
        "modelConfigs": {},
    }
    print(f"[OnDemand Agent] Submitting STREAM query to: {url}")
    print(f"[OnDemand Agent] Query snippet: {query[:100]}...")
    resp = requests.post(url, json=body, headers=headers, timeout=timeout, stream=True)
    print(f"[OnDemand Agent] Query response: {resp.status_code}")
    if resp.status_code != 200:
        raise RuntimeError(f"Stream query failed: {resp.status_code} {resp.text}")
    answer = ""
    sid = msg_id = ""
    metrics: Dict[str, Any] = {}
    ev_types: Dict[str, int] = {}
    try:
        for raw_line in resp.iter_lines():
            if not raw_line:
                continue
            line = raw_line.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if data_str == "[DONE]":
                break
            ev = _parse_sse_event(data_str)
            if not ev:
                continue
            et = ev.get("eventType", "")
            if et:
                ev_types[et] = ev_types.get(et, 0) + 1
            if et == "fulfillment":
                piece = ev.get("answer") or ""
                if piece:
                    answer += piece
                sid = ev.get("sessionId", sid)
                msg_id = ev.get("messageId", msg_id)
            elif et == "metricsLog":
                if "publicMetrics" in ev:
                    metrics = ev["publicMetrics"]
    except requests.RequestException as e:
        raise RuntimeError(f"Stream interrupted: {e}")
    finally:
        try:
            resp.close()
        except Exception:
            pass
    if not answer:
        raise RuntimeError(f"Stream query returned no fulfillment answer (events: {ev_types})")
    print(f"[OnDemand Agent] Stream reassembled answer ({len(answer)} chars, events={ev_types})")
    return {
        "message": "Chat query submitted successfully",
        "data": {
            "sessionId": sid or session_id,
            "messageId": msg_id,
            "answer": answer,
            "metrics": metrics,
            "status": "completed",
            "contextMetadata": [],
        },
    }


def _extract_image_url(payload: Dict[str, Any]) -> Optional[str]:
    """Extract the generated image URL.
    Handles both sync (root "data") and webhook (root "message") structures.
    """
    root = payload.get("data") or payload.get("message") or payload
    
    # 1. Look into executedAgents response payloads
    steps = root.get("executionLog", {}).get("queryPlan", {}).get("steps", [])
    for step in steps:
        for agent in step.get("executedAgents", []):
            resp_str = agent.get("response")
            if resp_str:
                try:
                    resp_json = json.loads(resp_str)
                    if resp_json.get("url"):
                        print(f"[OnDemand Agent] URL found in executedAgents: {resp_json['url']}")
                        return resp_json["url"]
                except Exception:
                    continue
    
    # 2. Fallback: scrape URL from answer text - handle all image formats
    answer = root.get("answer", "")
    if answer:
        print(f"[OnDemand Agent] Answer snippet: {answer[:200]}...")
        # Match common image extensions: jpg, jpeg, png, webp, gif
        match = re.search(r"https://[^\s\"']+?\.(?:jpg|jpeg|png|webp|gif)(?:\?[^\s\"']*)?", answer, re.IGNORECASE)
        if match:
            url = match.group(0)
            print(f"[OnDemand Agent] URL extracted from answer: {url}")
            return url
    
    # 3. Also check statusLogs for answer
    status_logs = root.get("statusLogs", [])
    for log in status_logs:
        log_answer = log.get("answer", "")
        if log_answer:
            match = re.search(r"https://[^\s\"']+?\.(?:jpg|jpeg|png|webp|gif)(?:\?[^\s\"']*)?", log_answer, re.IGNORECASE)
            if match:
                url = match.group(0)
                print(f"[OnDemand Agent] URL extracted from statusLogs: {url}")
                return url
    
    return None

def generate_with_ondemand_agent(
    prompt: str,
    model: str,
    aspect_ratio: str = "1:1",
    api_key: str = "",
    input_image_url: Union[str, List[str], None] = None,
    job_type: str = "image",
    duration: int = 5,
    provider_key: str = "vision-ondemand",
    job_id: Optional[str] = None,
    webhook_url: Optional[str] = None,
    reference_image_url: Union[str, List[str], None] = None,
    use_direct_agent: bool = False,  # DEPRECATED: serverless direct path removed; kept for call-site compat
    **kwargs,
) -> Dict[str, Any]:
    """Generate an image using the On‑Demand Agent API via the CHAT ORCHESTRATOR
    (stream mode), mirroring agentforme's agent/ondemand.py.

    The serverless app endpoints (e.g. Nano Banana PRO direct edit-image2) have
    been removed completely — both text-to-image and image-to-image go through
    the chat orchestrator:
      - t2i: the prompt is submitted with an instruction to return ONLY the
        direct image URL.
      - i2i: reference image URLs are handed to the image agent in the query
        ("Use these as image_url: ..." + variation_prompt), same as agentforme.

    Defaults when credentials omit them: agent ``agent-1776826082``, endpoint
    ``predefined-gemini-3.5-flash``, reasoning mode ``gemini-3-flash``.
    Returns ``{"success": True, "url": "..."}``.
    """
    from provider_api_keys import get_provider_api_key

    cred_record = get_provider_api_key(provider_key)
    if not cred_record:
        raise RuntimeError(f"No credentials found for provider '{provider_key}'")
    raw = cred_record.get("api_key")
    if not raw:
        raise RuntimeError(f"Credential record for '{provider_key}' missing 'api_key' field")
    creds = _parse_ondemand_agent_credentials(raw)
    api_key_val = creds["api_key"]
    text_agent_ids = creds["agent_ids"]
    endpoint_id = creds["endpoint_id"]
    reasoning_mode = creds.get("reasoning_mode") or DEFAULT_REASONING_MODE

    print(f"[OnDemand Agent] Starting generation for job {job_id or 'N/A'}")
    print(f"[OnDemand Agent] Agents: {text_agent_ids}, Endpoint: {endpoint_id}, Reasoning: {reasoning_mode}")

    # Collect all image URLs from various parameters
    all_urls = []
    if input_image_url:
        urls = input_image_url if isinstance(input_image_url, list) else [input_image_url]
        all_urls.extend(urls)
    if reference_image_url:
        ref_urls = reference_image_url if isinstance(reference_image_url, list) else [reference_image_url]
        all_urls.extend(ref_urls)

    IMAGE_PARAM_PATTERNS = ['style_image_url', 'second_image_url', 'target_image_url', 'base_image_url', 'mask_image_url']
    for pattern in IMAGE_PARAM_PATTERNS:
        extra_url = kwargs.get(pattern)
        if extra_url:
            extra_urls = extra_url if isinstance(extra_url, list) else [extra_url]
            all_urls.extend(extra_urls)

    # Aspect ratio: the chat orchestrator has no structured size param, so it
    # is passed inside the query text (skipped for the default 1:1).
    ratio_hint = ""
    if aspect_ratio and aspect_ratio not in ("1:1", "auto"):
        ratio_hint = f" The image MUST have a {aspect_ratio} aspect ratio."

    # Build the chat-orchestrator query (agentforme-style phrasing).
    if all_urls:
        # Image-to-image / edit: hand the reference URLs to the image agent.
        refs = ", ".join(all_urls)
        query = ("Use these as image_url: " + refs
                 + " and produce an edited image. "
                 + "Re-render the subject from the reference image INSIDE the "
                 "described scene — matching the scene's lighting direction, "
                 "colour grade, perspective and scale; do NOT composite, "
                 "collage or paste the reference photo on top of a backdrop. "
                 + "variation_prompt=" + prompt + ratio_hint
                 + "\nWhen done, return ONLY the direct image URL of the edited "
                 "image, on its own line surrounded by whitespace — no "
                 "markdown, no punctuation and no other text touching the URL.")
        print(f"[OnDemand Agent] CHAT orchestrator i2i mode ({len(all_urls)} image URL(s))")
    else:
        # Text-to-image.
        query = ("Generate a single high-quality image from this description and "
                 "return ONLY the direct image URL, on its own line surrounded "
                 "by whitespace — no markdown, no punctuation and no other text "
                 "touching the URL:\n" + prompt + ratio_hint)
        print(f"[OnDemand Agent] CHAT orchestrator t2i mode")

    # Prepare external user id – reuse job_id if available, else a new UUID
    external_user_id = job_id or str(uuid.uuid4())
    context_metadata = [{"key": "jobId", "value": external_user_id}]
    session_agent_ids = text_agent_ids

    session_id = _create_chat_session(api_key_val, external_user_id, session_agent_ids, context_metadata)
    response_payload = _submit_query_stream(
        api_key_val,
        session_id,
        endpoint_id,
        query,
        session_agent_ids,
        reasoning_mode,
        timeout=240,
    )
    image_url = _extract_image_url(response_payload)
    if not image_url:
        answer = (response_payload.get("data") or {}).get("answer", "") or ""
        if _MODERATION_RE.search(answer):
            raise RuntimeError(f"On-Demand moderation-blocked: {answer[:400]}")
        raise RuntimeError(
            f"On‑Demand chat orchestrator returned no image URL. answer={answer[:600]}")
    return {"success": True, "url": image_url, "type": "image"}
