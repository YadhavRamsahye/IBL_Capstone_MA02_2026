"""
Author : Sahil Singh Rughoo (22414560) — Tech Lead / Yadhav Sharma Ramsahye (22108355) — Developer
Unit   : ISAD3000 Capstone Computing Project 1
Team   : IBL Group — Traffic Bottleneck Detection System traffic summaries
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from urllib.parse import quote_plus

from fastapi import FastAPI, HTTPException, Request, Form, Depends, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from email.message import EmailMessage
import smtplib
import uvicorn
import os
from dotenv import load_dotenv

# Load environment variables from .env file (if present) before any
# module tries to read ANTHROPIC_API_KEY or other secrets.
load_dotenv()

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from database import (
    DB_AVAILABLE, AsyncSessionLocal as _AsyncSession,
    HOST as _DB_HOST, PORT as _DB_PORT, DB_NAME as _DB_NAME,
)
from detection import direction
from detection.severity import capacity_for
from auth import (
    authenticate, create_password_reset_token, create_user,
    get_current_user, get_password_reset_user, get_session_secret,
    record_audit, reset_password, require_admin, require_user,
    websocket_user, _is_valid_email,
)

from detection.mock_pipeline import run_mock_pipeline
from detection.pipeline import run_pipeline
from detection.hls_pipeline import run_hls_pipeline, get_latest_frame, FRAME_INTERVAL
from detection.stationary_tracker import StationaryTracker
from detection import camera_catalogue
from detection.trafficwatch import discover_cameras
from detection.claude_api import summary_service
from detection.incident_detector import IncidentDetector

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM = os.getenv("SMTP_FROM", "andrea.ramsamy2@gmail.com")
PASSWORD_RESET_TOKEN_LIFETIME = int(os.getenv("PASSWORD_RESET_TOKEN_LIFETIME", "3600"))


def _smtp_configured() -> bool:
    return bool(SMTP_HOST and SMTP_FROM)


def _send_password_reset_email(to_email: str, reset_link: str) -> None:
    message = EmailMessage()
    message["Subject"] = "Reset your AI Traffic Bottleneck password"
    message["From"] = SMTP_FROM
    message["To"] = to_email
    message.set_content(
        "Hello,\n\n"
        "We received a request to reset the password for your AI Traffic Bottleneck account.\n\n"
        f"Reset your password by clicking the link below:\n\n{reset_link}\n\n"
        "If you did not request a password reset, you can safely ignore this email.\n\n"
        "This link will expire in one hour.\n\n"
        "IBL Capstone Project — AI Traffic Bottleneck Detection System"
    )

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
        server.starttls()
        if SMTP_USER and SMTP_PASSWORD:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(message)


# Keyed by camera_id; updated in-place by background tasks.
latest_detections: dict[str, dict] = {}

# Signals a fresh annotated frame landed for a camera, so an open MJPEG stream
# can wake up and check rather than polling _latest_frames on its own timer.
# Safe to set from here without call_soon_threadsafe: _store_annotated_frame
# runs inside asyncio.to_thread(_safe_next, gen) in the HLS camera loop below,
# and by the time that call returns control here, we're back on the event
# loop's own thread.
_frame_events: dict[str, asyncio.Event] = {}


def _get_frame_event(camera_id: str) -> asyncio.Event:
    ev = _frame_events.get(camera_id)
    if ev is None:
        ev = asyncio.Event()
        _frame_events[camera_id] = ev
    return ev

# Populated by lifespan() after discovery; read by /api/cameras.
_active_cameras: list[dict] = []

# Shared incident detector — analyses rolling count history per camera.
incident_detector = IncidentDetector()

# Interval (seconds) between successive reads from the mock generator.
_POLL_INTERVAL = 3.0

# Minimum seconds between Claude API summary requests per camera.
# Prevents flooding the API on every frame (mock yields ~7.5 fps).
_SUMMARY_COOLDOWN = 180.0

# A severity must hold for this many consecutive detections before it counts
# as a real transition.  Raw YOLO counts jitter frame to frame, so a camera
# sitting on a threshold boundary flaps free/moderate/heavy several times a
# minute; without debouncing every flap bypassed the cooldown and cost an
# API call.
_SEVERITY_DEBOUNCE = 4

# Absolute floor between API calls for one camera.  A confirmed severity
# change shortens the wait from _SUMMARY_COOLDOWN to this, but never skips it
# entirely — a camera parked on a threshold can confirm a transition
# repeatedly, and without this floor those transitions alone kept the call
# rate high.
_MIN_SUMMARY_INTERVAL = 60.0

# Vehicle-count bucket width.  Two detections in the same severity whose
# counts fall in the same bucket produce the same summary text, so the second
# one reuses the cached summary instead of paying for a fresh API call.
_COUNT_BUCKET = 3

# Longest a cached summary may be reused while nothing changes.  Bounds how
# stale the dashboard prose can get on a camera sitting in one steady state.
_SUMMARY_MAX_AGE = 900.0

# Track the last *confirmed* severity per camera so we only generate alerts on
# severity transitions (e.g., moderate -> heavy), not every frame.
_last_severity: dict[str, str] = {}

# Candidate severity awaiting confirmation, and how many consecutive
# detections have agreed with it so far.
_pending_severity: dict[str, tuple[str, int]] = {}

# Last (severity, count bucket) a summary was actually generated for.
_last_summary_key: dict[str, tuple[str, int]] = {}

# Track the last summary generation time per camera for cooldown.
_last_summary_time: dict[str, float] = {}

# WebSocket clients subscribed to /ws/detections.
connected_clients: set = set()


def _as_datetime(value) -> datetime:
    """Coerce an ISO-8601 string to a timezone-aware datetime for asyncpg.

    asyncpg binds TIMESTAMPTZ parameters strictly — it rejects strings rather
    than parsing them — so anything crossing into a timestamp column has to be
    converted here.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        logger.warning("Unparseable timestamp %r — using current time.", value)
        return datetime.now(timezone.utc)


def _live_severity() -> dict[str, str]:
    """Current severity of every camera, for validating detour suggestions.

    Route advice used to be a static string per camera, so the system could
    confidently send drivers onto a road it could see was jammed.
    """
    return {cid: det.get("severity", "free") for cid, det in latest_detections.items()}


