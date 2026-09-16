"""
Integration-level workflow tests for the traffic monitoring application.

This file exercises the key application flow from startup through API
behavior, including camera discovery, camera listing, traffic lookup,
alert triggering, and alert retrieval.

Although it does not invoke a real YOLO model, it behaves like an end-to-end
integration test by using the app's own FastAPI routes and in-memory state.
"""

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
from evidence import print_api_evidence


class IntegrationWorkflowTests(unittest.TestCase):
    """Integration-style tests for API workflow and in-memory app state."""

    def test_integration_workflow(self) -> None:
        """Run the main route sequence from discovery through alerts and traffic retrieval."""
        app_main.latest_detections.clear()
        app_main.alert_log.clear()
        app_main._active_cameras.clear()
        app_main._last_severity.clear()
        app_main._last_summary_time.clear()
        app_main.incident_detector = app_main.IncidentDetector()

        async def no_op_camera_loop(camera_id: str) -> None:
            """A mocked camera loop that exits immediately so startup remains deterministic."""
            return None

        mock_cameras = [
            {
                "camera_id": "caudan_north",
                "name": "Caudan North",
                "lat": -20.1626,
                "lng": 57.4939,
                "source": "mock",
                "validated": True,
            },
            {
                "camera_id": "caudan_south",
                "name": "Caudan South",
                "lat": -20.1640,
                "lng": 57.4945,
                "source": "mock",
                "validated": True,
            },
        ]

        with patch("main.discover_cameras", return_value=mock_cameras), patch(
            "main._camera_loop",
            side_effect=no_op_camera_loop,
        ), patch.object(
            app_main.summary_service,
            "generate_alert_description",
            new=AsyncMock(return_value=SimpleNamespace(summary="Heavy congestion ahead", source="template_fallback")),
        ):
            app_main.app.dependency_overrides[app_main.require_user] = lambda: {
                "username": "test", "role": "admin", "id": "test",
            }
            try:
                with TestClient(app_main.app) as client:
                    status = client.get("/api/status")
                    print_api_evidence("TC-050", "Startup status reflects a clean workflow start",
                                       method="GET", path="/api/status",
                                       expected_status=200, response=status)
                    self.assertEqual(status.status_code, 200)
                    status_payload = status.json()
                    self.assertEqual(status_payload["active_cameras"], 0)
                    self.assertEqual(status_payload["total_detections"], 0)
                    self.assertEqual(status_payload["bottlenecks"], 0)

                    cameras = client.get("/api/cameras?include_catalogue=false")
                    print_api_evidence("TC-051", "Discovered cameras are listed",
                                       method="GET", path="/api/cameras?include_catalogue=false",
                                       expected_status=200, response=cameras)
                    self.assertEqual(cameras.status_code, 200)
                    cameras_payload = cameras.json()
                    self.assertEqual(len(cameras_payload), 2)
                    self.assertEqual(cameras_payload[0]["camera_id"], "caudan_north")

                    app_main.latest_detections["caudan_north"] = {
                        "camera_id": "caudan_north",
                        "timestamp": "2026-07-15T00:00:00+00:00",
                        "vehicle_count": 22,
                        "severity": "heavy",
                        "color": "#e94560",
                        "fps_processed": 1.0,
                        "frame_shape": [720, 1280],
                    }

                    traffic = client.get("/api/traffic/caudan_north")
                    print_api_evidence("TC-052", "Single-camera traffic lookup returns the latest reading",
                                       method="GET", path="/api/traffic/caudan_north",
                                       expected_status=200, response=traffic)
                    self.assertEqual(traffic.status_code, 200)
                    traffic_payload = traffic.json()
                    self.assertEqual(traffic_payload["camera_id"], "caudan_north")
                    self.assertEqual(traffic_payload["severity"], "heavy")

                    alert = client.post(
                        "/api/alerts/trigger",
                        json={"camera_id": "caudan_north", "message": ""},
                    )
                    print_api_evidence("TC-053", "Alert trigger creates a new alert",
                                       method="POST", path="/api/alerts/trigger",
                                       expected_status=201, response=alert)
                    self.assertEqual(alert.status_code, 201)
                    alert_payload = alert.json()
                    self.assertEqual(alert_payload["camera_id"], "caudan_north")
                    self.assertEqual(alert_payload["severity"], "heavy")
                    self.assertEqual(alert_payload["message"], "Heavy congestion ahead")

                    alerts = client.get("/api/alerts")
                    print_api_evidence("TC-054", "Alert list includes the just-triggered alert",
                                       method="GET", path="/api/alerts",
                                       expected_status=200, response=alerts)
                    self.assertEqual(alerts.status_code, 200)
                    self.assertEqual(alerts.json()[0]["alert_id"], alert_payload["alert_id"])

                    traffic_all = client.get("/api/traffic")
                    print_api_evidence("TC-055", "Traffic-all endpoint includes the monitored camera",
                                       method="GET", path="/api/traffic",
                                       expected_status=200, response=traffic_all)
                    self.assertEqual(traffic_all.status_code, 200)
                    self.assertIn("caudan_north", traffic_all.json())
            finally:
                app_main.app.dependency_overrides.pop(app_main.require_user, None)


if __name__ == "__main__":
    unittest.main()
