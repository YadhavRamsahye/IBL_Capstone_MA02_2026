"""
Tests for detection/mock_pipeline.py - the synthetic detection generator used
for demos and UI development when no real camera stream is available.

run_mock_pipeline() itself is an infinite generator paced by real time.sleep,
so it is exercised here only as a short, sleep-mocked smoke test; the pure
helpers it's built from get full coverage. Previously untested.
"""

import itertools
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from detection.mock_pipeline import (
    _severity_for_count,
    _clamp,
    _build_scenario,
    run_mock_pipeline,
    SEVERITY_LEVELS,
    DWELL_MIN,
    DWELL_MAX,
    TICK_INTERVAL,
)
from evidence import print_evidence


class SeverityForCountTests(unittest.TestCase):
    def test_boundaries_match_the_declared_bands(self) -> None:
        result = _severity_for_count(0)
        print_evidence("TC-168", "Mock severity band - free",
                       "vehicle_count = 0", ("free", "#23c55e"), result)
        self.assertEqual(result, ("free", "#23c55e"))
        self.assertEqual(_severity_for_count(4), ("free", "#23c55e"))

        result = _severity_for_count(5)
        print_evidence("TC-169", "Mock severity band - moderate",
                       "vehicle_count = 5", ("moderate", "#f0883e"), result)
        self.assertEqual(result, ("moderate", "#f0883e"))
        self.assertEqual(_severity_for_count(14), ("moderate", "#f0883e"))

        result = _severity_for_count(15)
        print_evidence("TC-170", "Mock severity band - heavy",
                       "vehicle_count = 15", ("heavy", "#e94560"), result)
        self.assertEqual(result, ("heavy", "#e94560"))
        self.assertEqual(_severity_for_count(29), ("heavy", "#e94560"))

        result = _severity_for_count(30)
        print_evidence("TC-171", "Mock severity band - bottleneck",
                       "vehicle_count = 30", ("bottleneck", "#8b31c7"), result)
        self.assertEqual(result, ("bottleneck", "#8b31c7"))
        self.assertEqual(_severity_for_count(100), ("bottleneck", "#8b31c7"))

    def test_negative_count_is_free(self) -> None:
        result = _severity_for_count(-5)
        print_evidence("TC-172", "Negative mock vehicle count stays 'free'",
                       "vehicle_count = -5", ("free", "#23c55e"), result)
        self.assertEqual(result, ("free", "#23c55e"))


class ClampTests(unittest.TestCase):
    def test_value_within_range_is_unchanged(self) -> None:
        result = _clamp(5, 0, 10)
        print_evidence("TC-173", "Clamp leaves an in-range value unchanged",
                       "value=5, range=[0,10]", 5, result)
        self.assertEqual(result, 5)

    def test_value_below_range_is_raised_to_lo(self) -> None:
        result = _clamp(-5, 0, 10)
        print_evidence("TC-174", "Clamp raises a below-range value to the floor",
                       "value=-5, range=[0,10]", 0, result)
        self.assertEqual(result, 0)

    def test_value_above_range_is_lowered_to_hi(self) -> None:
        result = _clamp(50, 0, 10)
        print_evidence("TC-175", "Clamp lowers an above-range value to the ceiling",
                       "value=50, range=[0,10]", 10, result)
        self.assertEqual(result, 10)

    def test_boundary_values_are_kept(self) -> None:
        low = _clamp(0, 0, 10)
        high = _clamp(10, 0, 10)
        print_evidence("TC-176", "Clamp keeps values exactly at the boundary",
                       "value=0 and value=10, range=[0,10]",
                       {"low": 0, "high": 10}, {"low": low, "high": high})
        self.assertEqual(low, 0)
        self.assertEqual(high, 10)