# ── Sentinel pattern for safe generator iteration in async context ────────
#
# In Python 3.12, a StopIteration raised inside `asyncio.to_thread(next, gen)`
# is converted to RuntimeError before our `except StopIteration:` clause can
# catch it (PEP 479-related behaviour for threads). To work around this we
# wrap the bare `next()` call in a helper that catches StopIteration on the
# worker thread and returns a sentinel object instead. The async caller then
# tests for the sentinel by identity to detect generator exhaustion.
_GEN_DONE = object()


def _safe_next(gen):
    """Pull the next item from a sync generator, returning _GEN_DONE on exhaustion.

    Other exceptions are allowed to propagate so the caller can log them
    via the usual `except Exception` branch.
    """
    try:
        return next(gen)
    except StopIteration:
        return _GEN_DONE


async def _upsert_camera(db, camera_id: str) -> None:
    """Idempotent upsert of a camera's row in ``cameras``.

    traffic_snapshots, bottleneck_events and incidents all carry a foreign
    key on cameras(id), and _save_snapshot / _save_bottleneck_event /
    _save_incident are fired as independent, unordered asyncio tasks - none
    of them can assume another has already created the row. Calling this
    first in each makes every insert self-sufficient instead of depending on
    _save_snapshot happening to run first, which is what let a camera's very
    first bottleneck reading lose its write to a foreign-key violation
    (observed on casernes - see audit section 8).
    """
    cam = next((c for c in _active_cameras if c["camera_id"] == camera_id), {})
    await db.execute(text("""
        INSERT INTO cameras (id, name, latitude, longitude, capacity_pcu)
        VALUES (:id, :name, :lat, :lng, :capacity)
        ON CONFLICT (id) DO UPDATE
            SET last_seen = NOW(), capacity_pcu = EXCLUDED.capacity_pcu
    """), {
        "id":       camera_id,
        "name":     cam.get("name", camera_id),
        "lat":      cam.get("lat"),
        "lng":      cam.get("lng"),
        "capacity": capacity_for(camera_id),
    })


async def _save_snapshot(camera_id: str, result: dict) -> None:
    """Persist a detection result to traffic_snapshots (best-effort, non-blocking)."""
    if not DB_AVAILABLE or _AsyncSession is None:
        return
    try:
        async with _AsyncSession() as db:
            await _upsert_camera(db, camera_id)
            await db.execute(text("""
                INSERT INTO traffic_snapshots
                    (id, camera_id, snapshot_time, vehicle_count, pcu, saturation,
                     severity, fps_processed, is_incident)
                VALUES (:id, :camera_id, NOW(), :vehicle_count, :pcu, :saturation,
                        :severity, :fps, :is_incident)
            """), {
                "id":            str(uuid.uuid4()),
                "camera_id":     camera_id,
                "vehicle_count": result.get("vehicle_count", 0),
                "pcu":           result.get("pcu"),
                "saturation":    result.get("saturation"),
                "severity":      result.get("severity", "free"),
                "fps":           result.get("fps_processed", 0.0),
                "is_incident":   bool(result.get("possible_incident", False)),
            })
            await db.commit()
    except SQLAlchemyError as exc:
        # Narrow to database errors and log the traceback. A bare `except
        # Exception` here previously reduced a schema mismatch — which made
        # every write fail — to a single warning line that was easy to miss.
        logger.warning(
            "[db] Snapshot save failed for %s: %s", camera_id, exc, exc_info=True
        )


async def _save_bottleneck_event(camera_id: str, result: dict) -> None:
    """Persist a heavy/bottleneck event to BottleneckEvents (best-effort)."""
    if not DB_AVAILABLE or _AsyncSession is None:
        return
    try:
        cam = next((c for c in _active_cameras if c["camera_id"] == camera_id), {})
        async with _AsyncSession() as db:
            await _upsert_camera(db, camera_id)
            await db.execute(text("""
                INSERT INTO bottleneck_events
                    (id, camera_id, detected_at, severity, vehicle_count,
                     pcu, saturation, color, latitude, longitude)
                VALUES (:id, :camera_id, NOW(), :severity, :vehicle_count,
                        :pcu, :saturation, :color, :lat, :lng)
            """), {
                "id":            str(uuid.uuid4()),
                "camera_id":     camera_id,
                "severity":      result.get("severity"),
                "vehicle_count": result.get("vehicle_count", 0),
                "pcu":           result.get("pcu"),
                "saturation":    result.get("saturation"),
                "color":         result.get("color", "#23c55e"),
                "lat":           cam.get("lat"),
                "lng":           cam.get("lng"),
            })
            await db.commit()
    except SQLAlchemyError as exc:
        logger.warning(
            "[db] Bottleneck event save failed for %s: %s", camera_id, exc, exc_info=True
        )


async def _save_incident(inc) -> None:
    """Persist a confirmed incident so history survives a restart."""
    if not DB_AVAILABLE or _AsyncSession is None:
        return
    try:
        async with _AsyncSession() as db:
            await _upsert_camera(db, inc.camera_id)
            await db.execute(text("""
                INSERT INTO incidents
                    (id, camera_id, type, severity, confidence, vehicle_count,
                     detected_at, description)
                VALUES (:id, :camera_id, :type, :severity, :confidence,
                        :vehicle_count, :detected_at, :description)
                ON CONFLICT (id) DO NOTHING
            """), {
                "id":            inc.incident_id,
                "camera_id":     inc.camera_id,
                "type":          inc.type,
                "severity":      inc.severity,
                "confidence":    inc.confidence,
                "vehicle_count": inc.vehicle_count,
                # Incident.timestamp is an ISO-8601 *string*; detected_at is
                # TIMESTAMPTZ and asyncpg requires a real datetime, so parse it.
                # The other two save helpers use SQL NOW() and so were unaffected.
                "detected_at":   _as_datetime(inc.timestamp),
                "description":   inc.description,
            })
            await db.commit()
    except SQLAlchemyError as exc:
        logger.warning(
            "[db] Incident save failed for %s: %s", inc.camera_id, exc, exc_info=True
        )


