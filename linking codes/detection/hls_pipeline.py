from __future__ import annotations

import logging
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Generator

import numpy as np
from ultralytics import YOLO

logger = logging.getLogger(__name__)

# ── Constants (mirror pipeline.py exactly) ────────────────────────────────────
VEHICLE_CLASSES: set[int] = {2, 3, 5, 7}   # car, motorcycle, bus, truck

SEVERITY_THRESHOLDS = [
    (30, "bottleneck", "#8b31c7"),
    (15, "heavy",      "#e94560"),
    (5,  "moderate",   "#f0883e"),
    (0,  "free",       "#23c55e"),
]

MODEL_PATH      = "yolov8n.pt"
TARGET_FPS      = 0.5       # one frame every 2 s — halves pipe throughput, reduces stall risk
MAX_RETRIES     = 3
RETRY_DELAY     = 5         # seconds between reconnect attempts
DEFAULT_WIDTH   = 1280
DEFAULT_HEIGHT  = 720
FRAME_READ_TIMEOUT = 10     # seconds to wait for one complete frame before treating as stall


# ── Helpers ───────────────────────────────────────────────────────────────────

def _classify(count: int) -> tuple[str, str]:
    for threshold, severity, color in SEVERITY_THRESHOLDS:
        if count >= threshold:
            return severity, color
    return "free", "#23c55e"


def _ffmpeg_available() -> bool:
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _get_dimensions(hls_url: str) -> tuple[int, int]:
    """
    Use ffprobe to read the first video stream's width/height.
    Returns (width, height); falls back to DEFAULT_WIDTH × DEFAULT_HEIGHT.
    """
    try:
        import json as _json
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                hls_url,
            ],
            capture_output=True, timeout=10,
        )
        if result.returncode == 0:
            data = _json.loads(result.stdout or b"{}")
            for s in data.get("streams", []):
                if s.get("codec_type") == "video":
                    w = int(s.get("width",  DEFAULT_WIDTH))
                    h = int(s.get("height", DEFAULT_HEIGHT))
                    logger.info("[hls] Stream dimensions from ffprobe: %dx%d", w, h)
                    return w, h
    except Exception as exc:
        logger.warning("[hls] ffprobe dimension check failed: %s", exc)

    logger.info("[hls] Defaulting to %dx%d", DEFAULT_WIDTH, DEFAULT_HEIGHT)
    return DEFAULT_WIDTH, DEFAULT_HEIGHT


# ── FFmpeg frame reader ───────────────────────────────────────────────────────

def _open_ffmpeg_pipe(hls_url: str, width: int, height: int) -> subprocess.Popen:
    """
    Open an ffmpeg subprocess that writes raw BGR24 frames to stdout.
    Low-latency flags minimise buffering and reduce the chance of stalling
    between HLS segments.
    """
    cmd = [
        "ffmpeg",
        # ── Low-latency / buffer flags (fix 5) ───────────────────────────
        "-fflags",          "nobuffer",
        "-flags",           "low_delay",
        "-strict",          "experimental",
        "-probesize",       "32",
        "-analyzeduration", "0",
        # ── Input ─────────────────────────────────────────────────────────
        "-loglevel", "error",
        "-i", hls_url,
        # ── Output: one frame every 2 s (fix 3) ───────────────────────────
        "-vf", f"fps={TARGET_FPS}",
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "pipe:1",
    ]
    logger.debug("[hls] ffmpeg command: %s", " ".join(cmd))
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _read_one_frame(
    stdout,
    frame_bytes: int,
    result_box: list,
    done_event: threading.Event,
) -> None:
    """
    Worker thread: read exactly *frame_bytes* from *stdout* into
    *result_box[0]*, then set *done_event*.  If the pipe closes before
    a full frame is available, result_box[0] stays None.
    """
    buf = b""
    try:
        while len(buf) < frame_bytes:
            chunk = stdout.read(frame_bytes - len(buf))
            if not chunk:
                return          # pipe closed
            buf += chunk
        result_box[0] = buf
    finally:
        done_event.set()


