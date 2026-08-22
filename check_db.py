"""
check_db.py
Diagnose the database setup and say exactly which step is outstanding.

Author : Mokshan Mehess (22703417) - Document Lead
Unit   : ISAD3000 Capstone Computing Project 1
Team   : IBL Group - Traffic Bottleneck Detection System

Walks the chain in order - port open, credentials valid, database exists,
schema applied, admin user seeded, data flowing - and stops at the first
failure with the command that fixes it. Credentials come from .env; the
previous version had the password hardcoded and queried pre-rewrite column
names ("LastLogin", "isActive"), so it could not run against the current schema.

Usage:  python check_db.py
"""

import asyncio
import os
import socket
import sys

import asyncpg
from dotenv import load_dotenv

load_dotenv()

HOST     = os.getenv("DB_HOST", "localhost")
PORT     = int(os.getenv("DB_PORT", "5432"))
USER     = os.getenv("DB_USER", "postgres")
PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME  = os.getenv("DB_NAME", "trafficsystem")

EXPECTED_TABLES = {
    "users", "cameras", "traffic_snapshots", "bottleneck_events",
    "incidents", "alerts", "audit_log",
}

OK, BAD, INFO = "  [ok]  ", "  [--]  ", "  [i]   "


def fail(step: str, problem: str, fix: str) -> None:
    print(f"\n{BAD}{step}: {problem}\n\n  Fix:\n{fix}\n")
    sys.exit(1)


async def check() -> None:
    print(f"\nTarget: {USER}@{HOST}:{PORT}/{DB_NAME}\n")

    # 1 ── password configured -------------------------------------------------
    if not PASSWORD:
        fail("DB_PASSWORD", "not set in .env",
             "    Add the password you chose when installing PostgreSQL:\n"
             "        DB_PASSWORD=your-postgres-password")
    print(f"{OK}DB_PASSWORD is set")

    # 2 ── server reachable ----------------------------------------------------
    try:
        with socket.create_connection((HOST, PORT), timeout=3):
            pass
    except OSError as exc:
        fail("Server", f"nothing listening on {HOST}:{PORT} ({exc})",
             "    Install PostgreSQL:\n"
             "        winget install PostgreSQL.PostgreSQL.17\n\n"
             "    Already installed? Start the service (as Administrator):\n"
             "        Start-Service postgresql-x64-17")
    print(f"{OK}PostgreSQL is listening on {HOST}:{PORT}")

    # 3 ── credentials valid ---------------------------------------------------
    try:
        admin = await asyncpg.connect(host=HOST, port=PORT, user=USER,
                                      password=PASSWORD, database="postgres")
    except asyncpg.InvalidPasswordError:
        fail("Credentials", f"password rejected for user {USER!r}",
             "    Check DB_PASSWORD in .env matches the password set during\n"
             "    installation. To reset it, open SQL Shell (psql) and run:\n"
             "        ALTER USER postgres WITH PASSWORD 'new-password';")
    except Exception as exc:
        fail("Credentials", f"{type(exc).__name__}: {exc}",
             "    Verify DB_USER and DB_PASSWORD in .env.")
    print(f"{OK}Credentials accepted for {USER!r}")

    # 4 ── database exists -----------------------------------------------------
    try:
        exists = await admin.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", DB_NAME)
    finally:
        await admin.close()
    if not exists:
        fail("Database", f"{DB_NAME!r} does not exist",
             "    Create it and apply the schema:\n"
             "        python run_schema.py")
    print(f"{OK}Database {DB_NAME!r} exists")

    conn = await asyncpg.connect(host=HOST, port=PORT, user=USER,
                                 password=PASSWORD, database=DB_NAME)
    try:
        # 5 ── schema applied --------------------------------------------------
        rows = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        present = {r["tablename"] for r in rows}
        missing = EXPECTED_TABLES - present
        if missing:
            fail("Schema", f"missing table(s): {', '.join(sorted(missing))}",
                 "    Apply the schema (safe to re-run):\n"
                 "        python run_schema.py")
        print(f"{OK}All {len(EXPECTED_TABLES)} tables present")

        # 6 ── admin user seeded -----------------------------------------------
        users = await conn.fetch(
            "SELECT username, role, last_login FROM users ORDER BY username")
        if not users:
            fail("Users", "no accounts exist",
                 "    Create the administrator account:\n"
                 "        python seed_users.py")
        print(f"{OK}{len(users)} user account(s):")
        for u in users:
            seen = u["last_login"].strftime("%Y-%m-%d %H:%M") if u["last_login"] else "never"
            print(f"          {u['username']:<16} {u['role']:<6} last login: {seen}")

        # 7 ── data flowing ----------------------------------------------------
        snaps = await conn.fetchval("SELECT COUNT(*) FROM traffic_snapshots")
        cams  = await conn.fetchval("SELECT COUNT(*) FROM cameras")
        incs  = await conn.fetchval("SELECT COUNT(*) FROM incidents")

        print()
        if snaps == 0:
            print(f"{INFO}No snapshots recorded yet - everything above is ready.")
            print(f"{INFO}Start the app and let it run; Analytics fills in from here.")
        else:
            print(f"{OK}{snaps:,} snapshots across {cams} camera(s), {incs} incident(s)")
            recent = await conn.fetch("""
                SELECT camera_id, COUNT(*) AS n,
                       ROUND(AVG(vehicle_count)::numeric, 1) AS avg_count,
                       MAX(snapshot_time) AS latest
                  FROM traffic_snapshots
                 WHERE snapshot_time >= NOW() - INTERVAL '1 hour'
                 GROUP BY camera_id ORDER BY camera_id
            """)
            if recent:
                print("\n  Last hour:")
                for r in recent:
                    print(f"          {r['camera_id']:<16} {r['n']:>5} snapshots  "
                          f"avg {r['avg_count']:>5} vehicles  "
                          f"latest {r['latest'].strftime('%H:%M:%S')}")
            else:
                print(f"\n{INFO}Nothing in the last hour - is the detector running?")
    finally:
        await conn.close()

    print("\nDatabase is ready.\n")


if __name__ == "__main__":
    asyncio.run(check())
