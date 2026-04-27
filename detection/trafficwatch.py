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
import re
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
        "lat":         -20.1626,
        "lng":          57.4939,
        "stream_base": "https://stream.myt.mu/rh/prod/CAUDAN_NORTH.stream_720p",
        "origin":      "myt.trafficwatch",
        "_chunklist_fallback": "https://stream.myt.mu/rh/prod/CAUDAN_NORTH.stream_720p/chunklist_w998681874.m3u8",
    },
    {
        "camera_id":   "caudan_south",
        "name":        "Caudan South — Port Louis",
        "lat":         -20.1640,
        "lng":          57.4945,
        "stream_base": "https://stream.myt.mu/prod/CAUDAN_SOUTH.stream_720p",
        "origin":      "myt.trafficwatch",
        "_chunklist_fallback": "https://stream.myt.mu/prod/CAUDAN_SOUTH.stream_720p/chunklist_w674657069.m3u8",
    },
    {
        "camera_id":       "la_chaussee",
        "name":            "La Chaussee Street — Port Louis",
        "lat":             -20.1608,
        "lng":              57.4972,
        "stream_base":     "https://stream.myt.mu/prod/LA_CHAUSSEE_STREET.stream_720p",
        "source_override": "https://stream.myt.mu/prod/LA_CHAUSSEE_STREET.stream_720p/chunklist.m3u8",
        "url_candidates": [
            "https://stream.myt.mu/prod/LA_CHAUSSEE_STREET.stream_720p/chunklist.m3u8",
            "https://stream.myt.mu/prod/LA_CHAUSSEE_STREET.stream_720p/playlist.m3u8",
            "https://stream.myt.mu/prod/LA_CHAUSSEE_STREET.stream_720p/chunklist_w228974167.m3u8",
        ],
        "origin":          "myt.trafficwatch",
        "_chunklist_fallback": "https://stream.myt.mu/prod/LA_CHAUSSEE_STREET.stream_720p/chunklist_w228974167.m3u8",
    },
    {
        "camera_id":   "casernes",
        "name":        "Casernes / Brabant Street — Port Louis",
        "lat":         -20.1590,
        "lng":          57.4960,
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

FFPROBE_TIMEOUT = 15  # seconds per URL probe — Wowza needs more time to respond

MYT_PAGE = "https://www.myt.mu/sinformer/trafficwatch/"


def _scrape_live_urls() -> dict[str, str]:
    """
    Fetch the MYT traffic watch page and extract live .m3u8 URLs.
    Returns a dict mapping camera_id → url for any streams found.
    Silently returns {} on any error — scraping is best-effort.
    """
    try:
        import requests
    except ImportError:
        logger.warning("[trafficwatch] requests not installed — skipping live URL scrape.")
        return {}

    logger.info("[trafficwatch] Scraping live URLs from %s …", MYT_PAGE)
    try:
        resp = requests.get(
            MYT_PAGE, timeout=15,
            headers={"User-Agent": "Mozilla/5.0 (compatible; TrafficWatch/1.0)"},
        )
        resp.raise_for_status()
        html = resp.text
    except Exception as exc:
        logger.warning("[trafficwatch] Scrape request failed: %s", exc)
        return {}

    raw_urls = re.findall(r'https?://[^\s\'"<>]+\.m3u8', html)

    # Try BeautifulSoup for script tag content if raw regex found nothing
    if not raw_urls:
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup.find_all(["script", "source", "video"]):
                raw_urls += re.findall(r'https?://[^\s\'"<>]+\.m3u8', str(tag))
        except ImportError:
            pass

    raw_urls = list(set(raw_urls))
    if not raw_urls:
        logger.info("[trafficwatch] No .m3u8 URLs found on MYT page.")
        return {}

    logger.info("[trafficwatch] Found %d .m3u8 URL(s) on MYT page.", len(raw_urls))

    # Match scraped URLs to known camera IDs by stream name keywords
    keyword_map = {
        "CAUDAN_NORTH":          "caudan_north",
        "CAUDAN_SOUTH":          "caudan_south",
        "LA_CHAUSSEE":           "la_chaussee",
        "CASERNES":              "casernes",
    }
    matched: dict[str, str] = {}
    for url in raw_urls:
        url_upper = url.upper()
        for kw, cam_id in keyword_map.items():
            if kw in url_upper and cam_id not in matched:
                matched[cam_id] = url
                logger.info("[trafficwatch] Scraped %s → %s", cam_id, url)
    return matched


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

def _resolve_source(
    cam: dict,
    ffprobe_ok: bool,
    scraped: dict[str, str] | None = None,
) -> tuple[str, bool]:
    """
    Try candidate URLs in priority order until one validates.
    Returns (source_url, validated).

    Candidate order:
      1. {stream_base}/playlist.m3u8
      2. {stream_base}/chunklist.m3u8
      3. _chunklist_fallback (hardcoded rotating ID)
      4. scraped URL from MYT page (if available)

    If ffprobe is unavailable, returns the best available URL unvalidated.
    """
    # Fast path: camera has a known-good URL — skip all probing.
    if "source_override" in cam:
        url = cam["source_override"]
        logger.info("  [%s] source_override set — using %s (unvalidated)", cam["camera_id"], url)
        return url, False

    base = cam["stream_base"]
    cid  = cam["camera_id"]

    candidates: list[str] = [
        f"{base}/playlist.m3u8",
        f"{base}/chunklist.m3u8",
        cam.get("_chunklist_fallback", ""),
    ]
    # 4th candidate: live-scraped URL
    if scraped and cid in scraped:
        scraped_url = scraped[cid]
        if scraped_url not in candidates:
            candidates.append(scraped_url)
            logger.info("  [%s] Added scraped URL as candidate: %s", cid, scraped_url)

    candidates = [u for u in candidates if u]  # drop empty strings

    if not ffprobe_ok:
        primary = candidates[0]
        logger.info("  [%s] ffprobe unavailable — using %s (unvalidated)", cid, primary)
        return primary, False

    for url in candidates:
        logger.info("  [%s] Trying %s …", cid, url)
        if _validate_with_ffprobe(url):
            logger.info("  [%s] → VALID", cid)
            return url, True
        logger.info("  [%s] → no video streams", cid)

    # All candidates failed — still return primary so the pipeline can try anyway
    logger.warning("  [%s] All candidates failed ffprobe — returning primary unvalidated.", cid)
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

    # First pass: try without scraping
    cameras: list[dict] = []
    for cam in MYT_CAMERAS:
        logger.info("[trafficwatch] Processing camera: %s", cam["camera_id"])
        source, validated = _resolve_source(cam, ffprobe_ok)
        cameras.append({
            "camera_id":     cam["camera_id"],
            "name":          cam["name"],
            "lat":           cam.get("lat"),
            "lng":           cam.get("lng"),
            "source":        source,
            "stream_base":   cam["stream_base"],
            "origin":        cam["origin"],
            "validated":     validated,
            "url_candidates": cam.get("url_candidates"),
        })

    n_validated = sum(1 for c in cameras if c["validated"])

    # If ffprobe found nothing, scrape live URLs and retry unvalidated cameras
    if ffprobe_ok and n_validated == 0:
        logger.warning(
            "[trafficwatch] All %d stream(s) failed ffprobe — scraping MYT page for fresh URLs.",
            len(MYT_CAMERAS),
        )
        scraped = _scrape_live_urls()
        if scraped:
            cameras = []
            for cam in MYT_CAMERAS:
                source, validated = _resolve_source(cam, ffprobe_ok, scraped=scraped)
                cameras.append({
                    "camera_id":     cam["camera_id"],
                    "name":          cam["name"],
                    "lat":           cam.get("lat"),
                    "lng":           cam.get("lng"),
                    "source":        source,
                    "stream_base":   cam["stream_base"],
                    "origin":        cam["origin"],
                    "validated":     validated,
                    "url_candidates": cam.get("url_candidates"),
                })
            n_validated = sum(1 for c in cameras if c["validated"])

    logger.info(
        "[trafficwatch] Discovery complete — %d camera(s), %d validated.",
        len(cameras), n_validated,
    )
    return cameras
