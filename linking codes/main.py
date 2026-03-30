from __future__ import annotations
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from fastapi import Depends
from database import get_db, verify_password, AsyncSessionLocal

import json
import uuid as uuid_module
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

from detection.mock_pipeline import run_mock_pipeline
from detection.pipeline import run_pipeline
from detection.hls_pipeline import run_hls_pipeline
from detection.trafficwatch import discover_cameras

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# Keyed by camera_id; updated in-place by background tasks.
latest_detections: dict[str, dict] = {}

# Populated by lifespan() after discovery; read by /api/cameras.
_active_cameras: list[dict] = []

# Interval (seconds) between successive reads from the mock generator.
_POLL_INTERVAL = 3.0

async def _save_snapshot(camera_id: str, result: dict) -> None:
    """Save every detection result to TrafficSnapshots table."""
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("""
                INSERT INTO "TrafficSnapshots"
                    (id, "CameraId", "SnapshotTime", "VehicleCount", "Severity", "fpsProcessed", "RawResult")
                VALUES
                    (:id, :camera_id, NOW(), :vehicle_count, :severity, :fps, :raw)
            """), {
                "id":            str(uuid.uuid4()),
                "camera_id":     camera_id,
                "vehicle_count": result.get("vehicle_count", 0),
                "severity":      result.get("severity",      "free"),
                "fps":           result.get("fps_processed", 0.0),
                "raw":           json.dumps(result),
            })
            await db.commit()
    except Exception as e:
        logger.error("Failed to save snapshot for %s: %s", camera_id, e)
        
async def _camera_loop(camera_id: str) -> None:
    """Mock-pipeline background task for one camera."""
    gen = run_mock_pipeline(camera_id=camera_id)
    logger.info("[%s] Mock detection task started.", camera_id)
    try:
        while True:
            try:
                result: dict = await asyncio.to_thread(next, gen)
                latest_detections[camera_id] = result
                asyncio.create_task(_save_snapshot(camera_id, result))
                logger.debug("[%s] count=%d severity=%s",
                             camera_id, result["vehicle_count"], result["severity"])
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
                    asyncio.create_task(_save_snapshot(camera_id, result))
                    logger.debug("[%s] count=%d severity=%s",
                                 camera_id, result["vehicle_count"], result["severity"])
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
                asyncio.create_task(_save_snapshot(camera_id, result))
                logger.debug("[%s] count=%d severity=%s",
                             camera_id, result["vehicle_count"], result["severity"])
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

# Save discovered cameras to the database
    try:
        async with AsyncSessionLocal() as db:
            for cam in cameras:
                await db.execute(text("""
                    INSERT INTO cameras (id, name, "StreamUrl", "isActive")
                    VALUES (:id, :name, :stream, TRUE)
                    ON CONFLICT (id) DO UPDATE
                    SET name = EXCLUDED.name,
                        "StreamUrl" = EXCLUDED."StreamUrl",
                        "LastSeen" = NOW()
                """), {
                    "id":     cam["camera_id"],
                    "name":   cam["name"],
                    "stream": cam["source"],
                })
            await db.commit()
            logger.info("Cameras saved to database.")
    except Exception as e:
        logger.error("Failed to save cameras to DB: %s", e)
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
    allow_origins=["*"],
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

@app.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    # Look up the user in the database
    result = await db.execute(
        text('SELECT id, username, "PasswordHash", role FROM users WHERE username = :username'),
        {"username": username}
    )
    user = result.fetchone()

    # Check user exists and password matches
    if not user or not verify_password(password, user[2]):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid username or password."},
            status_code=401,
        )

    # Save user info in session
    request.session["user"]     = user.username
    request.session["role"]     = user.role
    request.session["user_id"]  = str(user.id)

    # Update LastLogin timestamp in database
    await db.execute(
        text('UPDATE users SET "LastLogin" = NOW() WHERE id = :id'),
        {"id": str(user.id)}
    )
    await db.commit()

    return RedirectResponse(url="/map", status_code=302)


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


# GET — show the signup page
@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    user = get_current_user(request)
    if user:
        return RedirectResponse(url="/map", status_code=302)
    return templates.TemplateResponse("signup.html", {"request": request, "error": None})


