"""
Author : Sahil Singh Rughoo (22414560) — Tech Lead
Unit   : ISAD3000 Capstone Computing Project 1
Team   : IBL Group — Traffic Bottleneck Detection System traffic summaries
"""

from __future__ import annotations

import logging
import os
import statistics
import subprocess
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Generator

import cv2
import numpy as np
from ultralytics import YOLO

from detection import direction
from detection import severity as severity_mod
from detection.severity import VEHICLE_CLASSES, classify, pcu_total
from detection.stationary_tracker import StationaryTracker

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
# ── Detection cost knobs ──────────────────────────────────────────────────────
# These are the levers that decide how many cameras this machine can sustain.
# Measured on a 16-core CPU with a 1024x576 stream:
#
#   model      imgsz   ms/frame   cameras sustainable at a 2s interval
#   yolov8m     1280        891      2.2
#   yolov8m      640        255      7.9
#   yolov8n      960        103     19.3
#   yolov8n      640         64     31.1
#
# Sustainable cameras ≈ FRAME_INTERVAL / (ms_per_frame / 1000). Exceed it and
# frame grabs queue up and time out, so more cameras yield *less* data.
# Configurable so the trade-off can be tuned without editing code; see
# tools/tune_detection.py for a recommendation based on your camera count.
MODEL_PATH      = os.getenv("YOLO_MODEL", "yolov8m.pt")
CONF_THRESHOLD  = float(os.getenv("YOLO_CONF", "0.25"))   # lowered for night footage
IOU_THRESHOLD   = 0.35   # looser NMS so queued/overlapping cars aren't merged
# Note the stream is 1024x576, so 1280 *upscales* it. That buys detail on small
# distant vehicles at roughly 3.5x the cost of 640.
IMGSZ           = int(os.getenv("YOLO_IMGSZ", "1280"))
SMOOTH_WINDOW   = 3      # smaller window = more responsive at 0.5 fps
FRAME_INTERVAL  = float(os.getenv("FRAME_INTERVAL", "2.0"))  # seconds between grabs

# Bounds how many YOLO inferences run at once. Each prediction is already
# multi-threaded, so letting every camera predict simultaneously makes the
# threads fight for cores and slows all of them down. Defaults to a quarter of
# the cores, minimum 2.
MAX_CONCURRENT_INFERENCE = int(
    os.getenv("MAX_CONCURRENT_INFERENCE", str(max(2, (os.cpu_count() or 4) // 4)))
)
_inference_slots = threading.Semaphore(MAX_CONCURRENT_INFERENCE)

# One model instance shared by every camera. This used to be constructed inside
# run_hls_pipeline, so each camera held its own copy — 38 cameras meant 38 model
# loads and 38x the memory for identical weights.
_model_cache: dict[str, YOLO] = {}
_model_lock = threading.Lock()


SINGLE_FRAME_TIMEOUT = int(os.getenv("FRAME_TIMEOUT", "20"))
MAX_CONSECUTIVE_FAILURES = 5        # consecutive None grabs before raising to retry loop
MAX_RETRIES     = 3                 # outer retry attempts before generator exhausts
RETRY_DELAY     = 10                # seconds between outer retry attempts
DEFAULT_WIDTH   = 1280
DEFAULT_HEIGHT  = 720


def get_model(path: str = None) -> YOLO:
    """Return the shared YOLO instance for *path*, loading it once."""
    path = path or MODEL_PATH
    with _model_lock:
        if path not in _model_cache:
            logger.info("Loading YOLO model %r (shared across all cameras) …", path)
            _model_cache[path] = YOLO(path)
        return _model_cache[path]


# ── Helpers ───────────────────────────────────────────────────────────────────

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
    pcu_window: deque[float] = deque(maxlen=SMOOTH_WINDOW)
    tracker = StationaryTracker(frame_interval=FRAME_INTERVAL)
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
            # A gap in the stream breaks track continuity: after it, a different
            # vehicle may occupy the same pixels and be matched as the same one
            # "not moving". Discard the tracks rather than trust them.
            if consecutive_failures >= 2:
                tracker.reset()
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
        with _inference_slots:
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
        vehicle_classes: list[int] = []
        for r in results:
            if r.boxes is None:
                continue
            cls_list  = r.boxes.cls.tolist()
            xyxy_list = r.boxes.xyxy.tolist()
            for i, cls_id in enumerate(cls_list):
                if int(cls_id) in VEHICLE_CLASSES:
                    raw_count += 1
                    vehicle_boxes.append(xyxy_list[i])
                    vehicle_classes.append(int(cls_id))

        # Rolling-median smoothing on both the raw count and the PCU load. PCU
        # is what drives severity: a bus occupies roughly 3 cars' worth of road,
        # so weighting by vehicle type reflects actual demand rather than
        # treating a bicycle and a truck as equivalent.
        raw_pcu = pcu_total(vehicle_classes)
        count_window.append(raw_count)
        pcu_window.append(raw_pcu)
        vehicle_count = int(round(statistics.median(count_window)))
        pcu = float(statistics.median(pcu_window))
        # Severity is saturation (PCU / this camera's capacity), so cameras with
        # different fields of view are directly comparable.
        severity, color, saturation = classify(pcu, camera_id)

        # ── Incident signal ───────────────────────────────────────────────────
        # Tracked and time-persistent: a vehicle must be held stationary across
        # many frames before this reports anything. The previous per-frame
        # heuristics (large box / nearest-any-centre) are gone — see the module
        # docstring in detection/stationary_tracker.py for why they fired on
        # ordinary traffic.
        verdict = tracker.update(vehicle_boxes, vehicle_classes)

        # ── Per-direction breakdown ───────────────────────────────────────────
        # Reuses the tracker's association, so no second pass over the boxes.
        # An uncalibrated camera yields a single "combined" direction, which is
        # identical to the previous behaviour.
        by_direction = severity_mod.classify_directional(
            camera_id, direction.classify(camera_id, tracker.visible_tracks())
        )
        if direction.is_two_way(camera_id):
            # Headline severity is the worst direction: a road with one side
            # gridlocked must not read as "moderate" because the other side is
            # clear. The aggregate count stays whole-frame.
            severity = severity_mod.worst_severity(
                d["severity"] for d in by_direction.values()
            )
            color = severity_mod.colour_for(severity)

        elapsed       = time.perf_counter() - t_start
        fps_processed = round(1.0 / max(elapsed, 1e-6), 2)

        yield {
            "camera_id":          camera_id,
            "timestamp":          datetime.now(timezone.utc).isoformat(),
            "vehicle_count":      vehicle_count,
            "pcu":                round(pcu, 2),
            "saturation":         round(saturation, 3),
            "severity":           severity,
            "color":              color,
            "fps_processed":      fps_processed,
            "frame_shape":        [height, width],
            "directions":         by_direction,
            "possible_incident":  verdict.is_incident,
            "incident_detail":    verdict.to_dict(),
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
    # Shared instance — see get_model(). Loading per camera meant 38 copies.
    model = get_model()
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
