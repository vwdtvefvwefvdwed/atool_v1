#!/usr/bin/env python3
"""
Test if .env.vault is being loaded correctly
Run this on your deployment to verify environment variables
"""

import os
from pathlib import Path
from dotenv_vault import load_dotenv

print("=" * 60)
print("DOTENV VAULT LOADING TEST")
print("=" * 60)

# Check if DOTENV_KEY is set
dotenv_key = os.getenv("DOTENV_KEY")
print(f"\n1. DOTENV_KEY: {'✅ SET' if dotenv_key else '❌ NOT SET'}")
if dotenv_key:
    print(f"   Value: {dotenv_key[:30]}...{dotenv_key[-20:]}")

# Check if .env.vault exists
script_dir = Path(__file__).parent
vault_path = script_dir / ".env.vault"
print(f"\n2. .env.vault file: {'✅ EXISTS' if vault_path.exists() else '❌ NOT FOUND'}")
print(f"   Path: {vault_path}")

# Try to load
print(f"\n3. Loading environment...")
try:
    if vault_path.exists():
        load_dotenv(vault_path)
        print(f"   ✅ load_dotenv() completed")
    else:
        print(f"   ⚠️  No vault file, loading from system")
        load_dotenv()
except Exception as e:
    print(f"   ❌ Error: {e}")

# Check Cloudinary accounts
print(f"\n4. Cloudinary Accounts Found:")
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

if not dotenv_key:
    print("❌ DOTENV_KEY is not set in environment variables")
    print("   → Add DOTENV_KEY to your deployment's env vars")
    print("   → Value: dotenv://:key_995ea79bdfe62b1000e4bcc6864de2e1689448c7091725f71b49fdef97c9b03a@dotenv.org/vault/.env.vault?environment=production")
elif not vault_path.exists():
    print("❌ .env.vault file not found")
    print("   → Ensure .env.vault is committed to git")
    print("   → Check if .gitignore allows !.env.vault")
elif len(accounts_found) < 3:
    print("⚠️  Vault loaded but missing accounts")
    print(f"   → Expected 3 accounts, found {len(accounts_found)}")
    print("   → Check if vault is encrypted with correct credentials")
else:
    print("✅ Everything is working correctly!")
    print(f"   → {len(accounts_found)} Cloudinary accounts loaded")

print("=" * 60)
