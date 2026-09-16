"""
tools/test_email.py
Check the SMTP configuration and optionally send a real test message.

Password recovery fails silently by design - the app never tells a visitor
whether mail was actually delivered, because that would reveal which accounts
exist. That makes SMTP problems invisible through the UI, so this exercises the
connection directly and reports exactly what went wrong.

    python tools/test_email.py                     # check config + connect + authenticate
    python tools/test_email.py --send you@mail.com # also send a real message
"""

from __future__ import annotations

import argparse
import os
import pathlib
import smtplib
import socket
import ssl
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv                     # noqa: E402
load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")

import mailer                                       # noqa: E402

OK, BAD, INFO = "  [ok]  ", "  [--]  ", "  [i]   "


def mask(value: str) -> str:
    if not value:
        return "(not set)"
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


def show_config() -> dict:
    cfg = {k: os.getenv(k, "").strip() for k in (
        "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD",
        "SMTP_FROM", "SMTP_FROM_NAME", "SMTP_TLS", "PUBLIC_BASE_URL",
    )}
    print("Current configuration\n")
    for k, v in cfg.items():
        shown = mask(v) if k == "SMTP_PASSWORD" else (v or "(not set)")
        print(f"  {k:<16} {shown}")
    print()
    return cfg


def diagnose(cfg: dict) -> bool:
    if not cfg["SMTP_HOST"]:
        print(f"{BAD}SMTP_HOST is not set - the app is writing mail to "
              f"sent_emails/ instead of sending it.\n")
        print("  For Gmail, put this in .env:\n")
        print("      SMTP_HOST=smtp.gmail.com")
        print("      SMTP_PORT=587")
        print("      SMTP_USER=your.address@gmail.com")
        print("      SMTP_PASSWORD=<16-character App Password>")
        print("      SMTP_FROM=your.address@gmail.com")
        print("      SMTP_TLS=starttls\n")
        print("  The App Password is NOT your Google password. Create one at")
        print("  https://myaccount.google.com/apppasswords (2-Step Verification")
        print("  must be enabled first, or the option will not appear).")
        return False

    ok = True
    if not cfg["SMTP_PASSWORD"]:
        print(f"{BAD}SMTP_PASSWORD is empty - authentication will fail.")
        ok = False
    if "gmail" in cfg["SMTP_HOST"] and len(cfg["SMTP_PASSWORD"].replace(" ", "")) not in (0, 16):
        print(f"{INFO}Gmail App Passwords are 16 characters. Yours is "
              f"{len(cfg['SMTP_PASSWORD'].replace(' ', ''))} - if authentication "
              f"fails, this is probably your account password rather than an "
              f"App Password.")
    if cfg["PUBLIC_BASE_URL"].startswith("http://localhost"):
        print(f"{INFO}PUBLIC_BASE_URL is localhost, so reset links in emails will "
              f"only work on this machine. Fine for testing; set a reachable "
              f"address before sending to anyone else.")
    return ok


def connect(cfg: dict, send_to: str | None) -> bool:
    host = cfg["SMTP_HOST"]
    port = int(cfg["SMTP_PORT"] or 587)
    mode = (cfg["SMTP_TLS"] or "starttls").lower()

    print(f"\nConnecting to {host}:{port} ({mode}) ...")
    try:
        if mode == "ssl":
            server = smtplib.SMTP_SSL(host, port, timeout=20,
                                      context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=20)

        with server:
            server.ehlo()
            print(f"{OK}Connected")
            if mode == "starttls":
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                print(f"{OK}TLS negotiated")
            if cfg["SMTP_USER"]:
                server.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
                print(f"{OK}Authenticated as {cfg['SMTP_USER']}")

            if send_to:
                sent = mailer.send_password_reset(
                    send_to, "test-user",
                    f"{mailer.public_base_url()}/reset-password?token=EXAMPLE",
                    30,
                )
                if sent:
                    print(f"{OK}Test message sent to {send_to}")
                    print(f"{INFO}Check the spam folder - mail from a home "
                          f"connection is often filtered.")
                else:
                    print(f"{BAD}Send failed; see the error above.")
                    return False
        return True

    except socket.gaierror:
        print(f"{BAD}Cannot resolve {host}. Check SMTP_HOST for typos.")
    except (socket.timeout, TimeoutError):
        print(f"{BAD}Timed out reaching {host}:{port}.")
        print("      Port 587 is blocked on some networks; try SMTP_PORT=465 "
              "with SMTP_TLS=ssl.")
    except ConnectionRefusedError:
        print(f"{BAD}Connection refused by {host}:{port}. Wrong port for this "
              f"provider?")
    except smtplib.SMTPAuthenticationError as exc:
        print(f"{BAD}Authentication rejected: {exc.smtp_code} "
              f"{exc.smtp_error.decode(errors='replace')[:120]}")
        print("\n      For Gmail this almost always means SMTP_PASSWORD is the")
        print("      account password rather than an App Password. Create one at")
        print("      https://myaccount.google.com/apppasswords")
    except ssl.SSLError as exc:
        print(f"{BAD}TLS error: {exc}")
        print("      Port 587 uses SMTP_TLS=starttls; port 465 uses SMTP_TLS=ssl.")
    except smtplib.SMTPException as exc:
        print(f"{BAD}SMTP error: {exc}")
    except OSError as exc:
        print(f"{BAD}Network error: {exc}")
    return False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--send", metavar="ADDRESS",
                    help="send a real test message to this address")
    args = ap.parse_args()

    cfg = show_config()
    if not diagnose(cfg):
        sys.exit(1)

    if connect(cfg, args.send):
        print("\nSMTP is working. Password recovery emails will be delivered.")
        if not args.send:
            print("Run with --send you@example.com to send a real test message.")
    else:
        print("\nSMTP is not working yet. Until it does, the app keeps writing "
              "mail to sent_emails/ and logging the reset link, so recovery "
              "still works locally.")
        sys.exit(1)


if __name__ == "__main__":
    main()
