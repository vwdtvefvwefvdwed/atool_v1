#!/usr/bin/env python3
"""
Test if .env is being loaded correctly (after migrating off dotenv-vault).
Run this on your deployment to verify environment variables.
"""

import os
from pathlib import Path
from envvault import load_env

print("=" * 60)
print("DOTENV ENV LOADING TEST")
print("=" * 60)

# Locate .env (local dev). On Render, env vars come from the platform directly.
backend_dir = Path(__file__).resolve().parent.parent
env_path = backend_dir / ".env"
print(f"\n1. .env file: {'✅ EXISTS' if env_path.exists() else '❌ NOT FOUND'}")
print(f"   Path: {env_path}")
if env_path.exists():
    print(f"   (On Render, ignore this; secrets come from Render env vars)")

# Try to load
print(f"\n2. Loading environment...")
try:
    if env_path.exists():
        load_env(fallback_dotenv=env_path)
        print(f"   ✅ load_env() completed")
    else:
        print(f"   ⚠️  No .env file, relying on system env vars")
except Exception as e:
    print(f"   ❌ Error: {e}")

# Check Cloudinary accounts
print(f"\n3. Cloudinary Accounts Found:")
print("=" * 60)

accounts_found = []

# Check Primary
if os.getenv("CLOUDINARY_CLOUD_NAME"):
    accounts_found.append({
        "name": "Primary",
        "cloud": os.getenv("CLOUDINARY_CLOUD_NAME"),
        "key": os.getenv("CLOUDINARY_API_KEY", "")[:4] + "..." + os.getenv("CLOUDINARY_API_KEY", "")[-4:]
    })

# Check Account 2
if os.getenv("CLOUDINARY_CLOUD_NAME_2"):
    accounts_found.append({
        "name": "Account 2",
        "cloud": os.getenv("CLOUDINARY_CLOUD_NAME_2"),
        "key": os.getenv("CLOUDINARY_API_KEY_2", "")[:4] + "..." + os.getenv("CLOUDINARY_API_KEY_2", "")[-4:]
    })

# Check Account 3
if os.getenv("CLOUDINARY_CLOUD_NAME_3"):
    accounts_found.append({
        "name": "Account 3",
        "cloud": os.getenv("CLOUDINARY_CLOUD_NAME_3"),
        "key": os.getenv("CLOUDINARY_API_KEY_3", "")[:4] + "..." + os.getenv("CLOUDINARY_API_KEY_3", "")[-4:]
    })

if accounts_found:
    for i, acc in enumerate(accounts_found, 1):
        print(f"  {i}. {acc['name']} | cloud: {acc['cloud']} | key: {acc['key']}")
    print(f"\n✅ Found {len(accounts_found)} account(s)")
else:
    print("  ❌ NO ACCOUNTS FOUND!")

print("\n" + "=" * 60)
print("DIAGNOSIS:")
print("=" * 60)

if not env_path.exists() and not os.getenv("CLOUDINARY_CLOUD_NAME"):
    print("❌ No .env file and no CLOUDINARY_CLOUD_NAME in system env")
    print("   → Local dev: create backend/.env with your secrets")
    print("   → Render:    set env vars in Render Dashboard (Environment tab)")
elif len(accounts_found) < 3:
    print("⚠️  Env loaded but missing accounts")
    print(f"   → Expected 3 accounts, found {len(accounts_found)}")
    print("   → Check your .env / Render env vars contain CLOUDINARY_*_2 and _3")
else:
    print("✅ Everything is working correctly!")
    print(f"   → {len(accounts_found)} Cloudinary accounts loaded")

print("=" * 60)
