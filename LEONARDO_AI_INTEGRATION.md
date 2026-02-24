# Leonardo AI Integration

## Overview
Leonardo AI has been integrated into the multi-endpoint manager with support for **2 models**: 1 image generation model and 1 video generation model.

---

## ✅ Implemented Models

### **Vision-Leonardo (Image Generation)**

| Model Name | Leonardo Model ID | Type | Features |
|------------|------------------|------|----------|
| **Ideogram 3.0** | `ideogram-v3.0` | Image | Text rendering specialist, TURBO mode, style presets |

**Capabilities:**
- ✅ Text-to-Image generation
- ✅ Aspect ratio support (1:1, 16:9, 9:16, 4:3, 3:4, 3:2, 2:3)
- ✅ Dimension range: 1008x1008 to 1792x1008
- ✅ Generation modes: TURBO (fast), BALANCED, QUALITY
- ✅ Style presets support (optional)
- ✅ Async polling-based generation

### **Cinematic-Leonardo (Video Generation)**

| Model Name | Leonardo Model ID | Type | Features |
|------------|------------------|------|----------|
| **Seedance 1.0 Pro Fast** | `seedance-1.0-pro-fast` | Video | Fast video generation, strong prompt following |

**Capabilities:**
- ✅ Text-to-Video generation
- ✅ Aspect ratio support (1:1, 16:9, 9:16, 4:3, 3:4, 3:2, 2:3)
- ✅ Dimension range: 1080x1080 to 1920x1080
- ✅ Duration: 3-10 seconds
- ✅ Rich details & diverse stylistic capabilities
- ✅ Async polling-based generation

---

## 🔧 API Implementation Details

### **Base URL**
```
https://cloud.leonardo.ai/api/rest/v2/generations
```

### **Authentication**
```
Authorization: Bearer <LEONARDO_API_KEY>
```

### **Generation Flow**
1. **Submit Request** → POST `/v2/generations`
   - Returns `generationId`
2. **Poll Status** → GET `/v1/generations/{generationId}`
   - Poll every 5 seconds
   - Max timeout: 5 minutes (image), 10 minutes (video)
3. **Extract Result** → `generations_by_pk.generated_images[0].url` (image) or `.motionMP4URL` (video)

### **Request Format**

#### Image Generation (Ideogram 3.0)
```json
{
  "model": "ideogram-v3.0",
  "public": false,
  "parameters": {
    "prompt": "your prompt here",
    "width": 1024,
    "height": 1024,
    "quantity": 1,
    "mode": "TURBO"
  }
}
```

#### Video Generation (Seedance 1.0 Pro Fast)
```json
{
  "model": "seedance-1.0-pro-fast",
  "public": false,
  "parameters": {
    "prompt": "your prompt here",
    "width": 1920,
    "height": 1080,
    "duration": 5,
    "quantity": 1
  }
}
```

---

## 📋 Code Changes

### **1. Added Leonardo Models Dictionary**
```python
# Leonardo AI Models - https://cloud.leonardo.ai/api/rest/v1 & v2
LEONARDO_MODELS = {
    # V2 Image Models
    'ideogram-3.0': 'ideogram-v3.0',  # Text rendering specialist (v2)
    
    # V2 Video Models
    'seedance-1.0-pro-fast': 'seedance-1.0-pro-fast',  # Fast video generation (v2)
}
```

### **2. Updated Provider Routing**
```python
PROVIDER_ROUTING = {
    # ... existing providers ...
    'vision-leonardo': 'leonardo',
    'cinematic-leonardo': 'leonardo',
}
```

### **3. Updated get_endpoint_type()**
Added Leonardo model detection:
```python
if model_name in LEONARDO_MODELS:
    return 'leonardo'
```

### **4. Created generate_with_leonardo() Function**
- Supports both image and video generation
- Aspect ratio to dimensions mapping
- Async polling implementation
- Error handling for failed/timeout generations
- Proper response extraction for images and videos

### **5. Updated generate() Router**
Added Leonardo endpoint routing:
```python
elif endpoint_type == "leonardo":
    return generate_with_leonardo(...)
```

---

## 🎯 Usage Examples

### **Image Generation (Ideogram 3.0)**

```python
from multi_endpoint_manager import generate
from provider_api_keys import get_api_key_for_job

# Get API key from Supabase (automatic round-robin rotation)
api_key_data = get_api_key_for_job(
    model_name="ideogram-3.0",
    provider_key="vision-leonardo",
    job_type="image"
)

result = generate(
    prompt="A majestic lion with the text 'KING OF THE JUNGLE' in bold letters",
    model="ideogram-3.0",
    aspect_ratio="16:9",
    api_key=api_key_data["api_key"],
    provider_key="vision-leonardo",
    job_type="image"
)

# Result: {"success": True, "url": "https://...", "type": "image"}
```

### **Video Generation (Seedance 1.0 Pro Fast)**

```python
from multi_endpoint_manager import generate
from provider_api_keys import get_api_key_for_job

# Get API key from Supabase (automatic round-robin rotation)
api_key_data = get_api_key_for_job(
    model_name="seedance-1.0-pro-fast",
    provider_key="cinematic-leonardo",
    job_type="video"
)

result = generate(
    prompt="A beautiful sunset over the ocean with birds flying, cinematic",
    model="seedance-1.0-pro-fast",
    aspect_ratio="16:9",
    api_key=api_key_data["api_key"],
    provider_key="cinematic-leonardo",
    job_type="video",
    duration=5
)

# Result: {"success": True, "url": "https://...", "type": "video"}
```

### **Automatic Provider Detection**

The system can automatically detect the provider from the model name:

