"""
tools/validate_camera_coords.py
Check whether a camera's coordinate actually sits on or beside a road.

Why this exists
----------------
The previous location audit (2026-08-06) checked catalogued coordinates
against a bounding box covering all of mainland Mauritius and reported zero
failures. That check could not fail in any way that mattered: Port Louis is
a coastal harbour city, so a rectangle around the island contains the
harbour, every bay, and all four corners. A camera marker was sitting in the
water of Port Louis harbour at the time, and the bounding-box check said
"fine" - it was structurally incapable of finding that.

This checks something that can actually fail: is there a real road, per
OpenStreetMap, within PASS_THRESHOLD_M of the point? If nothing is found,
the point is in water, in a field, or on a rooftop, and the answer is no.

Usage
-----
    python tools/validate_camera_coords.py                  # validate the current catalogue
    python tools/validate_camera_coords.py --lat -20.16 --lng 57.50 --id adhoc

Being a considerate API consumer
---------------------------------
Overpass is a free, shared, unauthenticated service. Every query result is
cached to disk (validate_camera_coords_cache/), keyed by rounded
coordinates, so re-running this costs nothing once a point has been checked
once. Uncached requests are rate-limited to about one per second. The
User-Agent identifies the project and links back to it, per Overpass's own
usage guidelines - not just to avoid being blocked, but because an
unidentified scraper hitting a free public service is bad practice on its
own terms.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import sys
import time

import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = (
    "IBL-Capstone-MA02-2026-CameraValidator/1.0 "
    "(+https://github.com/YadhavRamsahye/IBL_Capstone_MA02_2026)"
)
CACHE_DIR = pathlib.Path(__file__).resolve().parent / "validate_camera_coords_cache"

# Fetched wider than the pass threshold so a point near the *wrong* road is
# still visible to a human reader, rather than just reporting "no road found"
# when the real story is "80m from Such-and-such Street, not the road named".
SEARCH_RADIUS_M = 150
PASS_THRESHOLD_M = 40
# The public instance answered a real ~1-in-3 rate of 429/504 at 1.0s spacing
# during this session - measured, not assumed. 2.5s cleared it up.
RATE_LIMIT_S = 2.5

_EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _to_local_xy(lat: float, lon: float, origin_lat: float, origin_lon: float) -> tuple[float, float]:
    """Equirectangular projection around origin, in metres. Fine at this scale
    (a few hundred metres) and avoids doing real geodesics for a point-to-
    segment distance that only needs to be accurate to a metre or two."""
    x = math.radians(lon - origin_lon) * math.cos(math.radians(origin_lat)) * _EARTH_RADIUS_M
    y = math.radians(lat - origin_lat) * _EARTH_RADIUS_M
    return x, y


def point_to_segment_distance_m(
    lat: float, lon: float, a: tuple[float, float], b: tuple[float, float]
) -> float:
    """Shortest distance from (lat, lon) to the segment a-b (each (lat, lon))."""
    px, py = _to_local_xy(lat, lon, lat, lon)  # origin at the query point: (0, 0)
    ax, ay = _to_local_xy(a[0], a[1], lat, lon)
    bx, by = _to_local_xy(b[0], b[1], lat, lon)

    abx, aby = bx - ax, by - ay
    length_sq = abx * abx + aby * aby
    if length_sq == 0:
        return math.hypot(px - ax, py - ay)

    t = max(0.0, min(1.0, ((px - ax) * abx + (py - ay) * aby) / length_sq))
    cx, cy = ax + t * abx, ay + t * aby
    return math.hypot(px - cx, py - cy)


def point_to_way_distance_m(lat: float, lon: float, geometry: list[dict]) -> float:
    """Minimum distance from (lat, lon) to any segment of a way's geometry."""
    points = [(pt["lat"], pt["lon"]) for pt in geometry]
    if len(points) == 1:
        return haversine_m(lat, lon, points[0][0], points[0][1])
    return min(
        point_to_segment_distance_m(lat, lon, points[i], points[i + 1])
        for i in range(len(points) - 1)
    )


def _cache_key(lat: float, lon: float) -> str:
    # Rounded so nearby-but-not-identical queries (e.g. re-runs after a tiny
    # coordinate nudge) still hit the same cache entry when it wouldn't
    # change the Overpass result meaningfully.
    rounded = f"{lat:.5f},{lon:.5f}"
    return hashlib.sha1(rounded.encode()).hexdigest()[:16]


