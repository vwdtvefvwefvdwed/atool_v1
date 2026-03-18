"""
Diagnose Supabase Realtime WebSocket Issues (Main Account)
Run: python diagnose_realtime.py
"""
import os
import sys
import asyncio
import time
import httpx
import websockets
from dotenv_vault import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("[FAIL] SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not found in env")
    sys.exit(1)

PROJECT_REF = SUPABASE_URL.replace("https://", "").split(".")[0]
WS_URL = f"wss://{PROJECT_REF}.supabase.co/realtime/v1/websocket?apikey={SUPABASE_KEY}&vsn=1.0.0"
REST_URL = f"{SUPABASE_URL}/rest/v1/"
HEALTH_URL = f"{SUPABASE_URL}/rest/v1/"

SEP = "=" * 65


def section(title):
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)


# ─── 1. REST API health ────────────────────────────────────────────
section("1. REST API Health Check")
try:
    r = httpx.get(HEALTH_URL, timeout=10, headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"})
    if r.status_code == 200:
        print(f"[OK]   REST API is reachable (HTTP {r.status_code})")
    else:
        print(f"[WARN] REST API returned HTTP {r.status_code}")
        print(f"       Body: {r.text[:200]}")
except Exception as e:
    print(f"[FAIL] REST API unreachable: {e}")


# ─── 2. Realtime endpoint reachable ───────────────────────────────
section("2. Realtime Endpoint Reachability")
REALTIME_HTTP = f"https://{PROJECT_REF}.supabase.co/realtime/v1/api"
try:
    r = httpx.get(REALTIME_HTTP, timeout=10, headers={"apikey": SUPABASE_KEY})
    print(f"[OK]   Realtime HTTP endpoint responded: HTTP {r.status_code}")
except Exception as e:
    print(f"[INFO] Realtime HTTP probe result: {e}")


# ─── 3. WebSocket connect & stay alive ────────────────────────────
section("3. WebSocket Connection Test (10 second hold)")

async def test_websocket():
    print(f"       Connecting to: wss://{PROJECT_REF}.supabase.co/realtime/v1/websocket")
    connect_time = None
    disconnect_reason = None
    messages_received = 0
    heartbeats_sent = 0

    try:
        async with websockets.connect(
            WS_URL,
            ping_interval=None,
            open_timeout=10,
            close_timeout=5,
        ) as ws:
            connect_time = time.time()
            print(f"[OK]   WebSocket CONNECTED")

            # Subscribe to jobs channel
            await ws.send('{"topic":"realtime:public:jobs","event":"phx_join","payload":{},"ref":"1"}')
            print(f"[OK]   Sent channel join for realtime:public:jobs")

            # Hold for 10 seconds, send heartbeats every 3s
            deadline = time.time() + 10
            while time.time() < deadline:
                remaining = deadline - time.time()
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=min(3.0, remaining))
                    messages_received += 1
                    import json
                    try:
                        parsed = json.loads(msg)
                        event = parsed.get("event", "?")
                        topic = parsed.get("topic", "?")
                        status = parsed.get("payload", {}).get("status", "")
                        print(f"       MSG [{messages_received}] event={event} topic={topic} status={status}")
                    except Exception:
                        print(f"       MSG [{messages_received}] raw: {msg[:120]}")
                except asyncio.TimeoutError:
                    # Send heartbeat
                    heartbeats_sent += 1
                    await ws.send('{"topic":"phoenix","event":"heartbeat","payload":{},"ref":null}')
                    print(f"       HEARTBEAT [{heartbeats_sent}] sent — connection still alive ✓")

            duration = time.time() - connect_time
            print(f"\n[OK]   Connection held for {duration:.1f}s")
            print(f"       Messages received : {messages_received}")
            print(f"       Heartbeats sent   : {heartbeats_sent}")
            print(f"\n[RESULT] Realtime WebSocket is HEALTHY")

    except websockets.exceptions.ConnectionClosedError as e:
        duration = (time.time() - connect_time) if connect_time else 0
        print(f"[FAIL] Connection CLOSED after {duration:.1f}s")
        print(f"       Code   : {e.code}")
        print(f"       Reason : {e.reason}")
        _diagnose_close_code(e.code, e.reason)

    except websockets.exceptions.RejectHandshake as e:
        print(f"[FAIL] WebSocket handshake rejected: HTTP {e.status_code}")
        _diagnose_http_rejection(e.status_code)
    except Exception as e:
        if "status code" in str(e).lower() or "rejected" in str(e).lower():
            import re
            m = re.search(r"(\d{3})", str(e))
            code = int(m.group(1)) if m else 0
            print(f"[FAIL] WebSocket handshake rejected: HTTP {code}")
            _diagnose_http_rejection(code)
        else:
            print(f"[FAIL] Unexpected error: {type(e).__name__}: {e}")


def _diagnose_close_code(code, reason):
    print(f"\n[DIAGNOSIS]")
    if code == 1001:
        print("  Code 1001 = 'Going Away'")
        print("  The Supabase Realtime SERVER closed the connection.")
        print("  Common causes:")
        print("    • Realtime service is restarting / redeploying on Supabase side")
        print("    • Your project is being paused (free-tier idle pause)")
        print("    • Realtime connection limit reached for your plan")
        print("    • Supabase infrastructure incident")
        print("  Actions:")
        print("    • Check https://status.supabase.com for incidents")
        print("    • Check Supabase Dashboard → Settings → Infrastructure")
        print("    • Check if project is paused (free tier pauses after 1 week)")
        print("    • Review Realtime connection count in dashboard metrics")
    elif code == 1006:
        print("  Code 1006 = Abnormal closure (network level drop)")
        print("  Check your network / firewall / proxy settings")
    elif code == 4001 or code == 4002:
        print("  Code 4001/4002 = Authentication error")
        print("  Your SUPABASE_SERVICE_ROLE_KEY may be invalid or expired")
    elif code == 4004:
        print("  Code 4004 = Too many connections")
        print("  Upgrade your Supabase plan or reduce open connections in app.py")
    else:
        print(f"  Unknown close code {code} — check Supabase docs or status page")


def _diagnose_http_rejection(status):
    print(f"\n[DIAGNOSIS]")
    if status == 401:
        print("  HTTP 401 = API key invalid. Check SUPABASE_SERVICE_ROLE_KEY")
    elif status == 429:
        print("  HTTP 429 = Rate limited. Too many connection attempts")
    elif status == 503:
        print("  HTTP 503 = Supabase Realtime service is down")
    else:
        print(f"  HTTP {status} — check Supabase status page")


asyncio.run(test_websocket())


# ─── 4. Summary ───────────────────────────────────────────────────
section("4. Quick Reference")
print(f"  Project ref   : {PROJECT_REF}")
print(f"  Supabase URL  : {SUPABASE_URL}")
print(f"  Status page   : https://status.supabase.com")
print(f"  Dashboard     : https://supabase.com/dashboard/project/{PROJECT_REF}")
print(f"  Realtime logs : https://supabase.com/dashboard/project/{PROJECT_REF}/logs/realtime-logs")
print(SEP)