```python
# No provider_key needed - auto-detected from model name
api_key_data = get_api_key_for_job(
    model_name="ideogram-3.0",
    job_type="image"  # Automatically maps to "vision-leonardo"
)
```

---

## ⚙️ API Key Management

Leonardo AI keys are stored in **Supabase** (Worker1), not in environment variables.

### **Add Leonardo Provider and Keys**

Use the `manage_provider_keys.py` script:

```bash
# 1. Add vision-leonardo provider
python manage_provider_keys.py --add-provider vision-leonardo

# 2. Add API key(s) to vision-leonardo
python manage_provider_keys.py --add-key vision-leonardo "your_leonardo_api_key_here"

# 3. Add cinematic-leonardo provider
python manage_provider_keys.py --add-provider cinematic-leonardo

# 4. Add API key(s) to cinematic-leonardo
python manage_provider_keys.py --add-key cinematic-leonardo "your_leonardo_api_key_here"

# Optional: Add multiple keys at once
python manage_provider_keys.py --add-bulk vision-leonardo

# List all providers and keys
python manage_provider_keys.py --list
```

### **Supabase Tables**

API keys are stored in Worker1 Supabase:
- **Table**: `providers` - Stores provider names (`vision-leonardo`, `cinematic-leonardo`)
- **Table**: `provider_api_keys` - Stores API keys with round-robin rotation
- **Table**: `deleted_api_keys` - Archives deleted/invalid keys

---

## 🔄 Aspect Ratio Mappings

### **Image (Ideogram 3.0)**
| Aspect Ratio | Width | Height |
|--------------|-------|--------|
| 1:1 | 1024 | 1024 |
| 16:9 | 1792 | 1008 |
| 9:16 | 1008 | 1792 |
| 4:3 | 1536 | 1152 |
| 3:4 | 1152 | 1536 |
| 3:2 | 1536 | 1024 |
| 2:3 | 1024 | 1536 |

### **Video (Seedance 1.0 Pro Fast)**
| Aspect Ratio | Width | Height | Quality |
|--------------|-------|--------|---------|
| 1:1 | 1440 | 1440 | Square |
| 16:9 | 1920 | 1080 | 1080p Landscape |
| 9:16 | 1080 | 1920 | 1080p Portrait |
| 4:3 | 1440 | 1080 | Standard |
| 3:4 | 1080 | 1440 | Portrait |
| 3:2 | 1920 | 1280 | Wide |
| 2:3 | 1280 | 1920 | Tall |

---

## ⏱️ Polling Configuration

| Type | Max Attempts | Poll Interval | Total Timeout |
|------|--------------|---------------|---------------|
| Image | 60 | 5 seconds | 5 minutes |
| Video | 120 | 5 seconds | 10 minutes |

---

## 🚀 Generation Status Flow

```
SUBMIT REQUEST
     ↓
[PENDING] → Poll every 5s
     ↓
[PROCESSING] → Poll every 5s
     ↓
[COMPLETE] → Extract URL & Return
     ↓
SUCCESS
```

**Possible Statuses:**
- `PENDING` - Request queued
- `PROCESSING` - Generation in progress
- `COMPLETE` - Generation successful
- `FAILED` - Generation failed

---

## ⚠️ Limitations & Notes

1. **API Key Required**: Leonardo uses pay-as-you-go (PAYG) pricing
2. **Async Only**: All generations are async (polling required)
3. **No Image-to-Video**: Image-to-video requires pre-uploaded images (not implemented yet)
4. **Rate Limits**: Leonardo API has rate limits and concurrency restrictions
5. **NSFW Filtering**: Built-in safety filters may reject some prompts
6. **Style Presets**: Ideogram 3.0 supports optional style presets (not exposed in current implementation)

---

## 📊 Provider Comparison

| Provider | Image | Video | Sync/Async | Free |
|----------|-------|-------|------------|------|
| Leonardo | ✅ Ideogram 3.0 | ✅ Seedance 1.0 Pro Fast | Async | ❌ |
| Replicate | ✅ 8 models | ✅ 3 models | Sync | ❌ |
| KIE AI | ✅ 2 models | ✅ 1 model | Async | ❌ |
| Xeven | ✅ 5 models | ❌ | Sync | ✅ |

---

## ✅ Testing Checklist

- [ ] Test image generation with Ideogram 3.0
- [ ] Test video generation with Seedance 1.0 Pro Fast
- [ ] Test all aspect ratios (1:1, 16:9, 9:16, 4:3, 3:4, 3:2, 2:3)
- [ ] Test different video durations (3s, 5s, 10s)
- [ ] Test error handling (invalid API key, timeout, failed generation)
- [ ] Test integration with frontend
- [ ] Add to provider API keys management system
- [ ] Update frontend model listings

---

## 🔗 References

- **Leonardo AI Docs**: https://docs.leonardo.ai/docs/getting-started
- **API Reference**: https://docs.leonardo.ai/reference/creategeneration
- **Ideogram 3.0 Guide**: https://docs.leonardo.ai/docs/ideogram-30
- **Video Generation Guide**: https://docs.leonardo.ai/docs/video-generation

---

## 📝 Future Enhancements

1. **Image Upload Support**: Implement image upload for image-to-video
2. **More Models**: Add FLUX Dev, Lucid Origin, Phoenix, Kling 3.0, Veo 3.0
3. **Style Presets**: Expose style preset selection in API
4. **Quality Modes**: Allow selection between TURBO/BALANCED/QUALITY
5. **Webhook Support**: Use webhooks instead of polling (optional)
6. **Batch Generation**: Support quantity > 1

---

**Status**: ✅ **IMPLEMENTED & READY FOR TESTING**

**Last Updated**: 2026-02-15
