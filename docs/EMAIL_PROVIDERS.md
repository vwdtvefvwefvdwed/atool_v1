# Email Provider System — Magic Link Sending

## Overview

Magic link emails are sent through a **4-provider cascade with 2 full cycles**. Each provider is attempted in order. If one fails, the system automatically falls back to the next and fires an ntfy notification. After all 4 providers fail in cycle 1, the system **restarts from Brevo** (cycle 2). Only after cycle 2 is fully exhausted does it hard fail.

---

## Provider Chain

```
User requests magic link
        │
        ▼
━━━━━━━━━━━━━━━━━━━━━━ CYCLE 1 ━━━━━━━━━━━━━━━━━━━━━━
        │
        ▼
┌─────────────────┐
│   1. BREVO      │  ← Primary
│  (sib-api-v3)   │
└────────┬────────┘
         │ success → email sent ✅
         │ any error ↓  notify (NTFY_API_KEYS or NTFY_CRITICAL)
         ▼
┌─────────────────┐
│  2. MAILTRAP    │  ← Secondary
│  (mailtrap SDK) │
└────────┬────────┘
         │ success → email sent ✅
         │ any error ↓  notify (NTFY_API_KEYS or NTFY_CRITICAL)
         ▼
┌─────────────────┐
│   3. RESEND     │  ← Tertiary
│  (resend SDK)   │
└────────┬────────┘
         │ success → email sent ✅
         │ any error ↓  notify (NTFY_CRITICAL)
         ▼
┌─────────────────┐
│   4. LOOPS      │  ← Last Resort
│  (REST/requests)│
└────────┬────────┘
         │ success → email sent ✅
         │ any error ↓  notify (NTFY_API_KEYS): "cycling back to Brevo"
         ▼
━━━━━━━━━━━━━━━━━━━━━━ CYCLE 2 ━━━━━━━━━━━━━━━━━━━━━━
        │
        ▼
┌─────────────────┐
│   1. BREVO      │  ← Primary (retry)
└────────┬────────┘
         │ success → email sent ✅
         │ any error ↓  notify (NTFY_API_KEYS or NTFY_CRITICAL)
         ▼
┌─────────────────┐
│  2. MAILTRAP    │  ← Secondary (retry)
└────────┬────────┘
         │ success → email sent ✅
         │ any error ↓  notify (NTFY_API_KEYS or NTFY_CRITICAL)
         ▼
┌─────────────────┐
│   3. RESEND     │  ← Tertiary (retry)
└────────┬────────┘
         │ success → email sent ✅
         │ any error ↓  notify (NTFY_CRITICAL)
         ▼
┌─────────────────┐
│   4. LOOPS      │  ← Last Resort (retry)
└────────┬────────┘
         │ success → email sent ✅
         │ any error ↓
         ▼
   Hard Fail ❌
   ntfy CRITICAL: "ALL email providers are down"
   User sees: "Email service temporarily unavailable."
```

---

## Provider 1 — Brevo

**File:** `brevo_manager.py`  
**SDK:** `sib-api-v3-sdk==7.6.0`  
**Free tier:** 300 emails/day  
**Integration:** Raw HTML — no template needed  
**Env vars required:**

| Variable | Description |
|---|---|
| `BREVO_API_KEY` | Brevo dashboard → SMTP & API → API Keys |
| `BREVO_SENDER_EMAIL` | Verified sender email in Brevo |
| `BREVO_SENDER_NAME` | Display name (default: `Ashel-Free AI Studio`) |

### Error Handling

| HTTP Status | Brevo Error | ErrorType | Next Action |
|---|---|---|---|
| `400` | `missing_parameter`, `invalid_parameter`, `out_of_range` | `BREVO_BAD_REQUEST` | Fallback to Mailtrap |
| `401` | API key missing, invalid, or API access not activated | `BREVO_UNAUTHORIZED` | Fallback to Mailtrap |
| `402` | `not_enough_credits`, account not activated | `BREVO_PAYMENT_REQUIRED` | Fallback to Mailtrap |
| `403` | `permission_denied` | `BREVO_FORBIDDEN` | Fallback to Mailtrap |
| `403` + body `suspended` / `account_under_validation` | Account suspended or under review | `BREVO_ACCOUNT_SUSPENDED` | Fallback to Mailtrap |
| `404` | `document_not_found` (sender ID not found) | `BREVO_NOT_FOUND` | Fallback to Mailtrap |
| `429` | Rate limit or quota exceeded (daily/monthly) | `BREVO_QUOTA_EXCEEDED` | Fallback to Mailtrap |
| `5xx` | Brevo server error | `BREVO_FALLBACK_TO_RESEND` | Fallback to Mailtrap |
| Any other | Unknown error | `BREVO_FALLBACK_TO_RESEND` | Fallback to Mailtrap |

