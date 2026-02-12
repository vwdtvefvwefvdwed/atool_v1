"""
Provider API Keys Manager
Simple management for provider API keys.

Usage:
    python manage_provider_keys.py --list                              # List all providers and their keys
    python manage_provider_keys.py --list --provider openai            # List keys for specific provider
    python manage_provider_keys.py --add-provider <name>               # Add a new provider
    python manage_provider_keys.py --add-key <provider> <api_key>      # Add a key to provider (auto-numbered)
    python manage_provider_keys.py --add-bulk <provider>               # Add multiple keys to a provider
    python manage_provider_keys.py --update-key <provider> <num> <key> # Update key by number
    python manage_provider_keys.py --delete-key <provider> <num>       # Delete key by number
    python manage_provider_keys.py --delete-provider <name>            # Delete provider and all its keys
"""

import os
import sys
import argparse
from supabase import create_client, Client
from dotenv_vault import load_dotenv

load_dotenv()

WORKER_1_URL = os.getenv("WORKER_1_URL")
WORKER_1_KEY = os.getenv("WORKER_1_SERVICE_ROLE_KEY") or os.getenv("WORKER_1_ANON_KEY")

if not WORKER_1_URL or not WORKER_1_KEY:
    print("Error: WORKER_1_URL and WORKER_1_SERVICE_ROLE_KEY (or WORKER_1_ANON_KEY) must be set in .env file")
    sys.exit(1)

worker_client: Client = create_client(WORKER_1_URL, WORKER_1_KEY)
print(f"[OK] Connected to Worker1: {WORKER_1_URL}\n")


def get_providers():
    """Get all providers"""
    try:
        result = worker_client.table("providers").select("*").order("provider_name").execute()
        return result.data if result.data else []
    except Exception as e:
        print(f"Error getting providers: {e}")
        return []


def get_provider_id(provider_name):
    """Get provider ID by name"""
    try:
        result = worker_client.table("providers").select("id").eq("provider_name", provider_name).execute()
        if result.data:
            return result.data[0]['id']
        return None
    except Exception as e:
        print(f"Error getting provider: {e}")
        return None


def get_keys_for_provider(provider_id):
    """Get all keys for a provider"""
    try:
        result = worker_client.table("provider_api_keys").select("*").eq("provider_id", provider_id).order("key_number").execute()
        return result.data if result.data else []
    except Exception as e:
        print(f"Error getting keys: {e}")
        return []


def list_all(provider_filter=None):
    """List all providers and their keys in a table format"""
    providers = get_providers()
    
    if not providers:
        print("No providers found.")
        return
    
    if provider_filter:
        providers = [p for p in providers if p['provider_name'] == provider_filter]
        if not providers:
            print(f"Provider '{provider_filter}' not found.")
            return
    
    all_keys = {}
    max_keys = 0
    
    for provider in providers:
        keys = get_keys_for_provider(provider['id'])
        all_keys[provider['provider_name']] = keys
        if len(keys) > max_keys:
            max_keys = len(keys)
    
    if max_keys == 0:
        print("Providers found but no API keys stored yet.\n")
        print("Providers:")
        for p in providers:
            print(f"  - {p['provider_name']}")
        return
    
    provider_names = [p['provider_name'] for p in providers]
    col_width = max(20, max(len(name) for name in provider_names) + 2)
    
    print("=" * (10 + col_width * len(provider_names)))
    print(f"{'Key #':<10}", end="")
    for name in provider_names:
        print(f"{name:<{col_width}}", end="")
    print()
    print("=" * (10 + col_width * len(provider_names)))
    
    for key_num in range(1, max_keys + 1):
        print(f"{key_num:<10}", end="")
        for name in provider_names:
            keys = all_keys[name]
            key_data = next((k for k in keys if k['key_number'] == key_num), None)
            if key_data:
                api_key = key_data['api_key']
                if len(api_key) > col_width - 5:
                    display_key = api_key[:col_width-8] + "..."
                else:
                    display_key = api_key
                print(f"{display_key:<{col_width}}", end="")
            else:
                print(f"{'-':<{col_width}}", end="")
        print()
    
    print("=" * (10 + col_width * len(provider_names)))
    print(f"\nTotal: {len(providers)} provider(s), {sum(len(k) for k in all_keys.values())} key(s)")


def add_provider(provider_name):
    """Add a new provider"""
    try:
        result = worker_client.table("providers").insert({"provider_name": provider_name}).execute()
        if result.data:
            print(f"[OK] Provider '{provider_name}' added successfully!")
            print(f"  ID: {result.data[0]['id']}")
        else:
            print(f"Error: Could not add provider")
    except Exception as e:
        if "duplicate key" in str(e).lower():
            print(f"Error: Provider '{provider_name}' already exists")
        else:
            print(f"Error adding provider: {e}")


def add_key(provider_name, api_key):
    """Add a key to a provider (auto-numbered)"""
    provider_id = get_provider_id(provider_name)
    
    if not provider_id:
        print(f"Error: Provider '{provider_name}' not found")
        print("Use --add-provider to create it first")
        return
    
    keys = get_keys_for_provider(provider_id)
    next_number = max([k['key_number'] for k in keys], default=0) + 1
    
    try:
        result = worker_client.table("provider_api_keys").insert({
            "provider_id": provider_id,
            "key_number": next_number,
            "api_key": api_key
        }).execute()
        
        if result.data:
            print(f"[OK] Key #{next_number} added to '{provider_name}'")
        else:
            print(f"Error: Could not add key")
    except Exception as e:
        print(f"Error adding key: {e}")