async def _process_detection(camera_id: str, result: dict) -> None:
    """Post-detection hook: run incident detection, generate Claude summaries,
    and auto-trigger alerts.

    Called after every detection result is stored in ``latest_detections``.
    Incident detection runs unconditionally; Claude API respects a cooldown.
    """
    import time as _time

    # ── Database writes (non-blocking, best-effort) ────────────────────────
    asyncio.create_task(_save_snapshot(camera_id, result))
    if result.get("severity") in ("heavy", "bottleneck"):
        asyncio.create_task(_save_bottleneck_event(camera_id, result))

    # ── Incident detection (no external dependencies, always runs) ────────
    new_incidents = incident_detector.analyze(
        camera_id     = camera_id,
        vehicle_count = result["vehicle_count"],
        severity      = result["severity"],
        stall_verdict = result.get("incident_detail"),
    )
    if new_incidents:
        logger.info(
            "[%s] %d new incident(s) detected: %s",
            camera_id,
            len(new_incidents),
            ", ".join(i.type for i in new_incidents),
        )
        for inc in new_incidents:
            asyncio.create_task(_save_incident(inc))
            alert_log.append({
                "alert_id":      str(uuid.uuid4()),
                "camera_id":     camera_id,
                "severity":      inc.severity,
                "vehicle_count": inc.vehicle_count,
                "color":         inc.color,
                "timestamp":     inc.timestamp,
                "message":       inc.description,
                "summary":       inc.description,
                "source":        "incident_detector",
            })

    now = _time.monotonic()
    prev_time = _last_summary_time.get(camera_id, 0.0)
    prev_severity = _last_severity.get(camera_id, "free")
    raw_severity = result["severity"]

    # ── Debounce the raw severity ─────────────────────────────────────────
    # Only promote a new severity once it has held for _SEVERITY_DEBOUNCE
    # consecutive detections; a single jittery frame no longer counts as a
    # transition and no longer bypasses the cooldown below.
    if raw_severity == prev_severity:
        _pending_severity.pop(camera_id, None)
        severity_changed = False
        current_severity = prev_severity
    else:
        candidate, streak = _pending_severity.get(camera_id, (raw_severity, 0))
        streak = streak + 1 if candidate == raw_severity else 1
        if streak >= _SEVERITY_DEBOUNCE:
            _pending_severity.pop(camera_id, None)
            severity_changed = True
            current_severity = raw_severity
        else:
            _pending_severity[camera_id] = (raw_severity, streak)
            severity_changed = False
            current_severity = prev_severity   # not confirmed yet

    # Determine whether we should generate a new summary right now.  A
    # confirmed severity change shortens the wait but does not remove it.
    elapsed = now - prev_time
    if elapsed < _MIN_SUMMARY_INTERVAL:
        return  # too soon under any circumstances
    if not severity_changed and elapsed < _SUMMARY_COOLDOWN:
        return  # steady state — wait for the full cooldown

    # ── Skip the API when the summary would say the same thing ────────────
    # The generated text only varies with severity and (coarsely) vehicle
    # count, so an unchanged key means the cached summary is still accurate.
    # The clock is deliberately NOT reset here: letting `elapsed` keep growing
    # is what eventually forces the _SUMMARY_MAX_AGE refresh below, so a quiet
    # camera still gets its prose updated instead of being frozen forever.
    summary_key = (current_severity, result["vehicle_count"] // _COUNT_BUCKET)
    if (summary_key == _last_summary_key.get(camera_id)
            and not severity_changed
            and elapsed < _SUMMARY_MAX_AGE):
        return

    _last_severity[camera_id] = current_severity
    _last_summary_time[camera_id] = now
    _last_summary_key[camera_id] = summary_key

    # Generate the summary in the background (non-blocking).
    try:
        summary = await summary_service.generate_summary(result, _live_severity())
        logger.info(
            "[%s] Summary generated (source=%s): %.60s…",
            camera_id, summary.source, summary.summary,
        )
    except Exception as exc:
        logger.error("[%s] Summary generation error: %s", camera_id, exc)
        return

    # Auto-trigger alert on transition TO heavy or bottleneck.
    if severity_changed and current_severity in ("heavy", "bottleneck"):
        try:
            alert_summary = await summary_service.generate_alert_description(
                result, _live_severity())
            alert = {
                "alert_id":      str(uuid.uuid4()),
                "camera_id":     camera_id,
                "severity":      current_severity,
                "vehicle_count": result["vehicle_count"],
                "color":         result["color"],
                "timestamp":     datetime.now(timezone.utc).isoformat(),
                "message":       alert_summary.summary,
                "summary":       summary.summary,
                "source":        alert_summary.source,
            }
            alert_log.append(alert)
            logger.info(
                "Auto-alert created: %s camera=%s severity=%s",
                alert["alert_id"], camera_id, current_severity,
            )
        except Exception as exc:
            logger.error("[%s] Auto-alert creation error: %s", camera_id, exc)


async def _camera_loop(camera_id: str) -> None:
    """Mock-pipeline background task for one camera."""
    gen = run_mock_pipeline(camera_id=camera_id)
    logger.info("[%s] Mock detection task started.", camera_id)
    try:
        while True:
            try:
                result = await asyncio.to_thread(_safe_next, gen)
                if result is _GEN_DONE:
                    logger.info("[%s] Mock generator exhausted.", camera_id)
                    break
                latest_detections[camera_id] = result
                logger.debug("[%s] count=%d severity=%s",
                             camera_id, result["vehicle_count"], result["severity"])
                await _process_detection(camera_id, result)
            except Exception as exc:
                logger.error("[%s] Mock detection error: %s", camera_id, exc, exc_info=True)
            await asyncio.sleep(_POLL_INTERVAL)
    except asyncio.CancelledError:
        logger.info("[%s] Mock task cancelled.", camera_id)
        raise


_WATCHDOG_STALE_SECS  = 30   # restart camera if no new frame for this long
_MAX_HLS_ATTEMPTS     = 5    # outer retry limit before falling back to mock
_HLS_RETRY_SECS       = 60   # seconds between outer HLS restart attempts


async def _hls_camera_loop(
    camera_id: str,
    source: str,
    url_candidates: list[str] | None = None,
    startup_delay: float = 0.0,
) -> None:
    """
    Drive the HLS pipeline for one camera with persistent retry.

    Inner retry:  run_hls_pipeline retries MAX_RETRIES (3) times with 10 s delay.
    Outer retry:  on generator exhaustion this loop waits _HLS_RETRY_SECS (60 s)
                  and starts a fresh generator — up to _MAX_HLS_ATTEMPTS (5) times.
    After 5 outer failures the camera falls back to mock with an explicit warning,
    then retries HLS every _HLS_RETRY_SECS seconds indefinitely.

    url_candidates, if provided, are tried in order on each frame grab so that
    a single failing URL does not abort the entire detection cycle.

    startup_delay, if > 0, causes the task to sleep before its first HLS attempt
    so that multiple cameras do not hammer the Wowza server simultaneously.
    The delay lives here (inside the task) rather than in the lifespan startup
    phase so that a CancelledError during the sleep is handled gracefully and
    does not tear down the entire lifespan before it reaches ``yield``.
    """
    hls_urls = url_candidates or [source]
    if startup_delay > 0:
        logger.info(
            "[%s] HLS task staggered — waiting %.0fs before first attempt …",
            camera_id, startup_delay,
        )
        await asyncio.sleep(startup_delay)
    logger.info("[%s] HLS detection task started  urls=%s", camera_id, hls_urls)
    outer_attempt = 0

    try:
        while True:
            outer_attempt += 1
            logger.info(
                "[%s] HLS outer attempt %d/%d …",
                camera_id, outer_attempt, _MAX_HLS_ATTEMPTS,
            )
            gen = run_hls_pipeline(camera_id=camera_id, hls_urls=hls_urls)
            got_live_frame = False

            while True:
                try:
                    result = await asyncio.to_thread(_safe_next, gen)
                    if result is _GEN_DONE:
                        logger.warning("[%s] HLS generator exhausted.", camera_id)
                        break
                    latest_detections[camera_id] = result
                    _get_frame_event(camera_id).set()
                    if not got_live_frame:
                        logger.info("[%s] HLS stream live — real data flowing.", camera_id)
                        outer_attempt = 0   # reset on first successful frame
                    got_live_frame = True
                    logger.debug("[%s] count=%d severity=%s",
                                 camera_id, result["vehicle_count"], result["severity"])
                    await _process_detection(camera_id, result)
                except Exception as exc:
                    logger.error("[%s] HLS task error: %s", camera_id, exc, exc_info=True)
                    break

                # Watchdog
                last_ts = latest_detections.get(camera_id, {}).get("timestamp")
                if last_ts:
                    age = (datetime.now(timezone.utc) -
                           datetime.fromisoformat(last_ts)).total_seconds()
                    if age > _WATCHDOG_STALE_SECS:
                        logger.warning(
                            "[%s] Watchdog: no frame in %.0fs — restarting stream.",
                            camera_id, age,
                        )
                        break
                await asyncio.sleep(0)

            if outer_attempt < _MAX_HLS_ATTEMPTS:
                logger.warning(
                    "[%s] HLS attempt %d/%d failed — retrying in %ds …",
                    camera_id, outer_attempt, _MAX_HLS_ATTEMPTS, _HLS_RETRY_SECS,
                )
                await asyncio.sleep(_HLS_RETRY_SECS)
            else:
                logger.warning(
                    "WARNING: Camera %s using MOCK DATA — stream unavailable after %d attempts",
                    camera_id, _MAX_HLS_ATTEMPTS,
                )
                # Run mock temporarily while we keep trying HLS in background cadence
                mock_gen = __import__(
                    "detection.mock_pipeline", fromlist=["run_mock_pipeline"]
                ).run_mock_pipeline(camera_id=camera_id)
                mock_frames = 0
                while mock_frames < 20:   # ~60 s of mock (3 s/frame × 20), then retry HLS
                    try:
                        result = await asyncio.to_thread(_safe_next, mock_gen)
                        if result is _GEN_DONE:
                            break
                        latest_detections[camera_id] = result
                        await _process_detection(camera_id, result)
                        mock_frames += 1
                    except Exception:
                        break
                    await asyncio.sleep(_POLL_INTERVAL)

                logger.info("[%s] Retrying HLS after mock interlude …", camera_id)
                outer_attempt = 0   # reset so we get another full set of retries

    except asyncio.CancelledError:
        logger.info("[%s] HLS task cancelled.", camera_id)
        raise


async def _real_camera_loop(camera_id: str, source: str) -> None:

    logger.info("[%s] Real detection task started  source=%s", camera_id, source)
    try:
        gen = run_pipeline(camera_id=camera_id, source=source)
        while True:
            try:
                result = await asyncio.to_thread(_safe_next, gen)
                if result is _GEN_DONE:
                    logger.warning("[%s] Real stream ended — falling back to mock.", camera_id)
                    break
                latest_detections[camera_id] = result
                logger.debug("[%s] count=%d severity=%s",
                             camera_id, result["vehicle_count"], result["severity"])
                await _process_detection(camera_id, result)
            except Exception as exc:
                logger.error("[%s] Real stream error: %s — falling back to mock.",
                             camera_id, exc, exc_info=True)
                break
            await asyncio.sleep(0)          # yield the event loop between frames
    except asyncio.CancelledError:
        logger.info("[%s] Real camera task cancelled.", camera_id)
        raise

    # ── Fallback to mock once the real stream is gone ─────────────────────
    logger.info("[%s] Switching to mock pipeline fallback.", camera_id)
    await _camera_loop(camera_id)


@asynccontextmanager
async def lifespan(app: FastAPI):

    global _active_cameras

    # database.py's own "[db] Engine ready" log fires at import time, before
    # main.py's logging is configured — with no handler attached yet, Python's
    # last-resort handler (WARNING+ only) drops that INFO-level line silently.
    # Re-state the outcome here, now that logging is live, loud on both paths.
    if DB_AVAILABLE:
        logger.info("[db] Engine ready → %s:%d/%s", _DB_HOST, _DB_PORT, _DB_NAME)
    else:
        logger.warning(
            "[db] Not connected — starting without persistence "
            "(see the [db] warning above for why; check DB_HOST/DB_PORT/DB_USER/"
            "DB_PASSWORD/DB_NAME in .env)."
        )

    # Discovery runs in a thread — it is synchronous / blocking
    logger.info("Running Traffic Watch camera discovery …")
    cameras: list[dict] = await asyncio.to_thread(discover_cameras)

    # Log discovery outcome; keep HLS cameras even if ffprobe couldn't validate them —
    # _hls_camera_loop will retry persistently rather than silently falling to mock.
    if not cameras:
        logger.error("[lifespan] Discovery returned empty list — using mock fallback.")
        cameras = [
            {"camera_id": "caudan_north", "source": "mock", "lat": -20.1626, "lng": 57.4939,
             "name": "Caudan North — Port Louis",              "origin": "fallback", "validated": False},
            {"camera_id": "caudan_south", "source": "mock", "lat": -20.1640, "lng": 57.4945,
             "name": "Caudan South — Port Louis",              "origin": "fallback", "validated": False},
            {"camera_id": "la_chaussee",  "source": "mock", "lat": -20.1608, "lng": 57.4972,
             "name": "La Chaussee Street — Port Louis",        "origin": "fallback", "validated": False},
            {"camera_id": "casernes",     "source": "mock", "lat": -20.1590, "lng": 57.4960,
             "name": "Casernes / Brabant Street — Port Louis", "origin": "fallback", "validated": False},
        ]
    else:
        n_hls  = sum(1 for c in cameras if c["source"].endswith(".m3u8"))
        n_mock = sum(1 for c in cameras if c["source"] == "mock")
        n_val  = sum(1 for c in cameras if c.get("validated"))
        logger.info(
            "[lifespan] %d camera(s): %d HLS (%d validated by ffprobe), %d mock.",
            len(cameras), n_hls, n_val, n_mock,
        )
        if n_hls > 0 and n_val == 0:
            logger.warning(
                "[lifespan] No HLS streams validated — will attempt anyway and retry "
                "every %ds (up to %d times) before using mock data.",
                _HLS_RETRY_SECS, _MAX_HLS_ATTEMPTS,
            )

    _active_cameras = cameras

    tasks: list[asyncio.Task] = []
    for i, cam in enumerate(cameras):
        src = cam["source"]
        if src == "mock":
            coro = _camera_loop(cam["camera_id"])
        elif src.endswith(".m3u8"):
            # Stagger HLS cameras by 5 s each to avoid simultaneous network
            # hits on the Wowza server.  The delay is passed into the coroutine
            # so it sleeps INSIDE the task, not inside the lifespan startup phase
            # (sleeping here before yield risks a CancelledError from StatReload
            # or OneDrive aborting the entire lifespan before it reaches yield).
            coro = _hls_camera_loop(
                cam["camera_id"], src,
                cam.get("url_candidates"),
                startup_delay=float(5 * i),
            )
        else:
            coro = _real_camera_loop(cam["camera_id"], src)

        task = asyncio.create_task(coro, name=f"camera_{cam['camera_id']}")
        tasks.append(task)
        logger.info("Started task for camera: %s  source=%s", cam["camera_id"], src)

    yield  # application runs here

    logger.info("Shutting down detection tasks …")
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("All detection tasks stopped.")


app = FastAPI(title="AI Traffic Bottleneck Detection - Port Louis", lifespan=lifespan)

# Secret comes from SESSION_SECRET; it used to be a literal committed to git,
# which let anyone with the repo forge an admin session cookie.
app.add_middleware(
    SessionMiddleware,
    secret_key=get_session_secret(),
    https_only=os.getenv("SESSION_HTTPS_ONLY", "false").lower() == "true",
    same_site="lax",
)

# The "null" origin was previously allowed, which let any local file:// page or
# sandboxed iframe call the API. Origins are configurable for deployment.
_origins = [o for o in os.getenv(
    "CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000"
).split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")


if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


def _username(user) -> str:
    """Session values may be a dict (current) or a bare string (older cookies)."""
    return user.get("username") if isinstance(user, dict) else str(user)


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/map", status_code=302)
    return RedirectResponse(url="/login", status_code=302)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = None, success: str = None):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/map", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": error, "success": success})


