"""
Batch Add Providers Script
Adds all providers used in HomeNew.jsx to the provider_api_keys table.
This script should be run after the 020 migration.

Usage:
    python add_homenew_providers.py
"""

import os
import sys
from supabase import create_client, Client
from dotenv_vault import load_dotenv

load_dotenv()

WORKER_1_URL = os.getenv("WORKER_1_URL")
WORKER_1_SERVICE_KEY = os.getenv("WORKER_1_SERVICE_ROLE_KEY")

if not WORKER_1_URL or not WORKER_1_SERVICE_KEY:
    print("Error: WORKER_1_URL and WORKER_1_SERVICE_ROLE_KEY must be set in .env file")
    print("Note: You need the SERVICE ROLE KEY (not ANON KEY) to manage API keys")
    sys.exit(1)

worker_client: Client = create_client(WORKER_1_URL, WORKER_1_SERVICE_KEY)
print(f"[OK] Connected to Worker1: {WORKER_1_URL}\n")

# All providers from HomeNew.jsx
PROVIDERS = [
    # Image Generation Providers
    {
        "provider_key": "vision-nova",
        "provider_name": "Vision Engine Nova",
        "api_key": "REPLACE_WITH_YOUR_ACTUAL_API_KEY",
        "api_secret": None,
        "additional_config": {
            "type": "image",
            "description": "Recommended - Balanced Quality & Speed",
            "models": [
                "openflux1-v0.1.0-fp8.safetensors",
                "playground-v2.5-1024px-aesthetic.safetensors",
                "sd_xl_base_1.0.safetensors",
                "qwen_image_edit_fp8_e4m3fn.safetensors"
            ],
            "default_model": "openflux1-v0.1.0-fp8.safetensors"
        },
        "priority": 100,
        "is_active": False  # Set to False until real API key is added
    },
    {
        "provider_key": "vision-atlas",
        "provider_name": "Vision Engine Atlas",
        "api_key": "REPLACE_WITH_YOUR_ACTUAL_API_KEY",
        "api_secret": None,
        "additional_config": {
            "type": "image",
            "description": "High-quality aesthetic images",
            "models": [
                "openflux1-v0.1.0-fp8.safetensors",
                "playground-v2.5-1024px-aesthetic.safetensors",
                "sd_xl_base_1.0.safetensors",
                "qwen_image_edit_fp8_e4m3fn.safetensors"
            ],
            "default_model": "playground-v2.5-1024px-aesthetic.safetensors"
        },
        "priority": 90,
        "is_active": False
    },
    {
        "provider_key": "vision-flux",
        "provider_name": "Vision Engine Flux",
        "api_key": "REPLACE_WITH_YOUR_ACTUAL_API_KEY",
        "api_secret": None,
        "additional_config": {
            "type": "image",
            "description": "Industry standard model",
            "models": [
                "openflux1-v0.1.0-fp8.safetensors",
                "playground-v2.5-1024px-aesthetic.safetensors",
                "sd_xl_base_1.0.safetensors",
                "qwen_image_edit_fp8_e4m3fn.safetensors"
            ],
            "default_model": "sd_xl_base_1.0.safetensors"
        },
        "priority": 80,
        "is_active": False
    },
    {
        "provider_key": "vision-bria",
        "provider_name": "Vision Bria AI",
        "api_key": "REPLACE_WITH_YOUR_ACTUAL_API_KEY",
        "api_secret": None,
        "additional_config": {
            "type": "image",
            "description": "Enterprise-grade image generation and editing",
            "models": [
                "bria_image_generate",
                "bria_image_generate_lite",
                "bria_gen_fill",
                "bria_erase",
                "bria_remove_background",
                "bria_replace_background",
                "bria_blur_background",
                "bria_erase_foreground",
                "bria_expand",
                "bria_enhance"
            ],
            "default_model": "bria_image_generate"
        },
        "priority": 70,
        "is_active": False
    },
    
    # Video Generation Providers
    {
        "provider_key": "cinematic-nova",
        "provider_name": "Cinematic Engine Nova",
        "api_key": "REPLACE_WITH_YOUR_ACTUAL_API_KEY",
        "api_secret": None,
        "additional_config": {
            "type": "video",
            "description": "High-quality video generation",
            "models": [
                "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors"
            ],
            "default_model": "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors"
        },
        "priority": 100,
        "is_active": False
    },
    {
        "provider_key": "cinematic-pro",
        "provider_name": "Cinematic Engine Pro",
        "api_key": "REPLACE_WITH_YOUR_ACTUAL_API_KEY",
        "api_secret": None,
        "additional_config": {
            "type": "video",
            "description": "Advanced video AI model",
            "models": [
                "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors"
            ],
            "default_model": "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors"
        },
        "priority": 90,
        "is_active": False
    },
    {
        "provider_key": "cinematic-x",
        "provider_name": "Cinematic Engine X",
        "api_key": "REPLACE_WITH_YOUR_ACTUAL_API_KEY",
        "api_secret": None,
        "additional_config": {
            "type": "video",
            "description": "Fast video generation",
            "models": [
                "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors"
            ],
            "default_model": "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors"
        },
        "priority": 80,
        "is_active": False
    },
    {
        "provider_key": "cinematic-bria",
        "provider_name": "Cinematic Bria AI",
        "api_key": "REPLACE_WITH_YOUR_ACTUAL_API_KEY",
        "api_secret": None,
        "additional_config": {
            "type": "video",
            "description": "Enterprise-grade video editing and segmentation",
            "models": [
                "bria_video_erase",
                "bria_video_upscale",
                "bria_video_remove_bg",
                "bria_video_mask_prompt",
                "bria_video_mask_keypoints",
                "bria_video_foreground_mask"
            ],
            "default_model": "bria_video_upscale"
        },
        "priority": 70,
        "is_active": False
    },
]

