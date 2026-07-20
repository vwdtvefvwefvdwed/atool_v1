# Bria AI Integration Documentation

## Overview

Bria AI provides enterprise-grade APIs for visual content generation and editing. This integration implements two specialized providers focusing on image and video operations.

### Providers

- **vision_bria**: Image generation and editing capabilities
- **cinematic_bria**: Video generation and editing capabilities

### Core Features

- Enterprise-grade quality and safety
- Commercial-use licensed training data
- Asynchronous processing with status polling
- Support for URL and base64 inputs
- Comprehensive error handling

---

## Authentication

All API requests require authentication via API token in the request header:

```
api_token: <your_api_token>
```

**Get API Token**: Register at https://bria.ai/platform

---

## Rate Limits

| Plan Type | Request Limit |
|-----------|---------------|
| Free Trial | 10 requests/min per endpoint |
| Starter | 60 requests/min per endpoint |
| Pro & Enterprise | 1000 requests/min per endpoint |

**Rate Limit Response**: HTTP 429 - Implement retry with exponential backoff

---

## Asynchronous Processing

All V2 endpoints use async processing:

### Request Flow

1. **Submit Request** → Receive `request_id` and `status_url`
2. **Poll Status** → Query status until completed state
3. **Retrieve Result** → Download from `image_url` or `video_url`

### Status Values

- `IN_PROGRESS`: Request accepted and processing
- `COMPLETED`: Processing finished, result available
- `ERROR`: Processing failed, error details provided
- `UNKNOWN`: Unexpected error, contact support with request_id

### Polling Implementation

- Poll interval: 2-5 seconds
- Timeout: 5 minutes (image), 15 minutes (video)
- Retry on network errors

---

## Vision Bria Provider

**Provider Name**: `vision_bria`

**Base URLs**:
- Generation: `https://engine.prod.bria-api.com/v2`
- Editing: `https://engine.prod.bria-api.com/v2/image/edit`

### Image Generation Models

#### bria_image_generate

**Endpoint**: `POST /image/generate`

**Description**: Generate high-quality images using FIBO architecture with Gemini 2.5 Flash Bridge

**Parameters**:
- `prompt` (required, string): Text description of desired image
- `num_results` (optional, integer): Number of images to generate (default: 1, max: 4)
- `aspect_ratio` (optional, string): Image aspect ratio
  - Options: `1:1`, `16:9`, `9:16`, `4:3`, `3:4`, `21:9`, `9:21`
  - Default: `1:1`
- `seed` (optional, integer): Random seed for reproducibility
- `sync` (optional, boolean): Synchronous mode (default: false)

**Response** (async):
```json
{
  "request_id": "uuid",
  "status_url": "https://engine.prod.bria-api.com/v2/status/{request_id}"
}
```

**Response** (status completed):
```json
{
  "status": "COMPLETED",
  "result": {
    "image_url": "https://...",
    "seed": 12345,
    "prompt": "original prompt",
    "refined_prompt": "enhanced prompt used"
  }
}
```

**Processing Time**: 30 seconds - 2 minutes

---

#### bria_image_generate_lite

**Endpoint**: `POST /image/generate/lite`

**Description**: Fast image generation using FIBO Lite pipeline (optimized for speed and privacy)

**Parameters**: Same as `bria_image_generate`

**Response**: Same as `bria_image_generate`

**Processing Time**: 15-45 seconds

**Use Cases**:
- Rapid prototyping
- High-volume generation
- On-premises deployment scenarios
- Data sovereignty requirements

---

#### bria_structured_prompt

**Endpoint**: `POST /structured_prompt/generate`

**Description**: Generate structured JSON prompts for deterministic image generation

**Parameters**:
- `prompt` (required, string): Text description
- `image_file` (optional, base64 string): Reference image
- `image_url` (optional, string): Reference image URL

**Response** (status completed):
```json
{
  "status": "COMPLETED",
  "result": {
    "structured_prompt": {
      "subject": "...",
      "style": "...",
      "composition": "...",
      "lighting": "...",
      "color_palette": "..."
    }
  }
}
```

**Processing Time**: 10-30 seconds

---

### Image Editing Models

#### bria_gen_fill

**Endpoint**: `POST /image/edit/gen_fill`

**Description**: Generative fill for masked areas or object removal with AI-generated content

**Parameters**:
- `image_file` (conditional, base64 string): Input image (base64)
- `image_url` (conditional, string): Input image URL
- `prompt` (required, string): Description of desired fill content
- `mask_file` (optional, base64 string): Mask image (white=fill area, black=preserve)
- `mask_url` (optional, string): Mask image URL
- `sync` (optional, boolean): Synchronous mode (default: false)

**Note**: Provide either `image_file` OR `image_url`, not both

