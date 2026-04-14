"""
Multi-Endpoint Manager
Routes generation requests to different API providers based on provider key:
- vision-nova, cinematic-nova → Replicate API
- vision-pixazo → Pixazo API
- vision-huggingface → Hugging Face API (Gradio Space: IllusionDiffusion | Gradio Space Upscaler: Real-ESRGAN 4x, Swin2SR 2x via LULDev/upscale)
- vision-ultrafast → RapidAPI (Ultra Fast Nano Banana)
- vision-atlas → A4F API (OpenAI-compatible)
- vision-flux, cinematic-pro → KIE AI (Task-based)
- vision-removebg → Remove.bg API
- vision-bria → Bria AI Vision (Image generation and editing)
- vision-infip → Infip.pro API (Async polling-based)
- vision-deapi, cinematic-deapi → deAPI (Async polling-based)
- vision-leonardo, cinematic-leonardo → Leonardo AI (Async polling-based)
- vision-stabilityai → Stability AI (Image upscaling)
- vision-picsart → Picsart API (Ultra upscaling)
- vision-clipdrop → Clipdrop API (Image upscaling)
- cinematic-bria → Bria AI Cinematic (Video editing and generation)
- cinematic-vercel, vision-vercel → Vercel AI Gateway (xAI Grok models)
- vision-frenix → Frenix API (Image generation)
- vision-aicc → AICC API (Image generation / img2img via Gemini 2.5 Flash)
- cinematic-aicc → AICC API (Video generation via Wan 2.2 i2v-plus)
- vision-felo → Felo AI API (Text-to-image + image editing via nano-banana-2)
- vision-gemini → Gemini API (Image generation & editing via Gemini 2.0 Flash)
- vision-ondemand → On-Demand API (Webhook-based image generation via workflow execution)
"""

import os
import time
import requests
import base64
import json
from urllib.parse import urlparse, unquote
from dotenv_vault import load_dotenv
import replicate

load_dotenv()


def get_image_format_from_url(url):
    """
    Detect image format from URL path extension or Cloudinary f_FORMAT transform params.
    Returns lowercase format string: 'jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp', 'tiff', 'tif', 'avif', etc., or None.

    Handles:
      - Standard extension:   .../image.tiff
      - Cloudinary f_FORMAT:  .../upload/f_tiff/v123/image
      - Cloudinary combined:  .../upload/w_1024,f_png/v123/image
      - Cloudinary f_auto:    returns None (format unknown)
    """
    if not url or not isinstance(url, str):
        return None
    try:
        parsed = urlparse(url)
        path = unquote(parsed.path).lower()

        known_formats = ['jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp', 'tiff', 'tif', 'avif',
                         'mp4', 'webm', 'mov', 'avi', 'mkv']

        for fmt in known_formats:
            if path.endswith(f'.{fmt}'):
                return fmt

        for segment in path.split('/'):
            for part in segment.split(','):
                part = part.strip()
                if part.startswith('f_') and part != 'f_auto':
                    candidate = part[2:]
                    if candidate in known_formats:
                        return candidate

        return None
    except Exception:
        return None


def validate_image_format(url, allowed_formats, endpoint_name, is_video=False):
    """
    Validate that an image/video URL has a supported format for the given endpoint.
    
    Args:
        url: The image/video URL (str or list of str)
        allowed_formats: List of allowed lowercase extensions e.g. ['jpg', 'jpeg', 'png', 'webp']
        endpoint_name: Name for error messages (e.g. '[RemoveBG]')
        is_video: If True, validate video formats instead
    
    Raises:
        Exception with INVALID_IMAGE_FORMAT prefix if format is not supported.
    
    Returns silently if format is supported or cannot be determined from URL.
    """
    urls = url if isinstance(url, list) else [url]
    
    for u in urls:
        fmt = get_image_format_from_url(u)
        if fmt is None:
            print(f"{endpoint_name} Could not detect image format from URL: {u[:80]}... Proceeding (format will be validated by API).")
            continue
        
        normalized = 'jpg' if fmt == 'jpeg' else fmt
        normalized_allowed = ['jpg' if f == 'jpeg' else f for f in allowed_formats]
        
        if normalized not in normalized_allowed:
            supported_str = ', '.join(f.upper() for f in allowed_formats)
            raise Exception(
                f"INVALID_IMAGE_FORMAT: {endpoint_name} does not support '{fmt.upper()}' format. "
                f"Supported formats: {supported_str}. "
                f"Please convert your image to one of the supported formats and try again."
            )

REPLICATE_MODELS = {
    'google/imagen-4': 'google/imagen-4',
    'black-forest-labs/flux-kontext-pro': 'black-forest-labs/flux-kontext-pro',
    'ideogram-ai/ideogram-v3-turbo': 'ideogram-ai/ideogram-v3-turbo',
    'black-forest-labs/flux-1.1-pro': 'black-forest-labs/flux-1.1-pro',
    'black-forest-labs/flux-dev': 'black-forest-labs/flux-dev',
    'minimax/video-01': 'minimax/video-01',
    'luma/reframe-video': 'luma/reframe-video',
    'topazlabs/video-upscale': 'topazlabs/video-upscale',
    'topazlabs/image-upscale': 'topazlabs/image-upscale',
    'sczhou/codeformer': 'sczhou/codeformer:7de2ea26c616d5bf2245ad0d5e24f0ff9a6204578a5c876db53142edd9d2cd56',
    'tencentarc/gfpgan': 'tencentarc/gfpgan:ae80bbe1adce7d616b8a96ba88a91d3556838d4f2f4da76327638b8e95ea4694',
}

# Pixazo Models
PIXAZO_MODELS = {
    'flux-1-schnell': 'flux-1-schnell',
}

# Hugging Face Models (Gradio Space)
HUGGINGFACE_MODELS = {
    'AP123/IllusionDiffusion': 'AP123/IllusionDiffusion',
}

# HuggingFace upscaler/restore models via Gradio Spaces
# Maps model name → (gradio_space_id, scale_value, api_type)
# api_type: 'real-esrgan' | 'codeformer'
HUGGINGFACE_SERVERLESS_MODELS = {
    'finegrain/finegrain-image-enhancer': ('finegrain/finegrain-image-enhancer', '4x', 'finegrain'),
    'sczhou/CodeFormer':                  ('sczhou/CodeFormer',                  '2',  'codeformer'),
}

# RapidAPI Models - Ultra Fast Nano Banana
RAPIDAPI_MODELS = {
    'ultra-fast-nano': 'ultra-fast-nano-banana-2',
    'ultra-fast-nano-banana-2': 'ultra-fast-nano-banana-2',
    'flux-nano-banana': 'flux-nano-banana',
    'nano-banana-gemini': 'nano-banana-gemini',
}

# A4F Models - OpenAI-compatible API at https://api.a4f.co/v1
A4F_MODELS = {
    # Provider-8 models
    'imagen-3': 'provider-8/imagen-3',
    'firefrost': 'provider-8/firefrost',
    'z-image': 'provider-8/z-image',
    
    # Provider-4 models
    'imagen-3.5': 'provider-4/imagen-3.5',
    'imagen-4': 'provider-4/imagen-4',  # Clean name (user-facing)
    'imagen-4-a4f': 'provider-4/imagen-4',  # Legacy alias (backward compatibility)
    'sdxl-lite': 'provider-4/sdxl-lite',
    'phoenix': 'provider-4/phoenix',
    'flux-schnell': 'provider-4/flux-schnell',  # Clean name (user-facing)
    'flux-schnell-a4f': 'provider-4/flux-schnell',  # Legacy alias (backward compatibility)
}

# KIE AI Models - https://api.kie.ai/api/v1
KIE_MODELS = {
    # Image models (vision-flux)
    'nano-banana-pro': 'flux-2/pro-text-to-image',
    'flux-2-pro': 'flux-2/pro-text-to-image',
    
    # Video models (cinematic-pro)
    'kling-2.6': 'kling-2.6/image-to-video',
    
    # Grok Imagine models (cinematic-pro) - xAI Grok multimodal video generation
    'grok-text-to-video': 'grok-imagine/text-to-video',
    'grok-image-to-video': 'grok-imagine/image-to-video',
}

# Remove.bg Models - https://api.remove.bg/v1.0/removebg
REMOVEBG_MODELS = {
    'remove-bg': 'remove-bg',
}

# Bria AI Vision Models - https://engine.prod.bria-api.com/v2
BRIA_VISION_MODELS = {
    # Image Generation
    'bria_image_generate': '/image/generate',
    'bria_image_generate_lite': '/image/generate/lite',
    'bria_structured_prompt': '/structured_prompt/generate',
    
    # Image Editing
    'bria_gen_fill': '/image/edit/gen_fill',
    'bria_erase': '/image/edit/erase',
    'bria_remove_background': '/image/edit/remove_background',
    'bria_replace_background': '/image/edit/replace_background',
    'bria_blur_background': '/image/edit/blur_background',
    'bria_erase_foreground': '/image/edit/erase_foreground',
    'bria_expand': '/image/edit/expand',
    'bria_enhance': '/image/edit/enhance',
}

# Bria AI Cinematic Models - https://engine.prod.bria-api.com/v2
BRIA_CINEMATIC_MODELS = {
    # Video Editing
    'bria_video_erase': '/video/edit/erase',
    'bria_video_upscale': '/video/edit/increase_resolution',
    'bria_video_remove_bg': '/video/edit/remove_background',
    
    # Video Segmentation
    'bria_video_mask_prompt': '/video/segment/mask_by_prompt',
    'bria_video_mask_keypoints': '/video/segment/mask_by_key_points',
    'bria_video_foreground_mask': '/video/generate/foreground_mask',
}

# Custom Cloudflare Workers AI Models
# Endpoint URL is stored in Supabase (api_key field) and rotated on limit
CUSTOM_MODELS = {
    'flux-fast-custom':       'flux_fast',        # FLUX 1 Schnell, bulk generation
    'sdxl-fast-custom':       'sdxl_fast',        # SDXL Lightning, preview engine
    'flux2-klein-custom':     'flux_2_klein',     # FLUX 2 Klein 4B, balanced quality
    'flux2-klein-9b-custom':  'flux_2_klein_9b',  # FLUX 2 Klein 9B, high quality
    'flux-dev-custom':        'flux_dev',         # FLUX Dev, high quality
    'flux-pro-custom':        'flux_pro',         # FLUX Pro, ultra quality (limited use)
    'sdxl-custom':            'sdxl',             # SDXL Base, balanced, supports img2img
    'leonardo-custom':        'leonardo',         # Lucid Origin, artistic quality
    'phoenix-custom':         'phoenix',          # Phoenix 1.0, professional quality
}

# Steps configuration per model and aspect ratio for vision-custom provider
# Based on MASTER MODEL PLAN - optimized steps for quality/speed balance
CUSTOM_MODEL_STEPS = {
    'flux_fast':        {'1:1': 8,  '16:9': 10, '9:16': 10},  # Bulk generator
    'sdxl_fast':        {'1:1': 6,  '16:9': 6,  '9:16': 6},   # Preview engine
    'flux_2_klein':     {'1:1': 22, '16:9': 24, '9:16': 24},  # Balanced quality (4B)
    'flux_2_klein_9b':  {'1:1': 24, '16:9': 26, '9:16': 26},  # High quality (9B)
    'flux_dev':         {'1:1': 28, '16:9': 30, '9:16': 30},  # High quality
    'flux_pro':         {'1:1': 30, '16:9': 32, '9:16': 32},  # Ultra quality (premium)
    'sdxl':             {'1:1': 35, '16:9': 38, '9:16': 38},  # Classic quality
    'leonardo':         {'1:1': 25, '16:9': 28, '9:16': 28},  # Artistic
    'phoenix':          {'1:1': 25, '16:9': 28, '9:16': 28},  # Cinematic
}

# Only these 3 aspect ratios supported for vision-custom models
CUSTOM_MODEL_RATIOS = ['1:1', '16:9', '9:16']

# Infip.pro API Models - https://api.infip.pro/v1
# Note: Async models (z-image-turbo, qwen) require polling
INFIP_MODELS = {
    # New naming convention with -infip suffix (used by frontend)
    'z-image-turbo': 'z-image-turbo',  # Fast async model
    'qwen': 'qwen',  # Qwen async model
    'flux2-klein-9b': 'flux2-klein-9b',  # FLUX 2 Klein 9B
    'flux2-dev': 'flux2-dev',  # FLUX 2 Dev
    'phoenix-infip': 'phoenix',  # Phoenix 1.0 (with -infip suffix)
    'lucid-origin': 'lucid-origin',  # Lucid Origin
    'sdxl-infip': 'sdxl',  # SDXL (with -infip suffix)
    'sdxl-lite-infip': 'sdxl-lite',  # SDXL Lite (with -infip suffix)
    'img3': 'img3',  # Imagen 3
    'img4': 'img4',  # Imagen 4
    'flux-schnell-infip': 'flux-schnell',  # FLUX Schnell (with -infip suffix)
    
    # Legacy naming (for backward compatibility - these will be mapped above anyway)
    'phoenix': 'phoenix',  # Phoenix 1.0
    'sdxl': 'sdxl',  # SDXL
    'sdxl-lite': 'sdxl-lite',  # SDXL Lite
    'flux-schnell': 'flux-schnell',  # FLUX Schnell
}

# deAPI Models - https://api.deapi.ai
# Note: All models require async polling
DEAPI_MODELS = {
    'z-image-turbo-deapi': 'ZImageTurbo_INT8',  # Fast photorealistic model (INT8 quantized)
    'flux-schnell-deapi': 'Flux1schnell',  # Fast iteration model (20 steps)
    'ltx2-19b-dist-fp8-deapi': 'Ltx2_19B_Dist_FP8',  # LTX-2 19B Distilled FP8 (img2video)
    'ltx2-3-22b-dist-int8-deapi': 'Ltx2_3_22B_Dist_INT8',  # LTX-2.3 22B Distilled INT8 (txt2video)
}

# Leonardo AI Models - https://cloud.leonardo.ai/api/rest/v1 & v2
# Note: All models require async polling (similar to Infip/deAPI)
LEONARDO_MODELS = {
    # V2 Image Models
    'ideogram-3.0': 'ideogram-v3.0',  # Text rendering specialist (v2)
    'nano-banana-pro-leonardo': 'gemini-image-2',  # Nano Banana Pro with image guidance (v2)
    
    # V2 Video Models
    'seedance-1.0-pro-fast': 'seedance-1.0-pro-fast',  # Fast video generation (v2)
    'seedance-1.0-lite': 'seedance-1.0-lite',  # Lite video generation (v2)
    'seedance-1.0-pro': 'seedance-1.0-pro',  # Pro video generation (v2)
    'hailuo-2.3-fast': 'hailuo-2.3-fast',  # Image-to-video fast generation (v2)
    
    # V1 Legacy Video Models
    'motion-2.0': 'motion-2.0',  # Motion 2.0 image-to-video (v1 legacy)
    'motion-2.0-fast': 'motion-2.0-fast',  # Motion 2.0 Fast image-to-video (v1 legacy)
}

# Stability AI Models - https://api.stability.ai/v2beta/stable-image
# Note: Fast Upscaler costs only 2 credits (vs 40 for Conservative)
STABILITYAI_MODELS = {
    'stability-upscale-fast': 'fast',  # Fast upscaler (4x resolution in ~1s, 2 credits)
}

# Picsart Ultra Upscale Models - https://api.picsart.io/tools/1.0
PICSART_MODELS = {
    'picsart-ultra-upscale': 'ultra',  # Ultra upscale (async, AI-enhanced)
    'picsart-upscale': 'normal',       # Normal upscale (sync, fast)
}

# Clipdrop Image Upscaling Models - https://clipdrop-api.co
CLIPDROP_MODELS = {
    'clipdrop-upscale': 'upscale',  # 2x upscale
    'clipdrop-expand':  'uncrop',   # outpainting / image expansion
}

# Frenix Image Models - https://api.frenix.sh/v1
FRENIX_IMAGE_MODELS = {
    # Existing models
    'frenix-dirtberry':  'provider-2/dirtberry',
    'frenix-flux-2-pro': 'provider-2/flux-2-pro',
    # New Frenix models
    'frenix-z-image': 'provider-2/z-image',
    'frenix-imagen-2': 'provider-2/imagen-2',
    'frenix-imagen-4': 'provider-2/imagen-4',
    'frenix-flux-2-flex': 'provider-2/flux-2-flex',
    'frenix-flux-2-dev': 'provider-2/flux-2-dev',
    'frenix-flux-klein-4b': 'provider-2/flux-klein-4b',
    'frenix-flux-klein-9b': 'provider-2/flux-klein-9b',
}

# AICC Image Models - https://api.ai.cc/v1
AICC_IMAGE_MODELS = {
    'gemini-25-flash-aicc': 'gemini-2.5-flash-image-preview',
}

# AICC Video Models - https://api.ai.cc/v1
AICC_VIDEO_MODELS = {
    'wan22-i2v-plus-aicc': 'wan2.2-i2v-plus',
}

# Felo AI Models - https://openapi.felo.ai/v2
# nano-banana-2 = Gemini 2.5 Flash Image; supports text-to-image and image editing
FELO_MODELS = {
    'nano-banana-2': 'nano-banana-2',
}

# Gemini Image Models - https://ai.google.dev/gemini-api/docs/vision
# Gemini 2.5 Flash Image - supports text-to-image and image editing
GEMINI_IMAGE_MODELS = {
    'gemini-25-flash-image': 'gemini-2.5-flash-image',
}

# Gemini Web API Models - https://github.com/HanaokaYuzu/Gemini-API (reverse-engineered)
# Uses dual cookies (secure_1psid + secure_1psidts) stored as JSON in api_key field
# Supports text-to-image and image editing with natural language prompts
# Available models from API: gemini-3-pro, gemini-3-flash, gemini-3-flash-thinking, etc.
GEMINI_WEB_API_MODELS = {
'gemini-2.5-flash-image-web': 'gemini-3-flash', # Map to gemini-3-flash (Nano Banana)
'gemini-3.1-flash-image-web': 'gemini-3-flash', # Map to gemini-3-flash (Nano Banana 2 - default)
'gemini-1.5-flash-web': 'gemini-3-flash', # Map to gemini-3-flash
'gemini-2.0-flash-web': 'gemini-3-flash', # Map to gemini-3-flash
'gemini-2.5-pro-web': 'gemini-3-pro', # Map to gemini-3-pro (Pro quality)
'gemini-3-pro-web': 'gemini-3-pro', # Best quality model ✅
}

# On-Demand API Models - https://api.on-demand.io
# Uses API key + workflow ID with webhook callback for result delivery
ONDEMAND_MODELS = {
'nano-banana-ondemand': 'nano-banana',
'nano-banana-2-ondemand': 'nano-banana-2',
}

def generate_with_gemini(prompt, model, aspect_ratio, api_key, input_image_url=None, job_type="image", duration=5):
    """
    Generate or edit an image using Google Gemini API.
    
    API: POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
    
    Text-to-image: prompt only in the parts array.
    Image editing: input_image_url can be a single URL string or a list of up to 3 URLs.
    Multiple images are all sent as inlineData parts, enabling:
      - Multi-image fusion (combine product from image 1 into background from image 2)
      - Character consistency (multiple angles of same character)
      - Style reference (subject from one image, style from another)
    
    Limits: Up to 3 input images; total request size (all images + text) <= 20MB.
    """
    import base64 as _base64
    import io as _io
    from PIL import Image as _Image
    
    gemini_model = GEMINI_IMAGE_MODELS.get(model, model)
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
    # Gemini API requires the key as a query parameter, NOT as a Bearer token
    HEADERS = {
        "Content-Type": "application/json",
    }
    
    # Build parts - always include the prompt
    parts = [{"text": prompt}]
    
    # If input image(s) provided, normalize to list and convert each to base64
    if input_image_url:
        # Normalize: accept single URL string or list of URLs (up to 3)
        input_urls = input_image_url if isinstance(input_image_url, list) else [input_image_url]
        input_urls = [u for u in input_urls if u]  # filter out None/empty
        input_urls = input_urls[:3]  # Gemini 2.0/2.5 Flash supports up to 3 input images
        
        for idx, img_url in enumerate(input_urls):
            try:
                # Download image
                img_response = requests.get(img_url, timeout=30)
                img_response.raise_for_status()
                
                # Determine format from URL/content
                content_type = img_response.headers.get('Content-Type', 'image/png')
                if 'jpeg' in content_type.lower() or 'jpg' in content_type.lower():
                    mime_type = "image/jpeg"
                elif 'png' in content_type.lower():
                    mime_type = "image/png"
                elif 'webp' in content_type.lower():
                    mime_type = "image/webp"
                else:
                    mime_type = "image/png"
                
                # Convert to base64
                b64_data = _base64.b64encode(img_response.content).decode('utf-8')
                parts.append({
                    "inlineData": {
                        "mimeType": mime_type,
                        "data": b64_data
                    }
                })
            except Exception as e:
                raise Exception(f"Failed to process input image {idx + 1} for Gemini: {str(e)}")
    
    # Normalize aspect ratio to supported values
    # gemini-2.0-flash-preview-image-generation supports: 1:1, 3:4, 4:3, 9:16, 16:9
    supported_ratios = {"1:1", "3:4", "4:3", "9:16", "16:9"}
    gemini_aspect = aspect_ratio if aspect_ratio in supported_ratios else "1:1"
    
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
        },
    }
    
    # API key passed as query parameter (Gemini REST API requirement)
    url = f"{BASE_URL}/models/{gemini_model}:generateContent?key={api_key}"
    
    try:
        resp = requests.post(url, headers=HEADERS, json=payload, timeout=180)
        
        if resp.status_code != 200:
            raise Exception(f"Gemini API error {resp.status_code}: {resp.text}")
        
        result = resp.json()
        
        # Extract base64 image from response
        for candidate in result.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                inline = part.get("inlineData", {})
                if inline.get("data"):
                    return {
                        "success": True,
                        "data": inline["data"],
                        "type": "image",
                        "is_base64": True
                    }
        
        raise Exception("Gemini response contained no image data")
        
    except requests.exceptions.Timeout:
        raise Exception("Gemini API request timed out")
    except requests.exceptions.RequestException as e:
        raise Exception(f"Gemini API request failed: {str(e)}")

# Vercel AI Gateway Models - https://ai-gateway.vercel.sh/v1
# xAI Grok Imagine models via Vercel AI Gateway (separate from KIE-based grok models)
VERCEL_AI_GATEWAY_MODELS = {
    # Video models (cinematic-vercel) - xai/grok-imagine-video
    'grok-text-to-video-2': 'xai/grok-imagine-video',    # text-to-video (numbered to differ from KIE grok)
    'grok-image-to-video-2': 'xai/grok-imagine-video',   # image-to-video (numbered to differ from KIE grok)
    # Image models (vision-vercel) - xai/grok-imagine-image (normal, not pro)
    'grok-imagine-image': 'xai/grok-imagine-image',       # image generation
}