# POST — handle signup form submission
@app.post("/signup", response_class=HTMLResponse)
async def signup_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    # Check passwords match
    if password != confirm_password:
        return templates.TemplateResponse(
            "signup.html",
            {"request": request, "error": "Passwords do not match."},
            status_code=400,
        )

    # Check password length
    if len(password) < 6:
        return templates.TemplateResponse(
            "signup.html",
            {"request": request, "error": "Password must be at least 6 characters."},
            status_code=400,
        )

    # Check if username already exists
    result = await db.execute(
        text("SELECT id FROM users WHERE username = :username"),
        {"username": username}
    )
    existing = result.fetchone()
    if existing:
        return templates.TemplateResponse(
            "signup.html",
            {"request": request, "error": "Username already taken."},
            status_code=400,
        )

    # Create the new user
    from database import hash_password
    new_id     = str(uuid.uuid4())
    hashed_pwd = hash_password(password)

    await db.execute(text("""
        INSERT INTO users (id, username, "PasswordHash", role)
        VALUES (:id, :username, :password_hash, 'user')
    """), {
        "id":            new_id,
        "username":      username,
        "password_hash": hashed_pwd,
    })
    await db.commit()

    # Log them in straight away
    request.session["user"]    = username
    request.session["role"]    = "user"
    request.session["user_id"] = new_id

    return RedirectResponse(url="/map", status_code=302)
# In-memory list of alert dicts; newest entries appended at the end.
alert_log: list[dict] = []


class AlertTriggerRequest(BaseModel):
    camera_id: str
    message: str = ""




@app.get("/api/traffic", response_class=JSONResponse)
async def api_traffic_all(db: AsyncSession = Depends(get_db)):
    """Return latest detection per camera — reads from DB."""
    result = await db.execute(text("""
        SELECT DISTINCT ON ("CameraId")
            "CameraId", "VehicleCount", "Severity", "fpsProcessed", "SnapshotTime", "RawResult"
        FROM "TrafficSnapshots"
        ORDER BY "CameraId", "SnapshotTime" DESC
    """))
    rows = result.fetchall()

    # Fall back to in-memory if DB has no snapshots yet
    if not rows:
        return latest_detections

    data = {}
    for row in rows:
        raw = row[5] if row[5] else {}
        if isinstance(raw, str):
            raw = json.loads(raw)
        data[row[0]] = {
            "camera_id":     row[0],
            "vehicle_count": row[1],
            "severity":      row[2],
            "fps_processed": row[3],
            "timestamp":     row[4].isoformat(),
            "color":         raw.get("color", "#58a6ff"),
            "frame_shape":   raw.get("frame_shape", []),
        }
    return data


@app.get("/api/traffic/{camera_id}", response_class=JSONResponse)
async def api_traffic_camera(camera_id: str, db: AsyncSession = Depends(get_db)):
    """Return latest detection for one camera — reads from DB."""
    result = await db.execute(text("""
        SELECT "CameraId", "VehicleCount", "Severity", "fpsProcessed", "SnapshotTime", "RawResult"
        FROM "TrafficSnapshots"
        WHERE "CameraId" = :camera_id
        ORDER BY "SnapshotTime" DESC
        LIMIT 1
    """), {"camera_id": camera_id})
    row = result.fetchone()

    # Fall back to in-memory if DB has no data yet
    if row is None:
        det = latest_detections.get(camera_id)
        if det is None:
            raise HTTPException(status_code=404, detail=f"Camera '{camera_id}' not found.")
        return det

    raw = row[5] if row[5] else {}
    if isinstance(raw, str):
        raw = json.loads(raw)

    return {
        "camera_id":     row[0],
        "vehicle_count": row[1],
        "severity":      row[2],
        "fps_processed": row[3],
        "timestamp":     row[4].isoformat(),
        "color":         raw.get("color", "#58a6ff"),
        "frame_shape":   raw.get("frame_shape", []),
    }


@app.get("/api/status", response_class=JSONResponse)
async def api_status(db: AsyncSession = Depends(get_db)):
    """System status — reads from DB snapshots."""
    result = await db.execute(text("""
        SELECT DISTINCT ON ("CameraId")
            "CameraId", "Severity"
        FROM "TrafficSnapshots"
        ORDER BY "CameraId", "SnapshotTime" DESC
    """))
    rows = result.fetchall()

    # Fall back to in-memory if DB empty
    if not rows:
        active_cameras    = len(latest_detections)
        total_detections  = sum(r["vehicle_count"] for r in latest_detections.values())
        bottlenecks       = sum(1 for r in latest_detections.values() if r["severity"] == "bottleneck")
    else:
        active_cameras   = len(rows)
        total_detections = sum(
            r["vehicle_count"] for r in latest_detections.values()
        ) if latest_detections else 0
        bottlenecks      = sum(1 for row in rows if row[1] == "bottleneck")

    return {
        "active_cameras":   active_cameras,
        "total_detections": total_detections,
        "bottlenecks":      bottlenecks,
        "system":           "online",
    }


