"""
Monetag Postback URL Manager
Purpose: Store, retrieve, and log Monetag postback URLs for debugging
Date: 2025-12-26
"""

import os
import time
import json
from datetime import datetime, timedelta
from typing import Dict, Optional, List

# In-memory cache for postback URLs
postback_cache = {
    'urls': [],
    'last_url': None,
    'last_received': None,
    'received_count': 0,
    'last_reset': datetime.now()
}

# Configuration
MAX_CACHE_SIZE = 100
CACHE_RETENTION_HOURS = 24
PUBLIC_DOMAIN = os.getenv("PUBLIC_DOMAIN", "https://friendly-potato-g4jv5rrr69953p9xv-5000.app.github.dev")


def get_postback_url() -> str:
    """
    Generate the complete Monetag postback URL

    Returns:
        Full postback URL with macro parameters
    """
    url = f"{PUBLIC_DOMAIN}/api/monetag/postback"
    macros = "?ymid={{ymid}}&estimated_price={{estimated_price}}&reward_event_type={{reward_event_type}}"

    return url + macros


def log_postback_received(click_id: str, revenue: float, status: str) -> None:
    """
    Log postback reception for debugging

    Args:
        click_id: The unique click identifier
        revenue: Revenue amount
        status: Completion status (valued/not_valued)
    """
    postback_data = {
        'timestamp': datetime.now().isoformat(),
        'click_id': click_id,
        'revenue': revenue,
        'status': status
    }

    # Add to cache
    postback_cache['urls'].append(postback_data)
    postback_cache['last_url'] = postback_data
    postback_cache['last_received'] = datetime.now().isoformat()
    postback_cache['received_count'] += 1

    # Keep cache size manageable
    if len(postback_cache['urls']) > MAX_CACHE_SIZE:
        postback_cache['urls'].pop(0)

    print(f"\n📊 POSTBACK LOGGED:")
    print(f"   Click ID: {click_id}")
    print(f"   Revenue: ${revenue}")
    print(f"   Status: {status}")
    print(f"   Total Received: {postback_cache['received_count']}")


def get_postback_stats() -> Dict:
    """
    Get statistics about postbacks received

    Returns:
        Dictionary with postback statistics
    """
    return {
        'public_domain': PUBLIC_DOMAIN,
        'postback_url': get_postback_url(),
        'total_received': postback_cache['received_count'],
        'last_postback': postback_cache['last_received'],
        'recent_postbacks': postback_cache['urls'][-10:],  # Last 10
        'cache_size': len(postback_cache['urls']),
        'uptime_hours': (datetime.now() - postback_cache['last_reset']).total_seconds() / 3600
    }


def get_recent_postbacks(limit: int = 20) -> List[Dict]:
    """
    Get recent postbacks from cache

    Args:
        limit: Maximum number to return

    Returns:
        List of recent postback records
    """
    return postback_cache['urls'][-limit:]


def clear_postback_cache() -> Dict:
    """
    Clear postback cache (for testing)

    Returns:
        Previous cache statistics
    """
    stats = get_postback_stats()

    postback_cache['urls'] = []
    postback_cache['last_url'] = None
    postback_cache['last_received'] = None
    postback_cache['received_count'] = 0
    postback_cache['last_reset'] = datetime.now()

    print(f"\n🗑️  Postback cache cleared")
    print(f"   Previous stats: {stats['total_received']} postbacks")

    return stats


def get_postback_url_config() -> Dict:
    """
    Get postback URL configuration for Monetag dashboard

    Returns:
        Dictionary with configuration details
    """
    return {
        'postback_url': get_postback_url(),
        'public_domain': PUBLIC_DOMAIN,
        'endpoint_path': '/api/monetag/postback',
        'supported_methods': ['GET', 'POST'],
        'required_parameters': {
            'ymid': 'Unique click identifier',
            'estimated_price': 'Revenue from ad',
            'reward_event_type': 'valued or not_valued'
        },
        'optional_parameters': {
            'zone_id': 'Ad zone identifier (optional)',
            'user_id': 'User identifier (optional)'
        },
        'expected_response': {
            'status': 200,
            'body': {
                'success': True,
                'message': 'Postback received and validated',
                'click_id': 'echo of ymid',
                'revenue': 'echo of estimated_price',
                'processed': True
            }
        },
        'example_url': get_postback_url().replace('{ymid}', 'test_click_123').replace('{estimated_price}', '2.50').replace('{reward_event_type}', 'valued'),
        'instructions': [
            '1. Copy the postback_url above',
            '2. Go to Monetag Dashboard → Settings → Postback URL',
            '3. Paste the URL (with {macros} for dynamic values)',
            '4. Save configuration',
            '5. Complete an ad to trigger postback'
        ]
    }


def format_postback_log() -> str:
    """
    Format postback cache as readable log

    Returns:
        Formatted string for display
    """
    stats = get_postback_stats()

    log = f"""
{'='*80}
MONETAG POSTBACK STATISTICS
{'='*80}
Public Domain: {stats['public_domain']}
Postback URL: {stats['postback_url']}
Total Received: {stats['total_received']}
Last Postback: {stats['last_postback']}
Cache Size: {stats['cache_size']}/{MAX_CACHE_SIZE}
Uptime: {stats['uptime_hours']:.1f} hours

RECENT POSTBACKS (Last 10):
"""

    if stats['recent_postbacks']:
        for i, pb in enumerate(stats['recent_postbacks'][-10:], 1):
            log += f"\n{i}. [{pb['timestamp']}]"
            log += f"\n   Click ID: {pb['click_id']}"
            log += f"\n   Revenue: ${pb['revenue']}"
            log += f"\n   Status: {pb['status']}"
    else:
        log += "\nNo postbacks received yet"

    log += f"\n{'='*80}\n"

    return log


# Print configuration on module load
print(f"\n🔗 MONETAG POSTBACK URL MANAGER INITIALIZED")
print(f"   Postback URL: {get_postback_url()}")
print(f"   Cache size: {len(postback_cache['urls'])}/{MAX_CACHE_SIZE}")