ENDPOINT_IMAGE_INPUT_SUPPORT = {
    'replicate': {
        'supported': True,
        'notes': 'Per-model: imagen-4, flux-kontext-pro, flux-1.1-pro, ideogram-v3-turbo, flux-dev support optional image input. topazlabs/image-upscale, sczhou/codeformer, tencentarc/gfpgan REQUIRE image input.',
        'models_requiring_image': ['topazlabs/image-upscale', 'sczhou/codeformer', 'tencentarc/gfpgan'],
        'models_supporting_image': ['google/imagen-4', 'black-forest-labs/flux-kontext-pro', 'black-forest-labs/flux-1.1-pro', 'ideogram-ai/ideogram-v3-turbo', 'black-forest-labs/flux-dev', 'minimax/video-01'],
    },
    'pixazo': {
        'supported': False,
        'notes': 'flux-1-schnell is text-to-image only. No image input accepted.',
    },
    'huggingface': {
        'supported': True,
        'requires_image': True,
        'notes': 'IllusionDiffusion REQUIRES a control image input.',
    },
    'rapidapi': {
        'supported': True,
        'notes': 'Passes image_urls array to API. image-to-image guidance is provider-dependent.',
    },
    'a4f': {
        'supported': False,
        'notes': 'OpenAI-compatible /images/generations is text-to-image only. No image editing endpoint currently exposed.',
    },
    'kie': {
        'supported': True,
        'notes': 'Image models accept input_urls. Video models (kling, grok) accept image_urls or input_urls for image-to-video.',
    },
    'removebg': {
        'supported': True,
        'requires_image': True,
        'notes': 'REQUIRES input image. Returns image with background removed.',
    },
    'bria_vision': {
        'supported': True,
        'notes': 'Generation models (bria_image_generate*) do not need image. Editing models (bria_gen_fill, bria_erase, bria_remove_background, etc.) require image input.',
        'models_requiring_image': ['bria_gen_fill', 'bria_erase', 'bria_remove_background', 'bria_replace_background', 'bria_blur_background', 'bria_erase_foreground', 'bria_expand', 'bria_enhance'],
    },
    'bria_cinematic': {
        'supported': True,
        'requires_image': True,
        'notes': 'All Bria Cinematic models REQUIRE video input (passed as input_image_url).',
    },
    'custom': {
        'supported': True,
        'notes': 'Only sdxl-custom supports img2img via POST /img2img. Other models are text-to-image only.',
        'models_supporting_image': ['sdxl-custom'],
        'models_not_supporting_image': ['flux-fast-custom', 'sdxl-fast-custom', 'leonardo-custom', 'phoenix-custom'],
    },
    'infip': {
        'supported': False,
        'notes': 'All Infip models use text-to-image via /images/generations only.',
    },
    'deapi': {
        'supported': False,
        'notes': 'txt2img endpoint is text-to-image only. deAPI has img2img endpoint but it is not integrated.',
    },
    'leonardo': {
        'supported': True,
        'notes': 'ideogram-3.0 is text-to-image only. nano-banana-pro-leonardo supports up to 6 image references. All video models (seedance, hailuo, motion-2.0) REQUIRE image input for image-to-video.',
        'models_requiring_image': ['seedance-1.0-pro-fast', 'seedance-1.0-lite', 'seedance-1.0-pro', 'hailuo-2.3-fast', 'motion-2.0', 'motion-2.0-fast'],
        'models_supporting_image': ['nano-banana-pro-leonardo'],
        'models_not_supporting_image': ['ideogram-3.0'],
    },
    'stabilityai': {
        'supported': True,
        'requires_image': True,
        'notes': 'stability-upscale-fast REQUIRES input image. Upscales to 4x resolution.',
    },
    'vercel_ai_gateway': {
        'supported': True,
        'notes': 'Image: grok-imagine-image is text-to-image only. Video: grok-image-to-video-2 REQUIRES image input. grok-text-to-video-2 is text-to-video only.',
        'models_requiring_image': ['grok-image-to-video-2'],
        'models_not_supporting_image': ['grok-text-to-video-2', 'grok-imagine-image'],
    },
    'picsart': {
        'supported': True,
        'requires_image': True,
        'notes': 'picsart-ultra-upscale REQUIRES input image. Upscales up to 8x resolution.',
    },
    'clipdrop': {
        'supported': True,
        'requires_image': True,
        'notes': 'clipdrop-upscale REQUIRES input image. Doubles resolution. clipdrop-expand REQUIRES input image. Expands canvas to target aspect ratio.',
    },
    'felo': {
        'supported': True,
        'notes': 'nano-banana-2 supports both text-to-image and image editing. Image passed as base64 data URL.',
        'models_supporting_image': ['nano-banana-2'],
    },
    'gemini': {
        'supported': True,
        'notes': 'gemini-2.5-flash-image supports text-to-image and img2img. Accepts up to 3 input images as inlineData parts. Total request size (all images + text) <= 20MB.',
        'max_input_images': 3,
        'models_supporting_image': ['gemini-25-flash-image'],
        'use_cases': [
            'Multi-image fusion: combine product from image 1 into background from image 2',
            'Character consistency: multiple angles of same character in new scene',
            'Style reference: subject from one image, artistic style from another',
        ],
    },
'geminiwebapi': {
'supported': True,
'notes': 'Gemini Web API (reverse-engineered) supports text-to-image and img2img via natural language. Accepts up to 3 input images. Uses dual cookies (secure_1psid + secure_1psidts). Auto-refresh enabled.',
'max_input_images': 3,
'models_supporting_image': ['gemini-2.5-flash-image-web', 'gemini-3.1-flash-image-web', 'gemini-1.5-flash-web', 'gemini-2.0-flash-web', 'gemini-2.5-pro-web', 'gemini-3-pro-web'],
'use_cases': [
'Multi-image fusion: combine elements from multiple images',
'Style transfer: apply artistic style from reference image',
'Image editing: modify existing image with natural language (use "Edit" or "Modify" in prompt)',
],
},
'ondemand': {
'supported': False,
'notes': 'On-Demand API is text-to-image only. Uses webhook for result delivery. Stores API key + workflow_id as JSON.',
},
}


PROVIDER_ROUTING = {
    'vision-nova': 'replicate',
    'vision-pixazo': 'pixazo',
    'vision-huggingface': 'huggingface',
    'vision-ultrafast': 'rapidapi',
    'vision-atlas': 'a4f',
    'vision-flux': 'kie',
    'vision-removebg': 'removebg',
    'vision-bria': 'bria_vision',
    'vision-custom': 'custom',
    'vision-infip': 'infip',
    'vision-deapi': 'deapi',
    'cinematic-deapi': 'deapi',
    'vision-leonardo': 'leonardo',
    'vision-stabilityai': 'stabilityai',
    'cinematic-nova': 'replicate',
    'cinematic-pro': 'kie',
    'cinematic-bria': 'bria_cinematic',
    'cinematic-leonardo': 'leonardo',
    'cinematic-vercel': 'vercel_ai_gateway',
    'vision-vercel': 'vercel_ai_gateway',
    'vision-picsart': 'picsart',
    'vision-clipdrop': 'clipdrop',
    'vision-frenix':    'frenix',
    'vision-aicc':      'aicc',
    'cinematic-aicc':   'aicc',
'vision-felo': 'felo',
'vision-gemini': 'gemini',
'vision-geminiwebapi': 'geminiwebapi', # NEW: Gemini Web API (reverse-engineered)
'vision-ondemand': 'ondemand', # NEW: On-Demand API (webhook-based)
}


PROVIDER_ALLOWED_IMAGE_FORMATS = {
    'vision-replicate':     ['jpg', 'jpeg', 'png', 'webp', 'gif'],
    'vision-huggingface':   ['jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp'],
    'vision-ultrafast':     ['jpg', 'jpeg', 'png', 'webp'],
    'vision-kie':           ['jpg', 'jpeg', 'png', 'webp'],
    'vision-flux':          ['jpg', 'jpeg', 'png', 'webp'],
    'vision-removebg':      ['jpg', 'jpeg', 'png', 'webp'],
    'vision-bria':          ['jpg', 'jpeg', 'png', 'webp'],
    'vision-custom':        ['jpg', 'jpeg', 'png', 'webp'],
    'vision-leonardo':      ['jpg', 'jpeg', 'png', 'webp', 'gif'],
    'vision-stabilityai':   ['jpg', 'jpeg', 'png', 'webp'],
    'vision-atlas':         [],
    'vision-pixazo':        [],
    'vision-infip':         [],
    'vision-deapi':         [],
    'cinematic-deapi':      ['jpg', 'jpeg', 'png', 'webp'],
    'cinematic-nova':       ['jpg', 'jpeg', 'png', 'webp', 'gif'],
    'cinematic-pro':        ['jpg', 'jpeg', 'png', 'webp'],
    'cinematic-bria':       ['mp4', 'webm', 'mov'],
    'cinematic-leonardo':   ['jpg', 'jpeg', 'png', 'webp', 'gif'],
    'cinematic-vercel':     ['jpg', 'jpeg', 'png', 'webp'],
    'vision-vercel':        [],
    'vision-picsart':       ['jpg', 'jpeg', 'png', 'webp'],
    'vision-clipdrop':      ['jpg', 'jpeg', 'png', 'webp'],
    'vision-frenix':        [],
    'vision-aicc':          ['jpg', 'jpeg', 'png', 'webp'],
    'cinematic-aicc':       ['jpg', 'jpeg', 'png', 'webp'],
    'vision-felo':          [],
'vision-geminiwebapi': ['jpg', 'jpeg', 'png', 'webp'], # NEW: Accepts images for editing (img2img)
'vision-ondemand': [], # NEW: Text-to-image only, webhook-based
}


def get_provider_allowed_formats(provider_key):
    """Return list of allowed image/video formats for the given provider key, or None if unknown."""
    return PROVIDER_ALLOWED_IMAGE_FORMATS.get(provider_key)


def validate_workflow_image_formats(image_url, steps):
    """
    Validate that the user's input image is accepted by every generation step's provider.
    Raises Exception with INVALID_IMAGE_FORMAT prefix on first violation.
    Steps that are type 'input' or have no provider are skipped.
    Only the FIRST generation step typically receives the user image directly;
    subsequent steps receive AI-generated outputs (always jpg/png). We check all
    generation steps for safety and future-proofing.
    """
    if not image_url:
        return

    first_only = True
    for step in steps:
        if step.get('type') != 'generation':
            continue
        provider = step.get('provider')
        if not provider:
            continue
        allowed = PROVIDER_ALLOWED_IMAGE_FORMATS.get(provider)
        if allowed is None:
            continue
        if len(allowed) == 0:
            raise Exception(
                f"INVALID_IMAGE_FORMAT: Provider '{provider}' (step: {step.get('name', '?')}) "
                f"does not accept image input — it is a text-to-image only endpoint."
            )
        step_label = f"[{provider} / step:{step.get('name', '?')}]"
        validate_image_format(image_url, allowed, step_label)
        if first_only:
            break


def get_endpoint_image_support(provider_key):
    endpoint = PROVIDER_ROUTING.get(provider_key)
    if endpoint:
        return ENDPOINT_IMAGE_INPUT_SUPPORT.get(endpoint)
    return None


def get_endpoint_type(provider_key, model_name=None):
    if provider_key and provider_key in PROVIDER_ROUTING:
        return PROVIDER_ROUTING[provider_key]
    if model_name:
        if model_name in REPLICATE_MODELS:
            return 'replicate'
        if model_name in PIXAZO_MODELS:
            return 'pixazo'
        if model_name in HUGGINGFACE_MODELS:
            return 'huggingface'
        if model_name in HUGGINGFACE_SERVERLESS_MODELS:
            return 'huggingface'
        if model_name in RAPIDAPI_MODELS:
            return 'rapidapi'
        if model_name in A4F_MODELS:
            return 'a4f'
        if model_name in KIE_MODELS:
            return 'kie'
        if model_name in REMOVEBG_MODELS:
            return 'removebg'
        if model_name in BRIA_VISION_MODELS:
            return 'bria_vision'
        if model_name in BRIA_CINEMATIC_MODELS:
            return 'bria_cinematic'
        if model_name in CUSTOM_MODELS:
            return 'custom'
        if model_name in INFIP_MODELS:
            return 'infip'
        if model_name in DEAPI_MODELS:
            return 'deapi'
        if model_name in LEONARDO_MODELS:
            return 'leonardo'
        if model_name in STABILITYAI_MODELS:
            return 'stabilityai'
        if model_name in VERCEL_AI_GATEWAY_MODELS:
            return 'vercel_ai_gateway'
        if model_name in PICSART_MODELS:
            return 'picsart'
        if model_name in CLIPDROP_MODELS:
            return 'clipdrop'
        if model_name in FRENIX_IMAGE_MODELS:
            return 'frenix'
        if model_name in AICC_IMAGE_MODELS or model_name in AICC_VIDEO_MODELS:
            return 'aicc'
        if model_name in FELO_MODELS:
            return 'felo'
        if model_name in GEMINI_WEB_API_MODELS:
            return 'geminiwebapi'
        if model_name in ONDEMAND_MODELS:
            return 'ondemand'
    return 'replicate'


def generate_with_replicate(prompt, model, aspect_ratio, api_key, input_image_url=None, job_type="image", duration=5):
    replicate_model = REPLICATE_MODELS.get(model, model)
    
    # Create a new Replicate client with the API key
    client = replicate.Client(api_token=api_key)
    
    input_data = {"prompt": prompt}
    
    if input_image_url:
        validate_image_format(input_image_url, ['jpg', 'jpeg', 'png', 'webp', 'gif'], '[Replicate]')
    
    if model == "google/imagen-4":
        # Imagen-4 only supports: 1:1, 9:16, 16:9, 3:4, 4:3
        # Map unsupported ratios to closest supported ones
        aspect_ratio_map = {
            "3:2": "4:3",  # Map 3:2 to closest landscape (4:3)
            "2:3": "3:4",  # Map 2:3 to closest portrait (3:4)
        }
        mapped_ratio = aspect_ratio_map.get(aspect_ratio, aspect_ratio)
        if aspect_ratio:
            input_data["aspect_ratio"] = mapped_ratio
            if mapped_ratio != aspect_ratio:
                print(f"[Replicate] Note: Imagen-4 doesn't support {aspect_ratio}, using {mapped_ratio}")
        input_data["safety_filter_level"] = "block_medium_and_above"
        if input_image_url:
            input_data["image"] = input_image_url
    
    elif model == "black-forest-labs/flux-kontext-pro":
        if input_image_url:
            input_data["input_image"] = input_image_url
        if aspect_ratio:
            input_data["aspect_ratio"] = aspect_ratio if not input_image_url else "match_input_image"
        input_data["output_format"] = "jpg"
        input_data["safety_tolerance"] = 2
    
    elif model == "black-forest-labs/flux-1.1-pro":
        if aspect_ratio:
            input_data["aspect_ratio"] = aspect_ratio
        input_data["output_format"] = "webp"
        input_data["output_quality"] = 80
        input_data["safety_tolerance"] = 2
        input_data["prompt_upsampling"] = True
        if input_image_url:
            input_data["image"] = input_image_url
    
    elif model == "ideogram-ai/ideogram-v3-turbo":
        input_data["resolution"] = "None"
        input_data["style_type"] = "None"
        if aspect_ratio:
            input_data["aspect_ratio"] = aspect_ratio
        input_data["magic_prompt_option"] = "Auto"
        if input_image_url:
            input_data["image"] = input_image_url
    
    elif model == "black-forest-labs/flux-dev":
        if aspect_ratio:
            input_data["aspect_ratio"] = aspect_ratio
        if input_image_url:
            input_data["image"] = input_image_url
    
    elif model == "luma/reframe-video":
        if input_image_url:
            input_data["video_url"] = input_image_url
        if aspect_ratio:
            input_data["aspect_ratio"] = aspect_ratio
    
    elif model == "minimax/video-01":
        if aspect_ratio:
            input_data["aspect_ratio"] = aspect_ratio
        if input_image_url:
            input_data["first_frame_image"] = input_image_url
    
    elif model == "topazlabs/video-upscale":
        if input_image_url:
            input_data["video"] = input_image_url
    
    elif model == "topazlabs/image-upscale":
        input_data = {}
        if input_image_url:
            input_data["image"] = input_image_url
        else:
            raise Exception("topazlabs/image-upscale requires an input image")
        input_data["enhance_model"] = "Standard V2"
        input_data["upscale_factor"] = "2x"
        input_data["output_format"] = "jpg"
        input_data["subject_detection"] = "None"
        input_data["face_enhancement"] = False
        input_data["face_enhancement_strength"] = 0.8
        input_data["face_enhancement_creativity"] = 0
    
    elif model == "sczhou/codeformer":
        input_data = {}
        if input_image_url:
            input_data["image"] = input_image_url
        else:
            raise Exception("sczhou/codeformer requires an input image")
        input_data["codeformer_fidelity"] = 0.5
        input_data["background_enhance"] = True
        input_data["face_upsample"] = True
        input_data["upscale"] = 2
    
    elif model == "tencentarc/gfpgan":
        input_data = {}
        if input_image_url:
            input_data["img"] = input_image_url
        else:
            raise Exception("tencentarc/gfpgan requires an input image")
        input_data["version"] = "v1.3"
        input_data["scale"] = 2
    
    else:
        if aspect_ratio:
            input_data["aspect_ratio"] = aspect_ratio
        if input_image_url:
            input_data["image"] = input_image_url
        if job_type == "video":
            input_data["duration"] = duration
    
    print(f"[Replicate] Running model: {replicate_model}")
    print(f"[Replicate] Input: {input_data}")
    
    try:
        output = client.run(replicate_model, input=input_data)
        
        print(f"[Replicate] Output type: {type(output)}")
        
        # Check for FileOutput first (has read method)
        if hasattr(output, 'read'):
            content = output.read()
            import base64
            b64_data = base64.b64encode(content).decode('utf-8')
            print(f"[Replicate] Returning base64 data ({len(content)} bytes)")
            return {"success": True, "data": b64_data, "type": job_type, "is_base64": True}
        
        # Check for iterable (list of outputs)
        if hasattr(output, '__iter__') and not isinstance(output, (str, dict)):
            output_list = list(output)
            if output_list:
                first_output = output_list[0]
                if hasattr(first_output, 'read'):
                    content = first_output.read()
                    import base64
                    b64_data = base64.b64encode(content).decode('utf-8')
                    print(f"[Replicate] Returning base64 data ({len(content)} bytes)")
                    return {"success": True, "data": b64_data, "type": job_type, "is_base64": True}
                elif hasattr(first_output, 'url'):
                    return {"success": True, "url": first_output.url, "type": job_type}
                elif isinstance(first_output, str):
                    return {"success": True, "url": first_output, "type": job_type}
                else:
                    return {"success": True, "url": str(first_output), "type": job_type}
        
        if hasattr(output, 'url'):
            return {"success": True, "url": output.url, "type": job_type}
        
        if isinstance(output, str):
            return {"success": True, "url": output, "type": job_type}
        
        if isinstance(output, dict):
            url = output.get("url") or output.get("output") or output.get("video_url")
            if url:
                return {"success": True, "url": url, "type": job_type}
        
        return {"success": True, "url": str(output), "type": job_type}
        
    except Exception as e:
        print(f"[Replicate] Error: {str(e)}")
        raise Exception(f"Replicate generation failed: {str(e)}")


def generate_with_pixazo(prompt, model, aspect_ratio, api_key, input_image_url=None, job_type="image", duration=5):
    """
    Generate images using Pixazo API
    Supports multiple models including flux-1-schnell, SDXL, and Stable Diffusion variants
    
    IMAGE INPUT: NOT SUPPORTED - Pixazo (flux-1-schnell) is text-to-image only.
    """
    pixazo_model = PIXAZO_MODELS.get(model, model)
    
    print(f"[Pixazo] Running model: {pixazo_model}")
    print(f"[Pixazo] Aspect ratio: {aspect_ratio}")
    
    if input_image_url:
        print(f"[Pixazo] WARNING: Image input is not supported by Pixazo (flux-1-schnell is text-to-image only). Input image will be ignored.")
        raise Exception("IMAGE_NOT_SUPPORTED: Pixazo (flux-1-schnell) does not support image input. Please use a text-to-image prompt only, or switch to a different endpoint that supports image-to-image.")
    
    # Map aspect ratios to dimensions
    aspect_map = {
        "1:1": {"width": 1024, "height": 1024},
        "16:9": {"width": 1344, "height": 768},
        "9:16": {"width": 768, "height": 1344},
        "4:3": {"width": 1024, "height": 768},
        "3:4": {"width": 768, "height": 1024},
        "3:2": {"width": 1152, "height": 768},
        "2:3": {"width": 768, "height": 1152},
    }
    
    dimensions = aspect_map.get(aspect_ratio, {"width": 1024, "height": 1024})
    
    # Only flux-1-schnell is supported
    if pixazo_model != 'flux-1-schnell':
        raise Exception(f"Unsupported Pixazo model: {pixazo_model}. Only flux-1-schnell is supported.")
    
    url = "https://gateway.pixazo.ai/flux-1-schnell/v1/getData"
    payload = {
        "prompt": prompt,
        "num_steps": 4,
        "seed": 42,
        "height": dimensions["height"],
        "width": dimensions["width"]
    }
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "Ocp-Apim-Subscription-Key": api_key
    }
    
    print(f"[Pixazo] Request URL: {url}")
    print(f"[Pixazo] Request payload: {payload}")
    print(f"[Pixazo] Request headers: {headers}")
    
    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=120
        )
        
        print(f"[Pixazo] Response status: {response.status_code}")
        
        if response.status_code != 200:
            error_msg = f"Pixazo API error {response.status_code}: {response.text}"
            print(f"[Pixazo] Error: {error_msg}")
            raise Exception(error_msg)
        
        result = response.json()
        print(f"[Pixazo] Response: {result}")
        
        # Extract image URL from response (different endpoints use different field names)
        if "output" in result:
            image_url = result["output"]
            print(f"[Pixazo] Image URL: {image_url}")
            return {"success": True, "url": image_url, "type": job_type}
        elif "imageUrl" in result:
            image_url = result["imageUrl"]
            print(f"[Pixazo] Image URL: {image_url}")
            return {"success": True, "url": image_url, "type": job_type}
        
        raise Exception(f"Pixazo response missing output/imageUrl field. Response: {result}")
        
    except Exception as e:
        print(f"[Pixazo] Error: {str(e)}")
        raise Exception(f"Pixazo generation failed: {str(e)}")


def _pillow_upscale_fallback(image_bytes, scale_str):
    """
    Local Pillow LANCZOS upscale — always works, no external dependency.
    Used when all HF Gradio spaces fail.
    """
    import io
    from PIL import Image
    scale = int(scale_str.replace('x', ''))
    img = Image.open(io.BytesIO(image_bytes)).convert('RGB')
    new_size = (img.width * scale, img.height * scale)
    upscaled = img.resize(new_size, Image.LANCZOS)
    out = io.BytesIO()
    upscaled.save(out, format='JPEG', quality=92)
    return out.getvalue()


def _read_hf_result(result):
    """Extract image bytes from a Gradio predict result (path, url, or dict)."""
    import os
    output_path = result[0] if isinstance(result, (tuple, list)) else result
    if isinstance(output_path, dict):
        output_path = output_path.get('path') or output_path.get('url') or output_path.get('name')

    if isinstance(output_path, str) and os.path.exists(output_path):
        with open(output_path, 'rb') as f:
            return f.read()
    elif isinstance(output_path, str) and output_path.startswith('http'):
        dl = requests.get(output_path, timeout=60)
        if dl.status_code != 200:
            raise Exception(f"Failed to download result: {dl.status_code}")
        return dl.content
    else:
        raise Exception(f"Unexpected result format: {type(output_path)} → {output_path}")


def _try_hf_space_predict(space_id, scale, temp_path, api_type='real-esrgan', timeout=90):
    """
    Try one HF Gradio Space. Returns raw image bytes or raises.
    Enforces a hard timeout via concurrent.futures so a hanging Space doesn't block forever.
    api_type: 'real-esrgan' → predict(image, scale_str)
              'codeformer'  → predict(image, fidelity, upscale)
              'finegrain'   → predict(image, prompt, neg_prompt, steps, guidance, strength, seed)
    """
    import concurrent.futures
    from gradio_client import Client, handle_file

    def _call_real_esrgan():
        client = Client(space_id, verbose=False)
        try:
            return client.predict(
                handle_file(temp_path),
                scale,
                api_name="/predict"
            )
        except Exception:
            return client.predict(
                handle_file(temp_path),
                scale,
            )

    def _call_codeformer():
        client = Client(space_id, verbose=False)
        upscale_int = int(str(scale).replace('x', '')) if str(scale).replace('x', '').isdigit() else 2
        return client.predict(
            handle_file(temp_path),  # image
            0.5,                     # codeformer_fidelity (0=high restoration, 1=original look)
            upscale_int,             # upscale factor
        )

    def _call_finegrain():
        client = Client(space_id, verbose=False)
        result = client.predict(
            handle_file(temp_path),                            # image
            "masterpiece, best quality, highres",              # prompt
            "worst quality, low quality, normal quality",      # negative_prompt
            20,    # num_inference_steps
            6,     # guidance_scale
            0.6,   # controlnet_conditioning_scale
            0,     # seed
        )
        # result[0] = input preview (tiny), result[1] = enhanced output (full size)
        return result[1] if isinstance(result, (list, tuple)) and len(result) > 1 else result

    if api_type == 'codeformer':
        fn = _call_codeformer
    elif api_type == 'finegrain':
        fn = _call_finegrain
    else:
        fn = _call_real_esrgan

    print(f"[HuggingFace Upscale] Connecting to Space: {space_id} (type={api_type}, timeout={timeout}s)")

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn)
        try:
            result = future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise Exception(f"Space {space_id} timed out after {timeout}s")

    print(f"[HuggingFace Upscale] Raw result type: {type(result)}, value: {str(result)[:300]}")
    return _read_hf_result(result)