@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/map", status_code=302)
    return templates.TemplateResponse("signup.html", {"request": request, "error": None})


@app.post("/signup", response_class=HTMLResponse)
async def signup_submit(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
):
    if len(username) < 3:
        return templates.TemplateResponse(
            "signup.html",
            {"request": request, "error": "Username must be at least 3 characters.",
             "username": username, "email": email},
            status_code=400,
        )
    if password != confirm_password:
        return templates.TemplateResponse(
            "signup.html",
            {"request": request, "error": "Passwords do not match.",
             "username": username, "email": email},
            status_code=400,
        )

    ok, message = await create_user(username, email, password)
    await record_audit(request, "signup", username, ok)
    if not ok:
        # Previously this path always reported success while writing nothing.
        return templates.TemplateResponse(
            "signup.html",
            {"request": request, "error": message, "username": username, "email": email},
            status_code=400,
        )
    return RedirectResponse(
        url=f"/login?success={quote_plus(message)}", status_code=302,
    )


@app.get("/recover-password", response_class=HTMLResponse)
async def recover_password_page(request: Request, error: str = None, success: str = None):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/map", status_code=302)
    return templates.TemplateResponse(
        "recoverypassword.html",
        {"request": request, "error": error, "success": success},
    )


@app.post("/recover-password", response_class=HTMLResponse)
async def recover_password_submit(request: Request, email: str = Form(...)):
    if not _is_valid_email(email.strip().lower()):
        return templates.TemplateResponse(
            "recoverypassword.html",
            {"request": request, "error": "Enter a valid email address."},
            status_code=400,
        )

    token = await create_password_reset_token(email)
    if not token:
        return templates.TemplateResponse(
            "recoverypassword.html",
            {"request": request, "error": "No account was found with that email. Please enter a valid email address."},
            status_code=400,
        )

    if not _smtp_configured():
        return templates.TemplateResponse(
            "recoverypassword.html",
            {"request": request, "error": "Email service is not configured. Contact the administrator."},
            status_code=500,
        )

    try:
        reset_link = str(request.url_for("reset_password_page")) + f"?token={quote_plus(token)}"
        _send_password_reset_email(email.strip().lower(), reset_link)
    except Exception as exc:
        logger.error("Password reset email send failed: %s", exc, exc_info=True)
        return templates.TemplateResponse(
            "recoverypassword.html",
            {"request": request, "error": "Unable to send reset email at this time."},
            status_code=500,
        )

    return templates.TemplateResponse(
        "recoverypassword.html",
        {"request": request, "success": "Account found. A reset link has been sent to your email!"},
    )


