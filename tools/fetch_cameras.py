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

    python tools/fetch_cameras.py                       # regenerate the catalogue
    python tools/fetch_cameras.py --dry-run             # show what would change
    python tools/fetch_cameras.py --skip-coord-validation  # skip the OSM gate below

Coordinates
-----------
MYT's page carries no coordinates. Cameras with a KNOWN_COORDS entry below
are marked `exact` - each one checked against tools/validate_camera_coords.py
(a real OSM road within 40m) before being trusted, not just plausible-
sounding. Everything else is placed at the centroid of the region MYT groups
it under and marked `unverified`.

Every run re-validates KNOWN_COORDS against OpenStreetMap before writing
anything (validate_known_coords(), below) and refuses to write the catalogue
if any entry no longer sits near a real road. This is the check that would
have caught a camera marker sitting in Port Louis harbour before it ever
reached the map - a prior bounding-box sea check called that "fine". Results
are cached, so this costs real network time only the first run after
KNOWN_COORDS changes.

Earlier versions of this generator spread unverified cameras around their
region centroid on a ring, so each got its own pixel on the map. That was a
mistake worth naming: it invented a precise-looking position for a camera
whose position is not known, and it looked exactly like a bug to anyone
using the map (a perfect circle of evenly-spaced dots over open ground). An
`unverified` camera now shares the exact region centroid with every other
unverified camera in that region - main.py aggregates these into one marker
per region rather than rendering several identical-looking individual dots.
Replace a camera's entry with a real, validated coordinate and it graduates
to `exact` and its own marker automatically.
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