def generate_with_hf_serverless(model, api_key, input_image_url):
    """
    Upscale/restore an image via HuggingFace Gradio Spaces.
    Falls back to Pillow LANCZOS if the Space fails (always returns a result).

    Models:
      finegrain/finegrain-image-enhancer → 4x upscale (Finegrain Image Enhancer)
      sczhou/CodeFormer   → 2x upscale + face restoration
    """
    import os
    import tempfile

    space_info = HUGGINGFACE_SERVERLESS_MODELS.get(model)
    if not space_info:
        raise Exception(f"Unknown upscale model: {model}")
    space_id, scale, api_type = space_info

    if not input_image_url:
        raise Exception(f"Upscale model '{model}' requires an input image")

    print(f"[HuggingFace Upscale] Model: {model}, space={space_id}, scale={scale}")
    print(f"[HuggingFace Upscale] Downloading input image: {input_image_url}")

    img_response = requests.get(input_image_url, timeout=30)
    if img_response.status_code != 200:
        raise Exception(f"Failed to download input image: {img_response.status_code}")

    image_bytes = img_response.content

    ext = input_image_url.split('?')[0].rsplit('.', 1)[-1].lower()
    if ext not in ('jpg', 'jpeg', 'png', 'webp'):
        ext = 'jpg'

    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f'.{ext}')
    temp_file.write(image_bytes)
    temp_file.close()
    temp_path = temp_file.name

    image_data = None
    try:
        try:
            print(f"[HuggingFace Upscale] Trying space: {space_id}")
            image_data = _try_hf_space_predict(space_id, scale, temp_path, api_type=api_type)
            print(f"[HuggingFace Upscale] Success via {space_id} ({len(image_data)} bytes)")
        except Exception as e:
            print(f"[HuggingFace Upscale] Space {space_id} failed: {str(e)[:150]}")
    finally:
        try:
            os.unlink(temp_path)
        except Exception:
            pass

    if image_data is None:
        print(f"[HuggingFace Upscale] Space failed — using Pillow LANCZOS fallback")
        image_data = _pillow_upscale_fallback(image_bytes, scale)
        print(f"[HuggingFace Upscale] Pillow fallback done ({len(image_data)} bytes)")

    b64_data = base64.b64encode(image_data).decode('utf-8')
    return {"success": True, "data": b64_data, "type": "image", "is_base64": True}


def generate_with_huggingface(prompt, model, aspect_ratio, api_key, input_image_url=None, job_type="image", duration=5):
    """
    Generate images using Hugging Face.
    - Gradio Space for upscale/restore models (finegrain/finegrain-image-enhancer, sczhou/CodeFormer)
    - Gradio Space (gradio_client) for AP123/IllusionDiffusion
    """
    if model in HUGGINGFACE_SERVERLESS_MODELS:
        return generate_with_hf_serverless(model, api_key, input_image_url)

    from gradio_client import Client
    
    hf_model = HUGGINGFACE_MODELS.get(model, model)
    
    print(f"[HuggingFace] Running Space: {hf_model}")
    print(f"[HuggingFace] Aspect ratio: {aspect_ratio}")
    
    # Validate input image
    if not input_image_url:
        raise Exception("IllusionDiffusion requires an input image")
    
    validate_image_format(input_image_url, ['jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp'], '[HuggingFace]')
    
    try:
        import tempfile
        import os
        from gradio_client import handle_file
        
        # Download the input image to a temporary file
        print(f"[HuggingFace] Downloading input image from: {input_image_url}")
        img_response = requests.get(input_image_url, timeout=30)
        
        if img_response.status_code != 200:
            raise Exception(f"Failed to download input image: {img_response.status_code}")
        
        # Save to temporary file
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
        temp_file.write(img_response.content)
        temp_file.close()
        temp_path = temp_file.name
        
        print(f"[HuggingFace] Image saved to: {temp_path}")
        
        # Create Gradio client for the Space
        print(f"[HuggingFace] Connecting to Space: AP123/IllusionDiffusion")
        client = Client("AP123/IllusionDiffusion")
        
        # Enhance the prompt if it's too short
        enhanced_prompt = prompt if len(prompt) > 20 else f"{prompt}, beautiful detailed illustration, high quality, professional artwork"
        
        print(f"[HuggingFace] Calling predict with:")
        print(f"  - Prompt: {enhanced_prompt[:80]}...")
        print(f"  - Image path: {temp_path}")
        
        # Call /inference endpoint with correct parameter order using handle_file
        # Parameters: control_image, prompt, negative_prompt, guidance_scale, controlnet_conditioning_scale,
        #             control_guidance_start, control_guidance_end, upscaler_strength, seed, sampler
        result = client.predict(
            handle_file(temp_path),  # control_image (required) - use handle_file helper
            enhanced_prompt,  # prompt (required)
            "low quality, blurry, bad anatomy, distorted, ugly, deformed",  # negative_prompt
            7.5,  # guidance_scale
            0.8,  # controlnet_conditioning_scale (default value from API)
            0.0,  # control_guidance_start
            1.0,  # control_guidance_end
            1.0,  # upscaler_strength
            -1,  # seed (random)
            "Euler",  # sampler
            api_name="/inference"
        )
        
        # Clean up temp file
        try:
            os.unlink(temp_path)
        except:
            pass
        
        print(f"[HuggingFace] Result type: {type(result)}")
        print(f"[HuggingFace] Result: {result}")
        
        # Result is a tuple: (illusion_diffusion_output, illusion_diffusion_output, last_seed_used)
        # Extract the first output image
        if isinstance(result, (tuple, list)) and len(result) >= 1:
            output_image = result[0]
            print(f"[HuggingFace] Output image: {output_image}")
            
            # If it's a local file path from the temp directory
            if isinstance(output_image, str):
                if output_image.startswith('/tmp/') or output_image.startswith('C:\\') or os.path.exists(output_image):
                    with open(output_image, 'rb') as f:
                        image_data = f.read()
                else:
                    # If it's a URL, download it
                    img_response = requests.get(output_image, timeout=30)
                    if img_response.status_code == 200:
                        image_data = img_response.content
                    else:
                        raise Exception(f"Failed to download image from {output_image}")
                
                b64_data = base64.b64encode(image_data).decode('utf-8')
                print(f"[HuggingFace] Returning base64 data ({len(image_data)} bytes)")
                return {"success": True, "data": b64_data, "type": job_type, "is_base64": True}
            else:
                raise Exception(f"Unexpected output image type: {type(output_image)}")
        else:
            raise Exception(f"Unexpected result format: {result}")
        
    except Exception as e:
        print(f"[HuggingFace] Error: {str(e)}")
        import traceback
        traceback.print_exc()
        raise Exception(f"HuggingFace generation failed: {str(e)}")


def generate_with_rapidapi(prompt, model, aspect_ratio, api_key, input_image_url=None, job_type="image", duration=5):
    """
    Generate images using RapidAPI's Ultra Fast Nano Banana model
    This is a fast and lightweight image generation API.
    Handles both URL and base64-encoded image responses.
    Supports multiple reference images via input_image_url parameter (can be a single URL or list of URLs).
    """
    rapidapi_key = api_key

    ASYNC_RAPIDAPI_MODELS = {
        'flux-nano-banana': ("flux-api-4-custom-models-100-style.p.rapidapi.com", "Flux Nano Banana"),
        'nano-banana-gemini': ("nano-banana-pro-google-gemini-free1.p.rapidapi.com", "Nano Banana Gemini"),
    }

    if model in ASYNC_RAPIDAPI_MODELS:
        rapidapi_host, model_display = ASYNC_RAPIDAPI_MODELS[model]
        endpoint_path = "/create-v9"
        payload = {
            "prompt": prompt,
        }
        if aspect_ratio:
            payload["aspect_ratio"] = aspect_ratio
        if input_image_url:
            validate_image_format(input_image_url, ['jpg', 'jpeg', 'png', 'webp'], '[RapidAPI]')
            if isinstance(input_image_url, list):
                payload["images"] = input_image_url
            else:
                payload["images"] = [input_image_url]
        is_async = True
    else:
        rapidapi_host = "ultra-fast-nano-banana-22.p.rapidapi.com"
        endpoint_path = "/index.php"
        payload = {
            "prompt": prompt,
        }
        if input_image_url:
            validate_image_format(input_image_url, ['jpg', 'jpeg', 'png', 'webp'], '[RapidAPI]')
            if isinstance(input_image_url, list):
                payload["image_urls"] = input_image_url
            else:
                payload["image_urls"] = [input_image_url]
        model_display = "Ultra Fast Nano Banana"
        is_async = False

    api_url = f"https://{rapidapi_host}{endpoint_path}"

    headers = {
        "x-rapidapi-host": rapidapi_host,
        "x-rapidapi-key": rapidapi_key,
        "Content-Type": "application/json"
    }

    print(f"[RapidAPI] Running model: {model_display}")
    print(f"[RapidAPI] Host: {rapidapi_host}")
    print(f"[RapidAPI] Prompt: {prompt}")
    if input_image_url:
        if isinstance(input_image_url, list):
            print(f"[RapidAPI] Reference Images: {len(input_image_url)} images")
            for idx, img_url in enumerate(input_image_url, 1):
                print(f"[RapidAPI]   Image {idx}: {img_url[:100]}...")
        else:
            print(f"[RapidAPI] Reference Image: {input_image_url}")

    try:
        response = requests.post(
            api_url,
            headers=headers,
            json=payload,
            timeout=120
        )

        print(f"[RapidAPI] Response status: {response.status_code}")

        if response.status_code != 200:
            error_msg = f"RapidAPI error {response.status_code}: {response.text}"
            print(f"[RapidAPI] Error: {error_msg}")
            raise Exception(error_msg)

        result = response.json()
        print(f"[RapidAPI] Response keys: {result.keys() if isinstance(result, dict) else 'not a dict'}")

        # Async flow: POST returns jobId, poll until completed
        if is_async and isinstance(result, dict) and "jobId" in result:
            job_id = result["jobId"]
            print(f"[RapidAPI] Async job submitted. Job ID: {job_id}")

            poll_url = f"https://{rapidapi_host}{endpoint_path}/job-status?jobId={job_id}"
            max_attempts = 40
            poll_interval = 5

            for attempt in range(max_attempts):
                time.sleep(poll_interval)

                poll_response = requests.get(poll_url, headers=headers, timeout=30)
                print(f"[RapidAPI] Poll {attempt + 1}/{max_attempts}: status {poll_response.status_code}")

                if poll_response.status_code != 200:
                    error_msg = f"RapidAPI polling error {poll_response.status_code}: {poll_response.text}"
                    print(f"[RapidAPI] Error: {error_msg}")
                    raise Exception(error_msg)

                poll_result = poll_response.json()
                status = poll_result.get("status", "")
                progress = poll_result.get("progress", 0)
                print(f"[RapidAPI] Job status: {status}, progress: {progress}%")

                if status in ("failed", "error"):
                    error_msg = poll_result.get("error", poll_result.get("message", poll_result.get("errorMessage", "Unknown error")))
                    print(f"[RapidAPI] Job errored. Full response: {poll_result}")
                    raise Exception(f"RapidAPI job failed: {error_msg}")

                if status == "completed":
                    image_url = (
                        poll_result.get("imageUrl")
                        or poll_result.get("outputUrl")
                        or poll_result.get("image_url")
                        or poll_result.get("url")
                        or poll_result.get("output")
                        or poll_result.get("result")
                    )
                    if not image_url and isinstance(poll_result.get("images"), list) and poll_result["images"]:
                        image_url = poll_result["images"][0]
                    if image_url:
                        print(f"[RapidAPI] Job completed! Image URL: {image_url}")
                        return {"success": True, "url": image_url, "type": "image"}
                    raise Exception(f"RapidAPI job completed but no image URL found. Response: {poll_result}")

            raise Exception(f"RapidAPI job {job_id} timed out after {max_attempts * poll_interval} seconds")

        # Sync flow: response contains image directly
        if isinstance(result, dict):
            base64_data = None
            base64_fields = ["image_base64", "image", "output", "data"]

            for field in base64_fields:
                if field in result and isinstance(result[field], str) and len(result[field]) > 100:
                    if result[field].startswith(('iVBOR', '/9j/', 'data:image')):
                        base64_data = result[field]
                        if base64_data.startswith('data:image'):
                            base64_data = base64_data.split(',')[1]
                        print(f"[RapidAPI] Found base64 data in field '{field}'")
                        break

            if base64_data:
                print(f"[RapidAPI] Detected base64 image response ({len(base64_data)} chars)")
                return {"success": True, "data": base64_data, "type": "image", "is_base64": True}

            image_url = None
            if "image_url" in result:
                image_url = result["image_url"]
            elif "url" in result:
                image_url = result["url"]
            elif "output" in result and isinstance(result["output"], str) and result["output"].startswith('http'):
                image_url = result["output"]
            elif "result" in result and isinstance(result["result"], str) and result["result"].startswith('http'):
                image_url = result["result"]
            elif "imageUrl" in result:
                image_url = result["imageUrl"]
            elif "images" in result and isinstance(result["images"], list) and len(result["images"]) > 0:
                first_image = result["images"][0]
                if isinstance(first_image, str):
                    image_url = first_image
                elif isinstance(first_image, dict) and "url" in first_image:
                    image_url = first_image["url"]

            if image_url:
                print(f"[RapidAPI] Image URL: {image_url}")
                return {"success": True, "url": image_url, "type": "image"}

        raise Exception(f"RapidAPI response missing image data or URL. Response: {result}")

    except requests.exceptions.Timeout:
        error_msg = "RapidAPI request timeout after 120 seconds"
        print(f"[RapidAPI] Error: {error_msg}")
        raise Exception(error_msg)
    except requests.exceptions.ConnectionError as e:
        error_msg = f"RapidAPI connection error: {str(e)}"
        print(f"[RapidAPI] Error: {error_msg}")
        raise Exception(error_msg)
    except Exception as e:
        print(f"[RapidAPI] Error: {str(e)}")
        raise Exception(f"RapidAPI generation failed: {str(e)}")


def generate_with_custom(prompt, model, aspect_ratio, api_key, input_image_url=None, job_type="image", duration=5):
    """
    Generate images using our own Cloudflare Workers AI deployment.
    api_key IS the endpoint URL (e.g. https://my-worker.workers.dev).
    Multiple endpoint URLs are stored in Supabase and rotated on rate limit.

    Endpoints:
    - GET  /generate  — text to image
    - POST /img2img   — image to image (multipart/form-data)

    Models & Steps (per MASTER MODEL PLAN):
    - flux_fast:     1:1=8,  16:9=10, 9:16=10  (bulk)
    - sdxl_fast:     1:1=6,  16:9=6,  9:16=6   (preview)
    - flux_2_klein:  1:1=22, 16:9=24, 9:16=24  (balanced)
    - flux_dev:      1:1=28, 16:9=30, 9:16=30  (high quality)
    - flux_pro:      1:1=30, 16:9=32, 9:16=32  (premium)
    - sdxl:          1:1=35, 16:9=38, 9:16=38  (detailed, supports img2img)
    - leonardo:      1:1=25, 16:9=28, 9:16=28  (artistic)
    - phoenix:       1:1=25, 16:9=28, 9:16=28  (cinematic)
    
    Only 3 aspect ratios supported: 1:1, 16:9, 9:16
    """
    if not api_key:
        raise Exception("vision-custom: endpoint URL is missing. Add it to Supabase Worker1 provider_api_keys for provider 'custom'.")

    cf_model = CUSTOM_MODELS.get(model, 'flux_fast')
    endpoint_url = api_key.rstrip('/')

    # Validate ratio - only 3 ratios supported for vision-custom
    ratio = aspect_ratio if aspect_ratio in CUSTOM_MODEL_RATIOS else "1:1"
    
    # Get steps based on model and ratio from MASTER MODEL PLAN
    model_steps_config = CUSTOM_MODEL_STEPS.get(cf_model, {'1:1': 20, '16:9': 22, '9:16': 22})
    steps = model_steps_config.get(ratio, 20)

    print(f"[Custom] Running model: {cf_model} (from: {model})")
    print(f"[Custom] Endpoint: {endpoint_url}")
    print(f"[Custom] Aspect ratio: {ratio}")
    print(f"[Custom] Steps: {steps}")

    try:
        if input_image_url and cf_model == 'sdxl':
            # img2img via POST /img2img (multipart/form-data)
            validate_image_format(input_image_url, ['jpg', 'jpeg', 'png', 'webp'], '[Custom]')
            print(f"[Custom] img2img mode — fetching input image: {input_image_url}")
            img_response = requests.get(input_image_url, timeout=30)
            if img_response.status_code != 200:
                raise Exception(f"Failed to fetch input image (status {img_response.status_code})")
            image_bytes = img_response.content

            files = {'image': ('image.png', image_bytes, 'image/png')}
            params = {
                'prompt': prompt,
                'model': cf_model,
                'ratio': ratio,
                'steps': steps,
                'quality': 'high',
                'strength': '0.75',
            }
            print(f"[Custom] POST {endpoint_url}/img2img params={params}")
            response = requests.post(
                f"{endpoint_url}/img2img",
                params=params,
                files=files,
                timeout=120
            )
        elif input_image_url:
            raise Exception(f"IMAGE_NOT_SUPPORTED: Custom model '{cf_model}' does not support image input. Only 'sdxl-custom' supports img2img.")
        else:
            # text-to-image via GET /generate
            params = {
                'prompt': prompt,
                'model': cf_model,
                'ratio': ratio,
                'steps': steps,
                'quality': 'high',
            }
            print(f"[Custom] GET {endpoint_url}/generate params={params}")
            response = requests.get(
                f"{endpoint_url}/generate",
                params=params,
                timeout=120
            )

        print(f"[Custom] Response status: {response.status_code}")
        print(f"[Custom] Content-Type: {response.headers.get('Content-Type', 'unknown')}")

        if response.status_code != 200:
            error_msg = f"Custom API error {response.status_code}"
            try:
                error_data = response.json()
                if isinstance(error_data, dict) and 'error' in error_data:
                    error_msg += f": {error_data['error']}"
                else:
                    error_msg += f": {response.text[:200]}"
            except Exception:
                error_msg += f": {response.text[:200]}"
            print(f"[Custom] Error: {error_msg}")
            raise Exception(error_msg)

        content_type = response.headers.get('Content-Type', '')
        if 'image' in content_type or len(response.content) > 1000:
            image_data = response.content
            b64_data = base64.b64encode(image_data).decode('utf-8')
            print(f"[Custom] Success! {len(image_data)} bytes → base64 {len(b64_data)} chars")
            return {"success": True, "data": b64_data, "type": "image", "is_base64": True}
        else:
            raise Exception(f"Custom API returned unexpected content type: {content_type}. Response: {response.text[:200]}")

    except requests.exceptions.Timeout:
        raise Exception("Custom API request timeout after 120 seconds")
    except requests.exceptions.ConnectionError as e:
        raise Exception(f"Custom API connection error: {str(e)}")
    except Exception as e:
        print(f"[Custom] Error: {str(e)}")
        import traceback
        traceback.print_exc()
        raise Exception(f"Custom generation failed: {str(e)}")


def generate_with_infip(prompt, model, aspect_ratio, api_key, input_image_url=None, job_type="image", duration=5):
    """
    Generate images using Infip.pro API (OpenAI-compatible endpoint)
    https://api.infip.pro/v1/images/generations
    
    Supports 11 models:
    - Async models (require polling via /v1/tasks/{task_id}):
      - z-image-turbo: Fast async model
      - qwen: Qwen async model
    - Sync models (return URL directly):
      - flux2-klein-9b: FLUX 2 Klein 9B
      - flux2-dev: FLUX 2 Dev
      - phoenix: Phoenix
      - lucid-origin: Lucid Origin
      - sdxl: SDXL
      - sdxl-lite: SDXL Lite
      - img3: Imagen 3
      - img4: Imagen 4
      - flux-schnell: FLUX Schnell
    
    IMAGE INPUT: NOT SUPPORTED - All Infip models use text-to-image via /images/generations only.
    """
    infip_model = INFIP_MODELS.get(model, model)
    base_url = "https://api.infip.pro/v1"
    
    print(f"[Infip] Running model: {infip_model}")
    print(f"[Infip] Aspect ratio: {aspect_ratio}")
    
    if input_image_url:
        print(f"[Infip] WARNING: Image input is not supported by Infip models (text-to-image only). Input image will be ignored.")
        raise Exception("IMAGE_NOT_SUPPORTED: Infip.pro models (z-image-turbo, qwen, flux2-klein-9b, flux2-dev) do not support image input. These are text-to-image models only. Please use a different endpoint for image-to-image tasks.")
    
    # Map aspect ratios to sizes
    # Infip supports: 1024x1024, 1792x1024, 1024x1792
    aspect_map = {
        "1:1": "1024x1024",
        "16:9": "1792x1024",
        "9:16": "1024x1792",
        "4:3": "1792x1024",  # Map to 16:9 (closest landscape)
        "3:4": "1024x1792",  # Map to 9:16 (closest portrait)
        "3:2": "1792x1024",  # Map to 16:9 (closest landscape)
        "2:3": "1024x1792",  # Map to 9:16 (closest portrait)
    }
    
    size = aspect_map.get(aspect_ratio, "1024x1024")
    
    # Log if using non-standard ratio mapping
    if aspect_ratio not in ["1:1", "16:9", "9:16"]:
        print(f"[Infip] Note: Mapping {aspect_ratio} to closest supported size: {size}")
    
    # Prepare request payload
    payload = {
        "model": infip_model,
        "prompt": prompt,
        "n": 1,
        "size": size,
        "response_format": "url"
    }
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    print(f"[Infip] Request payload: {payload}")
    
    try:
        # Call Infip images endpoint
        response = requests.post(
            f"{base_url}/images/generations",
            headers=headers,
            json=payload,
            timeout=120
        )
        
        print(f"[Infip] Response status: {response.status_code}")
        
        if response.status_code not in [200, 202]:
            error_msg = f"Infip API error {response.status_code}: {response.text}"
            print(f"[Infip] Error: {error_msg}")
            raise Exception(error_msg)
        
        result = response.json()
        print(f"[Infip] Response: {result}")
        
        # Check if this is an async model response (has task_id)
        if "task_id" in result:
            task_id = result["task_id"]
            print(f"[Infip] Async model - polling for task: {task_id}")
            
            # Poll for completion
            max_attempts = 40  # 40 attempts * 3 seconds = 120 seconds max
            poll_interval = 3  # seconds
            
            for attempt in range(max_attempts):
                time.sleep(poll_interval)
                
                poll_response = requests.get(
                    f"{base_url}/tasks/{task_id}",
                    headers=headers,
                    timeout=30
                )
                
                if poll_response.status_code != 200:
                    error_msg = f"Infip polling error {poll_response.status_code}: {poll_response.text}"
                    print(f"[Infip] Error: {error_msg}")
                    raise Exception(error_msg)
                
                poll_result = poll_response.json()
                status = poll_result.get("status")
                
                print(f"[Infip] Poll attempt {attempt + 1}/{max_attempts}: status = {status}")
                
                # Debug: Log full response if status is None or unexpected
                if status not in ["pending", "processing", "completed", "failed"]:
                    print(f"[Infip] WARNING: Unexpected status. Full response: {poll_result}")
                
                # Check if image URL is available (task completed) regardless of status field
                # Some APIs return URL directly without explicit "completed" status
                if "data" in poll_result and isinstance(poll_result["data"], list) and len(poll_result["data"]) > 0:
                    image_url = poll_result["data"][0].get("url")
                    if image_url:
                        print(f"[Infip] Task completed! Image URL: {image_url}")
                        return {"success": True, "url": image_url, "type": "image"}
                
                # Also check for direct url field in response
                if "url" in poll_result and poll_result["url"]:
                    print(f"[Infip] Task completed! Image URL: {poll_result['url']}")
                    return {"success": True, "url": poll_result["url"], "type": "image"}
                
                if status == "failed":
                    error_msg = poll_result.get("error", "Unknown error")
                    raise Exception(f"Infip task failed: {error_msg}")
                
                # Status is still "pending" or "processing", continue polling
            
            # Timeout after max attempts
            raise Exception(f"Infip task {task_id} timed out after {max_attempts * poll_interval} seconds")
        
        # Sync model response (direct URL)
        elif "data" in result and isinstance(result["data"], list) and len(result["data"]) > 0:
            image_url = result["data"][0].get("url")
            if image_url:
                print(f"[Infip] Sync model - Image URL: {image_url}")
                return {"success": True, "url": image_url, "type": "image"}
            
            raise Exception(f"Infip response missing image URL. Response: {result}")
        
        else:
            raise Exception(f"Infip response format unexpected. Response: {result}")
        
    except requests.exceptions.Timeout:
        error_msg = "Infip API request timeout after 120 seconds"
        print(f"[Infip] Error: {error_msg}")
        raise Exception(error_msg)
    except requests.exceptions.ConnectionError as e:
        error_msg = f"Infip API connection error: {str(e)}"
        print(f"[Infip] Error: {error_msg}")
        raise Exception(error_msg)
    except Exception as e:
        print(f"[Infip] Error: {str(e)}")
        import traceback
        traceback.print_exc()
        raise Exception(f"Infip generation failed: {str(e)}")


