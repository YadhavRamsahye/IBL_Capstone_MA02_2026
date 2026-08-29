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
Associates boxes between frames by IoU (matching identity - "is this the same
vehicle") and separately measures whether that vehicle moved by normalised
centroid displacement (its own box diagonal, not IoU, not raw pixels - see
2026-08-07 below), then requires "did not move" to hold for a sustained
period before reporting anything.

2026-08-07 recalibration
-------------------------
An incident-detection audit found this tracker correctly wired into every
frame yet its verdict had never once fired across 42,975 recorded snapshots.
Three real problems, found with tools/incident_harness.py rather than
guessed:

* STATIONARY_IOU=0.85 measured "did not move" as box-overlap, which
  penalises ordinary YOLO box-size jitter as much as real displacement - an
  8% shrink/grow on a re-detection reads as "moved" even at zero centroid
  displacement. Replaced with centroid displacement normalised by the box's
  own diagonal, which is insensitive to the box's size wobbling.
* A single non-stationary reading reset the whole streak to zero, so one
  flickered detection undid minutes of real accumulated stillness. Now
  decrements by MISMATCH_PENALTY instead.
* STALL_SECONDS_REQUIRED was tuned against the nominal 2.0s FRAME_INTERVAL;
  the real measured loop cadence on this deployment is closer to 2.3s
  (grab+inference alone routinely exceeds the nominal interval, so the
  "sleep the remainder" step contributes ~0). hls_pipeline.py now measures
  its own real cadence and updates frame_interval on this tracker directly,
  rather than trusting the constant.

STATIONARY_CENTROID_FRAC below is still provisional: live footage during
this recalibration was empty, rainy-night streets on every covered camera -
no genuinely stationary vehicle was available to sample. What *is* measured
is a floor: identical input frames (confirmed via the HLS segment's ~10-11s
refresh interval - polling faster than that re-decodes the same segment,
see incident_harness.py's --gap warning) produce byte-identical detection
boxes, so YOLO's own regression contributes zero jitter on truly unchanged
pixels. Real jitter comes from genuinely different frames of the same
physical vehicle, which needs daytime traffic to sample. Re-run
`python tools/incident_harness.py measure --camera <id> --gap 11` next time
real stopped traffic is visible, and tighten this from the printed p90.

This cannot make incident detection certain - a long light, a stalled delivery
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

import math
from dataclasses import dataclass, field

# ── Tunables ──────────────────────────────────────────────────────────────────
# Association: boxes overlapping by at least this are treated as the same vehicle.
MATCH_IOU = 0.30

# A matched vehicle counts as "not moving" when its centre shifted less than
# this fraction of its own box diagonal between frames. Normalised so a near
# (large-box) and distant (small-box) vehicle are held to the same real-world
# standard, and so ordinary box-size jitter (regression noise on width/height,
# not position) doesn't read as movement the way an IoU threshold did.
# PROVISIONAL - not yet measured against a real stationary vehicle; see the
# 2026-08-07 note in the module docstring and tools/incident_harness.py.
STATIONARY_CENTROID_FRAC = 0.04

# A non-stationary reading costs the streak this many frames rather than
# zeroing it outright, so one flickered detection doesn't erase minutes of
# real accumulated stillness. Still net-negative on genuine movement, which
# is non-stationary every single frame.
MISMATCH_PENALTY = 1

# How long the stop must persist. The original 150s was deliberately above
# Mauritius' ~120s longest signal cycle so a red light could never read as an
# incident on duration alone. Lowered to 60s on 2026-08-07 so the capability
# is demonstrable without a 2.5-minute wait - this reopens that false-positive
# risk on a long red light; MIN_STATIONARY_VEHICLES/MIN_STATIONARY_FRACTION
# below don't distinguish "everyone stopped at a light" from "everyone
# stopped behind a crash" either, so a long cycle at a busy signal can still
# raise a false stalled_vehicle incident until this trade-off is revisited.
STALL_SECONDS_REQUIRED = 60.0

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
    # gives the vehicle a direction of travel - see detection/direction.py.
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
        """Consume one frame's vehicle boxes and return the whole-frame verdict.

        `classes` are the matching COCO class ids, kept on each track so
        per-direction PCU can reuse this association.
        """
        self._associate(boxes, classes or [])
        verdict = self._verdict_for_tracks(self.visible_tracks())
        # Every early-return path inside _verdict_for_tracks corresponds to
        # "not an incident", so this reduces to the same reset-on-no /
        # increment-on-yes behaviour the inline version had.
        self._confirmed_frames = self._confirmed_frames + 1 if verdict.is_incident else 0
        return verdict

    def verdict_for(self, tracks: list["_Track"]) -> Verdict:
        """Verdict for an arbitrary subset of currently-visible tracks.

        Applies the identical stalled-traffic thresholds as the whole-frame
        verdict, just scoped smaller — e.g. one direction's tracks from
        direction.classify(camera_id, tracker.visible_tracks()). This is what
        lets a single blocked direction surface even when a flowing opposite
        direction would dilute the whole-frame stalled fraction below
        MIN_STATIONARY_FRACTION and hide it entirely.
        """
        return self._verdict_for_tracks(tracks)

    def _verdict_for_tracks(self, tracks: list["_Track"]) -> Verdict:
        """Core verdict math against a list of already-visible tracks.

        Callers are responsible for having already filtered to
        missed_frames == 0 (both `visible_tracks()` and direction.classify's
        grouping do this), so no such filtering happens here.
        """
        stationary = [t for t in tracks if t.stationary_frames >= self._frames_required]

        n_stat, n_vis = len(stationary), len(tracks)
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
            return verdict

        if fraction < MIN_STATIONARY_FRACTION:
            verdict.reason = (
                f"{fraction:.0%} of traffic stalled (need "
                f"{MIN_STATIONARY_FRACTION:.0%}) - traffic still flowing"
            )
            return verdict

        # Confidence grows with how far past the threshold the stall has gone
        # and how completely traffic has stopped, so a marginal case does not
        # read the same as an unambiguous one.
        over = stalled_seconds / STALL_SECONDS_REQUIRED       # >= 1.0 here
        verdict.confidence = min(0.99, 0.60 + 0.20 * min(over - 1.0, 1.0) + 0.20 * fraction)

        if verdict.confidence < MIN_CONFIDENCE:
            verdict.reason = f"confidence {verdict.confidence:.2f} below {MIN_CONFIDENCE}"
            return verdict

        verdict.is_incident = True
        verdict.reason = (
            f"{n_stat} of {n_vis} vehicles stationary for "
            f"{stalled_seconds:.0f}s - traffic is stopped, not flowing"
        )
        return verdict

    def visible_tracks(self) -> list[_Track]:
        """Tracks detected in the most recent frame.

        Exposed so direction classification can reuse this association rather
        than running a second, independent tracker over the same boxes.
        """
        return [t for t in self._tracks if t.missed_frames == 0]

    def reset(self) -> None:
        """Drop all state - call when a stream reconnects and continuity breaks
        (e.g. a fresh outer retry in hls_pipeline.run_hls_pipeline, which
        constructs a new tracker anyway; kept for callers that don't)."""
        self._tracks.clear()
        self._confirmed_frames = 0

    def mark_missed(self) -> None:
        """Advance every track's missed-frame counter with no new detections,
        for a frame that could not be grabbed at all.

        Uses the same MAX_MISSED_FRAMES grace period _associate() already
        gives a track that simply wasn't matched this frame, rather than
        wiping every vehicle's progress on a brief grab failure - a single
        HLS hiccup used to reset() the whole tracker after just two failed
        grabs, discarding a stall that had been building for two minutes
        over one dropped frame.
        """
        survivors = []
        for track in self._tracks:
            track.missed_frames += 1
            if track.missed_frames <= MAX_MISSED_FRAMES:
                survivors.append(track)
        self._tracks = survivors

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
            # "stationary" measures actual displacement - normalised by the
            # box's own diagonal so it isn't fooled by ordinary regression
            # jitter on width/height (see STATIONARY_CENTROID_FRAC above).
            prev_centre = centre_of(best.box)
            new_centre  = centre_of(box)
            diagonal    = math.hypot(best.box[2] - best.box[0], best.box[3] - best.box[1])
            disp_frac   = math.hypot(new_centre[0] - prev_centre[0],
                                     new_centre[1] - prev_centre[1]) / max(1.0, diagonal)
            best.stationary_frames = (
                best.stationary_frames + 1 if disp_frac <= STATIONARY_CENTROID_FRAC
                else max(0, best.stationary_frames - MISMATCH_PENALTY)
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
