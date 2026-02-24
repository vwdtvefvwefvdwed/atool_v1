"""
Multi-Endpoint Manager
Routes generation requests to different API providers based on provider key:
- vision-nova, cinematic-nova → Replicate API
- vision-pixazo → Pixazo API
- vision-huggingface → Hugging Face API
- vision-ultrafast → RapidAPI (Ultra Fast Nano Banana)
- vision-atlas → A4F API (OpenAI-compatible)
- vision-flux, cinematic-pro → KIE AI (Task-based)
- vision-removebg → Remove.bg API
- vision-bria → Bria AI Vision (Image generation and editing)
- vision-infip → Infip.pro API (Async polling-based)
- vision-deapi → deAPI (Async polling-based)
- vision-leonardo, cinematic-leonardo → Leonardo AI (Async polling-based)
- vision-stabilityai → Stability AI (Image upscaling)
- cinematic-bria → Bria AI Cinematic (Video editing and generation)
"""

import os
import time
import requests
import base64
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

# Hugging Face Models
HUGGINGFACE_MODELS = {
    'AP123/IllusionDiffusion': 'AP123/IllusionDiffusion',
}

# RapidAPI Models - Ultra Fast Nano Banana
RAPIDAPI_MODELS = {
    'ultra-fast-nano': 'ultra-fast-nano-banana-2',
    'ultra-fast-nano-banana-2': 'ultra-fast-nano-banana-2',
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

# Xeven Free API Models - https://ai-image-api.xeven.workers.dev/img
# Note: This is a FREE API that doesn't require an API key
XEVEN_MODELS = {
    # Fast and high-quality models
    'sdxl-lightning-xeven': 'sdxl-lightning',  # Fast, high-quality < 5 secs
    'sdxl-xeven': 'sdxl',  # Balanced, professional < 12 secs
    'flux-schnell-2': 'flux-schnell',  # Best realistic model < 6 secs (numbered to avoid conflict with A4F)
    'lucid-origin': 'lucid-origin',  # High-quality artistic images
    'phoenix-2': 'phoenix',  # Professional-grade (numbered to avoid conflict with A4F)
}

# Infip.pro API Models - https://api.infip.pro/v1
# Note: Async models (z-image-turbo, qwen) require polling
INFIP_MODELS = {
    'z-image-turbo': 'z-image-turbo',  # Fast async model
    'qwen': 'qwen',  # Qwen async model
    'flux2-klein-9b': 'flux2-klein-9b',  # FLUX 2 Klein 9B
    'flux2-dev': 'flux2-dev',  # FLUX 2 Dev
}

# deAPI Models - https://api.deapi.ai
# Note: All models require async polling
DEAPI_MODELS = {
    'z-image-turbo-deapi': 'ZImageTurbo_INT8',  # Fast photorealistic model (INT8 quantized)
    'flux-schnell-deapi': 'Flux1schnell',  # Fast iteration model (1-10 steps)
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
    'xeven': {
        'supported': True,
        'notes': 'Only sdxl and sdxl-lightning support img2img via image_b64. flux-schnell, lucid-origin, phoenix are text-to-image only and will raise an error if image is provided.',
        'models_supporting_image': ['sdxl-lightning', 'sdxl'],
        'models_not_supporting_image': ['flux-schnell', 'lucid-origin', 'phoenix'],
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
    'vision-xeven': 'xeven',
    'vision-infip': 'infip',
    'vision-deapi': 'deapi',
    'vision-leonardo': 'leonardo',
    'vision-stabilityai': 'stabilityai',
    'cinematic-nova': 'replicate',
    'cinematic-pro': 'kie',
    'cinematic-bria': 'bria_cinematic',
    'cinematic-leonardo': 'leonardo',
}


PROVIDER_ALLOWED_IMAGE_FORMATS = {
    'vision-replicate':     ['jpg', 'jpeg', 'png', 'webp', 'gif'],
    'vision-huggingface':   ['jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp'],
    'vision-ultrafast':     ['jpg', 'jpeg', 'png', 'webp'],
    'vision-kie':           ['jpg', 'jpeg', 'png', 'webp'],
    'vision-flux':          ['jpg', 'jpeg', 'png', 'webp'],
    'vision-removebg':      ['jpg', 'jpeg', 'png', 'webp'],
    'vision-bria':          ['jpg', 'jpeg', 'png', 'webp'],
    'vision-xeven':         ['jpg', 'jpeg', 'png', 'webp'],
    'vision-leonardo':      ['jpg', 'jpeg', 'png', 'webp', 'gif'],
    'vision-stabilityai':   ['jpg', 'jpeg', 'png', 'webp'],
    'vision-atlas':         [],
    'vision-pixazo':        [],
    'vision-infip':         [],
    'vision-deapi':         [],
    'cinematic-nova':       ['jpg', 'jpeg', 'png', 'webp', 'gif'],
    'cinematic-pro':        ['jpg', 'jpeg', 'png', 'webp'],
    'cinematic-bria':       ['mp4', 'webm', 'mov'],
    'cinematic-leonardo':   ['jpg', 'jpeg', 'png', 'webp', 'gif'],
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
        if model_name in XEVEN_MODELS:
            return 'xeven'
        if model_name in INFIP_MODELS:
            return 'infip'
        if model_name in DEAPI_MODELS:
            return 'deapi'
        if model_name in LEONARDO_MODELS:
            return 'leonardo'
        if model_name in STABILITYAI_MODELS:
            return 'stabilityai'
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


def generate_with_huggingface(prompt, model, aspect_ratio, api_key, input_image_url=None, job_type="image", duration=5):
    """
    Generate images using Hugging Face Gradio Space
    Uses gradio_client for AP123/IllusionDiffusion
    """
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
    # RapidAPI host
    rapidapi_host = "ultra-fast-nano-banana-22.p.rapidapi.com"
    
    # API key is the RapidAPI key
    rapidapi_key = api_key
    
    url = f"https://{rapidapi_host}/index.php"
    
    payload = {
        "prompt": prompt,
    }
    
    # Add image URL(s) if provided (for image-to-image)
    # Supports both single URL (string) and multiple URLs (list)
    if input_image_url:
        validate_image_format(input_image_url, ['jpg', 'jpeg', 'png', 'webp'], '[RapidAPI]')
        if isinstance(input_image_url, list):
            payload["image_urls"] = input_image_url
        else:
            payload["image_urls"] = [input_image_url]
    
    headers = {
        "x-rapidapi-host": rapidapi_host,
        "x-rapidapi-key": rapidapi_key,
        "Content-Type": "application/json"
    }
    
    print(f"[RapidAPI] Running model: Ultra Fast Nano Banana")
    print(f"[RapidAPI] Host: {rapidapi_host}")
    print(f"[RapidAPI] Prompt: {prompt}")
    if input_image_url:
        if isinstance(input_image_url, list):
            print(f"[RapidAPI] Reference Images: {len(input_image_url)} images")
            for idx, url in enumerate(input_image_url, 1):
                print(f"[RapidAPI]   Image {idx}: {url[:100]}...")
        else:
            print(f"[RapidAPI] Reference Image: {input_image_url}")
    
    try:
        response = requests.post(
            url,
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
        
        # Check for base64-encoded image response
        if isinstance(result, dict):
            # Check for base64 in various field names
            base64_data = None
            base64_fields = ["image_base64", "image", "output", "data"]
            
            for field in base64_fields:
                if field in result and isinstance(result[field], str) and len(result[field]) > 100:
                    # Check if it looks like base64
                    if result[field].startswith(('iVBOR', '/9j/', 'data:image')):
                        base64_data = result[field]
                        # Strip data URI prefix if present
                        if base64_data.startswith('data:image'):
                            base64_data = base64_data.split(',')[1]
                        print(f"[RapidAPI] Found base64 data in field '{field}'")
                        break
            
            # If we have base64 data, return it
            if base64_data:
                print(f"[RapidAPI] Detected base64 image response ({len(base64_data)} chars)")
                return {"success": True, "data": base64_data, "type": "image", "is_base64": True}
            
            # Check for URL response
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


def generate_with_xeven(prompt, model, aspect_ratio, api_key=None, input_image_url=None, job_type="image", duration=5):
    """
    Generate images using Xeven Free API (NO API KEY REQUIRED)
    https://ai-image-api.xeven.workers.dev/img
    
    Supports 5 models:
    - sdxl-lightning: Fast, high-quality (< 5 secs), size 256-2048px
    - sdxl: Balanced, professional (< 12 secs), size 256-2048px
    - flux-schnell: Best realistic model (< 6 secs), size flexible
    - lucid-origin: High-quality artistic, size 0-2500px, default 1120x1120
    - phoenix: Professional-grade (25 steps), size 0-2048px, default 1024x1024
    
    All models support negative prompts except flux-schnell.
    SDXL variants support img2img via image_b64 parameter.
    """
    xeven_model = XEVEN_MODELS.get(model, 'sdxl-lightning')
    base_url = "https://ai-image-api.xeven.workers.dev/img"
    
    print(f"[Xeven] Running model: {xeven_model} (internal: {xeven_model})")
    print(f"[Xeven] Aspect ratio: {aspect_ratio}")
    print(f"[Xeven] FREE API - No API key required")
    
    # Map aspect ratios to dimensions based on model constraints
    # Lucid Origin: 0-2500px, default 1120x1120
    # Phoenix: 0-2048px, default 1024x1024
    # SDXL/Lightning: 256-2048px
    # Flux Schnell: flexible
    
    if xeven_model == 'lucid-origin':
        aspect_map = {
            "1:1": {"width": 1120, "height": 1120},
            "16:9": {"width": 1920, "height": 1080},
            "9:16": {"width": 1080, "height": 1920},
            "4:3": {"width": 1600, "height": 1200},
            "3:4": {"width": 1200, "height": 1600},
            "3:2": {"width": 1680, "height": 1120},
            "2:3": {"width": 1120, "height": 1680},
        }
    elif xeven_model == 'phoenix':
        aspect_map = {
            "1:1": {"width": 1024, "height": 1024},
            "16:9": {"width": 1792, "height": 1008},
            "9:16": {"width": 1008, "height": 1792},
            "4:3": {"width": 1536, "height": 1152},
            "3:4": {"width": 1152, "height": 1536},
            "3:2": {"width": 1536, "height": 1024},
            "2:3": {"width": 1024, "height": 1536},
        }
    else:  # SDXL, SDXL Lightning, Flux Schnell
        aspect_map = {
            "1:1": {"width": 1024, "height": 1024},
            "16:9": {"width": 1536, "height": 864},
            "9:16": {"width": 864, "height": 1536},
            "4:3": {"width": 1536, "height": 1152},
            "3:4": {"width": 1152, "height": 1536},
            "3:2": {"width": 1536, "height": 1024},
            "2:3": {"width": 1024, "height": 1536},
        }
    
    dimensions = aspect_map.get(aspect_ratio, {"width": 1024, "height": 1024})
    
    # Build request parameters based on model
    params = {
        "prompt": prompt,
        "model": xeven_model,
        "height": dimensions["height"],
        "width": dimensions["width"],
    }
    
    # Model-specific parameters - Using maximum quality settings
    if xeven_model == 'lucid-origin':
        params["guidance"] = 10  # Max guidance for Lucid Origin (0-10)
        params["num_steps"] = 40  # Max steps for best quality (1-40)
    elif xeven_model == 'phoenix':
        params["guidance"] = 10  # Max guidance for Phoenix (2-10)
        params["num_steps"] = 50  # Max steps for best quality (1-50)
        params["negative_prompt"] = "blurry, low quality, distorted, ugly, bad anatomy"
    elif xeven_model == 'flux-schnell':
        params["steps"] = 8  # Max steps for best quality (1-8)
    elif xeven_model in ['sdxl-lightning', 'sdxl']:
        params["guidance"] = 7.5  # Optimal guidance scale
        params["num_steps"] = 20  # Max steps (1-20)
        params["negative_prompt"] = "blurry, low quality, distorted, ugly, bad anatomy"
        params["strength"] = 1.0  # Full strength by default
    
    # Add image_b64 for img2img if input_image_url provided
    # Only SDXL and SDXL Lightning support img2img
    if input_image_url and xeven_model in ['sdxl-lightning', 'sdxl']:
        validate_image_format(input_image_url, ['jpg', 'jpeg', 'png', 'webp'], '[Xeven]')
        try:
            print(f"[Xeven] Fetching input image for img2img: {input_image_url}")
            img_response = requests.get(input_image_url, timeout=30)
            if img_response.status_code == 200:
                b64_img = base64.b64encode(img_response.content).decode('utf-8')
                params["image_b64"] = b64_img
                params["strength"] = 0.75  # Moderate transformation for img2img
                print(f"[Xeven] Added img2img support with strength 0.75")
            else:
                print(f"[Xeven] Warning: Failed to fetch input image (status {img_response.status_code})")
        except Exception as e:
            print(f"[Xeven] Warning: Failed to process input image: {e}")
    elif input_image_url:
        print(f"[Xeven] WARNING: Model {xeven_model} does not support image input (only sdxl and sdxl-lightning support img2img). Input image will be rejected.")
        raise Exception(f"IMAGE_NOT_SUPPORTED: Xeven model '{xeven_model}' does not support image input. Only 'sdxl' and 'sdxl-lightning' models support img2img. Please switch to sdxl-xeven or sdxl-lightning-xeven, or use a different endpoint.")
    
    print(f"[Xeven] Request URL: {base_url}")
    print(f"[Xeven] Request params: {params}")
    
    try:
        response = requests.get(
            base_url,
            params=params,
            timeout=120  # 2 minutes max (API is fast, but be safe)
        )
        
        print(f"[Xeven] Response status: {response.status_code}")
        print(f"[Xeven] Response content-type: {response.headers.get('Content-Type', 'unknown')}")
        
        if response.status_code != 200:
            error_msg = f"Xeven API error {response.status_code}"
            # Try to extract error message from response
            try:
                error_data = response.json()
                if isinstance(error_data, dict) and 'error' in error_data:
                    error_msg += f": {error_data['error']}"
                else:
                    error_msg += f": {response.text[:200]}"
            except:
                error_msg += f": {response.text[:200]}"
            
            print(f"[Xeven] Error: {error_msg}")
            raise Exception(error_msg)
        
        # Response is binary image data (PNG format)
        content_type = response.headers.get('Content-Type', '')
        
        if 'image' in content_type or len(response.content) > 1000:
            # Binary image response
            image_data = response.content
            b64_data = base64.b64encode(image_data).decode('utf-8')
            print(f"[Xeven] Success! Returning base64 image data ({len(image_data)} bytes, {len(b64_data)} chars)")
            return {"success": True, "data": b64_data, "type": "image", "is_base64": True}
        else:
            # Unexpected response format
            raise Exception(f"Xeven API returned unexpected content type: {content_type}. Response: {response.text[:200]}")
        
    except requests.exceptions.Timeout:
        error_msg = "Xeven API request timeout after 120 seconds"
        print(f"[Xeven] Error: {error_msg}")
        raise Exception(error_msg)
    except requests.exceptions.ConnectionError as e:
        error_msg = f"Xeven API connection error: {str(e)}"
        print(f"[Xeven] Error: {error_msg}")
        raise Exception(error_msg)
    except Exception as e:
        print(f"[Xeven] Error: {str(e)}")
        import traceback
        traceback.print_exc()
        raise Exception(f"Xeven generation failed: {str(e)}")


def generate_with_infip(prompt, model, aspect_ratio, api_key, input_image_url=None, job_type="image", duration=5):
    """
    Generate images using Infip.pro API (OpenAI-compatible endpoint)
    https://api.infip.pro/v1/images/generations
    
    Supports 4 models (async models require polling):
    - z-image-turbo: Fast async model
    - qwen: Qwen async model
    - flux2-klein-9b: FLUX 2 Klein 9B
    - flux2-dev: FLUX 2 Dev
    
    API returns task_id for async models, which requires polling via GET /v1/tasks/{task_id}
    
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
    Generate images using deAPI (https://api.deapi.ai)
    https://api.deapi.ai/api/v1/client/txt2img
    
    Supports 2 models (all require async polling):
    - ZImageTurbo_INT8: Fast photorealistic model (4 steps, ultra-fast)
    - Flux1schnell: Fast iteration model (10 steps, high quality)
    
    Workflow:
    1. POST /api/v1/client/txt2img → returns request_id
    2. Poll GET /api/v1/client/request-status/{request_id}
    3. Extract result_url when status = "done"
    
    IMAGE INPUT: NOT SUPPORTED - deAPI txt2img endpoint is text-to-image only.
    deAPI has a separate img2img endpoint but it is not currently integrated.
    """
    deapi_model = DEAPI_MODELS.get(model, model)
    base_url = "https://api.deapi.ai/api/v1/client"
    
    print(f"[deAPI] Running model: {deapi_model}")
    print(f"[deAPI] Aspect ratio: {aspect_ratio}")
    
    if input_image_url:
        print(f"[deAPI] WARNING: Image input is not supported by deAPI txt2img models. Input image will be ignored.")
        raise Exception("IMAGE_NOT_SUPPORTED: deAPI models (ZImageTurbo_INT8, Flux1schnell) do not support image input via the current txt2img endpoint. These are text-to-image models only. Please use a different endpoint for image-to-image tasks.")
    
    # Map aspect ratios to width/height
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
    
    # Log if using non-standard ratio mapping
    if aspect_ratio not in aspect_map:
        print(f"[deAPI] Note: Using default size 1024x1024 for aspect ratio: {aspect_ratio}")
    
    # Model-specific settings
    if deapi_model == "ZImageTurbo_INT8":
        guidance = 3.5
        steps = 4
    elif deapi_model == "Flux1schnell":
        guidance = 7.5
        steps = 10
    else:
        guidance = 7.5
        steps = 20
    
    # Prepare request payload
    payload = {
        "prompt": prompt,
        "model": deapi_model,
        "width": width,
        "height": height,
        "guidance": guidance,
        "steps": steps,
        "seed": -1  # Random seed
    }
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    print(f"[deAPI] Request payload: {payload}")
    
    try:
        # Call deAPI txt2img endpoint
        response = requests.post(
            f"{base_url}/txt2img",
            headers=headers,
            json=payload,
            timeout=30
        )
        
        print(f"[deAPI] Response status: {response.status_code}")
        
        if response.status_code != 200:
            error_msg = f"deAPI error {response.status_code}: {response.text}"
            print(f"[deAPI] Error: {error_msg}")
            raise Exception(error_msg)
        
        result = response.json()
        print(f"[deAPI] Response: {result}")
        
        # Extract request_id
        if "data" in result and "request_id" in result["data"]:
            request_id = result["data"]["request_id"]
            print(f"[deAPI] Request submitted - polling for request: {request_id}")
            
            # Poll for completion
            max_attempts = 60  # 60 attempts * 2 seconds = 120 seconds max
            poll_interval = 2  # seconds
            
            for attempt in range(max_attempts):
                time.sleep(poll_interval)
                
                poll_response = requests.get(
                    f"{base_url}/request-status/{request_id}",
                    headers=headers,
                    timeout=30
                )
                
                if poll_response.status_code != 200:
                    error_msg = f"deAPI polling error {poll_response.status_code}: {poll_response.text}"
                    print(f"[deAPI] Error: {error_msg}")
                    raise Exception(error_msg)
                
                poll_result = poll_response.json()
                
                # Extract status from response
                status = None
                if "data" in poll_result:
                    status = poll_result["data"].get("status")
                
                print(f"[deAPI] Poll attempt {attempt + 1}/{max_attempts}: status = {status}")
                
                if status == "done":
                    # Extract image URL from completed request
                    result_url = poll_result["data"].get("result_url")
                    result_data = poll_result["data"].get("result")
                    
                    # Try to get URL from either field
                    image_url = result_url or result_data
                    
                    if image_url:
                        print(f"[deAPI] Request completed! Image URL: {image_url}")
                        return {"success": True, "url": image_url, "type": "image"}
                    
                    raise Exception(f"deAPI request completed but no image URL found. Response: {poll_result}")
                
                elif status == "error":
                    error_msg = poll_result["data"].get("error", "Unknown error")
                    raise Exception(f"deAPI request failed: {error_msg}")
                
                # Status is still "pending" or "processing", continue polling
            
            # Timeout after max attempts
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


def generate(prompt, model, aspect_ratio, api_key, provider_key=None, input_image_url=None, job_type="image", duration=5, **kwargs):
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
    elif endpoint_type == "xeven":
        return generate_with_xeven(
            prompt=prompt,
            model=model,
            aspect_ratio=aspect_ratio,
            api_key=None,  # Xeven doesn't need API key
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
    else:
        raise Exception(f"Unsupported endpoint type: {endpoint_type}")


class EndpointManager:
    """
    Async wrapper for multi-endpoint generation functions
    Used by workflow engine for model routing
    """
    
    async def generate_image(self, prompt, model, provider_key, aspect_ratio='1:1', input_image_url=None, **kwargs):
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
        from api_key_rotation import handle_api_key_rotation, should_rotate_key
        
        print(f"🔍 [EndpointManager] generate_image - provider_key: {provider_key}, model: {model}")
        
        max_rotation_attempts = 5
        attempt = 0
        
        while attempt < max_rotation_attempts:
            attempt += 1
            
            api_key_data = get_api_key_for_job(model, provider_key=provider_key, job_type='image')
            
            if not api_key_data:
                error_msg = f"NO_API_KEY_AVAILABLE: No API keys found for provider '{provider_key}'"
                print(f"[EndpointManager] {error_msg}")
                raise Exception(error_msg)
            
            api_key = api_key_data.get('api_key')
            api_key_id = api_key_data.get('id')
            
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
                
                return result
                
            except Exception as e:
                error_message = str(e)
                print(f"[EndpointManager] Generation error (attempt {attempt}/{max_rotation_attempts}): {error_message}")
                
                if should_rotate_key(error_message, provider_key):
                    print(f"[EndpointManager] Error requires key rotation, attempting...")
                    
                    rotation_success, next_key = handle_api_key_rotation(
                        api_key_id,
                        provider_key,
                        error_message,
                        job_id=f"workflow-{model}"
                    )
                    
                    if rotation_success and next_key:
                        print(f"[EndpointManager] Rotation successful, retrying with new key...")
                        continue
                    else:
                        print(f"[EndpointManager] Rotation failed or no keys available")
                        raise Exception(f"NO_API_KEY_AVAILABLE: All API keys exhausted for provider '{provider_key}': {error_message}")
                else:
                    print(f"[EndpointManager] Error doesn't require rotation, re-raising...")
                    raise
        
        raise Exception(f"Generation failed after {max_rotation_attempts} rotation attempts")
    
    async def generate_video(self, prompt, model, provider_key, input_image_url=None, duration=5, **kwargs):
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
        from api_key_rotation import handle_api_key_rotation, should_rotate_key
        
        print(f"🔍 [EndpointManager] generate_video - provider_key: {provider_key}, model: {model}")
        
        # Extract aspect_ratio from kwargs to avoid duplicate argument
        aspect_ratio = kwargs.pop('aspect_ratio', '16:9')
        
        max_rotation_attempts = 5
        attempt = 0
        
        while attempt < max_rotation_attempts:
            attempt += 1
            
            api_key_data = get_api_key_for_job(model, provider_key=provider_key, job_type='video')
            
            if not api_key_data:
                error_msg = f"NO_API_KEY_AVAILABLE: No API keys found for provider '{provider_key}'"
                print(f"[EndpointManager] {error_msg}")
                raise Exception(error_msg)
            
            api_key = api_key_data.get('api_key')
            api_key_id = api_key_data.get('id')
            
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
                
                return result
                
            except Exception as e:
                error_message = str(e)
                print(f"[EndpointManager] Generation error (attempt {attempt}/{max_rotation_attempts}): {error_message}")
                
                if should_rotate_key(error_message, provider_key):
                    print(f"[EndpointManager] Error requires key rotation, attempting...")
                    
                    rotation_success, next_key = handle_api_key_rotation(
                        api_key_id,
                        provider_key,
                        error_message,
                        job_id=f"workflow-{model}"
                    )
                    
                    if rotation_success and next_key:
                        print(f"[EndpointManager] Rotation successful, retrying with new key...")
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
