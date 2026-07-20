# Error Message Sanitization - Implementation Documentation

## Date: 2026-05-16

## Problem

When jobs failed due to API key exhaustion or provider rotation failures, raw internal error messages were exposed to users. These messages contained:

- Provider names (e.g., `vision-infip`, `vision-aicc`)
- Internal routing details (e.g., `RR-ROTATION`, `No-delete provider`)
- API key terminology (e.g., `All API keys for vision-infip failed after 2 full service cycles`)
- Internal error prefixes (e.g., `COMPLETION_FAILED: Video generated but status update failed - ...`)
- Raw retry counts and attempt numbers (e.g., `attempt 5 of 4 max`)

### Example of a Leaking Message (Before Fix)

```
All API keys for vision-infip failed after 2 full rotation cycles. Please try again later.
```

This message reveals the internal provider name `vision-infip`, that the system uses multiple API keys, and internal rotation cycle details — all of which should be invisible to users.

---

## Root Cause

Two separate layers both had gaps:

### Layer 1 — Backend (`job_worker_realtime.py`)
Error messages passed directly to `mark_job_failed()` contained f-string interpolations of `provider_key` and raw `error_message` values. Since these strings are stored in the database `error_message` field, they reached the frontend verbatim.

### Layer 2 — Frontend (`src/utils/errorMessages.js`)
The `sanitizeErrorMessage()` function had patterns to catch many technical errors, but the specific format `"All API keys for {provider_name} failed..."` was not matched because the existing pattern `all.?keys.?failed` used `.?` (0–1 chars) between tokens — insufficient to skip words like `"API"`.

Since the message was under 120 characters and contained no JSON/stack characters, it passed the final length check and was returned raw to the user.

---

## Solution

Fixed at **both layers** so no internal message can ever reach a user.

---

## Layer 1 Fixes — Backend (`backend/job_worker_realtime.py`)

All `mark_job_failed()` calls that previously contained provider names, raw error messages, or internal prefixes have been replaced with user-friendly static strings.

### Fixed Messages

| Location | Old Message | New Message |
|----------|-------------|-------------|
| Video — RR rotation exhausted | `All API keys for {provider_key} failed after {N} rotation attempts. Please try again later.` | `🔄 This model is temporarily unavailable. Please try again later or use a different model.` |
| Video — No keys in RR pool | `No API keys available for provider: {provider_key}` | `🔄 This model is temporarily unavailable. Please try again later or use a different model.` |
| Video — Completion failed | `COMPLETION_FAILED: Video generated but status update failed - {error_message}` | `⚠️ Your video was generated but could not be saved. Please try again.` |
| Video — Validation error | raw `error_message` from provider API | `⚠️ This model requires a specific input type that was not provided. Please check your input and try again.` |
| Image — RR rotation exhausted | `All API keys for {provider_key} failed after 2 full rotation cycles. Please try again later.` | `🔄 This model is temporarily unavailable. Please try again later or use a different model.` |
| Image — No keys in RR pool | `No API keys available for provider: {provider_key}` | `🔄 This model is temporarily unavailable. Please try again later or use a different model.` |
| Image — Completion failed | `COMPLETION_FAILED: Image generated but status update failed - {error_message}` | `⚠️ Your image was generated but could not be saved. Please try again.` |
| Image — Validation error | raw `error_message` from provider API | `⚠️ This model requires a specific input type that was not provided. Please check your input and try again.` |
| `reset_job_to_pending` — max retries hit | `Job failed after {N} retry attempts. Last error: {error_message}` | `🔄 This model is temporarily unavailable. Please try again later or use a different model.` |
| `reset_job_to_pending` — fallback retry cap | `Job failed after {N} retry attempts. Last error: {error_message}` | `🔄 This model is temporarily unavailable. Please try again later or use a different model.` |

---

## Layer 2 Fixes — Frontend (`src/utils/errorMessages.js`)

### New Patterns Added

Four new regex patterns were added to `PROVIDER_ERROR_PATTERNS` at the top of the list so they are checked first:

