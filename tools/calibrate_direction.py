"""
tools/calibrate_direction.py
Learn where a camera's two directions of travel separate.

Why this is needed
------------------
detection/direction.py classifies vehicles by *position* - which side of a
dividing line they sit on - because position works when traffic is stopped and
motion does not. But the line itself has to come from somewhere, and it depends
on how each camera is mounted and which way it points.

This tool derives it from the camera's own footage: watch traffic flow, record
where vehicles are and which way they move, then find the line that best
separates the two streams.

Method
------
1. Grab frames and track vehicles (reusing the pipeline's IoU tracker).
2. Keep every track that moved far enough to have a real direction, recording
   its start position and displacement.
3. Find the dominant axis of travel - the direction along which movement varies
   most - via the principal eigenvector of the displacement covariance.
4. Split tracks by the sign of their movement along that axis.
5. The dividing line sits midway between the two groups' centroids, with its
   normal pointing from the negative group toward the positive one.

Step 5 is the part that makes stopped traffic classifiable: the output is
geometry, not motion, so it still applies when nothing is moving.

Usage
-----
    python tools/calibrate_direction.py --camera caudan_south --minutes 10

Run it while traffic is *flowing* - during a jam there is no motion to learn
from. Longer samples give a better line; ten minutes across a busy period is a
reasonable start. Paste the printed block into CAMERA_DIRECTIONS.

Reading the output
------------------
`separation` is how cleanly the two groups divide, as the ratio of the gap
between centroids to their combined spread. Above ~1.5 the streams are clearly
apart and the line is trustworthy. Below ~0.8 they overlap heavily - usually a
camera pointing along the road rather than across it, where the two directions
genuinely occupy the same pixels and no line can separate them. The tool says so
rather than emitting a config that would misclassify half the traffic.
"""

from __future__ import annotations

import argparse
import math
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

MIN_TRACKS = 40          # below this the fit is noise
GOOD_SEPARATION = 1.5
POOR_SEPARATION = 0.8


def _principal_axis(vectors: list[tuple[float, float]]) -> tuple[float, float]:
    """Unit vector along which the displacements vary most (2x2 eigenvector)."""
    n = len(vectors)
    mx = sum(v[0] for v in vectors) / n
    my = sum(v[1] for v in vectors) / n
    sxx = sum((v[0] - mx) ** 2 for v in vectors) / n
    syy = sum((v[1] - my) ** 2 for v in vectors) / n
    sxy = sum((v[0] - mx) * (v[1] - my) for v in vectors) / n

    # Larger eigenvalue of [[sxx, sxy], [sxy, syy]].
    tr, det = sxx + syy, sxx * syy - sxy * sxy
    disc = max(0.0, tr * tr / 4 - det)
    lam = tr / 2 + math.sqrt(disc)

    vx, vy = (lam - syy, sxy) if abs(sxy) > 1e-9 else ((1.0, 0.0) if sxx >= syy else (0.0, 1.0))
    norm = math.hypot(vx, vy) or 1.0
    return (vx / norm, vy / norm)


def _centroid(points: list[tuple[float, float]]) -> tuple[float, float]:
    n = len(points)
    return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)


def _spread(points: list[tuple[float, float]], axis: tuple[float, float]) -> float:
    """Standard deviation of `points` projected onto `axis`."""
    proj = [p[0] * axis[0] + p[1] * axis[1] for p in points]
    m = sum(proj) / len(proj)
    return math.sqrt(sum((x - m) ** 2 for x in proj) / len(proj))


def collect(camera_id: str, minutes: float) -> list[dict]:
    import cv2  # noqa: F401  (imported by the pipeline modules below)
    from ultralytics import YOLO

    from detection.hls_pipeline import (
        CONF_THRESHOLD, IOU_THRESHOLD, IMGSZ, MODEL_PATH, FRAME_INTERVAL,
        DEFAULT_WIDTH, DEFAULT_HEIGHT, _grab_single_frame,
    )
    from detection.severity import VEHICLE_CLASSES
    from detection.stationary_tracker import StationaryTracker
    from detection.direction import MIN_MOTION_PX
    from detection.trafficwatch import discover_cameras

    cams = {c["camera_id"]: c for c in discover_cameras()}
    if camera_id not in cams:
        sys.exit(f"Unknown camera {camera_id!r}. Known: {', '.join(sorted(cams))}")
    url = cams[camera_id]["source"]

    print(f"Loading {MODEL_PATH} …")
    model = YOLO(MODEL_PATH)
    tracker = StationaryTracker(frame_interval=FRAME_INTERVAL)

    samples: list[dict] = []
    seen: set[int] = set()
    deadline = time.time() + minutes * 60
    frames = 0

    print(f"Sampling {camera_id} for {minutes:g} minutes - traffic must be moving.\n")
    while time.time() < deadline:
        frame = _grab_single_frame([url], DEFAULT_WIDTH, DEFAULT_HEIGHT)
        if frame is None:
            time.sleep(FRAME_INTERVAL)
            continue
        frames += 1

        results = model.predict(source=frame, conf=CONF_THRESHOLD,
                                iou=IOU_THRESHOLD, imgsz=IMGSZ, verbose=False)
        boxes, classes = [], []
        for r in results:
            if r.boxes is None:
                continue
            for i, c in enumerate(r.boxes.cls.tolist()):
                if int(c) in VEHICLE_CLASSES:
                    boxes.append(r.boxes.xyxy.tolist()[i])
                    classes.append(int(c))

        tracker.update(boxes, classes)
        for t in tracker.visible_tracks():
            dx, dy = t.displacement
            if math.hypot(dx, dy) < MIN_MOTION_PX:
                continue
            key = id(t)
            if key in seen:                    # record each track once, at its
                continue                       # furthest travelled point
            seen.add(key)
            samples.append({"origin": t.origin, "centre": t.centre,
                            "disp": (dx, dy)})

        remaining = int(deadline - time.time())
        print(f"\r  frames={frames}  tracks with motion={len(samples)}  "
              f"{remaining//60}m{remaining%60:02d}s left ", end="", flush=True)
        time.sleep(FRAME_INTERVAL)

    print()
    return samples


