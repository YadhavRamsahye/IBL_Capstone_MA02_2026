"""
Concurrency tests for the traffic monitoring app.

This file simulates concurrent alert generation for the same camera and
verifies that duplicate alert suppression remains correct under parallel
load. It exercises shared in-memory state and helps detect race-conditions
in alert creation logic.

Key behavior tested:
- multiple concurrent requests to trigger an alert for one camera
- only one alert is persisted
- duplicate alerts are suppressed reliably
"""

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import main as app_main


class ConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    """Concurrency tests that verify duplicate suppression under parallel alert load."""

    async def asyncSetUp(self) -> None:
        """Reset shared runtime state before each concurrency scenario."""
        app_main.latest_detections.clear()
        app_main.alert_log.clear()
        app_main._last_severity.clear()
        app_main._last_summary_time.clear()
        app_main.incident_detector = app_main.IncidentDetector()

    async def test_simultaneous_alert_generation_for_single_camera(self) -> None:
        """Issue many concurrent alert triggers and assert only one actual alert is recorded."""
        app_main.latest_detections["cam1"] = {
            "camera_id": "cam1",
            "timestamp": "2026-07-15T00:00:00+00:00",
            "vehicle_count": 20,
            "severity": "heavy",
            "color": "#e94560",
            "fps_processed": 1.0,
            "frame_shape": [720, 1280],
        }

        async def delayed_alert_description(detection):
            """Simulated async summary generation to increase concurrency contention."""
            await asyncio.sleep(0.01)
            return SimpleNamespace(summary="Heavy congestion ahead", source="template_fallback")

        with patch.object(
            app_main.summary_service,
            "generate_alert_description",
            new=AsyncMock(side_effect=delayed_alert_description),
        ):
            tasks = [
                app_main.api_alerts_trigger(app_main.AlertTriggerRequest(camera_id="cam1", message=""))
                for _ in range(100)
            ]
            alerts = await asyncio.gather(*tasks)

        self.assertEqual(len(alerts), 100)
        alert_ids = {alert["alert_id"] for alert in alerts}
        self.assertEqual(len(alert_ids), 1)
        self.assertEqual(len(app_main.alert_log), 1)

        self.assertEqual(app_main.alert_log[0]["camera_id"], "cam1")
        self.assertEqual(app_main.alert_log[0]["severity"], "heavy")
        self.assertEqual(app_main.alert_log[0]["message"], "Heavy congestion ahead")