```js
// Catches: "All API keys for vision-infip failed after..."
{ pattern: /all\s+api\s+keys?\s+for\s+\S+\s+failed|all\s+api\s+keys?\s+failed|api\s+keys?\s+failed\s+after/i }

// Catches: "2 full service cycles completed for...", "after 2 full rotation cycles"
{ pattern: /full\s+(service\s+)?cycles?\s+completed|full\s+(service\s+)?cycles?\s+for|after\s+\d+\s+full/i }

// Catches: "No-delete provider", "[RR-ROTATION]", "key rotation failed"
{ pattern: /no-?delete\s+provider|rr.?rotation|key\s+rotation\s+failed|\[rr-rotation\]/i }

// Catches: "provider 'vision-infip' | attempt 5 of 4 max"
{ pattern: /provider\s+['"]?[\w-]+['"]?\s*\|?\s*(attempt|failed|unavailable|skip)/i }
```

### Final Safety Net Added

A catch-all check was added after all regex patterns and string cleanup. If the cleaned message still contains any known internal term, it returns a generic user-friendly message instead of passing through:

```js
const INTERNAL_TERMS = /provider|api.?key|service.?cycle|rotation|rr-rotation|infip|
  replicate\.com|fal\.ai|stability|civitai|runware|failing.?job|
  \bmax\s+retries?\b|\battempt\s+\d+\s+of\s+\d+\b/i;

if (INTERNAL_TERMS.test(str)) {
  return '🔄 This model is temporarily unavailable. Please try again later or use a different model.';
}
```

This ensures that even if a future backend change introduces a new leaking message format, the frontend will still block it.

---

## Defense in Depth

The sanitization now works in two independent layers:

```
Backend mark_job_failed()
    ↓ writes user-friendly string to DB error_message field
    ↓
Frontend receives error_message from SSE / polling
    ↓
sanitizeErrorMessage() checks PROVIDER_ERROR_PATTERNS (specific)
    ↓ no match →
HTTP_STATUS_PATTERNS (status codes)
    ↓ no match →
String cleanup (removes sk- tokens, URLs, long numbers)
    ↓
INTERNAL_TERMS safety net (catch-all for any remaining technical terms)
    ↓ still passes →
Length/complexity check (< 120 chars, no JSON/stack)
    ↓ fails →
Generic fallback: "❌ Generation failed. Please try again or select a different model."
```

No internal technical detail can reach the user even if only one layer works.

---

## User-Facing Error Message Reference

| Situation | Message Shown to User |
|-----------|----------------------|
| All API keys exhausted for a provider | `🔄 This model is temporarily unavailable. Please try again later or use a different model.` |
| Max retry attempts reached | `🔄 This model is temporarily unavailable. Please try again later or use a different model.` |
| No API keys available | `🔄 This model is temporarily unavailable. Please try again later or use a different model.` |
| Video generated but save failed | `⚠️ Your video was generated but could not be saved. Please try again.` |
| Image generated but save failed | `⚠️ Your image was generated but could not be saved. Please try again.` |
| Model requires specific input type | `⚠️ This model requires a specific input type that was not provided. Please check your input and try again.` |
| Input image could not be loaded | `⚠️ Your uploaded image could not be loaded. Please re-upload your image and try again.` |
| Content policy / NSFW | `⚠️ Your prompt was flagged by content filters. Please modify your prompt and try again.` |
| Rate limit | `⏳ Too many requests right now. Please wait a moment and try again.` |
| Quota exceeded | `🔄 This model has reached its limit. Please try a different model.` |
| Network / timeout | `🌐 Network error. Please check your connection and try again.` |
| Unknown / catch-all | `❌ Generation failed. Please try again or select a different model.` |

---

## Files Changed

| File | Change |
|------|--------|
| `backend/job_worker_realtime.py` | Replaced 10 leaking `mark_job_failed()` messages with user-friendly strings |
| `src/utils/errorMessages.js` | Added 4 new regex patterns for provider/rotation error formats |
| `src/utils/errorMessages.js` | Added `INTERNAL_TERMS` safety net catch-all before the length check |
