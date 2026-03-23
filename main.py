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
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
import uvicorn
import os

from detection.mock_pipeline import run_mock_pipeline

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ── Shared detection state ────────────────────────────────────────────────────
# Keyed by camera_id; updated in-place by background tasks.
latest_detections: dict[str, dict] = {}

# Demo cameras that use the mock pipeline at startup
_DEMO_CAMERAS = [
    {"camera_id": "port_louis", "source": "mock"},
    {"camera_id": "grand_baie", "source": "mock"},
]

# Interval (seconds) between successive reads from the mock generator.
# The generator itself handles its own internal tick pacing.
_POLL_INTERVAL = 3.0


async def _camera_loop(camera_id: str) -> None:
    """
    Async background task that drives the mock pipeline generator and
    writes each new result into *latest_detections* every _POLL_INTERVAL
    seconds.  Runs until the task is cancelled (app shutdown).
    """
    gen = run_mock_pipeline(camera_id=camera_id)
    logger.info("[%s] Background detection task started.", camera_id)
    try:
        while True:
            # Advance the synchronous generator one step in a thread so the
            # event loop is never blocked by the generator's internal sleep.
            try:
                result: dict = await asyncio.to_thread(next, gen)
                latest_detections[camera_id] = result
                logger.debug(
                    "[%s] count=%d severity=%s",
                    camera_id, result["vehicle_count"], result["severity"],
                )
            except StopIteration:
                logger.info("[%s] Mock generator exhausted.", camera_id)
                break
            except Exception as exc:
                logger.error("[%s] Detection error: %s", camera_id, exc, exc_info=True)

            await asyncio.sleep(_POLL_INTERVAL)
    except asyncio.CancelledError:
        logger.info("[%s] Background detection task cancelled.", camera_id)
        raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background camera tasks on startup; cancel them on shutdown."""
    tasks: list[asyncio.Task] = []
    for cam in _DEMO_CAMERAS:
        task = asyncio.create_task(
            _camera_loop(cam["camera_id"]),
            name=f"camera_{cam['camera_id']}",
        )
        tasks.append(task)
        logger.info("Started detection task for camera: %s", cam["camera_id"])

    yield  # application runs here

    # ── Shutdown ──────────────────────────────────────────────────────────
    logger.info("Shutting down detection tasks…")
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("All detection tasks stopped.")


app = FastAPI(title="AI Traffic Bottleneck Detection - Mauritius", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key="traffic-mauritius-secret-key-2026")

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


# ── Alert store ──────────────────────────────────────────────────────────────
# In-memory list of alert dicts; newest entries appended at the end.
alert_log: list[dict] = []


class AlertTriggerRequest(BaseModel):
    camera_id: str
    message: str = ""


# ── Traffic API endpoints ─────────────────────────────────────────────────────

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


# ── Alert endpoints ───────────────────────────────────────────────────────────

@app.post("/api/alerts/trigger", response_class=JSONResponse, status_code=201)
async def api_alerts_trigger(body: AlertTriggerRequest):
    """
    Manually trigger an alert for a camera.
    Looks up the current detection state for *camera_id* and appends a new
    entry to *alert_log*.  Returns 404 if the camera has no data yet.
    """
    detection = latest_detections.get(body.camera_id)
    if detection is None:
        raise HTTPException(
            status_code=404,
            detail=f"Camera '{body.camera_id}' has no detection data yet.",
        )

    alert = {
        "alert_id":      str(uuid.uuid4()),
        "camera_id":     body.camera_id,
        "severity":      detection["severity"],
        "vehicle_count": detection["vehicle_count"],
        "color":         detection["color"],
        "timestamp":     datetime.now(timezone.utc).isoformat(),
        "message":       body.message or f"Alert triggered for {body.camera_id}",
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


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
