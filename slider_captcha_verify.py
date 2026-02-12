import os
import jwt
import time
from datetime import datetime
from collections import defaultdict

CAPTCHA_SECRET = os.getenv('CAPTCHA_SECRET', 'your-super-secret-captcha-key-change-in-production')

# Track used tokens (in-memory, token -> timestamp when used)
used_tokens = {}
last_cleanup = time.time()
CLEANUP_INTERVAL = 600  # Clean up every 10 minutes

def cleanup_expired_tokens():
    """Remove tokens older than 10 minutes from used_tokens"""
    global last_cleanup
    current_time = time.time()
    
    if current_time - last_cleanup < CLEANUP_INTERVAL:
        return
    
    last_cleanup = current_time
    expired_keys = [token for token, used_time in used_tokens.items() if current_time - used_time > 600]
    
    for token in expired_keys:
        del used_tokens[token]
    
    if expired_keys:
        print(f"🧹 Cleaned up {len(expired_keys)} expired captcha tokens")

def verify_captcha_token(token: str, mark_as_used: bool = True) -> dict:
    """
    Verify captcha token and optionally mark it as used.
    
    Args:
        token: The JWT token to verify
        mark_as_used: If True, marks the token as used (one-time use). Default True.
    
    Returns:
        dict with 'success' and optional 'error' or 'verified_at'
    """
    cleanup_expired_tokens()
    
    if not token:
        return {'success': False, 'error': 'No CAPTCHA token provided'}
    
    # Check if token was already used
    if token in used_tokens:
        return {'success': False, 'error': 'CAPTCHA token already used'}
    
    try:
        payload = jwt.decode(token, CAPTCHA_SECRET, algorithms=['HS256'])
        
        if not payload.get('captcha'):
            return {'success': False, 'error': 'Invalid CAPTCHA token'}
        
        # Mark token as used
        if mark_as_used:
            used_tokens[token] = time.time()
            print(f"✅ CAPTCHA token marked as used. Total used: {len(used_tokens)}")
        
        return {
            'success': True,
            'verified_at': payload.get('ts'),
        }
    except jwt.ExpiredSignatureError:
        return {'success': False, 'error': 'CAPTCHA token expired'}
    except jwt.InvalidTokenError as e:
        return {'success': False, 'error': f'Invalid CAPTCHA token: {str(e)}'}
    except Exception as e:
        return {'success': False, 'error': f'CAPTCHA verification failed: {str(e)}'}