def fit(camera_id: str, samples: list[dict]) -> None:
    if len(samples) < MIN_TRACKS:
        sys.exit(f"\nOnly {len(samples)} moving tracks (need {MIN_TRACKS}). "
                 "Sample for longer, or at a busier time.")

    axis = _principal_axis([s["disp"] for s in samples])

    pos, neg = [], []
    for s in samples:
        dot = s["disp"][0] * axis[0] + s["disp"][1] * axis[1]
        (pos if dot > 0 else neg).append(s)

    print(f"\nDominant axis of travel : ({axis[0]:+.2f}, {axis[1]:+.2f})")
    print(f"Tracks with axis        : {len(pos)}")
    print(f"Tracks against axis     : {len(neg)}")

    if not pos or not neg:
        sys.exit("\nAll traffic moved the same way - this looks like a one-way "
                 "road, or the sample only caught one phase. No split needed.")

    c_pos = _centroid([s["centre"] for s in pos])
    c_neg = _centroid([s["centre"] for s in neg])

    # Normal points from the negative group toward the positive one, so
    # side(centre) == 1 means "travelling with the axis".
    nx, ny = c_pos[0] - c_neg[0], c_pos[1] - c_neg[1]
    gap = math.hypot(nx, ny)
    if gap < 1e-6:
        sys.exit("\nBoth directions share the same centroid - the camera looks "
                 "along the road rather than across it, so no line can "
                 "separate them. Direction cannot be derived for this camera.")
    normal = (nx / gap, ny / gap)
    point = ((c_pos[0] + c_neg[0]) / 2.0, (c_pos[1] + c_neg[1]) / 2.0)

    spread = (_spread([s["centre"] for s in pos], normal)
              + _spread([s["centre"] for s in neg], normal)) / 2.0
    separation = gap / spread if spread > 1e-6 else float("inf")

    print(f"Centroid gap            : {gap:.1f}px")
    print(f"Separation quality      : {separation:.2f}", end="  ")
    if separation >= GOOD_SEPARATION:
        print("(clean split - trustworthy)")
    elif separation >= POOR_SEPARATION:
        print("(marginal - verify against footage before relying on it)")
    else:
        print("(POOR - the two directions overlap)")
        print("\nThe streams are not separable by position on this camera. "
              "Leave it uncalibrated: a single combined figure is honest, "
              "whereas this line would misclassify a large share of vehicles.")
        return

    # Name directions from the axis: screen y grows downward.
    horiz = abs(axis[0]) >= abs(axis[1])
    pos_label = ("eastbound" if axis[0] > 0 else "westbound") if horiz else \
                ("southbound" if axis[1] > 0 else "northbound")
    neg_label = {"eastbound": "westbound", "westbound": "eastbound",
                 "northbound": "southbound", "southbound": "northbound"}[pos_label]

    print("\n" + "=" * 68)
    print("Paste into CAMERA_DIRECTIONS in detection/direction.py:\n")
    print(f'    "{camera_id}": DirectionConfig(')
    print(f'        positive_label="{pos_label}",')
    print(f'        negative_label="{neg_label}",')
    print(f'        divider=Divider(point=({point[0]:.1f}, {point[1]:.1f}),')
    print(f'                        normal=({normal[0]:+.3f}, {normal[1]:+.3f})),')
    print("    ),")
    print("=" * 68)
    print("\nLabels are inferred from image axes and assume the camera is "
          "roughly north-up. Check them against the real road before "
          "committing - a mislabelled direction is worse than none.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", required=True)
    ap.add_argument("--minutes", type=float, default=10.0)
    args = ap.parse_args()
    fit(args.camera, collect(args.camera, args.minutes))


if __name__ == "__main__":
    main()
