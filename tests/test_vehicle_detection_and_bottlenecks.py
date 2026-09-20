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

from unittest.mock import patch
from types import SimpleNamespace

from detection.incident_detector import IncidentDetector
from detection.severity import (
    classify_count,
    classify,
    classify_directional,
    capacity_for,
    pcu_total,
    worst_severity,
    colour_for,
)
from evidence import print_evidence


class VehicleDetectionClassificationTests(unittest.TestCase):
    """Validate pure vehicle count classification behavior and threshold boundaries."""

    def test_classify_maps_expected_thresholds(self) -> None:
        """Assert each count falls into the expected severity and color bucket."""
        result = classify_count(0, "unknown_camera")[:2]
        print_evidence("TC-001", "Vehicle count classification - free",
                       "vehicle_count = 0", ("free", "#23c55e"), result)
        self.assertEqual(result, ("free", "#23c55e"))
        self.assertEqual(classify_count(4, "unknown_camera")[:2], ("free", "#23c55e"))

        result = classify_count(5, "unknown_camera")[:2]
        print_evidence("TC-002", "Vehicle count classification - moderate",
                       "vehicle_count = 5", ("moderate", "#f0883e"), result)
        self.assertEqual(result, ("moderate", "#f0883e"))
        self.assertEqual(classify_count(11, "unknown_camera")[:2], ("moderate", "#f0883e"))

        result = classify_count(12, "unknown_camera")[:2]
        print_evidence("TC-003", "Vehicle count classification - heavy",
                       "vehicle_count = 12", ("heavy", "#e94560"), result)
        self.assertEqual(result, ("heavy", "#e94560"))
        self.assertEqual(classify_count(19, "unknown_camera")[:2], ("heavy", "#e94560"))

        result = classify_count(20, "unknown_camera")[:2]
        print_evidence("TC-004", "Vehicle count classification - bottleneck",
                       "vehicle_count = 20", ("bottleneck", "#8b31c7"), result)
        self.assertEqual(result, ("bottleneck", "#8b31c7"))
        self.assertEqual(classify_count(100, "unknown_camera")[:2], ("bottleneck", "#8b31c7"))

    def test_classify_uses_inclusive_thresholds(self) -> None:
        """Ensure the classification thresholds are inclusive at boundary values."""
        result = classify_count(5, "unknown_camera")[:2]
        print_evidence("TC-005", "Threshold boundary is inclusive (moderate starts at 5)",
                       "vehicle_count = 5", ("moderate", "#f0883e"), result)
        self.assertEqual(result, ("moderate", "#f0883e"))
        self.assertEqual(classify_count(12, "unknown_camera")[:2], ("heavy", "#e94560"))
        self.assertEqual(classify_count(20, "unknown_camera")[:2], ("bottleneck", "#8b31c7"))

    def test_negative_vehicle_counts_remain_below_congestion_thresholds(self) -> None:
        """Confirm invalid negative input cannot create a congestion severity."""
        self.assertEqual(classify_count(-1, "unknown_camera")[:2], ("free", "#23c55e"))
        result = classify_count(-10, "unknown_camera")[:2]
        print_evidence("TC-006", "Negative vehicle count cannot classify as congestion",
                       "vehicle_count = -10", ("free", "#23c55e"), result)
        self.assertEqual(result, ("free", "#23c55e"))


