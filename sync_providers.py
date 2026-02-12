"""
Provider Sync Script
Synchronizes providers configuration with the database.

Usage:
    python sync_providers.py              # Sync all providers
    python sync_providers.py --dry-run    # Preview changes without applying
    python sync_providers.py --list       # List current providers in DB
"""

import sys
import argparse
from datetime import datetime
from supabase_client import supabase

PROVIDERS_CONFIG = [
    {"key": "flux_schnell", "name": "Flux Schnell", "type": "image"},
    {"key": "flux_dev", "name": "Flux Dev", "type": "image"},
    {"key": "sdxl_turbo", "name": "SDXL Turbo", "type": "image"},
    {"key": "stable_diffusion_3", "name": "Stable Diffusion 3", "type": "image"},
    {"key": "runway_gen3", "name": "Runway Gen-3", "type": "video"},
    {"key": "kling_ai", "name": "Kling AI", "type": "video"},
]

def get_existing_providers():
    result = supabase.table("providers").select("*").execute()
    return {p["provider_key"]: p for p in result.data}

def list_providers():
    providers = get_existing_providers()
    if not providers:
        print("No providers found in database.")
        return
    
    print(f"\n{'Provider Key':<25} {'Name':<25} {'Type':<10} {'Active':<8}")
    print("-" * 70)
    for key, p in sorted(providers.items()):
        status = "Yes" if p["is_active"] else "No"
        print(f"{p['provider_key']:<25} {p['provider_name']:<25} {p['provider_type']:<10} {status:<8}")
    print(f"\nTotal: {len(providers)} providers")

def sync_providers(dry_run=False):
    print(f"\n{'[DRY RUN] ' if dry_run else ''}Syncing providers...")
    print(f"Timestamp: {datetime.now().isoformat()}\n")
    
    existing = get_existing_providers()
    existing_keys = set(existing.keys())
    config_keys = {p["key"] for p in PROVIDERS_CONFIG}
    
    added = []
    updated = []
    deactivated = []
    reactivated = []
    
    for provider in PROVIDERS_CONFIG:
        key = provider["key"]
        
        if key not in existing_keys:
            added.append(provider)
            if not dry_run:
                supabase.table("providers").insert({
                    "provider_key": key,
                    "provider_name": provider["name"],
                    "provider_type": provider["type"],
                    "is_active": True
                }).execute()
        else:
            current = existing[key]
            needs_update = (
                current["provider_name"] != provider["name"] or
                current["provider_type"] != provider["type"]
            )
            
            if needs_update:
                updated.append(provider)
                if not dry_run:
                    supabase.table("providers").update({
                        "provider_name": provider["name"],
                        "provider_type": provider["type"]
                    }).eq("provider_key", key).execute()
            
            if not current["is_active"]:
                reactivated.append(key)
                if not dry_run:
                    supabase.table("providers").update({
                        "is_active": True
                    }).eq("provider_key", key).execute()
    
    for key in existing_keys - config_keys:
        if existing[key]["is_active"]:
            deactivated.append(key)
            if not dry_run:
                supabase.table("providers").update({
                    "is_active": False
                }).eq("provider_key", key).execute()
    
    print("=== Sync Results ===\n")
    
    if added:
        print(f"ADDED ({len(added)}):")
        for p in added:
            print(f"  + {p['key']} ({p['name']}, {p['type']})")
        print()
    
    if updated:
        print(f"UPDATED ({len(updated)}):")
        for p in updated:
            print(f"  ~ {p['key']} ({p['name']}, {p['type']})")
        print()
    
    if reactivated:
        print(f"REACTIVATED ({len(reactivated)}):")
        for key in reactivated:
            print(f"  * {key}")
        print()
    
    if deactivated:
        print(f"DEACTIVATED ({len(deactivated)}):")
        for key in deactivated:
            print(f"  - {key}")
        print()
    
    if not any([added, updated, reactivated, deactivated]):
        print("No changes needed. All providers are in sync.")
    
    print(f"\nSync {'preview ' if dry_run else ''}complete.")

def main():
    parser = argparse.ArgumentParser(description="Sync providers with database")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    parser.add_argument("--list", action="store_true", help="List current providers in DB")
    args = parser.parse_args()
    
    if args.list:
        list_providers()
    else:
        sync_providers(dry_run=args.dry_run)

if __name__ == "__main__":
    main()
