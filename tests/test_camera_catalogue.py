"""
Tests for detection/camera_catalogue.py - the generated table of known
cameras (id, coordinates, region, stream source).

This is a generated data file, so these tests are contract/invariant checks
on the data itself (uniqueness, required fields, coordinate sanity) rather
than logic tests - the kind of check that would have caught a bad entry from
tools/fetch_cameras.py before it reached the map UI. Previously untested.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from detection.camera_catalogue import CAMERAS, CAMERAS_BY_ID, REGIONS, get
from evidence import print_evidence

REQUIRED_KEYS = {"camera_id", "name", "region", "lat", "lng",
                  "coords_precision", "source", "origin"}

# Generous bounding box around Mauritius (the whole catalogue is Mauritian
# traffic cameras); catches a badly transcribed lat/lng, not precise siting.
MAURITIUS_LAT_RANGE = (-20.6, -19.9)
MAURITIUS_LNG_RANGE = (57.2, 57.9)


class CatalogueDataIntegrityTests(unittest.TestCase):
    def test_catalogue_is_not_empty(self) -> None:
        result = len(CAMERAS)
        print_evidence("TC-137", "Camera catalogue is not empty",
                       "CAMERAS list", "> 0", result, passed=result > 0)
        self.assertGreater(result, 0)

    def test_every_camera_has_the_required_fields(self) -> None:
        missing_by_camera = {c.get("camera_id"): REQUIRED_KEYS - c.keys()
                             for c in CAMERAS if REQUIRED_KEYS - c.keys()}
        print_evidence("TC-138", "Every camera has the required fields",
                       f"{len(CAMERAS)} cameras, required = {sorted(REQUIRED_KEYS)}",
                       {}, missing_by_camera)
        for camera in CAMERAS:
            missing = REQUIRED_KEYS - camera.keys()
            self.assertFalse(missing, f"{camera.get('camera_id')} missing {missing}")

    def test_camera_ids_are_unique(self) -> None:
        ids = [c["camera_id"] for c in CAMERAS]
        duplicates = {cid for cid in ids if ids.count(cid) > 1}
        print_evidence("TC-139", "Camera IDs are unique across the catalogue",
                       f"{len(ids)} camera_id values", set(), duplicates)
        self.assertEqual(duplicates, set())

    def test_camera_ids_are_non_empty_strings(self) -> None:
        offenders = [c["camera_id"] for c in CAMERAS
                    if not isinstance(c["camera_id"], str) or not c["camera_id"].strip()]
        print_evidence("TC-140", "Every camera_id is a non-empty string",
                       f"{len(CAMERAS)} cameras", [], offenders)
        for camera in CAMERAS:
            self.assertIsInstance(camera["camera_id"], str)
            self.assertTrue(camera["camera_id"].strip())

    def test_coords_precision_is_a_known_value(self) -> None:
        offenders = [c["camera_id"] for c in CAMERAS
                    if c["coords_precision"] not in ("exact", "unverified")]
        print_evidence("TC-141", "coords_precision is always 'exact' or 'unverified'",
                       f"{len(CAMERAS)} cameras", [], offenders)
        for camera in CAMERAS:
            self.assertIn(
                camera["coords_precision"], ("exact", "unverified"),
                f"{camera['camera_id']} has unexpected coords_precision",
            )

    def test_coordinates_are_within_mauritius(self) -> None:
        offenders = [
            c["camera_id"] for c in CAMERAS
            if not (MAURITIUS_LAT_RANGE[0] <= c["lat"] <= MAURITIUS_LAT_RANGE[1]
                    and MAURITIUS_LNG_RANGE[0] <= c["lng"] <= MAURITIUS_LNG_RANGE[1])
        ]
        print_evidence("TC-142", "Every camera's coordinates fall within Mauritius",
                       f"{len(CAMERAS)} cameras, lat in {MAURITIUS_LAT_RANGE}, "
                       f"lng in {MAURITIUS_LNG_RANGE}", [], offenders)
        for camera in CAMERAS:
            lat, lng = camera["lat"], camera["lng"]
            self.assertTrue(
                MAURITIUS_LAT_RANGE[0] <= lat <= MAURITIUS_LAT_RANGE[1],
                f"{camera['camera_id']} lat {lat} outside Mauritius",
            )
            self.assertTrue(
                MAURITIUS_LNG_RANGE[0] <= lng <= MAURITIUS_LNG_RANGE[1],
                f"{camera['camera_id']} lng {lng} outside Mauritius",
            )

    def test_source_is_an_https_stream_url(self) -> None:
        offenders = [c["camera_id"] for c in CAMERAS if not c["source"].startswith("https://")]
        print_evidence("TC-143", "Every camera's stream source is an https:// URL",
                       f"{len(CAMERAS)} cameras", [], offenders)
        for camera in CAMERAS:
            self.assertTrue(
                camera["source"].startswith("https://"),
                f"{camera['camera_id']} source is not https: {camera['source']}",
            )

    def test_every_camera_region_is_a_declared_region(self) -> None:
        offenders = [c["camera_id"] for c in CAMERAS if c["region"] not in REGIONS]
        print_evidence("TC-144", "Every camera's region is a declared region",
                       f"{len(CAMERAS)} cameras, {len(REGIONS)} declared regions",
                       [], offenders)
        for camera in CAMERAS:
            self.assertIn(camera["region"], REGIONS,
                          f"{camera['camera_id']} region not in REGIONS")

    def test_regions_list_has_no_duplicates(self) -> None:
        print_evidence("TC-145", "REGIONS list has no duplicate entries",
                       f"REGIONS = {REGIONS}", len(set(REGIONS)), len(REGIONS))
        self.assertEqual(len(REGIONS), len(set(REGIONS)))


class CamerasByIdTests(unittest.TestCase):
    def test_by_id_index_matches_the_camera_list(self) -> None:
        print_evidence("TC-146", "CAMERAS_BY_ID index matches the CAMERAS list 1:1",
                       f"{len(CAMERAS)} cameras", len(CAMERAS), len(CAMERAS_BY_ID))
        self.assertEqual(len(CAMERAS_BY_ID), len(CAMERAS))
        for camera in CAMERAS:
            self.assertIs(CAMERAS_BY_ID[camera["camera_id"]], camera)


class GetTests(unittest.TestCase):
    def test_get_returns_the_matching_entry(self) -> None:
        known_id = CAMERAS[0]["camera_id"]
        result = get(known_id)
        print_evidence("TC-147", "get() returns the matching catalogue entry",
                       f"camera_id = {known_id!r}", CAMERAS[0]["name"],
                       result["name"] if result else None)
        self.assertEqual(result, CAMERAS[0])

    def test_get_returns_none_for_unknown_camera(self) -> None:
        result = get("definitely_not_a_real_camera_id")
        print_evidence("TC-148", "get() returns None for an unknown camera_id",
                       "camera_id = 'definitely_not_a_real_camera_id'", None, result)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
