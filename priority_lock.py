"""
Priority Lock Manager
Enable/disable priority lock mode via the backend public URL.
When enabled, job_worker_realtime.py will only process Priority 1 jobs.
Priority 2 and 3 jobs stay pending until lock is disabled.

Usage:
    SECRET_KEY=your-secret-key BACKEND_URL=https://your-app.com python priority_lock.py enable
    SECRET_KEY=your-secret-key BACKEND_URL=https://your-app.com python priority_lock.py disable

Or set in .env and run:
    python priority_lock.py enable
    python priority_lock.py disable
"""

import os
import sys
import requests
from dotenv_vault import load_dotenv

load_dotenv()


def toggle_priority_lock(enable=True):
    backend_url = os.getenv("BACKEND_URL", "http://localhost:5000")
    secret_key = os.getenv("SECRET_KEY")

    if not secret_key:
        print("❌ ERROR: SECRET_KEY not set in environment")
        print("Run with: SECRET_KEY=your-secret-key python priority_lock.py [enable|disable]")
        return False

    url = f"{backend_url}/admin/priority-lock"

    print("=" * 60)
    print(f"PRIORITY LOCK - {'ENABLE' if enable else 'DISABLE'}")
    print("=" * 60)
    print(f"Target: {backend_url}")

    if enable:
        print("\nThis will:")
        print("  ✓ Block Priority 2 and 3 jobs from being processed")
        print("  ✓ Keep Priority 1 jobs running normally")
        print("  ✓ P2/P3 jobs stay pending - no data loss")
        print("  ✓ Persists across worker restarts (stored in Supabase)")
    else:
        print("\nThis will:")
        print("  ✓ Resume processing all priority jobs")
        print("  ✓ Worker auto-flushes all pending P2/P3 jobs immediately")

    print("=" * 60)

    try:
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {secret_key}",
                "Content-Type": "application/json"
            },
            json={"enable": enable},
            timeout=15
        )

        if response.status_code == 200:
            data = response.json()
            print("\n✅ SUCCESS")
            print(f"Message: {data.get('message')}")
            print(f"Mode: {data.get('mode')}")
            if not enable:
                print("\nℹ️  Worker will now flush all pending P2/P3 jobs automatically via Realtime.")
            return True
        elif response.status_code == 401:
            print("\n❌ UNAUTHORIZED: Check your SECRET_KEY")
            return False
        elif response.status_code == 403:
            print("\n❌ FORBIDDEN: Invalid SECRET_KEY")
            return False
        elif response.status_code == 500:
            print("\n❌ SERVER ERROR: SECRET_KEY may not be configured on backend")
            print(f"Response: {response.text}")
            return False
        else:
            print(f"\n❌ FAILED: HTTP {response.status_code}")
            print(f"Response: {response.text}")
            return False

    except requests.exceptions.ConnectionError:
        print(f"\n❌ ERROR: Cannot connect to {backend_url}")
        print("Check that BACKEND_URL is correct and the service is running")
        return False
    except requests.exceptions.Timeout:
        print(f"\n❌ ERROR: Request timed out connecting to {backend_url}")
        return False
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        return False


if __name__ == "__main__":
    if len(sys.argv) > 1:
        action = sys.argv[1].lower()
        if action in ["enable", "on", "1", "true"]:
            toggle_priority_lock(enable=True)
        elif action in ["disable", "off", "0", "false"]:
            toggle_priority_lock(enable=False)
        else:
            print("Usage: python priority_lock.py [enable|disable]")
            print("  enable  - Only process Priority 1 jobs (hold P2/P3)")
            print("  disable - Resume all priorities (auto-flush pending P2/P3)")
            sys.exit(1)
    else:
        print("Usage: python priority_lock.py [enable|disable]")
        print("  enable  - Only process Priority 1 jobs (hold P2/P3)")
        print("  disable - Resume all priorities (auto-flush pending P2/P3)")
        sys.exit(1)
