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
            replays a synthetic N-vehicle sequence through the real
            StationaryTracker -> IncidentDetector -> _save_incident ->
            incidents table, in-process, using the actual production
            thresholds at the time of the call. Reports whether and when it
            confirmed, then re-checks /api/incidents/active to prove the
            incident is really being served, not just written. Needs the app
            running and an admin login.

  discriminate  Run inject twice and check both answers, which is the only
            way to show a detector is right rather than merely willing to
            fire. A stall with traffic moving past it must confirm; a queue
            where everything stopped together - a red light - must not. Exits
            non-zero if either case gives the wrong answer, so it can gate a
            change to the thresholds in detection/stationary_tracker.py.

Usage
-----
    python tools/incident_harness.py measure --camera caudan_north --frames 20
    python tools/incident_harness.py inject --camera caudan_north --username admin --password ...
    python tools/incident_harness.py discriminate --username admin --password ...
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

    print(f"Logged in as {args.username!r}. Replaying {args.vehicles} stopped "
          f"vehicle(s) and {args.moving} moving on {args.camera!r} "
          f"({args.frames} frames) …")
    resp = session.post(f"{args.base_url}/api/demo/stall", params={
        "camera_id": args.camera, "frames": args.frames,
        "vehicles": args.vehicles, "moving": args.moving,
    })
    resp.raise_for_status()
    result = resp.json()

    print(f"\nframe_interval used by the tracker : {result['frame_interval_s']}s")
    print(f"frames required to confirm a stall  : {result['frames_required']}")
    print(f"frames replayed                     : {result['frames_replayed']}")
    print(f"stopped / moving vehicles           : "
          f"{result['stalled_vehicles']} / {result['moving_vehicles']}")
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


# ── discriminate ─────────────────────────────────────────────────────────────

def _replay(session, base_url: str, camera: str, frames: int,
            vehicles: int, moving: int) -> dict:
    resp = session.post(f"{base_url}/api/demo/stall", params={
        "camera_id": camera, "frames": frames,
        "vehicles": vehicles, "moving": moving,
    })
    resp.raise_for_status()
    return resp.json()


def cmd_discriminate(args: argparse.Namespace) -> None:
    import requests

    session = requests.Session()
    resp = session.post(f"{args.base_url}/login",
                        data={"username": args.username, "password": args.password})
    if not session.cookies:
        sys.exit(f"Login failed (status {resp.status_code}) - check --username/--password.")

    cases = [
        {
            "name":     "stall with traffic moving past it",
            "camera":   args.camera_stall,
            "vehicles": args.vehicles,
            "moving":   args.moving,
            "expect":   True,
            "why":      "vehicles stopped while others keep moving - a breakdown",
        },
        {
            "name":     "queue stopped together (red light)",
            "camera":   args.camera_queue,
            "vehicles": args.vehicles + args.moving,
            "moving":   0,
            "expect":   False,
            "why":      "everything stopped together - a signal cycle",
        },
        {
            # Not a pass/fail case: it probes where MIN_STATIONARY_FRACTION
            # stops a genuine stall from confirming. That threshold predates
            # the discriminator and pulls the opposite way - it wants most of
            # the frame stopped, while a stall by definition has traffic
            # moving around it. At 0.70 a stall confirms only while movers
            # stay under a third of the tracked vehicles. Reported, not
            # asserted, so tuning that threshold does not read as a failure.
            "name":     "stall in busier traffic (boundary probe)",
            "camera":   args.camera_probe,
            "vehicles": args.vehicles,
            "moving":   args.moving + 1,
            "expect":   None,
            "why":      "same stall, one more vehicle moving past it",
        },
    ]

    print(f"Logged in as {args.username!r}. Each case replays {args.frames} frames "
          f"through the real tracker and incident pipeline.\n")

    failures = checked = 0
    for case in cases:
        r = _replay(session, args.base_url, case["camera"], args.frames,
                    case["vehicles"], case["moving"])
        confirmed = r["confirmed_at_frame"] is not None
        verdict = r["final_verdict"] or {}

        if case["expect"] is None:
            label = "INFO"
        else:
            checked += 1
            ok = confirmed == case["expect"]
            failures += 0 if ok else 1
            label = "PASS" if ok else "FAIL"

        print(f"{label}  {case['name']}")
        print(f"        {case['why']}")
        print(f"        {case['vehicles']} stopped, {case['moving']} moving on "
              f"{case['camera']!r}; {r['frames_required']} frames "
              f"({r['frame_interval_s']:.2f}s each) required to confirm")
        if case["expect"] is not None:
            print(f"        expected {'a confirmed stall' if case['expect'] else 'no incident'}, "
                  f"got {'confirmed at frame ' + str(r['confirmed_at_frame']) if confirmed else 'none'}")
        else:
            print(f"        result: {'confirmed at frame ' + str(r['confirmed_at_frame']) if confirmed else 'no incident'}")
        print(f"        tracker: stationary={verdict.get('stationary_count')} "
              f"moving={verdict.get('moving_count')} "
              f"tracked={verdict.get('total_tracked')} "
              f"stalled_seconds={verdict.get('stalled_seconds')}")
        print(f"        reason: {verdict.get('reason')}\n")

    if failures:
        sys.exit(f"{failures} of {checked} checked case(s) gave the wrong answer.")
    print(f"Both checked cases correct: a stall beside moving traffic confirms, "
          f"a queue stopped together does not.")


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
    j.add_argument("--moving", type=int, default=1,
                   help="vehicles that keep moving alongside the stopped ones. "
                        "0 replays a queue that stopped together, which a "
                        "correct detector refuses to call a stall - see "
                        "MIN_MOVING_VEHICLES in detection/stationary_tracker.py.")
    j.add_argument("--base-url", default="http://127.0.0.1:8000")
    j.add_argument("--username", default="admin")
    j.add_argument("--password", required=True)
    j.set_defaults(func=cmd_inject)

    d = sub.add_parser("discriminate",
                       help="prove a stall confirms and a red-light queue does not")
    d.add_argument("--camera-stall", default="caudan_north")
    d.add_argument("--camera-queue", default="caudan_south")
    d.add_argument("--camera-probe", default="la_chaussee")
    d.add_argument("--frames", type=int, default=80)
    d.add_argument("--vehicles", type=int, default=4,
                   help="vehicles held still in every case")
    d.add_argument("--moving", type=int, default=1,
                   help="vehicles moving in the stall case; the queue case "
                        "replays the same total with none of them moving. "
                        "Default 1 because MIN_STATIONARY_FRACTION (0.70) "
                        "rejects a stall once movers pass about a third of "
                        "the tracked vehicles - the third case probes that.")
    d.add_argument("--base-url", default="http://127.0.0.1:8000")
    d.add_argument("--username", default="admin")
    d.add_argument("--password", required=True)
    d.set_defaults(func=cmd_discriminate)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
