"""
mailer.py
SMTP delivery for transactional email (currently password recovery).

Configuration
-------------
    SMTP_HOST       smtp.gmail.com
    SMTP_PORT       587 for STARTTLS, 465 for implicit TLS
    SMTP_USER       account to authenticate as
    SMTP_PASSWORD   app password, not the account password
    SMTP_FROM       address the message is sent from (defaults to SMTP_USER)
    SMTP_FROM_NAME  display name
    SMTP_TLS        starttls | ssl | none      (default starttls)
    PUBLIC_BASE_URL base for links in emails    (default http://localhost:8000)

Delivery with no SMTP server
----------------------------
When SMTP_HOST is unset the message is written to sent_emails/ as a .eml file
and the reset link is logged. That keeps the whole recovery flow exercisable -
and demonstrable - on a machine with no mail server, instead of the feature
appearing to work while silently sending nothing. last_dev_link() exposes the
most recent link so tests can complete the round trip.

Deliverability note
-------------------
Mail sent from a residential connection through a free SMTP account is very
likely to be spam-filtered. For anything beyond a demo, send through a
transactional provider on a domain you control.
"""

from __future__ import annotations

import logging
import os
import pathlib
import re
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

logger = logging.getLogger(__name__)

DEV_OUTBOX = pathlib.Path("sent_emails")

# Deliberately permissive. Full RFC 5322 validation rejects addresses that work
# in practice; the only authoritative test of an address is whether mail to it
# arrives, so this catches typos rather than pretending to prove validity.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")

_last_dev_link = None


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def is_valid_email(address: str) -> bool:
    return bool(_EMAIL_RE.match((address or "").strip()))


def smtp_configured() -> bool:
    return bool(_env("SMTP_HOST"))


def public_base_url() -> str:
    return _env("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")


def last_dev_link():
    """Most recent link written to the dev outbox, for tests and local use."""
    return _last_dev_link


def _from_header() -> str:
    address = _env("SMTP_FROM") or _env("SMTP_USER") or "no-reply@localhost"
    name = _env("SMTP_FROM_NAME", "IBL Traffic Detection")
    return formataddr((name, address))


def _build(to: str, subject: str, text_body: str, html_body) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = _from_header()
    msg["To"] = to
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain="ibl-traffic.local")
    # Recovery mail is a direct response to a user action; keep it out of
    # bulk-mail heuristics and out of auto-responder loops.
    msg["Auto-Submitted"] = "auto-generated"
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    return msg


def _deliver_to_outbox(msg: EmailMessage, link) -> bool:
    global _last_dev_link
    DEV_OUTBOX.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    safe_to = str(msg["To"]).replace("@", "_at_")
    path = DEV_OUTBOX / (stamp + "-" + safe_to + ".eml")
    path.write_text(msg.as_string(), encoding="utf-8")
    _last_dev_link = link
    logger.warning(
        "SMTP is not configured - email written to %s instead of being sent.", path
    )
    if link:
        logger.warning("Recovery link for %s: %s", msg["To"], link)
    return True


def send(to: str, subject: str, text_body: str, html_body=None, link=None) -> bool:
    """Send one message. True when delivered (or written to the dev outbox).

    Never raises: a failure to send must not produce a different response to
    the caller than a success would, because that difference tells an attacker
    whether an account exists.
    """
    msg = _build(to, subject, text_body, html_body)

    if not smtp_configured():
        return _deliver_to_outbox(msg, link)

    host = _env("SMTP_HOST")
    port = int(_env("SMTP_PORT", "587"))
    user = _env("SMTP_USER")
    password = _env("SMTP_PASSWORD")
    mode = _env("SMTP_TLS", "starttls").lower()

    try:
        if mode == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=20,
                                      context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=20)
        with server:
            server.ehlo()
            if mode == "starttls":
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
            if user:
                server.login(user, password)
            server.send_message(msg)
        logger.info("Sent %r to %s via %s.", subject, to, host)
        return True

    except smtplib.SMTPAuthenticationError:
        logger.error(
            "SMTP authentication failed for %s. Gmail and most providers "
            "require an app password rather than the account password.", user
        )
    except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
        logger.error("SMTP delivery to %s failed: %s", to, exc, exc_info=True)
    return False


# -- Message templates --------------------------------------------------------

def send_password_reset(to: str, username: str, link: str, minutes: int) -> bool:
    subject = "Reset your IBL Traffic Detection password"

    text = (
        "Hello " + username + ",\n\n"
        "We received a request to reset the password for your IBL Traffic\n"
        "Detection account. Open the link below to choose a new one:\n\n"
        + link + "\n\n"
        "This link expires in " + str(minutes) + " minutes and can only be\n"
        "used once.\n\n"
        "If you did not request this, no action is needed - your password has\n"
        "not changed and the link above can be ignored.\n\n"
        "IBL Group - Traffic Bottleneck Detection System\n"
    )

    html = (
        '<!doctype html><html><body style="margin:0;padding:24px;'
        'background:#f4f6f8;font-family:Segoe UI,Tahoma,sans-serif;color:#10151c;">'
        '<div style="max-width:520px;margin:0 auto;background:#ffffff;'
        'border:1px solid #dbe2e9;border-radius:12px;padding:32px;">'
        '<h1 style="margin:0 0 6px;font-size:19px;">Reset your password</h1>'
        '<p style="margin:0 0 20px;color:#40505f;font-size:14px;">'
        'IBL Traffic Detection System</p>'
        '<p style="font-size:15px;line-height:1.6;">Hello ' + username + ',</p>'
        '<p style="font-size:15px;line-height:1.6;color:#40505f;">'
        'We received a request to reset your password. Choose a new one using '
        'the button below.</p>'
        '<p style="margin:26px 0;"><a href="' + link + '" '
        'style="display:inline-block;background:#0f3460;color:#ffffff;'
        'text-decoration:none;padding:12px 26px;border-radius:8px;'
        'font-weight:600;font-size:15px;">Choose a new password</a></p>'
        '<p style="font-size:13px;color:#6b7c8c;line-height:1.6;">'
        'This link expires in ' + str(minutes) + ' minutes and can only be used '
        'once. If the button does not work, copy this address into your browser:</p>'
        '<p style="font-size:12px;color:#6b7c8c;word-break:break-all;">'
        + link + '</p>'
        '<hr style="border:none;border-top:1px solid #dbe2e9;margin:24px 0;">'
        '<p style="font-size:13px;color:#6b7c8c;line-height:1.6;margin:0;">'
        'If you did not request this, no action is needed - your password has '
        'not changed.</p>'
        '</div></body></html>'
    )

    return send(to, subject, text, html, link=link)
