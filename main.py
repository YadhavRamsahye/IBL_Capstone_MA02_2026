from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request, Form, Depends
from pydantic import BaseModel
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
import uvicorn
import os
from dotenv import load_dotenv

# Load environment variables from .env file (if present) before any
# module tries to read ANTHROPIC_API_KEY or other secrets.
load_dotenv()

from detection.mock_pipeline import run_mock_pipeline
from detection.pipeline import run_pipeline
from detection.hls_pipeline import run_hls_pipeline
from detection.trafficwatch import discover_cameras
from detection.claude_api import summary_service

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# Keyed by camera_id; updated in-place by background tasks.
latest_detections: dict[str, dict] = {}

# Populated by lifespan() after discovery; read by /api/cameras.
_active_cameras: list[dict] = []

# Interval (seconds) between successive reads from the mock generator.
_POLL_INTERVAL = 3.0

# Minimum seconds between Claude API summary requests per camera.
# Prevents flooding the API on every frame (mock yields ~7.5 fps).
_SUMMARY_COOLDOWN = 30.0

# Track the last severity per camera so we only generate alerts on
# severity *transitions* (e.g., moderate -> heavy), not every frame.
_last_severity: dict[str, str] = {}

# Track the last summary generation time per camera for cooldown.
_last_summary_time: dict[str, float] = {}


async def _process_detection(camera_id: str, result: dict) -> None:
    """Post-detection hook: generate Claude summaries and auto-trigger alerts.

    Called after every detection result is stored in ``latest_detections``.
    Respects cooldown to avoid excessive API calls.
    """
    import time as _time

    now = _time.monotonic()
    prev_time = _last_summary_time.get(camera_id, 0.0)
    prev_severity = _last_severity.get(camera_id, "free")
    current_severity = result["severity"]

    # Determine whether we should generate a new summary right now.
    cooldown_elapsed = (now - prev_time) >= _SUMMARY_COOLDOWN
    severity_changed = current_severity != prev_severity

    if not cooldown_elapsed and not severity_changed:
        return  # skip — too soon and nothing changed

    _last_severity[camera_id] = current_severity
    _last_summary_time[camera_id] = now

    # Generate the summary in the background (non-blocking).
    try:
        summary = await summary_service.generate_summary(result)
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
            alert_summary = await summary_service.generate_alert_description(result)
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
                result: dict = await asyncio.to_thread(next, gen)
                latest_detections[camera_id] = result
                logger.debug("[%s] count=%d severity=%s",
                             camera_id, result["vehicle_count"], result["severity"])
                await _process_detection(camera_id, result)
            except StopIteration:
                logger.info("[%s] Mock generator exhausted.", camera_id)
                break
            except Exception as exc:
                logger.error("[%s] Mock detection error: %s", camera_id, exc, exc_info=True)
            await asyncio.sleep(_POLL_INTERVAL)
    except asyncio.CancelledError:
        logger.info("[%s] Mock task cancelled.", camera_id)
        raise


_WATCHDOG_STALE_SECS = 30   # restart camera if no new frame for this long


async def _hls_camera_loop(camera_id: str, source: str) -> None:
    """
    Background task for HLS (.m3u8) streams via ffmpeg subprocess.
    On StopIteration or any failure the HLS pipeline itself chains into
    mock — this coroutine just drives the generator.

    Includes a watchdog (fix 4): if latest_detections[camera_id] has not
    been updated for _WATCHDOG_STALE_SECS, the current generator is
    discarded and a fresh one is started.
    """
    logger.info("[%s] HLS detection task started  url=%s", camera_id, source)
    try:
        while True:                     # watchdog restart loop
            gen = run_hls_pipeline(camera_id=camera_id, hls_url=source)
            restarted_by_watchdog = False

            while True:
                try:
                    result: dict = await asyncio.to_thread(next, gen)
                    latest_detections[camera_id] = result
                    logger.debug("[%s] count=%d severity=%s",
                                 camera_id, result["vehicle_count"], result["severity"])
                    await _process_detection(camera_id, result)
                except StopIteration:
                    logger.info("[%s] HLS generator exhausted.", camera_id)
                    return              # pipeline fell back to mock; let it run to end
                except Exception as exc:
                    logger.error("[%s] HLS task error: %s", camera_id, exc, exc_info=True)
                    break               # inner break → retry via watchdog loop

                # ── Watchdog check (fix 4) ────────────────────────────────
                last_ts = latest_detections.get(camera_id, {}).get("timestamp")
                if last_ts:
                    age = (datetime.now(timezone.utc) -
                           datetime.fromisoformat(last_ts)).total_seconds()
                    if age > _WATCHDOG_STALE_SECS:
                        logger.warning(
                            "[%s] Watchdog: no update in %.0fs — restarting stream.",
                            camera_id, age,
                        )
                        restarted_by_watchdog = True
                        break           # break inner loop → new generator

                await asyncio.sleep(0)  # yield event loop between frames

            if not restarted_by_watchdog:
                # Non-watchdog exit (e.g. total failure already handled by
                # the pipeline's own mock fallback) — stop the outer loop.
                break

    except asyncio.CancelledError:
        logger.info("[%s] HLS task cancelled.", camera_id)
        raise


