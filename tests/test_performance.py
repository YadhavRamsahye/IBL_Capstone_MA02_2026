"""Small repeatable performance probe for an authenticated API route."""

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import main as app_main
from evidence import print_performance_evidence


class ApiPerformanceTests(unittest.TestCase):
    """Record response-time statistics without imposing a machine-specific SLA."""

    def test_status_endpoint_100_requests(self) -> None:
        """Send 100 requests and report average, maximum, and p95 latency."""
        async def no_op_camera_loop(camera_id: str) -> None:
            return None

        app_main.app.dependency_overrides[app_main.require_user] = lambda: {
            "username": "perf", "role": "user", "id": "perf",
        }
        try:
            with patch("main.discover_cameras", return_value=[]), patch(
                "main._camera_loop", side_effect=no_op_camera_loop,
            ):
                with TestClient(app_main.app) as client:
                    timings_s = []
                    successful = 0
                    for _ in range(100):
                        started = time.perf_counter()
                        response = client.get("/api/status")
                        timings_s.append(time.perf_counter() - started)
                        if response.status_code == 200:
                            successful += 1

            passed = successful == 100 and len(timings_s) == 100
            print_performance_evidence(
                "TC-056", "API status endpoint latency under 100 sequential requests",
                endpoint="GET /api/status", total_requests=100,
                successful_requests=successful, response_times=timings_s, passed=passed,
            )
            self.assertEqual(successful, 100)
            self.assertEqual(len(timings_s), 100)
        finally:
            app_main.app.dependency_overrides.pop(app_main.require_user, None)


if __name__ == "__main__":
    unittest.main()
