"""
Author : Sahil Singh Rughoo (22414560) - Tech Lead
Unit   : ISAD3000 Capstone Computing Project 1
Team   : IBL Group - Traffic Bottleneck Detection System traffic summaries
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)

# Allow running from the detection/ subdirectory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from detection.trafficwatch import discover_cameras, FALLBACK_CAMERAS  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────────────

def _divider(char: str = "─", width: int = 62) -> None:
    print(char * width)


def _check_tool(name: str) -> tuple[bool, str]:
    """
    Return (available, version_string) for an executable on PATH.
    version_string is empty if the tool is missing.
    """
    try:
        r = subprocess.run(
            [name, "-version"],
            capture_output=True, timeout=5,
        )
        if r.returncode == 0:
            # First line of output contains the version string
            first_line = (r.stdout or r.stderr or b"").decode(errors="replace").splitlines()[0]
            return True, first_line.strip()
        return False, ""
    except FileNotFoundError:
        return False, ""
    except subprocess.TimeoutExpired:
        return False, "(timeout)"


def _grab_one_frame(hls_url: str, width: int = 1280, height: int = 720) -> bool:
    """
    Attempt to pull exactly one raw BGR24 frame from *hls_url* via ffmpeg.
    Returns True on success.
    """
    cmd = [
        "ffmpeg",
        "-loglevel", "error",
        "-i", hls_url,
        "-vframes", "1",          # grab exactly one frame
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "pipe:1",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=20)
        frame_bytes = width * height * 3
        return len(proc.stdout) >= frame_bytes
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    _divider("═")
    print("  MYT Traffic Watch - Camera Discovery & HLS Test")
    print("  IBL Capstone MA02 2026 - Mauritius")
    _divider("═")
    print()

    # ── 1. Tool checks ────────────────────────────────────────────────────────
    print("  TOOL AVAILABILITY")
    _divider()

    ffmpeg_ok,  ffmpeg_ver  = _check_tool("ffmpeg")
    ffprobe_ok, ffprobe_ver = _check_tool("ffprobe")

    ffmpeg_label  = f"OK   {ffmpeg_ver}"  if ffmpeg_ok  else "NOT FOUND"
    ffprobe_label = f"OK   {ffprobe_ver}" if ffprobe_ok else "NOT FOUND"

    print(f"  ffmpeg  : {ffmpeg_label}")
    print(f"  ffprobe : {ffprobe_label}")

    if not ffmpeg_ok or not ffprobe_ok:
        print()
        print("  NOTE: FFmpeg / ffprobe are required for HLS stream processing.")
        print("  Download  : https://ffmpeg.org/download.html")
        print("  Windows   : https://www.gyan.dev/ffmpeg/builds/")
        print("  After installing, restart your terminal and run:")
        print("    ffmpeg -version && ffprobe -version")

    print()

    # ── 2. Camera discovery ───────────────────────────────────────────────────
    _divider("═")
    print("  CAMERA DISCOVERY")
    _divider()

    start    = time.monotonic()
    cameras  = discover_cameras()
    elapsed  = time.monotonic() - start

    print()
    print(f"  Discovery finished in {elapsed:.1f}s")
    print()

    # ── 3. Per-camera results ──────────────────────────────────────────────────
    validated_cameras = [c for c in cameras if c.get("validated")]
    mock_cameras      = [c for c in cameras if c.get("source") == "mock"]
    hls_cameras       = [c for c in cameras if str(c.get("source", "")).endswith(".m3u8")]

    _divider()
    print("  DISCOVERED CAMERAS")
    _divider()

    for i, cam in enumerate(cameras, 1):
        src       = cam.get("source", "-")
        validated = cam.get("validated", False)
        v_label   = "validated" if validated else "unvalidated"
        is_mock   = src == "mock"
        type_tag  = "mock" if is_mock else ("HLS" if src.endswith(".m3u8") else "RTSP")

        print(f"  [{i}] {cam['camera_id']}")
        print(f"      Name        : {cam['name']}")
        print(f"      Source      : {src}")
        print(f"      Stream base : {cam.get('stream_base', '-')}")
        print(f"      Type        : {type_tag}  ({v_label})")
        print(f"      Origin      : {cam.get('origin', '-')}")
        print()

    # ── 4. Frame-grab test ────────────────────────────────────────────────────
    _divider()
    print("  HLS FRAME GRAB TEST")
    _divider()

    grab_target = next(
        (c for c in cameras if str(c.get("source", "")).endswith(".m3u8")),
        None,
    )

    if not grab_target:
        print("  No HLS cameras available - skipping frame grab test.")

    elif not ffmpeg_ok:
        print("  ffmpeg not installed - cannot perform frame grab.")
        print("  Install ffmpeg and re-run this script to test frame capture.")

    else:
        src = grab_target["source"]
        print(f"  Target : {grab_target['camera_id']}  ({grab_target['name']})")
        print(f"  URL    : {src}")
        print("  Grabbing one frame (timeout: 20s) …")

        frame_w, frame_h = 1280, 720
        success = _grab_one_frame(src, frame_w, frame_h)

        if success:
            print(f"  Frame grab : SUCCESS ({frame_w}x{frame_h})")
        else:
            print("  Frame grab : FAILED")
            print("  Possible causes:")
            print("    • Stream is currently offline")
            print("    • URL has expired (chunklist ID rotated - server restart may refresh it)")
            print("    • Network / geo-restriction")

    print()

    # ── 5. Full JSON ──────────────────────────────────────────────────────────
    _divider()
    print("  FULL CAMERA CONFIG (JSON)")
    _divider()
    print(json.dumps(cameras, indent=2))
    print()

    # ── 6. Summary ────────────────────────────────────────────────────────────
    _divider("═")
    print("  SUMMARY")
    _divider()
    print(f"  Total cameras   : {len(cameras)}")
    print(f"  HLS streams     : {len(hls_cameras)}")
    print(f"  Validated       : {len(validated_cameras)}")
    print(f"  Mock / fallback : {len(mock_cameras)}")
    print(f"  ffmpeg ready    : {'YES' if ffmpeg_ok else 'NO - install required'}")
    print(f"  ffprobe ready   : {'YES' if ffprobe_ok else 'NO - install required'}")
    print()

    if hls_cameras and ffmpeg_ok:
        print("  System is ready to process live MYT Traffic Watch streams.")
    elif hls_cameras and not ffmpeg_ok:
        print("  HLS cameras discovered but ffmpeg is missing.")
        print("  Install ffmpeg then restart the server - streams will be live.")
    else:
        print("  No HLS streams configured - server will use mock pipeline.")

    _divider("═")
    print()
    sys.exit(0)


if __name__ == "__main__":
    main()
