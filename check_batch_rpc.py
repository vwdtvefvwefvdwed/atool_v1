"""Check if create_job_batch RPC function exists in Supabase"""
from supabase_client import supabase

try:
    # Try to call the RPC with a test (non-existent) user
    result = supabase.rpc(
        'create_job_batch',
        {
            'p_user_id': '00000000-0000-0000-0000-000000000000',
            'p_prompt': 'test',
            'p_model': 'flux-dev',
            'p_aspect_ratio': '1:1'
        }
    ).execute()
    
    print("[OK] RPC function 'create_job_batch' EXISTS in database!")
    print(f"Response: {result.data}")
    
    # Should return error about user not found if function works
    if result.data:
        if isinstance(result.data, dict) and not result.data.get('success'):
            print(f"[OK] Function works correctly! Error: {result.data.get('error')}")
        
except Exception as e:
    error_msg = str(e)
    if 'function' in error_msg.lower() and ('does not exist' in error_msg.lower() or 'not found' in error_msg.lower()):
        print("[ERROR] RPC function 'create_job_batch' DOES NOT EXIST in database!")
        print("\nTo fix: Run migration 005 in Supabase SQL Editor:")
        print("   File: backend/migrations/005_batch_job_creation.sql")
    else:
        print(f"[WARNING] Error calling RPC: {error_msg}")
