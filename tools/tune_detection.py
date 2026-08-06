"""
tools/tune_detection.py
Benchmark this machine and recommend detection settings for N cameras.

The problem
-----------
Every active camera costs one YOLO inference per FRAME_INTERVAL seconds. If the
total inference work exceeds real time, frame grabs queue and then time out, and
you get *less* data from more cameras. The limit is:

    sustainable cameras = FRAME_INTERVAL / seconds_per_inference

The defaults (yolov8m at imgsz 1280) cost ~890 ms per frame on a 16-core CPU,
which sustains barely two cameras at a 2-second interval — so even the original
four were oversubscribed. Note 1280 *upscales* a 1024x576 stream.

This measures your actual hardware and prints the configuration to put in .env.

    python tools/tune_detection.py --cameras 38
    python tools/tune_detection.py --cameras 38 --max-interval 10

Accuracy trade-off
------------------
A smaller model or a smaller imgsz detects fewer small and distant vehicles.
That changes vehicle counts, which changes saturation, which changes severity —
so CAMERA_CAPACITY in detection/severity.py is calibrated against whatever
settings were in use when the data was collected. Change these and recalibrate
with tools/calibrate_capacity.py, or severity will drift.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# Ordered best-quality first: the recommendation is the first option that fits.
OPTIONS = [
    ("yolov8m.pt", 1280), ("yolov8m.pt", 960), ("yolov8m.pt", 640),
    ("yolov8n.pt", 1280), ("yolov8n.pt", 960), ("yolov8n.pt", 640),
]

# Leave headroom: ffmpeg decoding, tracking and the web app also need CPU, and a
# pipeline pinned at 100% has no slack for a slow frame.
TARGET_UTILISATION = 0.65


def benchmark(runs: int = 3) -> dict[tuple[str, int], float]:
    import numpy as np
    from ultralytics import YOLO

    frame = (np.random.rand(576, 1024, 3) * 255).astype("uint8")
    results: dict[tuple[str, int], float] = {}
    cache: dict[str, object] = {}

    print(f"Benchmarking on {os.cpu_count()} cores "
          f"(a 1024x576 frame, {runs} runs each)\n")
    print(f"{'model':<14}{'imgsz':>7}{'ms/frame':>11}")
    print("-" * 34)
    for path, imgsz in OPTIONS:
        if not pathlib.Path(path).exists():
            print(f"{path:<14}{imgsz:>7}   not present, skipped")
            continue
        if path not in cache:
            cache[path] = YOLO(path)
        model = cache[path]
        model.predict(source=frame, imgsz=imgsz, verbose=False)      # warm up
        t0 = time.perf_counter()
        for _ in range(runs):
            model.predict(source=frame, imgsz=imgsz, conf=0.25,
                          iou=0.35, verbose=False)
        per = (time.perf_counter() - t0) / runs
        results[(path, imgsz)] = per
        print(f"{path:<14}{imgsz:>7}{per * 1000:>11.0f}")
    return results


def recommend(measured: dict, cameras: int, max_interval: float) -> None:
    print(f"\nFor {cameras} camera(s), targeting {TARGET_UTILISATION:.0%} "
          f"CPU utilisation:\n")
    print(f"{'model':<14}{'imgsz':>7}{'min interval':>14}{'verdict':>26}")
    print("-" * 62)

    best = None
    for (path, imgsz), per in measured.items():
        # Interval needed so N cameras fit inside the utilisation target.
        needed = (cameras * per) / TARGET_UTILISATION
        fits = needed <= max_interval
        verdict = "OK" if fits else f"needs {needed:.0f}s interval"
        print(f"{path:<14}{imgsz:>7}{needed:>13.1f}s{verdict:>26}")
        if fits and best is None:
            best = (path, imgsz, max(2.0, round(needed)))

    if best is None:
        cheapest = min(measured.items(), key=lambda kv: kv[1])
        (path, imgsz), per = cheapest
        needed = (cameras * per) / TARGET_UTILISATION
        print(f"\nNo option fits {cameras} cameras within a {max_interval:.0f}s "
              f"interval.\nThe cheapest is {path} at imgsz {imgsz}, which needs "
              f"a {needed:.0f}s interval.\n"
              f"Either accept that interval, run fewer cameras, or use a GPU.")
        print(f"\n  ACTIVE_CAMERAS=all\n  YOLO_MODEL={path}\n  YOLO_IMGSZ={imgsz}\n"
              f"  FRAME_INTERVAL={needed:.0f}")
        return

    path, imgsz, interval = best
    sustainable = interval / measured[(path, imgsz)]
    print(f"\nRecommended — put this in .env:\n")
    print(f"  YOLO_MODEL={path}")
    print(f"  YOLO_IMGSZ={imgsz}")
    print(f"  FRAME_INTERVAL={interval:.0f}")
    print(f"  MAX_CONCURRENT_INFERENCE={max(2, (os.cpu_count() or 4) // 4)}")
    print(f"\nThat sustains about {sustainable:.0f} cameras; you asked for {cameras}.")
    if path.endswith("n.pt") or imgsz < 1280:
        print("\nNote: this is a smaller model and/or resolution than the default,\n"
              "so counts will be lower — small and distant vehicles get missed.\n"
              "Recalibrate with tools/calibrate_capacity.py once data accumulates,\n"
              "or severity thresholds will be miscalibrated against the new counts.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cameras", type=int, default=38,
                    help="how many cameras you want monitored (default 38)")
    ap.add_argument("--max-interval", type=float, default=8.0,
                    help="longest acceptable seconds between frames (default 8)")
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    measured = benchmark(args.runs)
    if not measured:
        sys.exit("No model files found. Expected yolov8m.pt / yolov8n.pt in the "
                 "project root.")
    recommend(measured, args.cameras, args.max_interval)


if __name__ == "__main__":
    main()
