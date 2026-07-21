import io, json, re, sys, uuid
import requests
from PIL import Image

CHAT_URL = "https://api.on-demand.io/chat/v1"
AGENT_IDS = ["agent-1776826082"]
ENDPOINT_ID = "predefined-gemini-3.5-flash"
REASONING_MODE = "gemini-3-flash"
URL_RE = re.compile(r"https://[^\s\"']+?\.(?:jpg|jpeg|png|webp|gif)(?:\?[^\s\"']*)?", re.I)

def create_session(api_key):
    resp = requests.post(f"{CHAT_URL}/sessions",
        json={"agentIds": AGENT_IDS, "externalUserId": str(uuid.uuid4()),
              "contextMetadata": [{"key": "jobId", "value": "ratio-test2"}]},
        headers={"apikey": api_key, "Content-Type": "application/json"}, timeout=30)
    if resp.status_code != 201:
        raise RuntimeError(f"session {resp.status_code}: {resp.text[:300]}")
    return resp.json()["data"]["id"]

def stream_query(api_key, session_id, query, timeout=240):
    resp = requests.post(f"{CHAT_URL}/sessions/{session_id}/query",
        json={"endpointId": ENDPOINT_ID, "query": query, "agentIds": AGENT_IDS,
              "responseMode": "stream", "reasoningMode": REASONING_MODE, "modelConfigs": {}},
        headers={"apikey": api_key, "Content-Type": "application/json"}, timeout=timeout, stream=True)
    if resp.status_code != 200:
        raise RuntimeError(f"query {resp.status_code}: {resp.text[:300]}")
    answer = ""
    for raw in resp.iter_lines():
        if not raw: continue
        line = raw.decode("utf-8", "ignore").strip()
        if not line.startswith("data:"): continue
        data = line[5:].strip()
        if data == "[DONE]": break
        try: ev = json.loads(data)
        except ValueError: continue
        if ev.get("eventType") == "fulfillment":
            answer += ev.get("answer") or ""
    resp.close()
    return answer

def run(api_key, label, hint, expected):
    print(f"\n=== {label} ===", flush=True)
    try:
        sid = create_session(api_key)
        query = ("Generate a single high-quality image from this description and "
                 "return ONLY the direct image URL, on its own line surrounded "
                 "by whitespace - no markdown, no punctuation and no other text "
                 "touching the URL:\nA red vintage bicycle leaning on a brick wall" + hint)
        answer = stream_query(api_key, sid, query)
        m = URL_RE.search(answer)
        if not m:
            print(f"  NO URL: {answer[:300]}"); return
        img = Image.open(io.BytesIO(requests.get(m.group(0), timeout=60).content))
        w, h = img.size
        actual = w / h
        ok = abs(actual - expected) / expected < 0.12
        print(f"  size: {w}x{h}  actual={actual:.3f} expected={expected:.3f} -> {'PASS' if ok else 'FAIL'}")
    except Exception as e:
        print(f"  ERROR: {e}")

api_key = sys.argv[1]
run(api_key, "9:16 strong hint",
    " CRITICAL REQUIREMENT: the generated image MUST be a vertical portrait with an EXACT 9:16 aspect ratio "
    "(width:height = 9:16, like 1080x1920). Pass aspect_ratio=9:16 to the image tool if supported.", 9/16)
run(api_key, "3:2 hint", " The image MUST have a 3:2 aspect ratio (landscape, width:height = 3:2).", 3/2)