class PcuWeightedSeverityTests(unittest.TestCase):
    """Validate PCU-weighted classification, per-camera capacity, and the
    directional/aggregation helpers that classify_count's tests never touch."""

    def test_pcu_total_weights_by_vehicle_class(self) -> None:
        """Each COCO vehicle class contributes its own PCU weight, not 1.0 each."""
        # bicycle(0.20) + motorcycle(0.35) + car(1.00) + bus(3.00) + truck(3.50)
        result = pcu_total([1, 3, 2, 5, 7])
        print_evidence("TC-007", "PCU total weights each vehicle class",
                       "classes = [bicycle, motorcycle, car, bus, truck]", 8.05, result)
        self.assertAlmostEqual(result, 8.05)

    def test_pcu_total_ignores_unknown_classes(self) -> None:
        """A detection of a non-vehicle class (e.g. person) must not inflate load."""
        result = pcu_total([0, 99])
        print_evidence("TC-008", "PCU total ignores non-vehicle classes",
                       "classes = [0 (person), 99 (unknown)]", 0.0, result)
        self.assertEqual(result, 0.0)
        self.assertEqual(pcu_total([]), 0.0)

    def test_capacity_for_uses_per_camera_table(self) -> None:
        """Named cameras use their declared capacity; unknown ones use the default."""
        result = capacity_for("la_chaussee")
        print_evidence("TC-009", "Per-camera capacity lookup",
                       "camera_id = 'la_chaussee'", 14.0, result)
        self.assertEqual(result, 14.0)
        self.assertEqual(capacity_for("caudan_north"), 26.0)
        self.assertEqual(capacity_for("casernes"), 18.0)
        self.assertEqual(capacity_for("some_camera_never_configured"), 20.0)

    def test_classify_uses_saturation_not_raw_pcu(self) -> None:
        """A narrow-view camera reaches 'bottleneck' at a much lower PCU count
        than a wide-view one, because severity is PCU / capacity."""
        # la_chaussee capacity=14: 14 PCU is saturation 1.0 -> bottleneck.
        result = classify(14.0, "la_chaussee")[0]
        print_evidence("TC-010", "Saturation-based severity - narrow camera",
                       "pcu = 14.0, camera_id = 'la_chaussee' (capacity 14)",
                       "bottleneck", result)
        self.assertEqual(result, "bottleneck")
        # The same 14 PCU on caudan_north (capacity=26) is only ~0.54 -> moderate.
        result = classify(14.0, "caudan_north")[0]
        print_evidence("TC-011", "Saturation-based severity - wide camera",
                       "pcu = 14.0, camera_id = 'caudan_north' (capacity 26)",
                       "moderate", result)
        self.assertEqual(result, "moderate")

    def test_classify_saturation_band_boundaries_are_inclusive(self) -> None:
        """Saturation exactly on a threshold takes the higher band."""
        # la_chaussee capacity=14 -> thresholds at 3.5 / 8.4 / 14.0 PCU.
        result = classify(0.0, "la_chaussee")[0]
        print_evidence("TC-012", "Saturation classification - free",
                       "pcu = 0.0, camera_id = 'la_chaussee'", "free", result)
        self.assertEqual(result, "free")

        result = classify(3.5, "la_chaussee")[0]
        print_evidence("TC-013", "Saturation classification - moderate",
                       "pcu = 3.5, camera_id = 'la_chaussee'", "moderate", result)
        self.assertEqual(result, "moderate")

        result = classify(8.4, "la_chaussee")[0]
        print_evidence("TC-014", "Saturation classification - heavy",
                       "pcu = 8.4, camera_id = 'la_chaussee'", "heavy", result)
        self.assertEqual(result, "heavy")

        result = classify(14.0, "la_chaussee")[0]
        print_evidence("TC-015", "Saturation classification - bottleneck",
                       "pcu = 14.0, camera_id = 'la_chaussee'", "bottleneck", result)
        self.assertEqual(result, "bottleneck")

    def test_classify_returns_saturation_alongside_severity(self) -> None:
        """The saturation ratio returned must be comparable across cameras."""
        _, _, saturation = classify(7.0, "la_chaussee")
        print_evidence("TC-016", "Classify returns comparable saturation ratio",
                       "pcu = 7.0, camera_id = 'la_chaussee' (capacity 14)",
                       0.5, saturation, passed=abs(saturation - 0.5) < 1e-9)
        self.assertAlmostEqual(saturation, 0.5)

    def test_worst_severity_picks_the_most_congested(self) -> None:
        """Aggregating directions/cameras must surface the worst reading, not
        an average, so a gridlocked side is never hidden by a clear one."""
        result = worst_severity(["free", "moderate", "heavy"])
        print_evidence("TC-017", "Worst-of aggregation picks the most congested",
                       "severities = ['free', 'moderate', 'heavy']", "heavy", result)
        self.assertEqual(result, "heavy")

        result = worst_severity([])
        print_evidence("TC-018", "Worst-of aggregation defaults to free when empty",
                       "severities = []", "free", result)
        self.assertEqual(result, "free")
        self.assertEqual(worst_severity(["bottleneck", "free"]), "bottleneck")

    def test_colour_for_matches_severity_and_falls_back(self) -> None:
        """Every declared severity resolves to its band colour; an unknown
        severity name falls back to the free colour rather than raising."""
        result = colour_for("heavy")
        print_evidence("TC-019", "Colour lookup for a known severity",
                       "severity = 'heavy'", "#e94560", result)
        self.assertEqual(result, "#e94560")

        result = colour_for("not-a-real-severity")
        print_evidence("TC-020", "Colour lookup falls back for an unknown severity",
                       "severity = 'not-a-real-severity'", "#23c55e", result)
        self.assertEqual(result, "#23c55e")
        self.assertEqual(colour_for("bottleneck"), "#8b31c7")

    def test_classify_directional_scores_each_direction_independently(self) -> None:
        """An uncalibrated camera reports one 'combined' direction against the
        camera's full capacity."""
        tracks = [SimpleNamespace(cls_id=2) for _ in range(5)]  # 5 cars = 5.0 PCU
        result = classify_directional("some_camera_never_configured",
                                      {"combined": tracks})
        print_evidence("TC-021", "Directional classification - uncalibrated camera",
                       "5 cars, camera_id = 'some_camera_never_configured' (capacity 20)",
                       "moderate", result["combined"]["severity"])
        self.assertEqual(result["combined"]["vehicle_count"], 5)
        self.assertEqual(result["combined"]["pcu"], 5.0)
        # capacity 20 (default) -> saturation 0.25 -> moderate, inclusive boundary.
        self.assertEqual(result["combined"]["saturation"], 0.25)
        self.assertEqual(result["combined"]["severity"], "moderate")
        self.assertEqual(result["combined"]["color"], "#f0883e")

    def test_classify_directional_does_not_average_across_directions(self) -> None:
        """A calibrated two-way camera scores each side against its own
        capacity, so a gridlocked side is not averaged against a clear one."""
        from detection.direction import DirectionConfig, Divider

        config = DirectionConfig(
            positive_label="northbound",
            negative_label="southbound",
            divider=Divider(point=(0.0, 0.0), normal=(0.0, 1.0)),
            capacity_pcu=(10.0, 10.0),
        )
        gridlocked = [SimpleNamespace(cls_id=2) for _ in range(10)]  # 10 PCU / 10 = 1.0
        clear = [SimpleNamespace(cls_id=2) for _ in range(1)]        # 1 PCU / 10 = 0.1

        with patch.dict("detection.direction.CAMERA_DIRECTIONS",
                        {"two_way_cam": config}, clear=True):
            result = classify_directional(
                "two_way_cam",
                {"northbound": gridlocked, "southbound": clear},
            )

        print_evidence("TC-022", "Directional classification - gridlocked side",
                       "10 cars on northbound (capacity 10)",
                       "bottleneck", result["northbound"]["severity"])
        self.assertEqual(result["northbound"]["severity"], "bottleneck")

        print_evidence("TC-023", "Directional classification - clear side is not averaged down",
                       "1 car on southbound (capacity 10)",
                       "free", result["southbound"]["severity"])
        self.assertEqual(result["southbound"]["severity"], "free")


