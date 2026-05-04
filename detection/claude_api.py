"""
detection/claude_api.py
AI-powered traffic summary generation using the Anthropic Claude API.

This module provides the ``TrafficSummaryService`` class, which:
  - Accepts detection data from the YOLO/OpenCV pipeline (or mock).
  - Calls the Claude API (async) to generate human-readable traffic summaries.
  - Falls back to template-based summaries when the API is unavailable.
  - Caches the last successful summary per camera for resilience.
  - Uses exponential backoff on transient failures (SRS Section 5.2.8).

Author : Yadhav Sharma Ramsahye (22108355) — Developer
Unit   : ISAD3000 Capstone Computing Project 1
Team   : IBL Group — Traffic Bottleneck Detection System
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DetectionData:
    """Structured representation of a single pipeline detection result."""

    camera_id: str
    vehicle_count: int
    severity: str          # "free" | "moderate" | "heavy" | "bottleneck"
    color: str             # hex colour associated with severity
    timestamp: str         # ISO-8601 UTC string
    fps_processed: float = 0.0
    frame_shape: list[int] = field(default_factory=lambda: [720, 1280])

    @classmethod
    def from_dict(cls, data: dict) -> "DetectionData":
        """Build a ``DetectionData`` from the dict yielded by the pipeline."""
        return cls(
            camera_id=data["camera_id"],
            vehicle_count=data["vehicle_count"],
            severity=data["severity"],
            color=data["color"],
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
            fps_processed=data.get("fps_processed", 0.0),
            frame_shape=data.get("frame_shape", [720, 1280]),
        )


@dataclass
class TrafficSummary:
    """Container for a generated traffic summary (API or fallback)."""

    camera_id: str
    summary: str           # 2-3 sentence human-readable summary
    severity: str
    vehicle_count: int
    timestamp: str         # when the summary was generated
    source: str            # "claude_api" | "template_fallback"


# ---------------------------------------------------------------------------
# Retry / back-off configuration (SRS 5.2.8)
# ---------------------------------------------------------------------------

_MAX_RETRIES: int = 3
_BASE_DELAY: float = 1.0          # seconds
_MAX_DELAY: float = 16.0          # cap for exponential growth
_JITTER_FACTOR: float = 0.25      # +-25% randomness on delay

# ---------------------------------------------------------------------------
# Circuit-breaker state
# ---------------------------------------------------------------------------
# Shared across all callers (there is only one TrafficSummaryService instance).
#
# _CB_OPEN_UNTIL   – monotonic timestamp; breaker is open while now < this.
#                    0.0 means closed (normal operation).
# _CB_COOLDOWN     – how long (seconds) to keep the breaker open after a
#                    permanent error (401/403/billing-400).  Auto-recovers
#                    after cooldown: the next call becomes a probe.
# _DISABLED_LOG_*  – state for the once-per-60 s throttle on the
#                    "API unavailable" log message.
# ---------------------------------------------------------------------------
_CB_COOLDOWN: float = 300.0        # 5-minute breaker window
_CB_OPEN_UNTIL: float = 0.0        # 0 → breaker closed
_DISABLED_LOG_INTERVAL: float = 60.0
_disabled_log_next: float = 0.0


def _backoff_delay(attempt: int) -> float:
    """Return the delay (seconds) for the given attempt using exponential
    backoff with jitter, capped at ``_MAX_DELAY``.

    Formula: min(base * 2^attempt, max_delay) +/- jitter
    """
    import random

    delay = min(_BASE_DELAY * (2 ** attempt), _MAX_DELAY)
    jitter = delay * _JITTER_FACTOR * (2 * random.random() - 1)
    return max(0, delay + jitter)


# ---------------------------------------------------------------------------
# Circuit-breaker helpers
# ---------------------------------------------------------------------------

def _is_permanent_api_error(exc: Exception) -> bool:
    """Return True for errors that will NOT resolve on immediate retry.

    Permanent:  401 Unauthorised (bad/missing key), 403 Forbidden,
                400 responses that mention billing / credit balance.
    Transient:  5xx server errors, 429 rate-limit, network failures
                → handled by the existing exponential-backoff loop.
    """
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return True
    if status == 400:
        msg = str(exc).lower()
        if any(kw in msg for kw in ("credit", "billing", "balance", "quota")):
            return True
    return False


def _api_usable() -> bool:
    """Return True when it is worth attempting the Claude API.

    Returns False — fast-path to template — if ANY of the following hold:
      - ANTHROPIC_API_KEY is unset or blank
      - CLAUDE_API_DISABLED=1 is set in the environment (kill switch for demos)
      - the circuit breaker is currently open (monotonic clock check)
    """
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return False
    if os.environ.get("CLAUDE_API_DISABLED", "").strip() == "1":
        return False
    if time.monotonic() < _CB_OPEN_UNTIL:
        return False
    return True


def _open_circuit_breaker(exc: Exception) -> None:
    """Open the circuit breaker for ``_CB_COOLDOWN`` seconds."""
    global _CB_OPEN_UNTIL
    _CB_OPEN_UNTIL = time.monotonic() + _CB_COOLDOWN
    logger.warning(
        "Circuit breaker OPEN for %.0fs — permanent API error: %s",
        _CB_COOLDOWN, exc,
    )


def _log_api_unavailable_throttled() -> None:
    """Emit one INFO log per ``_DISABLED_LOG_INTERVAL`` seconds."""
    global _disabled_log_next
    now = time.monotonic()
    if now >= _disabled_log_next:
        logger.info(
            "Claude API unavailable (key missing, CLAUDE_API_DISABLED=1, or "
            "circuit breaker open) — template fallback in use."
        )
        _disabled_log_next = now + _DISABLED_LOG_INTERVAL


# ---------------------------------------------------------------------------
# Template-based fallback summaries (no API call required)
# ---------------------------------------------------------------------------

# Mapping of severity to a human-friendly label used in templates.
_SEVERITY_LABELS: dict[str, str] = {
    "free":       "Free-flowing",
    "moderate":   "Moderate",
    "heavy":      "Heavy congestion",
    "bottleneck": "Bottleneck",
}

# Pre-written route suggestions per well-known camera location.  If a camera
# is not listed here the template uses a generic suggestion.
_ROUTE_SUGGESTIONS: dict[str, str] = {
    "port_louis":        "Consider alternative routes via the M1 motorway or Harbour Bridge bypass.",
    "grand_baie":        "Consider using the B13 coastal road or Royal Road as an alternative.",
    "caudan":            "Try the A1 motorway northbound or the Quay D waterfront detour.",
    "ebene":             "Consider the Cyber City ring road or St Jean bypass.",
    "quatre_bornes":     "Use the Phoenix–Vacoas link road or Route Royale as alternatives.",
    "curepipe":          "Try the A10 towards Floreal or the Forest Side detour.",
    "rose_hill":         "Consider the Stanley–Beau Bassin connector or Route Hugnin.",
}


def _template_summary(data: DetectionData) -> str:
    """Generate a purely template-based summary with no API dependency.

    Returns a 2-3 sentence string suitable for the alert panel UI.
    """
    label = _SEVERITY_LABELS.get(data.severity, data.severity.title())

    # Build the base sentence.
    base = (
        f"{label} detected at {_camera_display_name(data.camera_id)} "
        f"with {data.vehicle_count} vehicle{'s' if data.vehicle_count != 1 else ''} "
        f"in the detection zone."
    )

    # Append route suggestion for heavy / bottleneck.
    if data.severity in ("heavy", "bottleneck"):
        suggestion = _ROUTE_SUGGESTIONS.get(
            data.camera_id,
            "Consider using an alternative route to avoid delays.",
        )
        return f"{base} {suggestion}"

    return base


def _template_alert_description(data: DetectionData) -> str:
    """Generate a template-based alert description for high-severity events."""
    label = _SEVERITY_LABELS.get(data.severity, data.severity.title())
    return (
        f"ALERT: {label} at {_camera_display_name(data.camera_id)}. "
        f"{data.vehicle_count} vehicles detected. "
        f"Severity level: {data.severity.upper()}. "
        f"Immediate attention may be required."
    )


def _camera_display_name(camera_id: str) -> str:
    """Convert a snake_case camera ID into a human-readable name.

    Examples
    --------
    >>> _camera_display_name("port_louis")
    'Port Louis'
    >>> _camera_display_name("caudan_north")
    'Caudan North'
    """
    return camera_id.replace("_", " ").title()


# ---------------------------------------------------------------------------
# Claude API interaction (async)
# ---------------------------------------------------------------------------

# Lazy-loaded Anthropic client (created on first use so that import of this
# module never fails even when the ``anthropic`` package is not installed).
_client: Optional[object] = None


def _get_client():
    """Return a shared ``anthropic.AsyncAnthropic`` client, creating it on
    first call.  Raises ``RuntimeError`` if the API key is not set or the
    ``anthropic`` package is missing.
    """
    global _client

    if _client is not None:
        return _client

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY environment variable is not set. "
            "Claude API summaries are unavailable; using template fallback."
        )

    try:
        import anthropic  # noqa: E402  — intentionally lazy
    except ImportError as exc:
        raise RuntimeError(
            "The 'anthropic' package is not installed.  "
            "Run: pip install anthropic"
        ) from exc

    _client = anthropic.AsyncAnthropic(api_key=api_key)
    logger.info("Anthropic async client initialised.")
    return _client


# The model used for all summary generation (cost-effective for short text).
_MODEL: str = "claude-sonnet-4-20250514"

# System prompt that shapes all Claude responses for traffic summaries.
_SYSTEM_PROMPT: str = (
    "You are an AI traffic analyst for the IBL Group Traffic Bottleneck "
    "Detection System in Mauritius. Your job is to generate short, clear "
    "traffic summaries for a public-facing dashboard.\n\n"
    "Rules:\n"
    "- Keep every summary to exactly 2-3 sentences.\n"
    "- State the location, vehicle count, and severity level.\n"
    "- For heavy or bottleneck severity, suggest one specific alternative "
    "route in Mauritius.\n"
    "- Use professional but accessible language (no jargon).\n"
    "- Do NOT include timestamps, technical details, or markdown formatting.\n"
    "- Do NOT start with 'Sure' or any preamble — go straight to the summary."
)


async def _call_claude_api(data: DetectionData, prompt_type: str = "summary") -> str:
    """Make a single Claude API call and return the generated text.

    Parameters
    ----------
    data : DetectionData
        The current detection snapshot.
    prompt_type : str
        Either ``"summary"`` (general traffic summary) or ``"alert"``
        (alert description for heavy / bottleneck events).

    Returns
    -------
    str
        The raw text content from Claude's response.

    Raises
    ------
    Exception
        Any API or network error is propagated to the caller for retry
        handling.
    """
    client = _get_client()

    if prompt_type == "alert":
        user_message = (
            f"Generate a short ALERT description for this traffic event:\n"
            f"- Camera: {_camera_display_name(data.camera_id)}\n"
            f"- Vehicle count: {data.vehicle_count}\n"
            f"- Severity: {data.severity}\n"
            f"Include an urgent tone and suggest one alternative route."
        )
    else:
        user_message = (
            f"Generate a traffic summary for this detection:\n"
            f"- Camera location: {_camera_display_name(data.camera_id)}\n"
            f"- Vehicle count: {data.vehicle_count}\n"
            f"- Severity: {data.severity}\n"
            f"- Colour code: {data.color}"
        )

    response = await client.messages.create(
        model=_MODEL,
        max_tokens=200,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    # Extract the text from the first content block.
    return response.content[0].text.strip()


# ---------------------------------------------------------------------------
# TrafficSummaryService — the public interface
# ---------------------------------------------------------------------------

class TrafficSummaryService:
    """High-level service that generates and caches traffic summaries.

    Usage
    -----
    >>> service = TrafficSummaryService()
    >>> summary = await service.generate_summary(detection_dict)

    The service tries the Claude API first.  On failure it retries with
    exponential backoff (up to ``_MAX_RETRIES`` attempts) and then falls
    back to a template-based summary so that the dashboard always has
    *something* to show.

    Thread-safety: designed for single-event-loop use (``asyncio``).
    """

    def __init__(self) -> None:
        # Cache: camera_id -> last successful TrafficSummary
        self._cache: dict[str, TrafficSummary] = {}
        # Track consecutive API failures for circuit-breaker logging.
        self._consecutive_failures: int = 0

    # -- Public methods -----------------------------------------------------

    async def generate_summary(self, detection: dict) -> TrafficSummary:
        """Generate a traffic summary for the given detection result.

        Parameters
        ----------
        detection : dict
            A detection dict as yielded by ``run_mock_pipeline`` /
            ``run_pipeline`` (keys: camera_id, vehicle_count, severity,
            color, timestamp, …).

        Returns
        -------
        TrafficSummary
            Always returns a summary — either from the Claude API or from
            the template fallback.
        """
        data = DetectionData.from_dict(detection)
        summary = await self._try_claude_api(data, prompt_type="summary")
        return summary

    async def generate_alert_description(self, detection: dict) -> TrafficSummary:
        """Generate an alert-specific description for heavy/bottleneck events.

        Same resilience guarantees as ``generate_summary``.
        """
        data = DetectionData.from_dict(detection)
        summary = await self._try_claude_api(data, prompt_type="alert")
        return summary

    def get_cached_summary(self, camera_id: str) -> Optional[TrafficSummary]:
        """Return the last successfully generated summary for a camera,
        or ``None`` if no summary has been cached yet."""
        return self._cache.get(camera_id)

    # -- Internal helpers ---------------------------------------------------

    async def _try_claude_api(
        self,
        data: DetectionData,
        prompt_type: str,
    ) -> TrafficSummary:
        """Attempt the Claude API with retries, falling back to templates.

        Fast-path decisions (before any network call):
          - Key missing or blank               → template immediately
          - CLAUDE_API_DISABLED=1 in env       → template immediately (kill switch)
          - Circuit breaker open               → template immediately

        Permanent errors (401/403/billing-400) on any attempt:
          - Open a 5-minute circuit breaker.
          - Skip remaining retries immediately.
          - Return template fallback.

        Transient errors (5xx, 429, network blips):
          - Retry with exponential back-off (SRS 5.2.8):
              attempt 0 → ~1 s delay
              attempt 1 → ~2 s delay
              attempt 2 → ~4 s delay
          - After all retries exhausted → template fallback.
        """
        # ── Fast path: API is not usable right now ─────────────────────────
        if not _api_usable():
            _log_api_unavailable_throttled()
            return self._fallback(data, prompt_type)

        # ── Retry loop for transient errors ───────────────────────────────
        for attempt in range(_MAX_RETRIES):
            try:
                text = await _call_claude_api(data, prompt_type)
                self._consecutive_failures = 0

                summary = TrafficSummary(
                    camera_id=data.camera_id,
                    summary=text,
                    severity=data.severity,
                    vehicle_count=data.vehicle_count,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    source="claude_api",
                )
                self._cache[data.camera_id] = summary
                logger.info(
                    "[%s] Claude API summary generated (attempt %d).",
                    data.camera_id,
                    attempt + 1,
                )
                return summary

            except Exception as exc:
                self._consecutive_failures += 1

                # Permanent error → open breaker, skip remaining retries.
                if _is_permanent_api_error(exc):
                    _open_circuit_breaker(exc)
                    break

                # Transient error → log and sleep before next attempt.
                delay = _backoff_delay(attempt)
                logger.warning(
                    "[%s] Claude API attempt %d/%d failed: %s — retrying in %.1fs",
                    data.camera_id,
                    attempt + 1,
                    _MAX_RETRIES,
                    exc,
                    delay,
                )
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(delay)

        # All retries exhausted (or breaker just opened) — use template.
        return self._fallback(data, prompt_type)

    def _fallback(self, data: DetectionData, prompt_type: str) -> TrafficSummary:
        """Build a template-based summary (zero external dependencies)."""
        if prompt_type == "alert":
            text = _template_alert_description(data)
        else:
            text = _template_summary(data)

        summary = TrafficSummary(
            camera_id=data.camera_id,
            summary=text,
            severity=data.severity,
            vehicle_count=data.vehicle_count,
            timestamp=datetime.now(timezone.utc).isoformat(),
            source="template_fallback",
        )
        # Cache the fallback too so there is always *something* to display.
        self._cache[data.camera_id] = summary
        logger.debug(
            "[%s] Template fallback used (consecutive API failures: %d).",
            data.camera_id,
            self._consecutive_failures,
        )
        return summary


# ---------------------------------------------------------------------------
# Module-level convenience instance
# ---------------------------------------------------------------------------
# A single shared service instance used by ``main.py``.  Import it as:
#     from detection.claude_api import summary_service
summary_service = TrafficSummaryService()
