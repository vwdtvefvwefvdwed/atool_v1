'''\
Gemini Web API Client Wrapper
==============================

This module provides a thin async wrapper around the ``gemini_webapi``
library (HanaokaYuzu/Gemini-API).  It integrates the Gemini Web API into the
existing provider infrastructure:

* Credentials are stored as JSON in the ``api_key`` column of the
  ``provider_api_keys`` table (same as other providers).
* The wrapper parses the JSON to extract the two required cookies
  ``secure_1psid`` and ``secure_1psidts``.
* Clients are cached per provider and automatically refresh cookies in the
  background (``auto_refresh=True``).
* Generation and editing functions accept a prompt, model name, aspect ratio
  and an optional list of image URLs (up to three, per Gemini Web API limits).

The wrapper is deliberately lightweight – it does not modify the existing
database schema and works with the current round‑robin rotation logic.
'''\

import asyncio
import base64
import json
import os
import tempfile
from typing import Any, Dict, List, Optional

from gemini_webapi import GeminiClient

# Import the existing credential helper (will be extended later)
import sys, os
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)
from provider_api_keys import get_provider_api_key

# Cache of instantiated GeminiClient objects keyed by provider name
_GEMINI_CLIENT_CACHE: Dict[str, GeminiClient] = {}

# Cache of credential hashes to detect changes
_CREDENTIAL_HASH_CACHE: Dict[str, str] = {}

def _get_credential_hash(raw: str) -> str:
    """Generate a hash of credentials to detect changes."""
    import hashlib
    return hashlib.md5(raw.encode()).hexdigest()