@app.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str = None, error: str = None):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/map", status_code=302)
    if token:
        valid_user = await get_password_reset_user(token)
        if not valid_user:
            return templates.TemplateResponse(
                "resetpassword.html",
                {"request": request, "error": "Invalid or expired password reset link.", "token": ""},
                status_code=400,
            )
    return templates.TemplateResponse(
        "resetpassword.html",
        {"request": request, "error": error, "success": None, "token": token or ""},
    )


@app.post("/reset-password", response_class=HTMLResponse)
async def reset_password_submit(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
):
    if password != confirm_password:
        return templates.TemplateResponse(
            "resetpassword.html",
            {"request": request, "error": "Passwords do not match.", "token": token},
            status_code=400,
        )
    ok, message = await reset_password(token, password)
    if not ok:
        return templates.TemplateResponse(
            "resetpassword.html",
            {"request": request, "error": message, "token": token},
            status_code=400,
        )
    return RedirectResponse(
        url=f"/login?success={quote_plus(message)}", status_code=302,
    )


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    user = await authenticate(username, password)
    await record_audit(request, "login", username, user is not None)
    if user:
        request.session["user"] = user
        return RedirectResponse(url="/map", status_code=302)
    return templates.TemplateResponse(
        "login.html",
        # Deliberately does not distinguish unknown user from wrong password —
        # that difference tells an attacker which usernames exist.
        {"request": request, "error": "Invalid username/email or password.", "username": username},
        status_code=401,
    )


