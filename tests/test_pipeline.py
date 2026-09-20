"""
Tests for detection/pipeline.py - the non-HLS (local file / plain RTSP)
detection loop used by main.py's run_pipeline() path.

_detect_loop and _open_capture are pure orchestration over cv2.VideoCapture
and a YOLO model, so both are exercised here with lightweight fakes rather
than a real video source or model - the same style as
tests/test_vehicle_detection_and_bottlenecks.py. run_pipeline() itself (the
outer reconnect loop) is not covered: it differs from _detect_loop only by
retry/sleep scaffolding around a real cv2.VideoCapture/YOLO(), which would
require faking time.sleep and the whole reconnect loop for little additional
confidence over the frame-processing logic covered here. Previously none of
this module had any test coverage.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from detection.pipeline import _open_capture, _set_capture_fps, _detect_loop
from evidence import print_evidence


class FakeCapture:
    """Stands in for cv2.VideoCapture: reads a fixed list of frames, then EOF."""

    def __init__(self, frames, opened=True):
        self._frames = list(frames)
        self._opened = opened
        self.set_calls = []

    def isOpened(self):
        return self._opened

    def read(self):
        if not self._frames:
            return False, None
        return True, self._frames.pop(0)

    def set(self, prop, value):
        self.set_calls.append((prop, value))

    def release(self):
        pass


class FakeBoxes:
    def __init__(self, cls_ids):
        self._cls_ids = cls_ids

    @property
    def cls(self):
        return MagicMock(tolist=lambda: self._cls_ids)


class FakeResult:
    def __init__(self, cls_ids):
        self.boxes = FakeBoxes(cls_ids) if cls_ids is not None else None


def fake_frame():
    """A minimal stand-in for a decoded frame: just needs .shape[:2]."""
    return MagicMock(shape=(720, 1280, 3))


class OpenCaptureTests(unittest.TestCase):
    def test_raises_ioerror_when_source_cannot_be_opened(self):
        import detection.pipeline as pipeline_mod
        from unittest.mock import patch

        with patch.object(pipeline_mod.cv2, "VideoCapture",
                          return_value=FakeCapture([], opened=False)):
            with self.assertRaises(IOError) as context:
                _open_capture("bad_source.mp4")
        print_evidence("TC-201", "Opening an unavailable source raises IOError",
                       "source = 'bad_source.mp4', isOpened() = False",
                       "IOError", type(context.exception).__name__)

    def test_returns_capture_when_source_opens(self):
        import detection.pipeline as pipeline_mod
        from unittest.mock import patch

        fake_cap = FakeCapture([], opened=True)
        with patch.object(pipeline_mod.cv2, "VideoCapture", return_value=fake_cap):
            result = _open_capture("good_source.mp4")
        print_evidence("TC-202", "Opening an available source returns the capture",
                       "source = 'good_source.mp4', isOpened() = True",
                       True, result is fake_cap)
        self.assertIs(result, fake_cap)


class SetCaptureFpsTests(unittest.TestCase):
    def test_forwards_to_capture_set(self):
        cap = FakeCapture([])
        _set_capture_fps(cap, 15)
        print_evidence("TC-203", "Requested FPS is forwarded to VideoCapture.set()",
                       "fps = 15", 15, cap.set_calls[0][1] if cap.set_calls else None)
        self.assertEqual(len(cap.set_calls), 1)
        self.assertEqual(cap.set_calls[0][1], 15)


class DetectLoopTests(unittest.TestCase):
    def test_stops_when_capture_is_exhausted(self):
        cap = FakeCapture([])  # no frames at all
        model = MagicMock()
        results = list(_detect_loop(model, cap, "camera_a"))
        print_evidence("TC-204", "Detect loop stops cleanly when the capture is exhausted",
                       "0 frames available", [], results)
        self.assertEqual(results, [])

    def test_only_processes_every_frame_skip_th_frame(self):
        """FRAME_SKIP=2 - only the 2nd, 4th, ... frame reaches the model."""
        cap = FakeCapture([fake_frame() for _ in range(4)])
        model = MagicMock()
        model.predict.return_value = [FakeResult([2])]  # one car

        results = list(_detect_loop(model, cap, "camera_a"))

        print_evidence("TC-205", "Only every FRAME_SKIP-th frame reaches the model",
                       "4 frames available, FRAME_SKIP = 2",
                       {"predict_calls": 2, "results": 2},
                       {"predict_calls": model.predict.call_count, "results": len(results)})
        self.assertEqual(model.predict.call_count, 2)
        self.assertEqual(len(results), 2)

    def test_counts_only_known_vehicle_classes(self):
        cap = FakeCapture([fake_frame(), fake_frame()])
        model = MagicMock()
        # class 2 = car (counted), class 0 = person (not a vehicle, ignored)
        model.predict.return_value = [FakeResult([2, 0, 2])]

        results = list(_detect_loop(model, cap, "camera_a"))

        print_evidence("TC-206", "Non-vehicle classes are not counted",
                       "detected classes = [car, person, car]", 2, results[0]["vehicle_count"])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["vehicle_count"], 2)

    def test_result_includes_severity_and_frame_shape(self):
        cap = FakeCapture([fake_frame(), fake_frame()])
        model = MagicMock()
        model.predict.return_value = [FakeResult([2, 2, 2, 2, 2])]  # 5 cars = 5 PCU

        results = list(_detect_loop(model, cap, "some_camera_never_configured"))

        result = results[0]
        # default capacity 20 -> saturation 0.25 -> moderate (inclusive boundary)
        print_evidence("TC-207", "Detect loop result includes severity and frame shape",
                       "5 cars, camera_id = 'some_camera_never_configured' (capacity 20)",
                       {"severity": "moderate", "frame_shape": [720, 1280]},
                       {"severity": result["severity"], "frame_shape": result["frame_shape"]})
        self.assertEqual(len(results), 1)
        self.assertEqual(result["camera_id"], "some_camera_never_configured")
        self.assertEqual(result["vehicle_count"], 5)
        self.assertEqual(result["pcu"], 5.0)
        self.assertEqual(result["severity"], "moderate")
        self.assertEqual(result["frame_shape"], [720, 1280])

    def test_a_frame_with_no_boxes_yields_zero_vehicles(self):
        cap = FakeCapture([fake_frame(), fake_frame()])
        model = MagicMock()
        model.predict.return_value = [FakeResult(None)]  # r.boxes is None

        results = list(_detect_loop(model, cap, "camera_a"))

        print_evidence("TC-208", "A frame with no detection boxes yields zero vehicles",
                       "r.boxes is None", 0, results[0]["vehicle_count"])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["vehicle_count"], 0)
        self.assertEqual(results[0]["severity"], "free")

if __name__ == "__main__":
    unittest.main()
