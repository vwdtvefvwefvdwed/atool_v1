# Provider API Keys - Usage Guide

Simple management for provider API keys with a column-based structure.

## Table Structure

```
==============================================================
Key #     openai              replicate           fal-ai
==============================================================
1         sk-proj-abc123...   r8_xyz789...        fal_key1...
2         sk-proj-def456...   r8_uvw012...        fal_key2...
3         sk-proj-ghi789...   -                   fal_key3...
==============================================================

Total: 3 provider(s), 8 key(s)
```

---

## Quick Command Reference

| Action | Command |
|--------|---------|
| List all | `python manage_provider_keys.py --list` |
| List one provider | `python manage_provider_keys.py --list --provider openai` |
| Add provider | `python manage_provider_keys.py --add-provider openai` |
| Add single key | `python manage_provider_keys.py --add-key openai sk-xxx` |
| Add multiple keys | `python manage_provider_keys.py --add-bulk openai` |
| Update key | `python manage_provider_keys.py --update-key openai 3 sk-new` |
| Delete key | `python manage_provider_keys.py --delete-key openai 3` |
| Delete provider | `python manage_provider_keys.py --delete-provider openai` |

---

## 1. List All Providers and Keys

```bash
python manage_provider_keys.py --list
```

**Output:**
```
==============================================================
Key #     openai              replicate           fal-ai
==============================================================
1         sk-proj-abc123...   r8_xyz789...        fal_key1...
2         sk-proj-def456...   r8_uvw012...        fal_key2...
3         sk-proj-ghi789...   -                   fal_key3...
==============================================================

Total: 3 provider(s), 8 key(s)
```

### List Single Provider

```bash
python manage_provider_keys.py --list --provider openai
```

---

## 2. Add a New Provider

```bash
python manage_provider_keys.py --add-provider openai

[OK] Provider 'openai' added successfully!
  ID: 1
```

---

## 3. Add API Keys

### Add Single Key

```bash
python manage_provider_keys.py --add-key openai sk-proj-abc123xyz

[OK] Key #1 added to 'openai'
```

### Add Multiple Keys (Bulk)

```bash
python manage_provider_keys.py --add-bulk openai

=== Add Multiple Keys to 'openai' ===
Starting from key #1
Enter API keys one per line. Empty line to finish.

Key #1: sk-proj-key1xxxxxxxxx
Key #2: sk-proj-key2xxxxxxxxx
Key #3: sk-proj-key3xxxxxxxxx
Key #4: sk-proj-key4xxxxxxxxx
Key #5: sk-proj-key5xxxxxxxxx
Key #6: sk-proj-key6xxxxxxxxx
Key #7: sk-proj-key7xxxxxxxxx
Key #8: sk-proj-key8xxxxxxxxx
Key #9: sk-proj-key9xxxxxxxxx
Key #10: sk-proj-key10xxxxxxxx
Key #11: 

[OK] Added 10 key(s) to 'openai'
  Keys #1 to #10
```

---

## 4. Update a Key

Update key #3 for openai:

```bash
python manage_provider_keys.py --update-key openai 3 sk-proj-newkey123

[OK] Key #3 updated for 'openai'
```

---

## 5. Delete a Key

Delete key #3 from openai:

```bash
python manage_provider_keys.py --delete-key openai 3

Delete key #3 from 'openai'? (yes/no): yes

[OK] Key #3 deleted from 'openai'
```

---

## 6. Delete a Provider

Delete provider and all its keys:

```bash
python manage_provider_keys.py --delete-provider openai

Delete provider 'openai' and 10 key(s)? (yes/no): yes

[OK] Provider 'openai' and 10 key(s) deleted
```

---

## Example Workflow

### Step 1: Add providers

```bash
python manage_provider_keys.py --add-provider openai
python manage_provider_keys.py --add-provider replicate
python manage_provider_keys.py --add-provider fal-ai
```

### Step 2: Add keys to each provider

```bash
# Add 10 keys to openai
python manage_provider_keys.py --add-bulk openai

# Add 5 keys to replicate  
python manage_provider_keys.py --add-bulk replicate

# Add 3 keys to fal-ai
python manage_provider_keys.py --add-bulk fal-ai
```

### Step 3: View all keys

```bash
python manage_provider_keys.py --list
```

### Step 4: Update a specific key

```bash
python manage_provider_keys.py --update-key openai 5 sk-new-key-here
```

---

## Database Schema

### providers table
| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL | Auto-increment ID |
| provider_name | TEXT | Unique provider name |
| created_at | TIMESTAMP | When created |

### provider_api_keys table
| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL | Auto-increment ID |
| provider_id | INTEGER | FK to providers |
| key_number | INTEGER | Key number (1, 2, 3...) |
| api_key | TEXT | The actual API key |
| created_at | TIMESTAMP | When created |

---

## SQL Functions

### Get specific key
```sql
SELECT get_api_key('openai', 1);  -- Get key #1 for openai
```

### Get random key (for load balancing)
```sql
SELECT get_random_api_key('openai');  -- Get random openai key
```
