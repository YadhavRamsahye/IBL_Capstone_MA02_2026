"""
tools/fetch_cameras.py
Regenerate detection/camera_catalogue.py from MYT's Traffic Watch page.

Why generate rather than scrape at startup
------------------------------------------
detection/trafficwatch.py used to carry four hand-written camera entries, so the
system covered central Port Louis while MYT publishes 38 cameras island-wide.
Scraping live on every boot would keep the list current but makes startup depend
on MYT's page being up and unchanged — a layout change would leave the app with
no cameras at all.

So the page is parsed here, offline, into a checked-in Python module. The
catalogue is then a reviewable artefact: you can see what changed in a diff,
and startup never depends on a scrape succeeding. Re-run this when MYT adds or
removes cameras.

    python tools/fetch_cameras.py            # regenerate the catalogue
    python tools/fetch_cameras.py --dry-run  # show what would change

Coordinates
-----------
MYT's page carries no coordinates. Four cameras have hand-verified positions
(kept as `exact`); the rest are placed at the centroid of the region MYT groups
them under and marked `approximate`. Region-level placement is honest — the
camera really is in that region — and the UI shows approximate markers
differently so they are never mistaken for surveyed positions. Replace a
camera's entry with a real coordinate and set precision to "exact" as you
verify them.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

MYT_PAGE = "https://www.myt.mu/sinformer/trafficwatch/"
OUTPUT = pathlib.Path(__file__).resolve().parent.parent / "detection" / "camera_catalogue.py"

# Region centroids, to roughly a town centre. Used for cameras whose exact
# position is unknown; see the module docstring.
REGION_CENTRES: dict[str, tuple[float, float]] = {
    "Port Louis Center":         (-20.1619, 57.4989),
    "Port Louis South":          (-20.1880, 57.4905),
    "Port Louis North":          (-20.1370, 57.5030),
    "Reduit & Ebene":            (-20.2355, 57.4915),
    "Beau Bassin & Rose Hill":   (-20.2330, 57.4670),
    "Phoenix & St-Jean":         (-20.2700, 57.4980),
    "Quatre Bornes & Belle Rose": (-20.2650, 57.4790),
    "Curepipe":                  (-20.3150, 57.5250),
}
FALLBACK_CENTRE = (-20.2000, 57.5000)

# Positions verified by hand; these keep precision "exact".
KNOWN_COORDS: dict[str, tuple[float, float]] = {
    "caudan_north": (-20.1626, 57.4939),
    "caudan_south": (-20.1640, 57.4945),
    "la_chaussee":  (-20.1608, 57.4972),
    "casernes":     (-20.1590, 57.4960),
    # Added 2026-08-06 (map-accuracy audit). Web-search-sourced, specifically
    # named to the landmark/road itself (not a general town centroid) - see
    # ..\capstone-notes\2026-08-06-camera-location-audit.md for the sources
    # and confidence notes on each. Everything else in the catalogue stayed
    # at its region centroid: general "which town is this in" results were
    # not treated as junction-level precision.
    "place_darmes":     (-20.1619577, 57.5021109),
    "pailles_motorway": (-20.196876, 57.483271),
}

# Stream identifier → camera_id, where the derived id would not match the ids
# already used in the database, capacity table and route-suggestion map.
ID_OVERRIDES: dict[str, str] = {
    "CASERNES_BRABANT_STREET": "casernes",
    "LA_CHAUSSEE_STREET":      "la_chaussee",
}

# Words that should not be title-cased naively when building display names.
NAME_FIXUPS = {
    "Cdm": "CDM", "St": "St", "Darmes": "d'Armes", "Gard": "Gare",
    "Quatres": "Quatre", "Metro": "Metro", "1": "1", "2": "2",
}


def slugify(stream: str) -> str:
    """CAUDAN_NORTH -> caudan_north, honouring ID_OVERRIDES."""
    if stream in ID_OVERRIDES:
        return ID_OVERRIDES[stream]
    return stream.lower()


def display_name(stream: str) -> str:
    """CAUDAN_NORTH -> 'Caudan North'."""
    words = [NAME_FIXUPS.get(w.capitalize(), w.capitalize())
             for w in stream.replace("_", " ").split()]
    return " ".join(words)


def scrape() -> list[dict]:
    """Return the camera list in page order: id, name, region, stream URL."""
    try:
        import requests
    except ImportError:
        sys.exit("requests is not installed. Run: pip install requests")

    print(f"Fetching {MYT_PAGE} …")
    resp = requests.get(MYT_PAGE, timeout=30,
                        headers={"User-Agent": "Mozilla/5.0 (TrafficWatch catalogue)"})
    resp.raise_for_status()
    html = resp.text
    print(f"  {len(html):,} bytes")

    # Walk headings and player sourceURLs in document order so each stream can
    # be attributed to the region heading that precedes it.
    cameras: list[dict] = []
    region = None
    pattern = re.compile(
        r'<h([1-6])[^>]*>([^<]{2,70})</h\1>|"sourceURL"\s*:\s*"([^"]+)"')

    for m in pattern.finditer(html):
        if m.group(3):
            url = m.group(3)
            stream_m = re.search(r'/([A-Za-z0-9_]+)\.stream', url)
            if not stream_m:
                print(f"  ! skipping unrecognised sourceURL: {url}")
                continue
            stream = stream_m.group(1)
            cameras.append({
                "camera_id": slugify(stream),
                "name":      display_name(stream),
                "region":    region or "Unknown",
                "source":    url,
                "stream":    stream,
            })
        else:
            text = m.group(2).strip()
            # Region headings are the ones we have centroids for; everything
            # else on the page (nav, footer) is noise.
            if text in REGION_CENTRES:
                region = text

    return cameras


# Radius of the ring that separates cameras sharing a region centroid.
# ~0.0035 degrees is roughly 390 m at Mauritius' latitude — inside the town the
# camera is named for, so the marker still means "in this region" and nothing
# more precise. Without it six Curepipe cameras land on one pixel and the map
# shows 12 markers for 38 cameras.
RING_RADIUS_DEG = 0.0035


def attach_coords(cameras: list[dict]) -> None:
    import math

    for cam in cameras:
        if cam["camera_id"] in KNOWN_COORDS:
            cam["lat"], cam["lng"] = KNOWN_COORDS[cam["camera_id"]]
            cam["coords_precision"] = "exact"
        else:
            cam["lat"], cam["lng"] = REGION_CENTRES.get(cam["region"], FALLBACK_CENTRE)
            cam["coords_precision"] = "approximate"

    # Spread each region's approximate cameras evenly around its centroid so
    # every one is separately visible and clickable. Deterministic, so
    # regenerating the catalogue does not shuffle the map.
    by_region: dict[str, list[dict]] = {}
    for cam in cameras:
        if cam["coords_precision"] == "approximate":
            by_region.setdefault(cam["region"], []).append(cam)

    for region, group in by_region.items():
        if len(group) < 2:
            continue
        clat, clng = REGION_CENTRES.get(region, FALLBACK_CENTRE)
        # Longitude degrees shrink with latitude; correct so the ring is round.
        lng_scale = 1.0 / max(0.2, math.cos(math.radians(clat)))
        for i, cam in enumerate(sorted(group, key=lambda c: c["camera_id"])):
            angle = 2 * math.pi * i / len(group)
            cam["lat"] = round(clat + RING_RADIUS_DEG * math.sin(angle), 6)
            cam["lng"] = round(clng + RING_RADIUS_DEG * math.cos(angle) * lng_scale, 6)


def render(cameras: list[dict]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    exact = sum(1 for c in cameras if c["coords_precision"] == "exact")
    regions = sorted({c["region"] for c in cameras})

    lines = [
        '"""',
        "detection/camera_catalogue.py",
        "",
        f"GENERATED FILE — do not edit by hand.",
        f"Regenerate with:  python tools/fetch_cameras.py",
        "",
        f"Source : {MYT_PAGE}",
        f"Fetched: {stamp}",
        f"Cameras: {len(cameras)} across {len(regions)} regions "
        f"({exact} with verified coordinates)",
        "",
        "coords_precision is 'exact' for hand-verified positions and",
        "'approximate' for cameras placed at their region centroid because MYT",
        "publishes no coordinates. The UI distinguishes the two so an",
        "approximate marker is never read as a surveyed position.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        f"SOURCE_PAGE = {MYT_PAGE!r}",
        f"FETCHED_AT = {stamp!r}",
        "",
        "CAMERAS: list[dict] = [",
    ]

    current_region = None
    for cam in cameras:
        if cam["region"] != current_region:
            current_region = cam["region"]
            lines.append(f"    # ── {current_region} " + "─" * max(0, 56 - len(current_region)))
        lines.append("    {")
        lines.append(f'        "camera_id":        {cam["camera_id"]!r},')
        lines.append(f'        "name":             {cam["name"]!r},')
        lines.append(f'        "region":           {cam["region"]!r},')
        lines.append(f'        "lat":              {cam["lat"]},')
        lines.append(f'        "lng":              {cam["lng"]},')
        lines.append(f'        "coords_precision": {cam["coords_precision"]!r},')
        lines.append(f'        "source":           {cam["source"]!r},')
        lines.append(f'        "origin":           "myt.trafficwatch",')
        lines.append("    },")
    lines += [
        "]",
        "",
        "CAMERAS_BY_ID: dict[str, dict] = {c['camera_id']: c for c in CAMERAS}",
        "",
        "REGIONS: list[str] = " + repr(regions),
        "",
        "",
        "def get(camera_id: str) -> dict | None:",
        '    """Catalogue entry for a camera, or None if unknown."""',
        "    return CAMERAS_BY_ID.get(camera_id)",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="print a summary without writing the catalogue")
    args = ap.parse_args()

    cameras = scrape()
    if not cameras:
        sys.exit("No cameras found — MYT's page layout may have changed. "
                 "The existing catalogue has been left untouched.")

    # Duplicate ids would silently drop cameras from the dict lookup.
    seen: dict[str, int] = {}
    for cam in cameras:
        seen[cam["camera_id"]] = seen.get(cam["camera_id"], 0) + 1
    dupes = {k: v for k, v in seen.items() if v > 1}
    if dupes:
        sys.exit(f"Duplicate camera ids would be generated: {dupes}. "
                 "Add entries to ID_OVERRIDES to disambiguate.")

    attach_coords(cameras)

    by_region: dict[str, int] = {}
    for cam in cameras:
        by_region[cam["region"]] = by_region.get(cam["region"], 0) + 1

    print(f"\n{len(cameras)} cameras across {len(by_region)} regions:")
    for region, n in by_region.items():
        print(f"  {region:<28} {n:>2}")

    exact = [c["camera_id"] for c in cameras if c["coords_precision"] == "exact"]
    print(f"\ncoordinates: {len(exact)} exact ({', '.join(exact)}), "
          f"{len(cameras) - len(exact)} at region centroid")

    if args.dry_run:
        print("\n--dry-run: catalogue not written.")
        return

    OUTPUT.write_text(render(cameras), encoding="utf-8")
    print(f"\nWrote {OUTPUT.relative_to(OUTPUT.parent.parent)}")


if __name__ == "__main__":
    main()
