"""
detection/claude_api.py
AI-powered traffic summary generation using the Anthropic Claude API.

This module provides the ``TrafficSummaryService`` class, which:
  - Accepts detection data from the YOLO/OpenCV pipeline (or mock).
  - Calls the Claude API (async) to generate human-readable traffic summaries.
  - Falls back to template-based summaries when the API is unavailable.
  - Caches the last successful summary per camera for resilience.
  - Uses exponential backoff on transient failures (SRS Section 5.2.8).

Author : Yadhav Sharma Ramsahye (22108355) — Scrum Master
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
    # Current severity of every camera, so a detour can be checked against live
    # conditions before it is recommended. Optional: absent means "unknown",
    # in which case the static suggestion is used unchanged.
    live_severity: Optional[dict] = None

    @classmethod
    def from_dict(cls, data: dict, live_severity: Optional[dict] = None) -> "DetectionData":
        """Build a ``DetectionData`` from the dict yielded by the pipeline."""
        return cls(
            camera_id=data["camera_id"],
            vehicle_count=data["vehicle_count"],
            severity=data["severity"],
            color=data["color"],
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
            fps_processed=data.get("fps_processed", 0.0),
            frame_shape=data.get("frame_shape", [720, 1280]),
            live_severity=live_severity,
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

# Circuit breaker: after this many consecutive failures the API is skipped
# entirely for ``_BREAKER_COOLDOWN`` seconds.  Without this the service would
# re-attempt (and re-sleep) on every detection cycle of every camera even when
# the failure is permanent — e.g. an exhausted credit balance.
_BREAKER_THRESHOLD: int = 5
_BREAKER_COOLDOWN: float = 300.0  # seconds before a single probe is allowed

# HTTP status codes that can never succeed on retry.  These are client-side
# errors (bad request, auth, exhausted credit, missing model) — retrying only
# wastes time and floods the log.  429 and 5xx are excluded: those are genuinely
# transient and remain eligible for backoff.
_NON_RETRYABLE_STATUS: frozenset[int] = frozenset({400, 401, 403, 404, 422})


def _is_retryable(exc: Exception) -> bool:
    """Return ``True`` if retrying ``exc`` could plausibly succeed.

    Anthropic SDK errors carry a ``status_code``; anything in
    ``_NON_RETRYABLE_STATUS`` is a permanent client error.  A missing API key
    or absent ``anthropic`` package (``RuntimeError`` from ``_get_client``) is
    likewise permanent.  Everything else — timeouts, connection resets, 429,
    5xx — is treated as transient.
    """
    if isinstance(exc, RuntimeError):
        return False

    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status not in _NON_RETRYABLE_STATUS

    return True


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
# Cameras whose road a given detour actually routes traffic onto. Used to
# suppress a suggestion when the alternative is itself congested — advising a
# driver onto a jammed road is worse than giving no advice, and the system has
# the live data to know the difference.
_ROUTE_COVERED_BY: dict[str, str] = {
    "caudan_north": "caudan_south",
    "caudan_south": "caudan_north",
    "la_chaussee":  "casernes",
    "casernes":     "la_chaussee",
}

_ROUTE_SUGGESTIONS: dict[str, str] = {
    # Keys must match the camera IDs produced by discover_cameras() — the
    # earlier "caudan" entry never matched "caudan_north"/"caudan_south", so
    # those cameras silently fell through to the generic suggestion.
    "caudan_north":      "Try the A1 motorway northbound or the Quay D waterfront detour.",
    "caudan_south":      "Try the A1 motorway southbound or the Quay D waterfront detour.",
    "la_chaussee":       "Consider Place d'Armes or the Pope Hennessy Street bypass.",
    "casernes":          "Consider Brabant Street or the Sir William Newton Street detour.",
    "port_louis":        "Consider alternative routes via the M1 motorway or Harbour Bridge bypass.",
    "grand_baie":        "Consider using the B13 coastal road or Royal Road as an alternative.",
    "caudan":            "Try the A1 motorway northbound or the Quay D waterfront detour.",
    "ebene":             "Consider the Cyber City ring road or St Jean bypass.",
    "quatre_bornes":     "Use the Phoenix–Vacoas link road or Route Royale as alternatives.",
    "curepipe":          "Try the A10 towards Floreal or the Forest Side detour.",
    "rose_hill":         "Consider the Stanley–Beau Bassin connector or Route Hugnin.",
}


def route_advice(camera_id: str, live_severity: dict[str, str] | None = None) -> str:
    """Detour text for a camera, suppressed when the alternative is also busy.

    ``live_severity`` maps camera_id → current severity. When the camera that
    watches the suggested alternative is itself heavy or bottlenecked, the
    named detour is withheld: routing drivers onto a road the system can see is
    jammed is worse than declining to advise.
    """
    suggestion = _ROUTE_SUGGESTIONS.get(camera_id)
    if not suggestion:
        return "Consider using an alternative route to avoid delays."

    alt_cam = _ROUTE_COVERED_BY.get(camera_id)
    if live_severity and alt_cam:
        alt_state = live_severity.get(alt_cam)
        if alt_state in ("heavy", "bottleneck"):
            return ("Alternative routes are also congested — expect delays on "
                    "any approach.")
    return suggestion


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
        return f"{base} {route_advice(data.camera_id, data.live_severity)}"

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


# The model used for all summary generation.  Haiku is sufficient here: the
# facts are supplied in the prompt (including the detour), so the model only
# phrases them — there is nothing for it to reason about or recall.
_MODEL: str = "claude-haiku-4-5"

# System prompt that shapes all Claude responses for traffic summaries.
# Kept deliberately short: it is re-sent on every request and is far below the
# minimum cacheable prefix, so every token here is billed on every call.
_SYSTEM_PROMPT: str = (
    "Traffic analyst for a public dashboard in Mauritius. Write plain-language "
    "traffic summaries.\n"
    "- Two sentences maximum. No preamble, markdown, or timestamps.\n"
    "- State location, vehicle count, and severity.\n"
    "- If a detour is supplied, rephrase that one. Never invent a road name."
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

    # Supply the curated detour rather than asking the model to recall one —
    # road names are the only part of the summary the model could get wrong.
    detour = ""
    if data.severity in ("heavy", "bottleneck"):
        detour = "\nDetour: " + route_advice(data.camera_id, data.live_severity)

    # `color` is dashboard styling the model never mentions, so it is not sent.
    facts = (
        f"Location: {_camera_display_name(data.camera_id)}\n"
        f"Vehicles: {data.vehicle_count}\n"
        f"Severity: {data.severity}{detour}"
    )
    if prompt_type == "alert":
        user_message = f"Urgent alert:\n{facts}"
    else:
        user_message = f"Summary:\n{facts}"

    response = await client.messages.create(
        model=_MODEL,
        # Safety cap only — billing is on tokens actually generated, so the
        # length control that saves money is the two-sentence rule in the
        # system prompt, not this ceiling.
        max_tokens=150,
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
        # Track consecutive API failures to drive the circuit breaker.
        self._consecutive_failures: int = 0
        # Monotonic timestamp before which the API must not be called at all.
        # 0.0 means the circuit is closed (normal operation).
        self._breaker_open_until: float = 0.0
        # Guards against every camera logging the same "circuit open" message.
        self._breaker_logged: bool = False

    # -- Public methods -----------------------------------------------------

    async def generate_summary(self, detection: dict,
                               live_severity: Optional[dict] = None) -> TrafficSummary:
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
        data = DetectionData.from_dict(detection, live_severity)
        summary = await self._try_claude_api(data, prompt_type="summary")
        return summary

    async def generate_alert_description(self, detection: dict,
                                         live_severity: Optional[dict] = None) -> TrafficSummary:
        """Generate an alert-specific description for heavy/bottleneck events.

        Same resilience guarantees as ``generate_summary``.
        """
        data = DetectionData.from_dict(detection, live_severity)
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

        Implements the exponential backoff strategy from SRS 5.2.8:
          attempt 0 → ~1 s delay
          attempt 1 → ~2 s delay
          attempt 2 → ~4 s delay
          (then give up and use the template)

        Two guards keep a persistent outage from flooding the API and the log:

        * Permanent errors (see ``_is_retryable``) abandon the retry loop
          immediately rather than sleeping through attempts that cannot work.
        * After ``_BREAKER_THRESHOLD`` consecutive failures the circuit opens
          and every call short-circuits to the template for
          ``_BREAKER_COOLDOWN`` seconds, after which a single probe is allowed
          through.  One success closes the circuit.
        """
        if self._breaker_is_open():
            return self._fallback(data, prompt_type)

        for attempt in range(_MAX_RETRIES):
            try:
                text = await _call_claude_api(data, prompt_type)
                self._close_breaker()

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

                if not _is_retryable(exc):
                    logger.warning(
                        "[%s] Claude API failed permanently (not retryable): %s",
                        data.camera_id,
                        exc,
                    )
                    self._maybe_open_breaker()
                    return self._fallback(data, prompt_type)

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

        # All retries exhausted — fall back to template.
        self._maybe_open_breaker()
        return self._fallback(data, prompt_type)

    # -- Circuit breaker ----------------------------------------------------

    def _breaker_is_open(self) -> bool:
        """Return ``True`` while the API should be skipped entirely.

        Once the cooldown elapses this returns ``False`` so exactly one probe
        call is attempted; the breaker stays armed until that probe either
        succeeds (closing it) or fails (re-arming the cooldown).
        """
        if self._breaker_open_until == 0.0:
            return False

        if time.monotonic() >= self._breaker_open_until:
            logger.info("Claude API cooldown elapsed — probing with one request.")
            self._breaker_open_until = 0.0
            self._breaker_logged = False
            return False

        return True

    def _maybe_open_breaker(self) -> None:
        """Open the circuit once failures cross ``_BREAKER_THRESHOLD``."""
        if self._consecutive_failures < _BREAKER_THRESHOLD:
            return

        self._breaker_open_until = time.monotonic() + _BREAKER_COOLDOWN
        if not self._breaker_logged:
            logger.warning(
                "Claude API circuit OPEN after %d consecutive failures — "
                "using template summaries for the next %.0fs.",
                self._consecutive_failures,
                _BREAKER_COOLDOWN,
            )
            self._breaker_logged = True

    def _close_breaker(self) -> None:
        """Reset all failure state after a successful call."""
        if self._consecutive_failures >= _BREAKER_THRESHOLD:
            logger.info("Claude API recovered — circuit CLOSED.")
        self._consecutive_failures = 0
        self._breaker_open_until = 0.0
        self._breaker_logged = False

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
        # While the circuit is open this runs on every detection cycle of every
        # camera, so keep it at debug — the single "circuit OPEN" warning
        # already records the outage.
        logger.log(
            logging.DEBUG if self._breaker_open_until else logging.INFO,
            "[%s] Using template fallback (consecutive API failures: %d).",
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
