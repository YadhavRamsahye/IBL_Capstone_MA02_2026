"""
detection/direction.py
Split a camera's vehicles into the two directions of travel.

The problem
-----------
Most of these cameras watch a two-way road but the pipeline reported a single
number for the whole frame. On a road where northbound is queued back and
southbound is empty, one aggregate "moderate" describes neither direction: it
overstates the clear side and understates the blocked one. A driver deciding
whether to take that road is asking about *their* direction, and the system had
no answer.

Why position, not motion
------------------------
The obvious approach is to use each vehicle's motion vector — it moved right,
so it is eastbound. That fails exactly when the data matters most: a queued
vehicle has no motion, so in the jam you are trying to detect, every vehicle
becomes unclassifiable. Congestion would systematically fall out of the
per-direction counts.

So classification is by **position**: which side of a per-camera dividing line
the vehicle sits on. That works whether traffic is flowing or stopped, and it
is deterministic and explainable.

Motion still matters, but for calibration rather than classification. The
dividing line and which side is which direction are *learned* from observed
motion by tools/calibrate_direction.py, then frozen into CAMERA_DIRECTIONS
below. Motion is also the fallback when a camera has no configuration yet.

Configuring a camera
--------------------
Run `python tools/calibrate_direction.py --camera <id>` while traffic is
flowing. It watches vehicles, works out the dominant axis of travel and where
the two streams separate, and prints a config block to paste in here.

An unconfigured camera reports a single "combined" direction, which is exactly
the previous behaviour — so adding this changed nothing until a camera is
calibrated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

COMBINED = "combined"

# Vehicles must have moved at least this many pixels from where they were first
# seen before their motion is treated as a direction rather than detector jitter.
MIN_MOTION_PX = 12.0


@dataclass(frozen=True)
class Divider:
    """A line splitting the frame, as a point on it plus a normal vector.

    A vehicle is on the positive side when (centre - point) · normal > 0.
    """
    point: tuple[float, float]
    normal: tuple[float, float]

    def side(self, centre: tuple[float, float]) -> int:
        dx = centre[0] - self.point[0]
        dy = centre[1] - self.point[1]
        dot = dx * self.normal[0] + dy * self.normal[1]
        return 1 if dot > 0 else -1


@dataclass(frozen=True)
class DirectionConfig:
    """How one camera's frame maps onto two directions of travel.

    `positive_label` names the side where `divider.side(...) == 1`.
    `capacity_pcu` is per direction; when omitted each direction gets half the
    camera's total, which is the right default for a symmetric two-way road.
    """
    positive_label: str
    negative_label: str
    divider: Divider
    capacity_pcu: Optional[tuple[float, float]] = None

    @property
    def labels(self) -> tuple[str, str]:
        return (self.positive_label, self.negative_label)


# ── Per-camera configuration ──────────────────────────────────────────────────
# Empty until calibrated. Deliberately not guessed: the dividing line depends on
# where each camera is mounted and which way it points, and inventing values
# would silently produce confident, wrong per-direction numbers — worse than the
# single aggregate this replaces.
#
# Populate with the output of tools/calibrate_direction.py, e.g.
#
#   "caudan_north": DirectionConfig(
#       positive_label="northbound",
#       negative_label="southbound",
#       divider=Divider(point=(512.0, 300.0), normal=(0.0, -1.0)),
#   ),
CAMERA_DIRECTIONS: dict[str, DirectionConfig] = {}


def config_for(camera_id: str) -> Optional[DirectionConfig]:
    return CAMERA_DIRECTIONS.get(camera_id)


def is_two_way(camera_id: str) -> bool:
    return camera_id in CAMERA_DIRECTIONS


def labels_for(camera_id: str) -> tuple[str, ...]:
    cfg = config_for(camera_id)
    return cfg.labels if cfg else (COMBINED,)


def direction_capacity(camera_id: str, label: str, camera_capacity: float
                       ) -> float:
    """Capacity for one direction, defaulting to an even split."""
    cfg = config_for(camera_id)
    if cfg is None:
        return camera_capacity
    if cfg.capacity_pcu is None:
        return camera_capacity / 2.0
    return cfg.capacity_pcu[0 if label == cfg.positive_label else 1]


def motion_direction(displacement: tuple[float, float],
                     axis: tuple[float, float]) -> Optional[int]:
    """Sign of travel along `axis`, or None when the vehicle has barely moved.

    Used for calibration and as the fallback for uncalibrated cameras. Returning
    None rather than guessing is deliberate — a stationary vehicle genuinely has
    no direction, and inventing one is how queued traffic ends up misattributed.
    """
    dx, dy = displacement
    if math.hypot(dx, dy) < MIN_MOTION_PX:
        return None
    dot = dx * axis[0] + dy * axis[1]
    if dot == 0:
        return None
    return 1 if dot > 0 else -1


def classify(camera_id: str, tracks) -> dict[str, list]:
    """Group visible tracks by direction label.

    Uncalibrated cameras get every vehicle under COMBINED, preserving the
    previous single-number behaviour rather than reporting a split it cannot
    justify.
    """
    cfg = config_for(camera_id)
    if cfg is None:
        return {COMBINED: list(tracks)}

    grouped: dict[str, list] = {cfg.positive_label: [], cfg.negative_label: []}
    for track in tracks:
        side = cfg.divider.side(track.centre)
        label = cfg.positive_label if side == 1 else cfg.negative_label
        grouped[label].append(track)
    return grouped
