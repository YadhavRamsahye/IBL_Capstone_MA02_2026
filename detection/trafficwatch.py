"""
detection/trafficwatch.py
Hardcoded MYT Traffic Watch camera list with ffprobe-based validation.

Stream source: https://www.myt.mu/sinformer/trafficwatch/
Wowza Streaming Engine — HLS (.m3u8) format.

Public API
----------
    discover_cameras() -> list[dict]

Each returned dict has the shape:
    {
        "camera_id":   str,
        "name":        str,
        "source":      str,   # validated playlist.m3u8 URL
        "stream_base": str,   # stable base URL without filename
        "origin":      str,
        "validated":   bool,
    }
"""

from __future__ import annotations

import json
import logging
import subprocess
import time

logger = logging.getLogger(__name__)

# ── Hardcoded camera catalogue ────────────────────────────────────────────────
# Use stable playlist.m3u8 paths; chunklist URLs rotate and should only be
# tried as a last-resort fallback.

MYT_CAMERAS: list[dict] = [
    {
        "camera_id":   "caudan_north",
        "name":        "Caudan North — Port Louis",
        "stream_base": "https://stream.myt.mu/rh/prod/CAUDAN_NORTH.stream_720p",
        "origin":      "myt.trafficwatch",
        # Last-resort chunklist observed during discovery (chunk ID rotates)
        "_chunklist_fallback": "https://stream.myt.mu/rh/prod/CAUDAN_NORTH.stream_720p/chunklist_w998681874.m3u8",
    },
    {
        "camera_id":   "caudan_south",
        "name":        "Caudan South — Port Louis",
        "stream_base": "https://stream.myt.mu/prod/CAUDAN_SOUTH.stream_720p",
        "origin":      "myt.trafficwatch",
        "_chunklist_fallback": "https://stream.myt.mu/prod/CAUDAN_SOUTH.stream_720p/chunklist_w674657069.m3u8",
    },
    {
        "camera_id":   "la_chaussee",
        "name":        "La Chaussee Street — Port Louis",
        "stream_base": "https://stream.myt.mu/prod/LA_CHAUSSEE_STREET.stream_720p",
        "origin":      "myt.trafficwatch",
        "_chunklist_fallback": "https://stream.myt.mu/prod/LA_CHAUSSEE_STREET.stream_720p/chunklist_w228974167.m3u8",
    },
    {
        "camera_id":   "casernes",
        "name":        "Casernes / Brabant Street — Port Louis",
        "stream_base": "https://stream.myt.mu/prod/CASERNES_BRABANT_STREET.stream_720p",
        "origin":      "myt.trafficwatch",
        "_chunklist_fallback": "https://stream.myt.mu/prod/CASERNES_BRABANT_STREET.stream_720p/chunklist_w1553997703.m3u8",
    },
]

FALLBACK_CAMERAS: list[dict] = [
    {
        "camera_id":   "tw_fallback_1",
        "name":        "Fallback — check myt.mu/trafficwatch manually",
        "source":      "mock",
        "stream_base": "",
        "origin":      "fallback",
        "validated":   False,
    }
]

FFPROBE_TIMEOUT = 5   # seconds per URL probe


# ── ffprobe helpers ───────────────────────────────────────────────────────────

