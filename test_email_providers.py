"""
Email Provider Test Script
Tests all 4 providers individually: Brevo, Mailtrap, Resend, Loops

Usage:
    python test_email_providers.py
    python test_email_providers.py --email your@email.com
    python test_email_providers.py --email your@email.com --provider brevo
    python test_email_providers.py --email your@email.com --provider all
"""

import os
import sys
import argparse
from dotenv_vault import load_dotenv

load_dotenv()

TEST_MAGIC_LINK = "https://ashel.space/auth/verify?token=TEST-TOKEN-12345"
TEST_SUBJECT = "✅ [TEST] Email Provider Test — Ashel AI Studio"

HTML_CONTENT = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Email Provider Test</title>
</head>
<body style="margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; background-color: #f5f5f5;">
    <table role="presentation" style="width: 100%; border-collapse: collapse;">
        <tr>
            <td align="center" style="padding: 40px 0;">
                <table role="presentation" style="width: 600px; max-width: 100%; border-collapse: collapse; background-color: white; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                    <tr>
                        <td style="padding: 30px; text-align: center; background: linear-gradient(135deg, #a855f7 0%, #6366f1 100%); border-radius: 10px 10px 0 0;">
                            <h1 style="margin: 0; color: #fff; font-size: 22px; font-weight: 600;">🎨 Ashel-Free AI Studio</h1>
                            <p style="margin: 8px 0 0; color: rgba(255,255,255,0.85); font-size: 14px;">Email Provider Test</p>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 30px;">
                            <h2 style="color: #333; margin: 0 0 16px; font-size: 18px;">✅ This provider is working!</h2>
                            <p style="color: #666; font-size: 15px; line-height: 1.6; margin: 0 0 20px;">
                                This is a test email to verify the provider is correctly configured.
                                Below is a sample magic link button — exactly as users would see it.
                            </p>
                            <table role="presentation" style="margin: 20px 0;">
                                <tr>
                                    <td>
                                        <a href="{TEST_MAGIC_LINK}"
                                           style="background: linear-gradient(135deg, #a855f7 0%, #6366f1 100%); color: #fff; padding: 14px 28px; text-decoration: none; border-radius: 6px; font-weight: 600; display: inline-block; font-size: 15px;">
                                            Sign In Now → (test)
                                        </a>
                                    </td>
                                </tr>
                            </table>
                            <p style="color: #999; font-size: 13px; margin: 20px 0 0;">
                                Test link: <a href="{TEST_MAGIC_LINK}" style="color: #a855f7;">{TEST_MAGIC_LINK}</a>
                            </p>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 20px 30px; background-color: #f9f9f9; border-top: 1px solid #eee; border-radius: 0 0 10px 10px;">
                            <p style="color: #999; font-size: 12px; margin: 0; text-align: center;">
                                Ashel-Free AI Studio — Email provider test<br>
                                This is an automated test message.
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""

TEXT_CONTENT = f"""[TEST] Ashel-Free AI Studio — Email Provider Test

This provider is working correctly!

Sample magic link:
{TEST_MAGIC_LINK}

This is an automated test message from Ashel-Free AI Studio.
"""


# ─── ANSI colours ────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg):  print(f"{GREEN}  ✅ {msg}{RESET}")
def fail(msg): print(f"{RED}  ❌ {msg}{RESET}")
def info(msg): print(f"{CYAN}  ℹ  {msg}{RESET}")
def warn(msg): print(f"{YELLOW}  ⚠  {msg}{RESET}")
def header(msg): print(f"\n{BOLD}{CYAN}{'─'*55}{RESET}\n{BOLD}  {msg}{RESET}\n{'─'*55}")


# ─── Individual provider tests ───────────────────────────────────────────────

def test_brevo(to_email: str) -> bool:
    header("1 / 4  —  BREVO  (primary)")

    api_key     = os.getenv("BREVO_API_KEY")
    sender_email = os.getenv("BREVO_SENDER_EMAIL")
    sender_name  = os.getenv("BREVO_SENDER_NAME", "Ashel-Free AI Studio")

    if not api_key:
        warn("BREVO_API_KEY is not set — skipping")
        return False
    if not sender_email:
        warn("BREVO_SENDER_EMAIL is not set — skipping")
        return False

    info(f"API key  : {api_key[:12]}...")
    info(f"Sender   : {sender_name} <{sender_email}>")
    info(f"To       : {to_email}")

    try:
        import sib_api_v3_sdk
        configuration = sib_api_v3_sdk.Configuration()
        configuration.api_key["api-key"] = api_key
        api_instance = sib_api_v3_sdk.TransactionalEmailsApi(
            sib_api_v3_sdk.ApiClient(configuration)
        )
        send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
            to=[{"email": to_email}],
            sender={"name": sender_name, "email": sender_email},
            subject=TEST_SUBJECT,
            html_content=HTML_CONTENT,
            text_content=TEXT_CONTENT,
        )
        api_instance.send_transac_email(send_smtp_email)
        ok(f"Email sent via Brevo to {to_email}")
        return True
    except Exception as e:
        fail(f"Brevo failed: {e}")
        return False


