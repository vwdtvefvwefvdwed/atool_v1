"""
Loops Email Manager
4th/last-resort email provider — used when Brevo, Mailtrap, and Resend all fail

Unlike the other providers, Loops is template-based.
Setup (one-time):
  1. Create a transactional email in the Loops dashboard
  2. Add a data variable named `magic_link` to the template
  3. Copy the transactionalId and set it as LOOPS_TRANSACTIONAL_ID env var

Loops API endpoint: POST https://app.loops.so/api/v1/transactional
Auth: Authorization: Bearer <LOOPS_API_KEY>

Loops status codes handled:
  400 - Bad Request       (missing fields, invalid payload, validation error)
  401 - Unauthorized      (invalid or missing API key)
  404 - Not Found         (transactionalId does not exist — template deleted/wrong ID)
  409 - Conflict          (idempotency key already used within 24 hours)
  429 - Too Many Requests (rate limit exceeded)
  500 - Internal Server Error (Loops-side transient issue)
"""

import os
import requests
from dotenv_vault import load_dotenv
from error_notifier import notify_error, ErrorType

load_dotenv()

LOOPS_API_KEY = os.getenv("LOOPS_API_KEY")
LOOPS_TRANSACTIONAL_ID = os.getenv("LOOPS_TRANSACTIONAL_ID")

_LOOPS_ENDPOINT = "https://app.loops.so/api/v1/transactional"


def send_email(to_email: str, magic_link: str) -> None:
    """
    Send a magic link email via Loops.

    Uses the pre-created Loops transactional template identified by
    LOOPS_TRANSACTIONAL_ID, passing the magic_link as a data variable.

    Raises on any error so auth.py can handle the hard fail.
    """
    response = requests.post(
        _LOOPS_ENDPOINT,
        headers={
            "Authorization": f"Bearer {LOOPS_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "email": to_email,
            "transactionalId": LOOPS_TRANSACTIONAL_ID,
            "dataVariables": {
                "magic_link": magic_link,
            },
        },
        timeout=15,
    )

    if not response.ok:
        raise LoopsError(status=response.status_code, body=response.text)


class LoopsError(Exception):
    """Raised when Loops returns a non-2xx response."""

    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"Loops error {status}: {body}")


def _classify_error(error: Exception) -> tuple:
    """
    Map a Loops error into (ErrorType, human_message).
    Returns (ErrorType, message_str)
    """
    if isinstance(error, LoopsError):
        status = error.status
        body_lower = error.body.lower()

        if status == 400:
            return (
                ErrorType.LOOPS_BAD_REQUEST,
                "Loops 400 Bad Request — missing fields, invalid payload, or validation error",
            )
        if status == 401:
            return (
                ErrorType.LOOPS_UNAUTHORIZED,
                "Loops 401 Unauthorized — invalid or missing API key",
            )
        if status == 404:
            return (
                ErrorType.LOOPS_NOT_FOUND,
                "Loops 404 Not Found — LOOPS_TRANSACTIONAL_ID is wrong or template was deleted",
            )
        if status == 409:
            return (
                ErrorType.LOOPS_CONFLICT,
                "Loops 409 Conflict — idempotency key already used within 24 hours",
            )
        if status == 429:
            return (
                ErrorType.LOOPS_RATE_LIMITED,
                "Loops 429 Too Many Requests — rate limit exceeded",
            )
        if status >= 500:
            return (
                ErrorType.LOOPS_SERVER_ERROR,
                f"Loops {status} Server Error — transient Loops-side issue",
            )

        return (
            ErrorType.LOOPS_SEND_FAILED,
            f"Loops unexpected error (status={status})",
        )

    error_str = str(error).lower()
    if "timeout" in error_str or "connection" in error_str:
        return (
            ErrorType.LOOPS_SEND_FAILED,
            "Loops network error — connection timeout or DNS failure",
        )

    return (
        ErrorType.LOOPS_SEND_FAILED,
        "Loops failed with an unexpected error",
    )


def notify_loops_failed(error: Exception, to_email: str) -> None:
    """
    Classify the Loops error and fire the matching ntfy notification.
    Called when all 4 providers have failed on the final cycle.
    """
    error_type, message = _classify_error(error)

    notify_error(
        error_type,
        f"CRITICAL: {message} — ALL email providers are down (recipient: {to_email})",
        context={
            "error": str(error)[:300],
            "action_required": "Check all email provider accounts and API keys",
        },
    )


def notify_loops_cycling(error: Exception, to_email: str, cycle: int, max_cycles: int) -> None:
    """
    Classify the Loops error and fire a non-critical ntfy notification.
    Called when Loops fails but we are cycling back to Brevo for another attempt.
    """
    error_type, message = _classify_error(error)

    notify_error(
        ErrorType.LOOPS_CYCLING_TO_BREVO,
        f"Loops failed (cycle {cycle}/{max_cycles}) — {message} — restarting from Brevo (recipient: {to_email})",
        context={
            "error": str(error)[:300],
            "cycle": f"{cycle}/{max_cycles}",
            "action": "Cycling back to Brevo and retrying full provider chain",
        },
    )