async def _real_camera_loop(camera_id: str, source: str) -> None:
    """
    Real-pipeline background task for one camera (RTSP / HTTP stream).
    On StopIteration or any unrecoverable stream error, automatically
    falls back to the mock pipeline for that camera.
    """
    logger.info("[%s] Real detection task started  source=%s", camera_id, source)
    try:
        gen = run_pipeline(camera_id=camera_id, source=source)
        while True:
            try:
                result: dict = await asyncio.to_thread(next, gen)
                latest_detections[camera_id] = result
                logger.debug("[%s] count=%d severity=%s",
                             camera_id, result["vehicle_count"], result["severity"])
                await _process_detection(camera_id, result)
            except StopIteration:
                logger.warning("[%s] Real stream ended — falling back to mock.", camera_id)
                break
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
    """
    Startup: run Traffic Watch discovery, start one background task per
    camera (real pipeline or mock).  Shutdown: cancel all tasks cleanly.
    """
    global _active_cameras

    # Discovery runs in a thread — it is synchronous / blocking
    logger.info("Running Traffic Watch camera discovery …")
    cameras: list[dict] = await asyncio.to_thread(discover_cameras)

    # Fall back to mock if nothing real was found
    if not cameras or all(c["source"] == "mock" for c in cameras):
        logger.warning("No live Traffic Watch streams found — using mock pipeline for demo.")
        cameras = [
            {"camera_id": "port_louis", "source": "mock",
             "name": "Port Louis (mock)", "origin": "fallback", "validated": False},
            {"camera_id": "grand_baie", "source": "mock",
             "name": "Grand Baie (mock)", "origin": "fallback", "validated": False},
        ]

    _active_cameras = cameras

    tasks: list[asyncio.Task] = []
    for i, cam in enumerate(cameras):
        src = cam["source"]
        if src == "mock":
            coro = _camera_loop(cam["camera_id"])
        elif src.endswith(".m3u8"):
            coro = _hls_camera_loop(cam["camera_id"], src)
        else:
            coro = _real_camera_loop(cam["camera_id"], src)

        # Stagger HLS camera startup by 5 s each to avoid simultaneous
        # network hits on the Wowza server (fix 2).
        if src.endswith(".m3u8") and i > 0:
            logger.info(
                "Staggering camera %s startup by %ds …",
                cam["camera_id"], 5 * i,
            )
            await asyncio.sleep(5)

        task = asyncio.create_task(coro, name=f"camera_{cam['camera_id']}")
        tasks.append(task)
        logger.info("Started task for camera: %s  source=%s", cam["camera_id"], src)

    yield  # application runs here

    logger.info("Shutting down detection tasks …")
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("All detection tasks stopped.")


app = FastAPI(title="AI Traffic Bottleneck Detection - Mauritius", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key="traffic-mauritius-secret-key-2026")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")


if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")


DEMO_USERS = {
    "admin": "admin123",
    "user": "password",
}


def get_current_user(request: Request):
    return request.session.get("user")


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/map", status_code=302)
    return RedirectResponse(url="/login", status_code=302)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = None):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/map", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request, "error": error})


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if username in DEMO_USERS and DEMO_USERS[username] == password:
        request.session["user"] = username
        return RedirectResponse(url="/map", status_code=302)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": "Invalid username or password."},
        status_code=401,
    )


@app.get("/map", response_class=HTMLResponse)
async def map_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("map.html", {"request": request, "user": user})


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)



# In-memory list of alert dicts; newest entries appended at the end.
alert_log: list[dict] = []


class AlertTriggerRequest(BaseModel):
    camera_id: str
    message: str = ""




@app.get("/api/traffic", response_class=JSONResponse)
async def api_traffic_all():
    """Return the latest detection result for every active camera."""
    return latest_detections


@app.get("/api/traffic/{camera_id}", response_class=JSONResponse)
async def api_traffic_camera(camera_id: str):
    """Return the latest detection for a specific camera, or 404 if unknown."""
    result = latest_detections.get(camera_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Camera '{camera_id}' not found.")
    return result


@app.get("/api/summary/{camera_id}", response_class=JSONResponse)
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

    result = await summary_service.generate_summary(detection)
    return {
        "camera_id":     result.camera_id,
        "summary":       result.summary,
        "severity":      result.severity,
        "vehicle_count": result.vehicle_count,
        "timestamp":     result.timestamp,
        "source":        result.source,
    }


@app.get("/api/summaries", response_class=JSONResponse)
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


@app.get("/api/status", response_class=JSONResponse)
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




@app.post("/api/alerts/trigger", response_class=JSONResponse, status_code=201)
async def api_alerts_trigger(body: AlertTriggerRequest):
    """
    Manually trigger an alert for a camera.
    Looks up the current detection state for *camera_id*, generates a
    Claude API summary (with template fallback), and appends a new
    entry to *alert_log*.  Returns 404 if the camera has no data yet.
    """
    detection = latest_detections.get(body.camera_id)
    if detection is None:
        raise HTTPException(
            status_code=404,
            detail=f"Camera '{body.camera_id}' has no detection data yet.",
        )

    # Generate a Claude summary for the alert (falls back to template).
    ai_summary = await summary_service.generate_alert_description(detection)

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


@app.get("/api/alerts", response_class=JSONResponse)
async def api_alerts_list(limit: int = 50):
    """
    Return the most recent *limit* alerts from the in-memory log (default 50),
    newest first.
    """
    return alert_log[-limit:][::-1]


@app.get("/api/cameras", response_class=JSONResponse)
async def api_cameras():
    """Return the discovered camera list enriched with live detection state."""
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
            "source":           src,
            "stream_base":      cam.get("stream_base", ""),
            "stream_type":      stream_type,
            "validated":        cam.get("validated", False),
            "current_severity": det.get("severity"),
            "current_count":    det.get("vehicle_count"),
            "is_mock":          src == "mock",
        })
    return result


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