def generate_with_deapi(prompt, model, aspect_ratio, api_key, input_image_url=None, job_type="image", duration=5):
    """
    Generate images or videos using deAPI (https://api.deapi.ai)
    Image:  https://api.deapi.ai/api/v1/client/txt2img
    Video:  https://api.deapi.ai/api/v1/client/txt2video  (text-to-video)
            https://api.deapi.ai/api/v1/client/img2video  (image-to-video, multipart)

    Supports models (all require async polling):
    - ZImageTurbo_INT8: Fast photorealistic image model (INT8 quantized)
    - Flux1schnell: Fast iteration image model
    - Ltx2_19B_Dist_FP8: LTX-2 19B Distilled FP8 video model (txt2video / img2video)

    Workflow:
    1. POST endpoint → returns request_id
    2. Poll GET /api/v1/client/request-status/{request_id}
    3. Extract result_url when status = "done"
    """
    deapi_model = DEAPI_MODELS.get(model, model)
    base_url = "https://api.deapi.ai/api/v1/client"

    print(f"[deAPI] Running model: {deapi_model}, job_type: {job_type}")
    print(f"[deAPI] Aspect ratio: {aspect_ratio}")

    headers_json = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    headers_auth = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }

    # ── VIDEO GENERATION (cinematic-deapi) ──────────────────────────────────
    if job_type == "video":
        video_aspect_map = {
            "1:1":  (768, 768),
            "16:9": (1024, 576),
            "9:16": (576, 1024),
            "4:3":  (1024, 768),
            "3:4":  (768, 1024),
            "3:2":  (1024, 682),
            "2:3":  (682, 1024),
        }
        width, height = video_aspect_map.get(aspect_ratio, (768, 768))

        effective_duration = max(duration, 8) if duration else 8
        frames = min(int(effective_duration * 24), 240)
        fps = 24
        guidance = 7.5
        seed = -1

        if input_image_url:
            steps = 20
            print(f"[deAPI] Video: image-to-video mode. Downloading input image: {input_image_url}")
            img_response = requests.get(input_image_url, timeout=30)
            if img_response.status_code != 200:
                raise Exception(f"[deAPI] Failed to download input image: {img_response.status_code}")

            form_data = {
                "prompt": prompt,
                "model": deapi_model,
                "width": str(width),
                "height": str(height),
                "guidance": str(guidance),
                "steps": str(steps),
                "frames": str(frames),
                "fps": str(fps),
                "seed": str(seed),
            }
            files = {"first_frame_image": ("frame.jpg", img_response.content, "image/jpeg")}

            print(f"[deAPI] img2video request: model={deapi_model}, size={width}x{height}, frames={frames}")
            response = requests.post(
                f"{base_url}/img2video",
                headers=headers_auth,
                data=form_data,
                files=files,
                timeout=60,
            )
        else:
            steps = 8
            payload = {
                "prompt": prompt,
                "model": deapi_model,
                "width": width,
                "height": height,
                "guidance": guidance,
                "steps": steps,
                "frames": frames,
                "fps": fps,
                "seed": seed,
            }
            print(f"[deAPI] txt2video request: model={deapi_model}, size={width}x{height}, frames={frames}")
            response = requests.post(
                f"{base_url}/txt2video",
                headers=headers_json,
                json=payload,
                timeout=60,
            )

        print(f"[deAPI] Video response status: {response.status_code}")
        if response.status_code != 200:
            raise Exception(f"deAPI video error {response.status_code}: {response.text}")

        result = response.json()
        print(f"[deAPI] Video response: {result}")

        if not ("data" in result and "request_id" in result["data"]):
            raise Exception(f"deAPI video response missing request_id. Response: {result}")

        request_id = result["data"]["request_id"]
        print(f"[deAPI] Video request submitted - polling for request: {request_id}")

        max_attempts = 90
        poll_interval = 5

        for attempt in range(max_attempts):
            time.sleep(poll_interval)
            poll_response = requests.get(
                f"{base_url}/request-status/{request_id}",
                headers=headers_auth,
                timeout=30,
            )
            if poll_response.status_code != 200:
                raise Exception(f"deAPI video polling error {poll_response.status_code}: {poll_response.text}")

            poll_result = poll_response.json()
            status = poll_result.get("data", {}).get("status")
            print(f"[deAPI] Video poll {attempt + 1}/{max_attempts}: status={status}")

            if status == "done":
                result_url = poll_result["data"].get("result_url") or poll_result["data"].get("result")
                if result_url:
                    print(f"[deAPI] Video completed! URL: {result_url}")
                    return {"success": True, "url": result_url, "type": "video"}
                raise Exception(f"deAPI video completed but no URL found. Response: {poll_result}")

            elif status == "error":
                error_msg = poll_result["data"].get("error", "Unknown error")
                raise Exception(f"deAPI video request failed: {error_msg}")

        raise Exception(f"deAPI video request {request_id} timed out after {max_attempts * poll_interval} seconds")

    # ── IMAGE GENERATION (vision-deapi) ─────────────────────────────────────
    if input_image_url:
        print(f"[deAPI] WARNING: Image input is not supported by deAPI txt2img models. Input image will be ignored.")
        raise Exception("IMAGE_NOT_SUPPORTED: deAPI models (ZImageTurbo_INT8, Flux1schnell) do not support image input via the current txt2img endpoint. These are text-to-image models only. Please use a different endpoint for image-to-image tasks.")

    aspect_map = {
        "1:1": (1024, 1024),
        "16:9": (1344, 768),
        "9:16": (768, 1344),
        "4:3": (1024, 768),
        "3:4": (768, 1024),
        "3:2": (1152, 768),
        "2:3": (768, 1152),
    }

    width, height = aspect_map.get(aspect_ratio, (1024, 1024))

    if aspect_ratio not in aspect_map:
        print(f"[deAPI] Note: Using default size 1024x1024 for aspect ratio: {aspect_ratio}")

    if deapi_model == "ZImageTurbo_INT8":
        guidance = 3.5
        steps = 20
    elif deapi_model == "Flux1schnell":
        guidance = 7.5
        steps = 9
    else:
        guidance = 7.5
        steps = 20

    payload = {
        "prompt": prompt,
        "model": deapi_model,
        "width": width,
        "height": height,
        "guidance": guidance,
        "steps": steps,
        "seed": -1,
    }

    print(f"[deAPI] Request payload: {payload}")

    try:
        response = requests.post(
            f"{base_url}/txt2img",
            headers=headers_json,
            json=payload,
            timeout=30,
        )

        print(f"[deAPI] Response status: {response.status_code}")

        if response.status_code != 200:
            error_msg = f"deAPI error {response.status_code}: {response.text}"
            print(f"[deAPI] Error: {error_msg}")
            raise Exception(error_msg)

        result = response.json()
        print(f"[deAPI] Response: {result}")

        if "data" in result and "request_id" in result["data"]:
            request_id = result["data"]["request_id"]
            print(f"[deAPI] Request submitted - polling for request: {request_id}")

            max_attempts = 60
            poll_interval = 2

            for attempt in range(max_attempts):
                time.sleep(poll_interval)

                poll_response = requests.get(
                    f"{base_url}/request-status/{request_id}",
                    headers=headers_json,
                    timeout=30,
                )

                if poll_response.status_code != 200:
                    error_msg = f"deAPI polling error {poll_response.status_code}: {poll_response.text}"
                    print(f"[deAPI] Error: {error_msg}")
                    raise Exception(error_msg)

                poll_result = poll_response.json()

                status = None
                if "data" in poll_result:
                    status = poll_result["data"].get("status")

                print(f"[deAPI] Poll attempt {attempt + 1}/{max_attempts}: status = {status}")

                if status == "done":
                    result_url = poll_result["data"].get("result_url")
                    result_data = poll_result["data"].get("result")

                    image_url = result_url or result_data

                    if image_url:
                        print(f"[deAPI] Request completed! Image URL: {image_url}")
                        return {"success": True, "url": image_url, "type": "image"}

                    raise Exception(f"deAPI request completed but no image URL found. Response: {poll_result}")

                elif status == "error":
                    error_msg = poll_result["data"].get("error", "Unknown error")
                    raise Exception(f"deAPI request failed: {error_msg}")

            raise Exception(f"deAPI request {request_id} timed out after {max_attempts * poll_interval} seconds")

        else:
            raise Exception(f"deAPI response missing request_id. Response: {result}")

    except requests.exceptions.Timeout:
        error_msg = "deAPI request timeout after 30 seconds"
        print(f"[deAPI] Error: {error_msg}")
        raise Exception(error_msg)
    except requests.exceptions.ConnectionError as e:
        error_msg = f"deAPI connection error: {str(e)}"
        print(f"[deAPI] Error: {error_msg}")
        raise Exception(error_msg)
    except Exception as e:
        print(f"[deAPI] Error: {str(e)}")
        import traceback
        traceback.print_exc()
        raise Exception(f"deAPI generation failed: {str(e)}")


def generate_with_a4f(prompt, model, aspect_ratio, api_key, input_image_url=None, job_type="image", duration=5):
    """
    Generate images using A4F API (OpenAI-compatible endpoint)
    A4F provides unified access to image generation models
    
    IMAGE INPUT: NOT SUPPORTED - A4F uses the OpenAI-compatible /images/generations endpoint
    which is text-to-image only. Image editing requires /images/edits (not exposed here).
    """
    a4f_model = A4F_MODELS.get(model, model)
    a4f_base_url = "https://api.a4f.co/v1"
    
    print(f"[A4F] Running model: {a4f_model}")
    print(f"[A4F] Aspect ratio: {aspect_ratio}")
    
    if input_image_url:
        print(f"[A4F] WARNING: Image input is not supported by A4F image generation models (/images/generations is text-to-image only). Input image will be ignored.")
        raise Exception("IMAGE_NOT_SUPPORTED: A4F image generation does not support image input. The /images/generations endpoint is text-to-image only. Please use a different endpoint (e.g., vision-bria, vision-replicate with flux-kontext-pro) for image-to-image tasks.")
    
    # Model-specific size support on A4F
    # Phoenix and SDXL-Lite only support 1024x1024
    SQUARE_ONLY_MODELS = ['provider-4/phoenix', 'provider-4/sdxl-lite']
    
    if a4f_model in SQUARE_ONLY_MODELS:
        # Force 1024x1024 for square-only models
        dimensions = {"width": 1024, "height": 1024}
        if aspect_ratio != "1:1":
            print(f"[A4F] Note: {a4f_model} only supports 1:1 (1024x1024). Forcing square size.")
    else:
        # Other A4F models use OpenAI DALL-E 3 compatible sizes
        # DALL-E 3 supports: 1024x1024, 1792x1024, 1024x1792
        aspect_map = {
            "1:1": {"width": 1024, "height": 1024},
            "16:9": {"width": 1792, "height": 1024},
            "9:16": {"width": 1024, "height": 1792},
            "4:3": {"width": 1792, "height": 1024},   # Map to 16:9 (closest landscape)
            "3:4": {"width": 1024, "height": 1792},   # Map to 9:16 (closest portrait)
            "3:2": {"width": 1792, "height": 1024},   # Map to 16:9 (closest landscape)
            "2:3": {"width": 1024, "height": 1792},   # Map to 9:16 (closest portrait)
        }
        dimensions = aspect_map.get(aspect_ratio, {"width": 1024, "height": 1024})
        
        # Log if using non-standard ratio mapping
        if aspect_ratio not in ["1:1", "16:9", "9:16"]:
            print(f"[A4F] Note: Mapping {aspect_ratio} to closest DALL-E 3 size.")
    
    # Prepare request payload
    payload = {
        "model": a4f_model,
        "prompt": prompt,
        "n": 1,
        "size": f"{dimensions['width']}x{dimensions['height']}"
    }
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    print(f"[A4F] Request payload: {payload}")
    
    try:
        # Call A4F images endpoint
        response = requests.post(
            f"{a4f_base_url}/images/generations",
            headers=headers,
            json=payload,
            timeout=120
        )
        
        print(f"[A4F] Response status: {response.status_code}")
        
        if response.status_code != 200:
            error_msg = f"A4F API error {response.status_code}: {response.text}"
            print(f"[A4F] Error: {error_msg}")
            raise Exception(error_msg)
        
        result = response.json()
        print(f"[A4F] Response: {result}")
        
        # Extract image URL from response
        if "data" in result and len(result["data"]) > 0:
            image_data = result["data"][0]
            
            # Check if it's a URL or base64
            if "url" in image_data:
                image_url = image_data["url"]
                print(f"[A4F] Image URL: {image_url}")
                return {"success": True, "url": image_url, "type": job_type}
            
            elif "b64_json" in image_data:
                b64_data = image_data["b64_json"]
                print(f"[A4F] Base64 data received ({len(b64_data)} chars)")
                return {"success": True, "data": b64_data, "type": job_type, "is_base64": True}
        
        raise Exception("A4F response missing image data")
        
    except Exception as e:
        print(f"[A4F] Error: {str(e)}")
        raise Exception(f"A4F generation failed: {str(e)}")


def generate_with_kie(prompt, model, aspect_ratio, api_key, input_image_url=None, job_type="image", duration=5):
    """
    Generate content using KIE AI API
    Supports both image and video generation via task-based API
    """
    kie_model = KIE_MODELS.get(model, model)
    kie_base_url = "https://api.kie.ai/api/v1"
    
    print(f"[KIE] Running model: {kie_model}")
    print(f"[KIE] Job type: {job_type}")
    print(f"[KIE] Aspect ratio: {aspect_ratio}")
    
    # Map aspect ratios to KIE format
    aspect_map = {
        "1:1": "1:1",
        "16:9": "16:9",
        "9:16": "9:16",
        "4:3": "4:3",
        "3:4": "3:4",
        "3:2": "3:2",
        "2:3": "2:3",
    }
    
    kie_aspect = aspect_map.get(aspect_ratio, "1:1")
    
    # Prepare input based on model type
    input_data = {
        "prompt": prompt,
    }
    
    if input_image_url:
        validate_image_format(input_image_url, ['jpg', 'jpeg', 'png', 'webp'], '[KIE]')
    
    # Image generation models
    if job_type == "image":
        input_data["aspect_ratio"] = kie_aspect
        input_data["resolution"] = "1K"
        
        if input_image_url:
            input_data["input_urls"] = [input_image_url]
    
    # Video generation models
    elif job_type == "video":
        # Grok Imagine models - text-to-video and image-to-video
        if "grok-imagine" in kie_model.lower():
            input_data["duration"] = "6"
            input_data["resolution"] = "480p"
            input_data["mode"] = "normal"
            if input_image_url:
                # image-to-video: use image_urls key (external image input)
                input_data["image_urls"] = [input_image_url]
            else:
                # text-to-video: include aspect_ratio
                input_data["aspect_ratio"] = kie_aspect
        
        # For kling-2.6/image-to-video
        elif "kling" in kie_model.lower():
            input_data["input_urls"] = [input_image_url] if input_image_url else []
            input_data["mode"] = "720p"
        
        else:
            if input_image_url:
                input_data["input_urls"] = [input_image_url]
    
    # Create task payload
    payload = {
        "model": kie_model,
        "input": input_data
    }
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    print(f"[KIE] Request payload: {payload}")
    
    try:
        # Step 1: Create task
        response = requests.post(
            f"{kie_base_url}/jobs/createTask",
            headers=headers,
            json=payload,
            timeout=30
        )
        
        print(f"[KIE] Create task response status: {response.status_code}")
        
        if response.status_code != 200:
            error_msg = f"KIE API error {response.status_code}: {response.text}"
            print(f"[KIE] Error: {error_msg}")
            raise Exception(error_msg)
        
        result = response.json()
        print(f"[KIE] Create task response: {result}")
        
        if result.get("code") != 200:
            raise Exception(f"KIE task creation failed: {result.get('msg', 'Unknown error')}")
        
        task_id = result.get("data", {}).get("taskId")
        if not task_id:
            raise Exception("KIE did not return taskId")
        
        print(f"[KIE] Task created: {task_id}")
        
        # Step 2: Poll for results
        max_attempts = 60
        poll_interval = 5
        
        for attempt in range(max_attempts):
            time.sleep(poll_interval)
            
            query_response = requests.get(
                f"{kie_base_url}/jobs/recordInfo",
                headers=headers,
                params={"taskId": task_id},
                timeout=30
            )
            
            if query_response.status_code != 200:
                print(f"[KIE] Query failed with status {query_response.status_code}")
                continue
            
            query_result = query_response.json()
            
            if query_result.get("code") != 200:
                print(f"[KIE] Query error: {query_result.get('msg')}")
                continue
            
            task_data = query_result.get("data", {})
            state = task_data.get("state")
            
            print(f"[KIE] Task state: {state} (attempt {attempt + 1}/{max_attempts})")
            
            if state == "success":
                result_json_str = task_data.get("resultJson")
                if result_json_str:
                    import json
                    result_json = json.loads(result_json_str)
                    result_urls = result_json.get("resultUrls", [])
                    
                    if result_urls and len(result_urls) > 0:
                        result_url = result_urls[0]
                        print(f"[KIE] Generation successful: {result_url}")
                        return {"success": True, "url": result_url, "type": job_type}
                
                raise Exception("KIE task succeeded but no result URL found")
            
            elif state == "fail":
                fail_msg = task_data.get("failMsg", "Unknown error")
                fail_code = task_data.get("failCode", "")
                raise Exception(f"KIE task failed: {fail_code} - {fail_msg}")
        
        raise Exception(f"KIE task timeout after {max_attempts * poll_interval} seconds")
        
    except Exception as e:
        print(f"[KIE] Error: {str(e)}")
        raise Exception(f"KIE generation failed: {str(e)}")


def generate_with_removebg(prompt, model, aspect_ratio, api_key, input_image_url=None, job_type="image", duration=5):
    """
    Remove background from images using Remove.bg API
    Requires an input image URL
    """
    print(f"[RemoveBG] Running background removal")
    
    # Validate input image
    if not input_image_url:
        raise Exception("Remove.bg requires an input image")
    
    validate_image_format(input_image_url, ['jpg', 'jpeg', 'png', 'webp'], '[RemoveBG]')
    
    url = "https://api.remove.bg/v1.0/removebg"
    
    payload = {
        'image_url': input_image_url,
        'size': 'auto'
    }
    
    headers = {
        'X-Api-Key': api_key
    }
    
    print(f"[RemoveBG] Request URL: {url}")
    print(f"[RemoveBG] Image URL: {input_image_url}")
    
    try:
        response = requests.post(
            url,
            data=payload,
            headers=headers,
            timeout=120
        )
        
        print(f"[RemoveBG] Response status: {response.status_code}")
        
        if response.status_code == 200:
            # Response contains the image with background removed
            image_data = response.content
            b64_data = base64.b64encode(image_data).decode('utf-8')
            print(f"[RemoveBG] Successfully removed background ({len(image_data)} bytes)")
            return {"success": True, "data": b64_data, "type": "image", "is_base64": True}
        else:
            error_msg = f"Remove.bg API error {response.status_code}: {response.text}"
            print(f"[RemoveBG] Error: {error_msg}")
            
            # Check for unknown_foreground error (user-facing, non-retryable)
            if response.status_code == 400:
                try:
                    import json
                    error_data = json.loads(response.text)
                    errors = error_data.get("errors", [])
                    for error in errors:
                        if error.get("code") == "unknown_foreground":
                            # This is a user-facing error - image quality issue, not API issue
                            raise Exception("REMOVEBG_FOREGROUND_ERROR: Could not identify foreground in image. Please use a different image with a clear subject.")
                except json.JSONDecodeError:
                    pass  # If JSON parsing fails, continue with generic error
            
            raise Exception(error_msg)
        
    except Exception as e:
        print(f"[RemoveBG] Error: {str(e)}")
        raise Exception(f"Remove.bg generation failed: {str(e)}")


