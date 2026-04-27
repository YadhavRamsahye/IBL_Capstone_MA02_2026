"""
detection/incident_detector.py
Analyses a rolling window of vehicle-count readings per camera to
detect traffic incidents without additional hardware.

Incident Types
--------------
sudden_congestion    — count spikes ≥10 vehicles in one reading cycle
sustained_bottleneck — bottleneck severity for 3+ consecutive readings
road_blockage        — count drops from ≥15 to ≤2 in one reading cycle
rapid_buildup        — 4+ consecutive readings each rising ≥2 vehicles,
                       ending in heavy or bottleneck
camera_freeze        — no update received for FREEZE_TIMEOUT_SECS seconds
                       (raised externally by the watchdog)

Public API
----------
    detector = IncidentDetector()

    # Call after every detection result:
    new_incidents = detector.analyze(camera_id, vehicle_count, severity)

    # Query state:
    detector.get_active_incidents()       -> list[dict]  (unresolved)
    detector.get_all_incidents(limit=100) -> list[dict]
    detector.get_incidents_by_camera(id)  -> list[dict]

    # Camera-freeze helpers (called by the watchdog in main.py):
    detector.mark_camera_freeze(camera_id)
    detector.resolve_camera_freeze(camera_id)
"""

from __future__ import annotations

import logging
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Tunable thresholds ────────────────────────────────────────────────────────
HISTORY_SIZE        = 10   # readings kept per camera
SPIKE_THRESHOLD     = 6    # vehicles added in one step → sudden_congestion
BLOCKAGE_HIGH_MIN   = 10   # previous count must be ≥ this
BLOCKAGE_LOW_MAX    = 2    # current count must drop to ≤ this
BOTTLENECK_STREAK   = 2    # consecutive bottleneck readings → sustained_bottleneck
BUILDUP_STREAK      = 4    # consecutive rising readings → rapid_buildup
BUILDUP_MIN_STEP    = 1    # each step must rise ≥ this many vehicles
FREEZE_TIMEOUT_SECS = 90   # seconds without data → camera_freeze
NIGHT_ZERO_STREAK   = 3    # consecutive zero readings at night → night_low_visibility
NIGHT_START_HOUR    = 18   # UTC hour — night window start (22:00 Port Louis)
NIGHT_END_HOUR      = 6    # UTC hour — night window end (10:00 Port Louis)

# ── Incident type metadata ────────────────────────────────────────────────────
_TYPE_META: dict[str, dict] = {
    "sudden_congestion":    {"label": "Sudden Congestion",     "color": "#e94560"},
    "sustained_bottleneck": {"label": "Sustained Bottleneck",  "color": "#8b31c7"},
    "road_blockage":        {"label": "Road Blockage",         "color": "#f0883e"},
    "rapid_buildup":        {"label": "Rapid Traffic Buildup", "color": "#f0883e"},
    "camera_freeze":        {"label": "Camera Offline",        "color": "#5a7a9a"},
    "night_low_visibility": {"label": "Night Low Visibility",  "color": "#f59e0b"},
}

_CAMERA_DISPLAY: dict[str, str] = {
    "caudan_north": "Caudan North — Port Louis",
    "caudan_south": "Caudan South — Port Louis",
    "la_chaussee":  "La Chaussee Street — Port Louis",
    "casernes":     "Casernes / Brabant Street — Port Louis",
}


def _display(camera_id: str) -> str:
    return _CAMERA_DISPLAY.get(camera_id, camera_id.replace("_", " ").title())


# ── Internal data types ───────────────────────────────────────────────────────

@dataclass
class _Reading:
    count: int
    severity: str
    timestamp: datetime


@dataclass
class Incident:
    incident_id:   str
    camera_id:     str
    type:          str
    severity:      str    # "minor" | "moderate" | "severe" | "critical"
    confidence:    float  # 0.0–1.0
    vehicle_count: int
    timestamp:     str    # ISO-8601
    description:   str
    location:      str
    color:         str
    resolved:      bool = False
    resolved_at:   Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "incident_id":   self.incident_id,
            "camera_id":     self.camera_id,
            "type":          self.type,
            "severity":      self.severity,
            "confidence":    round(self.confidence, 2),
            "vehicle_count": self.vehicle_count,
            "timestamp":     self.timestamp,
            "description":   self.description,
            "location":      self.location,
            "color":         self.color,
            "resolved":      self.resolved,
            "resolved_at":   self.resolved_at,
        }


# ── Main class ────────────────────────────────────────────────────────────────