@app.get("/map", response_class=HTMLResponse)
async def map_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "map.html", {"request": request, "user": _username(user)}
    )


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)



# In-memory alert ring buffer; newest entries appended at the end.
# Bounded because this was an unbounded list that grew for the process lifetime —
# a slow leak on any long-running deployment.
ALERT_LOG_MAX = 500
alert_log: deque[dict] = deque(maxlen=ALERT_LOG_MAX)


class AlertTriggerRequest(BaseModel):
    camera_id: str
    message: str = ""




@app.get("/api/traffic", response_class=JSONResponse, dependencies=[Depends(require_user)])
async def api_traffic_all():
    """Return the latest detection result for every active camera."""
    return latest_detections


@app.get("/api/traffic/{camera_id}", response_class=JSONResponse, dependencies=[Depends(require_user)])
async def api_traffic_camera(camera_id: str):
    """Return the latest detection for a specific camera, or 404 if unknown."""
    result = latest_detections.get(camera_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Camera '{camera_id}' not found.")
    return result


@app.get("/api/summary/{camera_id}", response_class=JSONResponse, dependencies=[Depends(require_user)])
async def api_summary(camera_id: str):
    """Return the Claude-generated traffic summary for a specific camera.

    Checks the in-memory cache first for an instant response.  If no cached
    summary exists, generates one on demand from the latest detection data.
    Returns 404 if the camera has no detection data at all.
    """
    # Try cache first for instant response.
    cached = summary_service.get_cached_summary(camera_id)
    if cached:
        return {
            "camera_id":     cached.camera_id,
            "summary":       cached.summary,
            "severity":      cached.severity,
            "vehicle_count": cached.vehicle_count,
            "timestamp":     cached.timestamp,
            "source":        cached.source,
        }

    # No cache — generate on demand from current detection data.
    detection = latest_detections.get(camera_id)
    if detection is None:
        raise HTTPException(
            status_code=404,
            detail=f"Camera '{camera_id}' not found or has no detection data yet.",
        )

    result = await summary_service.generate_summary(detection, _live_severity())
    return {
        "camera_id":     result.camera_id,
        "summary":       result.summary,
        "severity":      result.severity,
        "vehicle_count": result.vehicle_count,
        "timestamp":     result.timestamp,
        "source":        result.source,
    }


@app.get("/api/summaries", response_class=JSONResponse, dependencies=[Depends(require_user)])
async def api_summaries_all():
    """Return cached Claude summaries for all cameras that have one.

    Useful for the dashboard to fetch all summaries in a single call
    rather than polling each camera individually.
    """
    summaries = {}
    for camera_id in latest_detections:
        cached = summary_service.get_cached_summary(camera_id)
        if cached:
            summaries[camera_id] = {
                "summary":       cached.summary,
                "severity":      cached.severity,
                "vehicle_count": cached.vehicle_count,
                "timestamp":     cached.timestamp,
                "source":        cached.source,
            }
    return summaries


@app.get("/api/status", response_class=JSONResponse, dependencies=[Depends(require_user)])
async def api_status():
    """Return a high-level system status summary."""
    active_cameras = len(latest_detections)
    total_detections = sum(r["vehicle_count"] for r in latest_detections.values())
    bottlenecks = sum(
        1 for r in latest_detections.values() if r["severity"] == "bottleneck"
    )
    return {
        "active_cameras":   active_cameras,
        "total_detections": total_detections,
        "bottlenecks":      bottlenecks,
        "system":           "online",
    }




@app.post("/api/alerts/trigger", response_class=JSONResponse, status_code=201, dependencies=[Depends(require_user)])
async def api_alerts_trigger(body: AlertTriggerRequest):

    detection = latest_detections.get(body.camera_id)
    if detection is None:
        raise HTTPException(
            status_code=404,
            detail=f"Camera '{body.camera_id}' has no detection data yet.",
        )

    # Generate a Claude summary for the alert (falls back to template).
    ai_summary = await summary_service.generate_alert_description(
        detection, _live_severity())

    alert = {
        "alert_id":      str(uuid.uuid4()),
        "camera_id":     body.camera_id,
        "severity":      detection["severity"],
        "vehicle_count": detection["vehicle_count"],
        "color":         detection["color"],
        "timestamp":     datetime.now(timezone.utc).isoformat(),
        "message":       body.message or ai_summary.summary,
        "summary":       ai_summary.summary,
        "source":        ai_summary.source,
    }
    alert_log.append(alert)
    logger.info("Alert created: %s  camera=%s severity=%s", alert["alert_id"], alert["camera_id"], alert["severity"])
    return alert


@app.get("/api/alerts", response_class=JSONResponse, dependencies=[Depends(require_user)])
async def api_alerts_list(limit: int = 50):
    """
    Return the most recent *limit* alerts from the in-memory log (default 50),
    newest first.
    """
    return list(alert_log)[-limit:][::-1]


@app.get("/api/cameras", response_class=JSONResponse, dependencies=[Depends(require_user)])
async def api_cameras(include_catalogue: bool = True):
    """Cameras with live detection state, plus the rest of the MYT catalogue.

    MYT publishes 38 cameras island-wide but only the ACTIVE_CAMERAS subset has
    detection running — each one costs an ffmpeg subprocess and a YOLO
    inference, which CPU-only inference cannot sustain across all of them. The
    remainder are returned with ``monitored: false`` so the map can show the
    full network without implying data exists for every marker.
    """
    result = []
    for cam in _active_cameras:
        src = cam["source"]
        if src == "mock":
            stream_type = "mock"
        elif src.endswith(".m3u8"):
            stream_type = "hls"
        else:
            stream_type = "rtsp"

        det = latest_detections.get(cam["camera_id"], {})
        result.append({
            "camera_id":        cam["camera_id"],
            "name":             cam.get("name", cam["camera_id"]),
            "lat":              cam.get("lat"),
            "lng":              cam.get("lng"),
            "source":           src,
            "stream_base":      cam.get("stream_base", ""),
            "stream_type":      stream_type,
            "validated":        cam.get("validated", False),
            "current_severity": det.get("severity"),
            "current_count":    det.get("vehicle_count"),
            # Per-direction breakdown; a camera that has not been calibrated
            # reports a single "combined" entry. See detection/direction.py.
            "directions":       det.get("directions", {}),
            "is_two_way":       direction.is_two_way(cam["camera_id"]),
            "is_mock":          src == "mock",
            "monitored":        True,
            "region":           cam.get("region", ""),
            "coords_precision": cam.get("coords_precision", "exact"),
        })

    if include_catalogue:
        live = {c["camera_id"] for c in _active_cameras}
        for cam in camera_catalogue.CAMERAS:
            if cam["camera_id"] in live:
                continue
            result.append({
                "camera_id":        cam["camera_id"],
                "name":             cam["name"],
                "lat":              cam["lat"],
                "lng":              cam["lng"],
                "source":           cam["source"],
                "stream_base":      "",
                "stream_type":      "hls",
                "validated":        False,
                "current_severity": None,
                "current_count":    None,
                "directions":       {},
                "is_two_way":       False,
                "is_mock":          False,
                # Catalogued but not processed — no detection data exists.
                "monitored":        False,
                "region":           cam["region"],
                "coords_precision": cam["coords_precision"],
            })
    return result


@app.get("/api/cameras/{camera_id}/frame.jpg", dependencies=[Depends(require_user)])
async def api_camera_frame(camera_id: str):
    """Latest annotated JPEG frame for a live camera, boxes already drawn.

    Behind the same auth as every other /api/* route — the original audit's
    headline finding was unauthenticated API routes, and camera footage is
    exactly the kind of thing that should not be reintroduced as one.

    Mock cameras and anything not running the HLS pipeline never have a
    stored frame, so this 404s for them rather than serving nothing useful.
    """
    stored = get_latest_frame(camera_id)
    if stored is None:
        raise HTTPException(
            status_code=404,
            detail=f"No frame captured yet for camera '{camera_id}'.",
        )
    jpeg_bytes, captured_at = stored
    age_seconds = (datetime.now(timezone.utc) - captured_at).total_seconds()
    return Response(
        content=jpeg_bytes,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "no-store",
            "X-Frame-Age-Seconds": f"{age_seconds:.1f}",
        },
    )