@app.post("/api/alerts/trigger", response_class=JSONResponse, status_code=201)
async def api_alerts_trigger(body: AlertTriggerRequest, db: AsyncSession = Depends(get_db)):
    """Trigger an alert and save it to the database."""
    detection = latest_detections.get(body.camera_id)
    if detection is None:
        raise HTTPException(
            status_code=404,
            detail=f"Camera '{body.camera_id}' has no detection data yet.",
        )

    # First save the bottleneck event
    event_id = str(uuid_module.uuid4())
    await db.execute(text("""
        INSERT INTO "BottleneckEvents" (id, "CameraId", "DetectedAt", "Severity", "VehicleCount", "Color")
        VALUES (:id, :camera_id, NOW(), :severity, :vehicle_count, :color)
    """), {
        "id":            event_id,
        "camera_id":     body.camera_id,
        "severity":      detection["severity"],
        "vehicle_count": detection["vehicle_count"],
        "color":         detection["color"],
    })

    # Then save the alert linked to that event
    await db.execute(text("""
        INSERT INTO alerts ("CameraId", "EventId", "AlertType", "Severity", "Message")
        VALUES (:camera_id, :event_id, 'manual', :severity, :message)
    """), {
        "camera_id": body.camera_id,
        "event_id":  event_id,
        "severity":  detection["severity"],
        "message":   body.message or f"Alert triggered for {body.camera_id}",
    })

    await db.commit()
    return {"status": "saved", "camera_id": body.camera_id, "severity": detection["severity"]}


@app.get("/api/alerts", response_class=JSONResponse)
async def api_alerts_list(limit: int = 50, db: AsyncSession = Depends(get_db)):
    """Read alerts from the database, newest first."""
    result = await db.execute(text("""
        SELECT a.id, a."CameraId", a."Severity", a."Message", a."TriggeredAt", a.acknowledged
        FROM alerts a
        ORDER BY a."TriggeredAt" DESC
        LIMIT :limit
    """), {"limit": limit})
    rows = result.fetchall()

    return [
        {
            "alert_id":    row.id,
            "camera_id":   row[1],
            "severity":    row[2],
            "message":     row[3],
            "timestamp":   row[4].isoformat(),
            "acknowledged": row[5],
        }
        for row in rows
    ]


@app.get("/api/cameras", response_class=JSONResponse)
async def api_cameras(db: AsyncSession = Depends(get_db)):
    """Read cameras from the database, enriched with live detection state."""
    result = await db.execute(text('SELECT id, name, "StreamUrl", "isActive" FROM cameras'))
    rows = result.fetchall()
    
@app.get("/api/snapshots/{camera_id}", response_class=JSONResponse)
async def api_snapshots(camera_id: str, limit: int = 20, db: AsyncSession = Depends(get_db)):
    """Return last N snapshots for a camera — used by map.html for history."""
    result = await db.execute(text("""
        SELECT "VehicleCount", "Severity", "SnapshotTime"
        FROM "TrafficSnapshots"
        WHERE "CameraId" = :camera_id
        ORDER BY "SnapshotTime" DESC
        LIMIT :limit
    """), {"camera_id": camera_id, "limit": limit})
    rows = result.fetchall()
    return [
        {
            "vehicle_count": row[0],
            "severity":      row[1],
            "timestamp":     row[2].isoformat(),
        }
        for row in rows
    ]

    cameras = []
    for row in rows:
        det = latest_detections.get(row.id, {})
        cameras.append({
            "camera_id":        row.id,
            "name":             row.name,
            "source":           row[2] or "mock",
            "stream_type":      "hls" if (row[2] or "").endswith(".m3u8") else "rtsp",
            "is_active":        row[3],
            "current_severity": det.get("severity"),
            "current_count":    det.get("vehicle_count"),
        })
    return cameras


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