def generate_with_bria_vision(prompt, model, aspect_ratio, api_key, input_image_url=None, job_type="image", duration=5, **kwargs):
    """
    Generate/edit images using Bria AI Vision API
    Supports both image generation and editing with async processing
    """
    bria_base_url = "https://engine.prod.bria-api.com/v2"
    endpoint_path = BRIA_VISION_MODELS.get(model)
    
    if not endpoint_path:
        raise Exception(f"Unsupported Bria Vision model: {model}")
    
    print(f"[BriaVision] Running model: {model}")
    print(f"[BriaVision] Endpoint: {endpoint_path}")
    
    if input_image_url:
        validate_image_format(input_image_url, ['jpg', 'jpeg', 'png', 'webp'], '[BriaVision]')
    
    headers = {
        "api_token": api_key,
        "Content-Type": "application/json"
    }
    
    # Build request payload based on model type
    payload = {}
    
    # Image Generation Models
    if model in ['bria_image_generate', 'bria_image_generate_lite']:
        payload = {
            "prompt": prompt,
            "num_results": kwargs.get("num_results", 1),
            "sync": False
        }
        
        if aspect_ratio:
            # Map aspect ratios to Bria format
            aspect_map = {
                "1:1": "1:1",
                "16:9": "16:9",
                "9:16": "9:16",
                "4:3": "4:3",
                "3:4": "3:4",
                "21:9": "21:9",
                "9:21": "9:21",
                "3:2": "4:3",  # Map to closest
                "2:3": "3:4",  # Map to closest
            }
            payload["aspect_ratio"] = aspect_map.get(aspect_ratio, "1:1")
        
        if kwargs.get("seed"):
            payload["seed"] = kwargs["seed"]
    
    # Structured Prompt
    elif model == 'bria_structured_prompt':
        payload = {
            "prompt": prompt,
        }
        
        if input_image_url:
            payload["image"] = input_image_url
        elif kwargs.get("image_file"):
            payload["image"] = kwargs["image_file"]
    
    # Generative Fill
    elif model == 'bria_gen_fill':
        payload = {
            "prompt": prompt,
            "sync": False
        }
        
        if input_image_url:
            payload["image"] = input_image_url
        elif kwargs.get("image_file"):
            payload["image"] = kwargs["image_file"]
        
        if kwargs.get("mask_url"):
            payload["mask"] = kwargs["mask_url"]
        elif kwargs.get("mask_file"):
            payload["mask"] = kwargs["mask_file"]
    
    # Erase
    elif model == 'bria_erase':
        payload = {
            "sync": False
        }
        
        if input_image_url:
            payload["image"] = input_image_url
        elif kwargs.get("image_file"):
            payload["image"] = kwargs["image_file"]
        
        if kwargs.get("mask_url"):
            payload["mask"] = kwargs["mask_url"]
        elif kwargs.get("mask_file"):
            payload["mask"] = kwargs["mask_file"]
        elif prompt:
            payload["prompt"] = prompt
    
    # Remove Background
    elif model == 'bria_remove_background':
        payload = {
            "sync": False
        }
        
        if input_image_url:
            payload["image"] = input_image_url
        elif kwargs.get("image_file"):
            payload["image"] = kwargs["image_file"]
    
    # Replace Background
    elif model == 'bria_replace_background':
        payload = {
            "sync": False
        }
        
        if input_image_url:
            payload["image"] = input_image_url
        elif kwargs.get("image_file"):
            payload["image"] = kwargs["image_file"]
        
        if prompt:
            payload["prompt"] = prompt
        elif kwargs.get("background_image_url"):
            payload["background_image"] = kwargs["background_image_url"]
        elif kwargs.get("background_image_file"):
            payload["background_image"] = kwargs["background_image_file"]
    
    # Blur Background
    elif model == 'bria_blur_background':
        payload = {
            "sync": False,
            "blur_strength": kwargs.get("blur_strength", 0.5)
        }
        
        if input_image_url:
            payload["image"] = input_image_url
        elif kwargs.get("image_file"):
            payload["image"] = kwargs["image_file"]
    
    # Erase Foreground
    elif model == 'bria_erase_foreground':
        payload = {
            "sync": False
        }
        
        if input_image_url:
            payload["image"] = input_image_url
        elif kwargs.get("image_file"):
            payload["image"] = kwargs["image_file"]
        
        if kwargs.get("mask_url"):
            payload["mask"] = kwargs["mask_url"]
        elif kwargs.get("mask_file"):
            payload["mask"] = kwargs["mask_file"]
        elif prompt:
            payload["prompt"] = prompt
    
    # Expand
    elif model == 'bria_expand':
        payload = {
            "expansion_direction": kwargs.get("expansion_direction", "all"),
            "sync": False
        }
        
        if input_image_url:
            payload["image"] = input_image_url
        elif kwargs.get("image_file"):
            payload["image"] = kwargs["image_file"]
        
        # aspect_ratio is required by Bria API for expand
        if aspect_ratio:
            # Map aspect ratios to Bria format
            aspect_map = {
                "1:1": "1:1",
                "16:9": "16:9",
                "9:16": "9:16",
                "4:3": "4:3",
                "3:4": "3:4",
                "21:9": "21:9",
                "9:21": "9:21",
                "3:2": "4:3",  # Map to closest
                "2:3": "3:4",  # Map to closest
            }
            payload["aspect_ratio"] = aspect_map.get(aspect_ratio, "1:1")
        
        if kwargs.get("expansion_pixels"):
            payload["expansion_pixels"] = kwargs["expansion_pixels"]
        
        if prompt:
            payload["prompt"] = prompt
    
    # Enhance
    elif model == 'bria_enhance':
        payload = {
            "scale_factor": kwargs.get("scale_factor", 2),
            "sync": False
        }
        
        if input_image_url:
            payload["image"] = input_image_url
        elif kwargs.get("image_file"):
            payload["image"] = kwargs["image_file"]
    
    print(f"[BriaVision] Request payload: {payload}")
    
    try:
        # Step 1: Submit request
        url = f"{bria_base_url}{endpoint_path}"
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=30
        )
        
        print(f"[BriaVision] Response status: {response.status_code}")
        
        if response.status_code not in [200, 201, 202]:
            error_msg = f"Bria API error {response.status_code}: {response.text}"
            print(f"[BriaVision] Error: {error_msg}")
            raise Exception(error_msg)
        
        result = response.json()
        print(f"[BriaVision] Create response: {result}")
        
        # Get request_id and status_url
        request_id = result.get("request_id")
        status_url = result.get("status_url")
        
        if not request_id or not status_url:
            raise Exception("Bria API did not return request_id or status_url")
        
        print(f"[BriaVision] Request ID: {request_id}")
        print(f"[BriaVision] Status URL: {status_url}")
        
        # Step 2: Poll for completion
        max_attempts = 60  # 5 minutes for images
        poll_interval = 5  # 5 seconds
        
        for attempt in range(max_attempts):
            time.sleep(poll_interval)
            
            status_response = requests.get(
                status_url,
                headers={"api_token": api_key},
                timeout=30
            )
            
            if status_response.status_code != 200:
                print(f"[BriaVision] Status check failed: {status_response.status_code}")
                continue
            
            status_result = status_response.json()
            status = status_result.get("status")
            
            print(f"[BriaVision] Status: {status} (attempt {attempt + 1}/{max_attempts})")
            
            if status == "COMPLETED":
                result_data = status_result.get("result", {})
                image_url = result_data.get("image_url")
                
                if image_url:
                    print(f"[BriaVision] Generation successful: {image_url}")
                    return {"success": True, "url": image_url, "type": "image"}
                else:
                    raise Exception("Bria task completed but no image_url found")
            
            elif status == "ERROR":
                error_msg = status_result.get("error", "Unknown error")
                raise Exception(f"Bria task failed: {error_msg}")
            
            elif status == "UNKNOWN":
                raise Exception(f"Bria task in UNKNOWN state. Request ID: {request_id}")
        
        raise Exception(f"Bria task timeout after {max_attempts * poll_interval} seconds")
        
    except Exception as e:
        print(f"[BriaVision] Error: {str(e)}")
        raise Exception(f"Bria Vision generation failed: {str(e)}")


def preprocess_video_for_bria(video_url):
    """
    Adjust video FPS for Bria API using Cloudinary transformations (FPS must be 20-30)
    Returns a new Cloudinary URL with fps_25 transformation applied
    """
    try:
        print(f"[BriaPreprocess] Processing video URL: {video_url}")
        
        # Check if it's a Cloudinary URL
        if 'cloudinary.com' not in video_url:
            print(f"[BriaPreprocess] Not a Cloudinary URL, returning as-is")
            return video_url
        
        # Parse Cloudinary URL to inject fps transformation
        # Example URL: https://res.cloudinary.com/CLOUD_NAME/video/upload/v123456/folder/video.mp4
        # Target URL: https://res.cloudinary.com/CLOUD_NAME/video/upload/fps_25/v123456/folder/video.mp4
        
        parts = video_url.split('/upload/')
        if len(parts) != 2:
            print(f"[BriaPreprocess] Cannot parse Cloudinary URL, returning as-is")
            return video_url
        
        base_url = parts[0]
        path = parts[1]
        
        # Build new URL with fps_25 transformation (converts to 25 FPS)
        new_url = f"{base_url}/upload/fps_25/{path}"
        
        print(f"[BriaPreprocess] Applied FPS transformation (25 FPS)")
        print(f"[BriaPreprocess] New URL: {new_url}")
        
        return new_url
        
    except Exception as e:
        print(f"[BriaPreprocess] Error applying Cloudinary transformation: {e}")
        return video_url


def generate_with_bria_cinematic(prompt, model, aspect_ratio, api_key, input_image_url=None, job_type="video", duration=5, **kwargs):
    """
    Generate/edit videos using Bria AI Cinematic API
    Supports video editing with async processing
    """
    bria_base_url = "https://engine.prod.bria-api.com/v2"
    endpoint_path = BRIA_CINEMATIC_MODELS.get(model)
    
    if not endpoint_path:
        raise Exception(f"Unsupported Bria Cinematic model: {model}")
    
    print(f"[BriaCinematic] Running model: {model}")
    print(f"[BriaCinematic] Endpoint: {endpoint_path}")
    
    if input_image_url:
        validate_image_format(input_image_url, ['mp4', 'webm', 'mov'], '[BriaCinematic]', is_video=True)
    
    headers = {
        "api_token": api_key,
        "Content-Type": "application/json"
    }
    
    # Build request payload based on model type
    payload = {
        "sync": False
    }
    
    # Video Erase
    if model == 'bria_video_erase':
        if input_image_url:
            payload["video"] = input_image_url
        elif kwargs.get("video_file"):
            payload["video"] = kwargs["video_file"]
        else:
            raise Exception("bria_video_erase requires a video input (input_image_url or video_file)")
        
        if kwargs.get("mask_url"):
            payload["mask"] = kwargs["mask_url"]
        elif kwargs.get("mask_file"):
            payload["mask"] = kwargs["mask_file"]
        else:
            raise Exception("bria_video_erase requires a mask (mask_url or mask_file)")
    
    # Video Upscale
    elif model == 'bria_video_upscale':
        payload["scale_factor"] = kwargs.get("scale_factor", 2)
        
        if input_image_url:
            payload["video"] = input_image_url
        elif kwargs.get("video_file"):
            payload["video"] = kwargs["video_file"]
        else:
            raise Exception("bria_video_upscale requires a video input (input_image_url or video_file)")
    
    # Video Remove Background
    elif model == 'bria_video_remove_bg':
        if input_image_url:
            payload["video"] = input_image_url
        elif kwargs.get("video_file"):
            payload["video"] = kwargs["video_file"]
        else:
            raise Exception("bria_video_remove_bg requires a video input (input_image_url or video_file)")
    
    # Video Mask by Prompt (Segmentation)
    elif model == 'bria_video_mask_prompt':
        if input_image_url:
            payload["video"] = input_image_url
        elif kwargs.get("video_file"):
            payload["video"] = kwargs["video_file"]
        else:
            raise Exception("bria_video_mask_prompt requires a video input (input_image_url or video_file)")
        
        if prompt:
            payload["prompt"] = prompt
        else:
            raise Exception("bria_video_mask_prompt requires a prompt")
    
    # Video Mask by Keypoints (Segmentation)
    elif model == 'bria_video_mask_keypoints':
        if input_image_url:
            payload["video"] = input_image_url
        elif kwargs.get("video_file"):
            payload["video"] = kwargs["video_file"]
        else:
            raise Exception("bria_video_mask_keypoints requires a video input (input_image_url or video_file)")
        
        if kwargs.get("key_points"):
            payload["key_points"] = kwargs["key_points"]
        else:
            raise Exception("bria_video_mask_keypoints requires key_points parameter")
    
    # Video Foreground Mask (Segmentation)
    elif model == 'bria_video_foreground_mask':
        if input_image_url:
            payload["video"] = input_image_url
        elif kwargs.get("video_file"):
            payload["video"] = kwargs["video_file"]
        else:
            raise Exception("bria_video_foreground_mask requires a video input (input_image_url or video_file)")
    
    # Preprocess video for models that require 20-30 FPS
    if model in ['bria_video_mask_prompt', 'bria_video_erase'] and "video" in payload and payload["video"]:
        video_url = payload["video"]
        # Only preprocess if it's a URL (not a file path)
        if video_url.startswith("http://") or video_url.startswith("https://"):
            print(f"[BriaCinematic] Preprocessing video for FPS compliance ({model} requires 20-30 FPS)...")
            payload["video"] = preprocess_video_for_bria(video_url)
    
    print(f"[BriaCinematic] Request payload: {payload}")
    
    try:
        # Step 1: Submit request
        url = f"{bria_base_url}{endpoint_path}"
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=30
        )
        
        print(f"[BriaCinematic] Response status: {response.status_code}")
        
        if response.status_code not in [200, 201, 202]:
            error_msg = f"Bria API error {response.status_code}: {response.text}"
            print(f"[BriaCinematic] Error: {error_msg}")
            raise Exception(error_msg)
        
        result = response.json()
        print(f"[BriaCinematic] Create response: {result}")
        
        # Get request_id and status_url
        request_id = result.get("request_id")
        status_url = result.get("status_url")
        
        if not request_id or not status_url:
            raise Exception("Bria API did not return request_id or status_url")
        
        print(f"[BriaCinematic] Request ID: {request_id}")
        print(f"[BriaCinematic] Status URL: {status_url}")
        
        # Step 2: Poll for completion
        max_attempts = 180  # 15 minutes for videos
        poll_interval = 5  # 5 seconds
        
        for attempt in range(max_attempts):
            time.sleep(poll_interval)
            
            status_response = requests.get(
                status_url,
                headers={"api_token": api_key},
                timeout=30
            )
            
            if status_response.status_code != 200:
                print(f"[BriaCinematic] Status check failed: {status_response.status_code}")
                continue
            
            status_result = status_response.json()
            status = status_result.get("status")
            
            print(f"[BriaCinematic] Status: {status} (attempt {attempt + 1}/{max_attempts})")
            
            if status == "COMPLETED":
                result_data = status_result.get("result", {})
                
                # Segmentation models return mask_video_url, others return video_url
                video_url = result_data.get("video_url") or result_data.get("mask_video_url")
                
                if video_url:
                    print(f"[BriaCinematic] Generation successful: {video_url}")
                    return {"success": True, "url": video_url, "type": "video"}
                else:
                    raise Exception("Bria task completed but no video_url or mask_video_url found")
            
            elif status == "ERROR":
                error_msg = status_result.get("error", "Unknown error")
                raise Exception(f"Bria task failed: {error_msg}")
            
            elif status == "UNKNOWN":
                raise Exception(f"Bria task in UNKNOWN state. Request ID: {request_id}")
        
        raise Exception(f"Bria task timeout after {max_attempts * poll_interval} seconds")
        
    except Exception as e:
        print(f"[BriaCinematic] Error: {str(e)}")
        raise Exception(f"Bria Cinematic generation failed: {str(e)}")


def generate_with_leonardo(prompt, model, aspect_ratio, api_key, input_image_url=None, job_type="image", duration=5, **kwargs):
    """
    Generate images/videos using Leonardo AI API
    Supports both v2 image and video generation with async polling
    
    Models:
    - ideogram-3.0: Text rendering specialist (v2 image)
    - nano-banana-pro: Nano Banana Pro with image guidance support (v2 image)
    - seedance-1.0-pro-fast: Fast video generation (v2 video)
    """
    leonardo_model = LEONARDO_MODELS.get(model)
    
    if not leonardo_model:
        raise Exception(f"Unsupported Leonardo model: {model}")
    
    print(f"[Leonardo] Running model: {leonardo_model}")
    print(f"[Leonardo] Job type: {job_type}")
    print(f"[Leonardo] Aspect ratio: {aspect_ratio}")
    
    # Determine if this is image or video generation
    is_video = model in ['seedance-1.0-pro-fast', 'seedance-1.0-lite', 'seedance-1.0-pro', 'hailuo-2.3-fast', 'motion-2.0', 'motion-2.0-fast']
    is_nano_banana = model == 'nano-banana-pro-leonardo'
    is_seedance = model in ['seedance-1.0-pro-fast', 'seedance-1.0-lite', 'seedance-1.0-pro']
    is_hailuo = model == 'hailuo-2.3-fast'
    is_motion = model in ['motion-2.0', 'motion-2.0-fast']
    
    # Video models REQUIRE input images (image-to-video only)
    if is_video and not input_image_url:
        raise Exception("Video models require an input image (image-to-video only). Upload an image first.")
    
    if input_image_url:
        validate_image_format(input_image_url, ['jpg', 'jpeg', 'png', 'webp', 'gif'], '[Leonardo]')
    
    # Map aspect ratios to dimensions
    if is_seedance:
        # Seedance video dimensions (720p - 1080p doesn't support image reference!)
        # Note: 1080p mode doesn't support start_frame images, so we use 720p
        aspect_map = {
            "1:1": {"width": 960, "height": 960},     # Square 720p
            "16:9": {"width": 1248, "height": 704},   # Landscape 720p
            "9:16": {"width": 704, "height": 1248},   # Portrait 720p
            "4:3": {"width": 1120, "height": 832},    # 4:3 720p
            "3:4": {"width": 832, "height": 1120},    # 3:4 720p
            "21:9": {"width": 1504, "height": 640},   # Ultra-wide 720p
            "3:2": {"width": 1152, "height": 768},    # Approximate 720p
            "2:3": {"width": 768, "height": 1152},    # Approximate 720p
        }
    elif is_hailuo:
        # Hailuo 2.3 Fast video dimensions (768p)
        # Aspect ratio must be > 2:5 (0.4) and < 5:2 (2.5)
        aspect_map = {
            "1:1": {"width": 768, "height": 768},     # Square 768p
            "16:9": {"width": 1366, "height": 768},   # Landscape 768p
            "9:16": {"width": 768, "height": 1366},   # Portrait 768p
            "4:3": {"width": 1024, "height": 768},    # 4:3 768p
            "3:4": {"width": 768, "height": 1024},    # 3:4 768p
            "21:9": {"width": 1792, "height": 768},   # Ultra-wide 768p
            "3:2": {"width": 1152, "height": 768},    # 3:2 768p
            "2:3": {"width": 768, "height": 1152},    # 2:3 768p
        }
    elif is_motion:
        # Motion 2.0 and Motion 2.0 Fast video dimensions (720p)
        # V1 Legacy API - supports: 9:16, 16:9, 2:3, 4:5
        aspect_map = {
            "9:16": {"width": 720, "height": 1152},   # Portrait 720p
            "16:9": {"width": 1280, "height": 720},   # Landscape 720p
            "2:3": {"width": 768, "height": 1152},    # 2:3 720p
            "4:5": {"width": 864, "height": 1024},    # 4:5 720p
        }
    elif is_nano_banana:
        # Nano Banana Pro supported dimensions: 0, 672, 768, 832, 864, 896, 1024, 1152, 1184, 1248, 1344
        aspect_map = {
            "1:1": {"width": 1024, "height": 1024},
            "16:9": {"width": 1344, "height": 768},
            "9:16": {"width": 768, "height": 1344},
            "4:3": {"width": 1152, "height": 864},
            "3:4": {"width": 864, "height": 1152},
            "3:2": {"width": 1152, "height": 768},
            "2:3": {"width": 768, "height": 1152},
        }
    else:
        # Image dimensions (Ideogram 3.0)
        aspect_map = {
            "1:1": {"width": 1024, "height": 1024},
            "16:9": {"width": 1792, "height": 1008},
            "9:16": {"width": 1008, "height": 1792},
            "4:3": {"width": 1536, "height": 1152},
            "3:4": {"width": 1152, "height": 1536},
            "3:2": {"width": 1536, "height": 1024},
            "2:3": {"width": 1024, "height": 1536},
        }
    
    dimensions = aspect_map.get(aspect_ratio, {"width": 1024, "height": 1024})
    
    headers = {
        "accept": "application/json",
        "authorization": f"Bearer {api_key}",
        "content-type": "application/json"
    }
    
    # Build request payload based on model type
    if is_video:
        # Video generation (Seedance, Hailuo, or Motion)
        if is_motion:
            # Motion 2.0 uses v1 legacy API
            base_url = "https://cloud.leonardo.ai/api/rest/v1/generations-image-to-video"
            # Allow custom resolution from kwargs, default to 720p
            resolution_mode = kwargs.get('resolution', 'RESOLUTION_720')
            actual_duration = 5  # Motion 2.0 has fixed duration
        else:
            # Seedance and Hailuo use v2 API
            base_url = "https://cloud.leonardo.ai/api/rest/v2/generations"
        
        # Determine valid duration and resolution based on model
        if is_seedance:
            # Seedance: 4, 6, 8, or 10 seconds
            valid_durations = [4, 6, 8, 10]
            actual_duration = min(valid_durations, key=lambda x: abs(x - duration))
            resolution_mode = "RESOLUTION_720"
        elif is_hailuo:
            # Hailuo: 6 or 10 seconds
            valid_durations = [6, 10]
            actual_duration = min(valid_durations, key=lambda x: abs(x - duration))
            resolution_mode = "RESOLUTION_768"  # Default to 768p
        
        # Build payload based on API version
        if is_motion:
            # Motion 2.0 v1 API format
            payload = {
                "isPublic": False,
                "resolution": resolution_mode,
                "prompt": prompt,
                "frameInterpolation": True,
                "promptEnhance": False
            }
        else:
            # V2 API format (Seedance, Hailuo)
            payload = {
                "model": leonardo_model,
                "public": False,
                "parameters": {
                    "prompt": prompt,
                    "width": dimensions["width"],
                    "height": dimensions["height"],
                    "duration": actual_duration,
                    "quantity": 1,
                    "mode": resolution_mode,
                    "prompt_enhance": "OFF",
                }
            }
        
        # Add image reference if provided (image-to-video)
        if input_image_url:
            # Upload image to Leonardo and get image ID
            print(f"[Leonardo] Uploading input image to Leonardo: {input_image_url}")
            try:
                # Step 1: Download the image
                img_response = requests.get(input_image_url, timeout=30)
                if img_response.status_code != 200:
                    raise Exception(f"Failed to download input image: {img_response.status_code}")
                
                # Step 2: Get presigned upload URL from Leonardo
                init_upload_response = requests.post(
                    "https://cloud.leonardo.ai/api/rest/v1/init-image",
                    headers={
                        "accept": "application/json",
                        "authorization": f"Bearer {api_key}",
                        "content-type": "application/json"
                    },
                    json={"extension": "png"},
                    timeout=30
                )
                
                if init_upload_response.status_code != 200:
                    raise Exception(f"Failed to init image upload: {init_upload_response.text}")
                
                upload_data = init_upload_response.json()
                upload_url = upload_data["uploadInitImage"]["url"]
                upload_fields_str = upload_data["uploadInitImage"]["fields"]
                image_id = upload_data["uploadInitImage"]["id"]
                
                # Parse fields JSON string
                import json
                upload_fields = json.loads(upload_fields_str)
                
                print(f"[Leonardo] Got upload URL, uploading image (ID: {image_id})...")
                
                # Step 3: Upload image to presigned URL
                files = {"file": ("image.png", img_response.content, "image/png")}
                upload_response = requests.post(upload_url, data=upload_fields, files=files, timeout=60)
                
                if upload_response.status_code not in [200, 201, 204]:
                    raise Exception(f"Failed to upload image: {upload_response.status_code}")
                
                print(f"[Leonardo] Image uploaded successfully, ID: {image_id}")
                
                # Step 4: Add image to payload
                if is_motion:
                    # Motion 2.0 v1 API format
                    payload["imageId"] = image_id
                    payload["imageType"] = "UPLOADED"
                else:
                    # V2 API format (Seedance, Hailuo)
                    payload["parameters"]["guidances"] = {
                        "start_frame": [
                            {
                                "image": {
                                    "id": image_id,
                                    "type": "UPLOADED"
                                }
                            }
                        ]
                    }
                
            except Exception as e:
                print(f"[Leonardo] Error uploading image: {str(e)}")
                raise Exception(f"Failed to upload input image to Leonardo: {str(e)}")
    
    else:
        # Image generation (Ideogram 3.0 or Nano Banana Pro)
        base_url = "https://cloud.leonardo.ai/api/rest/v2/generations"
        
        payload = {
            "model": leonardo_model,
            "public": False,
            "parameters": {
                "prompt": prompt,
                "width": dimensions["width"],
                "height": dimensions["height"],
                "quantity": 1,
            }
        }
        
        # Nano Banana Pro specific parameters
        if is_nano_banana:
            payload["parameters"]["prompt_enhance"] = kwargs.get("prompt_enhance", "OFF")
            
            # Add style IDs if specified (Nano Banana Pro supports style presets)
            style_ids = kwargs.get("style_ids")
            if style_ids and isinstance(style_ids, list):
                payload["parameters"]["style_ids"] = style_ids
            elif kwargs.get("style_id"):
                payload["parameters"]["style_ids"] = [kwargs.get("style_id")]
            
            # Add seed if specified
            if kwargs.get("seed"):
                payload["parameters"]["seed"] = kwargs["seed"]
            
            # Handle image guidance (up to 6 reference images)
            if input_image_url:
                # Check if input_image_url is a list of URLs or a single URL
                image_urls_to_upload = input_image_url if isinstance(input_image_url, list) else [input_image_url]
                
                print(f"[Leonardo] Adding {len(image_urls_to_upload)} image reference(s) for Nano Banana Pro")
                
                try:
                    # Initialize guidances
                    payload["parameters"]["guidances"] = {"image_reference": []}
                    image_strength = kwargs.get("image_strength", "MID")
                    
                    # Upload each image (limit to 6 as per API docs)
                    for idx, img_url in enumerate(image_urls_to_upload[:6]):
                        print(f"[Leonardo] Uploading reference image {idx + 1}/{len(image_urls_to_upload[:6])}: {img_url}")
                        
                        # Download image
                        img_response = requests.get(img_url, timeout=30)
                        if img_response.status_code != 200:
                            print(f"[Leonardo] Warning: Failed to download image {idx + 1}: {img_response.status_code}")
                            continue
                        
                        # Get presigned upload URL from Leonardo
                        init_upload_response = requests.post(
                            "https://cloud.leonardo.ai/api/rest/v1/init-image",
                            headers={
                                "accept": "application/json",
                                "authorization": f"Bearer {api_key}",
                                "content-type": "application/json"
                            },
                            json={"extension": "png"},
                            timeout=30
                        )
                        
                        if init_upload_response.status_code != 200:
                            print(f"[Leonardo] Warning: Failed to init upload for image {idx + 1}: {init_upload_response.text}")
                            continue
                        
                        upload_data = init_upload_response.json()
                        upload_url = upload_data["uploadInitImage"]["url"]
                        upload_fields_str = upload_data["uploadInitImage"]["fields"]
                        image_id = upload_data["uploadInitImage"]["id"]
                        
                        import json
                        upload_fields = json.loads(upload_fields_str)
                        
                        # Upload image to presigned URL
                        files = {"file": ("image.png", img_response.content, "image/png")}
                        upload_response = requests.post(upload_url, data=upload_fields, files=files, timeout=60)
                        
                        if upload_response.status_code not in [200, 201, 204]:
                            print(f"[Leonardo] Warning: Failed to upload image {idx + 1}: {upload_response.status_code}")
                            continue
                        
                        print(f"[Leonardo] Reference image {idx + 1} uploaded successfully (ID: {image_id})")
                        
                        # Add to guidances
                        payload["parameters"]["guidances"]["image_reference"].append({
                            "image": {
                                "id": image_id,
                                "type": "UPLOADED"
                            },
                            "strength": image_strength
                        })
                    
                    # Check if at least one image was uploaded successfully
                    if len(payload["parameters"]["guidances"]["image_reference"]) == 0:
                        raise Exception("Failed to upload any reference images")
                    
                    print(f"[Leonardo] Successfully uploaded {len(payload['parameters']['guidances']['image_reference'])} reference image(s)")
                    
                except Exception as e:
                    print(f"[Leonardo] Error uploading reference images: {str(e)}")
                    raise Exception(f"Failed to upload reference images to Leonardo: {str(e)}")
            
            # Handle multiple reference images (if provided via reference_images kwarg)
            reference_images = kwargs.get("reference_images")
            if reference_images and isinstance(reference_images, list):
                print(f"[Leonardo] Adding {len(reference_images)} reference images (max 6)")
                if "guidances" not in payload["parameters"]:
                    payload["parameters"]["guidances"] = {"image_reference": []}
                
                # Limit to 6 images as per API docs
                for idx, ref_img in enumerate(reference_images[:6]):
                    if isinstance(ref_img, dict) and "id" in ref_img:
                        # Already uploaded image with ID
                        payload["parameters"]["guidances"]["image_reference"].append({
                            "image": {
                                "id": ref_img["id"],
                                "type": ref_img.get("type", "UPLOADED")
                            },
                            "strength": ref_img.get("strength", "MID")
                        })
        else:
            # Ideogram 3.0 specific parameters
            payload["parameters"]["mode"] = "TURBO"  # TURBO, BALANCED, or QUALITY
            
            # Add style preset if specified (optional)
            style_id = kwargs.get("style_id")
            if style_id:
                payload["parameters"]["style_ids"] = [style_id]
    
    print(f"[Leonardo] Request URL: {base_url}")
    print(f"[Leonardo] Request payload: {payload}")
    
    try:
        # Step 1: Submit generation request
        response = requests.post(
            base_url,
            headers=headers,
            json=payload,
            timeout=30
        )
        
        print(f"[Leonardo] Response status: {response.status_code}")
        
        if response.status_code not in [200, 201]:
            error_msg = f"Leonardo API error {response.status_code}: {response.text}"
            print(f"[Leonardo] Error: {error_msg}")
            raise Exception(error_msg)
        
        result = response.json()
        print(f"[Leonardo] Create response: {result}")
        
        # Handle GraphQL error responses (returned as list)
        if isinstance(result, list):
            error_msg = "Leonardo API error: "
            if len(result) > 0 and "message" in result[0]:
                error_msg += result[0]["message"]
                if "extensions" in result[0]:
                    error_msg += f" (Code: {result[0]['extensions'].get('code', 'unknown')})"
            else:
                error_msg += str(result)
            print(f"[Leonardo] GraphQL Error: {error_msg}")
            raise Exception(error_msg)
        
        # Extract generation ID
        # v2 API (Seedance, Hailuo) returns under "generate" key
        # v1 API (Motion 2.0) returns under "motionVideoGenerationJob" key
        if is_motion:
            generation_id = result.get("motionVideoGenerationJob", {}).get("generationId")
        else:
            generation_id = result.get("generate", {}).get("generationId")
        
        if not generation_id:
            raise Exception("Leonardo API did not return generationId")
        
        print(f"[Leonardo] Generation ID: {generation_id}")
        
        # Step 2: Poll for completion
        max_attempts = 120 if is_video else 60  # 10 min for video, 5 min for image
        poll_interval = 5  # 5 seconds
        
        status_url = f"https://cloud.leonardo.ai/api/rest/v1/generations/{generation_id}"
        
        for attempt in range(max_attempts):
            time.sleep(poll_interval)
            
            status_response = requests.get(
                status_url,
                headers=headers,
                timeout=30
            )
            
            if status_response.status_code != 200:
                print(f"[Leonardo] Status check failed: {status_response.status_code}")
                continue
            
            status_result = status_response.json()
            status = status_result.get("generations_by_pk", {}).get("status")
            
            print(f"[Leonardo] Status: {status} (attempt {attempt + 1}/{max_attempts})")
            
            if status == "COMPLETE":
                generated_items = status_result.get("generations_by_pk", {}).get("generated_images", [])
                
                if not generated_items or len(generated_items) == 0:
                    raise Exception("Leonardo generation completed but no images/videos found")
                
                # Get first item
                first_item = generated_items[0]
                
                if is_video:
                    # For video, check for motionMP4URL
                    video_url = first_item.get("motionMP4URL")
                    if video_url:
                        print(f"[Leonardo] Video generation successful: {video_url}")
                        return {"success": True, "url": video_url, "type": "video"}
                    else:
                        raise Exception("Leonardo video generation completed but no motionMP4URL found")
                else:
                    # For image, get URL
                    image_url = first_item.get("url")
                    if image_url:
                        print(f"[Leonardo] Image generation successful: {image_url}")
                        return {"success": True, "url": image_url, "type": "image"}
                    else:
                        raise Exception("Leonardo image generation completed but no URL found")
            
            elif status == "FAILED":
                raise Exception("Leonardo generation failed")
            
            elif status in ["PENDING", "PROCESSING"]:
                # Continue polling
                continue
            
            else:
                print(f"[Leonardo] Warning: Unknown status '{status}', continuing to poll...")
        
        raise Exception(f"Leonardo generation timeout after {max_attempts * poll_interval} seconds")
        
    except Exception as e:
        print(f"[Leonardo] Error: {str(e)}")
        raise Exception(f"Leonardo generation failed: {str(e)}")


