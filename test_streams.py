#!/usr/bin/env python3
"""
test_streams.py — standalone MYT stream connectivity tester
Run: python test_streams.py
"""
from __future__ import annotations

import re
import subprocess
import sys
import time

# ── Optional dependencies ─────────────────────────────────────────────────────
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    print("WARNING: 'requests' not installed. HTTP checks skipped.")
    print("         pip install requests")

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    HAS_BS4 = False

# ── Camera catalogue (mirrors trafficwatch.py) ────────────────────────────────
CAMERAS = [
    {
        "camera_id":   "caudan_north",
        "name":        "Caudan North — Port Louis",
        "stream_base": "https://stream.myt.mu/rh/prod/CAUDAN_NORTH.stream_720p",
        "_chunklist_fallback": "https://stream.myt.mu/rh/prod/CAUDAN_NORTH.stream_720p/chunklist_w998681874.m3u8",
    },
    {
        "camera_id":   "caudan_south",
        "name":        "Caudan South — Port Louis",
        "stream_base": "https://stream.myt.mu/prod/CAUDAN_SOUTH.stream_720p",
        "_chunklist_fallback": "https://stream.myt.mu/prod/CAUDAN_SOUTH.stream_720p/chunklist_w674657069.m3u8",
    },
    {
        "camera_id":   "la_chaussee",
        "name":        "La Chaussee Street — Port Louis",
        "stream_base": "https://stream.myt.mu/prod/LA_CHAUSSEE_STREET.stream_720p",
        "_chunklist_fallback": "https://stream.myt.mu/prod/LA_CHAUSSEE_STREET.stream_720p/chunklist_w228974167.m3u8",
    },
    {
        "camera_id":   "casernes",
        "name":        "Casernes / Brabant Street — Port Louis",
        "stream_base": "https://stream.myt.mu/prod/CASERNES_BRABANT_STREET.stream_720p",
        "_chunklist_fallback": "https://stream.myt.mu/prod/CASERNES_BRABANT_STREET.stream_720p/chunklist_w1553997703.m3u8",
    },
]

MYT_PAGE = "https://www.myt.mu/sinformer/trafficwatch/"
HTTP_TIMEOUT  = 15   # seconds for requests.get
FRAME_TIMEOUT = 30   # seconds for one-frame ffmpeg grab


# ── Helpers ───────────────────────────────────────────────────────────────────

def hr(char: str = "-", w: int = 64) -> None:
    print(char * w)


def check_http(url: str) -> tuple[bool, str]:
    if not HAS_REQUESTS:
        return False, "requests not installed"
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT, stream=True,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            preview = r.raw.read(512)
            return True, f"HTTP 200  ({len(preview)} bytes preview)"
        return False, f"HTTP {r.status_code}"
    except requests.exceptions.ConnectionError as exc:
        return False, f"Connection error: {exc}"
    except requests.exceptions.Timeout:
        return False, f"Timeout >{HTTP_TIMEOUT}s"
    except Exception as exc:
        return False, str(exc)


def grab_frame(url: str) -> tuple[bool, str]:
    """Attempt to decode exactly 1 video frame via ffmpeg."""
    try:
        t0 = time.time()
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-loglevel",      "error",
                "-timeout",       "30000000",
                "-rw_timeout",    "30000000",
                "-reconnect",     "1",
                "-reconnect_streamed", "1",
                "-i",             url,
                "-frames:v",      "1",
                "-f",             "rawvideo",
                "-pix_fmt",       "bgr24",
                "pipe:1",
            ],
            capture_output=True,
            timeout=FRAME_TIMEOUT,
        )
        elapsed = round(time.time() - t0, 1)
        if result.returncode == 0 and len(result.stdout) > 1000:
            return True, f"{len(result.stdout):,} bytes  ({elapsed}s)"
        stderr = result.stderr.decode(errors="replace")[:200].replace("\n", " ")
        return False, f"rc={result.returncode} after {elapsed}s  err={stderr!r}"
    except subprocess.TimeoutExpired:
        return False, f"ffmpeg timeout >{FRAME_TIMEOUT}s"
    except FileNotFoundError:
        return False, "ffmpeg not found on PATH"
    except Exception as exc:
        return False, str(exc)