def _read_frames(
    proc: subprocess.Popen,
    width: int,
    height: int,
) -> Generator[np.ndarray, None, None]:
    """
    Yield raw BGR frames from *proc*.stdout.

    Each frame is read in a daemon thread.  If no complete frame arrives
    within FRAME_READ_TIMEOUT seconds the ffmpeg process is killed and a
    TimeoutError is raised so the outer retry loop can reconnect (fix 1).
    """
    frame_bytes = width * height * 3

    while True:
        result_box: list  = [None]
        done_event        = threading.Event()

        reader = threading.Thread(
            target=_read_one_frame,
            args=(proc.stdout, frame_bytes, result_box, done_event),
            daemon=True,
        )
        reader.start()
        signalled = done_event.wait(timeout=FRAME_READ_TIMEOUT)

        if not signalled or result_box[0] is None:
            # Stalled or pipe closed — kill ffmpeg and surface the error
            try:
                proc.kill()
            except Exception:
                pass
            if not signalled:
                raise TimeoutError(
                    f"No frame received within {FRAME_READ_TIMEOUT}s — "
                    "HLS stream stalled between segments."
                )
            break   # pipe closed cleanly

        raw   = result_box[0]
        frame = np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 3))
        yield frame


# ── Detection loop ────────────────────────────────────────────────────────────

def _detect_loop(
    model: YOLO,
    proc: subprocess.Popen,
    camera_id: str,
    width: int,
    height: int,
) -> Generator[dict, None, None]:
    """
    Pull frames from *proc*, run YOLO, classify, yield result dicts.
    Returns when the pipe is exhausted or proc exits.
    """
    prev_time = time.perf_counter()

    for frame in _read_frames(proc, width, height):
        results = model.predict(source=frame, verbose=False, stream=False)

        vehicle_count = 0
        for r in results:
            if r.boxes is None:
                continue
            for cls_id in r.boxes.cls.tolist():
                if int(cls_id) in VEHICLE_CLASSES:
                    vehicle_count += 1

        severity, color = _classify(vehicle_count)

        now = time.perf_counter()
        fps_processed = round(1.0 / max(now - prev_time, 1e-6), 2)
        prev_time = now

        yield {
            "camera_id":     camera_id,
            "timestamp":     datetime.now(timezone.utc).isoformat(),
            "vehicle_count": vehicle_count,
            "severity":      severity,
            "color":         color,
            "fps_processed": fps_processed,
            "frame_shape":   [height, width],
        }


# ── Public entry point ────────────────────────────────────────────────────────

def run_hls_pipeline(
    camera_id: str,
    hls_url: str,
) -> Generator[dict, None, None]:
    
    # ── Pre-flight: check ffmpeg ──────────────────────────────────────────
    if not _ffmpeg_available():
        logger.error(
            "[%s] ffmpeg not found on PATH.\n"
            "  Install from  https://ffmpeg.org/download.html\n"
            "  Windows build: https://www.gyan.dev/ffmpeg/builds/\n"
            "  After installing, restart your terminal and verify:\n"
            "    ffmpeg -version\n"
            "  Falling back to mock pipeline for camera '%s'.",
            camera_id, camera_id,
        )
        from detection.mock_pipeline import run_mock_pipeline
        yield from run_mock_pipeline(camera_id=camera_id)
        return

    # ── Load YOLO model ───────────────────────────────────────────────────
    logger.info("[%s] Loading YOLOv8 model from %r …", camera_id, MODEL_PATH)
    model = YOLO(MODEL_PATH)

    # ── Get stream dimensions ─────────────────────────────────────────────
    width, height = _get_dimensions(hls_url)

    # ── Retry loop ────────────────────────────────────────────────────────
    attempt = 0
    while attempt <= MAX_RETRIES:
        proc = None
        try:
            logger.info(
                "[%s] Opening HLS stream (attempt %d/%d): %s",
                camera_id, attempt + 1, MAX_RETRIES + 1, hls_url,
            )
            proc = _open_ffmpeg_pipe(hls_url, width, height)

            # Reset retry counter on a successful pipe open
            attempt = 0

            yield from _detect_loop(model, proc, camera_id, width, height)

            # _detect_loop returned → stream ended cleanly
            logger.info("[%s] HLS stream ended.", camera_id)
            break

        except Exception as exc:
            logger.error("[%s] HLS pipeline error: %s", camera_id, exc, exc_info=True)

        finally:
            if proc is not None:
                try:
                    proc.stdout.close()
                    proc.wait(timeout=3)
                except Exception:
                    proc.kill()

        attempt += 1
        if attempt <= MAX_RETRIES:
            logger.warning(
                "[%s] HLS stream failed — retrying in %ds … (%d/%d)",
                camera_id, RETRY_DELAY, attempt, MAX_RETRIES,
            )
            time.sleep(RETRY_DELAY)

    logger.error(
        "[%s] HLS failed after %d attempt(s) — switching to mock pipeline.",
        camera_id, MAX_RETRIES,
    )
    from detection.mock_pipeline import run_mock_pipeline
    yield from run_mock_pipeline(camera_id=camera_id)