---

## Provider 2 — Mailtrap

**File:** `mailtrap_manager.py`  
**SDK:** `mailtrap==2.0.1`  
**Free tier:** 1,000 emails/month  
**Integration:** Raw HTML — no template needed  
**Endpoint:** `POST https://send.api.mailtrap.io/api/send`  
**Env vars required:**

| Variable | Description |
|---|---|
| `MAILTRAP_API_TOKEN` | Mailtrap dashboard → Settings → API Tokens |
| `MAILTRAP_SENDER_EMAIL` | Verified sender email in Mailtrap |
| `MAILTRAP_SENDER_NAME` | Display name (default: `Ashel-Free AI Studio`) |

### Error Handling

| HTTP Status | Mailtrap Error | ErrorType | Next Action |
|---|---|---|---|
| `400` | Validation error, malformed payload, invalid fields | `MAILTRAP_BAD_REQUEST` | Fallback to Resend |
| `401` | Invalid or missing API token, unverified sender domain | `MAILTRAP_UNAUTHORIZED` | Fallback to Resend |
| `403` | Sender domain not verified, insufficient permissions | `MAILTRAP_FORBIDDEN` | Fallback to Resend |
| `429` | Rate limit exceeded | `MAILTRAP_RATE_LIMITED` | Fallback to Resend |
| `500` | Mailtrap-side internal server error | `MAILTRAP_SERVER_ERROR` | Fallback to Resend |
| Any other | Unknown error | `MAILTRAP_FALLBACK_TO_RESEND` | Fallback to Resend |

---

## Provider 3 — Resend

**File:** `resend_manager.py`  
**SDK:** `resend==0.8.0`  
**Free tier:** 3,000 emails/month  
**Integration:** Raw HTML — no template needed  
**Env vars required:**

| Variable | Description |
|---|---|
| `RESEND_API_KEY` | Resend dashboard → API Keys |
| `EMAIL_FROM` | Verified sender email in Resend |
| `RESEND_SENDER_NAME` | Display name (default: `Ashel-Free AI Studio`) |

### Error Handling

| HTTP Status | Resend Error Type | ErrorType | Next Action |
|---|---|---|---|
| `400` | `validation_error`, `invalid_idempotency_key` | `RESEND_VALIDATION_ERROR` | Fallback to Loops |
| `401` | `missing_api_key`, `restricted_api_key` | `RESEND_UNAUTHORIZED` | Fallback to Loops |
| `403` | `invalid_api_key`, sending to unverified address | `RESEND_FORBIDDEN` | Fallback to Loops |
| `429` | `rate_limit_exceeded` | `RESEND_RATE_LIMITED` | Fallback to Loops |
| `429` | `daily_quota_exceeded` | `RESEND_DAILY_QUOTA_EXCEEDED` | Fallback to Loops |
| `429` | `monthly_quota_exceeded` | `RESEND_MONTHLY_QUOTA_EXCEEDED` | Fallback to Loops |
| `500` | `application_error`, infrastructure issue | `RESEND_SERVER_ERROR` | Fallback to Loops |
| Any other | Unknown error | `RESEND_SEND_FAILED` | Fallback to Loops |

---

## Provider 4 — Loops

**File:** `loops_manager.py`  
**SDK:** None — uses `requests` (already in requirements.txt)  
**Free tier:** 4,000 emails/month  
**Integration:** ⚠️ Template-based — requires one-time setup in Loops dashboard  
**Endpoint:** `POST https://app.loops.so/api/v1/transactional`  
**Env vars required:**

| Variable | Description |
|---|---|
| `LOOPS_API_KEY` | Loops dashboard → Settings → API Keys |
| `LOOPS_TRANSACTIONAL_ID` | ID of the magic link template in Loops |

### One-Time Template Setup

