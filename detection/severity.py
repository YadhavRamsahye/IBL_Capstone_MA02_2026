"""
detection/severity.py
Single source of truth for turning vehicle detections into a congestion level.

Why this module exists
----------------------
Severity used to be computed by a `_classify()` copied into both
detection/pipeline.py and detection/hls_pipeline.py, against a shared table of
absolute vehicle counts:

    (30, "bottleneck"), (15, "heavy"), (5, "moderate"), (0, "free")

That had three defects, all of which biased the output rather than merely
being untidy:

1. **Fixed counts across cameras.** A camera watching a narrow two-lane street
   and one watching a wide junction have completely different fields of view.
   Fifteen vehicles is gridlock on the first and free-flowing on the second, so
   wide-view cameras were permanently reported as more congested than narrow
   ones regardless of actual conditions. Severity is now `count / capacity`,
   where capacity is declared per camera.

2. **Every vehicle counted as 1.** A bicycle and an articulated truck
   contributed equally. Traffic engineering measures demand in Passenger Car
   Units, which is what `PCU_WEIGHTS` below encodes. This matters in Mauritius
   in particular: motorcycle share is high and motorcycles filter between lanes
   rather than queueing, so counting them as whole cars overstated congestion
   on exactly the roads where it was least real.

3. **Two copies that drifted.** hls_pipeline counted class 1 (bicycle) and
   pipeline.py did not, so the same road classified differently depending on
   which pipeline happened to be running.

Calibrating capacity
--------------------
`capacity` is "how many PCU are visible in this camera's frame when its road is
at practical capacity" — not a road-design figure. Estimate it from footage:
take the 95th-percentile PCU load observed over a busy period. The values in
CAMERA_CAPACITY are informed estimates from the Port Louis stream geometry, not
measurements; see tools/calibrate_capacity.py to derive real ones.
"""

from __future__ import annotations

# ── COCO class → PCU weight ───────────────────────────────────────────────────
# Keys are COCO class IDs as emitted by YOLOv8. Values follow standard PCU
# convention (car = 1.0). A vehicle class absent from this map is not counted.
PCU_WEIGHTS: dict[int, float] = {
    1: 0.20,   # bicycle
    3: 0.35,   # motorcycle
    2: 1.00,   # car
    5: 3.00,   # bus
    7: 3.50,   # truck
}

# Derived so the two pipelines can never disagree about what counts as a vehicle.
VEHICLE_CLASSES: frozenset[int] = frozenset(PCU_WEIGHTS)

# ── Per-camera capacity, in PCU visible at practical capacity ─────────────────
# Tune per camera; see the module docstring. DEFAULT_CAPACITY is used for any
# camera not listed, and is deliberately mid-range rather than optimistic.
CAMERA_CAPACITY: dict[str, float] = {
    "caudan_north": 26.0,   # wide dual-carriageway view, long sight line
    "caudan_south": 26.0,
    "la_chaussee":  14.0,   # narrow city street, short sight line
    "casernes":     18.0,   # single carriageway, medium view
}
DEFAULT_CAPACITY: float = 20.0

# ── Saturation → severity ─────────────────────────────────────────────────────
# Saturation is PCU / capacity, so these are comparable across cameras.
# Ordered high to low; first match wins.
SEVERITY_BANDS: list[tuple[float, str, str]] = [
    (1.00, "bottleneck", "#8b31c7"),   # at or over practical capacity
    (0.60, "heavy",      "#e94560"),
    (0.25, "moderate",   "#f0883e"),
    (0.00, "free",       "#23c55e"),
]

FREE_COLOR = "#23c55e"


def capacity_for(camera_id: str) -> float:
    """PCU capacity for a camera, falling back to the default."""
    return CAMERA_CAPACITY.get(camera_id, DEFAULT_CAPACITY)


def pcu_total(class_ids) -> float:
    """Sum PCU weights for an iterable of COCO class IDs.

    Unknown classes contribute 0, so a detection of a person or traffic light
    cannot inflate the load.
    """
    return sum(PCU_WEIGHTS.get(int(c), 0.0) for c in class_ids)


def classify(pcu: float, camera_id: str) -> tuple[str, str, float]:
    """Classify a PCU load for a specific camera.

    Returns ``(severity, colour, saturation)`` where saturation is the
    PCU-to-capacity ratio — the value that is actually comparable between
    cameras, and worth storing alongside the raw count.
    """
    capacity = capacity_for(camera_id)
    saturation = pcu / capacity if capacity > 0 else 0.0
    for threshold, severity, colour in SEVERITY_BANDS:
        if saturation >= threshold:
            return severity, colour, saturation
    return "free", FREE_COLOR, saturation


def classify_count(vehicle_count: int, camera_id: str) -> tuple[str, str, float]:
    """Classify a raw vehicle count when per-class data is unavailable.

    Assumes an average mix of 1.0 PCU per vehicle. Prefer ``classify`` with real
    PCU whenever the class IDs are to hand — this exists for the mock pipeline
    and for callers that only ever had a count.
    """
    return classify(float(vehicle_count), camera_id)
