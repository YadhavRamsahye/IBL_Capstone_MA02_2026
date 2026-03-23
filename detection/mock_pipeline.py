
from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone
from typing import Generator

logger = logging.getLogger(__name__)


SEVERITY_LEVELS = [
    ("free",        "#23c55e",  0,  4),   # (severity, color, count_min, count_max)
    ("moderate",    "#f0883e",  5, 14),
    ("heavy",       "#e94560", 15, 29),
    ("bottleneck",  "#8b31c7", 30, 45),
]


DWELL_MIN = 8
DWELL_MAX = 20

# How many counts to step per frame when transitioning between levels
TRANSITION_STEP = 2

# Simulated frame dimensions
MOCK_FRAME_SHAPE = [720, 1280]

# Seconds between yielded results (simulates ~15 fps processing every 2nd frame)
TICK_INTERVAL = 1.0 / 7.5


# ── Helpers ───────────────────────────────────────────────────────────────────

def _severity_for_count(count: int) -> tuple[str, str]:
    """Return (severity, color) for a given vehicle count."""
    if count >= 30:
        return "bottleneck", "#8b31c7"
    if count >= 15:
        return "heavy", "#e94560"
    if count >= 5:
        return "moderate", "#f0883e"
    return "free", "#23c55e"


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))




def _build_scenario() -> list[tuple[int, int]]:
    """
    Build a list of (target_count, dwell_frames) pairs that form one full
    realistic traffic cycle:  free → moderate → heavy → bottleneck → heavy
                               → moderate → free  (with random dwell times).
    """
    sequence = [
        SEVERITY_LEVELS[0],   # free
        SEVERITY_LEVELS[1],   # moderate
        SEVERITY_LEVELS[2],   # heavy
        SEVERITY_LEVELS[3],   # bottleneck
        SEVERITY_LEVELS[2],   # heavy 
        SEVERITY_LEVELS[1],   # moderate
        SEVERITY_LEVELS[0],   # free
    ]

    scenario: list[tuple[int, int]] = []
    for _, _, lo, hi in sequence:
        target = random.randint(lo, hi)
        dwell_secs = random.uniform(DWELL_MIN, DWELL_MAX)
        dwell_frames = max(1, int(dwell_secs / TICK_INTERVAL))
        scenario.append((target, dwell_frames))

    return scenario




def run_mock_pipeline(
    camera_id: str = "mock_cam",
    frame_shape: list[int] | None = None,
) -> Generator[dict, None, None]:
    """
    Simulate the real pipeline output with smoothly varying vehicle counts.

    Parameters
    ----------
    camera_id : str
        Identifier included in every result dict.
    frame_shape : list[int] | None
        [height, width] to report. Defaults to 720 × 1280.

    Yields
    ------
    dict
        Same schema as ``detection.pipeline.run_pipeline``.
    """
    if frame_shape is None:
        frame_shape = MOCK_FRAME_SHAPE

    logger.info("[%s] Mock pipeline started.", camera_id)

    current_count = 0
    prev_time = time.perf_counter()

    while True:
        scenario = _build_scenario()

        for target_count, dwell_frames in scenario:
            frames_emitted = 0

            while frames_emitted < dwell_frames:
                
                if current_count < target_count:
                    current_count = _clamp(
                        current_count + random.randint(1, TRANSITION_STEP),
                        0, target_count,
                    )
                elif current_count > target_count:
                    current_count = _clamp(
                        current_count - random.randint(1, TRANSITION_STEP),
                        target_count, 9999,
                    )
                else:
                    # Add small noise while dwelling so the count isn't static
                    noise = random.randint(-1, 1)
                    lo = SEVERITY_LEVELS[0][2]  # absolute minimum = 0
                    current_count = max(lo, current_count + noise)

                severity, color = _severity_for_count(current_count)

                now = time.perf_counter()
                fps_processed = round(1.0 / max(now - prev_time, 1e-6), 2)
                prev_time = now

                yield {
                    "camera_id":     camera_id,
                    "timestamp":     datetime.now(timezone.utc).isoformat(),
                    "vehicle_count": current_count,
                    "severity":      severity,
                    "color":         color,
                    "fps_processed": fps_processed,
                    "frame_shape":   frame_shape,
                }

                frames_emitted += 1
                time.sleep(TICK_INTERVAL)
