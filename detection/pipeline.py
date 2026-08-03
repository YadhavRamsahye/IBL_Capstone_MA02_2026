"""
Author : Sahil Singh Rughoo (22414560) — Tech Lead
Unit   : ISAD3000 Capstone Computing Project 1
Team   : IBL Group — Traffic Bottleneck Detection System traffic summaries
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Generator

import cv2
from ultralytics import YOLO

from detection.severity import VEHICLE_CLASSES, classify, pcu_total

logger = logging.getLogger(__name__)



TARGET_FPS = 15          # desired read rate
FRAME_SKIP = 2           # process every Nth frame (skip N-1 between processed)
MAX_RECONNECT = 3        # retry attempts after stream drop
RECONNECT_DELAY = 5      # seconds between retries

MODEL_PATH = "yolov8n.pt"  # downloaded automatically on first run




def _open_capture(source: str) -> cv2.VideoCapture:
    """Open a VideoCapture and raise if it fails."""
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise IOError(f"Cannot open video source: {source!r}")
    return cap


def _set_capture_fps(cap: cv2.VideoCapture, fps: int) -> None:
    """Request a specific FPS from the capture (best-effort; not all sources honour it)."""
    cap.set(cv2.CAP_PROP_FPS, fps)




def _detect_loop(
    model: YOLO,
    cap: cv2.VideoCapture,
    camera_id: str,
) -> Generator[dict, None, None]:
    """
    Inner generator: reads frames from *cap* and yields result dicts.
    Stops when the capture is exhausted or a read fails.
    """
    frame_index = 0
    prev_time = time.perf_counter()

    while True:
        ret, frame = cap.read()

        if not ret:
            # End-of-file for local files, or a dropped frame on a stream
            logger.warning("[%s] Frame read failed (ret=False).", camera_id)
            break

        frame_index += 1

        # Skip every other frame to stay within the 500 ms latency budget
        if frame_index % FRAME_SKIP != 0:
            continue

        
        results = model.predict(
            source=frame,
            verbose=False,
            stream=False,
        )

        vehicle_count = 0
        vehicle_classes: list[int] = []
        for r in results:
            if r.boxes is None:
                continue
            for cls_id in r.boxes.cls.tolist():
                if int(cls_id) in VEHICLE_CLASSES:
                    vehicle_count += 1
                    vehicle_classes.append(int(cls_id))

        pcu = pcu_total(vehicle_classes)
        severity, color, saturation = classify(pcu, camera_id)

        
        now = time.perf_counter()
        fps_processed = round(1.0 / max(now - prev_time, 1e-6), 2)
        prev_time = now

        h, w = frame.shape[:2]

        yield {
            "camera_id":     camera_id,
            "timestamp":     datetime.now(timezone.utc).isoformat(),
            "vehicle_count": vehicle_count,
            "pcu":           round(pcu, 2),
            "saturation":    round(saturation, 3),
            "severity":      severity,
            "color":         color,
            "fps_processed": fps_processed,
            "frame_shape":   [h, w],
        }




def run_pipeline(
    camera_id: str,
    source: str,
) -> Generator[dict, None, None]:

    logger.info("[%s] Loading YOLOv8 model from %r …", camera_id, MODEL_PATH)
    model = YOLO(MODEL_PATH)

    attempt = 0

    while attempt <= MAX_RECONNECT:
        cap = None
        try:
            logger.info(
                "[%s] Opening source (attempt %d/%d): %r",
                camera_id, attempt + 1, MAX_RECONNECT + 1, source,
            )
            cap = _open_capture(source)
            _set_capture_fps(cap, TARGET_FPS)

            # Reset reconnect counter on a successful open
            attempt = 0

            yield from _detect_loop(model, cap, camera_id)

            # _detect_loop returned → stream ended cleanly (e.g. end of file)
            logger.info("[%s] Stream ended (source exhausted).", camera_id)
            break

        except IOError as exc:
            logger.error("[%s] Could not open source: %s", camera_id, exc)

        except Exception as exc:  # noqa: BLE001
            logger.error("[%s] Unexpected error during detection: %s", camera_id, exc, exc_info=True)

        finally:
            if cap is not None:
                cap.release()

        attempt += 1
        if attempt <= MAX_RECONNECT:
            logger.info(
                "[%s] Reconnecting in %ds … (%d/%d)",
                camera_id, RECONNECT_DELAY, attempt, MAX_RECONNECT,
            )
            time.sleep(RECONNECT_DELAY)

    logger.error(
        "[%s] Giving up after %d reconnection attempt(s).",
        camera_id, MAX_RECONNECT,
    )
