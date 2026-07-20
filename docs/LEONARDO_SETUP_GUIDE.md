# Leonardo AI Setup Guide

## Quick Start

Follow these steps to set up Leonardo AI in your system:

---

## 1️⃣ Add Providers to Supabase

Run these commands to add the Leonardo providers:

```bash
cd backend

# Add vision-leonardo provider (for images)
python manage_provider_keys.py --add-provider vision-leonardo

# Add cinematic-leonardo provider (for videos)
python manage_provider_keys.py --add-provider cinematic-leonardo
```

---

## 2️⃣ Add API Keys

Get your Leonardo AI API key from: https://app.leonardo.ai/settings/api

Then add it to both providers:

```bash
# Add key to vision-leonardo
python manage_provider_keys.py --add-key vision-leonardo "sk-your-leonardo-api-key-here"

# Add key to cinematic-leonardo (can use same key)
python manage_provider_keys.py --add-key cinematic-leonardo "sk-your-leonardo-api-key-here"
```

**Note**: You can add multiple keys for automatic rotation/load balancing:

```bash
# Add more keys using bulk mode
python manage_provider_keys.py --add-bulk vision-leonardo
```

---

## 3️⃣ Verify Setup

List all providers and keys to verify:

```bash
python manage_provider_keys.py --list
```

You should see:

```
==========================================================================
Key #      vision-leonardo        cinematic-leonardo    
==========================================================================
1          sk-proj-xxxxxxxxx...   sk-proj-xxxxxxxxx...  
==========================================================================

Total: 2 provider(s), 2 key(s)
```

---

## 4️⃣ Test Generation

### **Test Image Generation (Ideogram 3.0)**

Create a test script `test_leonardo_image.py`:

```python
from multi_endpoint_manager import generate
from provider_api_keys import get_api_key_for_job

# Get API key from Supabase
api_key_data = get_api_key_for_job(
    model_name="ideogram-3.0",
    provider_key="vision-leonardo",
    job_type="image"
)

print(f"Using API key: {api_key_data['api_key'][:20]}...")

# Generate image
result = generate(
    prompt="A majestic lion with the text 'LEONARDO AI' in bold golden letters",
    model="ideogram-3.0",
    aspect_ratio="16:9",
    api_key=api_key_data["api_key"],
    provider_key="vision-leonardo",
    job_type="image"
)

print(f"Success: {result['success']}")
print(f"Image URL: {result['url']}")
```

Run it:
```bash
python test_leonardo_image.py
```

### **Test Video Generation (Seedance 1.0 Pro Fast)**

Create a test script `test_leonardo_video.py`:

```python
from multi_endpoint_manager import generate
from provider_api_keys import get_api_key_for_job

# Get API key from Supabase
api_key_data = get_api_key_for_job(
    model_name="seedance-1.0-pro-fast",
    provider_key="cinematic-leonardo",
    job_type="video"
)

print(f"Using API key: {api_key_data['api_key'][:20]}...")

# Generate video
result = generate(
    prompt="A beautiful sunset over the ocean with birds flying, cinematic camera movement",
    model="seedance-1.0-pro-fast",
    aspect_ratio="16:9",
    api_key=api_key_data["api_key"],
    provider_key="cinematic-leonardo",
    job_type="video",
    duration=5
)

print(f"Success: {result['success']}")
print(f"Video URL: {result['url']}")
```

Run it:
```bash
python test_leonardo_video.py
```

---

## 5️⃣ Integration with Job Worker

The job worker (`job_worker_realtime.py`) will automatically use Leonardo AI when:
- Model name is `ideogram-3.0` → Routes to `vision-leonardo`
- Model name is `seedance-1.0-pro-fast` → Routes to `cinematic-leonardo`

The API key is fetched automatically from Supabase with round-robin rotation.

---

## 📋 Available Models

| Endpoint | Model | Description | Job Type |
|----------|-------|-------------|----------|
| `vision-leonardo` | `ideogram-3.0` | Text rendering specialist | Image |
| `cinematic-leonardo` | `seedance-1.0-pro-fast` | Fast video generation | Video |

---

## 🔧 Troubleshooting

### Error: "Provider 'vision-leonardo' not found"
```bash
# Add the provider first
python manage_provider_keys.py --add-provider vision-leonardo
```

### Error: "No API keys found for provider"
```bash
# Add at least one API key
python manage_provider_keys.py --add-key vision-leonardo "your_api_key"
```

### Error: "Leonardo API error 401"
- Check that your API key is valid
- Verify you have credits in your Leonardo AI account
- Get a new key from: https://app.leonardo.ai/settings/api

### Error: "Leonardo generation timeout"
- Increase timeout in `generate_with_leonardo()` function
- Check Leonardo AI status page for service issues

### View all keys for a provider
```bash
python manage_provider_keys.py --list --provider vision-leonardo
```

### Update a key
```bash
python manage_provider_keys.py --update-key vision-leonardo 1 "new_api_key"
```

### Delete a key
```bash
python manage_provider_keys.py --delete-key vision-leonardo 1
```

---

## 📊 API Key Rotation

The system uses **round-robin rotation** for API keys:
- Multiple keys are rotated automatically
- Helps distribute API usage and avoid rate limits
- Failed keys are archived to `deleted_api_keys` table

---

## ✅ Checklist

- [x] Leonardo AI integration implemented
- [ ] Providers added to Supabase (`vision-leonardo`, `cinematic-leonardo`)
- [ ] API keys added to both providers
- [ ] Tested image generation with Ideogram 3.0
- [ ] Tested video generation with Seedance 1.0 Pro Fast
- [ ] Frontend updated with new models
- [ ] Documentation added

---

## 🔗 Resources

- **Leonardo AI Website**: https://leonardo.ai
- **API Documentation**: https://docs.leonardo.ai
- **Get API Key**: https://app.leonardo.ai/settings/api
- **Pricing**: https://leonardo.ai/pricing (Pay-as-you-go)

---

**Setup Complete!** 🎉

Leonardo AI is now integrated and ready to use.
