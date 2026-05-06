-- Author: Whitney Ramsamy Student ID : 22387675
-- Role: Team Leader
-- Unit   : ISAD3000 Capstone Computing Project 1
-- Team   : IBL Group — Traffic Bottleneck Detection System traffic summaries


--Create the database
CREATE DATABASE TrafficSystem;

--Use the database
\c TrafficSystem;

--Create the extension
CREATE EXTENSION postgis;

--Create enums
CREATE TYPE UserRole AS ENUM ('admin', 'user');
CREATE TYPE BottleneckSeverity AS ENUM ('free', 'moderate', 'heavy', 'bottleneck');

--Create the tables
--User Table
CREATE TABLE users (
    id UUID PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    PasswordHash TEXT NOT NULL,
    role UserRole NOT NULL DEFAULT 'user',
    CreatedAt TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    LastLogin TIMESTAMPTZ
);

--Camera table
CREATE TABLE cameras (
    id VARCHAR(50) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    location GEOGRAPHY(Point, 4326),
    StreamUrl TEXT,
    isActive BOOLEAN NOT NULL DEFAULT TRUE,
    RegisteredAt TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    LastSeen TIMESTAMPTZ
);

--Traffic Snapshot Table
CREATE TABLE TrafficSnapshots (
    id UUID PRIMARY KEY,
    CameraId VARCHAR(50) NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    SnapshotTime TIMESTAMPTZ NOT NULL,
    VehicleCount INTEGER,
    Severity VARCHAR(20),
    fpsProcessed FLOAT,
    FrameShape INTEGER[],
    RawResult JSONB
);

--Bottleneck Events Table
CREATE TABLE BottleneckEvents (
    id UUID PRIMARY KEY,
    CameraId VARCHAR(50) NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    DetectedAt TIMESTAMPTZ NOT NULL,
    Severity BottleneckSeverity NOT NULL,
    VehicleCount INTEGER,
    Color VARCHAR(10),
    Location GEOGRAPHY(Point, 4326)
);

--Alerts Table
CREATE TABLE alerts (
    id SERIAL PRIMARY KEY,
    CameraId VARCHAR(50) NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    EventId UUID NOT NULL REFERENCES BottleneckEvents(id) ON DELETE CASCADE,
    AlertType VARCHAR(50) NOT NULL,
    Severity VARCHAR(20),
    Message TEXT,
    TriggeredAt TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    acknowledged BOOLEAN NOT NULL DEFAULT FALSE
);

--Audit Log Table
CREATE TABLE AuditLog (
    id SERIAL PRIMARY KEY,
    userId UUID NOT NULL REFERENCES users(id) ON DELETE SET NULL,
    action VARCHAR(100) NOT NULL,
    target VARCHAR(100),
    IPAddress INET,
    PerformedAt TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    success BOOLEAN NOT NULL
);