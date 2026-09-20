"""Locust load profile for authenticated Traffic Watch API traffic.

Run with:
    locust -f locustfile.py --host http://localhost:8000

Set LOADTEST_USERNAME and LOADTEST_PASSWORD in the environment. The profile
logs in once per simulated user, then exercises the read-heavy dashboard APIs.
"""

import os

from locust import HttpUser, between, task


class TrafficWatchUser(HttpUser):
    """Simulated dashboard user with a realistic read mix."""

    wait_time = between(1, 3)

    def on_start(self) -> None:
        username = os.getenv("LOADTEST_USERNAME")
        password = os.getenv("LOADTEST_PASSWORD")
        if not username or not password:
            raise RuntimeError(
                "Set LOADTEST_USERNAME and LOADTEST_PASSWORD before starting Locust."
            )
        response = self.client.post(
            "/login",
            data={"username": username, "password": password},
            name="POST /login",
            allow_redirects=False,
        )
        if response.status_code != 302:
            raise RuntimeError(f"Load-test login failed with HTTP {response.status_code}")

    @task(5)
    def status(self) -> None:
        self.client.get("/api/status", name="GET /api/status")

    @task(4)
    def traffic(self) -> None:
        self.client.get("/api/traffic", name="GET /api/traffic")

    @task(2)
    def cameras(self) -> None:
        self.client.get("/api/cameras", name="GET /api/cameras")

    @task(1)
    def incidents(self) -> None:
        self.client.get("/api/incidents/active", name="GET /api/incidents/active")