1. Go to **Loops dashboard → Transactional → New Email**
2. Build the magic link email with your branding
3. Add a **data variable** named exactly `magic_link`
4. Insert `{{magic_link}}` as the button/link URL in the template
5. Save and copy the **Transactional ID**
6. Set `LOOPS_TRANSACTIONAL_ID=<copied-id>` in your env

> The `magic_link` URL is passed dynamically on every send — the template only stores the visual design.

### Error Handling

| HTTP Status | Loops Error | ErrorType | Cycle 1 Action | Cycle 2 Action |
|---|---|---|---|---|
| `400` | Missing fields, invalid payload, validation error | `LOOPS_BAD_REQUEST` | Cycle back to Brevo + ntfy `LOOPS_CYCLING_TO_BREVO` | Hard fail + ntfy critical |
| `401` | Invalid or missing API key | `LOOPS_UNAUTHORIZED` | Cycle back to Brevo + ntfy `LOOPS_CYCLING_TO_BREVO` | Hard fail + ntfy critical |
| `404` | `LOOPS_TRANSACTIONAL_ID` wrong or template deleted | `LOOPS_NOT_FOUND` | Cycle back to Brevo + ntfy `LOOPS_CYCLING_TO_BREVO` | Hard fail + ntfy critical |
| `409` | Idempotency key already used within 24 hours | `LOOPS_CONFLICT` | Cycle back to Brevo + ntfy `LOOPS_CYCLING_TO_BREVO` | Hard fail + ntfy critical |
| `429` | Rate limit exceeded | `LOOPS_RATE_LIMITED` | Cycle back to Brevo + ntfy `LOOPS_CYCLING_TO_BREVO` | Hard fail + ntfy critical |
| `500` | Loops-side internal server error | `LOOPS_SERVER_ERROR` | Cycle back to Brevo + ntfy `LOOPS_CYCLING_TO_BREVO` | Hard fail + ntfy critical |
| Network error | Timeout or DNS failure | `LOOPS_SEND_FAILED` | Cycle back to Brevo + ntfy `LOOPS_CYCLING_TO_BREVO` | Hard fail + ntfy critical |
| Any other | Unknown error | `LOOPS_SEND_FAILED` | Cycle back to Brevo + ntfy `LOOPS_CYCLING_TO_BREVO` | Hard fail + ntfy critical |

> **Cycle 1:** When Loops fails, the system restarts from Brevo (cycle 2). ntfy fires `LOOPS_CYCLING_TO_BREVO` on `NTFY_API_KEYS`.  
> **Cycle 2:** When Loops fails again, **all providers are truly exhausted**. The user sees:  
> `"Email service temporarily unavailable. Please try again later."`  
> A critical ntfy alert is fired: `"CRITICAL: ALL email providers are down"`

---

## Notifications (ntfy)

Every fallback and hard fail fires an ntfy push notification via `error_notifier.py`.

| Severity | Channel | When |
|---|---|---|
| `api_keys` | `NTFY_API_KEYS` | Quota exceeded, bad request, rate limit, fallback triggered |
| `critical` | `NTFY_CRITICAL` | Unauthorized, payment required, account suspended, all providers down |

---

## Files Reference

| File | Role |
|---|---|
| `brevo_manager.py` | Brevo SDK wrapper + error classifier + ntfy notify |
| `mailtrap_manager.py` | Mailtrap SDK wrapper + error classifier + ntfy notify |
| `resend_manager.py` | Resend SDK wrapper + error classifier + ntfy notify |
| `loops_manager.py` | Loops REST wrapper + `LoopsError` class + error classifier + ntfy notify |
| `auth.py` → `send_magic_link()` | Orchestrates the 4-provider cascade |
| `error_notifier.py` | `ErrorType` enum + ntfy push notification sender |

---

## Required Environment Variables (all 4 providers)

```env
# Brevo (primary)
BREVO_API_KEY=
BREVO_SENDER_EMAIL=
BREVO_SENDER_NAME=Ashel-Free AI Studio

# Mailtrap (secondary)
MAILTRAP_API_TOKEN=
MAILTRAP_SENDER_EMAIL=
MAILTRAP_SENDER_NAME=Ashel-Free AI Studio

# Resend (tertiary)
RESEND_API_KEY=
EMAIL_FROM=
RESEND_SENDER_NAME=Ashel-Free AI Studio

# Loops (last resort) — requires one-time template setup
LOOPS_API_KEY=
LOOPS_TRANSACTIONAL_ID=
```
