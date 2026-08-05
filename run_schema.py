"""
Author : Mokshan Mehess (22703417) — Document Lead
Unit   : ISAD3000 Capstone Computing Project 1
Team   : IBL Group — Traffic Bottleneck Detection System
"""

# run_schema.py
import asyncio
import asyncpg

HOST     = "localhost"
PORT     = 5432
USER     = "postgres"
PASSWORD = "tqu9vfds"   # ← only change this
DB_NAME  = "TrafficSystem"

async def run():
    # Step 1 — create database if not exists
    conn = await asyncpg.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, database="postgres")
    exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = 'TrafficSystem'")
    if not exists:
        await conn.execute('CREATE DATABASE "TrafficSystem"')
        print("Database created.")
    else:
        print("Database already exists.")
    await conn.close()

    # Step 2 — connect to TrafficSystem
    conn = await asyncpg.connect(host=HOST, port=PORT, user=USER, password=PASSWORD, database=DB_NAME)

    # Create enums (skip if already exists)
    for stmt in [
        "CREATE TYPE \"UserRole\" AS ENUM ('admin', 'user')",
        "CREATE TYPE \"BottleneckSeverity\" AS ENUM ('free', 'moderate', 'heavy', 'bottleneck')",
    ]:
        try:
            await conn.execute(stmt)
            print(f"Created type.")
        except Exception:
            print(f"Type already exists, skipping.")

    # Create each table one by one
    tables = [
        (
            "users",
            """CREATE TABLE IF NOT EXISTS users (
                id UUID PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                "PasswordHash" TEXT NOT NULL,
                role "UserRole" NOT NULL DEFAULT 'user',
                "CreatedAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                "LastLogin" TIMESTAMPTZ
            )"""
        ),
        (
            "cameras",
            """CREATE TABLE IF NOT EXISTS cameras (
                id VARCHAR(50) PRIMARY KEY,
                name VARCHAR(100) NOT NULL,
                latitude FLOAT,
                longitude FLOAT,
                "StreamUrl" TEXT,
                "isActive" BOOLEAN NOT NULL DEFAULT TRUE,
                "RegisteredAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                "LastSeen" TIMESTAMPTZ
            )"""
        ),
        (
            "TrafficSnapshots",
            """CREATE TABLE IF NOT EXISTS "TrafficSnapshots" (
                id UUID PRIMARY KEY,
                "CameraId" VARCHAR(50) NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
                "SnapshotTime" TIMESTAMPTZ NOT NULL,
                "VehicleCount" INTEGER,
                "Severity" VARCHAR(20),
                "fpsProcessed" FLOAT,
                "FrameShape" INTEGER[],
                "RawResult" JSONB
            )"""
        ),
        (
            "BottleneckEvents",
            """CREATE TABLE IF NOT EXISTS "BottleneckEvents" (
                id UUID PRIMARY KEY,
                "CameraId" VARCHAR(50) NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
                "DetectedAt" TIMESTAMPTZ NOT NULL,
                "Severity" "BottleneckSeverity" NOT NULL,
                "VehicleCount" INTEGER,
                "Color" VARCHAR(10),
                latitude FLOAT,
                longitude FLOAT
            )"""
        ),
        (
            "alerts",
            """CREATE TABLE IF NOT EXISTS alerts (
                id SERIAL PRIMARY KEY,
                "CameraId" VARCHAR(50) NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
                "EventId" UUID NOT NULL REFERENCES "BottleneckEvents"(id) ON DELETE CASCADE,
                "AlertType" VARCHAR(50) NOT NULL,
                "Severity" VARCHAR(20),
                "Message" TEXT,
                "TriggeredAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                acknowledged BOOLEAN NOT NULL DEFAULT FALSE
            )"""
        ),
        (
            "AuditLog",
            """CREATE TABLE IF NOT EXISTS "AuditLog" (
                id SERIAL PRIMARY KEY,
                "userId" UUID REFERENCES users(id) ON DELETE SET NULL,
                action VARCHAR(100) NOT NULL,
                target VARCHAR(100),
                "IPAddress" INET,
                "PerformedAt" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                success BOOLEAN NOT NULL
            )"""
        ),
    ]

    for name, stmt in tables:
        try:
            await conn.execute(stmt)
            print(f"Table '{name}' created.")
        except Exception as e:
            print(f"Table '{name}' error: {e}")

    await conn.close()
    print("\nSchema done!")

asyncio.run(run())