# ---------------------------------------------------------------------------
# Helper: parse dual‑credential JSON stored in ``api_key`` field
# ---------------------------------------------------------------------------
def _parse_gemini_credentials(raw: str) -> Dict[str, Any]:
    """Parse the JSON credential string for Gemini Web API.

    Expected JSON structure (all fields are strings)::

        {
            "secure_1psid": "g.XXXXXXXXXXXXXXXX",
            "secure_1psidts": "g.YYYYYYYYYYYYYYYYYY",
            "proxy": "http://user:pass@host:port",  # optional
            "account_email": "user@example.com"   # optional
        }

    ``raw`` is the ``api_key`` text column from ``provider_api_keys``.
    An exception is raised if parsing fails or required fields are missing.
    
    Proxy is optional and will be used only for this specific credential.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON for Gemini credentials: {exc}")

    # Required fields
    if not isinstance(data, dict):
        raise ValueError("Gemini credentials JSON must be an object")

    secure_1psid = data.get("secure_1psid") or data.get("secure_1psid") or data.get("Secure_1PSID")
    secure_1psidts = data.get("secure_1psidts") or data.get("secure_1psidts") or data.get("Secure_1PSIDTS")

    if not secure_1psid or not secure_1psidts:
        raise ValueError("Gemini credentials must contain 'secure_1psid' and 'secure_1psidts'")

    # Extract proxy (optional field) - can be None or string
    proxy = data.get("proxy") or data.get("Proxy")
    
    # Validate proxy format if provided
    if proxy and not isinstance(proxy, str):
        raise ValueError("Proxy must be a string URL or null")
    
    return {
        "secure_1psid": secure_1psid,
        "secure_1psidts": secure_1psidts,
        "proxy": proxy,  # Include proxy in parsed credentials
        # Preserve any optional fields for future use
        **{k: v for k, v in data.items() if k not in ("secure_1psid", "secure_1psidts", "proxy")}
    }

# ---------------------------------------------------------------------------
# Public API: get a ready‑to‑use GeminiClient for a provider
# ---------------------------------------------------------------------------
async def get_gemini_client(provider_key: str = "vision-geminiwebapi") -> GeminiClient:
    """Return a cached ``GeminiClient`` instance for ``provider_key``.

    The function fetches the stored JSON credentials, parses them and creates a
    ``GeminiClient`` with ``auto_refresh=True`` so that the cookies are kept
    alive while the process runs.  The client is cached for the lifetime of the
    process to avoid re‑initialisation on every request.
    
    If the credential JSON contains a ``proxy`` field, it will be used for this
    specific client instance ONLY. Other credentials/providers are unaffected.
    """
    if provider_key in _GEMINI_CLIENT_CACHE:
        return _GEMINI_CLIENT_CACHE[provider_key]

    # Retrieve credentials from the standard ``provider_api_keys`` helper
    key_record = get_provider_api_key(provider_key)
    if not key_record:
        raise RuntimeError(f"No API key record found for provider '{provider_key}'")

    # ``api_key`` column stores the JSON string with both cookies
    raw_credentials = key_record.get("api_key")
    if not raw_credentials:
        raise RuntimeError(f"Provider '{provider_key}' has no stored credentials")

    creds = _parse_gemini_credentials(raw_credentials)
    
    # Extract proxy from credentials (may be None)
    proxy = creds.get("proxy")
    if proxy:
        print(f"[Gemini Web API] Using proxy for credential: {proxy[:30]}...")
    
    # Initialize Gemini client with proxy (if provided)
    # Note: proxy is used ONLY for this specific credential, not globally
    client = GeminiClient(
        creds["secure_1psid"], 
        creds["secure_1psidts"],
        proxy=proxy  # Pass proxy to client (None if not provided)
    )
    
    # ``auto_refresh`` enables background cookie refresh (see gemini_webapi docs)
    await client.init(timeout=30, auto_close=False, close_delay=300, auto_refresh=True)

    _GEMINI_CLIENT_CACHE[provider_key] = client
    return client

# ---------------------------------------------------------------------------
# Core generation / editing function used by ``multi_endpoint_manager``
# ---------------------------------------------------------------------------
async def generate_with_gemini_web(
    prompt: str,
    model: str,
    aspect_ratio: str = "1:1",
    input_images: Optional[List[str]] = None,
    provider_key: str = "vision-geminiwebapi",
) -> Dict[str, Any]:
    """Generate or edit an image using the Gemini *Web* endpoint.

    Parameters
    ----------
    prompt:
        The natural‑language prompt.  For image editing start the prompt with
        ``Edit`` or ``Modify`` as recommended by the Gemini documentation.
    model:
        One of the supported Gemini models, e.g. ``gemini-3-pro`` or
        ``gemini-2.5-flash-image``.  Model names are passed directly to the
        underlying ``GeminiClient``; the wrapper does **not** rewrite them.
    aspect_ratio:
        Desired aspect ratio (``1:1``, ``16:9``, ``9:16`` etc.).  Gemini Web
        supports a set of ratios; unsupported values fall back to ``1:1``.
    input_images:
        Optional list of up to three image URLs.  When provided the request
        performs *img2img* (image‑to‑image) editing.  The list may contain a
        single URL for a simple edit.
    provider_key:
        Database provider identifier (defaults to ``vision-geminiwebapi``).

    Returns
    -------
    dict
        ``{"success": True, "data": <base64‑encoded image>, "type": "image", "is_base64": True}``
        on success.  On failure an exception is raised.
    """
    client = await get_gemini_client(provider_key)

    # Normalise image list – Gemini Web API allows up to 3 images.
    files = []
    if input_images:
        # Ensure we never exceed the 3‑image limit.
        files = input_images[:3]

    # The underlying library accepts ``files`` as a list of file paths or URLs.
    # It will download the URLs internally, so we forward the URL strings.
    response = await client.generate_content(
        prompt=prompt,
        files=files,
        model=model,
        # The Gemini Web API does not expose aspect‑ratio as a separate
        # parameter; it is inferred from the model or prompt.  We keep the
        # argument for future flexibility.
    )

    # ``response.images`` is a list of ``GeneratedImage`` objects.  We convert
    # the first image to base64 to fit the existing return contract used by the
    # other providers.
    if not getattr(response, "images", None):
        raise RuntimeError("Gemini Web API returned no images")

    # Save the image to a temporary file, read bytes, encode.
    img_obj = response.images[0]
    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
        tmp_path = tmp.name
        # ``save`` returns the path when provided a directory + filename.
        await img_obj.save(path=os.path.dirname(tmp_path), filename=os.path.basename(tmp_path))
        tmp.flush()
        with open(tmp_path, "rb") as f:
            img_bytes = f.read()
        # Clean up temporary file
        os.unlink(tmp_path)

    b64_image = base64.b64encode(img_bytes).decode("utf-8")
    return {
        "success": True,
        "data": b64_image,
        "type": "image",
        "is_base64": True,
    }

# ---------------------------------------------------------------------------
# Convenience wrapper for image *editing* (adds context that the operation
# is an edit rather than a pure generation).  The caller can still use the same
# function – the important part is that the prompt starts with ``Edit`` or
# ``Modify``.
# ---------------------------------------------------------------------------
async def edit_image_with_gemini_web(
    prompt: str,
    input_image_url: str,
    model: str = "gemini-3-pro",
    aspect_ratio: str = "1:1",
) -> Dict[str, Any]:
    """Convenient helper for image‑to‑image editing.

    ``prompt`` should be phrased as an edit instruction (e.g. ``Edit this
    image to be cyberpunk``).  ``input_image_url`` may be a local path or a remote
    URL; the Gemini client will download remote URLs automatically.
    """
    return await generate_with_gemini_web(
        prompt=prompt,
        model=model,
        aspect_ratio=aspect_ratio,
        input_images=[input_image_url],
    )

# ---------------------------------------------------------------------------
# Helper for simple generation (no input image)
# ---------------------------------------------------------------------------
async def generate_image_with_gemini_web(
    prompt: str,
    model: str = "gemini-3-pro",
    aspect_ratio: str = "1:1",
) -> Dict[str, Any]:
    """Generate an image from a text‑only prompt.
    ``model`` selects the quality/speed trade‑off (see documentation).
    """
    return await generate_with_gemini_web(
        prompt=prompt,
        model=model,
        aspect_ratio=aspect_ratio,
        input_images=None,
    )

# ---------------------------------------------------------------------------
# Export symbols for ``multi_endpoint_manager`` imports
# ---------------------------------------------------------------------------
__all__ = [
    "get_gemini_client",
    "generate_with_gemini_web",
    "edit_image_with_gemini_web",
    "generate_image_with_gemini_web",
]
