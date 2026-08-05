"""
Author : Sahil Singh Rughoo (22414560) — Tech Lead
Unit   : ISAD3000 Capstone Computing Project 1
Team   : IBL Group — Traffic Bottleneck Detection System traffic summaries
"""

from __future__ import annotations

import logging
import statistics
import subprocess
import time
from collections import deque
from datetime import datetime, timezone
from typing import Generator

import cv2
import numpy as np
from ultralytics import YOLO

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
VEHICLE_CLASSES: set[int] = {1, 2, 3, 5, 7}  # bicycle, car, motorcycle, bus, truck

SEVERITY_THRESHOLDS = [
    (30, "bottleneck", "#8b31c7"),
    (15, "heavy",      "#e94560"),
    (5,  "moderate",   "#f0883e"),
    (0,  "free",       "#23c55e"),
]

MODEL_PATH      = "yolov8m.pt"
CONF_THRESHOLD  = 0.25   # lowered for night footage (valid detections score lower)
IOU_THRESHOLD   = 0.35   # looser NMS so queued/overlapping cars aren't merged
IMGSZ           = 1280   # stream is 1024x576 — upscale gives more detail for small vehicles
SMOOTH_WINDOW   = 3      # smaller window = more responsive at 0.5 fps
FRAME_INTERVAL  = 2.0               # seconds between frame grabs (= 1 / 0.5 fps)
SINGLE_FRAME_TIMEOUT = 20           # subprocess timeout for one frame grab (seconds)
MAX_CONSECUTIVE_FAILURES = 5        # consecutive None grabs before raising to retry loop
MAX_RETRIES     = 3                 # outer retry attempts before generator exhausts
RETRY_DELAY     = 10                # seconds between outer retry attempts
DEFAULT_WIDTH   = 1280
DEFAULT_HEIGHT  = 720


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
    """ffprobe the stream for width/height; fall back to defaults."""
    try:
        import json as _json
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", hls_url],
            capture_output=True, timeout=15,
        )
        if result.returncode == 0:
            data = _json.loads(result.stdout or b"{}")
            for s in data.get("streams", []):
                if s.get("codec_type") == "video":
                    w = int(s.get("width",  DEFAULT_WIDTH))
                    h = int(s.get("height", DEFAULT_HEIGHT))
                    logger.info("[hls] Stream dimensions: %dx%d", w, h)
                    return w, h
    except Exception as exc:
        logger.warning("[hls] ffprobe dimension check failed: %s", exc)

    logger.info("[hls] Using default dimensions %dx%d", DEFAULT_WIDTH, DEFAULT_HEIGHT)
    return DEFAULT_WIDTH, DEFAULT_HEIGHT


# ── Per-frame grab ────────────────────────────────────────────────────────────

def _grab_single_frame(hls_urls: list[str], width: int, height: int) -> np.ndarray | None:
    """
    Try each URL in *hls_urls* in order; return the first successfully decoded
    BGR24 frame, or None if every URL fails.

    A fresh subprocess is spawned per URL attempt so Wowza never sees a
    persistent connection long enough to drop it.
    """
    frame_bytes = width * height * 3
    for hls_url in hls_urls:
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-loglevel",           "error",
                    "-reconnect",          "1",
                    "-reconnect_streamed", "1",
                    "-timeout",            "30000000",   # microseconds = 30 s
                    "-rw_timeout",         "30000000",
                    "-i",                  hls_url,
                    "-vframes",            "1",
                    "-f",                  "rawvideo",
                    "-pix_fmt",            "bgr24",
                    "pipe:1",
                ],
                capture_output=True,
                timeout=SINGLE_FRAME_TIMEOUT,
            )
            if result.returncode == 0 and len(result.stdout) >= frame_bytes:
                raw = result.stdout[:frame_bytes]
                return np.frombuffer(raw, dtype=np.uint8).reshape((height, width, 3))
            err = result.stderr.decode(errors="replace")[:200].replace("\n", " ")
            logger.debug("[hls] frame grab failed rc=%d url=%s: %s", result.returncode, hls_url, err)
        except subprocess.TimeoutExpired:
            logger.debug("[hls] frame grab timed out after %ds url=%s", SINGLE_FRAME_TIMEOUT, hls_url)
        except Exception as exc:
            logger.debug("[hls] frame grab error url=%s: %s", hls_url, exc)
    return None


# ── Detection loop ────────────────────────────────────────────────────────────

