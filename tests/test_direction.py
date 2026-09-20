"""
Tests for detection/direction.py - splitting a camera's vehicles into the
two directions of travel.

This module previously had no test coverage at all despite being pure logic
(no cv2/YOLO/DB dependency) that backs severity.classify_directional.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from detection.direction import (
    COMBINED,
    Divider,
    DirectionConfig,
    config_for,
    is_two_way,
    labels_for,
    direction_capacity,
    motion_direction,
    classify,
)
from evidence import print_evidence


class DividerTests(unittest.TestCase):
    """A Divider splits the frame into a positive and negative side."""

    def test_side_is_positive_when_dot_product_is_positive(self) -> None:
        divider = Divider(point=(0.0, 0.0), normal=(0.0, 1.0))
        result = divider.side((0.0, 10.0))
        print_evidence("TC-067", "Divider side is positive above the line",
                       "point=(0,0), normal=(0,1), centre=(0,10)", 1, result)
        self.assertEqual(result, 1)

    def test_side_is_negative_when_dot_product_is_negative(self) -> None:
        divider = Divider(point=(0.0, 0.0), normal=(0.0, 1.0))
        result = divider.side((0.0, -10.0))
        print_evidence("TC-068", "Divider side is negative below the line",
                       "point=(0,0), normal=(0,1), centre=(0,-10)", -1, result)
        self.assertEqual(result, -1)

    def test_side_on_the_line_falls_to_negative(self) -> None:
        """dot == 0 is not > 0, so a point exactly on the divider is -1."""
        divider = Divider(point=(0.0, 0.0), normal=(0.0, 1.0))
        result = divider.side((5.0, 0.0))
        print_evidence("TC-069", "A point exactly on the divider falls to the negative side",
                       "point=(0,0), normal=(0,1), centre=(5,0)", -1, result)
        self.assertEqual(result, -1)

    def test_side_uses_point_as_origin(self) -> None:
        divider = Divider(point=(100.0, 100.0), normal=(1.0, 0.0))
        result = divider.side((150.0, 0.0))
        print_evidence("TC-070", "Divider side is measured relative to its own point",
                       "point=(100,100), normal=(1,0), centre=(150,0)", 1, result)
        self.assertEqual(result, 1)
        self.assertEqual(divider.side((50.0, 0.0)), -1)


class DirectionConfigTests(unittest.TestCase):
    def test_labels_returns_positive_then_negative(self) -> None:
        config = DirectionConfig(
            positive_label="north",
            negative_label="south",
            divider=Divider(point=(0.0, 0.0), normal=(0.0, 1.0)),
        )
        result = config.labels
        print_evidence("TC-071", "DirectionConfig.labels orders positive before negative",
                       "positive_label='north', negative_label='south'",
                       ("north", "south"), result)
        self.assertEqual(result, ("north", "south"))


class UnconfiguredCameraTests(unittest.TestCase):
    """A camera absent from CAMERA_DIRECTIONS must behave exactly like the
    old single-aggregate pipeline - nothing here should invent a split."""

    def test_config_for_unknown_camera_is_none(self) -> None:
        result = config_for("never_configured")
        print_evidence("TC-072", "Unconfigured camera has no DirectionConfig",
                       "camera_id = 'never_configured'", None, result)
        self.assertIsNone(result)

    def test_is_two_way_is_false_for_unknown_camera(self) -> None:
        result = is_two_way("never_configured")
        print_evidence("TC-073", "Unconfigured camera is not reported as two-way",
                       "camera_id = 'never_configured'", False, result)
        self.assertFalse(result)

    def test_labels_for_unknown_camera_is_combined(self) -> None:
        result = labels_for("never_configured")
        print_evidence("TC-074", "Unconfigured camera reports a single 'combined' label",
                       "camera_id = 'never_configured'", (COMBINED,), result)
        self.assertEqual(result, (COMBINED,))

    def test_direction_capacity_for_unknown_camera_is_full_capacity(self) -> None:
        result = direction_capacity("never_configured", COMBINED, 20.0)
        print_evidence("TC-075", "Unconfigured camera's direction gets the full capacity",
                       "camera_id = 'never_configured', camera_capacity = 20.0", 20.0, result)
        self.assertEqual(result, 20.0)

    def test_classify_for_unknown_camera_groups_everything_as_combined(self) -> None:
        tracks = [SimpleNamespace(centre=(1.0, 1.0)), SimpleNamespace(centre=(-1.0, -1.0))]
        grouped = classify("never_configured", tracks)
        print_evidence("TC-076", "Unconfigured camera groups all tracks under 'combined'",
                       "2 tracks, camera_id = 'never_configured'",
                       {COMBINED}, set(grouped.keys()))
        self.assertEqual(set(grouped.keys()), {COMBINED})
        self.assertEqual(grouped[COMBINED], tracks)


class CalibratedCameraTests(unittest.TestCase):
    """Behaviour once a camera has a real DirectionConfig, using a patched
    CAMERA_DIRECTIONS so these tests don't depend on any camera actually
    being calibrated in the shipped table."""

    def setUp(self) -> None:
        self.config = DirectionConfig(
            positive_label="northbound",
            negative_label="southbound",
            divider=Divider(point=(0.0, 0.0), normal=(0.0, 1.0)),
        )
        self.patcher = patch.dict(
            "detection.direction.CAMERA_DIRECTIONS",
            {"two_way_cam": self.config},
            clear=True,
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_config_for_returns_the_configured_object(self) -> None:
        result = config_for("two_way_cam")
        print_evidence("TC-077", "Calibrated camera returns its configured DirectionConfig",
                       "camera_id = 'two_way_cam'", id(self.config), id(result),
                       passed=result is self.config)
        self.assertIs(result, self.config)

    def test_is_two_way_is_true(self) -> None:
        result = is_two_way("two_way_cam")
        print_evidence("TC-078", "Calibrated camera is reported as two-way",
                       "camera_id = 'two_way_cam'", True, result)
        self.assertTrue(result)

    def test_labels_for_returns_both_directions(self) -> None:
        result = labels_for("two_way_cam")
        print_evidence("TC-079", "Calibrated camera reports both direction labels",
                       "camera_id = 'two_way_cam'", ("northbound", "southbound"), result)
        self.assertEqual(result, ("northbound", "southbound"))

    def test_direction_capacity_splits_evenly_when_not_specified(self) -> None:
        """No explicit capacity_pcu -> each direction gets half the total."""
        result = direction_capacity("two_way_cam", "northbound", 20.0)
        print_evidence("TC-080", "Capacity splits evenly with no explicit per-direction value",
                       "camera_capacity = 20.0, no capacity_pcu set", 10.0, result)
        self.assertEqual(result, 10.0)
        self.assertEqual(direction_capacity("two_way_cam", "southbound", 20.0), 10.0)

    def test_direction_capacity_uses_explicit_split_when_given(self) -> None:
        asymmetric = DirectionConfig(
            positive_label="northbound",
            negative_label="southbound",
            divider=Divider(point=(0.0, 0.0), normal=(0.0, 1.0)),
            capacity_pcu=(15.0, 5.0),
        )
        with patch.dict("detection.direction.CAMERA_DIRECTIONS",
                        {"two_way_cam": asymmetric}, clear=True):
            north_result = direction_capacity("two_way_cam", "northbound", 20.0)
            print_evidence("TC-081", "Explicit asymmetric capacity_pcu is honoured",
                           "capacity_pcu = (15.0, 5.0), label = 'northbound'",
                           15.0, north_result)
            self.assertEqual(north_result, 15.0)
            self.assertEqual(direction_capacity("two_way_cam", "southbound", 20.0), 5.0)

    def test_classify_groups_tracks_by_divider_side(self) -> None:
        tracks = [
            SimpleNamespace(centre=(0.0, 5.0)),   # positive side -> northbound
            SimpleNamespace(centre=(0.0, -5.0)),  # negative side -> southbound
            SimpleNamespace(centre=(0.0, 3.0)),   # positive side -> northbound
        ]
        grouped = classify("two_way_cam", tracks)
        print_evidence("TC-082", "Classify groups tracks by which side of the divider they're on",
                       "3 tracks at y=5, y=-5, y=3",
                       {"northbound": 2, "southbound": 1},
                       {"northbound": len(grouped["northbound"]),
                        "southbound": len(grouped["southbound"])})
        self.assertEqual(grouped["northbound"], [tracks[0], tracks[2]])
        self.assertEqual(grouped["southbound"], [tracks[1]])

    def test_classify_returns_empty_lists_for_a_direction_with_no_traffic(self) -> None:
        tracks = [SimpleNamespace(centre=(0.0, 5.0))]
        grouped = classify("two_way_cam", tracks)
        print_evidence("TC-083", "A direction with no traffic gets an empty list, not a missing key",
                       "1 track, all northbound", [], grouped["southbound"])
        self.assertEqual(grouped["southbound"], [])


class MotionDirectionTests(unittest.TestCase):
    """Motion is only used for calibration and the uncalibrated fallback, and
    must return None (not a guess) for a vehicle that hasn't really moved."""

    def test_returns_none_below_the_motion_threshold(self) -> None:
        result = motion_direction((1.0, 1.0), axis=(1.0, 0.0))
        print_evidence("TC-084", "Displacement below the jitter threshold has no direction",
                       "displacement=(1,1), axis=(1,0)", None, result)
        self.assertIsNone(result)

    def test_returns_positive_one_for_travel_along_axis(self) -> None:
        result = motion_direction((20.0, 0.0), axis=(1.0, 0.0))
        print_evidence("TC-085", "Travel along the axis returns +1",
                       "displacement=(20,0), axis=(1,0)", 1, result)
        self.assertEqual(result, 1)

    def test_returns_negative_one_for_travel_against_axis(self) -> None:
        result = motion_direction((-20.0, 0.0), axis=(1.0, 0.0))
        print_evidence("TC-086", "Travel against the axis returns -1",
                       "displacement=(-20,0), axis=(1,0)", -1, result)
        self.assertEqual(result, -1)

    def test_returns_none_when_motion_is_perpendicular_to_axis(self) -> None:
        """Enough displacement to clear the jitter threshold, but along an
        axis with zero dot product - genuinely ambiguous, so None not a guess."""
        result = motion_direction((0.0, 20.0), axis=(1.0, 0.0))
        print_evidence("TC-087", "Perpendicular motion is ambiguous, not guessed",
                       "displacement=(0,20), axis=(1,0)", None, result)
        self.assertIsNone(result)

    def test_exactly_at_threshold_is_not_barely_moved(self) -> None:
        """hypot == MIN_MOTION_PX is not < MIN_MOTION_PX, so it counts as motion."""
        result = motion_direction((12.0, 0.0), axis=(1.0, 0.0))
        print_evidence("TC-088", "Displacement exactly at the threshold counts as motion",
                       "displacement=(12,0) == MIN_MOTION_PX, axis=(1,0)", 1, result)
        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
