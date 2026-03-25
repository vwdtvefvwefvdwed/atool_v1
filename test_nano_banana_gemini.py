import http.client
import json
import time

API_KEY = "00b7d1ded8mshf5f2e6fb228d268p154be9jsn8a4b3af8e4e6"
HOST = "nano-banana-pro-google-gemini-free1.p.rapidapi.com"
HEADERS = {
    'x-rapidapi-key': API_KEY,
    'x-rapidapi-host': HOST,
    'Content-Type': "application/json"
}

IMAGE_1 = "https://res.cloudinary.com/dczhbssip/image/upload/v1774069035/workflow-inputs/tcyoqeek5xk8hdutudhl.jpg"
IMAGE_2 = "https://res.cloudinary.com/dnagl4r2t/image/upload/v1771751129/avatar002_xvbg7s.png"
PROMPT = "Use Image B as an immutable base with full composition lock; perform only a precise face swap on the background male warrior using Image A as identity reference."

def submit_job(images):
    conn = http.client.HTTPSConnection(HOST)
    payload = json.dumps({
        "prompt": PROMPT,
        "images": images,
    })
    conn.request("POST", "/create-v9", payload, HEADERS)
    res = conn.getresponse()
    body = res.read().decode("utf-8")
    print(f"  Submit STATUS: {res.status}  BODY: {body}")
    if res.status != 200:
        return None
    data = json.loads(body)
    return data.get("jobId")

def poll_job(job_id, label):
    print(f"\n  Polling job: {job_id}")
    for i in range(40):
        time.sleep(5)
        conn = http.client.HTTPSConnection(HOST)
        conn.request("GET", f"/create-v9/job-status?jobId={job_id}", headers=HEADERS)
        res = conn.getresponse()
        body = res.read().decode("utf-8")
        if res.status != 200:
            print(f"  Poll {i+1}: HTTP {res.status} - {body}")
            return None
        data = json.loads(body)
        status = data.get("status", "")
        progress = data.get("progress", 0)
        print(f"  Poll {i+1} ({(i+1)*5}s): status={status}, progress={progress}%")
        if status in ("completed",):
            print(f"\n  [{label}] SUCCESS! Full response: {json.dumps(data, indent=2)}")
            return data
        if status in ("failed", "error"):
            print(f"\n  [{label}] FAILED! Full response: {json.dumps(data, indent=2)}")
            return None
    print(f"\n  [{label}] TIMEOUT after 200s")
    return None


print("=" * 60)
print("TEST 1: Single image reference")
print("=" * 60)
job_id = submit_job([IMAGE_1])
if job_id:
    poll_job(job_id, "Single Image")

print()
print("=" * 60)
print("TEST 2: Two image references (face swap use case)")
print("=" * 60)
job_id2 = submit_job([IMAGE_1, IMAGE_2])
if job_id2:
    poll_job(job_id2, "Two Images")
