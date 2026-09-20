"""
Tests for mailer.py - SMTP delivery for password-recovery email, plus the
no-SMTP-configured dev outbox fallback.

Previously untested by any test file. DEV_OUTBOX is patched to a temporary
directory throughout so these tests never write into the real sent_emails/
folder in the working tree.
"""

import smtplib
import sys
import tempfile
import unittest
from email import message_from_string, policy
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import mailer
from evidence import print_evidence


class IsValidEmailTests(unittest.TestCase):
    def test_accepts_ordinary_addresses(self) -> None:
        result = mailer.is_valid_email("user@example.com")
        print_evidence("TC-149", "An ordinary email address is accepted",
                       "'user@example.com'", True, result)
        self.assertTrue(result)
        self.assertTrue(mailer.is_valid_email("first.last@sub.example.co.mu"))

    def test_rejects_missing_at_sign(self) -> None:
        result = mailer.is_valid_email("not-an-email")
        print_evidence("TC-150", "An address with no @ sign is rejected",
                       "'not-an-email'", False, result)
        self.assertFalse(result)

    def test_rejects_missing_domain_dot(self) -> None:
        result = mailer.is_valid_email("user@localhost")
        print_evidence("TC-151", "An address with no domain dot is rejected",
                       "'user@localhost'", False, result)
        self.assertFalse(result)

    def test_rejects_whitespace_in_address(self) -> None:
        result = mailer.is_valid_email("us er@example.com")
        print_evidence("TC-152", "An address containing whitespace is rejected",
                       "'us er@example.com'", False, result)
        self.assertFalse(result)

    def test_rejects_empty_or_none(self) -> None:
        empty_result = mailer.is_valid_email("")
        none_result = mailer.is_valid_email(None)
        print_evidence("TC-153", "Empty string and None are both rejected",
                       "'' and None", {"empty": False, "none": False},
                       {"empty": empty_result, "none": none_result})
        self.assertFalse(empty_result)
        self.assertFalse(none_result)

    def test_strips_surrounding_whitespace_before_checking(self) -> None:
        result = mailer.is_valid_email("  user@example.com  ")
        print_evidence("TC-154", "Surrounding whitespace is stripped before validation",
                       "'  user@example.com  '", True, result)
        self.assertTrue(result)


