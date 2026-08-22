"""
tools/incident_harness.py
Force a known outcome through the stalled-vehicle detection chain, so a
wiring bug and a tuning problem are never guessed apart again.

Why this exists
----------------
2026-08-06's incident-detection audit found detection/stationary_tracker.py
correctly instantiated and called on every single frame, and its verdict
never once True across 42,975 recorded snapshots. Telling "the wiring is
broken" apart from "the thresholds are unreachable at this frame rate"
needed a way to force a *known* stall through the real pipeline on demand,
rather than waiting for one to happen live. This is that tool.

Two independent modes
----------------------
  measure   Grab a short burst of real frames from a live camera, track
            vehicles with the real StationaryTracker association, and report
            the actual frame-to-frame IoU and (box-diagonal-normalised)
            centroid displacement of whatever track(s) persisted across
            frames. This is what STATIONARY_IOU / a centroid threshold
            should be set from - not a guess.

  inject    POST to the running app's admin-only /api/demo/stall, which
            replays a synthetic, held-still N-vehicle sequence through the
            real StationaryTracker -> IncidentDetector -> _save_incident ->
            incidents table, in-process, using the actual production
            thresholds at the time of the call. Reports whether and when it
            confirmed, then re-checks /api/incidents/active to prove the
            incident is really being served, not just written. Needs the app
            running and an admin login.

Usage
-----
    python tools/incident_harness.py measure --camera caudan_north --frames 20
    python tools/incident_harness.py inject --camera caudan_north --username admin --password ...
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


# ── measure ──────────────────────────────────────────────────────────────────

def cmd_measure(args: argparse.Namespace) -> None:
    from ultralytics import YOLO
    from detection.hls_pipeline import (
        CONF_THRESHOLD, IOU_THRESHOLD, IMGSZ, MODEL_PATH, FRAME_INTERVAL,
        _grab_single_frame, _get_dimensions,
    )
    from detection.severity import VEHICLE_CLASSES
    from detection.stationary_tracker import StationaryTracker, iou, centre_of
    from detection.trafficwatch import discover_cameras

    cams = {c["camera_id"]: c for c in discover_cameras()}
    if args.camera not in cams:
        sys.exit(f"Unknown camera {args.camera!r}. Known: {', '.join(sorted(cams))}")
    url = cams[args.camera]["source"]
    width, height = _get_dimensions(url)

    print(f"Loading {MODEL_PATH} …")
    model = YOLO(MODEL_PATH)
    tracker = StationaryTracker(frame_interval=FRAME_INTERVAL)

    # Keyed by the _Track object's identity, which the tracker itself keeps
    # stable across frames for a re-matched vehicle (see _associate() -
    # matched tracks are the same object, not a copy). Using the tracker's
    # own association means "is this the same vehicle" is answered by the
    # real production code, not a second guess from this script.
    prev_boxes: dict[int, list[float]] = {}
    samples: list[dict] = []

    t0 = time.perf_counter()
    print(f"Grabbing {args.frames} live frames from {args.camera!r} - "
          f"nominal FRAME_INTERVAL={FRAME_INTERVAL:.1f}s, real cadence may run "
          f"longer (grab+inference time included) …\n")

    for i in range(args.frames):
        f_start = time.perf_counter()
        frame = _grab_single_frame([url], width, height)
        if frame is None:
            print(f"  [{i+1}/{args.frames}] frame grab failed - skipping")
            continue

        results = model.predict(source=frame, conf=CONF_THRESHOLD,
                                iou=IOU_THRESHOLD, imgsz=IMGSZ, verbose=False)
        boxes, classes = [], []
        for r in results:
            if r.boxes is None:
                continue
            for j, c in enumerate(r.boxes.cls.tolist()):
                if int(c) in VEHICLE_CLASSES:
                    boxes.append(r.boxes.xyxy.tolist()[j])
                    classes.append(int(c))

        tracker.update(boxes, classes)

        for tr in tracker.visible_tracks():
            key = id(tr)
            if key in prev_boxes:
                prev = prev_boxes[key]
                cur_iou = iou(prev, tr.box)
                pc, cc = centre_of(prev), centre_of(tr.box)
                disp_px = ((cc[0] - pc[0]) ** 2 + (cc[1] - pc[1]) ** 2) ** 0.5
                # Normalised by the box's own diagonal, not frame size - a
                # near car and a distant one occupy very different pixel
                # counts for the same real-world stillness.
                diag = max(1.0, ((prev[2] - prev[0]) ** 2 + (prev[3] - prev[1]) ** 2) ** 0.5)
                samples.append({
                    "iou": cur_iou,
                    "centroid_disp_px": disp_px,
                    "centroid_disp_norm": disp_px / diag,
                })
            prev_boxes[key] = list(tr.box)

        elapsed = time.perf_counter() - f_start
        print(f"  [{i+1}/{args.frames}] {elapsed:5.2f}s  {len(boxes)} vehicle(s), "
              f"{len(tracker.visible_tracks())} tracked, "
              f"{len(samples)} consecutive-frame sample(s) so far")

        if args.gap > 0:
            time.sleep(args.gap)

    real_cadence = (time.perf_counter() - t0) / max(1, args.frames)
    print(f"\nMeasured real cadence this run: {real_cadence:.2f}s/frame "
          f"(nominal FRAME_INTERVAL={FRAME_INTERVAL:.1f}s)")

    if not samples:
        print("\nNo track survived two consecutive frames - no vehicle was both "
              "visible and slow-moving/stationary enough to sample. Try a camera "
              "with a parked car in frame, or run with more --frames.")
        return

    def pct(xs: list[float], p: float) -> float:
        xs = sorted(xs)
        return xs[min(len(xs) - 1, int(len(xs) * p))]

    ious  = [s["iou"] for s in samples]
    norms = [s["centroid_disp_norm"] for s in samples]

    print(f"\n{len(samples)} consecutive-frame sample(s) across "
          f"{len(prev_boxes)} track(s):")
    print(f"  IoU                  min={min(ious):.3f}  median={pct(ious,0.5):.3f}  "
          f"p90={pct(ious,0.9):.3f}  max={max(ious):.3f}")
    print(f"  centroid disp/diag   min={min(norms):.3f}  median={pct(norms,0.5):.3f}  "
          f"p90={pct(norms,0.9):.3f}  max={max(norms):.3f}")
    print("\n(centroid disp/diag: displacement between frames as a fraction of the "
          "box's own diagonal - independent of how near/far the vehicle is, unlike "
          "raw pixels or IoU which both shrink for a small distant box.)")


# ── inject ───────────────────────────────────────────────────────────────────

def cmd_inject(args: argparse.Namespace) -> None:
    import requests

    session = requests.Session()
    resp = session.post(f"{args.base_url}/login",
                        data={"username": args.username, "password": args.password})
    if not session.cookies:
        sys.exit(f"Login failed (status {resp.status_code}) - check --username/--password.")

    print(f"Logged in as {args.username!r}. Replaying a synthetic "
          f"{args.vehicles}-vehicle stall on {args.camera!r} ({args.frames} frames) …")
    resp = session.post(f"{args.base_url}/api/demo/stall", params={
        "camera_id": args.camera, "frames": args.frames, "vehicles": args.vehicles,
    })
    resp.raise_for_status()
    result = resp.json()

    print(f"\nframe_interval used by the tracker : {result['frame_interval_s']}s")
    print(f"frames required to confirm a stall  : {result['frames_required']}")
    print(f"frames replayed                     : {result['frames_replayed']}")
    if result["confirmed_at_frame"]:
        seconds = result["confirmed_at_frame"] * result["frame_interval_s"]
        print(f"CONFIRMED at frame {result['confirmed_at_frame']} (~{seconds:.0f}s), "
              f"incident_id(s): {result['incident_ids']}")
    else:
        print("NOT confirmed within the replayed frames. Final verdict:")
        print(f"  {result['final_verdict']}")

    # Re-check the live, DB-backed endpoint - proves this is really being
    # served, not just written.
    resp = session.get(f"{args.base_url}/api/incidents/active")
    resp.raise_for_status()
    active = resp.json()
    stalled = [i for i in active if i["type"] == "stalled_vehicle"]
    print(f"\n/api/incidents/active now returns {len(active)} incident(s), "
          f"{len(stalled)} of type 'stalled_vehicle':")
    for i in stalled:
        print(f"  {i['incident_id'][:8]}  {i['camera_id']}  conf={i['confidence']}  "
              f"{i['description']}")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)

    m = sub.add_parser("measure", help="measure real IoU/centroid jitter from a live camera")
    m.add_argument("--camera", default="caudan_north")
    m.add_argument("--frames", type=int, default=20)
    m.add_argument("--gap", type=float, default=0.0,
                   help="seconds to sleep between grabs. The HLS segment behind "
                        "these streams was measured to refresh only every ~10s "
                        "(2026-08-07) - a gap shorter than that samples the same "
                        "encoded frame repeatedly and reports trivial IoU=1.0. "
                        "Use --gap 11 or more for genuinely independent samples.")
    m.set_defaults(func=cmd_measure)

    j = sub.add_parser("inject", help="replay a synthetic stall through the running app")
    j.add_argument("--camera", default="caudan_north")
    j.add_argument("--frames", type=int, default=80)
    j.add_argument("--vehicles", type=int, default=4)
    j.add_argument("--base-url", default="http://127.0.0.1:8000")
    j.add_argument("--username", default="admin")
    j.add_argument("--password", required=True)
    j.set_defaults(func=cmd_inject)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
