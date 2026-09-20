"""Authentication contract tests for protected data routes."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import main as app_main
from evidence import print_security_evidence, print_api_evidence


class ApiAccessControlTests(unittest.TestCase):
    """Ensure API data is not readable or writable without a session."""

    def test_data_routes_require_authentication(self) -> None:
        """Anonymous callers receive 401 for every protected data surface."""
        protected_routes = [
            ("GET", "/api/status"),
            ("GET", "/api/traffic"),
            ("GET", "/api/cameras"),
            ("GET", "/api/alerts"),
            ("GET", "/api/incidents"),
        ]

        with patch("main.discover_cameras", return_value=[]):
            with TestClient(app_main.app) as client:
                for method, path in protected_routes:
                    response = client.request(method, path)
                    if path == "/api/status":
                        print_security_evidence(
                            "TC-032", "Protected data route rejects an anonymous caller",
                            method=method, path=path, auth="None",
                            expected_status=401, actual_status=response.status_code,
                        )
                    self.assertEqual(response.status_code, 401, path)

    def test_authenticated_caller_can_read_protected_data(self) -> None:
        """A valid dependency override can access the protected routes."""
        app_main.app.dependency_overrides[app_main.require_user] = lambda: {
            "username": "test", "role": "user", "id": "test",
        }
        try:
            with patch("main.discover_cameras", return_value=[]):
                with TestClient(app_main.app) as client:
                    response = client.get("/api/status")
                    print_api_evidence(
                        "TC-033", "Authenticated caller can read a protected data route",
                        method="GET", path="/api/status", expected_status=200,
                        response=response, extra={"Authentication": "session user (role=user)"},
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(client.get("/api/traffic").status_code, 200)
                    self.assertEqual(client.get("/api/cameras").status_code, 200)
                    self.assertEqual(client.get("/api/alerts").status_code, 200)
                    self.assertEqual(client.get("/api/incidents").status_code, 200)
        finally:
            app_main.app.dependency_overrides.pop(app_main.require_user, None)


if __name__ == "__main__":
    unittest.main()
