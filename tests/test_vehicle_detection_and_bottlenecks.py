"""
Core vehicle detection and bottleneck incident tests.

This file covers the pure detection logic and incident-detection engine
without requiring the full ML stack or running the web app. It tests:
- vehicle count classification thresholds and inclusive boundary behavior
- invalid negative vehicle count handling
- bottleneck incident detection rules
- incident isolation across multiple cameras

It uses lightweight stubs for optional dependencies (cv2, ultralytics)
so tests remain fast and deterministic.
"""

import sys
import types
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Provide lightweight stubs for optional runtime dependencies so the tests can
# exercise the pure classification/incident logic without loading a full ML stack.
if "cv2" not in sys.modules:
    cv2_stub = types.ModuleType("cv2")

    class _VideoCapture:
        def __init__(self, *args, **kwargs):
            pass

        def isOpened(self):
            return True

        def set(self, *args, **kwargs):
            return True

        def read(self):
            return False, None

        def release(self):
            return None

    cv2_stub.VideoCapture = _VideoCapture
    cv2_stub.CAP_PROP_FPS = 5
    sys.modules["cv2"] = cv2_stub

if "ultralytics" not in sys.modules:
    ultralytics_stub = types.ModuleType("ultralytics")

    class YOLO:  # pragma: no cover - test stub
        pass

    ultralytics_stub.YOLO = YOLO
    sys.modules["ultralytics"] = ultralytics_stub

from detection.incident_detector import IncidentDetector
from detection.pipeline import _classify


class VehicleDetectionClassificationTests(unittest.TestCase):
    """Validate pure vehicle count classification behavior and threshold boundaries."""

    def test_classify_maps_expected_thresholds(self) -> None:
        """Assert each count falls into the expected severity and color bucket."""
        self.assertEqual(_classify(0), ("free", "#23c55e"))
        self.assertEqual(_classify(4), ("free", "#23c55e"))
        self.assertEqual(_classify(5), ("moderate", "#f0883e"))
        self.assertEqual(_classify(14), ("moderate", "#f0883e"))
        self.assertEqual(_classify(15), ("heavy", "#e94560"))
        self.assertEqual(_classify(29), ("heavy", "#e94560"))
        self.assertEqual(_classify(30), ("bottleneck", "#8b31c7"))
        self.assertEqual(_classify(100), ("bottleneck", "#8b31c7"))

    def test_classify_uses_inclusive_thresholds(self) -> None:
        """Ensure the classification thresholds are inclusive at boundary values."""
        self.assertEqual(_classify(5), ("moderate", "#f0883e"))
        self.assertEqual(_classify(15), ("heavy", "#e94560"))
        self.assertEqual(_classify(30), ("bottleneck", "#8b31c7"))

    def test_classify_rejects_negative_vehicle_counts(self) -> None:
        """Confirm negative vehicle counts raise a validation error."""
        with self.assertRaises(ValueError):
            _classify(-1)
        with self.assertRaises(ValueError):
            _classify(-10)


class BottleneckIncidentTests(unittest.TestCase):
    """Test bottleneck incident detection rules and camera isolation."""

    def test_sustained_bottleneck_incident_is_raised_after_two_bottleneck_readings(self) -> None:
        """Validate that two consecutive bottleneck readings generate a sustained incident."""
        detector = IncidentDetector()

        detector.analyze("camera_a", 32, "bottleneck")
        incidents = detector.analyze("camera_a", 35, "bottleneck")

        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0].type, "sustained_bottleneck")
        self.assertEqual(incidents[0].severity, "moderate")
        self.assertEqual(incidents[0].vehicle_count, 35)

        active = detector.get_active_incidents()
        self.assertTrue(any(item["type"] == "sustained_bottleneck" and not item["resolved"] for item in active))

    def test_isolated_bottleneck_reading_does_not_raise_incident(self) -> None:
        """Check that a single bottleneck detection does not create an incident prematurely."""
        detector = IncidentDetector()

        incidents = detector.analyze("camera_a", 32, "bottleneck")

        self.assertEqual(incidents, [])
        self.assertEqual(detector.get_active_incidents(), [])

    def test_negative_vehicle_count_does_not_raise_incidents(self) -> None:
        """Verify negative counts do not trigger false incident creation."""
        detector = IncidentDetector()

        incidents = detector.analyze("camera_a", -3, "free")

        self.assertEqual(incidents, [])
        self.assertEqual(detector.get_active_incidents(), [])

    def test_incidents_stay_isolated_per_camera(self) -> None:
        """Assert incident state remains isolated between different cameras."""
        detector = IncidentDetector()

        detector.analyze("camera_a", 32, "bottleneck")
        detector.analyze("camera_a", 35, "bottleneck")
        detector.analyze("camera_b", 2, "free")

        camera_a_incidents = detector.get_incidents_by_camera("camera_a")
        camera_b_incidents = detector.get_incidents_by_camera("camera_b")

        self.assertTrue(any(incident["type"] == "sustained_bottleneck" for incident in camera_a_incidents))
        self.assertEqual(camera_b_incidents, [])


if __name__ == "__main__":
    unittest.main()