class BuildScenarioTests(unittest.TestCase):
    def test_scenario_has_one_entry_per_stage(self) -> None:
        scenario = _build_scenario()
        result = len(scenario)
        print_evidence("TC-177", "Scenario has one entry per traffic-cycle stage",
                       "_build_scenario()", 7, result)  # free/moderate/heavy/bottleneck/heavy/moderate/free
        self.assertEqual(result, 7)

    def test_each_target_count_falls_within_its_bands_range(self) -> None:
        expected_bands = [
            SEVERITY_LEVELS[0], SEVERITY_LEVELS[1], SEVERITY_LEVELS[2], SEVERITY_LEVELS[3],
            SEVERITY_LEVELS[2], SEVERITY_LEVELS[1], SEVERITY_LEVELS[0],
        ]
        violations = []
        for _ in range(20):  # random dwell/target - run several times for confidence
            scenario = _build_scenario()
            for (target, _dwell), (_, _, lo, hi) in zip(scenario, expected_bands):
                if not (lo <= target <= hi):
                    violations.append((target, lo, hi))
        print_evidence("TC-178", "Every scenario target count stays within its declared band",
                       "20 generated scenarios x 7 stages", [], violations)
        self.assertEqual(violations, [])

    def test_dwell_frames_are_positive_and_within_expected_scale(self) -> None:
        max_possible_frames = int(DWELL_MAX / TICK_INTERVAL) + 1
        frames = [dwell for _target, dwell in _build_scenario()]
        in_range = all(1 <= f <= max_possible_frames for f in frames)
        print_evidence("TC-179", "Dwell frame counts stay within the expected scale",
                       f"dwell in seconds in [{DWELL_MIN}, {DWELL_MAX}]", True, in_range)
        for dwell_frames in frames:
            self.assertGreaterEqual(dwell_frames, 1)
            self.assertLessEqual(dwell_frames, max_possible_frames)

    def test_dwell_seconds_are_at_least_the_configured_minimum(self) -> None:
        min_possible_frames = max(1, int(DWELL_MIN / TICK_INTERVAL))
        frames = [dwell for _target, dwell in _build_scenario()]
        result = min(frames)
        print_evidence("TC-180", "Dwell duration respects the configured minimum",
                       f"DWELL_MIN = {DWELL_MIN}s", f">= {min_possible_frames - 1} frames",
                       f"{result} frames", passed=result >= min_possible_frames - 1)
        for dwell_frames in frames:
            self.assertGreaterEqual(dwell_frames, min_possible_frames - 1)


class RunMockPipelineSmokeTests(unittest.TestCase):
    """A short slice of the infinite generator, with time.sleep mocked out so
    the test doesn't actually wait for it."""

    def test_yields_well_formed_detection_dicts(self) -> None:
        with patch("detection.mock_pipeline.time.sleep", return_value=None):
            gen = run_mock_pipeline(camera_id="test_cam", frame_shape=[100, 200])
            results = list(itertools.islice(gen, 5))

        print_evidence("TC-181", "Generator yields well-formed detection dicts",
                       "camera_id='test_cam', frame_shape=[100,200], 5 frames requested",
                       {"count": 5, "camera_id": "test_cam", "frame_shape": [100, 200]},
                       {"count": len(results), "camera_id": results[0]["camera_id"],
                        "frame_shape": results[0]["frame_shape"]})
        self.assertEqual(len(results), 5)
        for result in results:
            self.assertEqual(result["camera_id"], "test_cam")
            self.assertEqual(result["frame_shape"], [100, 200])
            self.assertIn(result["severity"], {"free", "moderate", "heavy", "bottleneck"})
            self.assertGreaterEqual(result["vehicle_count"], 0)
            self.assertIn("timestamp", result)
            self.assertIn("fps_processed", result)

    def test_default_frame_shape_is_used_when_not_provided(self) -> None:
        with patch("detection.mock_pipeline.time.sleep", return_value=None):
            gen = run_mock_pipeline(camera_id="test_cam")
            result = next(gen)
        print_evidence("TC-182", "Default frame_shape is used when not provided",
                       "frame_shape not passed", [720, 1280], result["frame_shape"])
        self.assertEqual(result["frame_shape"], [720, 1280])


if __name__ == "__main__":
    unittest.main()