class ConfigHelperTests(unittest.TestCase):
    def test_smtp_configured_false_when_host_unset(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            result = mailer.smtp_configured()
        print_evidence("TC-155", "smtp_configured() is False with no SMTP_HOST",
                       "env: SMTP_HOST unset", False, result)
        self.assertFalse(result)

    def test_smtp_configured_true_when_host_set(self) -> None:
        with patch.dict("os.environ", {"SMTP_HOST": "smtp.example.com"}, clear=True):
            result = mailer.smtp_configured()
        print_evidence("TC-156", "smtp_configured() is True once SMTP_HOST is set",
                       "SMTP_HOST = 'smtp.example.com'", True, result)
        self.assertTrue(result)

    def test_public_base_url_default(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            result = mailer.public_base_url()
        print_evidence("TC-157", "public_base_url() defaults to localhost",
                       "env: PUBLIC_BASE_URL unset", "http://localhost:8000", result)
        self.assertEqual(result, "http://localhost:8000")

    def test_public_base_url_strips_trailing_slash(self) -> None:
        with patch.dict("os.environ", {"PUBLIC_BASE_URL": "https://example.com/"},
                        clear=True):
            result = mailer.public_base_url()
        print_evidence("TC-158", "public_base_url() strips a trailing slash",
                       "PUBLIC_BASE_URL = 'https://example.com/'",
                       "https://example.com", result)
        self.assertEqual(result, "https://example.com")


class DevOutboxTests(unittest.TestCase):
    """With no SMTP host configured, send() must write a .eml file rather
    than silently doing nothing - and never raise, since a caller must not
    be able to distinguish delivery failure from success (account enumeration)."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.outbox = Path(self._tmpdir.name)
        self.addCleanup(self._tmpdir.cleanup)
        self.patcher = patch.object(mailer, "DEV_OUTBOX", self.outbox)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_send_with_no_smtp_host_writes_an_eml_file(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            ok = mailer.send("user@example.com", "Subject", "body text")
        files = list(self.outbox.glob("*.eml"))
        print_evidence("TC-159", "send() with no SMTP configured writes a .eml file",
                       "SMTP_HOST unset", {"ok": True, "files_written": 1},
                       {"ok": ok, "files_written": len(files)})
        self.assertTrue(ok)
        self.assertEqual(len(files), 1)
        self.assertIn("user_at_example.com", files[0].name)

    def test_dev_outbox_message_contains_the_body_and_recipient(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            mailer.send("user@example.com", "Reset your password", "click this link")
        raw = next(self.outbox.glob("*.eml")).read_text(encoding="utf-8")
        msg = message_from_string(raw, policy=policy.default)
        print_evidence("TC-160", "Dev outbox message preserves recipient/subject/body",
                       "to='user@example.com', subject='Reset your password'",
                       {"To": "user@example.com", "Subject": "Reset your password",
                        "body_contains_link": True},
                       {"To": msg["To"], "Subject": msg["Subject"],
                        "body_contains_link": "click this link" in msg.get_content()})
        self.assertEqual(msg["To"], "user@example.com")
        self.assertEqual(msg["Subject"], "Reset your password")
        self.assertIn("click this link", msg.get_content())

    def test_last_dev_link_is_updated(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            mailer.send("user@example.com", "Subject", "body",
                        link="http://localhost:8000/reset?token=abc")
        result = mailer.last_dev_link()
        print_evidence("TC-161", "last_dev_link() reflects the most recently sent link",
                       "link = 'http://localhost:8000/reset?token=abc'",
                       "http://localhost:8000/reset?token=abc", result)
        self.assertEqual(result, "http://localhost:8000/reset?token=abc")


class SmtpDeliveryTests(unittest.TestCase):
    """SMTP-configured path, with smtplib itself mocked out."""

    def _env(self, **overrides):
        base = {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "587",
            "SMTP_USER": "bot@example.com",
            "SMTP_PASSWORD": "app-password",
            "SMTP_TLS": "starttls",
        }
        base.update(overrides)
        return base

    def test_starttls_send_success(self) -> None:
        server = MagicMock()
        server.__enter__.return_value = server
        with patch.dict("os.environ", self._env(), clear=True), \
             patch("mailer.smtplib.SMTP", return_value=server) as smtp_cls:
            ok = mailer.send("user@example.com", "Subject", "body")
        print_evidence("TC-162", "STARTTLS delivery succeeds and logs in",
                       "SMTP_TLS = 'starttls'", True, ok)
        self.assertTrue(ok)
        smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=20)
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("bot@example.com", "app-password")
        server.send_message.assert_called_once()

    def test_ssl_mode_uses_smtp_ssl(self) -> None:
        server = MagicMock()
        server.__enter__.return_value = server
        with patch.dict("os.environ", self._env(SMTP_TLS="ssl", SMTP_PORT="465"),
                        clear=True), \
             patch("mailer.smtplib.SMTP_SSL", return_value=server) as smtp_ssl_cls:
            ok = mailer.send("user@example.com", "Subject", "body")
        print_evidence("TC-163", "SSL mode uses SMTP_SSL instead of STARTTLS",
                       "SMTP_TLS = 'ssl', SMTP_PORT = '465'",
                       {"ok": True, "starttls_called": False},
                       {"ok": ok, "starttls_called": server.starttls.called})
        self.assertTrue(ok)
        smtp_ssl_cls.assert_called_once()
        server.starttls.assert_not_called()
        server.send_message.assert_called_once()

    def test_authentication_failure_returns_false_not_raise(self) -> None:
        server = MagicMock()
        server.__enter__.return_value = server
        server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad creds")
        with patch.dict("os.environ", self._env(), clear=True), \
             patch("mailer.smtplib.SMTP", return_value=server):
            ok = mailer.send("user@example.com", "Subject", "body")
        print_evidence("TC-164", "SMTP auth failure returns False, does not raise",
                       "server.login raises SMTPAuthenticationError", False, ok)
        self.assertFalse(ok)

    def test_smtp_exception_returns_false_not_raise(self) -> None:
        with patch.dict("os.environ", self._env(), clear=True), \
             patch("mailer.smtplib.SMTP", side_effect=smtplib.SMTPConnectError(421, b"down")):
            ok = mailer.send("user@example.com", "Subject", "body")
        print_evidence("TC-165", "SMTP connection failure returns False, does not raise",
                       "smtplib.SMTP() raises SMTPConnectError", False, ok)
        self.assertFalse(ok)

    def test_no_login_attempted_when_smtp_user_is_unset(self) -> None:
        server = MagicMock()
        server.__enter__.return_value = server
        with patch.dict("os.environ", self._env(SMTP_USER=""), clear=True), \
             patch("mailer.smtplib.SMTP", return_value=server):
            mailer.send("user@example.com", "Subject", "body")
        print_evidence("TC-166", "No login is attempted when SMTP_USER is unset",
                       "SMTP_USER = ''", False, server.login.called)
        server.login.assert_not_called()


class SendPasswordResetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.patcher = patch.object(mailer, "DEV_OUTBOX", Path(self._tmpdir.name))
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.addCleanup(self._tmpdir.cleanup)

    def test_includes_username_and_link_in_both_bodies(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            ok = mailer.send_password_reset(
                "user@example.com", "alice",
                "http://localhost:8000/reset-password?token=xyz", 30,
            )
        raw = next(Path(self._tmpdir.name).glob("*.eml")).read_text(encoding="utf-8")
        result = {"ok": ok, "has_username": "alice" in raw,
                  "has_token": "token=xyz" in raw, "has_expiry": "30 minutes" in raw}
        print_evidence("TC-167", "Password-reset email includes username, link, and expiry",
                       "username='alice', link contains token=xyz, minutes=30",
                       {"ok": True, "has_username": True, "has_token": True, "has_expiry": True},
                       result)
        self.assertTrue(ok)
        self.assertIn("alice", raw)
        self.assertIn("token=xyz", raw)
        self.assertIn("30 minutes", raw)


if __name__ == "__main__":
    unittest.main()