def query_overpass(lat: float, lon: float, radius: int = SEARCH_RADIUS_M) -> dict:
    CACHE_DIR.mkdir(exist_ok=True)
    cache_file = CACHE_DIR / f"{_cache_key(lat, lon)}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    query = f"""
    [out:json][timeout:25];
    way(around:{radius},{lat},{lon})["highway"];
    out tags geom;
    """
    # The public Overpass instance is shared and occasionally answers a
    # transient 429/504 under load, not a sign the query itself is wrong -
    # worth a few backed-off retries before giving up.
    max_attempts = 6
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            resp = requests.post(
                OVERPASS_URL,
                data={"data": query},
                headers={"User-Agent": USER_AGENT},
                timeout=30,
            )
            if resp.status_code in (429, 502, 503, 504):
                raise requests.exceptions.HTTPError(
                    f"{resp.status_code} (transient)", response=resp
                )
            resp.raise_for_status()
            data = resp.json()
            cache_file.write_text(json.dumps(data), encoding="utf-8")
            time.sleep(RATE_LIMIT_S)
            return data
        except (requests.exceptions.RequestException, ValueError) as exc:
            last_exc = exc
            delay = RATE_LIMIT_S * (2 ** attempt) + 2
            print(f"  Overpass request failed ({exc}) - retrying in {delay:.0f}s "
                  f"(attempt {attempt + 1}/{max_attempts}) …", file=sys.stderr)
            time.sleep(delay)
    raise RuntimeError(f"Overpass query for ({lat}, {lon}) failed after {max_attempts} attempts") from last_exc


def validate_point(camera_id: str, lat: float, lon: float) -> dict:
    """Return the validation result for one coordinate.

    passed=True means a real OSM road was found within PASS_THRESHOLD_M.
    nearest_road/distance_m/highway_type are reported regardless of pass/
    fail, so a point that's simply on the wrong road is still visible.
    """
    data = query_overpass(lat, lon)
    best = None  # (name, distance_m, highway_type)

    for el in data.get("elements", []):
        tags = el.get("tags", {})
        if "highway" not in tags:
            continue
        geometry = el.get("geometry")
        if not geometry:
            continue
        dist = point_to_way_distance_m(lat, lon, geometry)
        name = tags.get("name") or tags.get("ref") or f"(unnamed {tags['highway']})"
        if best is None or dist < best[1]:
            best = (name, dist, tags["highway"])

    if best is None:
        return {
            "camera_id": camera_id, "lat": lat, "lng": lon,
            "passed": False, "nearest_road": None,
            "distance_m": None, "highway_type": None,
        }

    name, dist, hwtype = best
    return {
        "camera_id": camera_id, "lat": lat, "lng": lon,
        "passed": dist <= PASS_THRESHOLD_M,
        "nearest_road": name,
        "distance_m": round(dist, 1),
        "highway_type": hwtype,
    }


def validate_catalogue() -> list[dict]:
    from detection import camera_catalogue

    results = []
    total = len(camera_catalogue.CAMERAS)
    for i, cam in enumerate(camera_catalogue.CAMERAS, 1):
        print(f"[{i}/{total}] {cam['camera_id']} …", file=sys.stderr, flush=True)
        result = validate_point(cam["camera_id"], cam["lat"], cam["lng"])
        result["name"] = cam["name"]
        result["region"] = cam["region"]
        result["catalogue_precision"] = cam["coords_precision"]
        results.append(result)
    return results


def print_table(results: list[dict]) -> None:
    passed = sum(1 for r in results if r["passed"])
    print(f"\n{'camera_id':<26} {'precision':<12} {'lat':>11} {'lng':>11} "
          f"{'pass':<5} {'dist_m':>7}  nearest_road")
    print("-" * 110)
    for r in results:
        dist_str = f"{r['distance_m']:.1f}" if r["distance_m"] is not None else "  n/a"
        road_str = r["nearest_road"] or "(no road found within search radius)"
        precision = r.get("catalogue_precision", "n/a")   # absent in --lat/--lng ad-hoc mode
        print(f"{r['camera_id']:<26} {precision:<12} {r['lat']:>11.6f} {r['lng']:>11.6f} "
              f"{'PASS' if r['passed'] else 'FAIL':<5} {dist_str:>7}  {road_str}")
    print("-" * 110)
    print(f"{passed}/{len(results)} passed (road found within {PASS_THRESHOLD_M}m)\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lat", type=float, help="validate a single ad-hoc coordinate instead of the catalogue")
    ap.add_argument("--lng", type=float)
    ap.add_argument("--id", default="adhoc", help="label for --lat/--lng mode")
    ap.add_argument("--json", action="store_true", help="print raw JSON instead of a table")
    args = ap.parse_args()

    if args.lat is not None and args.lng is not None:
        results = [validate_point(args.id, args.lat, args.lng)]
    else:
        results = validate_catalogue()

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print_table(results)


if __name__ == "__main__":
    main()
