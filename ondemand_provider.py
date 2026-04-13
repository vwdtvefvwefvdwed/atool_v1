'''
On-Demand API Provider with Polling and Webhook Support
=========================================================

This module implements the integration for the On-Demand API (https://api.on-demand.io).
It uses an API key and a workflow ID stored as JSON in the `provider_api_keys` table.

Two modes:
1. Webhook mode (preferred): Execute workflow with webhook URL, poll only for status.
   Result is delivered via POST to webhook endpoint.
2. Polling mode (fallback): Poll for results every 2 seconds (max 60 retries = 2 min timeout).

Raw JSON responses are logged for debugging.
'''

import os
import json
import time
import requests
from typing import Dict, Any, Optional

EXECUTE_URL_TEMPLATE = "https://api.on-demand.io/automation/api/workflow/{workflow_id}/execute"
WEBHOOK_EXECUTE_URL_TEMPLATE = "https://gateway.on-demand.io/automation/public/v1/webhook/workflow/{workflow_id}/execute"
RESULT_URL = "https://api.on-demand.io/automation/api/execution/"

MAX_RETRIES = 60
RETRY_INTERVAL = 2
USE_WEBHOOK_MODE = os.getenv("ONDEMAND_USE_POLLING", "false").lower() != "true"

_EXECUTION_JOB_MAP: Dict[str, str] = {}


def store_execution_mapping(execution_id: str, job_id: str):
    _EXECUTION_JOB_MAP[execution_id] = job_id


def get_execution_id(execution_id: str) -> Optional[str]:
    return _EXECUTION_JOB_MAP.get(execution_id)


def _parse_ondemand_credentials(raw: str) -> Dict[str, str]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON for On-Demand credentials: {exc}")
    if not isinstance(data, dict):
        raise ValueError("On-Demand credentials must be a JSON object")
    api_key = data.get("api_key")
    workflow_id = data.get("workflow_id")
    if not api_key or not workflow_id:
        raise ValueError("On-Demand credentials must contain 'api_key' and 'workflow_id'")
    return {"api_key": api_key, "workflow_id": workflow_id}


def generate_with_ondemand(
    prompt: str,
    model: str,
    aspect_ratio: str = "1:1",
    api_key: str = "",
    input_image_url: Optional[str] = None,
    job_type: str = "image",
    duration: int = 5,
    provider_key: str = "vision-ondemand",
    job_id: Optional[str] = None,
    webhook_url: Optional[str] = None,
) -> Dict[str, Any]:
    from provider_api_keys import get_provider_api_key

    cred_record = get_provider_api_key(provider_key)
    if not cred_record:
        raise RuntimeError(f"No credentials found for provider '{provider_key}'")

    raw = cred_record.get("api_key")
    if not raw:
        raise RuntimeError(f"Credential record for '{provider_key}' missing 'api_key' field")

    creds = _parse_ondemand_credentials(raw)
    api_key_val = creds["api_key"]
    workflow_id = creds["workflow_id"]

    execute_url_template = WEBHOOK_EXECUTE_URL_TEMPLATE if USE_WEBHOOK_MODE else EXECUTE_URL_TEMPLATE
    execute_url = execute_url_template.format(workflow_id=workflow_id)
    headers = {"apikey": api_key_val, "Content-Type": "application/json"}
    payload = {"input": prompt}
    if job_id:
        payload["job_id"] = job_id
    
    print(f"[OnDemand] Mode: {'WEBHOOK' if USE_WEBHOOK_MODE else 'POLLING'}")
    print(f"[OnDemand] Execute URL: {execute_url}")

    print(f"[OnDemand] Executing workflow {workflow_id} for job {job_id or 'N/A'}")
    print(f"[OnDemand] Prompt: {prompt[:100]}...")
    
    try:
        resp = requests.post(execute_url, headers=headers, json=payload, timeout=30)
        print(f"[OnDemand] Execute response status: {resp.status_code}")
        
        if resp.status_code != 200:
            raise RuntimeError(f"On-Demand execute error {resp.status_code}: {resp.text}")
        
        data = resp.json()
        print(f"[OnDemand] RAW Execute response JSON: {json.dumps(data, indent=2)}")
        
        execution_id = data.get("executionID")
        if not execution_id:
            raise RuntimeError("On-Demand response missing 'executionID'")
        
        print(f"[OnDemand] Execution ID: {execution_id}")
        if job_id:
            store_execution_mapping(execution_id, job_id)
            print(f"[OnDemand] Stored mapping: execution_id={execution_id} -> job_id={job_id}")
        
        if USE_WEBHOOK_MODE:
            print(f"[OnDemand] Webhook mode: result will be delivered to /ondemand/webhook endpoint")
            return {
                "success": True,
                "status": "queued",
                "execution_id": execution_id
            }
        
        print(f"[OnDemand] Starting polling (max {MAX_RETRIES} retries, {RETRY_INTERVAL}s interval)...")
        
        count = 0
        while count < MAX_RETRIES:
            count += 1
            
            try:
                result_resp = requests.get(
                    RESULT_URL + execution_id,
                    headers={"apikey": api_key_val},
                    timeout=30
                )
            except Exception as e:
                print(f"[OnDemand] Fetch error on attempt {count}: {e}")
                time.sleep(RETRY_INTERVAL)
                continue

            if result_resp.status_code != 200:
                print(f"[OnDemand] Attempt {count}: Status {result_resp.status_code}")
                time.sleep(RETRY_INTERVAL)
                continue

            try:
                result_data = result_resp.json()
                print(f"[OnDemand] RAW Poll response (attempt {count}): {json.dumps(result_data, indent=2)}")
            except Exception as json_err:
                print(f"[OnDemand] Attempt {count}: Non-JSON response, raw text: {result_resp.text[:500]}")
                time.sleep(RETRY_INTERVAL)
                continue

            status = (
                result_data.get("status") or
                result_data.get("state") or
                result_data.get("data", {}).get("status")
            )
            
            print(f"[OnDemand] Attempt {count}/{MAX_RETRIES}: status='{status}'")

            if status in ["success", "completed"]:
                print(f"[OnDemand] Generation completed!")
                
                output_data = result_data.get("data", {})
                print(f"[OnDemand] Output data: {json.dumps(output_data, indent=2)}")
                
                image_url = output_data.get("image_url")
                text_output = output_data.get("output")
                
                if image_url:
                    print(f"[OnDemand] Image URL: {image_url}")
                    return {
                        "success": True,
                        "url": image_url,
                        "type": "image",
                    }
                elif text_output:
                    print(f"[OnDemand] Text output: {text_output[:200]}")
                    return {
                        "success": True,
                        "url": text_output,
                        "type": "image",
                    }
                else:
                    raise RuntimeError(f"On-Demand success but no image_url or output in response: {output_data}")

            elif status in ["failed", "error"]:
                error_msg = result_data.get("error") or result_data.get("message") or result_data.get("data", {}).get("error", "Unknown error")
                raise RuntimeError(f"On-Demand generation failed: {error_msg}")
            
            elif status in ["executing", "running", "pending", "queued"]:
                print(f"[OnDemand] Status '{status}' - waiting...")
            
            else:
                print(f"[OnDemand] Unknown status '{status}' - waiting...")

            time.sleep(RETRY_INTERVAL)

        raise RuntimeError(f"On-Demand timeout: Workflow did not complete after {MAX_RETRIES * RETRY_INTERVAL} seconds")

    except Exception as exc:
        print(f"[OnDemand] Error: {exc}")
        raise RuntimeError(f"On-Demand generation failed: {exc}")