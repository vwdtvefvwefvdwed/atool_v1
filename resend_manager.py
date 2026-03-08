"""
Resend Email Manager
Fallback email provider used when Brevo fails

Resend status codes handled:
  400 - Bad Request          (invalid_idempotency_key, validation_error)
  401 - Unauthorized         (missing_api_key, restricted_api_key)
  403 - Forbidden            (invalid_api_key, sending to unverified address)
  429 - Too Many Requests    (rate_limit_exceeded, daily_quota_exceeded,
                              monthly_quota_exceeded)
  500 - Internal Server Error (application_error, infrastructure issue)
"""

import os
import resend
from dotenv_vault import load_dotenv
from error_notifier import notify_error, ErrorType

load_dotenv()

RESEND_API_KEY = os.getenv("RESEND_API_KEY")
RESEND_SENDER_EMAIL = os.getenv("EMAIL_FROM", "noreply@yourdomain.com")
RESEND_SENDER_NAME = os.getenv("RESEND_SENDER_NAME", "Ashel-Free AI Studio")

resend.api_key = RESEND_API_KEY


def send_email(to_email: str, subject: str, html_content: str, text_content: str) -> None:
    """
    Send a transactional email via Resend.
    Raises on failure so auth.py can handle the hard fail.
    """
    resend.Emails.send({
        "from": f"{RESEND_SENDER_NAME} <{RESEND_SENDER_EMAIL}>",
        "to": [to_email],
        "subject": subject,
        "html": html_content,
        "text": text_content,
    })


def _classify_resend_error(error: Exception) -> tuple:
    """
    Classify a Resend exception into (ErrorType, human_message).

    The resend SDK raises exceptions whose str() contains the HTTP status
    and the error type string from the API response body.
    """
    error_str = str(error).lower()

    if "500" in error_str or "application_error" in error_str:
        return (
            ErrorType.RESEND_SERVER_ERROR,
            "Resend 500 Internal Server Error — application_error or infrastructure issue",
        )

    if "403" in error_str or "invalid_api_key" in error_str:
        return (
            ErrorType.RESEND_FORBIDDEN,
            "Resend 403 Forbidden — invalid_api_key or sending to unverified address",
        )

    if "401" in error_str or "missing_api_key" in error_str or "restricted_api_key" in error_str:
        return (
            ErrorType.RESEND_UNAUTHORIZED,
            "Resend 401 Unauthorized — missing_api_key or restricted_api_key",
        )

    if "monthly_quota_exceeded" in error_str:
        return (
            ErrorType.RESEND_MONTHLY_QUOTA_EXCEEDED,
            "Resend 429 — monthly_quota_exceeded",
        )

    if "daily_quota_exceeded" in error_str:
        return (
            ErrorType.RESEND_DAILY_QUOTA_EXCEEDED,
            "Resend 429 — daily_quota_exceeded",
        )

    if "rate_limit_exceeded" in error_str or "429" in error_str:
        return (
            ErrorType.RESEND_RATE_LIMITED,
            "Resend 429 Too Many Requests — rate_limit_exceeded",
        )

    if "validation_error" in error_str or "invalid_idempotency_key" in error_str or "400" in error_str:
        return (
            ErrorType.RESEND_VALIDATION_ERROR,
            "Resend 400 Bad Request — validation_error or invalid_idempotency_key",
        )

    return (
        ErrorType.RESEND_SEND_FAILED,
        "Resend failed with an unexpected error",
    )


def notify_resend_failed(error: Exception, to_email: str) -> None:
    """
    Classify the Resend error and fire the matching ntfy notification.
    Called when both Brevo and Resend have failed (both providers down).
    """
    error_type, message = _classify_resend_error(error)

    notify_error(
        error_type,
        f"CRITICAL: {message} — both email providers are down (recipient: {to_email})",
        context={
            "error": str(error)[:300],
            "action_required": "Check Resend account status, API key, and quota",
        },
    )