class BottleneckIncidentTests(unittest.TestCase):
    """Test bottleneck incident detection rules and camera isolation."""

    def test_sustained_bottleneck_incident_is_raised_after_four_bottleneck_readings(self) -> None:
        """Validate the confirmation streak prevents one-frame incident noise."""
        detector = IncidentDetector()

        detector.analyze("camera_a", 32, "bottleneck")
        self.assertEqual(detector.analyze("camera_a", 35, "bottleneck"), [])
        self.assertEqual(detector.analyze("camera_a", 38, "bottleneck"), [])
        self.assertEqual(detector.analyze("camera_a", 41, "bottleneck"), [])
        self.assertEqual(detector.analyze("camera_a", 44, "bottleneck"), [])
        incidents = detector.analyze("camera_a", 47, "bottleneck")

        sustained = next(
            incident for incident in incidents
            if incident.type == "sustained_bottleneck"
        )
        print_evidence("TC-024", "Sustained bottleneck confirmed after streak of readings",
                       "6 consecutive 'bottleneck' readings on camera_a",
                       "critical", sustained.severity)
        self.assertEqual(sustained.severity, "critical")
        self.assertEqual(sustained.vehicle_count, 47)

        active = detector.get_active_incidents()
        self.assertTrue(any(item["type"] == "sustained_bottleneck" and not item["resolved"] for item in active))

    def test_isolated_bottleneck_reading_does_not_raise_incident(self) -> None:
        """Check that a single bottleneck detection does not create an incident prematurely."""
        detector = IncidentDetector()

        incidents = detector.analyze("camera_a", 32, "bottleneck")

        print_evidence("TC-025", "A single bottleneck reading does not raise an incident",
                       "1 'bottleneck' reading on camera_a", [], incidents)
        self.assertEqual(incidents, [])
        self.assertEqual(detector.get_active_incidents(), [])

    def test_negative_vehicle_count_does_not_raise_incidents(self) -> None:
        """Verify negative counts do not trigger false incident creation."""
        detector = IncidentDetector()

        incidents = detector.analyze("camera_a", -3, "free")

        print_evidence("TC-026", "Negative vehicle count cannot raise an incident",
                       "vehicle_count = -3, severity = 'free'", [], incidents)
        self.assertEqual(incidents, [])
        self.assertEqual(detector.get_active_incidents(), [])

    def test_incidents_stay_isolated_per_camera(self) -> None:
        """Assert incident state remains isolated between different cameras."""
        detector = IncidentDetector()

        detector.analyze("camera_a", 32, "bottleneck")
        detector.analyze("camera_a", 35, "bottleneck")
        detector.analyze("camera_a", 38, "bottleneck")
        detector.analyze("camera_a", 41, "bottleneck")
        detector.analyze("camera_a", 44, "bottleneck")
        detector.analyze("camera_a", 47, "bottleneck")
        detector.analyze("camera_b", 2, "free")

        camera_a_incidents = detector.get_incidents_by_camera("camera_a")
        camera_b_incidents = detector.get_incidents_by_camera("camera_b")

        camera_a_has_sustained = any(
            incident["type"] == "sustained_bottleneck" for incident in camera_a_incidents
        )
        print_evidence("TC-027", "Incident on one camera does not affect another - camera_a",
                       "camera_a: 6 bottleneck readings", True, camera_a_has_sustained)
        self.assertTrue(camera_a_has_sustained)

        print_evidence("TC-028", "Incident on one camera does not affect another - camera_b",
                       "camera_b: 1 free reading", [], camera_b_incidents)
        self.assertEqual(camera_b_incidents, [])


if __name__ == "__main__":
    unittest.main()
