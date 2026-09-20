"""
Smoke test for the FastAPI traffic monitoring application.

This file contains a lightweight coverage check that exercises the app
startup lifecycle, ensuring the server can start, accept requests, and
serve the most critical routes without requiring real camera streams.

It verifies:
- the root page is accessible
- the status endpoint returns a valid 200 response
- the traffic endpoint returns an empty dataset when no detections exist

This is intended as a fast sanity test for CI or deployment smoke checks.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import main as app_main
from evidence import print_api_evidence


class AppSmokeTests(unittest.TestCase):
    """Smoke tests for basic app startup and core endpoint availability."""

    def test_app_starts_and_serves_core_routes(self) -> None:
        """Confirm the app launches and serves the root, status, and traffic routes."""
        async def no_op_camera_loop(camera_id: str) -> None:
            """A mocked camera loop that does nothing so startup stays fast."""
            return None

        with patch("main.discover_cameras", return_value=[]), patch(
            "main._camera_loop",
            side_effect=no_op_camera_loop,
        ):
            app_main.app.dependency_overrides[app_main.require_user] = lambda: {
                "username": "test", "role": "admin", "id": "test",
            }
            try:
                with TestClient(app_main.app) as client:
                    home = client.get("/")
                    print_api_evidence("TC-029", "Root page is served", method="GET",
                                       path="/", expected_status=200, response=home)
                    self.assertEqual(home.status_code, 200)

                    status = client.get("/api/status")
                    print_api_evidence("TC-030", "Status endpoint reports a clean startup state",
                                       method="GET", path="/api/status",
                                       expected_status=200, response=status)
                    self.assertEqual(status.status_code, 200)

                    payload = status.json()
                    self.assertEqual(payload["active_cameras"], 0)
                    self.assertEqual(payload["total_detections"], 0)
                    self.assertEqual(payload["bottlenecks"], 0)

                    traffic = client.get("/api/traffic")
                    print_api_evidence("TC-031", "Traffic endpoint returns an empty dataset",
                                       method="GET", path="/api/traffic",
                                       expected_status=200, response=traffic)
                    self.assertEqual(traffic.status_code, 200)
                    self.assertEqual(traffic.json(), {})
            finally:
                app_main.app.dependency_overrides.pop(app_main.require_user, None)


if __name__ == "__main__":
    unittest.main()
