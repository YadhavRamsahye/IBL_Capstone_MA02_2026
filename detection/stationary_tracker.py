"""
detection/stationary_tracker.py
Frame-to-frame vehicle association used to decide whether traffic is *stopped*
rather than merely *dense*.

Why this exists
---------------
The original per-frame heuristic in hls_pipeline.py raised "possible incident"
when either

  (a) any single detection box covered >25% of the frame, or
  (b) >70% of vehicle centres sat within 10px of *any* centre from the previous
      frame.

Both fire on completely normal traffic.  (a) triggers whenever a bus or truck
passes near the camera.  (b) never actually tracked a vehicle: it matched each
centre against *any* previous centre, so in dense traffic some other car is
almost always near where a car used to be.  Neither had a time requirement, so
a single coincidental frame flipped the badge on.

What this does instead
----------------------
Associates boxes between frames by IoU so "stationary" means *this* vehicle did
not move, then requires that condition to hold for longer than a traffic-light
cycle before reporting anything.  A red light stops traffic for 30-120s; an
incident blocks it for far longer.  Duration is the only signal available at
0.5 fps that separates the two, so it is the one this leans on.

This cannot make incident detection certain — a long light, a stalled delivery
van, and a collision are genuinely indistinguishable from bounding boxes alone.
It is built to make a *positive* report mean something, accepting that slow or
missed detections are the cost.

Public API
----------
    tracker = StationaryTracker()
    verdict = tracker.update(vehicle_boxes)   # boxes as [x1, y1, x2, y2]
    verdict.is_incident      -> bool   (confirmed, not "possible")
    verdict.confidence       -> float  0.0-1.0
    verdict.stationary_count -> int
    verdict.stalled_seconds  -> float
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ── Tunables ──────────────────────────────────────────────────────────────────
# Association: boxes overlapping by at least this are treated as the same vehicle.
MATCH_IOU = 0.30

# A matched vehicle counts as "not moving" when its box barely shifted. At 0.5
# fps a vehicle in motion moves far more than this between frames.
STATIONARY_IOU = 0.85

# How long the stop must persist. Must exceed the longest normal traffic-light
# cycle at the site or every red light reads as an incident. Mauritius signals
# run up to ~120s, so this is deliberately above that.
STALL_SECONDS_REQUIRED = 150.0

# A stop is only interesting if it involves a queue, not one parked car.
MIN_STATIONARY_VEHICLES = 4

# ...and if most of the visible traffic is stopped, not a couple of parked cars
# beside moving lanes.
MIN_STATIONARY_FRACTION = 0.70

# Frames a track may go undetected before being dropped. YOLO flickers on
# distant/occluded vehicles; without this every flicker would reset the timer.
MAX_MISSED_FRAMES = 3

# Confidence below this is not worth surfacing.
MIN_CONFIDENCE = 0.75


def iou(a: list[float], b: list[float]) -> float:
    """Intersection-over-union of two [x1, y1, x2, y2] boxes."""
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = ix2 - ix1, iy2 - iy1
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def centre_of(box: list[float]) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


@dataclass
class _Track:
    box: list[float]
    stationary_frames: int = 0
    missed_frames: int = 0
    # Centre when this vehicle was first seen. Displacement from here is what
    # gives the vehicle a direction of travel — see detection/direction.py.
    origin: tuple[float, float] = (0.0, 0.0)
    age_frames: int = 0
    # COCO class id, carried so per-direction PCU can be computed from the same
    # association rather than re-matching boxes to classes downstream.
    cls_id: int = -1

    @property
    def centre(self) -> tuple[float, float]:
        return centre_of(self.box)

    @property
    def displacement(self) -> tuple[float, float]:
        cx, cy = self.centre
        return (cx - self.origin[0], cy - self.origin[1])


@dataclass
class Verdict:
    """Outcome of one update. `is_incident` is deliberately conservative."""
    is_incident: bool = False
    confidence: float = 0.0
    stationary_count: int = 0
    total_tracked: int = 0
    stalled_seconds: float = 0.0
    reason: str = "insufficient evidence"

    def to_dict(self) -> dict:
        return {
            "is_incident":      self.is_incident,
            "confidence":       round(self.confidence, 2),
            "stationary_count": self.stationary_count,
            "total_tracked":    self.total_tracked,
            "stalled_seconds":  round(self.stalled_seconds, 1),
            "reason":           self.reason,
        }


class StationaryTracker:
    """Per-camera tracker. Not thread-safe; one instance per camera loop."""

    def __init__(self, frame_interval: float = 2.0) -> None:
        self.frame_interval = frame_interval
        self._tracks: list[_Track] = []
        # Frames the confirmed-incident condition has held, so the flag does not
        # flicker off on a single noisy frame once raised.
        self._confirmed_frames = 0

    @property
    def _frames_required(self) -> int:
        return max(1, int(round(STALL_SECONDS_REQUIRED / self.frame_interval)))

    def update(self, boxes: list[list[float]],
               classes: list[int] | None = None) -> Verdict:
        """Consume one frame's vehicle boxes and return the current verdict.

        `classes` are the matching COCO class ids, kept on each track so
        per-direction PCU can reuse this association.
        """
        self._associate(boxes, classes or [])

        stationary = [t for t in self._tracks
                      if t.stationary_frames >= self._frames_required
                      and t.missed_frames == 0]
        visible = [t for t in self._tracks if t.missed_frames == 0]

        n_stat, n_vis = len(stationary), len(visible)
        longest = max((t.stationary_frames for t in stationary), default=0)
        stalled_seconds = longest * self.frame_interval
        fraction = (n_stat / n_vis) if n_vis else 0.0

        verdict = Verdict(
            stationary_count=n_stat,
            total_tracked=n_vis,
            stalled_seconds=stalled_seconds,
        )

        if n_stat < MIN_STATIONARY_VEHICLES:
            verdict.reason = (
                f"only {n_stat} vehicle(s) stalled >{STALL_SECONDS_REQUIRED:.0f}s "
                f"(need {MIN_STATIONARY_VEHICLES})"
            )
            self._confirmed_frames = 0
            return verdict

        if fraction < MIN_STATIONARY_FRACTION:
            verdict.reason = (
                f"{fraction:.0%} of traffic stalled (need "
                f"{MIN_STATIONARY_FRACTION:.0%}) — traffic still flowing"
            )
            self._confirmed_frames = 0
            return verdict

        # Confidence grows with how far past the threshold the stall has gone
        # and how completely traffic has stopped, so a marginal case does not
        # read the same as an unambiguous one.
        over = stalled_seconds / STALL_SECONDS_REQUIRED       # >= 1.0 here
        verdict.confidence = min(0.99, 0.60 + 0.20 * min(over - 1.0, 1.0) + 0.20 * fraction)

        if verdict.confidence < MIN_CONFIDENCE:
            verdict.reason = f"confidence {verdict.confidence:.2f} below {MIN_CONFIDENCE}"
            self._confirmed_frames = 0
            return verdict

        self._confirmed_frames += 1
        verdict.is_incident = True
        verdict.reason = (
            f"{n_stat} of {n_vis} vehicles stationary for "
            f"{stalled_seconds:.0f}s — traffic is stopped, not flowing"
        )
        return verdict

    def visible_tracks(self) -> list[_Track]:
        """Tracks detected in the most recent frame.

        Exposed so direction classification can reuse this association rather
        than running a second, independent tracker over the same boxes.
        """
        return [t for t in self._tracks if t.missed_frames == 0]

    def reset(self) -> None:
        """Drop all state — call when a stream reconnects and continuity breaks."""
        self._tracks.clear()
        self._confirmed_frames = 0

    # ── Internals ─────────────────────────────────────────────────────────────

    def _associate(self, boxes: list[list[float]], classes: list[int]) -> None:
        """Greedy IoU matching of this frame's boxes onto existing tracks."""
        unmatched_tracks = list(self._tracks)
        matched: list[_Track] = []

        for i, box in enumerate(boxes):
            cls_id = classes[i] if i < len(classes) else -1
            best, best_iou = None, MATCH_IOU
            for track in unmatched_tracks:
                score = iou(track.box, box)
                if score >= best_iou:
                    best, best_iou = track, score

            if best is None:
                matched.append(_Track(box=list(box), origin=centre_of(box),
                                      cls_id=cls_id))
                continue

            unmatched_tracks.remove(best)
            best.age_frames += 1
            # Compare against the box the track held *before* this update, so
            # "stationary" measures actual displacement.
            best.stationary_frames = (
                best.stationary_frames + 1 if iou(best.box, box) >= STATIONARY_IOU else 0
            )
            best.box = list(box)
            best.cls_id = cls_id
            best.missed_frames = 0
            matched.append(best)

        # Tracks with no detection this frame: keep briefly to ride out flicker,
        # but freeze their stationary counter rather than advancing it.
        for track in unmatched_tracks:
            track.missed_frames += 1
            if track.missed_frames <= MAX_MISSED_FRAMES:
                matched.append(track)

        self._tracks = matched