def _detect_loop(
    model: YOLO,
    hls_urls: list[str],
    camera_id: str,
    width: int,
    height: int,
    device: str = "cpu",
) -> Generator[dict, None, None]:
    """
    Repeatedly grab one frame per FRAME_INTERVAL seconds, run YOLO, yield results.

    Raises RuntimeError after MAX_CONSECUTIVE_FAILURES consecutive None grabs so
    the outer retry loop in run_hls_pipeline can reconnect / back off.
    """
    count_window: deque[int] = deque(maxlen=SMOOTH_WINDOW)
    prev_centers: list = []
    consecutive_failures = 0

    while True:
        t_start = time.perf_counter()

        frame = _grab_single_frame(hls_urls, width, height)

        if frame is None:
            consecutive_failures += 1
            logger.warning(
                "[%s] Frame grab failed (%d/%d consecutive)",
                camera_id, consecutive_failures, MAX_CONSECUTIVE_FAILURES,
            )
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                raise RuntimeError(
                    f"[{camera_id}] {MAX_CONSECUTIVE_FAILURES} consecutive frame grabs "
                    "failed — stream appears unavailable."
                )
            time.sleep(FRAME_INTERVAL)
            continue

        consecutive_failures = 0  # reset on any successful grab

        # ── Brightness boost for dark frames (applied before CLAHE) ──────────
        if cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).mean() < 80:
            frame = cv2.convertScaleAbs(frame, alpha=1.3, beta=20)

        # ── Contrast enhancement for night footage (CLAHE on L channel) ──────
        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        l = clahe.apply(l)
        frame = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)

        # ── YOLO inference ────────────────────────────────────────────────────
        results = model.predict(
            source  = frame,
            conf    = CONF_THRESHOLD,
            iou     = IOU_THRESHOLD,
            imgsz   = IMGSZ,
            device  = device,
            verbose = False,
            stream  = False,
        )

        raw_count: int = 0
        vehicle_boxes: list = []
        for r in results:
            if r.boxes is None:
                continue
            cls_list  = r.boxes.cls.tolist()
            xyxy_list = r.boxes.xyxy.tolist()
            for i, cls_id in enumerate(cls_list):
                if int(cls_id) in VEHICLE_CLASSES:
                    raw_count += 1
                    vehicle_boxes.append(xyxy_list[i])

        # Rolling-median smoothing
        count_window.append(raw_count)
        vehicle_count = int(round(statistics.median(count_window)))
        severity, color = _classify(vehicle_count)

        # ── Per-frame incident signals ─────────────────────────────────────────
        frame_area        = width * height
        possible_incident = any(
            (b[2] - b[0]) * (b[3] - b[1]) > 0.25 * frame_area
            for b in vehicle_boxes
        )
        current_centers = [((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) for b in vehicle_boxes]
        if not possible_incident and len(current_centers) >= 5 and prev_centers:
            stationary = sum(
                1 for cx, cy in current_centers
                if any(abs(cx - px) < 10 and abs(cy - py) < 10 for px, py in prev_centers)
            )
            if stationary / len(current_centers) > 0.70:
                possible_incident = True
        prev_centers = current_centers

        elapsed       = time.perf_counter() - t_start
        fps_processed = round(1.0 / max(elapsed, 1e-6), 2)

        yield {
            "camera_id":         camera_id,
            "timestamp":         datetime.now(timezone.utc).isoformat(),
            "vehicle_count":     vehicle_count,
            "severity":          severity,
            "color":             color,
            "fps_processed":     fps_processed,
            "frame_shape":       [height, width],
            "possible_incident": possible_incident,
        }

        # Sleep the remainder of the frame interval so YOLO time is included
        remaining = FRAME_INTERVAL - (time.perf_counter() - t_start)
        if remaining > 0:
            time.sleep(remaining)


# ── Public entry point ────────────────────────────────────────────────────────

def run_hls_pipeline(
    camera_id: str,
    hls_urls: list[str],
) -> Generator[dict, None, None]:

    if not _ffmpeg_available():
        logger.error(
            "[%s] ffmpeg not found on PATH — cannot run HLS pipeline.\n"
            "  Install: https://www.gyan.dev/ffmpeg/builds/\n"
            "  Verify:  ffmpeg -version",
            camera_id,
        )
        return   # caller handles fallback

    logger.info("[%s] Loading YOLOv8 model %r …", camera_id, MODEL_PATH)
    model = YOLO(MODEL_PATH)
    try:
        import torch
        _device = "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        _device = "cpu"
    logger.info("[%s] Inference device: %s", camera_id, _device)

    width, height = _get_dimensions(hls_urls[0])

    attempt = 0
    while attempt <= MAX_RETRIES:
        try:
            logger.info(
                "[%s] Starting frame-grab loop (attempt %d/%d) urls=%s",
                camera_id, attempt + 1, MAX_RETRIES + 1, hls_urls,
            )
            yield from _detect_loop(model, hls_urls, camera_id, width, height, device=_device)
            logger.info("[%s] Detection loop ended cleanly.", camera_id)
            break   # clean exit — don't retry

        except Exception as exc:
            logger.error("[%s] Detection loop error: %s", camera_id, exc)

        attempt += 1
        if attempt <= MAX_RETRIES:
            logger.warning(
                "[%s] Retrying in %ds … (%d/%d)",
                camera_id, RETRY_DELAY, attempt, MAX_RETRIES,
            )
            time.sleep(RETRY_DELAY)

    logger.error(
        "[%s] HLS generator exhausted after %d attempt(s).",
        camera_id, MAX_RETRIES,
    )
