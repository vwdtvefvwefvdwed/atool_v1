"""
Quick diagnostic script to check if metadata is being saved to Cloudinary
"""

import os
import sys
import cloudinary
import cloudinary.api
from envvault import load_env

# Fix Unicode output on Windows
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
load_env()
# Configure Cloudinary
cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")
api_key = os.getenv("CLOUDINARY_API_KEY")
api_secret = os.getenv("CLOUDINARY_API_SECRET")

if not all([cloud_name, api_key, api_secret]):
    print("❌ Cloudinary credentials not found in .env file")
    exit(1)

cloudinary.config(
    cloud_name=cloud_name,
    api_key=api_key,
    api_secret=api_secret,
    secure=True
)

print("=" * 70)
print("🔍 CHECKING CLOUDINARY METADATA STATUS")
print("=" * 70)
print()

try:
    # Get recent images from the ai-generated-images folder
    print("📁 Fetching recent images from 'ai-generated-images' folder...")
    result = cloudinary.api.resources(
        type="upload",
        prefix="ai-generated-images/",
        max_results=10,
        context=True  # Include context metadata
    )
    
    resources = result.get('resources', [])
    
    if not resources:
        print("⚠️  No images found in the ai-generated-images folder")
        print()
        print("💡 This could mean:")
        print("   1. No images have been uploaded yet")
        print("   2. The folder name is different")
        print("   3. Images are in the root folder")
        print()
        print("🔍 Checking root folder...")
        result = cloudinary.api.resources(
            type="upload",
            max_results=10,
            context=True
        )
        resources = result.get('resources', [])
    
    if not resources:
        print("❌ No images found in Cloudinary")
        exit(1)
    
    print(f"✅ Found {len(resources)} images")
    print()
    print("=" * 70)
    
    has_metadata = False
    
    for i, resource in enumerate(resources, 1):
        public_id = resource.get('public_id')
        created_at = resource.get('created_at')
        context = resource.get('context', {})
        custom_context = context.get('custom', {}) if isinstance(context, dict) else {}
        
        print(f"\n📸 Image {i}: {public_id}")
        print(f"   Created: {created_at}")
        print(f"   URL: {resource.get('secure_url')}")
        
        if custom_context:
            has_metadata = True
            print(f"   ✅ HAS METADATA:")
            for key, value in custom_context.items():
                print(f"      • {key}: {value}")
        else:
            print(f"   ❌ NO METADATA FOUND")
            print(f"      Context object: {context}")
    
    print()
    print("=" * 70)
    print("📊 SUMMARY")
    print("=" * 70)
    
    if has_metadata:
        print("✅ SUCCESS! At least one image has metadata stored")
        print()
        print("💡 The metadata storage feature is working correctly!")
    else:
        print("⚠️  WARNING: None of the recent images have metadata")
        print()
        print("🔍 Possible causes:")
        print("   1. Images were uploaded before metadata feature was implemented")
        print("   2. Job worker is not passing metadata correctly")
        print("   3. Metadata values are empty strings (filtered out)")
        print()
        print("✅ Next steps:")
        print("   1. Restart the backend server: python backend/app.py")
        print("   2. Restart the job worker: python backend/job_worker.py")
        print("   3. Create a new image generation job")
        print("   4. Run this script again to check if metadata was saved")
    
    print()

except cloudinary.exceptions.Error as e:
    print(f"❌ Cloudinary API error: {e}")
    exit(1)
except Exception as e:
    print(f"❌ Unexpected error: {e}")
    import traceback
    traceback.print_exc()
    exit(1)