def test_mailtrap(to_email: str) -> bool:
    header("2 / 4  —  MAILTRAP  (secondary)")

    api_token    = os.getenv("MAILTRAP_API_TOKEN")
    sender_email = os.getenv("MAILTRAP_SENDER_EMAIL")
    sender_name  = os.getenv("MAILTRAP_SENDER_NAME", "Ashel-Free AI Studio")

    if not api_token:
        warn("MAILTRAP_API_TOKEN is not set — skipping")
        return False
    if not sender_email:
        warn("MAILTRAP_SENDER_EMAIL is not set — skipping")
        return False

    info(f"API token: {api_token[:12]}...")
    info(f"Sender   : {sender_name} <{sender_email}>")
    info(f"To       : {to_email}")

    try:
        import mailtrap as mt
        client = mt.MailtrapClient(token=api_token)
        mail = mt.Mail(
            sender=mt.Address(email=sender_email, name=sender_name),
            to=[mt.Address(email=to_email)],
            subject=TEST_SUBJECT,
            html=HTML_CONTENT,
            text=TEXT_CONTENT,
        )
        client.send(mail)
        ok(f"Email sent via Mailtrap to {to_email}")
        return True
    except Exception as e:
        fail(f"Mailtrap failed: {e}")
        return False


def test_resend(to_email: str) -> bool:
    header("3 / 4  —  RESEND  (tertiary)")

    api_key      = os.getenv("RESEND_API_KEY")
    sender_email = os.getenv("EMAIL_FROM", "noreply@yourdomain.com")
    sender_name  = os.getenv("RESEND_SENDER_NAME", "Ashel-Free AI Studio")

    if not api_key:
        warn("RESEND_API_KEY is not set — skipping")
        return False

    info(f"API key  : {api_key[:12]}...")
    info(f"Sender   : {sender_name} <{sender_email}>")
    info(f"To       : {to_email}")

    try:
        import resend
        resend.api_key = api_key
        resend.Emails.send({
            "from": f"{sender_name} <{sender_email}>",
            "to": [to_email],
            "subject": TEST_SUBJECT,
            "html": HTML_CONTENT,
            "text": TEXT_CONTENT,
        })
        ok(f"Email sent via Resend to {to_email}")
        return True
    except Exception as e:
        fail(f"Resend failed: {e}")
        return False


def test_loops(to_email: str) -> bool:
    header("4 / 4  —  LOOPS  (last resort)")

    api_key        = os.getenv("LOOPS_API_KEY")
    trans_id       = os.getenv("LOOPS_TRANSACTIONAL_ID")

    if not api_key:
        warn("LOOPS_API_KEY is not set — skipping")
        return False
    if not trans_id:
        warn("LOOPS_TRANSACTIONAL_ID is not set — skipping")
        return False

    info(f"API key        : {api_key[:12]}...")
    info(f"Transactional ID: {trans_id}")
    info(f"To             : {to_email}")

    try:
        import requests
        response = requests.post(
            "https://app.loops.so/api/v1/transactional",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "email": to_email,
                "transactionalId": trans_id,
                "dataVariables": {
                    "magic_link": TEST_MAGIC_LINK,
                },
            },
            timeout=15,
        )
        if response.ok:
            ok(f"Email sent via Loops to {to_email}")
            return True
        else:
            fail(f"Loops failed: HTTP {response.status_code} — {response.text}")
            return False
    except Exception as e:
        fail(f"Loops failed: {e}")
        return False


# ─── Runner ──────────────────────────────────────────────────────────────────

PROVIDERS = {
    "brevo":    test_brevo,
    "mailtrap": test_mailtrap,
    "resend":   test_resend,
    "loops":    test_loops,
}


def run(to_email: str, provider: str = "all"):
    print(f"\n{BOLD}Email Provider Test Suite — Ashel AI Studio{RESET}")
    print(f"Target email : {BOLD}{to_email}{RESET}")
    print(f"Provider(s)  : {BOLD}{provider}{RESET}")

    if provider == "all":
        targets = list(PROVIDERS.items())
    elif provider in PROVIDERS:
        targets = [(provider, PROVIDERS[provider])]
    else:
        print(f"{RED}Unknown provider '{provider}'. Choose: brevo, mailtrap, resend, loops, all{RESET}")
        sys.exit(1)

    results = {}
    for name, fn in targets:
        results[name] = fn(to_email)

    # Summary
    print(f"\n{BOLD}{'─'*55}")
    print(f"  RESULTS SUMMARY")
    print(f"{'─'*55}{RESET}")
    all_passed = True
    for name, passed in results.items():
        if passed:
            ok(f"{name.upper():<12} PASSED")
        else:
            fail(f"{name.upper():<12} FAILED / SKIPPED")
            all_passed = False

    print(f"{'─'*55}")
    if all_passed:
        print(f"{GREEN}{BOLD}  All tested providers sent successfully ✅{RESET}\n")
    else:
        print(f"{YELLOW}{BOLD}  Some providers failed or were skipped ⚠{RESET}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test email providers for Ashel AI Studio")
    parser.add_argument(
        "--email", "-e",
        type=str,
        help="Recipient email address for the test",
    )
    parser.add_argument(
        "--provider", "-p",
        type=str,
        default="all",
        choices=["all", "brevo", "mailtrap", "resend", "loops"],
        help="Which provider to test (default: all)",
    )
    args = parser.parse_args()

    if not args.email:
        args.email = input("Enter your test email address: ").strip()
        if not args.email:
            print(f"{RED}Email address is required.{RESET}")
            sys.exit(1)

    run(args.email, args.provider)
