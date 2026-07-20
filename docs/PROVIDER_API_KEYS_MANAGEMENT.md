# Provider API Keys Management

Simple column-based storage for provider API keys.

## Files

1. **`migrations/021_simplify_provider_api_keys.sql`** - SQL migration for simplified schema
2. **`manage_provider_keys.py`** - Python CLI tool to manage API keys
3. **`PROVIDER_KEYS_USAGE_GUIDE.md`** - Detailed usage guide

## Setup

### 1. Run the SQL Migration

Run in your **Worker1 Supabase account**:

```bash
# Option A: Supabase CLI
supabase db push migrations/021_simplify_provider_api_keys.sql

# Option B: Supabase SQL Editor
# Paste contents of 021_simplify_provider_api_keys.sql and run
```

### 2. Configure Environment

Ensure `.env` has Worker1 credentials:

```env
WORKER_1_URL=https://your-worker1-project.supabase.co
WORKER_1_SERVICE_ROLE_KEY=your-service-role-key
```

## Quick Start

```bash
# Add a provider
python manage_provider_keys.py --add-provider openai

# Add 10 keys to it
python manage_provider_keys.py --add-bulk openai

# View all
python manage_provider_keys.py --list
```

## Commands

| Command | Description |
|---------|-------------|
| `--list` | List all providers and keys |
| `--list --provider NAME` | List keys for one provider |
| `--add-provider NAME` | Add new provider |
| `--add-key PROVIDER KEY` | Add single key |
| `--add-bulk PROVIDER` | Add multiple keys |
| `--update-key PROVIDER NUM KEY` | Update key by number |
| `--delete-key PROVIDER NUM` | Delete key by number |
| `--delete-provider NAME` | Delete provider and all keys |

## Database Schema

### providers
| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL | Auto ID |
| provider_name | TEXT | Unique name |
| created_at | TIMESTAMP | Created time |

### provider_api_keys
| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL | Auto ID |
| provider_id | INTEGER | FK to providers |
| key_number | INTEGER | Key number (1,2,3...) |
| api_key | TEXT | The API key |
| created_at | TIMESTAMP | Created time |

## SQL Functions

```sql
-- Get key #1 for openai
SELECT get_api_key('openai', 1);

-- Get random key for load balancing
SELECT get_random_api_key('openai');
```

## Backend Integration

```python
from supabase import create_client
import os

worker = create_client(
    os.getenv("WORKER_1_URL"),
    os.getenv("WORKER_1_SERVICE_ROLE_KEY")
)

# Get a random API key for openai
result = worker.rpc("get_random_api_key", {"p_provider_name": "openai"}).execute()
api_key = result.data

# Or get specific key #1
result = worker.rpc("get_api_key", {"p_provider_name": "openai", "p_key_number": 1}).execute()
api_key = result.data
```

## Security

- Keys stored in Worker1 Supabase (separate from main)
- RLS enabled - service role only
- Never commit keys to git
