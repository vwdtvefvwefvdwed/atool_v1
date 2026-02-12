"""
Remote Maintenance Mode Manager for Koyeb Deployment
Enable/disable maintenance mode via HTTP endpoint
Note: On Koyeb, the service will auto-restart if terminated. Use maintenance mode to block new jobs.
"""

import os
import sys
import requests
from dotenv_vault import load_dotenv

load_dotenv()

def toggle_maintenance(enable=True):
    backend_url = os.getenv("BACKEND_URL", "https://fiscal-darice-atoolworker-26d3b1bc.koyeb.app")
    admin_secret = os.getenv("ADMIN_SECRET")
    
    if not admin_secret:
        print("❌ ERROR: ADMIN_SECRET not set in environment")
        print("Set ADMIN_SECRET in your .env file")
        return False
    
    maintenance_url = f"{backend_url}/admin/maintenance"
    
    print("="*60)
    print(f"REMOTE MAINTENANCE MODE - {'ENABLE' if enable else 'DISABLE'}")
    print("="*60)
    print(f"Target: {backend_url}")
    print("="*60)
    
    if enable:
        print("\nThis will:")
        print("  ✓ Block new job submissions")
        print("  ✓ Keep service running (no restart)")
        print("  ✓ Allow existing jobs to complete")
        confirm_msg = "\nEnable maintenance mode? (yes/no): "
    else:
        print("\nThis will:")
        print("  ✓ Re-enable job submissions")
        print("  ✓ Resume normal operations")
        confirm_msg = "\nDisable maintenance mode? (yes/no): "
    
    confirm = input(confirm_msg).strip().lower()
    if confirm != "yes":
        print("Operation cancelled.")
        return False
    
    print(f"\nSending request to {'enable' if enable else 'disable'} maintenance mode...")
    
    try:
        response = requests.post(
            maintenance_url,
            headers={
                "Authorization": f"Bearer {admin_secret}",
                "Content-Type": "application/json"
            },
            json={"enable": enable},
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            print("\n✅ SUCCESS")
            print(f"Message: {data.get('message')}")
            print(f"Mode: {data.get('mode')}")
            return True
        else:
            print(f"\n❌ FAILED: {response.status_code}")
            print(f"Response: {response.text}")
            return False
            
    except requests.exceptions.ConnectionError:
        print("\n❌ ERROR: Cannot connect to backend")
        print(f"Check if {backend_url} is accessible")
        return False
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        return False

if __name__ == "__main__":
    # Check command line arguments
    if len(sys.argv) > 1:
        action = sys.argv[1].lower()
        if action in ["enable", "on", "1", "true"]:
            toggle_maintenance(enable=True)
        elif action in ["disable", "off", "0", "false"]:
            toggle_maintenance(enable=False)
        else:
            print("Usage: python remote_shutdown.py [enable|disable]")
            print("  enable  - Enable maintenance mode (block new jobs)")
            print("  disable - Disable maintenance mode (allow new jobs)")
    else:
        # Default: enable maintenance mode
        toggle_maintenance(enable=True)
