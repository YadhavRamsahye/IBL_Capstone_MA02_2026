"""
Concurrency tests for the traffic monitoring app.

This file simulates many concurrent, explicit alert-trigger requests for the
same camera and verifies that shared in-memory state (alert_log, alert IDs)
stays correct under parallel load - no lost updates, no duplicate/collided
IDs, no dropped alerts from a race on the shared log.

Key behavior tested:
- many concurrent explicit alert triggers for one camera
- every request is retained as its own alert (explicit triggers are never
  deduplicated - see test_regression_prevent_fixed_bugs.py)
- each alert gets a distinct ID and the shared alert_log ends up with exactly
  one entry per request, with no entries lost or corrupted by the race
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
from evidence import print_evidence


class ConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    """Concurrency tests for parallel alert creation."""

    async def asyncSetUp(self) -> None:
        """Reset shared runtime state before each concurrency scenario."""
        app_main.latest_detections.clear()
        app_main.alert_log.clear()
        app_main._last_severity.clear()
        app_main._last_summary_time.clear()
        app_main.incident_detector = app_main.IncidentDetector()

    async def test_simultaneous_alert_generation_for_single_camera(self) -> None:
        """Issue many concurrent alert triggers and retain each explicit request."""
        app_main.latest_detections["cam1"] = {
            "camera_id": "cam1",
            "timestamp": "2026-07-15T00:00:00+00:00",
            "vehicle_count": 20,
            "severity": "heavy",
            "color": "#e94560",
            "fps_processed": 1.0,
            "frame_shape": [720, 1280],
        }

        async def delayed_alert_description(detection, live_severity=None):
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

        alert_ids = {alert["alert_id"] for alert in alerts}
        print_evidence("TC-044", "100 concurrent alert triggers each get a distinct ID",
                       "100 concurrent api_alerts_trigger() calls for camera_id = 'cam1'",
                       {"alerts_returned": 100, "distinct_ids": 100, "alert_log_len": 100},
                       {"alerts_returned": len(alerts), "distinct_ids": len(alert_ids),
                        "alert_log_len": len(app_main.alert_log)})
        self.assertEqual(len(alerts), 100)
        self.assertEqual(len(alert_ids), 100)
        self.assertEqual(len(app_main.alert_log), 100)

        self.assertEqual(app_main.alert_log[0]["camera_id"], "cam1")
        self.assertEqual(app_main.alert_log[0]["severity"], "heavy")
        self.assertEqual(app_main.alert_log[0]["message"], "Heavy congestion ahead")