def generate_with_stabilityai(prompt, model, aspect_ratio, api_key, input_image_url=None, job_type="image", **kwargs):
    """
    Generate images using Stability AI Fast Upscaler
    Cost: 2 credits per upscale
    
    Model:
    - stability-upscale-fast: Fast upscaler (4x resolution in ~1s, 2 credits)
    """
    stabilityai_model = STABILITYAI_MODELS.get(model)
    
    if not stabilityai_model:
        raise Exception(f"Unsupported Stability AI model: {model}")
    
    print(f"[StabilityAI] Running model: {stabilityai_model}")
    print(f"[StabilityAI] Job type: {job_type}")
    
    # Upscale models require an input image
    if not input_image_url:
        raise Exception("Stability AI upscale models require an input image")
    
    validate_image_format(input_image_url, ['jpg', 'jpeg', 'png', 'webp'], '[StabilityAI]')
    
    # Fast upscaler endpoint
    endpoint = "https://api.stability.ai/v2beta/stable-image/upscale/fast"
    
    try:
        # Download the input image
        print(f"[StabilityAI] Downloading input image: {input_image_url}")
        img_response = requests.get(input_image_url, timeout=60)
        
        if img_response.status_code != 200:
            raise Exception(f"Failed to download input image: {img_response.status_code}")
        
        image_data = img_response.content
        image_size_mb = len(image_data) / (1024 * 1024)
        print(f"[StabilityAI] Image size: {image_size_mb:.2f} MB")
        
        # Validate image size (max 10MB for Stability AI)
        if image_size_mb > 10:
            raise Exception(f"Input image too large ({image_size_mb:.2f} MB). Maximum is 10MB.")
        
        # Prepare multipart form data
        files = {
            'image': ('image.png', image_data, 'image/png')
        }
        
        data = {
            'prompt': prompt,
            'output_format': kwargs.get('output_format', 'png')
        }
        
        # Add optional parameters
        if 'negative_prompt' in kwargs:
            data['negative_prompt'] = kwargs['negative_prompt']
        
        if 'seed' in kwargs:
            data['seed'] = kwargs['seed']
        
        headers = {
            'authorization': f'Bearer {api_key}',
            'accept': 'image/*'
        }
        
        print(f"[StabilityAI] Sending upscale request to {endpoint}")
        
        response = requests.post(
            endpoint,
            headers=headers,
            files=files,
            data=data,
            timeout=120
        )
        
        if response.status_code == 200:
            print(f"[StabilityAI] Upscale successful")
            
            # Response is the image bytes directly (PNG/JPEG)
            upscaled_image_data = response.content
            image_size_mb = len(upscaled_image_data) / (1024 * 1024)
            print(f"[StabilityAI] Upscaled image size: {image_size_mb:.2f} MB")
            
            # Return raw bytes for Cloudinary upload (not base64)
            return {
                "success": True,
                "is_raw_bytes": True,
                "data": upscaled_image_data,
                "type": "image"
            }
        else:
            error_msg = response.text
            print(f"[StabilityAI] Error {response.status_code}: {error_msg}")
            raise Exception(f"Stability AI upscale failed {response.status_code}: {error_msg}")
    
    except Exception as e:
        print(f"[StabilityAI] Error: {str(e)}")
        raise Exception(f"Stability AI generation failed: {str(e)}")


def _picsart_normal_upscale(api_key, input_image_url):
    """
    Picsart Normal Upscale (sync).
    POST https://api.picsart.io/tools/1.0/upscale
    Returns 200 with data.url immediately.
    """
    endpoint = "https://api.picsart.io/tools/1.0/upscale"
    headers = {
        "X-Picsart-API-Key": api_key,
        "Accept": "application/json",
    }

    print(f"[Picsart] Downloading input image: {input_image_url}")
    img_response = requests.get(input_image_url, timeout=60)
    if img_response.status_code != 200:
        raise Exception(f"Failed to download input image: {img_response.status_code}")

    image_data = img_response.content
    image_size_mb = len(image_data) / (1024 * 1024)
    print(f"[Picsart] Image size: {image_size_mb:.2f} MB")

    ext = input_image_url.split('?')[0].rsplit('.', 1)[-1].lower()
    mime = 'image/jpeg' if ext in ('jpg', 'jpeg') else f'image/{ext}'

    files = {'image': (f'image.{ext}', image_data, mime)}
    data = {'upscale_factor': 8}

    print(f"[Picsart Normal] Sending upscale request (8x)")
    response = requests.post(endpoint, headers=headers, files=files, data=data, timeout=120)

    if response.status_code == 402:
        raise Exception(f"Picsart API error 402: Payment Required - credits exhausted")
    if response.status_code == 401:
        raise Exception(f"Picsart API error 401: Unauthorized - invalid API key")
    if response.status_code != 200:
        raise Exception(f"Picsart API error {response.status_code}: {response.text}")

    result = response.json()
    if result.get('status') != 'success':
        raise Exception(f"Picsart normal upscale failed: {result}")

    url = result.get('data', {}).get('url')
    if not url:
        raise Exception(f"Picsart normal upscale response missing image URL: {result}")

    print(f"[Picsart Normal] Upscale successful: {url}")
    return {"success": True, "url": url, "type": "image"}


def generate_with_picsart(prompt, model, aspect_ratio, api_key, input_image_url=None, job_type="image", **kwargs):
    """
    Upscale an image using Picsart API.
    - picsart-upscale:       POST https://api.picsart.io/tools/1.0/upscale (sync, 200 + url)
    - picsart-ultra-upscale: POST https://api.picsart.io/tools/1.0/upscale/ultra (async 202 + poll)
    Auth: X-Picsart-API-Key header
    Credits: Depletes Picsart API credits. 402 = exhausted.
    """
    if not input_image_url:
        raise Exception("Picsart upscale requires an input image")

    validate_image_format(input_image_url, ['jpg', 'jpeg', 'png', 'webp'], '[Picsart]')

    picsart_type = PICSART_MODELS.get(model, 'ultra')

    if picsart_type == 'normal':
        return _picsart_normal_upscale(api_key, input_image_url)

    endpoint = "https://api.picsart.io/tools/1.0/upscale/ultra"
    headers = {
        "X-Picsart-API-Key": api_key,
        "Accept": "application/json",
    }

    print(f"[Picsart] Downloading input image: {input_image_url}")
    img_response = requests.get(input_image_url, timeout=60)
    if img_response.status_code != 200:
        raise Exception(f"Failed to download input image: {img_response.status_code}")

    image_data = img_response.content
    image_size_mb = len(image_data) / (1024 * 1024)
    print(f"[Picsart] Image size: {image_size_mb:.2f} MB")

    ext = input_image_url.split('?')[0].rsplit('.', 1)[-1].lower()
    mime = 'image/jpeg' if ext in ('jpg', 'jpeg') else f'image/{ext}'

    files = {'image': (f'image.{ext}', image_data, mime)}
    data = {'upscale_factor': 8}

    print(f"[Picsart] Sending ultra upscale request (8x)")
    response = requests.post(endpoint, headers=headers, files=files, data=data, timeout=120)

    if response.status_code == 402:
        raise Exception(f"Picsart API error 402: Payment Required - credits exhausted")
    if response.status_code == 401:
        raise Exception(f"Picsart API error 401: Unauthorized - invalid API key")
    if response.status_code not in (200, 202):
        raise Exception(f"Picsart API error {response.status_code}: {response.text}")

    result = response.json()

    if response.status_code == 200 and result.get('status') == 'success':
        url = result.get('data', {}).get('url')
        if not url:
            raise Exception(f"Picsart response missing image URL: {result}")
        print(f"[Picsart] Upscale successful (sync): {url}")
        return {"success": True, "url": url, "type": "image"}

    transaction_id = result.get('transaction_id')
    if not transaction_id:
        raise Exception(f"Picsart async response missing transaction_id: {result}")

    print(f"[Picsart] Job accepted (202), polling transaction_id: {transaction_id}")
    poll_url = f"https://api.picsart.io/tools/1.0/upscale/ultra/{transaction_id}"
    max_polls = 40
    poll_interval = 5

    for attempt in range(max_polls):
        time.sleep(poll_interval)
        poll_resp = requests.get(poll_url, headers=headers, timeout=30)

        if poll_resp.status_code == 402:
            raise Exception(f"Picsart API error 402: Payment Required - credits exhausted")
        if poll_resp.status_code == 401:
            raise Exception(f"Picsart API error 401: Unauthorized - invalid API key")
        if poll_resp.status_code != 200:
            raise Exception(f"Picsart poll error {poll_resp.status_code}: {poll_resp.text}")

        poll_result = poll_resp.json()
        status = poll_result.get('status')
        print(f"[Picsart] Poll {attempt + 1}/{max_polls}: status={status}")

        if status == 'success':
            url = poll_result.get('data', {}).get('url')
            if not url:
                raise Exception(f"Picsart poll result missing image URL: {poll_result}")
            print(f"[Picsart] Upscale successful: {url}")
            return {"success": True, "url": url, "type": "image"}

        if status in ('failed', 'error'):
            raise Exception(f"Picsart upscale failed: {poll_result}")

    raise Exception(f"Picsart upscale timed out after {max_polls * poll_interval}s")


def generate_with_clipdrop(prompt, model, aspect_ratio, api_key, input_image_url=None, job_type="image", **kwargs):
    """
    Dispatch to the appropriate Clipdrop endpoint based on model.
    - clipdrop-upscale: POST https://clipdrop-api.co/image-upscaling/v1/upscale
    - clipdrop-expand:  POST https://clipdrop-api.co/uncrop/v1
    Auth: x-api-key header
    Credits: 1 credit per successful call. 402 = exhausted.
    """
    if model == 'clipdrop-expand':
        return _clipdrop_expand(api_key=api_key, input_image_url=input_image_url, aspect_ratio=aspect_ratio)

    # default: upscale
    return _clipdrop_upscale(api_key=api_key, input_image_url=input_image_url)


def _clipdrop_upscale(api_key, input_image_url):
    """
    Upscale an image using Clipdrop Image Upscaling API.
    Endpoint: POST https://clipdrop-api.co/image-upscaling/v1/upscale
    Response: binary PNG image
    """
    import io
    from PIL import Image as PILImage

    if not input_image_url:
        raise Exception("Clipdrop upscale requires an input image")

    validate_image_format(input_image_url, ['jpg', 'jpeg', 'png', 'webp'], '[Clipdrop]')

    endpoint = "https://clipdrop-api.co/image-upscaling/v1/upscale"
    headers = {"x-api-key": api_key}

    print(f"[Clipdrop] Downloading input image: {input_image_url}")
    img_response = requests.get(input_image_url, timeout=60)
    if img_response.status_code != 200:
        raise Exception(f"Failed to download input image: {img_response.status_code}")

    image_data = img_response.content
    image_size_mb = len(image_data) / (1024 * 1024)
    print(f"[Clipdrop] Image size: {image_size_mb:.2f} MB")

    pil_img = PILImage.open(io.BytesIO(image_data))
    orig_w, orig_h = pil_img.size

    MAX_INPUT_PIXELS = 16_000_000
    if orig_w * orig_h > MAX_INPUT_PIXELS:
        import math
        scale = math.sqrt(MAX_INPUT_PIXELS / (orig_w * orig_h))
        capped_w = int(orig_w * scale)
        capped_h = int(orig_h * scale)
        print(f"[Clipdrop] Input exceeds 16MP ({orig_w}x{orig_h}), downscaling to {capped_w}x{capped_h} before upscale")
        pil_img = pil_img.resize((capped_w, capped_h), PILImage.LANCZOS)
        buf = io.BytesIO()
        pil_img.save(buf, format='JPEG', quality=95)
        image_data = buf.getvalue()
        orig_w, orig_h = capped_w, capped_h

    target_w = min(orig_w * 16, 4096)
    target_h = min(orig_h * 16, 4096)
    print(f"[Clipdrop] Upscaling {orig_w}x{orig_h} → {target_w}x{target_h}")

    ext = input_image_url.split('?')[0].rsplit('.', 1)[-1].lower()
    mime = 'image/jpeg' if ext in ('jpg', 'jpeg') else f'image/{ext}'

    files = {'image_file': (f'image.{ext}', image_data, mime)}
    data = {'target_width': target_w, 'target_height': target_h}

    print(f"[Clipdrop] Sending upscale request")
    response = requests.post(endpoint, headers=headers, files=files, data=data, timeout=120)

    if response.status_code == 402:
        raise Exception(f"Clipdrop API error 402: Payment Required - credits exhausted")
    if response.status_code == 401:
        raise Exception(f"Clipdrop API error 401: Unauthorized - invalid API key")
    if response.status_code != 200:
        raise Exception(f"Clipdrop API error {response.status_code}: {response.text}")

    upscaled_bytes = response.content
    print(f"[Clipdrop] Upscale successful: {len(upscaled_bytes) / (1024*1024):.2f} MB")
    return {
        "success": True,
        "is_raw_bytes": True,
        "data": upscaled_bytes,
        "type": "image"
    }


