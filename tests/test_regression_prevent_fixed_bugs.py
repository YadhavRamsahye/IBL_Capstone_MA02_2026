"""
Regression tests for previously fixed bugs in the traffic monitoring app.

This file locks in behavior for bug fixes that are important to prevent
from reappearing, such as repeated explicit alert triggers each creating
their own alert (not being deduplicated) and invalid vehicle count
classification.

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
from evidence import print_evidence


class RegressionPreventionTests(unittest.TestCase):
    """Tests that lock in prior bug fixes to prevent regressions."""

    def setUp(self) -> None:
        """Reset shared app state before each regression scenario."""
        app_main.latest_detections.clear()
        app_main.alert_log.clear()
        app_main._last_severity.clear()
        app_main._last_summary_time.clear()

    def test_repeated_alert_triggers_create_distinct_alerts(self) -> None:
        """Verify each explicit alert trigger is retained in the alert log."""
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

            first = asyncio.run(first_alert)
            second = asyncio.run(second_alert)

            print_evidence("TC-057", "Repeated explicit alert triggers create distinct alerts",
                           "2 explicit triggers for camera_id = 'cam1'",
                           {"distinct_ids": True, "alert_log_len": 2},
                           {"distinct_ids": first["alert_id"] != second["alert_id"],
                            "alert_log_len": len(app_main.alert_log)})
            self.assertNotEqual(first["alert_id"], second["alert_id"])
            self.assertEqual(len(app_main.alert_log), 2)

    def test_negative_vehicle_count_cannot_be_classified_as_congestion(self) -> None:
        """Ensure negative input does not produce a congestion severity."""
        from detection.severity import classify_count

        result = classify_count(-5, "unknown_camera")[:2]
        print_evidence("TC-058", "Regression: negative vehicle count stays 'free'",
                       "vehicle_count = -5", ("free", "#23c55e"), result)
        self.assertEqual(result, ("free", "#23c55e"))


if __name__ == "__main__":
    unittest.main()
