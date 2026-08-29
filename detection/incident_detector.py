"""
Author : Sahil Singh Rughoo (22414560) — Tech Lead
Unit   : ISAD3000 Capstone Computing Project 1
Team   : IBL Group — Traffic Bottleneck Detection System traffic summaries
"""

from __future__ import annotations

import logging
import statistics
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Tunable thresholds ────────────────────────────────────────────────────────
HISTORY_SIZE        = 12   # readings kept per camera
SPIKE_THRESHOLD     = 8    # vehicles above the rolling median → sudden_congestion
BLOCKAGE_HIGH_MIN   = 10   # baseline median must be ≥ this
BLOCKAGE_LOW_MAX    = 2    # current count must drop to ≤ this
BOTTLENECK_STREAK   = 4    # consecutive bottleneck readings → sustained_bottleneck
BUILDUP_STREAK      = 4    # consecutive rising readings → rapid_buildup
BUILDUP_MIN_STEP    = 2    # each step must rise ≥ this many vehicles
FREEZE_TIMEOUT_SECS = 90   # seconds without data → camera_freeze
NIGHT_ZERO_STREAK   = 5    # consecutive zero readings at night → night_low_visibility

# Mauritius is UTC+4. The previous 18→06 UTC window meant 22:00→10:00 local,
# which swallowed the entire morning rush — a busy 08:00 road with a briefly
# empty frame was being reported as night-time low visibility.
NIGHT_START_HOUR    = 17   # UTC — 21:00 Port Louis
NIGHT_END_HOUR      = 1    # UTC — 05:00 Port Louis

# A candidate must repeat on this many consecutive readings before it becomes
# a reported incident. Single-reading detections are what produced the
# raise-then-resolve-seconds-later churn: YOLO counts jitter, and any rule
# comparing two adjacent readings fires on that jitter alone.
CONFIRM_STREAK      = 3

# Candidates below this never surface, regardless of persistence.
MIN_CONFIDENCE      = 0.70

# Incidents kept in memory. Durable history belongs in the `incidents` table.
LOG_MAX             = 500

# ── Incident type metadata ────────────────────────────────────────────────────
_TYPE_META: dict[str, dict] = {
    "sudden_congestion":    {"label": "Sudden Congestion",     "color": "#e94560"},
    "sustained_bottleneck": {"label": "Sustained Bottleneck",  "color": "#8b31c7"},
    "road_blockage":        {"label": "Road Blockage",         "color": "#f0883e"},
    "rapid_buildup":        {"label": "Rapid Traffic Buildup", "color": "#f0883e"},
    "camera_freeze":        {"label": "Camera Offline",        "color": "#5a7a9a"},
    "night_low_visibility": {"label": "Night Low Visibility",  "color": "#f59e0b"},
    "stalled_vehicle":      {"label": "Stalled Traffic",       "color": "#e94560"},
}

_CAMERA_DISPLAY: dict[str, str] = {
    "caudan_north": "Caudan North — Port Louis",
    "caudan_south": "Caudan South — Port Louis",
    "la_chaussee":  "La Chaussee Street — Port Louis",
    "casernes":     "Casernes / Brabant Street — Port Louis",
}


def _display(camera_id: str, direction: Optional[str] = None) -> str:
    base = _CAMERA_DISPLAY.get(camera_id, camera_id.replace("_", " ").title())
    return f"{base} ({direction})" if direction else base


# ── Internal data types ───────────────────────────────────────────────────────

@dataclass
class _Reading:
    count: int
    severity: str
    timestamp: datetime
    # detection/stationary_tracker.py's Verdict.to_dict(), when the caller has
    # one. None for callers that don't track individual vehicles (tests, the
    # mock pipeline) - _check_stalled_vehicle treats that the same as "not
    # stalled" rather than raising on missing data.
    stall_verdict: Optional[dict] = None


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
    # Which side of a calibrated two-way camera this incident belongs to
    # (e.g. "northbound"), or None for a whole-camera incident / an
    # uncalibrated camera. See detection/direction.py.
    direction:     Optional[str] = None

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
            "direction":     self.direction,
        }