_MJPEG_BOUNDARY = "frame"


async def _mjpeg_frames(camera_id: str):
    """Yield multipart/x-mixed-replace parts as new frames become available.

    A new annotated frame only exists once per detection cycle
    (FRAME_INTERVAL, ~2s) - this doesn't create data faster than that. What it
    removes is everything *around* the data: the per-frame HTTP request/
    response round trip, the JS fetch-then-blob-then-swap sequence, and the
    brief flash back to a placeholder between polls that made the polled
    version (GET .../frame.jpg on a client timer) look like a slideshow.
    A plain <img src="this URL"> renders a standard MJPEG stream natively -
    no client-side polling code at all - so each new frame lands the moment
    it exists rather than up to one poll interval later.

    Waits on the camera's frame-ready Event rather than polling
    _latest_frames on a timer - one popup open no longer costs five
    dict lookups a second for nothing. The check-clear-recheck-wait
    sequence below is the standard safe pattern for a manually-reset
    Event: it avoids the lost-wakeup race where set() lands between an
    earlier check and the wait() call, because get_latest_frame() (the
    real data) is always the source of truth, never the Event's flag by
    itself.
    """
    event = _get_frame_event(camera_id)
    last_sent_at = None
    try:
        while True:
            stored = get_latest_frame(camera_id)
            if stored is not None:
                jpeg_bytes, captured_at = stored
                if captured_at != last_sent_at:
                    last_sent_at = captured_at
                    yield (
                        f"--{_MJPEG_BOUNDARY}\r\n"
                        f"Content-Type: image/jpeg\r\n"
                        f"Content-Length: {len(jpeg_bytes)}\r\n\r\n"
                    ).encode() + jpeg_bytes + b"\r\n"
                    continue   # a frame may already be queued up behind this one

            event.clear()
            stored = get_latest_frame(camera_id)
            if stored is not None and stored[1] != last_sent_at:
                continue   # landed between the check above and clear() - don't wait for it
            await event.wait()
    except asyncio.CancelledError:
        # Normal shutdown path - the client closed the <img> connection
        # (popup closed, tab navigated away). Nothing to clean up: this
        # generator holds no resources beyond its own local variables.
        return


