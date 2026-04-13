import os
import json
import uuid
import requests
import re
from typing import Dict, Any, Optional, List, Union

EXECUTE_URL = "https://api.on-demand.io/chat/v1"
MEDIA_URL = "https://api.on-demand.io/media/v1"

def _parse_ondemand_agent_credentials(raw: str) -> Dict[str, Any]:
    """Parse provider credentials for Agent API.
    Expected JSON keys:
        api_key: str
        agent_ids: List[str]          # Used for both text-to-image and image-to-image
        endpoint_id: str
        reasoning_mode: str (optional)
    """
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("On-Demand agent credentials must be a JSON object")
    api_key = data.get("api_key")
    agent_ids = data.get("agent_ids")
    endpoint_id = data.get("endpoint_id")
    reasoning_mode = data.get("reasoning_mode", "gemini-3-flash")
    if not api_key or not agent_ids or not endpoint_id:
        raise ValueError("Missing required fields in On-Demand agent credentials")
    return {
        "api_key": api_key,
        "agent_ids": agent_ids,
        "endpoint_id": endpoint_id,
        "reasoning_mode": reasoning_mode,
    }

# Nano Banana PRO direct agent endpoint
# This bypasses the chat orchestrator and sends images as structured array
NANO_BANANA_PRO_URL = "https://serverless.on-demand.io/apps/damandeep-agents/edit-image2"

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

def _submit_query_sync(api_key: str, session_id: str, endpoint_id: str, query: str, agent_ids: List[str], reasoning_mode: Optional[str]) -> Dict[str, Any]:
    url = f"{EXECUTE_URL}/sessions/{session_id}/query"
    headers = {"apikey": api_key, "Content-Type": "application/json"}
    body = {
        "endpointId": endpoint_id,
        "query": query,
        "agentIds": agent_ids,
        "responseMode": "sync",
        "reasoningMode": reasoning_mode or "gemini-3-flash",
        "modelConfigs": {},
    }
    print(f"[OnDemand Agent] Submitting query to: {url}")
    print(f"[OnDemand Agent] Query snippet: {query[:100]}...")
    resp = requests.post(url, json=body, headers=headers, timeout=260)
    print(f"[OnDemand Agent] Query response: {resp.status_code}")
    if resp.status_code != 200:
        raise RuntimeError(f"Sync query failed: {resp.status_code} {resp.text}")
    data = resp.json()
    print(f"[OnDemand Agent] RAW response: {json.dumps(data, indent=2)}")
    return data

