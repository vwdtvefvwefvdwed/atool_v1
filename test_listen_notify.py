"""
Test script for PostgreSQL LISTEN/NOTIFY.
Usage:
    1. Set DATABASE_URL in .env or as environment variable
    2. Run: python test_listen_notify.py
    3. In Supabase SQL Editor, run:
       NOTIFY job_events, '{"job_id":"test-001","status":"pending","job_type":"image","model":"test","user_id":"test","prompt":"test"}';
    4. Check this script's output for received notification
"""

import asyncio
import os
import sys

from dotenv_vault import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    print("❌ DATABASE_URL is not set!")
    print("   Add it to your .env file or set as environment variable.")
    sys.exit(1)

# Mask password in output
masked_url = DATABASE_URL.split(":")[0] + "://****:****@" + DATABASE_URL.split("@")[1] if "@" in DATABASE_URL else "***"
print(f"DATABASE_URL: {masked_url}")
print(f"Port: {DATABASE_URL.split(':')[-2].split('/')[-1] if ':' in DATABASE_URL else 'unknown'}")
print()


async def test_listen():
    import asyncpg

    print(f"[1/3] Connecting to PostgreSQL...")
    try:
        conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)
        print(f"[1/3] ✅ Connected successfully")
    except Exception as e:
        print(f"[1/3] ❌ Connection failed: {e}")
        sys.exit(1)

    print(f"[2/3] Registering listener on 'job_events' channel...")

    received = asyncio.Event()

    def handle_notify(connection, pid, channel, payload):
        print(f"\n{'='*60}")
        print(f"✅ NOTIFICATION RECEIVED!")
        print(f"Channel: {channel}")
        print(f"Payload: {payload}")
        print(f"{'='*60}")
        received.set()

    try:
        await conn.add_listener('job_events', handle_notify)
        print(f"[2/3] ✅ Listener registered")
    except Exception as e:
        print(f"[2/3] ❌ Failed to register listener: {e}")
        await conn.close()
        sys.exit(1)

    print(f"[3/3] Waiting for notifications...")
    print()
    print("Now go to Supabase SQL Editor and run:")
    print("NOTIFY job_events, '{\"job_id\":\"test-001\",\"status\":\"pending\",\"job_type\":\"image\",\"model\":\"test\",\"user_id\":\"test\",\"prompt\":\"test\"}';")
    print()
    print("This script will exit after receiving a notification or after 60 seconds.")
    print()

    try:
        await asyncio.wait_for(received.wait(), timeout=60)
        print("\n✅ Test PASSED — LISTEN/NOTIFY is working!")
    except asyncio.TimeoutError:
        print("\n❌ Test FAILED — No notification received within 60 seconds.")
        print("\nTroubleshooting:")
        print("  1. Check DATABASE_URL port — must be 5432 (Session mode), NOT 6543 (Transaction mode)")
        print("  2. Verify the SQL command was run exactly as shown above")
        print("  3. Check if pgbouncer is stripping LISTEN commands")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(test_listen())
