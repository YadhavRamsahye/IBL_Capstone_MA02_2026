"""
tools/validate_counts.py
Measure detector accuracy against hand-counted ground truth.

Why
---
Every accuracy figure in this project so far is either an assertion or a number
from synthetic data. This is the only thing that produces a defensible one:
frames you counted by hand, compared against what the detector reports.

Workflow
--------
1. Capture frames from the live cameras:

       python tools/validate_counts.py capture --camera caudan_south --n 20

   Frames are written to ground_truth/<camera>/ along with a labels.csv
   containing one row per frame with an empty `true_count` column.

2. Open each image, count the vehicles yourself, and fill in `true_count`.
   Count what a human would call a vehicle in the drivable area — this is the
   reference, so be consistent and write down your rule.

3. Score the detector against your labels:

       python tools/validate_counts.py score --camera caudan_south

   Reports MAE, bias, MAPE and per-severity agreement.

Interpreting the output
-----------------------
`bias` is the one to watch. A large negative bias means the detector
systematically under-counts, which is the expected failure mode at night and on
distant/occluded vehicles — and it is exactly the effect that makes a busy road
look "free". Run this separately on day and night captures; if the two biases
differ materially, that difference is a measured lighting bias, not a guess.
"""

from __future__ import annotations

import argparse
import csv
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

GT_ROOT = pathlib.Path(__file__).resolve().parent.parent / "ground_truth"


def _labels_path(camera: str) -> pathlib.Path:
    return GT_ROOT / camera / "labels.csv"


# ── capture ───────────────────────────────────────────────────────────────────

def capture(camera: str, n: int, interval: float) -> None:
    import time

    import cv2

    from detection.hls_pipeline import _grab_single_frame, DEFAULT_HEIGHT, DEFAULT_WIDTH
    from detection.trafficwatch import discover_cameras

    cams = {c["camera_id"]: c for c in discover_cameras()}
    if camera not in cams:
        sys.exit(f"Unknown camera '{camera}'. Known: {', '.join(sorted(cams))}")

    url = cams[camera]["source"]
    out_dir = GT_ROOT / camera
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for i in range(n):
        frame = _grab_single_frame([url], DEFAULT_WIDTH, DEFAULT_HEIGHT)
        if frame is None:
            print(f"  [{i+1}/{n}] grab failed, skipping")
            continue
        name = f"{camera}_{i:03d}.jpg"
        cv2.imwrite(str(out_dir / name), frame)
        rows.append({"frame": name, "true_count": ""})
        print(f"  [{i+1}/{n}] saved {name}")
        if i < n - 1:
            time.sleep(interval)

    if not rows:
        sys.exit("No frames captured — is the stream reachable?")

    with _labels_path(camera).open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["frame", "true_count"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} frames to {out_dir}")
    print(f"Now open each image, count the vehicles, and fill in `true_count` "
          f"in {_labels_path(camera).name}. Then run:\n"
          f"    python tools/validate_counts.py score --camera {camera}")


# ── score ─────────────────────────────────────────────────────────────────────

def score(camera: str) -> None:
    import cv2
    from ultralytics import YOLO

    from detection.severity import VEHICLE_CLASSES, classify, pcu_total
    from detection.hls_pipeline import CONF_THRESHOLD, IOU_THRESHOLD, IMGSZ, MODEL_PATH

    path = _labels_path(camera)
    if not path.exists():
        sys.exit(f"No labels at {path}. Run the `capture` step first.")

    with path.open(encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r["true_count"].strip()]
    if not rows:
        sys.exit("No labelled rows — fill in the `true_count` column first.")

    model = YOLO(MODEL_PATH)
    errors, truths, preds = [], [], []
    sev_agree = 0

    print(f"{'frame':<26}{'true':>6}{'pred':>6}{'err':>6}  severity")
    print("-" * 62)
    for row in rows:
        img_path = GT_ROOT / camera / row["frame"]
        frame = cv2.imread(str(img_path))
        if frame is None:
            print(f"{row['frame']:<26}  (unreadable, skipped)")
            continue

        results = model.predict(source=frame, conf=CONF_THRESHOLD,
                                iou=IOU_THRESHOLD, imgsz=IMGSZ, verbose=False)
        classes = [int(c) for r in results if r.boxes is not None
                   for c in r.boxes.cls.tolist() if int(c) in VEHICLE_CLASSES]
        pred = len(classes)
        true = int(row["true_count"])
        err = pred - true

        pred_sev, _, _ = classify(pcu_total(classes), camera)
        # Ground-truth severity assumes an average 1.0 PCU per vehicle, since a
        # hand count has no class breakdown.
        true_sev, _, _ = classify(float(true), camera)
        agree = pred_sev == true_sev
        sev_agree += int(agree)

        errors.append(err); truths.append(true); preds.append(pred)
        flag = "" if agree else f"  <- {true_sev} vs {pred_sev}"
        print(f"{row['frame']:<26}{true:>6}{pred:>6}{err:>+6}  {pred_sev}{flag}")

    if not errors:
        sys.exit("Nothing scored.")

    n = len(errors)
    mae = sum(abs(e) for e in errors) / n
    bias = sum(errors) / n
    mape = sum(abs(e) / t for e, t in zip(errors, truths) if t) / max(
        1, sum(1 for t in truths if t)) * 100

    print("-" * 62)
    print(f"frames scored      : {n}")
    print(f"mean absolute error: {mae:.2f} vehicles")
    print(f"bias               : {bias:+.2f} vehicles "
          f"({'under' if bias < 0 else 'over'}-counting on average)")
    print(f"MAPE               : {mape:.1f}%")
    print(f"severity agreement : {sev_agree}/{n} ({sev_agree / n:.0%})")
    if n < 20:
        print("\nNote: fewer than 20 frames — treat these figures as indicative "
              "only, not as a validated accuracy claim.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    cap = sub.add_parser("capture", help="grab frames to hand-label")
    cap.add_argument("--camera", required=True)
    cap.add_argument("--n", type=int, default=20)
    cap.add_argument("--interval", type=float, default=30.0,
                     help="seconds between grabs (spread over time for variety)")

    sc = sub.add_parser("score", help="score the detector against your labels")
    sc.add_argument("--camera", required=True)

    args = ap.parse_args()
    if args.cmd == "capture":
        capture(args.camera, args.n, args.interval)
    else:
        score(args.camera)


if __name__ == "__main__":
    main()
