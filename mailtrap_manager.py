"""
Mailtrap Email Manager
Secondary email provider — used when Brevo fails, before falling back to Resend

Mailtrap status codes handled:
  400 - Bad Request       (validation error, malformed payload, invalid fields)
  401 - Unauthorized      (invalid/missing API token, unverified sender domain)
  403 - Forbidden         (sender domain not verified, insufficient permissions)
  429 - Too Many Requests (rate limit exceeded)
  500 - Internal Server Error (Mailtrap-side transient issue)
"""

import os
import mailtrap as mt
from dotenv_vault import load_dotenv
from error_notifier import notify_error, ErrorType

load_dotenv()

MAILTRAP_API_TOKEN = os.getenv("MAILTRAP_API_TOKEN")
MAILTRAP_SENDER_EMAIL = os.getenv("MAILTRAP_SENDER_EMAIL")
MAILTRAP_SENDER_NAME = os.getenv("MAILTRAP_SENDER_NAME", "Ashel-Free AI Studio")


def send_email(to_email: str, subject: str, html_content: str, text_content: str) -> None:
    """
    Send a transactional email via Mailtrap.
    Raises on any error so auth.py can fall back to Resend.
    """
    client = mt.MailtrapClient(token=MAILTRAP_API_TOKEN)
    mail = mt.Mail(
        sender=mt.Address(email=MAILTRAP_SENDER_EMAIL, name=MAILTRAP_SENDER_NAME),
        to=[mt.Address(email=to_email)],
        subject=subject,
        html=html_content,
        text=text_content,
    )
    client.send(mail)


def _classify_mailtrap_error(error: Exception) -> tuple:
    """
    Classify a Mailtrap exception into (ErrorType, human_message).
    Checks the error string for HTTP status codes and known error keywords.
    Returns (ErrorType, message_str)
    """
    error_str = str(error).lower()

    if "500" in error_str:
        return (
            ErrorType.MAILTRAP_SERVER_ERROR,
            "Mailtrap 500 Internal Server Error — transient Mailtrap-side issue",
        )

    if "403" in error_str or "forbidden" in error_str or "domain" in error_str:
        return (
            ErrorType.MAILTRAP_FORBIDDEN,
            "Mailtrap 403 Forbidden — sender domain not verified or permission denied",
        )

    if "401" in error_str or "unauthorized" in error_str or "invalid" in error_str and "token" in error_str:
        return (
            ErrorType.MAILTRAP_UNAUTHORIZED,
            "Mailtrap 401 Unauthorized — invalid or missing API token, or unverified sender domain",
        )

    if "429" in error_str or "rate" in error_str and "limit" in error_str:
        return (
            ErrorType.MAILTRAP_RATE_LIMITED,
            "Mailtrap 429 Too Many Requests — rate limit exceeded",
        )

    if "400" in error_str or "validation" in error_str or "bad request" in error_str:
        return (
            ErrorType.MAILTRAP_BAD_REQUEST,
            "Mailtrap 400 Bad Request — validation error or malformed payload",
        )

    return (
        ErrorType.MAILTRAP_FALLBACK_TO_RESEND,
        "Mailtrap failed with an unexpected error",
    )


def notify_mailtrap_fallback(error: Exception, to_email: str) -> None:
    """
    Classify the Mailtrap error, fire the matching ntfy notification,
    and log to console. All Mailtrap errors fall back to Resend.
    """
    error_type, message = _classify_mailtrap_error(error)

    notify_error(
        error_type,
        f"{message} — falling back to Resend (recipient: {to_email})",
        context={
            "error": str(error)[:300],
            "action": "Falling back to Resend",
        },
    )
