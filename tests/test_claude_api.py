"""
Author : Yadhav Sharma Ramsahye (22108355) - Scrum Master
Unit   : ISAD3000 Capstone Computing Project 1
Team   : IBL Group - Traffic Bottleneck Detection System
"""

from __future__ import annotations

import asyncio
import os
import sys

# Load .env before anything else.
from dotenv import load_dotenv
load_dotenv()

from detection.claude_api import (
    DetectionData,
    TrafficSummaryService,
    _template_summary,
    _template_alert_description,
    _camera_display_name,
)


# ── Sample detection data (same format as mock_pipeline.py yields) ────────

SAMPLE_DETECTIONS: list[dict] = [
    {
        "camera_id": "port_louis",
        "timestamp": "2026-03-29T10:30:00+00:00",
        "vehicle_count": 3,
        "severity": "free",
        "color": "#23c55e",
        "fps_processed": 7.5,
        "frame_shape": [720, 1280],
    },
    {
        "camera_id": "grand_baie",
        "timestamp": "2026-03-29T10:30:00+00:00",
        "vehicle_count": 12,
        "severity": "moderate",
        "color": "#f0883e",
        "fps_processed": 7.5,
        "frame_shape": [720, 1280],
    },
    {
        "camera_id": "caudan",
        "timestamp": "2026-03-29T10:30:00+00:00",
        "vehicle_count": 22,
        "severity": "heavy",
        "color": "#e94560",
        "fps_processed": 7.5,
        "frame_shape": [720, 1280],
    },
    {
        "camera_id": "ebene",
        "timestamp": "2026-03-29T10:30:00+00:00",
        "vehicle_count": 38,
        "severity": "bottleneck",
        "color": "#8b31c7",
        "fps_processed": 7.5,
        "frame_shape": [720, 1280],
    },
]


def divider(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


# ── Test 1: Helper functions ─────────────────────────────────────────────

def test_helpers() -> None:
    divider("TEST 1: Helper Functions")

    # Camera display name conversion
    assert _camera_display_name("port_louis") == "Port Louis"
    assert _camera_display_name("grand_baie") == "Grand Baie"
    assert _camera_display_name("caudan_north") == "Caudan North"
    print("[PASS] _camera_display_name works correctly")

    # DetectionData.from_dict
    data = DetectionData.from_dict(SAMPLE_DETECTIONS[0])
    assert data.camera_id == "port_louis"
    assert data.vehicle_count == 3
    assert data.severity == "free"
    print("[PASS] DetectionData.from_dict works correctly")


# ── Test 2: Template fallback (no API needed) ───────────────────────────

def test_template_fallback() -> None:
    divider("TEST 2: Template Fallback Summaries (no API key needed)")

    for detection in SAMPLE_DETECTIONS:
        data = DetectionData.from_dict(detection)

        summary = _template_summary(data)
        print(f"  [{data.severity.upper():>10}] {data.camera_id}")
        print(f"    Summary: {summary}")

        if data.severity in ("heavy", "bottleneck"):
            alert = _template_alert_description(data)
            print(f"    Alert:   {alert}")
        print()

    print("[PASS] All template summaries generated successfully")


# ── Test 3: TrafficSummaryService with fallback ──────────────────────────

async def test_service_fallback() -> None:
    divider("TEST 3: TrafficSummaryService - Fallback Mode")

    # Force the template backend via the same kill switch main.py exposes
    # (CLAUDE_API_DISABLED=1), regardless of which provider/key .env has
    # configured. Popping only ANTHROPIC_API_KEY was not enough once
    # SUMMARY_PROVIDER could be "gemini": with a live GEMINI_API_KEY still in
    # the environment this made a real Gemini call here and then failed its
    # own "must be template_fallback" assertion below.
    from detection import providers
    original_disabled = os.environ.get("CLAUDE_API_DISABLED")
    os.environ["CLAUDE_API_DISABLED"] = "1"
    providers.reset_clients()

    try:
        service = TrafficSummaryService()

        for detection in SAMPLE_DETECTIONS:
            result = await service.generate_summary(detection)
            assert result.source == "template_fallback", (
                f"Expected template_fallback but got {result.source}"
            )
            print(f"  [{result.severity.upper():>10}] {result.camera_id}")
            print(f"    Source:  {result.source}")
            print(f"    Summary: {result.summary}")
            print()

        # Test caching
        cached = service.get_cached_summary("port_louis")
        assert cached is not None, "Cache should have an entry for port_louis"
        print("[PASS] Caching works - cached summary retrieved for port_louis")

    finally:
        if original_disabled is None:
            os.environ.pop("CLAUDE_API_DISABLED", None)
        else:
            os.environ["CLAUDE_API_DISABLED"] = original_disabled
        providers.reset_clients()


# ── Test 4: Live Claude API call (only if key is set) ────────────────────

async def test_live_api() -> None:
    divider("TEST 4: Live Claude API Call")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("  [SKIP] ANTHROPIC_API_KEY not set in .env - skipping live test.")
        print("         To run this test, create a .env file with your key:")
        print("         ANTHROPIC_API_KEY=sk-ant-api03-your-key-here")
        return

    print("  API key detected - making a live Claude API call...\n")

    # Force the anthropic backend for this probe regardless of the .env
    # default (SUMMARY_PROVIDER=template) — otherwise generate_summary()
    # short-circuits to the template fallback before ever touching the key
    # this test just checked for, and the [PASS]/[WARN] below would be
    # judging nothing.
    from detection import providers
    original_provider = os.environ.get("SUMMARY_PROVIDER")
    os.environ["SUMMARY_PROVIDER"] = "anthropic"
    providers.reset_clients()  # drop any cached client from an earlier run

    try:
        service = TrafficSummaryService()

        # Test with a heavy-severity detection (most interesting output).
        heavy_detection = SAMPLE_DETECTIONS[2]  # caudan, heavy
        result = await service.generate_summary(heavy_detection)

        print(f"  Camera:   {result.camera_id}")
        print(f"  Severity: {result.severity}")
        print(f"  Vehicles: {result.vehicle_count}")
        print(f"  Source:   {result.source}")
        print(f"  Summary:  {result.summary}")
        print()

        # source is the provider name that actually answered ("anthropic"),
        # not the literal string "claude_api" — that never occurs since
        # detection/providers.py made the backend configurable.
        if result.source == providers.ANTHROPIC:
            print("[PASS] Live Claude API call succeeded!")
        else:
            print("[WARN] Fell back to template — check your API key and network.")

        # Also test alert description generation.
        print("\n  Generating alert description...\n")
        alert_result = await service.generate_alert_description(heavy_detection)
        print(f"  Alert source:  {alert_result.source}")
        print(f"  Alert message: {alert_result.summary}")
        print()

        if alert_result.source == providers.ANTHROPIC:
            print("[PASS] Live Claude API alert description succeeded!")
        else:
            print("[WARN] Alert fell back to template.")
    finally:
        if original_provider is None:
            os.environ.pop("SUMMARY_PROVIDER", None)
        else:
            os.environ["SUMMARY_PROVIDER"] = original_provider
        providers.reset_clients()


# ── Main ─────────────────────────────────────────────────────────────────

async def main() -> None:
    print("\n" + "#" * 60)
    print("#  Claude API Module - Integration Test Suite")
    print("#  IBL Capstone - Traffic Bottleneck Detection System")
    print("#" * 60)

    test_helpers()
    test_template_fallback()
    await test_service_fallback()
    await test_live_api()

    divider("ALL TESTS COMPLETE")
    print("  Your Claude API module is ready to go.\n")


if __name__ == "__main__":
    asyncio.run(main())
