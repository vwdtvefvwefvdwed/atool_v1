import io, json, re, sys, uuid
import requests
from PIL import Image

CHAT_URL = "https://api.on-demand.io/chat/v1"
AGENT_IDS = ["agent-1776826082"]
ENDPOINT_ID = "predefined-gemini-3.5-flash"
REASONING_MODE = "gemini-3-flash"
RATIOS = {"16:9": 16/9, "9:16": 9/16, "1:1": 1.0}
URL_RE = re.compile(r"https://[^\s\"']+?\.(?:jpg|jpeg|png|webp|gif)(?:\?[^\s\"']*)?", re.I)

def create_session(api_key):
    resp = requests.post(f"{CHAT_URL}/sessions",
        json={"agentIds": AGENT_IDS, "externalUserId": str(uuid.uuid4()),
              "contextMetadata": [{"key": "jobId", "value": "ratio-test"}]},
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

def main():
    api_key = sys.argv[1]
    results = {}
    for ratio, expected in RATIOS.items():
        print(f"\n=== ratio {ratio} ===", flush=True)
        try:
            sid = create_session(api_key)
            hint = "" if ratio in ("1:1", "auto") else f" The image MUST have a {ratio} aspect ratio."
            query = ("Generate a single high-quality image from this description and "
                     "return ONLY the direct image URL, on its own line surrounded "
                     "by whitespace - no markdown, no punctuation and no other text "
                     "touching the URL:\nA red vintage bicycle leaning on a brick wall" + hint)
            answer = stream_query(api_key, sid, query)
            m = URL_RE.search(answer)
            if not m:
                print(f"  NO URL in answer: {answer[:300]}")
                results[ratio] = ("no-url", None); continue
            url = m.group(0)
            print(f"  url: {url}")
            img = Image.open(io.BytesIO(requests.get(url, timeout=60).content))
            w, h = img.size
            actual = w / h
            ok = abs(actual - expected) / expected < 0.12
            print(f"  size: {w}x{h}  actual={actual:.3f} expected={expected:.3f} -> {'PASS' if ok else 'FAIL'}")
            results[ratio] = ("PASS" if ok else "FAIL", f"{w}x{h}")
        except Exception as e:
            print(f"  ERROR: {e}")
            results[ratio] = ("error", str(e)[:200])
    print("\n===== SUMMARY =====")
    for r, (status, detail) in results.items():
        print(f"  {r}: {status} ({detail})")

if __name__ == "__main__":
    main()