@app.get("/api/cameras/{camera_id}/stream.mjpg", dependencies=[Depends(require_user)])
async def api_camera_stream(camera_id: str):
    """Live MJPEG stream of the annotated frame - boxes already drawn.

    Same auth, same data source as frame.jpg. Point a plain <img> tag at this
    URL and the browser handles continuous replacement on its own; no JS
    polling loop needed. 404s up front if the camera has never produced a
    frame, matching frame.jpg, rather than opening a stream that would never
    send anything.
    """
    if get_latest_frame(camera_id) is None:
        raise HTTPException(
            status_code=404,
            detail=f"No frame captured yet for camera '{camera_id}'.",
        )
    return StreamingResponse(
        _mjpeg_frames(camera_id),
        media_type=f"multipart/x-mixed-replace; boundary={_MJPEG_BOUNDARY}",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/incidents", response_class=JSONResponse, dependencies=[Depends(require_user)])
async def api_incidents_all(limit: int = 100):
    """Return all incidents (resolved + active), newest first."""
    return incident_detector.get_all_incidents(limit=limit)


@app.get("/api/incidents/active", response_class=JSONResponse, dependencies=[Depends(require_user)])
async def api_incidents_active():
    """Return all currently unresolved incidents across all cameras."""
    return incident_detector.get_active_incidents()


@app.get("/api/incidents/{camera_id}", response_class=JSONResponse, dependencies=[Depends(require_user)])
async def api_incidents_camera(camera_id: str):
    """Return all incidents for a specific camera, newest first."""
    if camera_id not in latest_detections:
        raise HTTPException(status_code=404, detail=f"Camera '{camera_id}' not found.")
    return incident_detector.get_incidents_by_camera(camera_id)


@app.post("/api/demo/escalate", response_class=JSONResponse, dependencies=[Depends(require_admin)])
async def api_demo_escalate():
    """Override caudan_north to bottleneck severity for demo purposes."""
    demo_result = {
        "camera_id":     "caudan_north",
        "timestamp":     datetime.now(timezone.utc).isoformat(),
        "vehicle_count": 38,
        "severity":      "bottleneck",
        "color":         "#8b31c7",
        "fps_processed": 1.0,
        "frame_shape":   [720, 1280],
    }
    latest_detections["caudan_north"] = demo_result
    await _process_detection("caudan_north", demo_result)
    return {"ok": True, "message": "Demo bottleneck triggered on caudan_north"}


@app.post("/api/demo/stall", response_class=JSONResponse, dependencies=[Depends(require_admin)])
async def api_demo_stall(camera_id: str = "caudan_north", frames: int = 80, vehicles: int = 4):
    """Replay a synthetic held-still vehicle sequence through the real
    StationaryTracker and the real incident pipeline (this process's live
    ``incident_detector``, ``_save_incident``, the ``incidents`` table, and
    /api/incidents/active) - proves the stalled-vehicle chain end-to-end
    without waiting for a real stall. Diagnostic harness only; see
    tools/incident_harness.py, which drives this endpoint.

    The boxes are static and non-overlapping (150px apart) so IoU-based
    frame-to-frame association is unambiguous - this is testing the incident
    pipeline's wiring, not the tracker's association logic.
    """
    tracker = StationaryTracker(frame_interval=FRAME_INTERVAL)
    boxes   = [[100.0 + i * 150, 300.0, 220.0 + i * 150, 420.0] for i in range(vehicles)]
    classes = [2] * vehicles  # COCO class 2 = car

    confirmed_at: Optional[int] = None
    incident_ids: list[str] = []
    verdict = None
    for i in range(frames):
        verdict = tracker.update(boxes, classes)
        new = incident_detector.analyze(
            camera_id     = camera_id,
            vehicle_count = vehicles,
            severity      = "moderate",
            stall_verdict = verdict.to_dict(),
        )
        if new and confirmed_at is None:
            confirmed_at = i + 1
            incident_ids = [inc.incident_id for inc in new]
            for inc in new:
                await _save_incident(inc)   # awaited, not backgrounded: caller needs the DB row to exist on return

    return {
        "camera_id":          camera_id,
        "frames_replayed":    frames,
        "frame_interval_s":   tracker.frame_interval,
        "frames_required":    tracker._frames_required,
        "confirmed_at_frame": confirmed_at,
        "incident_ids":       incident_ids,
        "final_verdict":      verdict.to_dict() if verdict else None,
    }


@app.get("/api/analytics/hourly", response_class=JSONResponse,
         dependencies=[Depends(require_user)])
async def api_analytics_hourly(hours: int = 24):
    """Hourly mean vehicle count and saturation per camera, from real snapshots.

    The analytics page previously generated its own numbers client-side from a
    Gaussian rush-hour curve plus Math.random(), so the charts changed on every
    reload and reflected nothing that was measured. This returns what was
    actually recorded, and reports `available: false` when there is no data
    rather than inventing some.
    """
    hours = max(1, min(hours, 168))          # clamp: 1 hour to 1 week
    empty = {
        "available": False,
        "hours": hours,
        "cameras": [],
        "series": [],
        "severity_counts": {},
        "sample_count": 0,
        "reason": "",
    }

    if not DB_AVAILABLE or _AsyncSession is None:
        empty["reason"] = (
            "No database connected, so no history has been recorded. "
            "Start PostgreSQL and run run_schema.py to collect analytics."
        )
        return empty

    try:
        async with _AsyncSession() as db:
            rows = (await db.execute(text("""
                SELECT camera_id,
                       EXTRACT(HOUR FROM snapshot_time)::int AS hour,
                       AVG(vehicle_count)::float             AS avg_count,
                       AVG(saturation)::float                AS avg_saturation,
                       MAX(vehicle_count)                    AS peak_count,
                       COUNT(*)                              AS samples,
                       COUNT(*) FILTER (WHERE is_incident)   AS stall_samples
                  FROM traffic_snapshots
                 WHERE snapshot_time >= NOW() - make_interval(hours => :h)
                 GROUP BY camera_id, hour
                 ORDER BY camera_id, hour
            """), {"h": hours})).mappings().all()

            sev = (await db.execute(text("""
                SELECT severity, COUNT(*) AS n
                  FROM traffic_snapshots
                 WHERE snapshot_time >= NOW() - make_interval(hours => :h)
                 GROUP BY severity
            """), {"h": hours})).mappings().all()
    except SQLAlchemyError as exc:
        logger.warning("[db] Analytics query failed: %s", exc, exc_info=True)
        empty["reason"] = "Analytics query failed — see server logs."
        return empty

    if not rows:
        empty["reason"] = (
            f"No snapshots recorded in the last {hours}h. Let the detector run, "
            "then reload."
        )
        return empty

    return {
        "available":       True,
        "hours":           hours,
        "cameras":         sorted({r["camera_id"] for r in rows}),
        "series":          [dict(r) for r in rows],
        "severity_counts": {r["severity"]: r["n"] for r in sev},
        "sample_count":    sum(r["samples"] for r in rows),
        "reason":          "",
    }


@app.websocket("/ws/detections")
async def ws_detections(websocket: WebSocket):
    # The socket carries the same live data as the REST API, so it needs the
    # same authentication — it was previously open to anyone.
    if not await websocket_user(websocket):
        await websocket.close(code=1008, reason="Authentication required")
        return

    await websocket.accept()
    connected_clients.add(websocket)
    last_payload: str | None = None
    try:
        while True:
            payload = json.dumps(latest_detections, sort_keys=True)
            # Push only when something actually changed. The previous loop
            # re-sent the entire state every second to every client whether or
            # not it differed, which is also what forced the map popup to be
            # rebuilt continuously.
            if payload != last_payload:
                await websocket.send_text(payload)
                last_payload = payload
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected.")
    except (RuntimeError, ConnectionError) as exc:
        logger.debug("WebSocket closed: %s", exc)
    finally:
        connected_clients.discard(websocket)


@app.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(
        "analytics.html", {"request": request, "user": _username(user)}
    )


if __name__ == "__main__":
    # Serve over HTTPS when a certificate pair is present.
    ssl_args = {}
    if os.path.exists("cert.pem") and os.path.exists("key.pem"):
        ssl_args = {"ssl_certfile": "cert.pem", "ssl_keyfile": "key.pem"}

    # reload=False is intentional — StatReload + OneDrive causes spurious
    # reloads that cancel the lifespan mid-startup.  Use the explicit
    # uvicorn CLI if you need hot-reload during development:
    #   python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
    # For demos and normal runs, just do:
    #   python main.py        (or)
    #   python -m uvicorn main:app --host 0.0.0.0 --port 8000
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False, **ssl_args)
