"""
Test script to verify Cloudinary account credentials
"""

import os
from pathlib import Path
import requests
from dotenv_vault import load_dotenv

# Load .env.vault from the same directory as this script
script_dir = Path(__file__).parent
load_dotenv(script_dir / ".env.vault")

def test_cloudinary_account(name, cloud_name, api_key, api_secret):
    """Test a single Cloudinary account"""
    print(f"\n{'='*60}")
    print(f"Testing: {name}")
    print(f"{'='*60}")
    print(f"Cloud Name: {cloud_name}")
    print(f"API Key: {api_key}")
    print(f"API Secret: {api_secret[:4]}...{api_secret[-4:]}")
    
    try:
        # Test authentication by checking usage
        url = f"https://api.cloudinary.com/v1_1/{cloud_name}/usage"
        auth = (api_key, api_secret)
        
        response = requests.get(url, auth=auth, timeout=10)
        
        if response.status_code == 200:
            print(f"SUCCESS: Account '{name}' credentials are valid")
            
            data = response.json()
            usage = data.get("usage", {})
            
            # Bandwidth info
            bandwidth = usage.get("bandwidth", {})
            bandwidth_used = bandwidth.get("used", 0)
            bandwidth_limit = bandwidth.get("limit", 0)
            
            # Storage info
            storage = usage.get("storage", {})
            storage_used = storage.get("used", 0)
            storage_limit = storage.get("limit", 0)
            
            print(f"   Bandwidth: {bandwidth_used / (1024**3):.2f}GB / {bandwidth_limit / (1024**3):.2f}GB")
            print(f"   Storage: {storage_used / (1024**3):.2f}GB / {storage_limit / (1024**3):.2f}GB")
            
            return True
        elif response.status_code == 401:
            print(f"FAILED: Invalid credentials (401 Unauthorized)")
            print(f"   Please verify the API key and secret in your Cloudinary dashboard")
            return False
        else:
            print(f"FAILED: HTTP {response.status_code}")
            print(f"   Response: {response.text[:200]}")
            return False
            
    except Exception as e:
        print(f"ERROR: {e}")
        return False

def main():
    print("\nCLOUDINARY ACCOUNT VERIFICATION")
    print("="*60)
    
    results = []
    
    # Test Account 2
    cloud_2 = os.getenv("CLOUDINARY_CLOUD_NAME_2") or os.getenv("CLOUDINARY_2_CLOUD_NAME")
    key_2 = os.getenv("CLOUDINARY_API_KEY_2") or os.getenv("CLOUDINARY_2_API_KEY")
    secret_2 = os.getenv("CLOUDINARY_API_SECRET_2") or os.getenv("CLOUDINARY_2_API_SECRET")
    
    if cloud_2 and key_2 and secret_2:
        result = test_cloudinary_account("Account 2", cloud_2, key_2, secret_2)
        results.append(("Account 2", result))
    else:
        print("\nAccount 2 not configured in .env")
    
    # Test Account 3
    cloud_3 = os.getenv("CLOUDINARY_CLOUD_NAME_3") or os.getenv("CLOUDINARY_3_CLOUD_NAME")
    key_3 = os.getenv("CLOUDINARY_API_KEY_3") or os.getenv("CLOUDINARY_3_API_KEY")
    secret_3 = os.getenv("CLOUDINARY_API_SECRET_3") or os.getenv("CLOUDINARY_3_API_SECRET")
    
    if cloud_3 and key_3 and secret_3:
        result = test_cloudinary_account("Account 3", cloud_3, key_3, secret_3)
        results.append(("Account 3", result))
    else:
        print("\nAccount 3 not configured in .env")
    
    # Test Primary Account
    cloud_primary = os.getenv("CLOUDINARY_CLOUD_NAME")
    key_primary = os.getenv("CLOUDINARY_API_KEY")
    secret_primary = os.getenv("CLOUDINARY_API_SECRET")
    
    if cloud_primary and key_primary and secret_primary:
        result = test_cloudinary_account("Primary", cloud_primary, key_primary, secret_primary)
        results.append(("Primary", result))
    else:
        print("\nPrimary account not configured in .env")
    
    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    for name, success in results:
        status = "Valid" if success else "Invalid"
        print(f"{name}: {status}")
    
    print("\n" + "="*60)
    print("NEXT STEPS:")
    print("="*60)
    
    failed = [name for name, success in results if not success]
    if failed:
        print("\nThe following accounts have invalid credentials:")
        for name in failed:
            print(f"   - {name}")
        print("\nTo fix:")
        print("1. Go to https://console.cloudinary.com/")
        print("2. Log into each failing account")
        print("3. Go to Settings > Access Keys")
        print("4. Copy the Cloud name, API Key, and API Secret")
        print("5. Update the values in backend/.env file")
        print("6. Restart the Flask server")
    else:
        print("\nAll configured accounts have valid credentials!")

if __name__ == "__main__":
    main()