def _ffprobe_available() -> bool:
    """Return True if ffprobe is on PATH."""
    try:
        r = subprocess.run(
            ["ffprobe", "-version"],
            capture_output=True, timeout=5,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _validate_with_ffprobe(url: str) -> bool:
    """
    Run ffprobe against *url* with a FFPROBE_TIMEOUT-second limit.
    Returns True if ffprobe detects at least one video stream.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                url,
            ],
            capture_output=True,
            timeout=FFPROBE_TIMEOUT,
        )
        if result.returncode != 0:
            return False
        data = json.loads(result.stdout or b"{}")
        streams = data.get("streams", [])
        has_video = any(s.get("codec_type") == "video" for s in streams)
        return has_video
    except subprocess.TimeoutExpired:
        logger.warning("  [ffprobe] Timeout (%ds) on %s", FFPROBE_TIMEOUT, url)
        return False
    except Exception as exc:
        logger.warning("  [ffprobe] Error on %s: %s", url, exc)
        return False


# ── URL resolution per camera ─────────────────────────────────────────────────

def _resolve_source(cam: dict, ffprobe_ok: bool) -> tuple[str, bool]:
    """
    Try candidate URLs in priority order until one validates.
    Returns (source_url, validated).

    If ffprobe is unavailable, returns the primary playlist URL unvalidated
    (we know the streams exist; validation is best-effort).
    """
    base = cam["stream_base"]
    candidates = [
        f"{base}/playlist.m3u8",
        f"{base}/chunklist.m3u8",
        cam.get("_chunklist_fallback", ""),
    ]
    candidates = [u for u in candidates if u]   # drop empty strings

    if not ffprobe_ok:
        primary = candidates[0]
        logger.info(
            "  [%s] ffprobe unavailable — using %s (unvalidated)",
            cam["camera_id"], primary,
        )
        return primary, False

    for url in candidates:
        logger.info("  [%s] Trying %s …", cam["camera_id"], url)
        if _validate_with_ffprobe(url):
            logger.info("  [%s] → VALID", cam["camera_id"])
            return url, True
        logger.info("  [%s] → no video streams", cam["camera_id"])

    # All candidates failed — still return the primary so the pipeline can try
    logger.warning(
        "  [%s] All candidates failed ffprobe — returning primary unvalidated.",
        cam["camera_id"],
    )
    return candidates[0], False


# ── Public entry point ────────────────────────────────────────────────────────

def discover_cameras() -> list[dict]:
    """
    Build and validate the MYT Traffic Watch camera list.

    Validation uses ffprobe (5 s timeout per URL).  If ffprobe is not
    installed, all cameras are returned unvalidated — the HLS pipeline
    will attempt them anyway and fall back to mock on failure.

    Never returns an empty list; falls back to FALLBACK_CAMERAS only if
    MYT_CAMERAS itself is somehow empty (defensive guard).
    """
    logger.info("=" * 62)
    logger.info("[trafficwatch] MYT Traffic Watch — camera discovery")
    logger.info("  Source: https://www.myt.mu/sinformer/trafficwatch/")
    logger.info("=" * 62)

    if not MYT_CAMERAS:
        logger.error("[trafficwatch] MYT_CAMERAS list is empty — returning fallback.")
        return FALLBACK_CAMERAS

    # Check ffprobe once for all cameras
    ffprobe_ok = _ffprobe_available()
    if ffprobe_ok:
        logger.info("[trafficwatch] ffprobe detected — will validate each stream URL.")
    else:
        logger.warning(
            "[trafficwatch] ffprobe not found on PATH.\n"
            "  Install FFmpeg from https://ffmpeg.org/download.html\n"
            "  Windows builds: https://www.gyan.dev/ffmpeg/builds/\n"
            "  After installing, restart your terminal and run: ffprobe -version\n"
            "  Cameras will be added unvalidated; the HLS pipeline will still attempt them."
        )

    cameras: list[dict] = []
    for cam in MYT_CAMERAS:
        logger.info("[trafficwatch] Processing camera: %s", cam["camera_id"])
        source, validated = _resolve_source(cam, ffprobe_ok)
        cameras.append({
            "camera_id":   cam["camera_id"],
            "name":        cam["name"],
            "source":      source,
            "stream_base": cam["stream_base"],
            "origin":      cam["origin"],
            "validated":   validated,
        })

    n_validated = sum(1 for c in cameras if c["validated"])
    logger.info(
        "[trafficwatch] Discovery complete — %d camera(s), %d validated.",
        len(cameras), n_validated,
    )
    return cameras