class IncidentDetector:
    """
    Stateful, per-camera rolling-window incident detector.

    Not thread-safe — designed for a single asyncio event loop where
    all calls arrive sequentially from background camera tasks.
    """

    def __init__(self) -> None:
        # camera_id → deque of _Reading
        self._history: dict[str, deque[_Reading]] = {}
        # camera_id → {incident_type: Incident}  (only open/unresolved)
        self._open: dict[str, dict[str, Incident]] = {}
        # flat chronological list of every incident ever raised
        self._log: list[Incident] = []
        # camera_id → consecutive zero-count readings during night hours
        self._night_zero_streak: dict[str, int] = {}

    # ── Public interface ──────────────────────────────────────────────────────

    def analyze(
        self,
        camera_id: str,
        vehicle_count: int,
        severity: str,
        timestamp: Optional[datetime] = None,
    ) -> list[Incident]:
        """
        Process one detection reading and return any newly raised incidents.
        Call this after every result is stored in ``latest_detections``.
        """
        ts      = timestamp or datetime.now(timezone.utc)
        reading = _Reading(count=vehicle_count, severity=severity, timestamp=ts)

        if camera_id not in self._history:
            self._history[camera_id] = deque(maxlen=HISTORY_SIZE)
            self._open[camera_id]    = {}

        hist = self._history[camera_id]
        new: list[Incident] = []

        for check in (
            self._check_sudden_congestion,
            self._check_sustained_bottleneck,
            self._check_road_blockage,
            self._check_rapid_buildup,
            self._check_night_low_visibility,
        ):
            inc = check(camera_id, reading, hist)
            if inc:
                new.append(inc)
                self._log.append(inc)
                self._open[camera_id][inc.type] = inc
                logger.info(
                    "[incident] NEW  camera=%-14s type=%-22s sev=%-8s conf=%.2f count=%d",
                    camera_id, inc.type, inc.severity, inc.confidence, inc.vehicle_count,
                )

        resolved = self._auto_resolve(camera_id, reading, ts)
        for inc in resolved:
            logger.info(
                "[incident] RESOLVED  camera=%-14s type=%s  id=%s",
                camera_id, inc.type, inc.incident_id[:8],
            )

        hist.append(reading)
        return new

    def get_active_incidents(self) -> list[dict]:
        """All unresolved incidents across every camera, newest first."""
        active = [
            inc
            for cam_dict in self._open.values()
            for inc in cam_dict.values()
            if not inc.resolved
        ]
        active.sort(key=lambda i: i.timestamp, reverse=True)
        return [i.to_dict() for i in active]

    def get_all_incidents(self, limit: int = 100) -> list[dict]:
        """All incidents (resolved + active), newest first, up to *limit*."""
        return [i.to_dict() for i in reversed(self._log[-limit:])]

    def get_incidents_by_camera(self, camera_id: str) -> list[dict]:
        """All incidents for a specific camera, newest first."""
        return [
            i.to_dict()
            for i in reversed(self._log)
            if i.camera_id == camera_id
        ]

    def mark_camera_freeze(self, camera_id: str) -> Optional[Incident]:
        """Raise a camera_freeze incident.  No-op if one is already open."""
        if camera_id not in self._open:
            self._open[camera_id] = {}
        if "camera_freeze" in self._open[camera_id]:
            return None
        inc = self._make(
            camera_id     = camera_id,
            itype         = "camera_freeze",
            iseverity     = "minor",
            confidence    = 1.0,
            vehicle_count = 0,
            description   = (
                f"No data received from {_display(camera_id)} for over "
                f"{FREEZE_TIMEOUT_SECS} seconds. Stream may be offline."
            ),
        )
        self._log.append(inc)
        self._open[camera_id]["camera_freeze"] = inc
        logger.warning("[incident] CAMERA FREEZE  camera=%s", camera_id)
        return inc

    def resolve_camera_freeze(self, camera_id: str) -> None:
        """Resolve the camera_freeze incident when new data arrives."""
        cam = self._open.get(camera_id, {})
        inc = cam.pop("camera_freeze", None)
        if inc:
            inc.resolved    = True
            inc.resolved_at = datetime.now(timezone.utc).isoformat()
            logger.info("[incident] CAMERA BACK ONLINE  camera=%s", camera_id)

    # ── Detectors ─────────────────────────────────────────────────────────────

    def _check_sudden_congestion(
        self,
        camera_id: str,
        r: _Reading,
        hist: deque[_Reading],
    ) -> Optional[Incident]:
        if not hist or "sudden_congestion" in self._open.get(camera_id, {}):
            return None
        spike = r.count - hist[-1].count
        if spike < SPIKE_THRESHOLD:
            return None
        confidence = min(0.95, 0.60 + spike / 40.0)
        severity   = "critical" if spike >= 20 else "severe" if spike >= 15 else "moderate"
        return self._make(
            camera_id     = camera_id,
            itype         = "sudden_congestion",
            iseverity     = severity,
            confidence    = confidence,
            vehicle_count = r.count,
            description   = (
                f"Sudden congestion at {_display(camera_id)}: vehicle count jumped "
                f"from {hist[-1].count} to {r.count} (+{spike}) in one detection cycle. "
                f"Possible accident or road obstruction ahead."
            ),
        )

    def _check_sustained_bottleneck(
        self,
        camera_id: str,
        r: _Reading,
        hist: deque[_Reading],
    ) -> Optional[Incident]:
        if "sustained_bottleneck" in self._open.get(camera_id, {}):
            return None
        if r.severity != "bottleneck" or len(hist) < BOTTLENECK_STREAK - 1:
            return None
        streak = 1
        for past in reversed(hist):
            if past.severity == "bottleneck":
                streak += 1
            else:
                break
        if streak < BOTTLENECK_STREAK:
            return None
        confidence = min(0.95, 0.65 + streak * 0.05)
        severity   = "critical" if streak >= 6 else "severe" if streak >= 4 else "moderate"
        return self._make(
            camera_id     = camera_id,
            itype         = "sustained_bottleneck",
            iseverity     = severity,
            confidence    = confidence,
            vehicle_count = r.count,
            description   = (
                f"Sustained bottleneck at {_display(camera_id)}: {r.count} vehicles "
                f"detected for {streak} consecutive cycles (~{streak * 2}+ seconds). "
                f"Significant congestion is persisting."
            ),
        )

    def _check_road_blockage(
        self,
        camera_id: str,
        r: _Reading,
        hist: deque[_Reading],
    ) -> Optional[Incident]:
        if not hist or "road_blockage" in self._open.get(camera_id, {}):
            return None
        prev = hist[-1].count
        if prev < BLOCKAGE_HIGH_MIN or r.count > BLOCKAGE_LOW_MAX:
            return None
        return self._make(
            camera_id     = camera_id,
            itype         = "road_blockage",
            iseverity     = "severe",
            confidence    = 0.82,
            vehicle_count = r.count,
            description   = (
                f"Possible road blockage at {_display(camera_id)}: count dropped "
                f"sharply from {prev} to {r.count}. Road may be blocked or emergency "
                f"vehicles are clearing the area."
            ),
        )

    def _check_rapid_buildup(
        self,
        camera_id: str,
        r: _Reading,
        hist: deque[_Reading],
    ) -> Optional[Incident]:
        if "rapid_buildup" in self._open.get(camera_id, {}):
            return None
        if len(hist) < BUILDUP_STREAK - 1 or r.severity not in ("heavy", "bottleneck"):
            return None
        window = list(hist)[-(BUILDUP_STREAK - 1):] + [r]
        if not all(
            window[i].count - window[i - 1].count >= BUILDUP_MIN_STEP
            for i in range(1, len(window))
        ):
            return None
        total_rise = r.count - window[0].count
        confidence = min(0.80, 0.50 + total_rise / 40.0)
        return self._make(
            camera_id     = camera_id,
            itype         = "rapid_buildup",
            iseverity     = "moderate",
            confidence    = confidence,
            vehicle_count = r.count,
            description   = (
                f"Rapid traffic buildup at {_display(camera_id)}: vehicle count rose "
                f"from {window[0].count} to {r.count} (+{total_rise}) over "
                f"{BUILDUP_STREAK} consecutive readings. Congestion is actively worsening."
            ),
        )

    def _check_night_low_visibility(
        self,
        camera_id: str,
        r: _Reading,
        hist: deque[_Reading],
    ) -> Optional[Incident]:
        if "night_low_visibility" in self._open.get(camera_id, {}):
            return None
        hour = datetime.now(timezone.utc).hour
        is_night = hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR
        if not is_night or r.count > 0:
            self._night_zero_streak[camera_id] = 0
            return None
        streak = self._night_zero_streak.get(camera_id, 0) + 1
        self._night_zero_streak[camera_id] = streak
        if streak < NIGHT_ZERO_STREAK:
            return None
        return self._make(
            camera_id     = camera_id,
            itype         = "night_low_visibility",
            iseverity     = "minor",
            confidence    = 0.65,
            vehicle_count = 0,
            description   = (
                f"No vehicles detected at {_display(camera_id)} during active night hours "
                f"({streak} consecutive zero readings). Possible camera obstruction or "
                f"road closure."
            ),
        )

    # ── Auto-resolution ───────────────────────────────────────────────────────

    def _auto_resolve(
        self,
        camera_id: str,
        r: _Reading,
        now: datetime,
    ) -> list[Incident]:
        resolved: list[Incident] = []
        for itype, inc in list(self._open.get(camera_id, {}).items()):
            should = False
            if itype == "sudden_congestion":
                should = r.severity in ("free", "moderate")
            elif itype == "sustained_bottleneck":
                should = r.severity in ("free", "moderate", "heavy")
            elif itype == "road_blockage":
                should = r.count >= 5
            elif itype == "rapid_buildup":
                should = r.severity in ("free", "moderate")
            elif itype == "night_low_visibility":
                should = r.count > 0
            if should:
                inc.resolved    = True
                inc.resolved_at = now.isoformat()
                del self._open[camera_id][itype]
                resolved.append(inc)
        return resolved

    # ── Factory ───────────────────────────────────────────────────────────────

    def _make(
        self,
        camera_id: str,
        itype: str,
        iseverity: str,
        confidence: float,
        vehicle_count: int,
        description: str,
    ) -> Incident:
        meta = _TYPE_META.get(itype, {"label": itype, "color": "#5a7a9a"})
        return Incident(
            incident_id   = str(uuid.uuid4()),
            camera_id     = camera_id,
            type          = itype,
            severity      = iseverity,
            confidence    = confidence,
            vehicle_count = vehicle_count,
            timestamp     = datetime.now(timezone.utc).isoformat(),
            description   = description,
            location      = _display(camera_id),
            color         = meta["color"],
        )