def _call_nano_banana_pro_direct(api_key: str, image_urls: List[str], prompt: str, timeout: int = 180) -> Dict[str, Any]:
    """
    Call Nano Banana PRO agent directly with structured image_urls array.
    This mimics exactly what the web UI does after manual image upload.

    Args:
        api_key: On-Demand API key
        image_urls: List of image URLs (first=base image, others=reference/identity)
        prompt: Face swap/edit instructions
        timeout: Request timeout in seconds

    Returns:
        Dict with 'success', 'url', and optional 'response' keys
    """
    if not image_urls or len(image_urls) == 0:
        raise ValueError("Nano Banana PRO requires at least one image URL")
    
    print(f"[Nano Banana PRO Direct] Calling agent: {NANO_BANANA_PRO_URL}")
    print(f"[Nano Banana PRO Direct] Images: {len(image_urls)} URL(s)")
    print(f"[Nano Banana PRO Direct] Prompt length: {len(prompt)} chars")
    
    headers = {
        "apikey": api_key,
        "Content-Type": "application/json"
    }
    
    # Build payload with image_urls array (first=base, rest=reference/identity)
    payload = {
        "image_urls": image_urls,
        "prompt": prompt
    }
    
    print(f"[Nano Banana PRO Direct] Sending request...")
    start_time = __import__('time').time()
    resp = requests.post(NANO_BANANA_PRO_URL, json=payload, headers=headers, timeout=timeout)
    elapsed = __import__('time').time() - start_time
    
    print(f"[Nano Banana PRO Direct] Response: {resp.status_code} ({elapsed:.1f}s)")
    
    if resp.status_code != 200:
        print(f"[Nano Banana PRO Direct] Error: {resp.text[:500]}")
        raise RuntimeError(f"Nano Banana PRO failed: {resp.status_code} {resp.text[:300]}")
    
    data = resp.json()
    print(f"[Nano Banana PRO Direct] Response keys: {list(data.keys())}")
    
    # Extract image URL from response
    image_url = data.get("url") or data.get("image_url") or data.get("output_url")
    
    if not image_url and "data" in data:
        image_url = data["data"].get("url") or data["data"].get("image_url")
    
    if not image_url and "answer" in data:
        match = re.search(r"https://[^\s\"']+?\.(?:jpg|jpeg|png|webp|gif)", data["answer"], re.IGNORECASE)
        if match:
            image_url = match.group(0)
    
    if image_url:
        print(f"[Nano Banana PRO Direct] Generated image: {image_url}")
        return {"success": True, "url": image_url, "type": "image", "response": data}
    else:
        print(f"[Nano Banana PRO Direct] Warning: No image URL in response")
        print(f"[Nano Banana PRO Direct] Response: {json.dumps(data, indent=2)[:500]}")
        return {"success": False, "error": "No image URL in response", "response": data}


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
    use_direct_agent: bool = True,  # NEW: Use direct Nano Banana PRO when images provided
    **kwargs,
) -> Dict[str, Any]:
    """Generate an image using On‑Demand Agent API (sync mode).
    
    When images are provided and use_direct_agent=True:
        Calls Nano Banana PRO directly with structured image_urls array
        (mimics web UI behavior after manual image upload)
    
    When no images or use_direct_agent=False:
        Falls back to chat orchestrator with URLs in prompt text
    
    Supports single and multiple images for complex edits.
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
    file_agent_ids = creds.get("file_agent_ids", text_agent_ids)
    endpoint_id = creds["endpoint_id"]
    reasoning_mode = creds.get("reasoning_mode") or "gemini-3-flash"

    print(f"[OnDemand Agent] Starting generation for job {job_id or 'N/A'}")
    print(f"[OnDemand Agent] Endpoint: {endpoint_id}, Reasoning: {reasoning_mode}")

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

    # DECISION POINT: Use direct Nano Banana PRO or chat orchestrator?
    if all_urls and use_direct_agent:
        # Use direct Nano Banana PRO call (mimics web UI behavior)
        print(f"[OnDemand Agent] Using DIRECT Nano Banana PRO method ({len(all_urls)} image(s))")
        
        # For face swap with multiple images:
        # - First URL should be base image (scene to edit)
        # - Second URL should be face identity source
        # The prompt should reference "first image" and "second image" accordingly
        print(f"[OnDemand Agent] Image URLs: {all_urls}")
        
        result = _call_nano_banana_pro_direct(api_key_val, all_urls, prompt, timeout=180)
        
        if result.get("success"):
            return {"success": True, "url": result["url"], "type": "image"}
        else:
            # Fallback to chat orchestrator if direct method fails
            print(f"[OnDemand Agent] Direct method failed, falling back to chat orchestrator: {result.get('error')}")
            # Continue to chat orchestrator below
            all_urls = []  # Reset to force fallback
    else:
        print(f"[OnDemand Agent] Using CHAT orchestrator (text-to-image or direct disabled)")

    # FALLBACK: Chat orchestrator with URLs in prompt text
    if all_urls:
        urls_str = " ".join(all_urls)
        prompt = f"{urls_str} {prompt}"
        print(f"[OnDemand Agent] i2i mode - prepended {len(all_urls)} image URL(s) to prompt")

    # Prepare external user id – reuse job_id if available, else a new UUID
    external_user_id = job_id or str(uuid.uuid4())
    context_metadata = [{"key": "jobId", "value": external_user_id}]
    session_agent_ids = text_agent_ids

    session_id = _create_chat_session(api_key_val, external_user_id, session_agent_ids, context_metadata)
    response_payload = _submit_query_sync(
        api_key_val,
        session_id,
        endpoint_id,
        prompt,
        session_agent_ids,
        reasoning_mode,
    )
    image_url = _extract_image_url(response_payload)
    if not image_url:
        raise RuntimeError("On‑Demand agent response missing image URL")
    return {"success": True, "url": image_url, "type": "image"}