def add_bulk_keys(provider_name):
    """Add multiple keys to a provider"""
    provider_id = get_provider_id(provider_name)
    
    if not provider_id:
        print(f"Error: Provider '{provider_name}' not found")
        create = input("Create this provider? (y/n): ").strip().lower()
        if create == 'y':
            add_provider(provider_name)
            provider_id = get_provider_id(provider_name)
            if not provider_id:
                return
        else:
            return
    
    existing_keys = get_keys_for_provider(provider_id)
    next_number = max([k['key_number'] for k in existing_keys], default=0) + 1
    
    print(f"\n=== Add Multiple Keys to '{provider_name}' ===")
    print(f"Starting from key #{next_number}")
    print("Enter API keys one per line. Empty line to finish.\n")
    
    keys_to_add = []
    key_num = next_number
    
    while True:
        api_key = input(f"Key #{key_num}: ").strip()
        if not api_key:
            break
        keys_to_add.append({
            "provider_id": provider_id,
            "key_number": key_num,
            "api_key": api_key
        })
        key_num += 1
    
    if not keys_to_add:
        print("No keys added.")
        return
    
    try:
        result = worker_client.table("provider_api_keys").insert(keys_to_add).execute()
        if result.data:
            print(f"\n[OK] Added {len(result.data)} key(s) to '{provider_name}'")
            print(f"  Keys #{next_number} to #{key_num - 1}")
        else:
            print(f"Error: Could not add keys")
    except Exception as e:
        print(f"Error adding keys: {e}")


def update_key(provider_name, key_number, new_api_key):
    """Update a specific key by number"""
    provider_id = get_provider_id(provider_name)
    
    if not provider_id:
        print(f"Error: Provider '{provider_name}' not found")
        return
    
    try:
        result = worker_client.table("provider_api_keys").update({
            "api_key": new_api_key
        }).eq("provider_id", provider_id).eq("key_number", key_number).execute()
        
        if result.data:
            print(f"[OK] Key #{key_number} updated for '{provider_name}'")
        else:
            print(f"Error: Key #{key_number} not found for '{provider_name}'")
    except Exception as e:
        print(f"Error updating key: {e}")


def delete_key(provider_name, key_number):
    """Delete a specific key by number"""
    provider_id = get_provider_id(provider_name)
    
    if not provider_id:
        print(f"Error: Provider '{provider_name}' not found")
        return
    
    confirm = input(f"Delete key #{key_number} from '{provider_name}'? (yes/no): ").strip().lower()
    if confirm != 'yes':
        print("Cancelled.")
        return
    
    try:
        result = worker_client.table("provider_api_keys").delete().eq("provider_id", provider_id).eq("key_number", key_number).execute()
        
        if result.data:
            print(f"[OK] Key #{key_number} deleted from '{provider_name}'")
        else:
            print(f"Error: Key #{key_number} not found for '{provider_name}'")
    except Exception as e:
        print(f"Error deleting key: {e}")


def delete_provider(provider_name):
    """Delete a provider and all its keys"""
    provider_id = get_provider_id(provider_name)
    
    if not provider_id:
        print(f"Error: Provider '{provider_name}' not found")
        return
    
    keys = get_keys_for_provider(provider_id)
    
    confirm = input(f"Delete provider '{provider_name}' and {len(keys)} key(s)? (yes/no): ").strip().lower()
    if confirm != 'yes':
        print("Cancelled.")
        return
    
    try:
        result = worker_client.table("providers").delete().eq("id", provider_id).execute()
        
        if result.data:
            print(f"[OK] Provider '{provider_name}' and {len(keys)} key(s) deleted")
        else:
            print(f"Error: Could not delete provider")
    except Exception as e:
        print(f"Error deleting provider: {e}")


def main():
    parser = argparse.ArgumentParser(description="Manage provider API keys")
    parser.add_argument("--list", action="store_true", help="List all providers and keys")
    parser.add_argument("--provider", type=str, help="Filter by provider name")
    parser.add_argument("--add-provider", type=str, metavar="NAME", help="Add a new provider")
    parser.add_argument("--add-key", nargs=2, metavar=("PROVIDER", "API_KEY"), help="Add a key to provider")
    parser.add_argument("--add-bulk", type=str, metavar="PROVIDER", help="Add multiple keys to provider")
    parser.add_argument("--update-key", nargs=3, metavar=("PROVIDER", "KEY_NUM", "API_KEY"), help="Update key by number")
    parser.add_argument("--delete-key", nargs=2, metavar=("PROVIDER", "KEY_NUM"), help="Delete key by number")
    parser.add_argument("--delete-provider", type=str, metavar="NAME", help="Delete provider and all keys")
    
    args = parser.parse_args()
    
    if args.list:
        list_all(args.provider)
    elif args.add_provider:
        add_provider(args.add_provider)
    elif args.add_key:
        add_key(args.add_key[0], args.add_key[1])
    elif args.add_bulk:
        add_bulk_keys(args.add_bulk)
    elif args.update_key:
        update_key(args.update_key[0], int(args.update_key[1]), args.update_key[2])
    elif args.delete_key:
        delete_key(args.delete_key[0], int(args.delete_key[1]))
    elif args.delete_provider:
        delete_provider(args.delete_provider)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