def scrape_live_urls() -> list[str]:
    if not HAS_REQUESTS:
        return []
    print(f"\nScraping {MYT_PAGE} …")
    try:
        r = requests.get(MYT_PAGE, timeout=15,
                         headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        html = r.text
    except Exception as exc:
        print(f"  Scrape request failed: {exc}")
        return []

    urls = list(set(re.findall(r'https?://[^\s\'"<>]+\.m3u8', html)))

    if not urls and HAS_BS4:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(["script", "source", "video"]):
            urls += re.findall(r'https?://[^\s\'"<>]+\.m3u8', str(tag))
        urls = list(set(urls))

    if urls:
        print(f"  Found {len(urls)} .m3u8 URL(s) in page source.")
    else:
        print("  No .m3u8 URLs found in page source.")
    return urls


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    hr("=")
    print("  MYT STREAM CONNECTIVITY TEST")
    print(f"  {len(CAMERAS)} cameras  |  HTTP timeout {HTTP_TIMEOUT}s  |  Frame timeout {FRAME_TIMEOUT}s")
    hr("=")

    results: dict[str, dict] = {}

    for cam in CAMERAS:
        cid  = cam["camera_id"]
        base = cam["stream_base"]
        candidates = [
            f"{base}/playlist.m3u8",
            f"{base}/chunklist.m3u8",
            cam["_chunklist_fallback"],
        ]

        hr()
        print(f"  {cam['name']}  [{cid}]")
        hr()

        working_url: str | None = None

        for url in candidates:
            label = url.split("/")[-1]   # just the filename for readability
            print(f"  HTTP  {label}")
            ok, reason = check_http(url)
            tag = "  PASS PASS" if ok else "  FAIL FAIL"
            print(f"  {tag}  {reason}")
            if ok and working_url is None:
                working_url = url

        if working_url:
            print(f"\n  FRAME grab from: …/{working_url.split('/')[-1]}")
            ok, reason = grab_frame(working_url)
            tag = "  PASS PASS" if ok else "  FAIL FAIL"
            print(f"  {tag}  {reason}")
            results[cid] = {"url": working_url, "http": True, "frame": ok}
        else:
            print("\n  FRAME  skipped — no URL returned HTTP 200")
            results[cid] = {"url": None, "http": False, "frame": False}

    # ── Summary ───────────────────────────────────────────────────────────────
    hr("=")
    print("  SUMMARY")
    hr("=")

    all_http_fail = all(not r["http"] for r in results.values())

    for cid, r in results.items():
        http_icon  = "PASS" if r["http"]  else "FAIL"
        frame_icon = "PASS" if r["frame"] else "FAIL"
        url = r["url"] or "—"
        print(f"  {cid:<22}  HTTP:{http_icon}  FRAME:{frame_icon}  {url}")

    if all_http_fail:
        print("\n  ALL HTTP CHECKS FAILED.")
        print("  Possible causes: no internet, VPN/firewall, or stream URLs rotated.")
        fresh = scrape_live_urls()
        if fresh:
            print("\n  Fresh URLs scraped from MYT page — update trafficwatch.py with these:")
            for u in fresh:
                print(f"    {u}")
        else:
            print("\n  Scrape also returned nothing. Check your network connection.")
    else:
        print("\n  Working URLs (copy into trafficwatch.py if needed):")
        for cid, r in results.items():
            if r["url"]:
                print(f'    # {cid}')
                print(f'    "{r["url"]}",')

    hr("=")
    any_frame = any(r["frame"] for r in results.values())
    sys.exit(0 if any_frame else 1)


if __name__ == "__main__":
    main()
