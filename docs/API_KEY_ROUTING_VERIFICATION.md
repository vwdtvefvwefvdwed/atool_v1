# API Key Routing Verification

## Worker 1 Supabase Account Configuration

### Environment Variables Required
```env
WORKER_1_URL=<Worker 1 Supabase URL>
WORKER_1_SERVICE_ROLE_KEY=<Worker 1 Service Role Key>
```

### Provider to API Mapping

#### Vision Providers (Image Generation)

**vision-nova** → Replicate API
- Models:
  - google/imagen-4
  - black-forest-labs/flux-kontext-pro
  - ideogram-ai/ideogram-v3-turbo
  - black-forest-labs/flux-1.1-pro
  - black-forest-labs/flux-dev
- API Key Type: `REPLICATE_API_TOKEN`
- SDK: `replicate` Python package

**vision-atlas** → FAL AI
- Models:
  - fal-ai/flux-2-pro
  - fal-ai/nano-banana-pro
  - fal-ai/gpt-image-1.5
  - fal-ai/bytedance/seedream/v4/text-to-image
- API Key Type: `FAL_KEY`
- SDK: `fal-client` Python package

**vision-flux** → FAL AI
- Models: (same as vision-atlas)
  - fal-ai/flux-2-pro
  - fal-ai/nano-banana-pro
  - fal-ai/gpt-image-1.5
  - fal-ai/bytedance/seedream/v4/text-to-image
- API Key Type: `FAL_KEY`
- SDK: `fal-client` Python package

#### Cinematic Providers (Video Generation)

**cinematic-nova** → Replicate API
- Models:
  - minimax/video-01
  - luma/reframe-video
  - topazlabs/video-upscale
- API Key Type: `REPLICATE_API_TOKEN`
- SDK: `replicate` Python package

**cinematic-pro** → FAL AI
- Models:
  - fal-ai/kling-video/v2.5-turbo/pro/image-to-video
  - fal-ai/minimax/hailuo-02-fast/image-to-video
  - fal-ai/minimax/hailuo-02/standard/image-to-video
  - fal-ai/bytedance/seedance/v1/lite/text-to-video
- API Key Type: `FAL_KEY`
- SDK: `fal-client` Python package

**cinematic-x** → FAL AI
- Models: (same as cinematic-pro)
  - fal-ai/kling-video/v2.5-turbo/pro/image-to-video
  - fal-ai/minimax/hailuo-02-fast/image-to-video
  - fal-ai/minimax/hailuo-02/standard/image-to-video
  - fal-ai/bytedance/seedance/v1/lite/text-to-video
- API Key Type: `FAL_KEY`
- SDK: `fal-client` Python package

## API Key Fetching Flow

1. **Job Created** → Frontend sends job with `model` and optional `provider_key`
2. **Worker Picks Job** → `job_worker_realtime.py` processes job
3. **Get Provider Key**:
   - If `provider_key` exists in job metadata → use it
   - Otherwise → map from model name using `map_model_to_provider()`
4. **Fetch API Key** → `get_api_key_for_job(model_name, provider_key, job_type)`
   - Queries Worker 1 Supabase: `provider_api_keys` table
   - Filters: `provider_key`, `is_active=true`
   - Orders: `priority DESC`, `usage_count ASC`
   - Returns: `api_key`, `api_secret`, `additional_config`, `id`
5. **Route to Endpoint** → `multi_endpoint_manager.py`
   - Replicate models → `generate_with_replicate()`
   - FAL AI models → `generate_with_fal()`
6. **Update Usage** → `increment_usage_count(api_key_id)`

## Database Schema (Worker 1)

### Table: `provider_api_keys`
```sql
CREATE TABLE provider_api_keys (
  id UUID PRIMARY KEY,
  provider_key TEXT NOT NULL,
  provider_name TEXT,
  api_key TEXT NOT NULL,
  api_secret TEXT,
  additional_config JSONB,
  is_active BOOLEAN DEFAULT true,
  priority INTEGER DEFAULT 0,
  usage_count INTEGER DEFAULT 0,
  last_used_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT NOW()
);
```

### Required Provider Keys in Worker 1:
- `vision-nova` (Replicate API key)
- `vision-atlas` (FAL AI key)
- `vision-flux` (FAL AI key)
- `cinematic-nova` (Replicate API key)
- `cinematic-pro` (FAL AI key)
- `cinematic-x` (FAL AI key)

## Image Upload Support

### Image-to-Image Models (Image Generation)
- `black-forest-labs/flux-kontext-pro` ✅

### Image-to-Video Models (Video Generation)
- `minimax/video-01` ✅
- `fal-ai/kling-video/v2.5-turbo/pro/image-to-video` ✅
- `fal-ai/minimax/hailuo-02-fast/image-to-video` ✅
- `fal-ai/minimax/hailuo-02/standard/image-to-video` ✅

### Video Input Models
- `luma/reframe-video` ✅
- `topazlabs/video-upscale` ✅

### Text-to-Image/Video Models (No Image Upload)
- All other models ❌

## Verification Checklist

- [x] Worker 1 Supabase client initialized
- [x] Provider keys mapped to correct API endpoints
- [x] Model names mapped to correct providers
- [x] API keys fetched from Worker 1 database
- [x] Usage count incremented after successful generation
- [x] Image upload only shown for appropriate models
- [x] Replicate SDK integration complete
- [x] FAL AI SDK integration complete
- [x] Base64 and URL outputs handled
