-- schema.sql — authoritative schema for the TrafficSystem database.
--
-- This file is the single definition of the schema. run_schema.py creates the
-- database and then executes this file; it no longer carries its own copy of
-- the DDL. Previously the two disagreed — schema.sql declared
-- `location GEOGRAPHY(Point,4326)` while run_schema.py and the application both
-- used plain `latitude`/`longitude` columns — so which schema you got depended
-- on which file you ran.
--
-- Identifiers are lowercase and unquoted throughout. PostgreSQL folds unquoted
-- identifiers to lowercase, so a table created as `TrafficSnapshots` is really
-- named `trafficsnapshots` and only matches a *quoted* "TrafficSnapshots" by
-- accident. Keeping everything lowercase removes that whole class of bug.
--
-- Usage:  python run_schema.py

-- ── Enums ────────────────────────────────────────────────────────────────────
DO $$ BEGIN
    CREATE TYPE user_role AS ENUM ('admin', 'user');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE bottleneck_severity AS ENUM ('free', 'moderate', 'heavy', 'bottleneck');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ── Users ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id            UUID PRIMARY KEY,
    username      VARCHAR(50) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,          -- bcrypt; never store plaintext
    role          user_role NOT NULL DEFAULT 'user',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login    TIMESTAMPTZ
);

-- ── Cameras ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cameras (
    id            VARCHAR(50) PRIMARY KEY,
    name          VARCHAR(100) NOT NULL,
    latitude      DOUBLE PRECISION,
    longitude     DOUBLE PRECISION,
    -- PCU visible when this camera's road is at practical capacity. Severity is
    -- count/capacity, so this is what makes cameras with different fields of
    -- view comparable. See detection/severity.py.
    capacity_pcu  DOUBLE PRECISION,
    stream_url    TEXT,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen     TIMESTAMPTZ
);

-- ── Traffic snapshots ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS traffic_snapshots (
    id             UUID PRIMARY KEY,
    camera_id      VARCHAR(50) NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    snapshot_time  TIMESTAMPTZ NOT NULL,
    vehicle_count  INTEGER,
    pcu            DOUBLE PRECISION,   -- passenger-car-unit weighted load
    saturation     DOUBLE PRECISION,   -- pcu / capacity_pcu; comparable across cameras
    severity       VARCHAR(20),
    fps_processed  DOUBLE PRECISION,
    is_incident    BOOLEAN NOT NULL DEFAULT FALSE,
    frame_shape    INTEGER[],
    raw_result     JSONB
);

-- The analytics endpoint groups snapshots by camera and hour over a time range;
-- without this index that becomes a full scan once the table grows.
CREATE INDEX IF NOT EXISTS idx_snapshots_camera_time
    ON traffic_snapshots (camera_id, snapshot_time DESC);
CREATE INDEX IF NOT EXISTS idx_snapshots_time
    ON traffic_snapshots (snapshot_time DESC);

-- ── Bottleneck events ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS bottleneck_events (
    id            UUID PRIMARY KEY,
    camera_id     VARCHAR(50) NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    detected_at   TIMESTAMPTZ NOT NULL,
    severity      bottleneck_severity NOT NULL,
    vehicle_count INTEGER,
    pcu           DOUBLE PRECISION,
    saturation    DOUBLE PRECISION,
    color         VARCHAR(10),
    latitude      DOUBLE PRECISION,
    longitude     DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS idx_events_camera_time
    ON bottleneck_events (camera_id, detected_at DESC);

-- ── Incidents ────────────────────────────────────────────────────────────────
-- Previously incidents lived only in memory and were lost on restart, so the
-- incident history shown in the UI could never outlive the process.
CREATE TABLE IF NOT EXISTS incidents (
    id            UUID PRIMARY KEY,
    camera_id     VARCHAR(50) NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    type          VARCHAR(50) NOT NULL,
    severity      VARCHAR(20),
    confidence    DOUBLE PRECISION,
    vehicle_count INTEGER,
    detected_at   TIMESTAMPTZ NOT NULL,
    resolved_at   TIMESTAMPTZ,
    description   TEXT,
    detail        JSONB
);

CREATE INDEX IF NOT EXISTS idx_incidents_camera_time
    ON incidents (camera_id, detected_at DESC);

-- ── Alerts ───────────────────────────────────────────────────────────────────
-- event_id is nullable: alerts are raised directly from live detections, which
-- do not always have a persisted bottleneck_event to point at. The old NOT NULL
-- reference made every such alert unwritable.
CREATE TABLE IF NOT EXISTS alerts (
    id           SERIAL PRIMARY KEY,
    camera_id    VARCHAR(50) NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    event_id     UUID REFERENCES bottleneck_events(id) ON DELETE CASCADE,
    alert_type   VARCHAR(50) NOT NULL,
    severity     VARCHAR(20),
    message      TEXT,
    triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    acknowledged BOOLEAN NOT NULL DEFAULT FALSE
);

-- ── Audit log ────────────────────────────────────────────────────────────────
-- user_id is nullable so failed logins (no known user) can still be recorded —
-- that is precisely the event worth auditing.
CREATE TABLE IF NOT EXISTS audit_log (
    id           SERIAL PRIMARY KEY,
    user_id      UUID REFERENCES users(id) ON DELETE SET NULL,
    username     VARCHAR(50),
    action       VARCHAR(100) NOT NULL,
    target       VARCHAR(100),
    ip_address   INET,
    performed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    success      BOOLEAN NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log (performed_at DESC);