**Response** (status completed):
```json
{
  "status": "COMPLETED",
  "result": {
    "image_url": "https://..."
  }
}
```

**Processing Time**: 45 seconds - 3 minutes

---

#### bria_erase

**Endpoint**: `POST /image/edit/erase`

**Description**: Remove unwanted elements from images

**Parameters**:
- `image_file` (conditional, base64 string): Input image
- `image_url` (conditional, string): Input image URL
- `mask_file` (conditional, base64 string): Mask indicating areas to erase
- `mask_url` (conditional, string): Mask URL
- `prompt` (optional, string): Description of object to erase (auto-masking)
- `sync` (optional, boolean): Synchronous mode (default: false)

**Response**: Same as `bria_gen_fill`

**Processing Time**: 30 seconds - 2 minutes

---

#### bria_remove_background

**Endpoint**: `POST /image/edit/remove_background`

**Description**: Automatically remove background from images

**Parameters**:
- `image_file` (conditional, base64 string): Input image
- `image_url` (conditional, string): Input image URL
- `sync` (optional, boolean): Synchronous mode (default: false)

**Response**: Returns image with transparent background (PNG format)

**Processing Time**: 15-45 seconds

---

#### bria_replace_background

**Endpoint**: `POST /image/edit/replace_background`

**Description**: Replace image background with new content

**Parameters**:
- `image_file` (conditional, base64 string): Input image
- `image_url` (conditional, string): Input image URL
- `background_prompt` (conditional, string): Text description of new background
- `background_image_file` (conditional, base64 string): Background image
- `background_image_url` (conditional, string): Background image URL
- `sync` (optional, boolean): Synchronous mode (default: false)

**Note**: Provide either background prompt OR background image

**Response**: Same as `bria_gen_fill`

**Processing Time**: 45 seconds - 2 minutes

---

#### bria_blur_background

**Endpoint**: `POST /image/edit/blur_background`

**Description**: Apply blur effect to background while keeping foreground sharp

**Parameters**:
- `image_file` (conditional, base64 string): Input image
- `image_url` (conditional, string): Input image URL
- `blur_strength` (optional, float): Blur intensity (0.0 - 1.0, default: 0.5)
- `sync` (optional, boolean): Synchronous mode (default: false)

**Response**: Same as `bria_gen_fill`

**Processing Time**: 20-60 seconds

---

#### bria_erase_foreground

**Endpoint**: `POST /image/edit/erase_foreground`

**Description**: Remove foreground elements while preserving background

**Parameters**:
- `image_file` (conditional, base64 string): Input image
- `image_url` (conditional, string): Input image URL
- `mask_file` (conditional, base64 string): Foreground mask
- `mask_url` (conditional, string): Mask URL
- `prompt` (optional, string): Description of foreground to remove
- `sync` (optional, boolean): Synchronous mode (default: false)

**Response**: Same as `bria_gen_fill`

**Processing Time**: 30 seconds - 2 minutes

---

#### bria_expand

**Endpoint**: `POST /image/edit/expand`

**Description**: Expand image canvas with AI-generated content (outpainting)

**Parameters**:
- `image_file` (conditional, base64 string): Input image
- `image_url` (conditional, string): Input image URL
- `expansion_direction` (required, string): Direction to expand
  - Options: `top`, `bottom`, `left`, `right`, `all`
- `expansion_pixels` (optional, integer): Pixels to add (default: auto)
- `prompt` (optional, string): Context for expansion content
- `sync` (optional, boolean): Synchronous mode (default: false)

**Response**: Same as `bria_gen_fill`

**Processing Time**: 1-4 minutes

---

#### bria_enhance

**Endpoint**: `POST /image/edit/enhance`

**Description**: Increase image resolution and quality (upscaling)

**Parameters**:
- `image_file` (conditional, base64 string): Input image
- `image_url` (conditional, string): Input image URL
- `scale_factor` (optional, integer): Upscale multiplier (2, 4, default: 2)
- `sync` (optional, boolean): Synchronous mode (default: false)

**Response**: Same as `bria_gen_fill`

**Processing Time**: 45 seconds - 3 minutes

---

## Cinematic Bria Provider

**Provider Name**: `cinematic_bria`

**Base URLs**:
- Editing: `https://engine.prod.bria-api.com/v2/video/edit`
- Generation: `https://engine.prod.bria-api.com/v2/video/generate`
- Segmentation: `https://engine.prod.bria-api.com/v2/video/segment`

### Video Editing Models

#### bria_video_erase

**Endpoint**: `POST /video/edit/erase`

**Description**: Remove unwanted elements from videos with temporal consistency

