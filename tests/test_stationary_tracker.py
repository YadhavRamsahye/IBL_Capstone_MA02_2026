"""
Tests for detection/stationary_tracker.py - frame-to-frame vehicle
association used to tell stopped traffic from merely dense traffic.

Pure geometry and a state machine with no cv2/YOLO/DB dependency, previously
untested despite being the module a 2026-08-07 audit found had never once
fired across 42,975 recorded frames.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from detection.stationary_tracker import (
    iou,
    centre_of,
    StationaryTracker,
    MIN_STATIONARY_VEHICLES,
    MIN_STATIONARY_FRACTION,
    MAX_MISSED_FRAMES,
)
from evidence import print_evidence


class IouTests(unittest.TestCase):
    def test_identical_boxes_have_iou_one(self) -> None:
        box = [0.0, 0.0, 10.0, 10.0]
        result = iou(box, box)
        print_evidence("TC-089", "Identical boxes have IoU 1.0",
                       "a = b = [0,0,10,10]", 1.0, result)
        self.assertEqual(result, 1.0)

    def test_disjoint_boxes_have_iou_zero(self) -> None:
        result = iou([0.0, 0.0, 10.0, 10.0], [20.0, 20.0, 30.0, 30.0])
        print_evidence("TC-090", "Disjoint boxes have IoU 0.0",
                       "a=[0,0,10,10], b=[20,20,30,30]", 0.0, result)
        self.assertEqual(result, 0.0)

    def test_touching_edges_have_iou_zero(self) -> None:
        """Boxes that only share an edge have zero area of intersection."""
        result = iou([0.0, 0.0, 10.0, 10.0], [10.0, 0.0, 20.0, 10.0])
        print_evidence("TC-091", "Boxes that only touch at an edge have IoU 0.0",
                       "a=[0,0,10,10], b=[10,0,20,10]", 0.0, result)
        self.assertEqual(result, 0.0)

    def test_partial_overlap_is_between_zero_and_one(self) -> None:
        score = iou([0.0, 0.0, 10.0, 10.0], [5.0, 5.0, 15.0, 15.0])
        # Intersection 5x5=25, union 100+100-25=175.
        expected = 25 / 175
        print_evidence("TC-092", "Partially overlapping boxes give a fractional IoU",
                       "a=[0,0,10,10], b=[5,5,15,15]", round(expected, 4), round(score, 4))
        self.assertAlmostEqual(score, expected)

    def test_degenerate_zero_area_box_has_iou_zero(self) -> None:
        result = iou([0.0, 0.0, 0.0, 0.0], [0.0, 0.0, 10.0, 10.0])
        print_evidence("TC-093", "A zero-area box never matches via IoU",
                       "a=[0,0,0,0] (degenerate), b=[0,0,10,10]", 0.0, result)
        self.assertEqual(result, 0.0)


class CentreOfTests(unittest.TestCase):
    def test_centre_of_box(self) -> None:
        result = centre_of([0.0, 0.0, 10.0, 20.0])
        print_evidence("TC-094", "Centre of an origin-anchored box",
                       "box = [0,0,10,20]", (5.0, 10.0), result)
        self.assertEqual(result, (5.0, 10.0))

    def test_centre_of_offset_box(self) -> None:
        result = centre_of([10.0, 10.0, 20.0, 30.0])
        print_evidence("TC-095", "Centre of an offset box",
                       "box = [10,10,20,30]", (15.0, 20.0), result)
        self.assertEqual(result, (15.0, 20.0))


def _box_at(cx: float, cy: float, size: float = 10.0) -> list:
    half = size / 2.0
    return [cx - half, cy - half, cx + half, cy + half]


class StationaryTrackerTests(unittest.TestCase):
    """frame_interval is set high in these tests so STALL_SECONDS_REQUIRED /
    frame_interval collapses to just 1-2 frames, keeping tests fast without
    changing any of the module's tunables."""

    def setUp(self) -> None:
        self.tracker = StationaryTracker(frame_interval=60.0)  # 1 frame required

    def test_no_vehicles_is_not_an_incident(self) -> None:
        verdict = self.tracker.update([])
        print_evidence("TC-096", "No vehicles in frame is never an incident",
                       "boxes = []", False, verdict.is_incident)
        self.assertFalse(verdict.is_incident)
        self.assertEqual(verdict.total_tracked, 0)

    def test_first_sighting_of_a_vehicle_is_not_yet_stationary(self) -> None:
        """A vehicle just seen for the first time has stationary_frames=0."""
        verdict = self.tracker.update([_box_at(0, 0)])
        print_evidence("TC-097", "A vehicle's first sighting is not yet stationary",
                       "1 box, first frame", 0, verdict.stationary_count)
        self.assertFalse(verdict.is_incident)
        self.assertEqual(verdict.stationary_count, 0)
        self.assertEqual(verdict.total_tracked, 1)

    def test_below_min_stationary_vehicles_never_confirms(self) -> None:
        """Fewer than MIN_STATIONARY_VEHICLES stalled vehicles is a parked car,
        not a queue - must never raise an incident regardless of duration."""
        boxes = [_box_at(i * 100, 0) for i in range(MIN_STATIONARY_VEHICLES - 1)]
        verdict = None
        for _ in range(5):
            verdict = self.tracker.update(boxes)
        print_evidence("TC-098", "Below MIN_STATIONARY_VEHICLES never confirms an incident",
                       f"{MIN_STATIONARY_VEHICLES - 1} stationary vehicles for 5 frames",
                       False, verdict.is_incident)
        self.assertFalse(verdict.is_incident)
        self.assertIn("need", verdict.reason)

    def test_sustained_stillness_of_a_queue_confirms_incident(self) -> None:
        """Enough vehicles, all stationary, for long enough -> confirmed incident."""
        boxes = [_box_at(i * 100, 0) for i in range(MIN_STATIONARY_VEHICLES)]
        verdict = None
        # frame_interval=60s and STALL_SECONDS_REQUIRED=60s -> 1 frame is
        # already the origin frame (stationary_frames=0), so a second
        # identical frame is needed to accumulate one stationary frame.
        for _ in range(3):
            verdict = self.tracker.update(boxes)
        print_evidence("TC-099", "Sustained stillness of a queue confirms an incident",
                       f"{MIN_STATIONARY_VEHICLES} vehicles stationary for 3 frames",
                       True, verdict.is_incident)
        self.assertTrue(verdict.is_incident)
        self.assertEqual(verdict.stationary_count, MIN_STATIONARY_VEHICLES)
        self.assertGreaterEqual(verdict.confidence, 0.60)
        self.assertIn("stationary", verdict.reason)

    def test_moving_traffic_never_confirms_even_with_enough_vehicles(self) -> None:
        """Vehicles that keep moving frame to frame never accumulate a stall,
        regardless of how many frames pass. Shift is small enough to stay
        IoU-matched (same identity) but well past STATIONARY_CENTROID_FRAC,
        so this exercises "matched but moving", not "lost track"."""
        verdict = None
        for step in range(10):
            boxes = [_box_at(i * 100 + step * 3, 0)
                     for i in range(MIN_STATIONARY_VEHICLES)]
            verdict = self.tracker.update(boxes)
        print_evidence("TC-100", "Continuously moving traffic never confirms an incident",
                       f"{MIN_STATIONARY_VEHICLES} vehicles shifting 3px/frame for 10 frames",
                       False, verdict.is_incident)
        self.assertFalse(verdict.is_incident)

    def test_below_min_stationary_fraction_does_not_confirm(self) -> None:
        """Even with enough stalled vehicles in absolute terms, if most of the
        visible traffic is still moving this must not read as a stopped road."""
        n_stationary = MIN_STATIONARY_VEHICLES
        # Add enough moving vehicles that the stationary ones are a minority.
        n_moving = int(n_stationary / MIN_STATIONARY_FRACTION)
        verdict = None
        for step in range(3):
            still = [_box_at(i * 100, 0) for i in range(n_stationary)]
            # Each moving vehicle keeps its own row (i * 100) so it is never
            # confused with another moving vehicle, but jumps 1000px in x
            # every frame - far past any IoU match - so it is always a brand
            # new track, never accumulating a stationary streak.
            moving = [_box_at(5000 + step * 1000, i * 100) for i in range(n_moving)]
            verdict = self.tracker.update(still + moving)
        print_evidence("TC-101", "A minority of stopped vehicles does not confirm an incident",
                       f"{n_stationary} stationary + {n_moving} moving vehicles",
                       False, verdict.is_incident)
        self.assertFalse(verdict.is_incident)
        self.assertIn("flowing", verdict.reason)

    def test_a_single_mismatch_decrements_rather_than_resets_the_streak(self) -> None:
        """One flickered non-stationary reading should cost the streak
        MISMATCH_PENALTY, not erase minutes of accumulated stillness."""
        tracker = StationaryTracker(frame_interval=2.0)
        boxes = [_box_at(i * 100, 0) for i in range(MIN_STATIONARY_VEHICLES)]

        # Build up a long stationary streak.
        for _ in range(40):
            tracker.update(boxes)
        track_before = tracker.visible_tracks()[0].stationary_frames
        self.assertGreater(track_before, 30)

        # One frame where every vehicle shifts a few px - still IoU-matched to
        # the same track, but past STATIONARY_CENTROID_FRAC, so it reads as moved.
        jumped = [_box_at(i * 100 + 3, 0) for i in range(MIN_STATIONARY_VEHICLES)]
        tracker.update(jumped)
        track_after = tracker.visible_tracks()[0].stationary_frames
        print_evidence("TC-102", "A single flicker decrements the streak, does not reset it",
                       f"stationary_frames={track_before} then one 3px shift",
                       track_before - 1, track_after)
        self.assertEqual(track_after, max(0, track_before - 1))

    def test_mark_missed_keeps_a_track_within_the_grace_period(self) -> None:
        tracker = StationaryTracker(frame_interval=60.0)
        tracker.update([_box_at(0, 0)])
        for _ in range(MAX_MISSED_FRAMES):
            tracker.mark_missed()
        self.assertEqual(len(tracker.visible_tracks()), 0)  # missed, not "visible"
        # Track should still exist internally, one grace frame left.
        verdict = tracker.update([_box_at(0, 0)])
        print_evidence("TC-103", "A track survives within the missed-frame grace period",
                       f"{MAX_MISSED_FRAMES} missed frames (== MAX_MISSED_FRAMES)",
                       1, verdict.total_tracked)
        self.assertEqual(verdict.total_tracked, 1)

    def test_mark_missed_drops_a_track_past_the_grace_period(self) -> None:
        tracker = StationaryTracker(frame_interval=60.0)
        tracker.update([_box_at(0, 0)])
        for _ in range(MAX_MISSED_FRAMES + 1):
            tracker.mark_missed()
        # The track has been dropped entirely; a re-appearance is a new track.
        verdict = tracker.update([_box_at(0, 0)])
        print_evidence("TC-104", "A track is dropped past the missed-frame grace period",
                       f"{MAX_MISSED_FRAMES + 1} missed frames (> MAX_MISSED_FRAMES)",
                       0, tracker.visible_tracks()[0].stationary_frames)
        self.assertEqual(verdict.total_tracked, 1)
        self.assertEqual(tracker.visible_tracks()[0].stationary_frames, 0)

    def test_reset_clears_all_state(self) -> None:
        boxes = [_box_at(i * 100, 0) for i in range(MIN_STATIONARY_VEHICLES)]
        for _ in range(3):
            self.tracker.update(boxes)
        self.tracker.reset()
        verdict = self.tracker.update(boxes)
        print_evidence("TC-105", "reset() clears all accumulated tracking state",
                       "3 stationary frames built up, then reset()", 0, verdict.stationary_count)
        self.assertEqual(verdict.stationary_count, 0)

    def test_verdict_to_dict_rounds_values(self) -> None:
        verdict = self.tracker.update([_box_at(0, 0)])
        d = verdict.to_dict()
        expected_keys = {"is_incident", "confidence", "stationary_count",
                         "total_tracked", "stalled_seconds", "reason"}
        print_evidence("TC-106", "Verdict.to_dict() exposes the expected fields",
                       "verdict from a single-box update", expected_keys, set(d.keys()))
        self.assertEqual(set(d.keys()), expected_keys)

    def test_visible_tracks_excludes_missed_ones(self) -> None:
        tracker = StationaryTracker(frame_interval=60.0)
        tracker.update([_box_at(0, 0), _box_at(500, 0)])
        # Next frame only re-detects one of the two boxes.
        tracker.update([_box_at(0, 0)])
        result = len(tracker.visible_tracks())
        print_evidence("TC-107", "visible_tracks() excludes a track missed this frame",
                       "2 tracks, then only 1 re-detected", 1, result)
        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
