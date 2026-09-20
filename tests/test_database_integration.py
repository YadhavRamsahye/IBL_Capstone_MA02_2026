"""Opt-in PostgreSQL integration tests for persistence contracts."""

import asyncio
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import auth
import database
import main as app_main
from evidence import print_db_evidence, print_evidence


@unittest.skipUnless(
    database.DB_AVAILABLE and database.AsyncSessionLocal is not None,
    "PostgreSQL is not configured or reachable",
)
class DatabaseIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Verify detection, alert, and user writes can be read back from PostgreSQL."""

    async def asyncSetUp(self) -> None:
        suffix = uuid.uuid4().hex[:12]
        self.camera_id = f"test_db_{suffix}"
        self.username = f"test_user_{suffix}"
        self.detection = {
            "camera_id": self.camera_id,
            "vehicle_count": 12,
            "pcu": 12.0,
            "saturation": 0.6,
            "severity": "heavy",
            "color": "#e94560",
            "fps_processed": 1.0,
            "timestamp": "2026-09-16T12:00:00+00:00",
        }

    async def asyncTearDown(self) -> None:
        async with database.AsyncSessionLocal() as db:
            await db.execute(text("DELETE FROM alerts WHERE camera_id = :camera"), {"camera": self.camera_id})
            await db.execute(text("DELETE FROM traffic_snapshots WHERE camera_id = :camera"), {"camera": self.camera_id})
            await db.execute(text("DELETE FROM bottleneck_events WHERE camera_id = :camera"), {"camera": self.camera_id})
            await db.execute(text("DELETE FROM incidents WHERE camera_id = :camera"), {"camera": self.camera_id})
            # Must run before the users delete below - password_resets has a FK
            # on user_id with no cascade, so an orphaned reset row would abort
            # the user delete for any test that issued one.
            await db.execute(text(
                "DELETE FROM password_resets WHERE user_id = "
                "(SELECT id FROM users WHERE username = :username)"
            ), {"username": self.username})
            await db.execute(text("DELETE FROM users WHERE username = :username"), {"username": self.username})
            await db.execute(text("DELETE FROM cameras WHERE id = :camera"), {"camera": self.camera_id})
            await db.commit()

    async def test_detection_snapshot_is_persisted(self) -> None:
        """A detection write creates a queryable traffic_snapshots row."""
        await app_main._save_snapshot(self.camera_id, self.detection)
        async with database.AsyncSessionLocal() as db:
            row = (await db.execute(text(
                "SELECT vehicle_count, severity FROM traffic_snapshots "
                "WHERE camera_id = :camera ORDER BY snapshot_time DESC LIMIT 1"
            ), {"camera": self.camera_id})).first()
        print_db_evidence("TC-045", "Detection write is persisted to traffic_snapshots",
                          context={"Camera ID": self.camera_id},
                          expected={"vehicle_count": 12, "severity": "heavy"},
                          actual={"vehicle_count": row.vehicle_count if row else None,
                                  "severity": row.severity if row else None})
        self.assertIsNotNone(row)
        self.assertEqual(row.vehicle_count, 12)
        self.assertEqual(row.severity, "heavy")

    async def test_alert_is_persisted(self) -> None:
        """An explicitly triggered alert creates a queryable alerts row."""
        app_main.latest_detections[self.camera_id] = self.detection
        with patch.object(
            app_main.summary_service,
            "generate_alert_description",
            new=AsyncMock(return_value=type("Summary", (), {
                "summary": "Heavy traffic", "source": "template_fallback",
            })()),
        ):
            await app_main.api_alerts_trigger(
                app_main.AlertTriggerRequest(camera_id=self.camera_id, message="Heavy traffic")
            )
        async with database.AsyncSessionLocal() as db:
            row = (await db.execute(text(
                "SELECT severity, message FROM alerts "
                "WHERE camera_id = :camera ORDER BY id DESC LIMIT 1"
            ), {"camera": self.camera_id})).first()
        print_db_evidence("TC-046", "Explicitly triggered alert is persisted to alerts",
                          context={"Camera ID": self.camera_id},
                          expected={"severity": "heavy", "message": "Heavy traffic"},
                          actual={"severity": row.severity if row else None,
                                  "message": row.message if row else None})
        self.assertIsNotNone(row)
        self.assertEqual(row.severity, "heavy")
        self.assertEqual(row.message, "Heavy traffic")

    async def test_user_is_persisted(self) -> None:
        """Account creation stores a user record that can be queried back."""
        ok, message = await auth.create_user(
            self.username, "DatabasePassword1", email=f"{self.username}@example.com"
        )
        self.assertTrue(ok, message)
        async with database.AsyncSessionLocal() as db:
            row = (await db.execute(text(
                "SELECT username, role, email FROM users WHERE username = :username"
            ), {"username": self.username})).first()
        print_db_evidence("TC-047", "Account creation is persisted to users",
                          context={"Username": self.username},
                          expected={"role": "user", "email": f"{self.username}@example.com"},
                          actual={"role": row.role if row else None,
                                  "email": row.email if row else None})
        self.assertIsNotNone(row)
        self.assertEqual(row.username, self.username)
        self.assertEqual(row.role, "user")
        self.assertEqual(row.email, f"{self.username}@example.com")

    async def test_password_reset_round_trip_succeeds(self) -> None:
        """A user can request, verify, and consume a reset token exactly once,
        and the new password (not the old one) authenticates afterwards."""
        email = f"{self.username}@example.com"
        ok, message = await auth.create_user(self.username, "OriginalPassw0rd", email=email)
        self.assertTrue(ok, message)

        user, token = await auth.create_password_reset(self.username)
        self.assertIsNotNone(token)
        self.assertEqual(user["username"], self.username)

        verified = await auth.verify_reset_token(token)
        self.assertIsNotNone(verified)
        self.assertEqual(verified["username"], self.username)

        changed, change_message = await auth.consume_reset_token(token, "BrandNewPassw0rd")
        self.assertTrue(changed, change_message)

        new_auth = await auth.authenticate(self.username, "BrandNewPassw0rd")
        old_auth = await auth.authenticate(self.username, "OriginalPassw0rd")
        print_evidence("TC-048", "Password reset round trip - new password works, old is revoked",
                       f"reset token consumed for {self.username}",
                       {"new_password_authenticates": True, "old_password_authenticates": False},
                       {"new_password_authenticates": new_auth is not None,
                        "old_password_authenticates": old_auth is not None})
        self.assertIsNotNone(new_auth)
        self.assertIsNone(old_auth)

    async def test_password_reset_token_cannot_be_replayed(self) -> None:
        """A spent token is rejected by both verification and a second consume."""
        email = f"{self.username}@example.com"
        await auth.create_user(self.username, "OriginalPassw0rd", email=email)
        _, token = await auth.create_password_reset(self.username)

        first_ok, _ = await auth.consume_reset_token(token, "FirstNewPassw0rd")
        self.assertTrue(first_ok)

        verify_after_use = await auth.verify_reset_token(token)
        replay_ok, replay_message = await auth.consume_reset_token(token, "SecondNewPassw0rd")
        print_evidence("TC-049", "A spent reset token cannot be replayed",
                       "consume_reset_token() called twice with the same token",
                       {"verify_after_use": None, "second_consume_ok": False},
                       {"verify_after_use": verify_after_use, "second_consume_ok": replay_ok})
        self.assertIsNone(verify_after_use)
        self.assertFalse(replay_ok)
        self.assertIn("already been used", replay_message)


if __name__ == "__main__":
    unittest.main()