# ── Main class ────────────────────────────────────────────────────────────────

class IncidentDetector:
    """
    Stateful, per-camera rolling-window incident detector.

    Not thread-safe — designed for a single asyncio event loop where
    all calls arrive sequentially from background camera tasks.
    """

    def __init__(self) -> None:
        # state_key → deque of _Reading. state_key is camera_id for a
        # whole-camera series, or "camera_id::direction" for one direction of
        # a calibrated two-way camera — kept separate so a jammed direction's
        # history isn't averaged together with a flowing one. See analyze().
        self._history: dict[str, deque[_Reading]] = {}
        # state_key → {incident_type: Incident}  (only open/unresolved)
        self._open: dict[str, dict[str, Incident]] = {}
        # Chronological ring buffer of raised incidents. Bounded because this
        # was an unbounded list that grew for the lifetime of the process;
        # durable history now lives in the `incidents` table instead.
        self._log: deque[Incident] = deque(maxlen=LOG_MAX)
        # state_key → consecutive zero-count readings during night hours
        self._night_zero_streak: dict[str, int] = {}
        # state_key → {incident_type: consecutive readings the candidate held}
        self._pending: dict[str, dict[str, int]] = {}

    # ── Public interface ──────────────────────────────────────────────────────

    def analyze(
        self,
        camera_id: str,
        vehicle_count: int,
        severity: str,
        timestamp: Optional[datetime] = None,
        stall_verdict: Optional[dict] = None,
        state_key: Optional[str] = None,
        direction: Optional[str] = None,
    ) -> list[Incident]:
        """
        Process one detection reading and return any newly raised incidents.
        Call this after every result is stored in ``latest_detections``.

        ``stall_verdict`` is detection/stationary_tracker.py's
        ``Verdict.to_dict()`` for this camera's current frame, when the
        caller has one (the HLS pipeline does; the mock pipeline does not).
        This is the one route a confirmed stall takes into the ``incidents``
        table - see _check_stalled_vehicle.

        ``state_key`` and ``direction`` let the same rule engine run
        independently per direction of a calibrated two-way camera: pass a
        composite key (e.g. "caudan_north::northbound") so that direction's
        rolling history/open-incident state never mixes with the whole-camera
        series or the opposite direction's, while ``camera_id`` on the
        emitted Incident stays the real camera id (required by the
        incidents.camera_id foreign key) and ``direction`` is folded into the
        description/location text. Both default to the whole-camera call
        this class always supported, so existing callers are unaffected.
        """
        key = state_key or camera_id
        ts      = timestamp or datetime.now(timezone.utc)
        reading = _Reading(count=vehicle_count, severity=severity, timestamp=ts,
                           stall_verdict=stall_verdict)

        if key not in self._history:
            self._history[key] = deque(maxlen=HISTORY_SIZE)
            self._open[key]    = {}

        hist = self._history[key]
        new: list[Incident] = []

        # Collect candidates first; nothing is reported on a single reading.
        candidates: dict[str, Incident] = {}
        for check in (
            self._check_sudden_congestion,
            self._check_sustained_bottleneck,
            self._check_road_blockage,
            self._check_rapid_buildup,
            self._check_night_low_visibility,
            self._check_stalled_vehicle,
        ):
            inc = check(camera_id, reading, hist, key, direction)
            if inc and inc.confidence >= MIN_CONFIDENCE:
                candidates[inc.type] = inc

        pending = self._pending.setdefault(key, {})
        # A candidate that stops recurring is noise — drop its streak entirely
        # rather than letting it accumulate across unrelated episodes.
        for itype in list(pending):
            if itype not in candidates:
                del pending[itype]

        for itype, inc in candidates.items():
            pending[itype] = pending.get(itype, 0) + 1
            if pending[itype] < CONFIRM_STREAK:
                logger.debug(
                    "[incident] candidate state=%s type=%s (%d/%d readings)",
                    key, itype, pending[itype], CONFIRM_STREAK,
                )
                continue
            del pending[itype]
            new.append(inc)
            self._log.append(inc)
            self._open[key][inc.type] = inc
            logger.info(
                "[incident] CONFIRMED  state=%-24s type=%-22s sev=%-8s conf=%.2f count=%d",
                key, inc.type, inc.severity, inc.confidence, inc.vehicle_count,
            )

        resolved = self._auto_resolve(key, reading, ts)
        for inc in resolved:
            logger.info(
                "[incident] RESOLVED  state=%-24s type=%s  id=%s",
                key, inc.type, inc.incident_id[:8],
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
        # deque does not support slicing — materialise first.
        return [i.to_dict() for i in reversed(list(self._log)[-limit:])]

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
        state_key: str,
        direction: Optional[str] = None,
    ) -> Optional[Incident]:
        if len(hist) < 3 or "sudden_congestion" in self._open.get(state_key, {}):
            return None
        # Measured against the rolling median, not the single previous reading.
        # An adjacent-reading comparison fires on YOLO count jitter and then
        # stops firing the moment the level holds, so it can never be confirmed.
        baseline = statistics.median(p.count for p in hist)
        spike = r.count - baseline
        if spike < SPIKE_THRESHOLD or r.severity not in ("heavy", "bottleneck"):
            return None
        confidence = min(0.95, 0.60 + spike / 40.0)
        severity   = "critical" if spike >= 20 else "severe" if spike >= 15 else "moderate"
        return self._make(
            camera_id     = camera_id,
            itype         = "sudden_congestion",
            iseverity     = severity,
            confidence    = confidence,
            vehicle_count = r.count,
            direction     = direction,
            description   = (
                # Reports the rolling-median baseline, not hist[-1]. The check
                # was changed to measure against the median (so it survives
                # confirmation across readings), but this text still quoted the
                # previous reading — which by then equals the current one, so
                # it read "jumped from 26 to 26 (+8)".
                f"Sudden congestion at {_display(camera_id, direction)}: vehicle count rose "
                f"to {r.count}, {spike:.0f} above the recent average of "
                f"{baseline:.0f}. Possible accident or road obstruction ahead."
            ),
        )

    def _check_sustained_bottleneck(
        self,
        camera_id: str,
        r: _Reading,
        hist: deque[_Reading],
        state_key: str,
        direction: Optional[str] = None,
    ) -> Optional[Incident]:
        if "sustained_bottleneck" in self._open.get(state_key, {}):
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
            direction     = direction,
            description   = (
                f"Sustained bottleneck at {_display(camera_id, direction)}: {r.count} vehicles "
                f"detected for {streak} consecutive cycles (~{streak * 2}+ seconds). "
                f"Significant congestion is persisting."
            ),
        )

    def _check_road_blockage(
        self,
        camera_id: str,
        r: _Reading,
        hist: deque[_Reading],
        state_key: str,
        direction: Optional[str] = None,
    ) -> Optional[Incident]:
        if len(hist) < 3 or "road_blockage" in self._open.get(state_key, {}):
            return None
        # Baseline median rather than the previous reading: a single dropped or
        # mis-decoded frame reads as "count fell from 12 to 0", which is a
        # detection failure, not a blocked road. The median stays high while
        # the low reading persists, so only a sustained drop is reported.
        prev = int(statistics.median(p.count for p in hist))
        if prev < BLOCKAGE_HIGH_MIN or r.count > BLOCKAGE_LOW_MAX:
            return None
        return self._make(
            camera_id     = camera_id,
            itype         = "road_blockage",
            iseverity     = "severe",
            confidence    = 0.82,
            vehicle_count = r.count,
            direction     = direction,
            description   = (
                f"Possible road blockage at {_display(camera_id, direction)}: count dropped "
                f"sharply from {prev} to {r.count}. Road may be blocked or emergency "
                f"vehicles are clearing the area."
            ),
        )

    def _check_rapid_buildup(
        self,
        camera_id: str,
        r: _Reading,
        hist: deque[_Reading],
        state_key: str,
        direction: Optional[str] = None,
    ) -> Optional[Incident]:
        if "rapid_buildup" in self._open.get(state_key, {}):
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
            direction     = direction,
            description   = (
                f"Rapid traffic buildup at {_display(camera_id, direction)}: vehicle count rose "
                f"from {window[0].count} to {r.count} (+{total_rise}) over "
                f"{BUILDUP_STREAK} consecutive readings. Congestion is actively worsening."
            ),
        )

    def _check_night_low_visibility(
        self,
        camera_id: str,
        r: _Reading,
        hist: deque[_Reading],
        state_key: str,
        direction: Optional[str] = None,
    ) -> Optional[Incident]:
        if "night_low_visibility" in self._open.get(state_key, {}):
            return None
        hour = datetime.now(timezone.utc).hour
        is_night = hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR
        if not is_night or r.count > 0:
            self._night_zero_streak[state_key] = 0
            return None
        streak = self._night_zero_streak.get(state_key, 0) + 1
        self._night_zero_streak[state_key] = streak
        if streak < NIGHT_ZERO_STREAK:
            return None
        return self._make(
            camera_id     = camera_id,
            itype         = "night_low_visibility",
            iseverity     = "minor",
            # Deliberately below MIN_CONFIDENCE, so this is recorded but never
            # surfaced as an incident: an empty road at 02:00 is the *expected*
            # state, not evidence of anything. Distinguishing "camera blinded"
            # from "nobody is driving" needs a per-camera night-time baseline
            # this detector does not have. Raise above the gate only once such
            # a baseline exists.
            confidence    = 0.65,
            vehicle_count = 0,
            direction     = direction,
            description   = (
                f"No vehicles detected at {_display(camera_id, direction)} during active night "
                f"hours ({streak} consecutive zero readings). Possible camera obstruction or "
                f"road closure."
            ),
        )

    def _check_stalled_vehicle(
        self,
        camera_id: str,
        r: _Reading,
        hist: deque[_Reading],
        state_key: str,
        direction: Optional[str] = None,
    ) -> Optional[Incident]:
        """Raise when detection/stationary_tracker.py reports a confirmed stall.

        The persistence requirement already lives in the tracker itself
        (STALL_SECONDS_REQUIRED - real, unbroken per-vehicle tracking, not a
        count pattern) - this only asks whether that already-confirmed
        verdict should become a reported incident, through the same
        CONFIRM_STREAK/MIN_CONFIDENCE gates every other check here uses, so
        this camera's incident history lives in one place instead of two.

        ``r.stall_verdict`` is whole-frame or per-direction depending on what
        the caller passed to analyze() - hls_pipeline now produces both (see
        _detect_loop), so a direction-scoped call here sees that direction's
        own tracked vehicles only, not the combined-frame verdict.
        """
        if "stalled_vehicle" in self._open.get(state_key, {}):
            return None
        sv = r.stall_verdict
        if not sv or not sv.get("is_incident"):
            return None
        return self._make(
            camera_id     = camera_id,
            itype         = "stalled_vehicle",
            iseverity     = "severe",
            confidence    = sv.get("confidence", 0.0),
            vehicle_count = r.count,
            direction     = direction,
            description   = (
                f"Stalled traffic at {_display(camera_id, direction)}: "
                f"{sv.get('stationary_count', 0)} of {sv.get('total_tracked', 0)} "
                f"tracked vehicles have not moved for "
                f"{sv.get('stalled_seconds', 0):.0f}s. Possible accident or breakdown."
            ),
        )

    # ── Auto-resolution ───────────────────────────────────────────────────────

    def _auto_resolve(
        self,
        state_key: str,
        r: _Reading,
        now: datetime,
    ) -> list[Incident]:
        resolved: list[Incident] = []
        for itype, inc in list(self._open.get(state_key, {}).items()):
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
            elif itype == "stalled_vehicle":
                should = not (r.stall_verdict and r.stall_verdict.get("is_incident"))
            if should:
                inc.resolved    = True
                inc.resolved_at = now.isoformat()
                del self._open[state_key][itype]
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
        direction: Optional[str] = None,
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
            location      = _display(camera_id, direction),
            color         = meta["color"],
            direction     = direction,
        )
