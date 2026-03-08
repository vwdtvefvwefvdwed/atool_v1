"""
Brevo (formerly Sendinblue) Email Manager
Primary email provider for magic link sending
Falls back to Resend on any Brevo error

Brevo status codes handled:
  400 - Bad Request        (missing_parameter, invalid_parameter, out_of_range)
  401 - Unauthorized       (missing/invalid API key, API access not activated)
  402 - Payment Required   (not_enough_credits, account not activated)
  403 - Forbidden          (permission_denied)
  404 - Not Found          (document_not_found — e.g. sender ID not found)
  429 - Too Many Requests  (rate limits exceeded — daily or monthly)
  5xx - Server Error       (Brevo-side transient issues)
  Account errors           (account_under_validation, account suspended)
"""

import os
import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException
from dotenv_vault import load_dotenv
from error_notifier import notify_error, ErrorType

load_dotenv()

BREVO_API_KEY = os.getenv("BREVO_API_KEY")
BREVO_SENDER_EMAIL = os.getenv("BREVO_SENDER_EMAIL")
BREVO_SENDER_NAME = os.getenv("BREVO_SENDER_NAME", "Ashel-Free AI Studio")


def _get_api_instance():
    configuration = sib_api_v3_sdk.Configuration()
    configuration.api_key["api-key"] = BREVO_API_KEY
    return sib_api_v3_sdk.TransactionalEmailsApi(
        sib_api_v3_sdk.ApiClient(configuration)
    )


def send_email(to_email: str, subject: str, html_content: str, text_content: str) -> None:
    """
    Send a transactional email via Brevo.
    Raises on any error so auth.py can fall back to Resend.
    """
    send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
        to=[{"email": to_email}],
        sender={"name": BREVO_SENDER_NAME, "email": BREVO_SENDER_EMAIL},
        subject=subject,
        html_content=html_content,
        text_content=text_content,
    )
    api_instance = _get_api_instance()
    api_instance.send_transac_email(send_smtp_email)


def _get_error_type_for_status(status: int, body: str) -> tuple:
    """
    Map Brevo HTTP status + body to the right ErrorType and a human message.
    Returns (ErrorType, message_str)
    """
    body_lower = body.lower()

    if status == 400:
        return (
            ErrorType.BREVO_BAD_REQUEST,
            "Brevo 400 Bad Request (missing_parameter / invalid_parameter / out_of_range)",
        )

    if status == 401:
        return (
            ErrorType.BREVO_UNAUTHORIZED,
            "Brevo 401 Unauthorized — API key missing, invalid, or API access not activated",
        )

    if status == 402:
        return (
            ErrorType.BREVO_PAYMENT_REQUIRED,
            "Brevo 402 Payment Required — account has no credits or requires activation",
        )

    if status == 403:
        if "account_under_validation" in body_lower or "suspended" in body_lower:
            return (
                ErrorType.BREVO_ACCOUNT_SUSPENDED,
                "Brevo 403 — account under validation or temporarily suspended",
            )
        return (
            ErrorType.BREVO_FORBIDDEN,
            "Brevo 403 Forbidden — permission_denied for this operation",
        )

    if status == 404:
        return (
            ErrorType.BREVO_NOT_FOUND,
            "Brevo 404 Not Found — resource not found (e.g. sender ID does not exist)",
        )

    if status == 429:
        return (
            ErrorType.BREVO_QUOTA_EXCEEDED,
            "Brevo 429 Too Many Requests — rate limit or quota exceeded (daily/monthly)",
        )

    if status and status >= 500:
        return (
            ErrorType.BREVO_FALLBACK_TO_RESEND,
            f"Brevo {status} Server Error — transient Brevo-side issue",
        )

    if "account_under_validation" in body_lower:
        return (
            ErrorType.BREVO_ACCOUNT_SUSPENDED,
            "Brevo account is under validation — sending temporarily blocked",
        )

    if "suspended" in body_lower:
        return (
            ErrorType.BREVO_ACCOUNT_SUSPENDED,
            "Brevo account suspended due to security concerns or bot attack detection",
        )

    return (
        ErrorType.BREVO_FALLBACK_TO_RESEND,
        f"Brevo unexpected error (status={status})",
    )


def notify_brevo_fallback(error: Exception, to_email: str) -> None:
    """
    Classify the Brevo error, fire the matching ntfy notification,
    and log to console. All Brevo errors fall back to Resend.
    """
    status = getattr(error, "status", None)
    body = str(getattr(error, "body", "") or "")

    error_type, message = _get_error_type_for_status(status, body)

    notify_error(
        error_type,
        f"{message} — falling back to Resend (recipient: {to_email})",
        context={
            "http_status": status,
            "error_body": body[:300],
            "action": "Falling back to Resend",
        },
    )