**Parameters**:
- `video_file` (conditional, base64 string): Input video
- `video_url` (conditional, string): Input video URL
- `mask_video_file` (conditional, base64 string): Mask video (per-frame masks)
- `mask_video_url` (conditional, string): Mask video URL
- `prompt` (optional, string): Description of element to erase
- `sync` (optional, boolean): Synchronous mode (default: false)

**Response** (status completed):
```json
{
  "status": "COMPLETED",
  "result": {
    "video_url": "https://..."
  }
}
```

**Processing Time**: 3-15 minutes (depends on video length)

**Supported Formats**: MP4, MOV, AVI

---

#### bria_video_upscale

**Endpoint**: `POST /video/edit/increase_resolution`

**Description**: Upscale video resolution while maintaining quality

**Parameters**:
- `video_file` (conditional, base64 string): Input video
- `video_url` (conditional, string): Input video URL
- `scale_factor` (optional, integer): Upscale multiplier (2, 4, default: 2)
- `sync` (optional, boolean): Synchronous mode (default: false)

**Response**: Same as `bria_video_erase`

**Processing Time**: 5-20 minutes

**Max Input**: 1080p, 60 seconds

---

#### bria_video_remove_bg

**Endpoint**: `POST /video/edit/remove_background`

**Description**: Remove background from videos with temporal consistency

**Parameters**:
- `video_file` (conditional, base64 string): Input video
- `video_url` (conditional, string): Input video URL
- `sync` (optional, boolean): Synchronous mode (default: false)

**Response**: Returns video with transparent background (alpha channel)

**Processing Time**: 4-12 minutes

---

### Video Segmentation Models

#### bria_video_mask_prompt

**Endpoint**: `POST /video/segment/mask_by_prompt`

**Description**: Generate segmentation masks for videos based on text prompts

**Parameters**:
- `video_file` (conditional, base64 string): Input video
- `video_url` (conditional, string): Input video URL
- `prompt` (required, string): Description of object/area to segment
- `sync` (optional, boolean): Synchronous mode (default: false)

**Response** (status completed):
```json
{
  "status": "COMPLETED",
  "result": {
    "mask_video_url": "https://..."
  }
}
```

**Processing Time**: 2-8 minutes

---

#### bria_video_mask_keypoints

**Endpoint**: `POST /video/segment/mask_by_key_points`

**Description**: Generate segmentation masks using spatial keypoints

**Parameters**:
- `video_file` (conditional, base64 string): Input video
- `video_url` (conditional, string): Input video URL
- `key_points` (required, array): Array of {x, y, frame} coordinates
- `sync` (optional, boolean): Synchronous mode (default: false)

**Response**: Same as `bria_video_mask_prompt`

**Processing Time**: 2-8 minutes

---

#### bria_video_foreground_mask

**Endpoint**: `POST /video/segment/foreground_mask`

**Description**: Automatically generate foreground segmentation masks

**Parameters**:
- `video_file` (conditional, base64 string): Input video
- `video_url` (conditional, string): Input video URL
- `sync` (optional, boolean): Synchronous mode (default: false)

**Response**: Same as `bria_video_mask_prompt`

**Processing Time**: 2-6 minutes

---

## Image Input Formats

Bria API supports two input methods:

### 1. Public URL

```json
{
  "image_url": "https://example.com/image.jpg"
}
```

### 2. Base64 Encoded

```json
{
  "image_file": "iVBORw0KGgoAAAANSUhEUgAA..."
}
```

**Important**: Do NOT include base64 headers (e.g., `data:image/png;base64,`)

### Python Base64 Conversion

```python
import base64

def image_to_base64(image_path):
    with open(image_path, "rb") as image_file:
        encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
    return encoded_string

# Usage
base64_string = image_to_base64("example.jpg")
```

---

## Error Handling

### HTTP Status Codes

| Code | Status | Action |
|------|--------|--------|
| 200 | OK | Request accepted |
| 400 | Bad Request | Check parameters |
| 401 | Unauthorized | Validate API token |
| 404 | Not Found | Verify endpoint URL |
| 429 | Rate Limited | Retry with backoff |
| 500 | Server Error | Retry request |

### Error Response Format

```json
{
  "status": "ERROR",
  "error": {
    "code": "error_code",
    "message": "Description of error",
    "details": {}
  }
}
```

### Retry Strategy

```
1st retry: Wait 2 seconds
2nd retry: Wait 4 seconds
3rd retry: Wait 8 seconds
4th retry: Wait 16 seconds
Max retries: 5
```

---

## IP-Related Content Warning

Bria models are trained on fully licensed, commercial-safe data. Prompts referencing public figures or protected brands may produce generic outputs.

**Warning in Response**:
```
This prompt may contain intellectual property (IP)-protected content.
To ensure compliance and safety, certain elements may be omitted or altered.
As a result, the output may not fully meet your request.
```

---

## Usage Examples

