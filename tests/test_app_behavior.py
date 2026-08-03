"""
Web application behavior tests for the FastAPI traffic monitoring app.

This file validates the behavior of API routes and UI-facing payloads,
including:
- traffic lookup success and failure
- status aggregation and bottleneck counts
- camera list enrichment with severity/color state
- alert triggering and alert log updates
- duplicate alert suppression at the API level
- invalid payload rejection and error handling

These tests ensure the app routes behave as expected under normal and
edge-case traffic scenarios.
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
from detection.incident_detector import IncidentDetector


class WebAppBehaviorTests(unittest.TestCase):
    """Behavioral tests that validate the app's API payloads and response logic."""

    def setUp(self) -> None:
        """Reset all in-memory app state for each API behavior test."""
        app_main.latest_detections.clear()
        app_main.alert_log.clear()
        app_main._active_cameras.clear()
        app_main._last_severity.clear()
        app_main._last_summary_time.clear()
        app_main.incident_detector = IncidentDetector()

    def test_unknown_camera_returns_not_found_for_traffic_lookup(self) -> None:
        """Confirm traffic lookup raises a 404 for cameras that are not registered."""
        with self.assertRaises(HTTPException) as context:
            asyncio.run(app_main.api_traffic_camera("missing_camera"))

        self.assertEqual(context.exception.status_code, 404)

    def test_known_camera_returns_latest_detection_for_traffic_lookup(self) -> None:
        """Verify traffic lookup returns the latest stored detection for a known camera."""
        app_main.latest_detections["cam1"] = {
            "camera_id": "cam1",
            "timestamp": "2026-07-13T00:00:00+00:00",
            "vehicle_count": 12,
            "severity": "moderate",
            "color": "#f0883e",
            "fps_processed": 1.0,
            "frame_shape": [720, 1280],
        }

        traffic = asyncio.run(app_main.api_traffic_camera("cam1"))

        self.assertEqual(traffic["camera_id"], "cam1")
        self.assertEqual(traffic["vehicle_count"], 12)
        self.assertEqual(traffic["severity"], "moderate")

    def test_invalid_alert_payload_is_rejected(self) -> None:
        """Ensure the alert trigger route rejects malformed or missing camera IDs."""
        with self.assertRaises(HTTPException) as context:
            asyncio.run(app_main.api_alerts_trigger(app_main.AlertTriggerRequest(camera_id="")))

        self.assertEqual(context.exception.status_code, 404)

    def test_heat_map_status_and_camera_payload_reflect_latest_detections(self) -> None:
        """Check that status and camera list payloads reflect the latest detection state."""
        app_main._active_cameras = [{
            "camera_id": "cam1",
            "name": "Cam 1",
            "lat": -20.16,
            "lng": 57.49,
            "source": "mock",
            "validated": True,
        }]
        app_main.latest_detections["cam1"] = {
            "camera_id": "cam1",
            "timestamp": "2026-07-13T00:00:00+00:00",
            "vehicle_count": 40,
            "severity": "bottleneck",
            "color": "#8b31c7",
            "fps_processed": 1.0,
            "frame_shape": [720, 1280],
        }

        status = asyncio.run(app_main.api_status())
        cameras = asyncio.run(app_main.api_cameras())

        self.assertEqual(status["total_detections"], 40)
        self.assertEqual(status["bottlenecks"], 1)
        self.assertEqual(cameras[0]["current_count"], 40)
        self.assertEqual(cameras[0]["current_severity"], "bottleneck")
        self.assertEqual(cameras[0]["current_color"], "#8b31c7")

    def test_status_reports_zero_bottlenecks_when_none_are_present(self) -> None:
        """Verify the status endpoint reports zero bottlenecks when traffic remains moderate or lower."""
        app_main._active_cameras = [{
            "camera_id": "cam1",
            "name": "Cam 1",
            "lat": -20.16,
            "lng": 57.49,
            "source": "mock",
            "validated": True,
        }]
        app_main.latest_detections["cam1"] = {
            "camera_id": "cam1",
            "timestamp": "2026-07-13T00:00:00+00:00",
            "vehicle_count": 6,
            "severity": "moderate",
            "color": "#f0883e",
            "fps_processed": 1.0,
            "frame_shape": [720, 1280],
        }

        status = asyncio.run(app_main.api_status())
        self.assertEqual(status["bottlenecks"], 0)
        self.assertEqual(status["total_detections"], 6)

    def test_empty_traffic_system_returns_zeroed_status(self) -> None:
        """Ensure the status endpoint returns zeroed aggregates when no cameras or detections exist."""
        app_main.latest_detections.clear()
        app_main._active_cameras.clear()

        status = asyncio.run(app_main.api_status())
        cameras = asyncio.run(app_main.api_cameras())

        self.assertEqual(status["active_cameras"], 0)
        self.assertEqual(status["total_detections"], 0)
        self.assertEqual(status["bottlenecks"], 0)
        self.assertEqual(cameras, [])

    def test_api_traffic_all_returns_latest_detections(self) -> None:
        """Confirm the traffic aggregate route returns the latest detection payloads."""
        app_main.latest_detections["cam1"] = {
            "camera_id": "cam1",
            "timestamp": "2026-07-13T00:00:00+00:00",
            "vehicle_count": 12,
            "severity": "moderate",
            "color": "#f0883e",
            "fps_processed": 1.0,
            "frame_shape": [720, 1280],
        }

        traffic = asyncio.run(app_main.api_traffic_all())

        self.assertIn("cam1", traffic)
        self.assertEqual(traffic["cam1"]["vehicle_count"], 12)
        self.assertEqual(traffic["cam1"]["severity"], "moderate")

    def test_alert_trigger_creates_notification_and_updates_alert_log(self) -> None:
        """Verify that alert triggering creates a notification and appends it to the alert log."""
        app_main.latest_detections["cam1"] = {
            "camera_id": "cam1",
            "timestamp": "2026-07-13T00:00:00+00:00",
            "vehicle_count": 18,
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
            alert = asyncio.run(app_main.api_alerts_trigger(app_main.AlertTriggerRequest(camera_id="cam1", message="")))

        self.assertEqual(alert["camera_id"], "cam1")
        self.assertEqual(alert["severity"], "heavy")
        self.assertEqual(alert["message"], "Heavy congestion ahead")
        self.assertEqual(app_main.alert_log[-1]["alert_id"], alert["alert_id"])

        alerts = asyncio.run(app_main.api_alerts_list(limit=5))
        self.assertEqual(alerts[0]["alert_id"], alert["alert_id"])

    def test_alert_severity_and_color_follow_detection_state(self) -> None:
        """Check that alert metadata reflects the current detection severity and color."""
        app_main.latest_detections["cam1"] = {
            "camera_id": "cam1",
            "timestamp": "2026-07-13T00:00:00+00:00",
            "vehicle_count": 40,
            "severity": "bottleneck",
            "color": "#8b31c7",
            "fps_processed": 1.0,
            "frame_shape": [720, 1280],
        }

        with patch.object(
            app_main.summary_service,
            "generate_alert_description",
            new=AsyncMock(return_value=SimpleNamespace(summary="Severe congestion", source="template_fallback")),
        ):
            alert = asyncio.run(app_main.api_alerts_trigger(app_main.AlertTriggerRequest(camera_id="cam1", message="")))

        self.assertEqual(alert["severity"], "bottleneck")
        self.assertEqual(alert["color"], "#8b31c7")
        self.assertEqual(alert["vehicle_count"], 40)

    def test_duplicate_alerts_are_suppressed(self) -> None:
        """Verify duplicate alert trigger attempts return the same alert and do not create a second entry."""
        app_main.latest_detections["cam1"] = {
            "camera_id": "cam1",
            "timestamp": "2026-07-13T00:00:00+00:00",
            "vehicle_count": 18,
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
            first_alert = asyncio.run(app_main.api_alerts_trigger(app_main.AlertTriggerRequest(camera_id="cam1", message="")))
            second_alert = asyncio.run(app_main.api_alerts_trigger(app_main.AlertTriggerRequest(camera_id="cam1", message="")))

        self.assertEqual(first_alert["alert_id"], second_alert["alert_id"])
        self.assertEqual(len(app_main.alert_log), 1)


if __name__ == "__main__":
    unittest.main()