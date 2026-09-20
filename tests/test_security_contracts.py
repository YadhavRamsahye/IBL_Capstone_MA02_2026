"""HTTP-level security contract tests."""

import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import main as app_main
from evidence import print_security_evidence


class SecurityContractTests(unittest.TestCase):
    """Verify authentication, authorization, login, and reset-token failures."""

    @staticmethod
    @contextmanager
    def _offline_app():
        async def no_op_camera_loop(camera_id: str) -> None:
            return None

        with patch("main.discover_cameras", return_value=[]), patch(
            "main._camera_loop", side_effect=no_op_camera_loop,
        ):
            yield

    def test_admin_endpoint_rejects_missing_token(self) -> None:
        """An anonymous caller cannot reach an admin-only endpoint."""
        with self._offline_app():
            with TestClient(app_main.app) as client:
                response = client.post("/api/demo/escalate")
        print_security_evidence("TC-061", "Admin-only endpoint rejects an anonymous caller",
                                method="POST", path="/api/demo/escalate", auth="None",
                                expected_status=401, actual_status=response.status_code)
        self.assertEqual(response.status_code, 401)

    def test_normal_user_is_rejected_by_admin_endpoint(self) -> None:
        """A signed-in normal user receives 403 from an admin-only endpoint."""
        app_main.app.dependency_overrides[app_main.require_user] = lambda: {
            "username": "user", "role": "user", "id": "user",
        }
        try:
            with self._offline_app():
                with TestClient(app_main.app) as client:
                    response = client.post("/api/demo/escalate")
            print_security_evidence("TC-062", "Admin-only endpoint rejects a non-admin session",
                                    method="POST", path="/api/demo/escalate",
                                    auth="session user (role=user)",
                                    expected_status=403, actual_status=response.status_code)
            self.assertEqual(response.status_code, 403)
        finally:
            app_main.app.dependency_overrides.pop(app_main.require_user, None)

    def test_invalid_login_fails(self) -> None:
        """Wrong credentials return 401 and do not create a session."""
        with self._offline_app():
            with TestClient(app_main.app) as client:
                response = client.post(
                    "/login",
                    data={"username": "not-a-user", "password": "wrong-password"},
                    follow_redirects=False,
                )
        print_security_evidence("TC-063", "Login rejects wrong credentials",
                                method="POST", path="/login",
                                auth="username='not-a-user', password='wrong-password'",
                                expected_status=401, actual_status=response.status_code)
        self.assertEqual(response.status_code, 401)
        self.assertIn("Invalid username or password", response.text)

    def test_missing_and_invalid_reset_tokens_fail(self) -> None:
        """Missing and fabricated reset tokens render an error response."""
        with self._offline_app():
            with TestClient(app_main.app) as client:
                missing = client.get("/reset-password")
                invalid = client.get("/reset-password?token=definitely-invalid")
        print_security_evidence("TC-064", "A fabricated reset token is rejected",
                                method="GET", path="/reset-password?token=definitely-invalid",
                                auth="unauthenticated", expected_status=400,
                                actual_status=invalid.status_code)
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("invalid or has expired", invalid.text)


if __name__ == "__main__":
    unittest.main()