### Example 1: Generate Image (Vision Bria)

```python
import requests
import time

API_TOKEN = "your_api_token"
BASE_URL = "https://engine.prod.bria-api.com/v2"

headers = {
    "api_token": API_TOKEN,
    "Content-Type": "application/json"
}

# Submit request
payload = {
    "prompt": "A serene mountain landscape at sunset",
    "aspect_ratio": "16:9",
    "num_results": 1
}

response = requests.post(
    f"{BASE_URL}/image/generate",
    headers=headers,
    json=payload
)

data = response.json()
request_id = data["request_id"]
status_url = data["status_url"]

# Poll for result
while True:
    status_response = requests.get(status_url, headers=headers)
    status_data = status_response.json()
    
    if status_data["status"] == "COMPLETED":
        image_url = status_data["result"]["image_url"]
        print(f"Image ready: {image_url}")
        break
    elif status_data["status"] == "ERROR":
        print(f"Error: {status_data['error']}")
        break
    
    time.sleep(3)
```

### Example 2: Remove Background (Vision Bria)

```python
# Using URL input
payload = {
    "image_url": "https://example.com/photo.jpg"
}

response = requests.post(
    "https://engine.prod.bria-api.com/v2/image/edit/remove_background",
    headers=headers,
    json=payload
)

# Poll status (same as Example 1)
```

### Example 3: Video Background Removal (Cinematic Bria)

```python
payload = {
    "video_url": "https://example.com/video.mp4"
}

response = requests.post(
    "https://engine.prod.bria-api.com/v2/video/edit/remove_background",
    headers=headers,
    json=payload
)

data = response.json()
status_url = data["status_url"]

# Poll for result (may take 4-12 minutes)
while True:
    status_response = requests.get(status_url, headers=headers)
    status_data = status_response.json()
    
    if status_data["status"] == "COMPLETED":
        video_url = status_data["result"]["video_url"]
        print(f"Video ready: {video_url}")
        break
    
    time.sleep(5)
```

---

## Model Summary Tables

### Vision Bria Models

| Model Name | Category | Primary Use Case | Processing Time |
|------------|----------|------------------|-----------------|
| bria_image_generate | Generation | Create images from text | 30s - 2min |
| bria_image_generate_lite | Generation | Fast image creation | 15s - 45s |
| bria_structured_prompt | Generation | Generate structured prompts | 10s - 30s |
| bria_gen_fill | Editing | Fill/replace image areas | 45s - 3min |
| bria_erase | Editing | Remove objects | 30s - 2min |
| bria_remove_background | Editing | Background removal | 15s - 45s |
| bria_replace_background | Editing | Background replacement | 45s - 2min |
| bria_blur_background | Editing | Background blur | 20s - 60s |
| bria_erase_foreground | Editing | Remove foreground | 30s - 2min |
| bria_expand | Editing | Canvas expansion | 1min - 4min |
| bria_enhance | Editing | Upscale resolution | 45s - 3min |

### Cinematic Bria Models

| Model Name | Category | Primary Use Case | Processing Time |
|------------|----------|------------------|-----------------|
| bria_video_erase | Editing | Remove video elements | 3min - 15min |
| bria_video_upscale | Editing | Increase video resolution | 5min - 20min |
| bria_video_remove_bg | Editing | Remove video background | 4min - 12min |
| bria_video_mask_prompt | Segmentation | Text-based video masking | 2min - 8min |
| bria_video_mask_keypoints | Segmentation | Keypoint-based masking | 2min - 8min |
| bria_video_foreground_mask | Segmentation | Auto foreground masking | 2min - 6min |

---

## Best Practices

### 1. Image Quality
- Use high-resolution inputs (min 1024x1024px recommended)
- Provide clear, detailed prompts for generation
- Use appropriate aspect ratios for target platform

### 2. Performance Optimization
- Implement proper polling intervals (2-5s for images, 5-10s for videos)
- Cache request_ids to avoid duplicate submissions
- Use lite models when speed is priority over quality

### 3. Error Recovery
- Always implement exponential backoff for rate limits
- Log request_ids for failed requests
- Set appropriate timeouts based on operation type

### 4. Cost Management
- Monitor usage against rate limits
- Batch operations when possible
- Use async processing efficiently

### 5. Security
- Never expose API tokens in client-side code
- Use HTTPS for all requests
- Validate user inputs before sending to API

---

## Support

**Email**: support@bria.ai

**API Status**: Check service status before reporting issues

**Documentation**: https://docs.bria.ai/

---

## Changelog

### Version 1.0
- Initial integration with Vision Bria and Cinematic Bria providers
- 11 image models (3 generation, 8 editing)
- 6 video models (3 editing, 3 segmentation)
- Async processing with status polling
- Comprehensive error handling
