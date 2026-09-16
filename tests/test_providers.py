"""
Tests for detection/providers.py - the Gemini/Anthropic/template failover
logic backing the traffic-summary service.

Previously entirely untested: no test file imported this module even
indirectly, despite it owning the retry/backoff classification that decides
whether a transient API error gets a second attempt.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import detection.providers as providers
from evidence import print_evidence


class ActiveProviderTests(unittest.TestCase):
    def test_defaults_to_template_with_no_env(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            result = providers.active_provider()
        print_evidence("TC-108", "No SUMMARY_PROVIDER set defaults to template",
                       "env: (unset)", providers.TEMPLATE, result)
        self.assertEqual(result, providers.TEMPLATE)

    def test_selects_gemini(self) -> None:
        with patch.dict("os.environ", {"SUMMARY_PROVIDER": "gemini"}, clear=True):
            result = providers.active_provider()
        print_evidence("TC-109", "SUMMARY_PROVIDER=gemini selects Gemini",
                       "SUMMARY_PROVIDER = 'gemini'", providers.GEMINI, result)
        self.assertEqual(result, providers.GEMINI)

    def test_selects_anthropic(self) -> None:
        with patch.dict("os.environ", {"SUMMARY_PROVIDER": "anthropic"}, clear=True):
            result = providers.active_provider()
        print_evidence("TC-110", "SUMMARY_PROVIDER=anthropic selects Anthropic",
                       "SUMMARY_PROVIDER = 'anthropic'", providers.ANTHROPIC, result)
        self.assertEqual(result, providers.ANTHROPIC)

    def test_is_case_insensitive(self) -> None:
        with patch.dict("os.environ", {"SUMMARY_PROVIDER": "GEMINI"}, clear=True):
            result = providers.active_provider()
        print_evidence("TC-111", "Provider selection is case-insensitive",
                       "SUMMARY_PROVIDER = 'GEMINI'", providers.GEMINI, result)
        self.assertEqual(result, providers.GEMINI)

    def test_unknown_provider_falls_back_to_template(self) -> None:
        with patch.dict("os.environ", {"SUMMARY_PROVIDER": "not-a-real-provider"},
                        clear=True):
            result = providers.active_provider()
        print_evidence("TC-112", "An unrecognised provider name falls back to template",
                       "SUMMARY_PROVIDER = 'not-a-real-provider'", providers.TEMPLATE, result)
        self.assertEqual(result, providers.TEMPLATE)

    def test_kill_switch_overrides_provider_selection(self) -> None:
        """CLAUDE_API_DISABLED=1 forces templates even with a provider set."""
        with patch.dict(
            "os.environ",
            {"SUMMARY_PROVIDER": "anthropic", "CLAUDE_API_DISABLED": "1"},
            clear=True,
        ):
            result = providers.active_provider()
        print_evidence("TC-113", "CLAUDE_API_DISABLED=1 overrides any provider selection",
                       "SUMMARY_PROVIDER='anthropic', CLAUDE_API_DISABLED='1'",
                       providers.TEMPLATE, result)
        self.assertEqual(result, providers.TEMPLATE)

    def test_env_values_are_stripped(self) -> None:
        """A stray leading/trailing space in .env must not break provider match."""
        with patch.dict("os.environ", {"SUMMARY_PROVIDER": " gemini "}, clear=True):
            result = providers.active_provider()
        print_evidence("TC-114", "Leading/trailing whitespace in the env value is stripped",
                       "SUMMARY_PROVIDER = ' gemini '", providers.GEMINI, result)
        self.assertEqual(result, providers.GEMINI)


class IsConfiguredTests(unittest.TestCase):
    def test_template_is_never_configured(self) -> None:
        with patch.dict("os.environ", {"SUMMARY_PROVIDER": "template"}, clear=True):
            result = providers.is_configured()
        print_evidence("TC-115", "Template provider is never 'configured' (no API call)",
                       "SUMMARY_PROVIDER = 'template'", False, result)
        self.assertFalse(result)

    def test_gemini_needs_its_api_key(self) -> None:
        with patch.dict("os.environ", {"SUMMARY_PROVIDER": "gemini"}, clear=True):
            without_key = providers.is_configured()
        with patch.dict(
            "os.environ",
            {"SUMMARY_PROVIDER": "gemini", "GEMINI_API_KEY": "key"},
            clear=True,
        ):
            with_key = providers.is_configured()
        print_evidence("TC-116", "Gemini is only 'configured' once GEMINI_API_KEY is set",
                       "SUMMARY_PROVIDER='gemini', with/without GEMINI_API_KEY",
                       {"without_key": False, "with_key": True},
                       {"without_key": without_key, "with_key": with_key})
        self.assertFalse(without_key)
        self.assertTrue(with_key)

    def test_anthropic_needs_its_api_key(self) -> None:
        with patch.dict("os.environ", {"SUMMARY_PROVIDER": "anthropic"}, clear=True):
            without_key = providers.is_configured()
        with patch.dict(
            "os.environ",
            {"SUMMARY_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "key"},
            clear=True,
        ):
            with_key = providers.is_configured()
        print_evidence("TC-117", "Anthropic is only 'configured' once ANTHROPIC_API_KEY is set",
                       "SUMMARY_PROVIDER='anthropic', with/without ANTHROPIC_API_KEY",
                       {"without_key": False, "with_key": True},
                       {"without_key": without_key, "with_key": with_key})
        self.assertFalse(without_key)
        self.assertTrue(with_key)


class IsRetryableTests(unittest.TestCase):
    """Whether an exception is worth another attempt - drives the caller's
    backoff and circuit breaker, so misclassifying it either floods retries
    on a permanent failure or gives up on a genuinely transient one."""

    def test_provider_unavailable_is_never_retryable(self) -> None:
        result = providers.is_retryable(providers.ProviderUnavailable("no key"))
        print_evidence("TC-118", "ProviderUnavailable is never retryable",
                       "ProviderUnavailable('no key')", False, result)
        self.assertFalse(result)

    def test_any_runtime_error_is_treated_as_permanent(self) -> None:
        result = providers.is_retryable(RuntimeError("config or programming fault"))
        print_evidence("TC-119", "Any RuntimeError is treated as a permanent failure",
                       "RuntimeError('config or programming fault')", False, result)
        self.assertFalse(result)

    def test_connection_error_is_retryable(self) -> None:
        result = providers.is_retryable(ConnectionError("network blip"))
        print_evidence("TC-120", "ConnectionError is retryable",
                       "ConnectionError('network blip')", True, result)
        self.assertTrue(result)

    def test_timeout_error_is_retryable(self) -> None:
        result = providers.is_retryable(TimeoutError("slow"))
        print_evidence("TC-121", "TimeoutError is retryable",
                       "TimeoutError('slow')", True, result)
        self.assertTrue(result)

    def test_generic_exception_with_no_status_defaults_to_retryable(self) -> None:
        result = providers.is_retryable(Exception("unclassified"))
        print_evidence("TC-122", "An unclassified exception defaults to retryable",
                       "Exception('unclassified'), no status code", True, result)
        self.assertTrue(result)

    def test_http_status_client_errors_are_not_retryable(self) -> None:
        exc = Exception("bad request")
        exc.status_code = 404
        result = providers.is_retryable(exc)
        print_evidence("TC-123", "A 4xx status_code is not retryable",
                       "Exception with status_code = 404", False, result)
        self.assertFalse(result)
        for code in (400, 401, 403, 422):
            other = Exception("bad request")
            other.status_code = code
            self.assertFalse(providers.is_retryable(other), f"status {code} should not retry")

    def test_http_status_other_codes_are_retryable(self) -> None:
        exc = Exception("server error")
        exc.status_code = 500
        result = providers.is_retryable(exc)
        print_evidence("TC-124", "A 5xx status_code is retryable",
                       "Exception with status_code = 500", True, result)
        self.assertTrue(result)

    def test_code_attribute_is_used_when_status_code_is_absent(self) -> None:
        exc = Exception("anthropic-style error")
        exc.code = 429
        result = providers.is_retryable(exc)
        print_evidence("TC-125", "The .code attribute is used when .status_code is absent",
                       "Exception with code = 429, no status_code", True, result)
        self.assertTrue(result)

    def _fake_genai_errors(self):
        """A stand-in for google.genai.errors, so these tests are deterministic
        regardless of whether the optional google-genai package is installed."""

        class ServerError(Exception):
            pass

        class ClientError(Exception):
            def __init__(self, message, code=None):
                super().__init__(message)
                self.code = code

        return type("FakeErrors", (), {"ServerError": ServerError, "ClientError": ClientError})

    def test_gemini_server_error_is_retryable(self) -> None:
        fake = self._fake_genai_errors()
        with patch.object(providers, "_genai_errors", fake):
            result = providers.is_retryable(fake.ServerError("5xx"))
        print_evidence("TC-126", "A Gemini ServerError (5xx) is retryable",
                       "genai.errors.ServerError('5xx')", True, result)
        self.assertTrue(result)

    def test_gemini_client_error_non_429_is_not_retryable(self) -> None:
        fake = self._fake_genai_errors()
        with patch.object(providers, "_genai_errors", fake):
            exc = fake.ClientError("bad request", code=400)
            result = providers.is_retryable(exc)
        print_evidence("TC-127", "A Gemini ClientError that isn't 429 is not retryable",
                       "genai.errors.ClientError(code=400)", False, result)
        self.assertFalse(result)

    def test_gemini_client_error_429_with_no_quota_is_not_retryable(self) -> None:
        """A project with no free-tier allocation reports 'limit: 0' on a 429 -
        retrying can never succeed and only floods the log."""
        fake = self._fake_genai_errors()
        with patch.object(providers, "_genai_errors", fake):
            exc = fake.ClientError("quota exceeded, limit: 0", code=429)
            result = providers.is_retryable(exc)
        print_evidence("TC-128", "A 429 with zero free-tier quota is not retryable",
                       "genai.errors.ClientError('...limit: 0', code=429)", False, result)
        self.assertFalse(result)

    def test_gemini_client_error_429_daily_cap_is_not_retryable(self) -> None:
        fake = self._fake_genai_errors()
        with patch.object(providers, "_genai_errors", fake):
            exc = fake.ClientError("RESOURCE_EXHAUSTED PerDay", code=429)
            result = providers.is_retryable(exc)
        print_evidence("TC-129", "A 429 hitting the daily cap is not retryable",
                       "genai.errors.ClientError('...PerDay', code=429)", False, result)
        self.assertFalse(result)

    def test_gemini_client_error_429_genuine_rate_limit_is_retryable(self) -> None:
        fake = self._fake_genai_errors()
        with patch.object(providers, "_genai_errors", fake):
            exc = fake.ClientError("too many requests per minute", code=429)
            result = providers.is_retryable(exc)
        print_evidence("TC-130", "A genuine per-minute 429 rate limit is retryable",
                       "genai.errors.ClientError('...per minute', code=429)", True, result)
        self.assertTrue(result)


class GenerateDispatchTests(unittest.IsolatedAsyncioTestCase):
    """generate() must call the backend matching the active provider, and
    refuse (rather than silently no-op) when the provider makes no API call."""

    async def test_template_provider_raises_provider_unavailable(self) -> None:
        with patch.dict("os.environ", {"SUMMARY_PROVIDER": "template"}, clear=True):
            with self.assertRaises(providers.ProviderUnavailable) as context:
                await providers.generate("system", "user")
        print_evidence("TC-131", "generate() refuses the template provider",
                       "SUMMARY_PROVIDER = 'template'",
                       "ProviderUnavailable", type(context.exception).__name__)

    async def test_gemini_provider_dispatches_to_gemini_backend(self) -> None:
        # _BACKENDS captured the original functions at import time, so the
        # dispatch table itself - not the module attribute - must be patched.
        mocked = AsyncMock(return_value="gemini summary")
        with patch.dict("os.environ", {"SUMMARY_PROVIDER": "gemini"}, clear=True), \
             patch.dict(providers._BACKENDS, {providers.GEMINI: mocked}):
            result = await providers.generate("system", "user", max_tokens=42)
        print_evidence("TC-132", "generate() dispatches to the Gemini backend",
                       "SUMMARY_PROVIDER = 'gemini'", "gemini summary", result)
        self.assertEqual(result, "gemini summary")
        mocked.assert_awaited_once_with("system", "user", 42)

    async def test_anthropic_provider_dispatches_to_anthropic_backend(self) -> None:
        mocked = AsyncMock(return_value="claude summary")
        with patch.dict("os.environ", {"SUMMARY_PROVIDER": "anthropic"}, clear=True), \
             patch.dict(providers._BACKENDS, {providers.ANTHROPIC: mocked}):
            result = await providers.generate("system", "user")
        print_evidence("TC-133", "generate() dispatches to the Anthropic backend",
                       "SUMMARY_PROVIDER = 'anthropic'", "claude summary", result)
        self.assertEqual(result, "claude summary")
        mocked.assert_awaited_once()

    async def test_missing_gemini_key_raises_provider_unavailable(self) -> None:
        with patch.dict("os.environ", {"SUMMARY_PROVIDER": "gemini"}, clear=True):
            with self.assertRaises(providers.ProviderUnavailable) as context:
                await providers.generate("system", "user")
        print_evidence("TC-134", "Missing GEMINI_API_KEY raises ProviderUnavailable",
                       "SUMMARY_PROVIDER='gemini', GEMINI_API_KEY unset",
                       "ProviderUnavailable", type(context.exception).__name__)

    async def test_missing_anthropic_key_raises_provider_unavailable(self) -> None:
        with patch.dict("os.environ", {"SUMMARY_PROVIDER": "anthropic"}, clear=True):
            with self.assertRaises(providers.ProviderUnavailable) as context:
                await providers.generate("system", "user")
        print_evidence("TC-135", "Missing ANTHROPIC_API_KEY raises ProviderUnavailable",
                       "SUMMARY_PROVIDER='anthropic', ANTHROPIC_API_KEY unset",
                       "ProviderUnavailable", type(context.exception).__name__)


class ResetClientsTests(unittest.TestCase):
    def test_reset_clients_clears_cached_clients(self) -> None:
        providers._gemini_client = object()
        providers._anthropic_client = object()
        providers.reset_clients()
        print_evidence("TC-136", "reset_clients() drops both cached SDK clients",
                       "clients pre-populated with sentinel objects",
                       {"gemini_client": None, "anthropic_client": None},
                       {"gemini_client": providers._gemini_client,
                        "anthropic_client": providers._anthropic_client})
        self.assertIsNone(providers._gemini_client)
        self.assertIsNone(providers._anthropic_client)


if __name__ == "__main__":
    unittest.main()