def _clipdrop_expand(api_key, input_image_url, aspect_ratio=None):
    """
    Expand an image using Clipdrop Uncrop API.
    Endpoint: POST https://clipdrop-api.co/uncrop/v1
    Computes extend amounts from the target aspect_ratio vs original image dimensions.
    Max 10 megapixels input, max 2000px extend per direction.
    Response: JPEG image.
    """
    import io
    import math
    from PIL import Image as PILImage

    if not input_image_url:
        raise Exception("Clipdrop expand requires an input image")

    validate_image_format(input_image_url, ['jpg', 'jpeg', 'png', 'webp'], '[Clipdrop-Expand]')

    print(f"[Clipdrop-Expand] Downloading input image: {input_image_url}")
    img_response = requests.get(input_image_url, timeout=60)
    if img_response.status_code != 200:
        raise Exception(f"Failed to download input image: {img_response.status_code}")

    image_data = img_response.content
    pil_img = PILImage.open(io.BytesIO(image_data))
    orig_w, orig_h = pil_img.size
    print(f"[Clipdrop-Expand] Original size: {orig_w}x{orig_h}")

    # Cap input to 10 megapixels
    MAX_INPUT_PIXELS = 10_000_000
    if orig_w * orig_h > MAX_INPUT_PIXELS:
        scale = math.sqrt(MAX_INPUT_PIXELS / (orig_w * orig_h))
        capped_w = int(orig_w * scale)
        capped_h = int(orig_h * scale)
        print(f"[Clipdrop-Expand] Downscaling {orig_w}x{orig_h} → {capped_w}x{capped_h} (10MP limit)")
        pil_img = pil_img.resize((capped_w, capped_h), PILImage.LANCZOS)
        buf = io.BytesIO()
        pil_img.save(buf, format='JPEG', quality=95)
        image_data = buf.getvalue()
        orig_w, orig_h = capped_w, capped_h

    # Compute extend amounts from target aspect ratio
    extend_left = extend_right = extend_up = extend_down = 0
    MAX_EXTEND = 2000

    if aspect_ratio and ':' in str(aspect_ratio):
        try:
            ar_parts = str(aspect_ratio).split(':')
            target_ar = float(ar_parts[0]) / float(ar_parts[1])
            current_ar = orig_w / orig_h

            if target_ar > current_ar:
                # Need to expand width
                target_w = int(orig_h * target_ar)
                total_extend = target_w - orig_w
                extend_left = min(total_extend // 2, MAX_EXTEND)
                extend_right = min(total_extend - extend_left, MAX_EXTEND)
            elif target_ar < current_ar:
                # Need to expand height
                target_h = int(orig_w / target_ar)
                total_extend = target_h - orig_h
                extend_up = min(total_extend // 2, MAX_EXTEND)
                extend_down = min(total_extend - extend_up, MAX_EXTEND)
        except Exception as e:
            print(f"[Clipdrop-Expand] Could not parse aspect ratio '{aspect_ratio}': {e}, using default 25% expansion")
            extend_left = extend_right = min(orig_w // 8, MAX_EXTEND)
            extend_up = extend_down = min(orig_h // 8, MAX_EXTEND)
    else:
        # No aspect ratio: expand 25% symmetrically
        extend_left = extend_right = min(orig_w // 8, MAX_EXTEND)
        extend_up = extend_down = min(orig_h // 8, MAX_EXTEND)

    print(f"[Clipdrop-Expand] Extending: left={extend_left} right={extend_right} up={extend_up} down={extend_down}")

    ext = input_image_url.split('?')[0].rsplit('.', 1)[-1].lower()
    mime = 'image/jpeg' if ext in ('jpg', 'jpeg') else f'image/{ext}'

    endpoint = "https://clipdrop-api.co/uncrop/v1"
    headers = {"x-api-key": api_key}
    files = {'image_file': (f'image.{ext}', image_data, mime)}
    data = {
        'extend_left':  extend_left,
        'extend_right': extend_right,
        'extend_up':    extend_up,
        'extend_down':  extend_down,
    }

    print(f"[Clipdrop-Expand] Sending uncrop request")
    response = requests.post(endpoint, headers=headers, files=files, data=data, timeout=120)

    if response.status_code == 402:
        raise Exception("Clipdrop API error 402: Payment Required - credits exhausted")
    if response.status_code == 401:
        raise Exception("Clipdrop API error 401: Unauthorized - invalid API key")
    if response.status_code != 200:
        raise Exception(f"Clipdrop API error {response.status_code}: {response.text}")

    result_bytes = response.content
    print(f"[Clipdrop-Expand] Expand successful: {len(result_bytes) / (1024*1024):.2f} MB")
    return {
        "success": True,
        "is_raw_bytes": True,
        "data": result_bytes,
        "type": "image"
    }


def generate_with_vercel_ai_gateway(prompt, model, aspect_ratio, api_key, input_image_url=None, job_type="image", duration=5):
    """
    Generate image or video using Vercel AI Gateway.
    Base URL: https://ai-gateway.vercel.sh/v1
    Auth: Bearer {AI_GATEWAY_API_KEY}
    Image: POST /v1/images/generations  (OpenAI-compatible)
    Video: POST /v1/video/generations   (Vercel video API)
    """
    vercel_base_url = "https://ai-gateway.vercel.sh/v1"
    vercel_model = VERCEL_AI_GATEWAY_MODELS.get(model, model)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    aspect_map = {
        "1:1": "1:1",
        "16:9": "16:9",
        "9:16": "9:16",
        "4:3": "4:3",
        "3:4": "3:4",
        "3:2": "3:2",
        "2:3": "2:3",
    }
    vercel_aspect = aspect_map.get(aspect_ratio, "16:9")

    if job_type == "image":
        payload = {
            "model": vercel_model,
            "prompt": prompt,
            "n": 1,
            "aspect_ratio": vercel_aspect,
            "providerOptions": {
                "xai": {
                    "aspect_ratio": vercel_aspect,
                }
            },
        }

        print(f"[Vercel AI Gateway] Image request: model={vercel_model}, aspect_ratio={vercel_aspect}")

        try:
            response = requests.post(
                f"{vercel_base_url}/images/generations",
                headers=headers,
                json=payload,
                timeout=120,
            )

            print(f"[Vercel AI Gateway] Image response status: {response.status_code}")

            if response.status_code != 200:
                raise Exception(f"Vercel AI Gateway error {response.status_code}: {response.text}")

            result = response.json()
            _preview = {k: (f"<b64 {len(v)} chars>" if k == "b64_json" else v)
                        for item in result.get("data", []) for k, v in item.items()}
            print(f"[Vercel AI Gateway] Image result: created={result.get('created')}, data={[_preview]}")

            data = result.get("data", [])
            if not data:
                raise Exception("Vercel AI Gateway returned no image data")

            image_data_item = data[0]
            if image_data_item.get("b64_json"):
                return {"success": True, "data": image_data_item["b64_json"], "type": job_type, "is_base64": True}
            elif image_data_item.get("url"):
                return {"success": True, "url": image_data_item["url"], "type": job_type}
            else:
                raise Exception("Vercel AI Gateway image response missing url/b64_json")

        except Exception as e:
            print(f"[Vercel AI Gateway] Image error: {str(e)}")
            raise Exception(f"Vercel AI Gateway image generation failed: {str(e)}")

    elif job_type == "video":
        payload = {
            "model": vercel_model,
            "prompt": prompt,
            "aspect_ratio": vercel_aspect,
            "duration": duration or 6,
        }

        if input_image_url:
            validate_image_format(input_image_url, ['jpg', 'jpeg', 'png', 'webp'], '[Vercel AI Gateway]')
            payload["image"] = input_image_url

        print(f"[Vercel AI Gateway] Video request: model={vercel_model}, aspect_ratio={vercel_aspect}, has_image={bool(input_image_url)}")

        try:
            response = requests.post(
                f"{vercel_base_url}/video/generations",
                headers=headers,
                json=payload,
                timeout=300,
            )

            print(f"[Vercel AI Gateway] Video response status: {response.status_code}")

            if response.status_code != 200:
                raise Exception(f"Vercel AI Gateway error {response.status_code}: {response.text}")

            result = response.json()
            print(f"[Vercel AI Gateway] Video result keys: {list(result.keys())}")

            data = result.get("data", [])
            if not data:
                raise Exception("Vercel AI Gateway returned no video data")

            video_url = data[0].get("url")
            if not video_url:
                raise Exception("Vercel AI Gateway video response missing url")

            return {"video_url": video_url, "type": "video"}

        except Exception as e:
            print(f"[Vercel AI Gateway] Video error: {str(e)}")
            raise Exception(f"Vercel AI Gateway video generation failed: {str(e)}")

    else:
        raise Exception(f"Unsupported job_type for Vercel AI Gateway: {job_type}")


def generate_with_frenix_image(prompt, model, aspect_ratio, api_key, input_image_url=None, job_type="image", duration=5):
    """
    Generate image using Frenix API.
    Base URL: https://api.frenix.sh/v1
    Auth: Bearer {api_key}
    Image: POST /v1/images/generations (OpenAI-compatible)
    """
    frenix_model = FRENIX_IMAGE_MODELS.get(model, model)

    aspect_to_size = {
        "1:1":  "1024x1024",
        "16:9": "1792x1024",
        "9:16": "1024x1792",
        "4:3":  "1344x1024",
        "3:4":  "1024x1344",
        "3:2":  "1536x1024",
        "2:3":  "1024x1536",
    }
    size = aspect_to_size.get(aspect_ratio, "1024x1024")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": frenix_model,
        "prompt": prompt,
        "size": size,
        "n": 1,
    }

    print(f"[Frenix] Image request: model={frenix_model}, size={size}")

    try:
        response = requests.post(
            "https://api.frenix.sh/v1/images/generations",
            headers=headers,
            json=payload,
            timeout=120,
        )

        print(f"[Frenix] Response status: {response.status_code}")

        if response.status_code == 401:
            raise Exception(f"Frenix error 401: invalid api key - {response.text}")
        if response.status_code == 403:
            raise Exception(f"Frenix error 403: tier restricted - {response.text}")
        if response.status_code == 429:
            raise Exception(f"Frenix error 429: rate limit - {response.text}")
        if response.status_code != 200:
            raise Exception(f"Frenix error {response.status_code}: {response.text}")

        result = response.json()
        data = result.get("data", [])
        if not data:
            raise Exception("Frenix returned no image data")

        item = data[0]
        if item.get("b64_json"):
            print(f"[Frenix] Response contains b64_json, returning as base64")
            return {"success": True, "data": item["b64_json"], "type": "image", "is_base64": True}
        elif item.get("url"):
            return {"success": True, "url": item["url"], "type": "image"}
        else:
            raise Exception("Frenix image response missing url and b64_json")

    except Exception as e:
        print(f"[Frenix] Error: {str(e)}")
        raise Exception(f"Frenix image generation failed: {str(e)}")


def _aicc_url_to_base64(url):
    """Download an image URL and return (base64_data, mime_type)."""
    resp = requests.get(url, timeout=60)
    if resp.status_code != 200:
        raise Exception(f"Failed to download image from {url}: HTTP {resp.status_code}")
    content_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
    b64 = base64.b64encode(resp.content).decode("utf-8")
    return b64, content_type


def generate_with_aicc(prompt, model, aspect_ratio, api_key, input_image_url=None, job_type="image", duration=5):
    """
    Generate image or video using AICC API.
    Base URL: https://api.ai.cc
    Auth: Bearer {api_key}

    Image (with input):  POST /v1beta/models/{model}:generateContent  (native Gemini, multi-image support)
    Image (text-only):   POST /v1/images/generations                  (OpenAI-compatible)
    Video:               POST /v1/video/generations + polling          (async)
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # ── VIDEO ────────────────────────────────────────────────────────────────
    if job_type == "video":
        aicc_model = AICC_VIDEO_MODELS.get(model, model)

        aspect_to_size = {
            "1:1":  "1280*720",
            "16:9": "1280*720",
            "9:16": "720*1280",
            "4:3":  "1280*960",
            "3:4":  "960*1280",
            "3:2":  "1280*854",
            "2:3":  "854*1280",
        }
        size = aspect_to_size.get(aspect_ratio, "1280*720")

        payload = {
            "model": aicc_model,
            "input": {"prompt": prompt},
            "parameters": {"size": size, "duration": duration},
        }
        if input_image_url:
            first_url = input_image_url[0] if isinstance(input_image_url, list) else input_image_url
            payload["input"]["img_url"] = first_url

        print(f"[AICC] Video request: model={aicc_model}, size={size}, duration={duration}")

        try:
            response = requests.post(
                "https://api.ai.cc/v1/video/generations",
                headers=headers,
                json=payload,
                timeout=60,
            )
            print(f"[AICC] Video submit status: {response.status_code}")

            if response.status_code == 401:
                raise Exception(f"AICC error 401: invalid api key - {response.text}")
            if response.status_code == 403:
                raise Exception(f"AICC error 403: forbidden - {response.text}")
            if response.status_code == 429:
                raise Exception(f"AICC error 429: rate limit - {response.text}")
            if response.status_code != 200:
                raise Exception(f"AICC video error {response.status_code}: {response.text}")

            result = response.json()
            print(f"[AICC] Submit raw response: {str(result)[:500]}")
            task_id = result.get("id") or result.get("task_id") or result.get("job_id")
            if not task_id:
                raise Exception(f"AICC did not return a video task ID. Response: {str(result)[:300]}")

            poll_url = f"https://api.ai.cc/v1/video/generations/{task_id}"
            for attempt in range(120):
                time.sleep(5)
                poll = requests.get(poll_url, headers=headers, timeout=30)
                poll_result = poll.json()
                if attempt < 3:
                    print(f"[AICC] Poll raw response: {str(poll_result)[:500]}")
                
                # Extract status from nested data structure
                data = poll_result.get("data", {})
                status = (
                    data.get("status")
                    or poll_result.get("status")
                    or poll_result.get("task_status")
                    or poll_result.get("state")
                    or poll_result.get("job_status")
                    or ""
                )
                print(f"[AICC] Video poll attempt {attempt + 1}: status={status}")

                # Check for completion - status can be SUCCESS or COMPLETE
                if status.upper() in ("SUCCESS", "SUCCEEDED", "COMPLETED", "COMPLETE"):
                    print(f"[AICC] Video SUCCESS!")
                    
                    # Extract video URL from nested data structure
                    # The AICC API returns video_url in data.data.output.video_url
                    video_url = None
                    
                    # Primary location: data.data.output.video_url
                    nested_output = data.get("data", {}).get("output", {})
                    if nested_output.get("video_url"):
                        video_url = nested_output["video_url"]
                        print(f"[AICC] Found video URL in data.data.output.video_url")
                    # Fallback: sometimes it's in fail_reason field (weird API quirk)
                    elif data.get("fail_reason") and data["fail_reason"].startswith("http"):
                        video_url = data["fail_reason"]
                        print(f"[AICC] Found video URL in fail_reason field")
                    # Other possible locations
                    elif data.get("output"):
                        video_url = data["output"]
                    elif poll_result.get("output"):
                        video_url = poll_result["output"]
                    elif poll_result.get("video_url"):
                        video_url = poll_result["video_url"]
                    elif poll_result.get("url"):
                        video_url = poll_result["url"]
                    
                    if isinstance(video_url, list):
                        video_url = video_url[0]
                    
                    if not video_url:
                        print(f"[AICC] ERROR: Could not find video URL in response")
                        print(f"[AICC] Full response: {json.dumps(poll_result, indent=2)}")
                        raise Exception(f"AICC video response missing output url")
                    
                    print(f"[AICC] Video URL: {video_url[:100]}...")
                    return {"success": True, "url": video_url, "type": "video"}

                # Check for failure
                if status.upper() in ("FAILURE", "FAILED", "ERROR", "CANCELLED"):
                    fail_reason = data.get("fail_reason") or poll_result.get("error") or status
                    raise Exception(f"AICC video job failed: {fail_reason}")

            raise Exception("AICC video polling timed out after 10 minutes")

        except Exception as e:
            print(f"[AICC] Video error: {str(e)}")
            raise Exception(f"AICC video generation failed: {str(e)}")

    # ── IMAGE WITH INPUT (native Gemini endpoint — supports multi-image) ──────
    elif input_image_url:
        aicc_model = AICC_IMAGE_MODELS.get(model, model)

        gemini_aspect = aspect_ratio if aspect_ratio in ("1:1", "3:4", "4:3", "16:9", "9:16") else "1:1"

        input_urls = input_image_url if isinstance(input_image_url, list) else [input_image_url]
        print(f"[AICC] Gemini img2img request: model={aicc_model}, aspect={gemini_aspect}, images={len(input_urls)}")

        image_data_list = []
        for idx, url in enumerate(input_urls):
            try:
                b64_data, mime_type = _aicc_url_to_base64(url)
                image_data_list.append((b64_data, mime_type))
                print(f"[AICC] Loaded reference image {idx + 1}/{len(input_urls)}: {mime_type}")
            except Exception as e:
                raise Exception(f"AICC failed to load reference image {idx + 1}: {str(e)}")

        def _try_v1_openai():
            """Try OpenAI-compatible /v1/chat/completions endpoint."""
            content = []
            for b64_data, mime_type in image_data_list:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{b64_data}"}
                })
            content.append({"type": "text", "text": prompt})

            v1_payload = {
                "model": aicc_model,
                "messages": [{"role": "user", "content": content}],
                "modalities": ["image", "text"],
            }
            print(f"[AICC] Trying v1 OpenAI endpoint: model={aicc_model}")
            r = requests.post(
                "https://api.ai.cc/v1/chat/completions",
                headers=headers,
                json=v1_payload,
                timeout=180,
            )
            print(f"[AICC] v1 OpenAI response status: {r.status_code}")
            if r.status_code == 401:
                raise Exception(f"AICC error 401: invalid api key - {r.text}")
            if r.status_code == 403:
                raise Exception(f"AICC error 403: forbidden - {r.text}")
            if r.status_code == 429:
                raise Exception(f"AICC error 429: rate limit - {r.text}")
            if r.status_code != 200:
                raise Exception(f"AICC v1 error {r.status_code}: {r.text}")

            result = r.json()
            choices = result.get("choices", [])
            if not choices:
                raise Exception("AICC v1 returned no choices")

            msg_content = choices[0].get("message", {}).get("content", "")
            if isinstance(msg_content, list):
                for part in msg_content:
                    if part.get("type") == "image_url":
                        data_uri = part.get("image_url", {}).get("url", "")
                        if data_uri.startswith("data:"):
                            b64 = data_uri.split(",", 1)[1]
                            return {"success": True, "data": b64, "type": "image", "is_base64": True}
                    if part.get("type") == "image":
                        b64 = part.get("data", "")
                        if b64:
                            return {"success": True, "data": b64, "type": "image", "is_base64": True}
            if isinstance(msg_content, str) and msg_content.startswith("data:"):
                b64 = msg_content.split(",", 1)[1]
                return {"success": True, "data": b64, "type": "image", "is_base64": True}

            raise Exception("AICC v1 response contained no image data")

        def _try_v1beta_gemini():
            """Try native Gemini /v1beta/models endpoint."""
            parts = []
            for b64_data, mime_type in image_data_list:
                parts.append({"inlineData": {"mimeType": mime_type, "data": b64_data}})
            parts.append({"text": prompt})

            v1beta_payload = {
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": {
                    "responseModalities": ["IMAGE"],
                    "imageConfig": {"aspectRatio": gemini_aspect},
                },
            }
            print(f"[AICC] Trying v1beta Gemini endpoint: model={aicc_model}")
            r = requests.post(
                f"https://api.ai.cc/v1beta/models/{aicc_model}:generateContent",
                headers=headers,
                json=v1beta_payload,
                timeout=180,
            )
            print(f"[AICC] v1beta Gemini response status: {r.status_code}")
            if r.status_code == 401:
                raise Exception(f"AICC error 401: invalid api key - {r.text}")
            if r.status_code == 403:
                raise Exception(f"AICC error 403: forbidden - {r.text}")
            if r.status_code == 429:
                raise Exception(f"AICC error 429: rate limit - {r.text}")
            if r.status_code != 200:
                raise Exception(f"AICC Gemini error {r.status_code}: {r.text}")

            result = r.json()
            candidates = result.get("candidates", [])
            if not candidates:
                raise Exception("AICC Gemini returned no candidates")

            for part in candidates[0].get("content", {}).get("parts", []):
                inline = part.get("inlineData", {})
                if inline.get("data"):
                    return {"success": True, "data": inline["data"], "type": "image", "is_base64": True}

            raise Exception("AICC Gemini response contained no image data")

        max_img2img_retries = 2
        img2img_retry_delay = 10
        last_error = None

        _AICC_QUOTA_MARKERS = (
            "insufficient_user_quota",
            "insufficient user quota",
            "用户额度不足",
            "quota_not_enough",
            "quota not enough",
            "user quota is not enough",
        )

        for img2img_attempt in range(1, max_img2img_retries + 1):
            try:
                try:
                    return _try_v1beta_gemini()
                except Exception as v1beta_err:
                    v1beta_err_str = str(v1beta_err)
                    if any(m in v1beta_err_str.lower() for m in _AICC_QUOTA_MARKERS):
                        print(f"[AICC] v1beta quota exhausted (attempt {img2img_attempt}/{max_img2img_retries}) — breaking inner retry")
                        raise
                    print(f"[AICC] v1beta endpoint failed (attempt {img2img_attempt}/{max_img2img_retries}): {v1beta_err} — falling back to v1 OpenAI")
                    return _try_v1_openai()
            except Exception as e:
                last_error = e
                e_str = str(e)
                print(f"[AICC] Gemini img2img error (attempt {img2img_attempt}/{max_img2img_retries}): {e_str}")
                if any(m in e_str.lower() for m in _AICC_QUOTA_MARKERS):
                    print(f"[AICC] Quota exhausted — stopping inner retries, triggering key rotation")
                    break
                if img2img_attempt < max_img2img_retries:
                    print(f"[AICC] Retrying in {img2img_retry_delay}s...")
                    time.sleep(img2img_retry_delay)

        print(f"[AICC] All {max_img2img_retries} img2img attempts failed")
        raise Exception(f"AICC image generation failed: {str(last_error)}")

    # ── IMAGE TEXT-ONLY (OpenAI-compatible endpoint) ──────────────────────────
    else:
        aicc_model = AICC_IMAGE_MODELS.get(model, model)

        aspect_to_size = {
            "1:1":  "1024x1024",
            "16:9": "1792x1024",
            "9:16": "1024x1792",
            "4:3":  "1344x1024",
            "3:4":  "1024x1344",
            "3:2":  "1536x1024",
            "2:3":  "1024x1536",
        }
        size = aspect_to_size.get(aspect_ratio, "1024x1024")

        payload = {
            "model": aicc_model,
            "prompt": prompt,
            "size": size,
            "n": 1,
        }

        max_t2i_retries = 5
        t2i_retry_delay = 10
        last_t2i_error = None

        for t2i_attempt in range(1, max_t2i_retries + 1):
            print(f"[AICC] Image text-to-image request (attempt {t2i_attempt}/{max_t2i_retries}): model={aicc_model}, size={size}")
            try:
                response = requests.post(
                    "https://api.ai.cc/v1/images/generations",
                    headers=headers,
                    json=payload,
                    timeout=120,
                )
                print(f"[AICC] Image response status: {response.status_code}")

                if response.status_code == 401:
                    raise Exception(f"AICC error 401: invalid api key - {response.text}")
                if response.status_code == 403:
                    raise Exception(f"AICC error 403: forbidden - {response.text}")
                if response.status_code == 429:
                    raise Exception(f"AICC error 429: rate limit - {response.text}")
                if response.status_code != 200:
                    raise Exception(f"AICC image error {response.status_code}: {response.text}")

                result = response.json()
                data = result.get("data", [])
                if not data:
                    raise Exception("AICC returned no image data")

                item = data[0]
                if item.get("b64_json"):
                    return {"success": True, "data": item["b64_json"], "type": "image", "is_base64": True}
                elif item.get("url"):
                    return {"success": True, "url": item["url"], "type": "image"}
                else:
                    raise Exception("AICC image response missing url and b64_json")

            except Exception as e:
                last_t2i_error = e
                e_str = str(e)
                print(f"[AICC] Image error (attempt {t2i_attempt}/{max_t2i_retries}): {e_str}")
                if any(m in e_str.lower() for m in (
                    "insufficient_user_quota", "insufficient user quota", "用户额度不足",
                    "quota_not_enough", "quota not enough", "user quota is not enough",
                )):
                    print(f"[AICC] Quota exhausted — stopping inner retries, triggering key rotation")
                    break
                if t2i_attempt < max_t2i_retries:
                    print(f"[AICC] Retrying in {t2i_retry_delay}s...")
                    time.sleep(t2i_retry_delay)

        print(f"[AICC] All {max_t2i_retries} text-to-image attempts failed")
        raise Exception(f"AICC image generation failed: {str(last_t2i_error)}")


def generate_with_felo(prompt, model, aspect_ratio, api_key, input_image_url=None, job_type="image", duration=5):
    """
    Generate or edit an image using Felo AI (openapi.felo.ai).

    Pipeline:
      1. POST /v2/conversations  → stream_key + live_doc_short_id
      2. Read SSE stream briefly (server closes it early; generation is async)
      3. Poll GET /v2/livedocs/{id}/resources until resource_type=image, status=completed
      4. GET /v2/livedocs/{id}/resources/{item_id}/download → 302 → S3 pre-signed URL
      5. Download raw PNG bytes from S3 and return as base64

    Text-to-image: prompt only, no images[] field.
    Image editing:  input_image_url downloaded, resized to 512px, sent as base64 data URL in images[].

    DNS note: openapi.felo.ai and *.amazonaws.com can fail to resolve on some hosts.
    This function resolves both via Google DNS-over-HTTPS and caches the IPs.
    """
    import socket as _socket

    felo_model = FELO_MODELS.get(model, model)
    BASE        = "https://openapi.felo.ai"
    FELO_HDRS   = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    SSE_HDRS    = {"Authorization": f"Bearer {api_key}", "Accept": "text/event-stream"}

    # ── DNS patch (Google DoH) ────────────────────────────────────────────────
    _felo_dns: dict = {}
    _orig_gai = _socket.getaddrinfo

    def _doh_resolve(host):
        try:
            r = requests.get(
                "https://dns.google/resolve",
                params={"name": host, "type": "A"},
                timeout=5,
            )
            for ans in r.json().get("Answer", []):
                if ans.get("type") == 1:
                    return ans["data"]
        except Exception:
            pass
        return None

    def _patched_gai(host, port, *args, **kwargs):
        if host in ("openapi.felo.ai",) or host.endswith(".amazonaws.com"):
            if host not in _felo_dns:
                ip = _doh_resolve(host)
                if ip:
                    _felo_dns[host] = ip
            if host in _felo_dns:
                host = _felo_dns[host]
        return _orig_gai(host, port, *args, **kwargs)

    _socket.getaddrinfo = _patched_gai

    try:
        # ── Pre-resolve openapi.felo.ai via DoH ───────────────────────────────
        ip = _doh_resolve("openapi.felo.ai")
        if ip:
            _felo_dns["openapi.felo.ai"] = ip
            print(f"[Felo] Resolved openapi.felo.ai → {ip}")

        # ── Safe helpers ─────────────────────────────────────────────────────
        def _felo_get(url, stream=False, allow_redirects=True, timeout=30, retries=4):
            hdrs = SSE_HDRS if stream else FELO_HDRS
            for attempt in range(retries):
                try:
                    return requests.get(url, headers=hdrs, stream=stream,
                                        allow_redirects=allow_redirects, timeout=timeout)
                except Exception as e:
                    print(f"[Felo] GET retry {attempt+1}/{retries}: {type(e).__name__}")
                    time.sleep(3)
            return None

        def _felo_post(url, payload, retries=4):
            for attempt in range(retries):
                try:
                    return requests.post(url, headers=FELO_HDRS, json=payload, timeout=30)
                except Exception as e:
                    print(f"[Felo] POST retry {attempt+1}/{retries}: {type(e).__name__}")
                    time.sleep(3)
            return None

        # ── Build query ───────────────────────────────────────────────────────
        # NOTE: Sending images[] as base64 causes Felo to enter visual Q&A mode
        # (no image_generation tool fires). For editing, embed the source URL
        # directly in the query text so nano-banana-2 generates a new image.
        if input_image_url:
            first_url = input_image_url[0] if isinstance(input_image_url, list) else input_image_url
            query = (
                f"Generate a new image based on this reference image {first_url} "
                f"with the following changes: {prompt}. "
                f"Output must be a fully generated image."
            )
            print(f"[Felo] Edit mode — reference URL in query (no images[] attachment)")
        else:
            query = f"Generate an image: {prompt}"

        payload = {
            "query": query,
            "model": felo_model,
            "tools": ["image_generation"],
        }

        # ── Step 1: Create conversation ───────────────────────────────────────
        print(f"[Felo] POST /v2/conversations model={felo_model}")
        r = _felo_post(f"{BASE}/v2/conversations", payload)
        if not r or r.status_code not in (200, 201):
            err = getattr(r, "text", "no response")[:300]
            if r and r.status_code in (401, 403):
                raise Exception(f"Felo error {r.status_code}: invalid api key - {err}")
            if r and r.status_code == 429:
                raise Exception(f"Felo error 429: rate limit - {err}")
            raise Exception(f"Felo conversation failed {getattr(r,'status_code','?')}: {err}")

        resp_data   = r.json()
        data        = resp_data.get("data", {})
        stream_key  = data.get("stream_key", "")
        live_doc_id = data.get("live_doc_short_id", "")
        thread_id   = data.get("thread_short_id", "")
        print(f"[Felo] stream_key={stream_key}  live_doc={live_doc_id}  thread={thread_id}")
        print(f"[Felo] Conversation response keys: {list(data.keys())}")

        # ── Step 2: Read SSE (wait for image_generation tool to fire) ──────────
        import json as _json
        sse_got_image_tool = False
        if stream_key:
            try:
                sse = _felo_get(f"{BASE}/v2/conversations/stream/{stream_key}",
                                stream=True, timeout=90)
                if sse and sse.status_code == 200:
                    print(f"[Felo] SSE connected, reading for up to 80s...")
                    deadline = time.time() + 80
                    for raw in sse.iter_lines(decode_unicode=True):
                        if time.time() > deadline:
                            print(f"[Felo] SSE deadline reached")
                            break
                        if not raw:        # blank line = SSE event separator; skip
                            continue
                        if raw.startswith("data:"):
                            try:
                                env = _json.loads(raw[5:].strip())
                                if env.get("is_complete"):
                                    print(f"[Felo] SSE is_complete=True")
                                    break
                                content = env.get("content", "")
                                if content:
                                    inner = _json.loads(content)
                                    d = inner.get("data", {})
                                    msg_type = inner.get("type", "")
                                    if msg_type == "processing":
                                        print(f"[Felo] SSE processing: {d.get('message','')}")
                                    if "tools" in d:
                                        for tool in d["tools"]:
                                            tn = tool.get("tool_name") or tool.get("name", "")
                                            st = tool.get("status", "")
                                            print(f"[Felo] SSE tool={tn} status={st}")
                                            if tn == "image_generation" and st in ("generating", "initialized"):
                                                sse_got_image_tool = True
                                    # Check for image URL in SSE content directly
                                    if "image_url" in str(d) or "url" in str(d):
                                        print(f"[Felo] SSE data snippet: {str(d)[:200]}")
                            except Exception:
                                pass
                        elif raw.startswith("event:") or raw.startswith("id:"):
                            print(f"[Felo] SSE meta: {raw}")
                else:
                    sc = getattr(sse, 'status_code', 'none')
                    print(f"[Felo] SSE unavailable (status={sc})")
                if sse:
                    sse.close()
            except Exception as e:
                print(f"[Felo] SSE error: {type(e).__name__}: {e}")
        print(f"[Felo] SSE image_tool_fired={sse_got_image_tool}")

        # ── Step 3: Poll LiveDoc for completed image ──────────────────────────
        print(f"[Felo] Polling LiveDoc {live_doc_id} ...")
        item_id = None

        def _extract_items(resp_json):
            """Try multiple field paths Felo may use for resource lists."""
            data = resp_json.get("data", {})
            if isinstance(data, list):
                return data
            for key in ("items", "resources", "list", "results"):
                val = data.get(key)
                if isinstance(val, list):
                    return val
            return []

        def _find_item_id(items):
            for item in items:
                rtype  = item.get("resource_type") or item.get("type", "")
                status = item.get("status", "")
                iid    = item.get("id", "")
                print(f"[Felo]   item type={rtype} status={status} id={iid}")
                if (rtype in ("image", "img", "generated_image")
                        and status in ("completed", "done", "success", "finished")
                        and iid):
                    print(f"[Felo] Image ready: {item.get('title','')} item_id={iid}")
                    return iid
                if status in ("completed", "done", "success") and iid:
                    print(f"[Felo] Item ready (type={rtype}): item_id={iid}")
                    return iid
            return None

        for attempt in range(60):           # up to ~5 minutes
            time.sleep(5)

            # Poll livedoc resources
            poll = _felo_get(f"{BASE}/v2/livedocs/{live_doc_id}/resources")
            if poll:
                try:
                    resp_json = poll.json()
                    items = _extract_items(resp_json)
                    if attempt == 0:
                        print(f"[Felo] Poll 1 livedoc raw: {str(resp_json)[:400]}")
                    print(f"[Felo] Poll {attempt+1}: {len(items)} livedoc item(s)")
                    found = _find_item_id(items)
                    if found:
                        item_id = found
                        break
                except Exception:
                    pass

            # Fallback: poll thread resources and messages every 5 attempts
            if not item_id and thread_id and attempt % 5 == 0:
                for t_endpoint in (
                    f"{BASE}/v2/threads/{thread_id}/resources",
                    f"{BASE}/v2/livedocs/{thread_id}/resources",
                ):
                    t_poll = _felo_get(t_endpoint)
                    if t_poll:
                        try:
                            t_json = t_poll.json()
                            t_items = _extract_items(t_json)
                            if attempt == 0:
                                print(f"[Felo] Poll 1 thread raw ({t_endpoint.split('/')[-2]}): {str(t_json)[:300]}")
                            if t_items:
                                print(f"[Felo] Thread poll {attempt+1}: {len(t_items)} item(s) from {t_endpoint}")
                                found = _find_item_id(t_items)
                                if found:
                                    item_id = found
                                    live_doc_id = thread_id
                                    break
                        except Exception:
                            pass
                if item_id:
                    break

        if not item_id:
            raise Exception("Felo image generation failed: no completed image resource found after ~5 minutes of polling")

        # ── Step 4: Get S3 pre-signed download URL ────────────────────────────
        dl_url  = f"{BASE}/v2/livedocs/{live_doc_id}/resources/{item_id}/download"
        dl_resp = _felo_get(dl_url, allow_redirects=False, timeout=15)
        if not dl_resp:
            raise Exception("Felo /download request failed")
        if dl_resp.status_code == 302:
            s3_url = dl_resp.headers.get("location", "")
        elif dl_resp.status_code == 200:
            s3_url = dl_resp.url
        else:
            raise Exception(f"Felo /download returned {dl_resp.status_code}: {dl_resp.text[:200]}")
        if not s3_url:
            raise Exception("Felo /download returned no redirect URL")

        print(f"[Felo] Downloading from S3 ...")
        img_resp = requests.get(s3_url, timeout=60)
        if img_resp.status_code != 200 or len(img_resp.content) < 1000:
            raise Exception(f"Felo S3 download failed: {img_resp.status_code}")

        b64_result = base64.b64encode(img_resp.content).decode()
        print(f"[Felo] Done — {len(img_resp.content)//1024} KB image")
        return {"success": True, "data": b64_result, "type": "image", "is_base64": True}

    except Exception as e:
        raise Exception(f"Felo image generation failed: {str(e)}")
    finally:
        _socket.getaddrinfo = _orig_gai


def generate(prompt, model, aspect_ratio, api_key, provider_key=None, input_image_url=None, job_type="image", duration=5, job_id=None, **kwargs):
    endpoint_type = get_endpoint_type(provider_key, model)

    print(f"[MultiEndpoint] Routing to: {endpoint_type.upper()}")
    print(f"[MultiEndpoint] Provider: {provider_key}")
    print(f"[MultiEndpoint] Model: {model}")
    print(f"[MultiEndpoint] Job Type: {job_type}")
    
    if endpoint_type == "replicate":
        return generate_with_replicate(
            prompt=prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            api_key=api_key,
            input_image_url=input_image_url,
            job_type=job_type,
            duration=duration
        )
    elif endpoint_type == "pixazo":
        return generate_with_pixazo(
            prompt=prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            api_key=api_key,
            input_image_url=input_image_url,
            job_type=job_type,
            duration=duration
        )
    elif endpoint_type == "huggingface":
        return generate_with_huggingface(
            prompt=prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            api_key=api_key,
            input_image_url=input_image_url,
            job_type=job_type,
            duration=duration
        )
    elif endpoint_type == "rapidapi":
        return generate_with_rapidapi(
            prompt=prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            api_key=api_key,
            input_image_url=input_image_url,
            job_type=job_type,
            duration=duration
        )
    elif endpoint_type == "a4f":
        return generate_with_a4f(
            prompt=prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            api_key=api_key,
            input_image_url=input_image_url,
            job_type=job_type,
            duration=duration
        )
    elif endpoint_type == "kie":
        return generate_with_kie(
            prompt=prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            api_key=api_key,
            input_image_url=input_image_url,
            job_type=job_type,
            duration=duration
        )
    elif endpoint_type == "removebg":
        return generate_with_removebg(
            prompt=prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            api_key=api_key,
            input_image_url=input_image_url,
            job_type=job_type,
            duration=duration
        )
    elif endpoint_type == "bria_vision":
        return generate_with_bria_vision(
            prompt=prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            api_key=api_key,
            input_image_url=input_image_url,
            job_type=job_type,
            duration=duration,
            **kwargs
        )
    elif endpoint_type == "bria_cinematic":
        return generate_with_bria_cinematic(
            prompt=prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            api_key=api_key,
            input_image_url=input_image_url,
            job_type=job_type,
            duration=duration,
            **kwargs
        )
    elif endpoint_type == "custom":
        return generate_with_custom(
            prompt=prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            api_key=api_key,
            input_image_url=input_image_url,
            job_type=job_type,
            duration=duration
        )
    elif endpoint_type == "infip":
        return generate_with_infip(
            prompt=prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            api_key=api_key,
            input_image_url=input_image_url,
            job_type=job_type,
            duration=duration
        )
    elif endpoint_type == "deapi":
        return generate_with_deapi(
            prompt=prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            api_key=api_key,
            input_image_url=input_image_url,
            job_type=job_type,
            duration=duration
        )
    elif endpoint_type == "leonardo":
        return generate_with_leonardo(
            prompt=prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            api_key=api_key,
            input_image_url=input_image_url,
            job_type=job_type,
            duration=duration,
            **kwargs
        )
    elif endpoint_type == "stabilityai":
        return generate_with_stabilityai(
            prompt=prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            api_key=api_key,
            input_image_url=input_image_url,
            job_type=job_type,
            **kwargs
        )
    elif endpoint_type == "vercel_ai_gateway":
        return generate_with_vercel_ai_gateway(
            prompt=prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            api_key=api_key,
            input_image_url=input_image_url,
            job_type=job_type,
            duration=duration
        )
    elif endpoint_type == "picsart":
        return generate_with_picsart(
            prompt=prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            api_key=api_key,
            input_image_url=input_image_url,
            job_type=job_type,
        )
    elif endpoint_type == "clipdrop":
        return generate_with_clipdrop(
            prompt=prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            api_key=api_key,
            input_image_url=input_image_url,
            job_type=job_type,
        )
    elif endpoint_type == "frenix":
        return generate_with_frenix_image(
            prompt=prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            api_key=api_key,
            input_image_url=input_image_url,
            job_type=job_type,
            duration=duration
        )
    elif endpoint_type == "aicc":
        return generate_with_aicc(
            prompt=prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            api_key=api_key,
            input_image_url=input_image_url,
            job_type=job_type,
            duration=duration
        )
    elif endpoint_type == "felo":
        return generate_with_felo(
            prompt=prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            api_key=api_key,
            input_image_url=input_image_url,
            job_type=job_type,
            duration=duration
        )
    elif endpoint_type == "gemini":
        return generate_with_gemini(
            prompt=prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            api_key=api_key,
            input_image_url=input_image_url,
            job_type=job_type,
            duration=duration
        )
    elif endpoint_type == "geminiwebapi":
        # Gemini Web API - uses dual cookies (async wrapper)
        import asyncio
        import sys
        import os
        # Add backend to path for import
        backend_path = os.path.dirname(os.path.abspath(__file__))
        if backend_path not in sys.path:
            sys.path.insert(0, backend_path)
        from gemini_webapi_client import generate_with_gemini_web

        # Normalize input images to list
        input_images = None
        if input_image_url:
            input_images = [input_image_url] if isinstance(input_image_url, str) else input_image_url

        # Map frontend model names to actual Gemini Web API model names
        gemini_model = GEMINI_WEB_API_MODELS.get(model, model)

        # Create new event loop for async execution (avoid loop conflicts)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(generate_with_gemini_web(
                prompt=prompt,
                model=gemini_model,
                aspect_ratio=aspect_ratio,
                input_images=input_images,
                provider_key=provider_key or "vision-geminiwebapi"
            ))
            return result
        except Exception as e:
            raise Exception(f"Gemini Web API error: {str(e)}")
        finally:
            loop.close()
    elif endpoint_type == "ondemand":
        # Determine if using Agent API (sync mode) or Workflow API (webhook mode)
        from provider_api_keys import get_provider_api_key
        import json
        cred_record = get_provider_api_key(provider_key or "vision-ondemand")
        raw_json = cred_record.get("api_key") if cred_record else None
        use_agent_api = False
        if raw_json:
            try:
                data = json.loads(raw_json)
                if "agent_ids" in data:
                    use_agent_api = True
            except Exception:
                pass
        if use_agent_api:
            from ondemand_agent_provider import generate_with_ondemand_agent
            return generate_with_ondemand_agent(
                prompt=prompt,
                model=model,
                aspect_ratio=aspect_ratio,
                api_key=api_key,
                input_image_url=input_image_url,
                job_type=job_type,
                duration=duration,
                provider_key=provider_key or "vision-ondemand",
                job_id=job_id,
                use_direct_agent=True,  # Use direct Nano Banana PRO when images provided
                **kwargs,
            )
        else:
            from ondemand_provider import generate_with_ondemand
            return generate_with_ondemand(
                prompt=prompt,
                model=model,
                aspect_ratio=aspect_ratio,
                api_key=api_key,
                input_image_url=input_image_url,
                job_type=job_type,
                duration=duration,
                provider_key=provider_key or "vision-ondemand",
                job_id=job_id,
            )
    else:
        raise Exception(f"Unsupported endpoint type: {endpoint_type}")


class EndpointManager:
    """
    Async wrapper for multi-endpoint generation functions
    Used by workflow engine for model routing
    """
    
    async def generate_image(self, prompt, model, provider_key, aspect_ratio='1:1', input_image_url=None, job_id=None, **kwargs):
        """
        Generate image with specified model and provider
        Includes automatic API key rotation on provider errors
        
        Args:
            prompt: Text prompt for generation
            model: Model name to use
            provider_key: Provider key (e.g., 'vision-ultrafast', 'cinematic-leonardo')
            aspect_ratio: Image aspect ratio
            input_image_url: Optional input image(s) for image-to-image
            **kwargs: Additional parameters (steps, cfg, etc.)
        
        Returns:
            dict with image_url or url key
        
        Raises:
            Exception: If no API keys available or generation fails after rotation
        """
        import asyncio
        from provider_api_keys import get_api_key_for_job
        from api_key_rotation import handle_api_key_rotation, handle_roundrobin_rotation, should_rotate_key
        from provider_constants import NO_DELETE_ROTATE_PROVIDERS
        
        print(f"🔍 [EndpointManager] generate_image - provider_key: {provider_key}, model: {model}")
        
        use_roundrobin = provider_key in NO_DELETE_ROTATE_PROVIDERS
        max_rotation_attempts = 5
        attempt = 0
        _next_api_key_data = None  # carries rotated key into next loop iteration

        while attempt < max_rotation_attempts:
            attempt += 1

            # Use pre-rotated key if available (skip re-fetch which would reset round-robin)
            if _next_api_key_data:
                api_key_data = _next_api_key_data
                _next_api_key_data = None
                print(f"[EndpointManager] Using pre-rotated key (id={api_key_data.get('id')})")
            else:
                api_key_data = get_api_key_for_job(model, provider_key=provider_key, job_type='image')

            if not api_key_data:
                error_msg = f"NO_API_KEY_AVAILABLE: No API keys found for provider '{provider_key}'"
                print(f"[EndpointManager] {error_msg}")
                raise Exception(error_msg)

            api_key = api_key_data.get('api_key')
            api_key_id = api_key_data.get('id')
            api_key_number = api_key_data.get('key_number')

            try:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: generate(
                        prompt=prompt,
                        model=model,
                        aspect_ratio=aspect_ratio,
                        api_key=api_key,
                        provider_key=provider_key,
                        input_image_url=input_image_url,
                        job_type='image',
                        **kwargs
                    )
                )

                # Clear cooldown/error status for NO_DELETE provider keys after successful use
                if api_key_number is not None and provider_key in NO_DELETE_ROTATE_PROVIDERS:
                    from provider_api_keys import clear_api_key_status
                    clear_api_key_status(provider_key, int(api_key_number))

                return result

            except Exception as e:
                error_message = str(e)
                print(f"[EndpointManager] Generation error (attempt {attempt}/{max_rotation_attempts}): {error_message}")

                if should_rotate_key(error_message, provider_key):
                    print(f"[EndpointManager] Error requires key rotation, attempting...")

                    if use_roundrobin:
                        print(f"[EndpointManager] Provider '{provider_key}' uses roundrobin (no key deletion)")
                        rotation_success, next_key = handle_roundrobin_rotation(
                            provider_key,
                            error_message,
                            job_id=job_id or f"workflow-{model}",
                            current_api_key_id=api_key_id
                        )
                    else:
                        rotation_success, next_key = handle_api_key_rotation(
                            api_key_id,
                            provider_key,
                            error_message,
                            job_id=job_id or f"workflow-{model}"
                        )

                    if rotation_success and next_key:
                        print(f"[EndpointManager] Rotation successful, retrying with key #{next_key.get('key_number')}...")
                        _next_api_key_data = next_key  # use directly, don't re-fetch
                        continue
                    else:
                        print(f"[EndpointManager] Rotation failed or no keys available")
                        raise Exception(f"NO_API_KEY_AVAILABLE: All API keys exhausted for provider '{provider_key}': {error_message}")
                else:
                    print(f"[EndpointManager] Error doesn't require rotation, re-raising...")
                    raise

        raise Exception(f"Generation failed after {max_rotation_attempts} rotation attempts")
    
    async def generate_video(self, prompt, model, provider_key, input_image_url=None, duration=5, job_id=None, **kwargs):
        """
        Generate video with specified model and provider
        Includes automatic API key rotation on provider errors
        
        Args:
            prompt: Text prompt for generation
            model: Model name to use
            provider_key: Provider key (e.g., 'cinematic-leonardo')
            input_image_url: Input image for image-to-video
            duration: Video duration in seconds
            **kwargs: Additional parameters
        
        Returns:
            dict with video_url or url key
        
        Raises:
            Exception: If no API keys available or generation fails after rotation
        """
        import asyncio
        from provider_api_keys import get_api_key_for_job
        from api_key_rotation import handle_api_key_rotation, handle_roundrobin_rotation, should_rotate_key
        from provider_constants import NO_DELETE_ROTATE_PROVIDERS

        print(f"🔍 [EndpointManager] generate_video - provider_key: {provider_key}, model: {model}")

        # Extract aspect_ratio from kwargs to avoid duplicate argument
        aspect_ratio = kwargs.pop('aspect_ratio', '16:9')

        use_roundrobin = provider_key in NO_DELETE_ROTATE_PROVIDERS
        max_rotation_attempts = 5
        attempt = 0
        _next_api_key_data = None  # carries rotated key into next loop iteration

        while attempt < max_rotation_attempts:
            attempt += 1

            # Use pre-rotated key if available (skip re-fetch which would reset round-robin)
            if _next_api_key_data:
                api_key_data = _next_api_key_data
                _next_api_key_data = None
                print(f"[EndpointManager] Using pre-rotated key (id={api_key_data.get('id')})")
            else:
                api_key_data = get_api_key_for_job(model, provider_key=provider_key, job_type='video')

            if not api_key_data:
                error_msg = f"NO_API_KEY_AVAILABLE: No API keys found for provider '{provider_key}'"
                print(f"[EndpointManager] {error_msg}")
                raise Exception(error_msg)

            api_key = api_key_data.get('api_key')
            api_key_id = api_key_data.get('id')
            api_key_number = api_key_data.get('key_number')

            try:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: generate(
                        prompt=prompt,
                        model=model,
                        aspect_ratio=aspect_ratio,
                        api_key=api_key,
                        provider_key=provider_key,
                        input_image_url=input_image_url,
                        job_type='video',
                        duration=duration,
                        **kwargs
                    )
                )

                # Clear cooldown/error status for NO_DELETE provider keys after successful use
                if api_key_number is not None and provider_key in NO_DELETE_ROTATE_PROVIDERS:
                    from provider_api_keys import clear_api_key_status
                    clear_api_key_status(provider_key, int(api_key_number))

                return result

            except Exception as e:
                error_message = str(e)
                print(f"[EndpointManager] Generation error (attempt {attempt}/{max_rotation_attempts}): {error_message}")

                if should_rotate_key(error_message, provider_key):
                    print(f"[EndpointManager] Error requires key rotation, attempting...")

                    if use_roundrobin:
                        print(f"[EndpointManager] Provider '{provider_key}' uses roundrobin (no key deletion)")
                        rotation_success, next_key = handle_roundrobin_rotation(
                            provider_key,
                            error_message,
                            job_id=job_id or f"workflow-{model}",
                            current_api_key_id=api_key_id
                        )
                    else:
                        rotation_success, next_key = handle_api_key_rotation(
                            api_key_id,
                            provider_key,
                            error_message,
                            job_id=job_id or f"workflow-{model}"
                        )

                    if rotation_success and next_key:
                        print(f"[EndpointManager] Rotation successful, retrying with key #{next_key.get('key_number')}...")
                        _next_api_key_data = next_key  # use directly, don't re-fetch
                        continue
                    else:
                        print(f"[EndpointManager] Rotation failed or no keys available")
                        raise Exception(f"NO_API_KEY_AVAILABLE: All API keys exhausted for provider '{provider_key}': {error_message}")
                else:
                    print(f"[EndpointManager] Error doesn't require rotation, re-raising...")
                    raise
        
        raise Exception(f"Generation failed after {max_rotation_attempts} rotation attempts")


_endpoint_manager_instance = None

def get_endpoint_manager():
    """Get singleton instance of EndpointManager"""
    global _endpoint_manager_instance
    if _endpoint_manager_instance is None:
        _endpoint_manager_instance = EndpointManager()
    return _endpoint_manager_instance
