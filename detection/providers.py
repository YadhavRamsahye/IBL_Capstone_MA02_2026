"""
detection/providers.py
Pluggable backends for traffic-summary generation.

Why this exists
---------------
The summary service previously called Anthropic directly, so switching model
vendor meant editing the service itself - and everything valuable in that
service (circuit breaker, retry/backoff, per-camera caching, template
fallback) is vendor-independent. This module isolates the one part that is
vendor-specific: turning a system prompt plus a user prompt into a string.

Selection is by environment, so moving between providers is a config change:

    SUMMARY_PROVIDER=template   # no API, no cost - the built-in fallback text
    SUMMARY_PROVIDER=gemini     # Google Gemini
    SUMMARY_PROVIDER=anthropic  # Claude

Adding a provider means adding one `_call_*` function and one dispatch entry;
nothing in the calling service changes.

Error contract
--------------
`generate()` raises `ProviderUnavailable` for conditions no retry can fix - no
key configured, SDK not installed, provider disabled. Everything else
propagates the SDK's own exception so `is_retryable()` can classify it, which
is what drives the caller's backoff and circuit breaker.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Imported once at module load rather than inside is_retryable(). Importing
# google.genai pulls in pydantic and protobuf and takes seconds; doing it lazily
# meant the first API failure stalled the detection loop while classifying the
# error. None when the SDK is absent, which is a supported configuration.
try:
    from google.genai import errors as _genai_errors
except ImportError:
    _genai_errors = None

TEMPLATE = "template"
GEMINI = "gemini"
ANTHROPIC = "anthropic"

DEFAULT_PROVIDER = TEMPLATE

# Free-tier quota is granted per model, not per key: an AI Studio key can list
# 42 models and still get "limit: 0" on most of them. gemini-2.0-flash and
# -flash-lite both return limit: 0 on a current free-tier key; this one has
# quota. Verify with tools/check_gemini.py before changing it.
GEMINI_DEFAULT_MODEL = "gemini-3-flash-preview"

# Extra output tokens reserved for Gemini 3's reasoning, which is billed against
# max_output_tokens. See the note in _call_gemini.
GEMINI_THINKING_ALLOWANCE = int(os.environ.get("GEMINI_THINKING_ALLOWANCE", "650"))


class ProviderUnavailable(RuntimeError):
    """Permanent configuration problem - retrying cannot help."""


def _env(name: str, default: str = "") -> str:
    # .strip() is deliberate: a key pasted into .env with a stray leading space
    # otherwise fails authentication with a confusing error.
    return os.environ.get(name, default).strip()


def active_provider() -> str:
    """Provider selected by SUMMARY_PROVIDER, normalised.

    CLAUDE_API_DISABLED=1 forces templates regardless, which is the kill switch
    used for demos and for silencing a misbehaving API.
    """
    if _env("CLAUDE_API_DISABLED") == "1":
        return TEMPLATE
    provider = _env("SUMMARY_PROVIDER", DEFAULT_PROVIDER).lower()
    if provider not in (TEMPLATE, GEMINI, ANTHROPIC):
        logger.warning(
            "Unknown SUMMARY_PROVIDER %r - falling back to %r. Valid values: "
            "%s, %s, %s.", provider, TEMPLATE, TEMPLATE, GEMINI, ANTHROPIC,
        )
        return TEMPLATE
    return provider


def is_configured() -> bool:
    """True when the selected provider has what it needs to make a call."""
    provider = active_provider()
    if provider == TEMPLATE:
        return False                      # templates need no API call at all
    if provider == GEMINI:
        return bool(_env("GEMINI_API_KEY"))
    if provider == ANTHROPIC:
        return bool(_env("ANTHROPIC_API_KEY"))
    return False


def is_retryable(exc: Exception) -> bool:
    """Whether retrying `exc` could plausibly succeed.

    Rate limits and server faults are transient; malformed requests, bad keys
    and exhausted quotas are not. Retrying the latter only wastes time and
    floods the log, which is what the circuit breaker exists to prevent.
    """
    # ProviderUnavailable is the explicit signal, but treat any RuntimeError as
    # permanent: a provider call raising one means a configuration or
    # programming fault, not a transient network condition. Genuine transients
    # surface as ConnectionError, TimeoutError or an SDK error with a status.
    if isinstance(exc, RuntimeError):
        return False

    # Google: ClientError is 4xx, ServerError is 5xx.
    if _genai_errors is not None:
        if isinstance(exc, _genai_errors.ServerError):
            return True
        if isinstance(exc, _genai_errors.ClientError):
            if getattr(exc, "code", None) != 429:
                return False
            # Not every 429 is transient. A project with no free-tier
            # allocation reports "limit: 0", and a daily cap will not reset
            # within any sensible backoff - retrying either just burns the
            # retry budget and floods the log. Only a genuine per-minute rate
            # limit is worth another attempt.
            msg = str(exc)
            if "limit: 0" in msg or "PerDay" in msg:
                return False
            return True

    # Anthropic and anything else exposing an HTTP status.
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if isinstance(status, int):
        return status not in (400, 401, 403, 404, 422)

    return True          # network/timeout - worth another attempt


# ── Backends ──────────────────────────────────────────────────────────────────

_gemini_client = None


async def _call_gemini(system: str, user: str, max_tokens: int) -> str:
    global _gemini_client

    api_key = _env("GEMINI_API_KEY")
    if not api_key:
        raise ProviderUnavailable("GEMINI_API_KEY is not set.")

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise ProviderUnavailable(
            "The 'google-genai' package is not installed. Run: pip install google-genai"
        ) from exc

    model = _env("GEMINI_MODEL", GEMINI_DEFAULT_MODEL)

    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=api_key)
        logger.info("Gemini client initialised (model=%s).", model)

    # Gemini 3 reasons before answering and those thinking tokens are charged
    # against max_output_tokens. Measured on gemini-3-flash-preview, a
    # two-sentence traffic summary spends 140-270 tokens thinking - so passing
    # the caller's 150-token cap straight through left ~6 tokens for the actual
    # answer and returned a truncated fragment. The visible-text budget is
    # therefore the caller's value plus an allowance for reasoning.
    response = await _gemini_client.aio.models.generate_content(
        model=model,
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max_tokens + GEMINI_THINKING_ALLOWANCE,
        ),
    )

    text = (response.text or "").strip()
    if text:
        return text

    # Distinguish "thinking ate the budget" from a genuine refusal/filter,
    # because the fixes are completely different.
    usage = getattr(response, "usage_metadata", None)
    thoughts = getattr(usage, "thoughts_token_count", None) or 0
    if thoughts >= max_tokens + GEMINI_THINKING_ALLOWANCE - 5:
        raise ValueError(
            f"Gemini produced no visible text: all "
            f"{thoughts} tokens went to reasoning. Raise "
            f"GEMINI_THINKING_ALLOWANCE (currently {GEMINI_THINKING_ALLOWANCE})."
        )
    raise ValueError(
        f"Gemini returned an empty response (finish_reason="
        f"{getattr(getattr(response, 'candidates', [None])[0], 'finish_reason', '?')})."
    )


_anthropic_client = None


async def _call_anthropic(system: str, user: str, max_tokens: int) -> str:
    global _anthropic_client

    api_key = _env("ANTHROPIC_API_KEY")
    if not api_key:
        raise ProviderUnavailable("ANTHROPIC_API_KEY is not set.")

    try:
        import anthropic
    except ImportError as exc:
        raise ProviderUnavailable(
            "The 'anthropic' package is not installed. Run: pip install anthropic"
        ) from exc

    if _anthropic_client is None:
        _anthropic_client = anthropic.AsyncAnthropic(api_key=api_key)
        logger.info("Anthropic client initialised.")

    response = await _anthropic_client.messages.create(
        model=_env("ANTHROPIC_MODEL", "claude-haiku-4-5"),
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text.strip()


_BACKENDS = {
    GEMINI: _call_gemini,
    ANTHROPIC: _call_anthropic,
}


async def generate(system: str, user: str, max_tokens: int = 150) -> str:
    """Generate one summary with the configured provider."""
    provider = active_provider()
    backend = _BACKENDS.get(provider)
    if backend is None:
        raise ProviderUnavailable(
            f"Provider {provider!r} does not make API calls; use the template fallback."
        )
    return await backend(system, user, max_tokens)


def reset_clients() -> None:
    """Drop cached clients so a changed key or model takes effect (tests)."""
    global _gemini_client, _anthropic_client
    _gemini_client = None
    _anthropic_client = None
