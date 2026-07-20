# Workflow Engine System - Complete Implementation Plan

## 📋 Table of Contents
1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Database Schema](#database-schema)
4. [Backend Components](#backend-components)
5. [Checkpoint & Resume System](#checkpoint--resume-system)
6. [Error Handling](#error-handling)
7. [Auto-Retry System](#auto-retry-system)
8. [Frontend Integration](#frontend-integration)
9. [API Endpoints](#api-endpoints)
10. [Implementation Phases](#implementation-phases)
11. [Example Workflows](#example-workflows)

---

## 🎯 Overview

### Purpose
Create a modular **workflow engine system** that allows users to execute multi-step AI transformations (e.g., image edit → video generation) with:
- **Automatic checkpointing** after each step
- **Resume capability** from failed steps
- **Smart error recovery** with auto-retry
- **Zero code duplication** across workflows
- **Frontend-agnostic** design (add workflows without frontend changes)

### Key Features
- ✅ Multi-step AI pipelines (img2img → video, etc.)
- ✅ Each step saves output automatically (checkpoints)
- ✅ Resume from exact failure point
- ✅ Distinguish retryable errors (API issues) from permanent errors
- ✅ Auto-retry background service
- ✅ Manual retry via UI
- ✅ Full execution audit trail
- ✅ Support all existing models in the project

---

## 🏗️ Architecture

### System Components

```
┌─────────────────────────────────────────────────────────────┐
│                        Frontend                              │
│  ┌──────────────┐  ┌────────────────┐  ┌─────────────────┐ │
│  │  HomeNew.jsx │→ │ Workflows.jsx  │→ │ WorkflowPlayer  │ │
│  │ (Engine Tool)│  │ (Upload+Select)│  │ (Progress/Result)│ │
│  └──────────────┘  └────────────────┘  └─────────────────┘ │
└─────────────────────────────────────────────────────────────┘
                            ↓ API Calls
┌─────────────────────────────────────────────────────────────┐
│                      Backend (Flask)                         │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  API Routes (app.py)                                 │   │
│  │  /workflows/list                                     │   │
│  │  /workflows/execute                                  │   │
│  │  /workflows/retry/<job_id>                           │   │
│  │  /workflows/execution/<job_id>                       │   │
│  └──────────────────────────────────────────────────────┘   │
│                            ↓                                 │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  WorkflowManager                                     │   │
│  │  - Load workflows from filesystem                    │   │
│  │  - Execute workflows                                 │   │
│  │  - Resume workflows from checkpoints                 │   │
│  └──────────────────────────────────────────────────────┘   │
│                            ↓                                 │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  BaseWorkflow (Abstract Class)                       │   │
│  │  - Auto-checkpointing after each step                │   │
│  │  - Error classification (retryable vs hard)          │   │
│  │  - Resume from saved checkpoints                     │   │
│  └──────────────────────────────────────────────────────┘   │
│                            ↓                                 │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Workflow Implementations (workflows/)               │   │
│  │  - img_edit_to_video/                                │   │
│  │  - style_transfer_video/                             │   │
│  │  - custom_workflow_n/                                │   │
│  └──────────────────────────────────────────────────────┘   │
│                            ↓                                 │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  WorkflowRetryManager (Background Service)           │   │
│  │  - Poll pending_retry jobs every 5 mins              │   │
│  │  - Auto-resume when API keys/quotas available        │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                            ↓ Storage
┌─────────────────────────────────────────────────────────────┐
│                    Supabase Database                         │
│  ┌─────────────────┐  ┌──────────────────────────────────┐  │
│  │  jobs table     │  │  workflow_executions table       │  │
│  │  (existing)     │  │  (new - stores checkpoints)      │  │
│  └─────────────────┘  └──────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 💾 Database Schema

### New Table: `workflow_executions`

```sql
CREATE TABLE workflow_executions (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  job_id UUID REFERENCES jobs(id) ON DELETE CASCADE,
  workflow_id TEXT NOT NULL,
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
  current_step INTEGER DEFAULT 0,
  total_steps INTEGER NOT NULL,
  status TEXT CHECK (status IN ('pending', 'running', 'completed', 'failed', 'pending_retry')) DEFAULT 'pending',
  checkpoints JSONB DEFAULT '{}',
  error_info JSONB,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_workflow_executions_job_id ON workflow_executions(job_id);
CREATE INDEX idx_workflow_executions_status ON workflow_executions(status);
CREATE INDEX idx_workflow_executions_user_id ON workflow_executions(user_id);
```

### Checkpoints JSONB Structure

```json
{
  "0": {
    "step_name": "upload",
    "step_type": "input",
    "status": "completed",
    "output": {
      "image_url": "https://res.cloudinary.com/.../image.jpg",
      "public_id": "abc123",
      "format": "jpg"
    },
    "started_at": "2024-01-01T10:00:00Z",
    "completed_at": "2024-01-01T10:00:15Z"
  },
  "1": {
    "step_name": "image_edit",
    "step_type": "generation",
    "status": "completed",
    "output": {
      "edited_image_url": "https://res.cloudinary.com/.../edited.jpg",
      "model_used": "nano-banana",
      "prompt": "man standing near elon musk"
    },
    "started_at": "2024-01-01T10:00:15Z",
    "completed_at": "2024-01-01T10:02:30Z"
  },
  "2": {
    "step_name": "video_generation",
    "step_type": "generation",
    "status": "failed_retryable",
    "error": "API quota exceeded for minimax/video-01",
    "error_type": "quota_exceeded",
    "retry_count": 2,
    "last_attempt": "2024-01-01T10:05:00Z",
    "started_at": "2024-01-01T10:02:30Z"
  }
}
```

### Update to Existing `jobs` Table

Add new status value:
```sql
-- Add 'pending_retry' status to jobs table
ALTER TABLE jobs 
  DROP CONSTRAINT IF EXISTS jobs_status_check,
  ADD CONSTRAINT jobs_status_check 
  CHECK (status IN ('pending', 'processing', 'completed', 'failed', 'cancelled', 'pending_retry'));

-- Add workflow metadata field
ALTER TABLE jobs 
  ADD COLUMN workflow_metadata JSONB;
```

---

## 🔧 Backend Components

### 1. File Structure

```
backend/
├── workflows/                          # NEW: Workflow scripts folder
│   ├── __init__.py                    # Exports get_all_workflows()
│   ├── base_workflow.py               # Abstract base class
│   ├── errors.py                      # Error types (RetryableError, HardError)
│   │
│   ├── img_edit_to_video/             # Example workflow #1
│   │   ├── __init__.py
│   │   ├── workflow.py                # Implementation
│   │   └── config.json                # Metadata
│   │
│   ├── style_transfer_video/          # Example workflow #2 (future)
│   │   ├── __init__.py
│   │   ├── workflow.py
│   │   └── config.json
│   │
│   └── template/                      # Template for new workflows
│       ├── __init__.py
│       ├── workflow.py
│       └── config.json
│
├── workflow_manager.py                # NEW: Orchestrator
├── workflow_retry_manager.py          # NEW: Auto-retry service
├── app.py                             # Add new routes
└── ...
```

### 2. Workflow Config JSON Schema

Each workflow has a `config.json`:

```json
{
  "id": "img-edit-to-video",
  "name": "Image Edit to Video",
  "description": "Transform your image with AI, then convert to cinematic video",
  "icon": "🎬",
  "category": "transform",
  "preview_video": "https://res.cloudinary.com/demo/video/upload/preview.mp4",
  "preview_thumbnail": "https://res.cloudinary.com/demo/image/upload/thumb.jpg",
  "enabled": true,
  "default_prompts": {
    "image_edit": "man standing near elon musk",
    "video_generation": "cinematic zoom in, 4k quality"
  },
  "supported_models": {
    "image_edit": [
      "nano-banana",
      "flux-kontext-pro"
    ],
    "video_generation": [
      "minimax/video-01",
      "kling/v1.6"
    ]
  },
  "steps": [
    {
      "id": 0,
      "name": "upload",
      "type": "input",
      "description": "Upload your input image",
      "required_input": "image"
    },
    {
      "id": 1,
      "name": "image_edit",
      "type": "generation",
      "description": "AI-powered image transformation",
      "model_type": "image",
      "default_model": "nano-banana"
    },
    {
      "id": 2,
      "name": "video_generation",
      "type": "generation",
      "description": "Convert edited image to video",
      "model_type": "video",
      "default_model": "minimax/video-01"
    }
  ],
  "estimated_duration_seconds": 180,
  "credits_cost": 50
}
```

---

## 🔄 Checkpoint & Resume System

### Core Concepts

1. **Checkpoint = Step Output + Metadata**
   - Every step completion saves its output to DB
   - Outputs are immutable (never overwritten)

2. **Resume = Load Last Checkpoint + Continue**
   - On retry, load checkpoints from DB
   - Skip completed steps
   - Start from first failed/pending step

3. **Stateless Execution**
   - Workflows don't hold state in memory
   - All state in database (can survive server restart)

### Checkpoint Flow

```
Step 0: Upload
  ↓ Success → Save Checkpoint 0 (image_url)
  
Step 1: Image Edit (uses checkpoint 0 output)
  ↓ Success → Save Checkpoint 1 (edited_image_url)
  
Step 2: Video Gen (uses checkpoint 1 output)
  ↓ FAIL (API quota) → Save Checkpoint 2 (status: failed_retryable)
  ↓ Mark job as pending_retry
  ↓ Stop execution

[5 minutes later - Auto Retry Service]
  ↓ Check if quota available
  ↓ Resume workflow from Step 2
  ↓ Load checkpoint 1 output (edited_image_url)
  ↓ Retry Step 2
  ↓ Success → Save Checkpoint 2 (video_url)
  ↓ Mark job as completed
```

---

## ⚠️ Error Handling

### Error Classification

```python
# workflows/errors.py

class WorkflowError(Exception):
    """Base workflow error"""
    pass

class RetryableError(WorkflowError):
    """
    Errors that can be retried automatically
    Examples:
    - API quota exceeded
    - API timeout
    - Invalid/expired API key (can be replaced)
    - Server temporarily unavailable (502, 503)
    - Rate limiting
    """
    def __init__(self, message, error_type='api_error', retry_count=0, retry_after=None):
        super().__init__(message)
        self.error_type = error_type  # quota_exceeded, timeout, rate_limit, etc.
        self.retry_count = retry_count
        self.retry_after = retry_after  # Seconds to wait before retry

class HardError(WorkflowError):
    """
    Permanent errors that cannot be retried
    Examples:
    - Invalid input format
    - Unsupported file type
    - Logic errors in workflow
    - Required parameter missing
    - Image too large/small
    """
    pass
```

### Error Type Mapping

| Error Scenario | Error Class | Action | Retry |
|---------------|-------------|--------|-------|
| API quota exceeded | `RetryableError(type='quota_exceeded')` | Mark `pending_retry`, save checkpoint | ✅ Auto (when quota available) |
| API timeout | `RetryableError(type='timeout')` | Mark `pending_retry`, save checkpoint | ✅ Auto (after 5 min) |
| Invalid API key | `RetryableError(type='invalid_key')` | Mark `pending_retry`, notify admin | ✅ Auto (when key updated) |
| Rate limit (429) | `RetryableError(type='rate_limit', retry_after=60)` | Mark `pending_retry`, wait specified time | ✅ Auto (after wait) |
| Invalid file format | `HardError` | Mark `failed`, notify user | ❌ Manual fix required |
| File too large | `HardError` | Mark `failed`, notify user | ❌ Manual fix required |
| Model not found | `HardError` | Mark `failed`, notify admin | ❌ Configuration error |

### Error Handling in Workflow Steps

```python
# workflows/img_edit_to_video/workflow.py

class ImgEditToVideoWorkflow(BaseWorkflow):
    async def step_generation(self, input_data, step_config):
        try:
            # Call generation API
            result = await self.call_generation_api(
                model=step_config['default_model'],
                input_image=input_data['image_url'],
                prompt=self.config['default_prompts']['image_edit']
            )
            return result
            
        except QuotaExceededError as e:
            # Retryable: quota issue
            raise RetryableError(
                f"Quota exceeded for model {step_config['default_model']}",
                error_type='quota_exceeded',
                retry_count=0
            )
            
        except APITimeoutError as e:
            # Retryable: timeout
            raise RetryableError(
                "API request timed out",
                error_type='timeout',
                retry_count=0
            )
            
        except ValueError as e:
            # Hard error: invalid input
            raise HardError(f"Invalid input data: {e}")
```

---

## 🔁 Auto-Retry System

### Background Service Architecture

```python
# workflow_retry_manager.py

class WorkflowRetryManager:
    def __init__(self):
        self.retry_interval = 300  # 5 minutes
        self.max_retries = 5
        self.running = False
        
    async def start(self):
        """Start background retry loop"""
        self.running = True
        logger.info("Workflow retry manager started")
        
        while self.running:
            try:
                await self.retry_pending_workflows()
            except Exception as e:
                logger.error(f"Retry loop error: {e}")
            
            await asyncio.sleep(self.retry_interval)
    
    async def stop(self):
        """Stop background service"""
        self.running = False
        logger.info("Workflow retry manager stopped")
    
    async def retry_pending_workflows(self):
        """Find and retry workflows marked as pending_retry"""
        # Get all jobs with pending_retry status
        jobs = await supabase.table('jobs')\
            .select('*, workflow_executions(*)')\
            .eq('status', 'pending_retry')\
            .execute()
        
        for job in jobs.data:
            execution = job['workflow_executions'][0]
            
            # Check retry count
            if execution.get('retry_count', 0) >= self.max_retries:
                logger.warning(f"Max retries reached for job {job['id']}")
                await self._mark_failed(job['id'], "Maximum retry attempts exceeded")
                continue
            
            # Check if error condition is resolved
            can_retry = await self._can_retry(execution)
            
            if can_retry:
                logger.info(f"Retrying workflow for job {job['id']}")
                await self._resume_workflow(execution['id'], job['id'])
            else:
                logger.debug(f"Conditions not met for retry: {job['id']}")
    
    async def _can_retry(self, execution):
        """Check if conditions for retry are met"""
        error_info = execution.get('error_info', {})
        error_type = error_info.get('error_type')
        
        if error_type == 'quota_exceeded':
            # Check if quota is available for the model
            model = error_info.get('model')
            return await self._check_quota_available(model)
            
        elif error_type == 'invalid_key':
            # Check if a valid API key exists
            provider = error_info.get('provider')
            return await self._check_api_key_valid(provider)
            
        elif error_type == 'rate_limit':
            # Check if rate limit window has passed
            retry_after = error_info.get('retry_after', 60)
            last_attempt = error_info.get('last_attempt')
            elapsed = (datetime.utcnow() - datetime.fromisoformat(last_attempt)).total_seconds()
            return elapsed >= retry_after
            
        elif error_type == 'timeout':
            # Always retry timeouts
            return True
        
        # Unknown error type - allow retry
        return True
    
    async def _check_quota_available(self, model):
        """Check if model quota is available"""
        quota_manager = get_quota_manager()
        return quota_manager.has_quota(model)
    
    async def _check_api_key_valid(self, provider):
        """Check if provider has valid API key"""
        from provider_api_keys import get_provider_key
        key = get_provider_key(provider)
        return key is not None
    
    async def _resume_workflow(self, execution_id, job_id):
        """Resume workflow execution"""
        from workflow_manager import get_workflow_manager
        
        workflow_manager = get_workflow_manager()
        
        try:
            await workflow_manager.resume_workflow(
                execution_id=execution_id,
                job_id=job_id
            )
        except Exception as e:
            logger.error(f"Failed to resume workflow {execution_id}: {e}")
    
    async def _mark_failed(self, job_id, reason):
        """Mark job as permanently failed"""
        await supabase.table('jobs').update({
            'status': 'failed',
            'error_message': reason,
            'updated_at': datetime.utcnow().isoformat()
        }).eq('id', job_id).execute()

# Global instance
_retry_manager = None

def get_retry_manager():
    global _retry_manager
    if _retry_manager is None:
        _retry_manager = WorkflowRetryManager()
    return _retry_manager

def start_retry_manager():
    """Start retry manager in background thread"""
    manager = get_retry_manager()
    thread = threading.Thread(target=asyncio.run, args=(manager.start(),), daemon=True)
    thread.start()
    return manager
```

---

## 🌐 Frontend Integration

### 1. Add "Engines" Category in HomeNew.jsx

```jsx
const toolsData = {
  generate: [...],
  edit: [...],
  enhance: [...],
  transform: [...],
  engines: [  // NEW CATEGORY
    { 
      id: 'workflow-engine', 
      name: 'AI Workflow Engine', 
      desc: 'Multi-step AI transformations with smart resume',
      icon: Zap,
      color: '#f59e0b',
      path: '/workflows',
      badge: 'NEW'
    },
  ],
};

const categories = [
  { id: 'all', label: 'All Tools', icon: '✨' },
  { id: 'generate', label: 'Generate', icon: '🎨' },
  { id: 'edit', label: 'Edit', icon: '✂️' },
  { id: 'enhance', label: 'Enhance', icon: '⚡' },
  { id: 'transform', label: 'Transform', icon: '🔄' },
  { id: 'engines', label: 'Engines', icon: '⚙️' },  // NEW
];
```

### 2. New Page: Workflows.jsx

**Features:**
- Upload image/video
- Select workflow from dropdown (loaded from `/workflows/list`)
- Preview video showing expected output
- Execute workflow
- Real-time progress with step-by-step status
- Retry button for failed workflows

**Component Structure:**
```jsx
<WorkflowsPage>
  <WorkflowSelector workflows={workflows} onSelect={setSelected} />
  <FileUploader onUpload={setFile} />
  <PreviewVideo url={selectedWorkflow.preview_video} />
  <ExecuteButton onClick={executeWorkflow} />
  <ProgressTracker execution={execution} />
  <ResultDisplay result={result} />
  <RetryButton visible={canRetry} onClick={retryWorkflow} />
</WorkflowsPage>
```

### 3. Workflow Execution Flow

```
User Action                    API Call                     Backend Action
────────────────────────────────────────────────────────────────────────────
1. Upload file                 POST /cloudinary/upload      Upload to Cloudinary
2. Select workflow             GET /workflows/list          Return available workflows
3. Click "Generate"            POST /workflows/execute      Create job + execution
                                                            Start workflow
4. Stream progress             GET /jobs/<id>/stream        SSE: checkpoint updates
5. (If failed) Click "Retry"   POST /workflows/retry/<id>   Resume from checkpoint
```

---

## 🔌 API Endpoints

### 1. List Workflows

```http
GET /workflows/list
```

**Response:**
```json
{
  "workflows": [
    {
      "id": "img-edit-to-video",
      "name": "Image Edit to Video",
      "description": "Transform your image with AI, then convert to cinematic video",
      "icon": "🎬",
      "category": "transform",
      "preview_video": "https://...",
      "preview_thumbnail": "https://...",
      "enabled": true,
      "steps": [
        {"name": "upload", "description": "Upload image"},
        {"name": "image_edit", "description": "AI transformation"},
        {"name": "video_generation", "description": "Video creation"}
      ],
      "estimated_duration_seconds": 180,
      "credits_cost": 50
    }
  ]
}
```

### 2. Execute Workflow

```http
POST /workflows/execute
Authorization: Bearer <token>
Content-Type: multipart/form-data

{
  "workflow_id": "img-edit-to-video",
  "file": <uploaded_file>,
  "options": {
    "models": {
      "image_edit": "nano-banana",
      "video_generation": "minimax/video-01"
    },
    "prompts": {
      "image_edit": "custom prompt",
      "video_generation": "custom video prompt"
    }
  }
}
```

**Response:**
```json
{
  "job_id": "123e4567-e89b-12d3-a456-426614174000",
  "execution_id": "987fcdeb-51a2-4c8d-9e3f-123456789abc",
  "status": "running",
  "stream_url": "/jobs/123e4567-e89b-12d3-a456-426614174000/stream"
}
```

### 3. Get Execution Status

```http
GET /workflows/execution/<job_id>
Authorization: Bearer <token>
```

**Response:**
```json
{
  "id": "987fcdeb-51a2-4c8d-9e3f-123456789abc",
  "job_id": "123e4567-e89b-12d3-a456-426614174000",
  "workflow_id": "img-edit-to-video",
  "status": "pending_retry",
  "current_step": 2,
  "total_steps": 3,
  "checkpoints": {
    "0": {
      "step_name": "upload",
      "status": "completed",
      "output": {"image_url": "https://..."}
    },
    "1": {
      "step_name": "image_edit",
      "status": "completed",
      "output": {"edited_image_url": "https://..."}
    },
    "2": {
      "step_name": "video_generation",
      "status": "failed_retryable",
      "error": "API quota exceeded"
    }
  },
  "can_retry": true,
  "retry_count": 1,
  "created_at": "2024-01-01T10:00:00Z",
  "updated_at": "2024-01-01T10:05:00Z"
}
```

### 4. Retry Workflow

```http
POST /workflows/retry/<job_id>
Authorization: Bearer <token>
```

**Response:**
```json
{
  "message": "Workflow retry started",
  "execution_id": "987fcdeb-51a2-4c8d-9e3f-123456789abc",
  "resume_from_step": 2
}
```

### 5. Stream Progress (Reuse Existing)

```http
GET /jobs/<job_id>/stream
Authorization: Bearer <token>
```

**SSE Events:**
```
event: progress
data: {"step": 0, "step_name": "upload", "progress": 10, "message": "Uploading image..."}

event: progress
data: {"step": 1, "step_name": "image_edit", "progress": 40, "message": "Generating edited image..."}

event: checkpoint
data: {"step": 1, "status": "completed", "output": {"edited_image_url": "https://..."}}

event: progress
data: {"step": 2, "step_name": "video_generation", "progress": 70, "message": "Creating video..."}

event: error
data: {"step": 2, "error": "API quota exceeded", "retryable": true}

event: complete
data: {"result": {"video_url": "https://..."}}
```

---

## 📝 Implementation Phases

### Phase 1: Database & Core Infrastructure (Day 1)
- [ ] Create `workflow_executions` table in Supabase
- [ ] Add `pending_retry` status to `jobs` table
- [ ] Create `workflows/` folder structure
- [ ] Implement `workflows/errors.py` (error classes)
- [ ] Implement `workflows/base_workflow.py` (base class with checkpointing)
- [ ] Implement `workflow_manager.py` (orchestrator)

### Phase 2: Auto-Retry System (Day 1-2)
- [ ] Implement `workflow_retry_manager.py`
- [ ] Add retry manager startup to `app.py`
- [ ] Test retry logic with mock failures

### Phase 3: First Workflow Implementation (Day 2)
- [ ] Create `workflows/img_edit_to_video/` folder
- [ ] Implement `config.json` with metadata
- [ ] Implement `workflow.py` with:
  - Upload step (Cloudinary)
  - Image edit step (nano-banana)
  - Video generation step (minimax)
- [ ] Upload preview video to Cloudinary
- [ ] Test end-to-end execution

### Phase 4: API Routes (Day 2-3)
- [ ] Add `/workflows/list` endpoint
- [ ] Add `/workflows/execute` endpoint
- [ ] Add `/workflows/retry/<job_id>` endpoint
- [ ] Add `/workflows/execution/<job_id>` endpoint
- [ ] Integrate with existing `/jobs/<id>/stream` for progress
- [ ] Test all endpoints with Postman

### Phase 5: Frontend Integration (Day 3-4)
- [ ] Add "Engines" category in `HomeNew.jsx`
- [ ] Create `Workflows.jsx` page
- [ ] Implement workflow selector component
- [ ] Implement file uploader
- [ ] Implement preview video player
- [ ] Implement progress tracker with step indicators
- [ ] Implement retry button
- [ ] Add routing in `App.jsx`

### Phase 6: Testing & Polish (Day 4-5)
- [ ] Test workflow execution with real files
- [ ] Test checkpoint system (kill server mid-execution)
- [ ] Test auto-retry (simulate quota exceeded)
- [ ] Test manual retry via UI
- [ ] Add error notifications
- [ ] Add loading states
- [ ] Mobile responsiveness
- [ ] Documentation updates

### Phase 7: Additional Workflows (Future)
- [ ] Workflow #2: Style Transfer Video
- [ ] Workflow #3: Face Swap + Video
- [ ] Workflow #4: Upscale + Enhance Pipeline
- [ ] Template generator for new workflows

---

## 🎬 Example Workflows

### Example 1: Image Edit to Video

**Folder:** `workflows/img_edit_to_video/`

**config.json:**
```json
{
  "id": "img-edit-to-video",
  "name": "Image Edit to Video",
  "description": "Transform your image with AI, then convert to cinematic video",
  "icon": "🎬",
  "category": "transform",
  "preview_video": "https://res.cloudinary.com/demo/video/upload/v1/preview_img2vid.mp4",
  "preview_thumbnail": "https://res.cloudinary.com/demo/image/upload/v1/preview_img2vid.jpg",
  "enabled": true,
  "default_prompts": {
    "image_edit": "man standing near elon musk, photorealistic, 4k",
    "video_generation": "cinematic zoom in, smooth camera movement, 4k quality"
  },
  "supported_models": {
    "image_edit": ["nano-banana", "flux-kontext-pro"],
    "video_generation": ["minimax/video-01", "kling/v1.6"]
  },
  "steps": [
    {
      "id": 0,
      "name": "upload",
      "type": "input",
      "description": "Upload your input image"
    },
    {
      "id": 1,
      "name": "image_edit",
      "type": "generation",
      "description": "AI-powered image transformation",
      "default_model": "nano-banana"
    },
    {
      "id": 2,
      "name": "video_generation",
      "type": "generation",
      "description": "Convert to cinematic video",
      "default_model": "minimax/video-01"
    }
  ],
  "estimated_duration_seconds": 180,
  "credits_cost": 50
}
```

**workflow.py:**
```python
from workflows.base_workflow import BaseWorkflow
from workflows.errors import RetryableError, HardError
from cloudinary_manager import get_cloudinary_manager
from multi_endpoint_manager import get_endpoint_manager

class ImgEditToVideoWorkflow(BaseWorkflow):
    async def step_upload(self, input_file):
        """Upload image to Cloudinary"""
        try:
            cloudinary = get_cloudinary_manager()
            result = cloudinary.upload_image(input_file)
            
            return {
                "image_url": result['secure_url'],
                "public_id": result['public_id'],
                "format": result['format']
            }
        except Exception as e:
            raise HardError(f"Failed to upload image: {e}")
    
    async def step_image_edit(self, input_data, step_config):
        """Edit image using nano-banana or flux-kontext-pro"""
        try:
            endpoint_manager = get_endpoint_manager()
            
            model = step_config.get('model', 'nano-banana')
            prompt = self.config['default_prompts']['image_edit']
            
            result = await endpoint_manager.generate_image(
                model=model,
                prompt=prompt,
                input_image_url=input_data['image_url'],
                aspect_ratio='1:1'
            )
            
            return {
                "edited_image_url": result['image_url'],
                "model_used": model,
                "prompt": prompt
            }
            
        except QuotaExceededError as e:
            raise RetryableError(
                f"Quota exceeded for {model}",
                error_type='quota_exceeded',
                retry_count=0
            )
        except APITimeoutError as e:
            raise RetryableError(
                "Image generation timed out",
                error_type='timeout'
            )
    
    async def step_video_generation(self, input_data, step_config):
        """Generate video from edited image"""
        try:
            endpoint_manager = get_endpoint_manager()
            
            model = step_config.get('model', 'minimax/video-01')
            prompt = self.config['default_prompts']['video_generation']
            
            result = await endpoint_manager.generate_video(
                model=model,
                prompt=prompt,
                input_image_url=input_data['edited_image_url']
            )
            
            return {
                "video_url": result['video_url'],
                "model_used": model,
                "prompt": prompt,
                "duration": result.get('duration', 5)
            }
            
        except QuotaExceededError as e:
            raise RetryableError(
                f"Quota exceeded for {model}",
                error_type='quota_exceeded'
            )
        except APIKeyInvalidError as e:
            raise RetryableError(
                "Invalid API key",
                error_type='invalid_key'
            )
```

### Example 2: Style Transfer + Video (Future)

**Folder:** `workflows/style_transfer_video/`

**Pipeline:**
1. Upload image
2. Apply artistic style (IllusionDiffusion)
3. Upscale styled image (Topaz)
4. Convert to video (Minimax)

---

## 🔐 Security Considerations

1. **Authentication**: All workflow endpoints require `@require_auth`
2. **Rate Limiting**: Limit workflow executions per user (e.g., 10/hour)
3. **File Validation**: Check file type, size before upload
4. **Sandboxing**: Workflows run with limited permissions
5. **Quota Enforcement**: Check credits before execution
6. **Audit Trail**: All checkpoints logged for debugging

---

## 📊 Monitoring & Observability

### Metrics to Track
- Total workflow executions
- Success/failure rates per workflow
- Average execution time per workflow
- Checkpoint restore rate
- Auto-retry success rate
- Most used workflows

### Logging
- Log every checkpoint save
- Log all errors with full context
- Log retry attempts
- Log execution start/end

### Alerts
- Alert on high failure rate (>20%)
- Alert on stuck workflows (>10 min in single step)
- Alert on checkpoint save failures

---

## 🚀 Future Enhancements

1. **Custom Prompts**: Allow users to override default prompts
2. **Model Selection**: Let users choose models per step
3. **Conditional Steps**: Steps that execute based on previous results
4. **Parallel Steps**: Execute multiple steps simultaneously
5. **Workflow Marketplace**: Share custom workflows
6. **Workflow Analytics**: Show users their execution history
7. **Webhook Notifications**: Notify on completion/failure
8. **Scheduled Workflows**: Batch processing
9. **API for Custom Workflows**: Let developers create workflows via API

---

## 📚 References

- [Multi Endpoint Manager](./multi_endpoint_manager.py) - For API calls
- [Cloudinary Manager](./cloudinary_manager.py) - For file uploads
- [Jobs System](./jobs.py) - For job tracking
- [Quota Manager](./model_quota_manager.py) - For quota checks

---

## ✅ Success Criteria

- [ ] Users can execute multi-step workflows via UI
- [ ] Failed workflows auto-resume when API issues resolved
- [ ] All workflow state survives server restarts
- [ ] Users can manually retry failed workflows
- [ ] New workflows can be added without frontend changes
- [ ] All errors properly classified and handled
- [ ] Full audit trail in database
- [ ] <5% permanent failure rate

---

**Last Updated:** 2024-02-16  
**Status:** Ready for Implementation