def add_providers():
    """Add all providers to the database"""
    print("=== Adding HomeNew Providers ===\n")
    
    added = 0
    skipped = 0
    errors = 0
    
    for provider in PROVIDERS:
        try:
            # Check if provider already exists
            existing = worker_client.table("provider_api_keys")\
                .select("id")\
                .eq("provider_key", provider["provider_key"])\
                .execute()
            
            if existing.data:
                print(f"[SKIP] {provider['provider_name']} (already exists)")
                skipped += 1
                continue
            
            # Insert provider
            result = worker_client.table("provider_api_keys").insert(provider).execute()
            
            if result.data:
                print(f"[OK] Added: {provider['provider_name']}")
                print(f"  Key: {provider['provider_key']}")
                print(f"  Type: {provider['additional_config']['type']}")
                print(f"  Priority: {provider['priority']}")
                print(f"  Active: {provider['is_active']}")
                print()
                added += 1
            else:
                print(f"[ERROR] Error adding: {provider['provider_name']}")
                errors += 1
                
        except Exception as e:
            print(f"[ERROR] Error adding {provider['provider_name']}: {e}")
            errors += 1
    
    print("\n" + "="*50)
    print(f"Summary:")
    print(f"  [OK] Added: {added}")
    print(f"  [SKIP] Skipped: {skipped}")
    print(f"  [ERROR] Errors: {errors}")
    print(f"  Total: {len(PROVIDERS)}")
    print("="*50)
    
    if added > 0:
        print(f"\nIMPORTANT: All providers are currently INACTIVE")
        print(f"   You need to update them with real API keys and activate them:")
        print(f"\n   1. Update API keys:")
        print(f"      python manage_provider_keys.py --update <key_id>")
        print(f"\n   2. Activate providers:")
        print(f"      python manage_provider_keys.py --activate <key_id>")
        print(f"\n   Or list all to see IDs:")
        print(f"      python manage_provider_keys.py --list")

def main():
    add_providers()

if __name__ == "__main__":
    main()