# Positions verified by hand; these keep precision "exact". Every entry
# below is checked against tools/validate_camera_coords.py - a real OSM
# road within 40m - before being trusted, not just plausible-sounding.
#
# "Verified by hand" turned out to be weaker than it sounds: a bounding-box
# sea check previously reported all 38 as fine while one sat in Port Louis
# harbour. la_chaussee and casernes below were both re-derived on
# 2026-08-06 for exactly that reason - their old values passed a road-
# proximity check by coincidence (an unrelated unnamed road happened to be
# nearby) while the actual *named* street they're supposed to be on was
# 500m-1km away. See ..\capstone-notes\2026-08-06-camera-positions-verified.md
# for the before/after evidence on every corrected or newly-added entry.
KNOWN_COORDS: dict[str, tuple[float, float]] = {
    "caudan_north": (-20.1626, 57.4939),   # 6.6m from "Caudan Approach Road" - name match, unchanged
    "caudan_south": (-20.1640, 57.4945),   # 6.9m from an unnamed link road; close to the real "Caudan" quarter centroid - unchanged
    # Re-derived 2026-08-06: the old value (-20.1608, 57.4972) was 0.2m from
    # an unnamed service road but ~500m from every real "Rue de la Chaussée"
    # segment in OSM - passed the proximity check by coincidence, not
    # because it was on the named street. This point sits directly on it.
    "la_chaussee": (-20.1640344, 57.5003395),
    # Re-derived 2026-08-06: the old value (-20.1590, 57.4960) failed the
    # road-proximity check outright (51.7m to the nearest road) and sat
    # ~1km from the nearest real "Rue Brabant" segment - this is very
    # likely the harbour-adjacent marker flagged directly from a map
    # screenshot. This point is the spot on Rue Brabant closest to "Les
    # Casernes" (the historical barracks area the camera is named for,
    # confirmed via a real business address in that locality) - it lands
    # exactly at Rue Brabant's junction with Rue Lord Kitchener, which is
    # why the validator names the latter as the nearest road.
    "casernes": (-20.1654977, 57.4921943),
    # Added 2026-08-06 (map-accuracy audit). Web-search-sourced, specifically
    # named to the landmark/road itself (not a general town centroid) - see
    # ..\capstone-notes\2026-08-06-camera-location-audit.md for the sources
    # and confidence notes on each. Everything else in the catalogue stayed
    # at its region centroid: general "which town is this in" results were
    # not treated as junction-level precision.
    "place_darmes":     (-20.1619577, 57.5021109),
    "pailles_motorway": (-20.196876, 57.483271),
    # Promoted 2026-08-06 (Overpass/Nominatim geocoding pass, Step 3).
    # Each is a precise, specifically-named OSM landmark match for the
    # camera's own name - not a suburb/village centroid - and lands
    # directly on or beside a real road. See the positions-verified note
    # for what was tried and rejected for the cameras NOT promoted here
    # (terre_rouge_motorway, ebene_motorway, queen_street_1/2, floreal_road,
    # palma_road, beau_bassin_main_road, curepipe_suisse_junction) - each of
    # those only turned up a generic town/suburb centroid or no match at
    # all, which the task was explicit is not good enough to promote on.
    "reduit_flyover":  (-20.2316888, 57.5000930),   # "Réduit Junction" by name, 0.0m
    "jan_palach_north": (-20.3162102, 57.5261022),  # "Jan Palach North" by name, 16.6m from Jerningham Street
    "jan_palach_south": (-20.3178309, 57.5272002),  # "Jan Palach South" by name, 9.1m
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


def validate_known_coords() -> None:
    """Gate: every KNOWN_COORDS entry must sit within a real OSM road, or the
    catalogue is not written.

    This is the check that would have caught casernes sitting in Port Louis
    harbour before it ever reached the map - a bounding-box sea check called
    it "fine" because a rectangle around the island contains the harbour.
    Results are cached (see tools/validate_camera_coords.py), so this only
    costs real Overpass time the first run after a KNOWN_COORDS entry
    changes or is added; every other run answers from disk.
    """
    from tools import validate_camera_coords as validator

    print(f"\nValidating {len(KNOWN_COORDS)} exact coordinates against OpenStreetMap …")
    failures = []
    for camera_id, (lat, lng) in KNOWN_COORDS.items():
        result = validator.validate_point(camera_id, lat, lng)
        dist = f"{result['distance_m']:.1f}m" if result["distance_m"] is not None else "n/a"
        road = result["nearest_road"] or "(no road found within search radius)"
        print(f"  [{'OK' if result['passed'] else 'FAIL'}] {camera_id:<20} {dist:>8}  {road}")
        if not result["passed"]:
            failures.append((camera_id, dist, road))

    if failures:
        detail = "\n  ".join(f"{cid}: {dist} from nearest road ({road})" for cid, dist, road in failures)
        sys.exit(
            f"\nRefusing to write the catalogue: {len(failures)} KNOWN_COORDS "
            f"entr{'y' if len(failures) == 1 else 'ies'} failed road-proximity validation "
            f"(tools/validate_camera_coords.py, {validator.PASS_THRESHOLD_M}m threshold):\n"
            f"  {detail}\n\n"
            "Fix the coordinate(s) above before regenerating, or pass "
            "--skip-coord-validation if Overpass is unreachable and you accept the risk."
        )


def attach_coords(cameras: list[dict]) -> None:
    """Assign each camera a coordinate and an honest precision label.

    Previous versions of this function spread unverified cameras around
    their region centroid on a ring, purely so they wouldn't all render on
    the same pixel. That produced a visibly perfect circle of evenly-spaced
    dots over open ground - which reads as a deliberate, surveyed layout to
    anyone looking at the map, not as "unknown". It invented a precise-
    looking position for a camera whose position is not known, which is
    the opposite of what "approximate" was supposed to signal.

    Cameras with no verified coordinate now get the region centroid itself
    - the same point as every other unverified camera in that region - and
    are labelled "unverified", not "approximate". main.py groups these into
    one aggregate marker per region rather than rendering 6 identical-
    looking dots for 6 cameras nobody has actually checked. See Step 4 of
    ..\\capstone-notes\\2026-08-06-camera-positions-verified.md.
    """
    for cam in cameras:
        if cam["camera_id"] in KNOWN_COORDS:
            cam["lat"], cam["lng"] = KNOWN_COORDS[cam["camera_id"]]
            cam["coords_precision"] = "exact"
        else:
            cam["lat"], cam["lng"] = REGION_CENTRES.get(cam["region"], FALLBACK_CENTRE)
            cam["coords_precision"] = "unverified"


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
        "coords_precision is 'exact' for positions checked against",
        "tools/validate_camera_coords.py (a real OSM road within 40m) and",
        "'unverified' for cameras placed at their region centroid because MYT",
        "publishes no coordinates and nobody has verified this one yet. The UI",
        "aggregates unverified cameras into one per-region marker rather than",
        "plotting individual positions nobody has confirmed.",
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
    ap.add_argument("--skip-coord-validation", action="store_true",
                    help="skip the OSM road-proximity gate on KNOWN_COORDS "
                         "(only for when Overpass is unreachable)")
    args = ap.parse_args()

    if args.skip_coord_validation:
        print("--skip-coord-validation set: KNOWN_COORDS will not be checked "
              "against OpenStreetMap. Do not rely on this for a real release.")
    else:
        validate_known_coords()

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
