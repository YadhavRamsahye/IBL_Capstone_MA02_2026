"""
Resilience and failure-recovery tests for the traffic monitoring application.

These tests validate how the system behaves when external dependencies fail:
- a camera loop failure is handled without crashing the web API
- the Claude summary service falls back to a template when the API is unavailable

This file helps ensure the application stays resilient under degraded conditions.
"""

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import main as app_main
from evidence import print_api_evidence, print_evidence


class ResilienceRecoveryTests(unittest.TestCase):
    """Validate that degraded dependency behavior does not break the live traffic API."""

    def setUp(self) -> None:
        """Reset in-memory app state before each test to ensure isolation."""
        app_main.latest_detections.clear()
        app_main.alert_log.clear()
        app_main._last_severity.clear()
        app_main._last_summary_time.clear()
        app_main.incident_detector = app_main.IncidentDetector()

    def test_camera_failure_triggers_recovery_path(self) -> None:
        """Simulate a camera stream failure and assert the API remains online."""

        async def failing_camera_loop(camera_id: str) -> None:
            """A mocked camera loop that fails immediately to force recovery handling."""
            raise RuntimeError("Camera stream failed")

        with patch("main.discover_cameras", return_value=[{
            "camera_id": "caudan_north",
            "name": "Caudan North",
            "lat": -20.1626,
            "lng": 57.4939,
            "source": "mock",
            "validated": True,
        }]), patch("main._camera_loop", side_effect=failing_camera_loop):
            app_main.app.dependency_overrides[app_main.require_user] = lambda: {
                "username": "test", "role": "admin", "id": "test",
            }
            try:
                with TestClient(app_main.app) as client:
                    status = client.get("/api/status")
                    print_api_evidence(
                        "TC-059", "API stays online after a camera loop crashes",
                        method="GET", path="/api/status", expected_status=200,
                        response=status,
                        extra={"Injected failure": "camera loop raises RuntimeError"},
                    )
                    self.assertEqual(status.status_code, 200)
                    self.assertEqual(status.json()["system"], "online")
            finally:
                app_main.app.dependency_overrides.pop(app_main.require_user, None)

    def test_summary_service_falls_back_when_claude_api_fails(self) -> None:
        """Force Claude API failure and verify the summary service uses fallback content."""
        detection = {
            "camera_id": "cam1",
            "vehicle_count": 40,
            "severity": "bottleneck",
            "color": "#8b31c7",
            "timestamp": "2026-07-15T00:00:00+00:00",
            "fps_processed": 1.0,
            "frame_shape": [720, 1280],
        }

        async def failing_api_call(data, prompt_type="summary"):
            """A mocked Claude API request that always fails to trigger fallback logic."""
            raise RuntimeError("Claude API unreachable")

        with patch("detection.claude_api._call_model", new=AsyncMock(side_effect=failing_api_call)), \
                patch("detection.claude_api._api_usable", return_value=True), \
                patch("detection.claude_api._is_retryable", return_value=False):
            summary = asyncio.run(app_main.summary_service.generate_summary(detection))
            print_evidence("TC-060", "Summary service falls back to a template when Claude fails",
                           "Claude API call raises RuntimeError (non-retryable)",
                           "template_fallback", summary.source)
            self.assertEqual(summary.source, "template_fallback")
            self.assertIn("Bottleneck detected at Cam1", summary.summary)


if __name__ == "__main__":
    unittest.main()
