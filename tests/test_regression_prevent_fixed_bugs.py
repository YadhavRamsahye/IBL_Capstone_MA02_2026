"""
Regression tests for previously fixed bugs in the traffic monitoring app.

This file locks in behavior for bug fixes that are important to prevent
from reappearing, such as duplicate alert suppression and invalid vehicle
count classification.

Each test is written around a concrete fix and ensures the system continues
to enforce the corrected behavior.
"""

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import main as app_main


class RegressionPreventionTests(unittest.TestCase):
    """Tests that lock in prior bug fixes to prevent regressions."""

    def setUp(self) -> None:
        """Reset shared app state before each regression scenario."""
        app_main.latest_detections.clear()
        app_main.alert_log.clear()
        app_main._last_severity.clear()
        app_main._last_summary_time.clear()

    def test_duplicate_alerts_still_suppressed_after_previous_fix(self) -> None:
        """Verify duplicate alert suppression remains in effect when alerts are triggered twice."""
        app_main.latest_detections["cam1"] = {
            "camera_id": "cam1",
            "timestamp": "2026-07-15T00:00:00+00:00",
            "vehicle_count": 20,
            "severity": "heavy",
            "color": "#e94560",
            "fps_processed": 1.0,
            "frame_shape": [720, 1280],
        }

        with patch.object(
            app_main.summary_service,
            "generate_alert_description",
            new=AsyncMock(return_value=SimpleNamespace(summary="Heavy congestion ahead", source="template_fallback")),
        ):
            first_alert = app_main.api_alerts_trigger(app_main.AlertTriggerRequest(camera_id="cam1", message=""))
            second_alert = app_main.api_alerts_trigger(app_main.AlertTriggerRequest(camera_id="cam1", message=""))

            self.assertEqual(asyncio.run(first_alert)["alert_id"], asyncio.run(second_alert)["alert_id"])
            self.assertEqual(len(app_main.alert_log), 1)

    def test_negative_vehicle_count_classification_remains_invalid(self) -> None:
        """Ensure the classification routine rejects negative vehicle counts as invalid."""
        from detection.pipeline import _classify

        with self.assertRaises(ValueError):
            _classify(-5)


if __name__ == "__main__":
    unittest.main()
