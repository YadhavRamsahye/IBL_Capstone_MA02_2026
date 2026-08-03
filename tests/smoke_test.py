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
            with TestClient(app_main.app) as client:
                home = client.get("/")
                self.assertEqual(home.status_code, 200)

                status = client.get("/api/status")
                self.assertEqual(status.status_code, 200)

                payload = status.json()
                self.assertEqual(payload["active_cameras"], 0)
                self.assertEqual(payload["total_detections"], 0)
                self.assertEqual(payload["bottlenecks"], 0)

                traffic = client.get("/api/traffic")
                self.assertEqual(traffic.status_code, 200)
                self.assertEqual(traffic.json(), {})


if __name__ == "__main__":
    unittest.main()
