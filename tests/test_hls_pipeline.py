"""
Tests for the pure/mockable helpers in detection/hls_pipeline.py.

The bulk of this module (run_hls_pipeline, _detect_loop) is a real-time
ffmpeg/YOLO streaming loop that is deliberately exercised by
tools/incident_harness.py against live streams rather than unit tests. This
file covers the parts that don't need a live camera: ffmpeg/ffprobe subprocess
handling, single-frame decoding, and the annotated-frame cache. Previously
none of this module had any test coverage.
"""

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from detection.hls_pipeline import (
    _ffmpeg_available,
    _get_dimensions,
    _grab_single_frame,
    _store_annotated_frame,
    get_latest_frame,
    DEFAULT_WIDTH,
    DEFAULT_HEIGHT,
)
import detection.hls_pipeline as hls_pipeline
from evidence import print_evidence


class FfmpegAvailableTests(unittest.TestCase):
    def test_true_when_ffmpeg_reports_success(self) -> None:
        completed = MagicMock(returncode=0)
        with patch("detection.hls_pipeline.subprocess.run", return_value=completed):
            result = _ffmpeg_available()
        print_evidence("TC-183", "ffmpeg availability - success",
                       "subprocess returncode = 0", True, result)
        self.assertTrue(result)

    def test_false_when_ffmpeg_reports_failure(self) -> None:
        completed = MagicMock(returncode=1)
        with patch("detection.hls_pipeline.subprocess.run", return_value=completed):
            result = _ffmpeg_available()
        print_evidence("TC-184", "ffmpeg availability - non-zero exit",
                       "subprocess returncode = 1", False, result)
        self.assertFalse(result)

    def test_false_when_ffmpeg_is_not_installed(self) -> None:
        with patch("detection.hls_pipeline.subprocess.run",
                   side_effect=FileNotFoundError()):
            result = _ffmpeg_available()
        print_evidence("TC-185", "ffmpeg availability - not installed",
                       "subprocess.run raises FileNotFoundError", False, result)
        self.assertFalse(result)

    def test_false_when_ffmpeg_hangs(self) -> None:
        with patch("detection.hls_pipeline.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=5)):
            result = _ffmpeg_available()
        print_evidence("TC-186", "ffmpeg availability - hangs and times out",
                       "subprocess.run raises TimeoutExpired", False, result)
        self.assertFalse(result)


class GetDimensionsTests(unittest.TestCase):
    def test_parses_width_and_height_from_ffprobe_json(self) -> None:
        stdout = (
            b'{"streams": [{"codec_type": "audio"}, '
            b'{"codec_type": "video", "width": 960, "height": 540}]}'
        )
        completed = MagicMock(returncode=0, stdout=stdout)
        with patch("detection.hls_pipeline.subprocess.run", return_value=completed):
            result = _get_dimensions("http://example.com/stream.m3u8")
        print_evidence("TC-187", "Dimensions are parsed from ffprobe JSON",
                       "ffprobe reports video stream 960x540", (960, 540), result)
        self.assertEqual(result, (960, 540))

    def test_falls_back_to_defaults_when_ffprobe_fails(self) -> None:
        completed = MagicMock(returncode=1, stdout=b"")
        with patch("detection.hls_pipeline.subprocess.run", return_value=completed):
            result = _get_dimensions("http://example.com/stream.m3u8")
        print_evidence("TC-188", "Dimensions fall back to defaults when ffprobe fails",
                       "ffprobe returncode = 1", (DEFAULT_WIDTH, DEFAULT_HEIGHT), result)
        self.assertEqual(result, (DEFAULT_WIDTH, DEFAULT_HEIGHT))

    def test_falls_back_to_defaults_when_no_video_stream_present(self) -> None:
        stdout = b'{"streams": [{"codec_type": "audio"}]}'
        completed = MagicMock(returncode=0, stdout=stdout)
        with patch("detection.hls_pipeline.subprocess.run", return_value=completed):
            result = _get_dimensions("http://example.com/stream.m3u8")
        print_evidence("TC-189", "Dimensions fall back to defaults with no video stream",
                       "ffprobe streams contain only audio", (DEFAULT_WIDTH, DEFAULT_HEIGHT), result)
        self.assertEqual(result, (DEFAULT_WIDTH, DEFAULT_HEIGHT))

    def test_falls_back_to_defaults_on_malformed_json(self) -> None:
        completed = MagicMock(returncode=0, stdout=b"not json")
        with patch("detection.hls_pipeline.subprocess.run", return_value=completed):
            result = _get_dimensions("http://example.com/stream.m3u8")
        print_evidence("TC-190", "Dimensions fall back to defaults on malformed JSON",
                       "ffprobe stdout = b'not json'", (DEFAULT_WIDTH, DEFAULT_HEIGHT), result)
        self.assertEqual(result, (DEFAULT_WIDTH, DEFAULT_HEIGHT))

    def test_falls_back_to_defaults_when_subprocess_raises(self) -> None:
        with patch("detection.hls_pipeline.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=15)):
            result = _get_dimensions("http://example.com/stream.m3u8")
        print_evidence("TC-191", "Dimensions fall back to defaults when ffprobe times out",
                       "subprocess.run raises TimeoutExpired", (DEFAULT_WIDTH, DEFAULT_HEIGHT), result)
        self.assertEqual(result, (DEFAULT_WIDTH, DEFAULT_HEIGHT))


class GrabSingleFrameTests(unittest.TestCase):
    def test_returns_decoded_frame_on_success(self) -> None:
        width, height = 4, 3
        raw = (b"\x00\x01\x02" * (width * height))[: width * height * 3]
        completed = MagicMock(returncode=0, stdout=raw)
        with patch("detection.hls_pipeline.subprocess.run", return_value=completed):
            frame = _grab_single_frame(["http://example.com/a.m3u8"], width, height)
        print_evidence("TC-192", "A successful ffmpeg grab decodes to the expected shape",
                       f"width={width}, height={height}", (height, width, 3),
                       frame.shape if frame is not None else None)
        self.assertIsNotNone(frame)
        self.assertEqual(frame.shape, (height, width, 3))

    def test_falls_through_to_the_next_url_on_failure(self) -> None:
        failing = MagicMock(returncode=1, stdout=b"", stderr=b"error")
        width, height = 2, 2
        good_raw = (b"\x00\x01\x02" * (width * height))[: width * height * 3]
        succeeding = MagicMock(returncode=0, stdout=good_raw)
        with patch("detection.hls_pipeline.subprocess.run",
                   side_effect=[failing, succeeding]):
            frame = _grab_single_frame(
                ["http://example.com/bad.m3u8", "http://example.com/good.m3u8"],
                width, height,
            )
        print_evidence("TC-193", "A failing URL falls through to the next candidate",
                       "first URL fails, second succeeds", True, frame is not None)
        self.assertIsNotNone(frame)

    def test_returns_none_when_every_url_fails(self) -> None:
        failing = MagicMock(returncode=1, stdout=b"", stderr=b"error")
        with patch("detection.hls_pipeline.subprocess.run", return_value=failing):
            frame = _grab_single_frame(["http://example.com/a.m3u8"], 4, 3)
        print_evidence("TC-194", "None is returned when every URL fails",
                       "1 URL, ffmpeg returncode = 1", None, frame)
        self.assertIsNone(frame)

    def test_returns_none_on_short_stdout(self) -> None:
        """Fewer bytes than width*height*3 means a truncated/partial grab."""
        completed = MagicMock(returncode=0, stdout=b"\x00\x01\x02")
        with patch("detection.hls_pipeline.subprocess.run", return_value=completed):
            frame = _grab_single_frame(["http://example.com/a.m3u8"], 100, 100)
        print_evidence("TC-195", "A truncated frame (short stdout) is rejected",
                       "expected 30000 bytes, got 3", None, frame)
        self.assertIsNone(frame)

    def test_returns_none_when_subprocess_times_out(self) -> None:
        with patch("detection.hls_pipeline.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=20)):
            frame = _grab_single_frame(["http://example.com/a.m3u8"], 4, 3)
        print_evidence("TC-196", "A timed-out grab returns None",
                       "subprocess.run raises TimeoutExpired", None, frame)
        self.assertIsNone(frame)


class AnnotatedFrameCacheTests(unittest.TestCase):
    """Uses the real cv2/numpy (both real dependencies here, not stubs)."""

    def setUp(self) -> None:
        # Isolate the module-level frame cache between tests.
        self.patcher = patch.object(hls_pipeline, "_latest_frames", {})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_get_latest_frame_is_none_before_anything_stored(self) -> None:
        result = get_latest_frame("never_stored_camera")
        print_evidence("TC-197", "No cached frame exists before anything is stored",
                       "camera_id = 'never_stored_camera'", None, result)
        self.assertIsNone(result)

    def test_store_then_get_round_trips_jpeg_bytes(self) -> None:
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        _store_annotated_frame("cam1", frame, boxes=[(10, 10, 40, 40)], classes=[2])

        result = get_latest_frame("cam1")
        jpeg_bytes, captured_at = result if result else (None, None)
        is_jpeg = jpeg_bytes.startswith(b"\xff\xd8") if jpeg_bytes else False
        print_evidence("TC-198", "A stored annotated frame round-trips as JPEG bytes",
                       "100x100 frame with one detection box",
                       {"stored": True, "is_jpeg": True},
                       {"stored": result is not None, "is_jpeg": is_jpeg})
        self.assertIsNotNone(result)
        self.assertTrue(is_jpeg)
        self.assertIsNotNone(captured_at)

    def test_wide_frame_is_downscaled_to_frame_width(self) -> None:
        import cv2

        with patch.object(hls_pipeline, "_FRAME_WIDTH", 50):
            frame = np.zeros((200, 400, 3), dtype=np.uint8)
            _store_annotated_frame("cam_wide", frame, boxes=[], classes=[])

        jpeg_bytes, _ = get_latest_frame("cam_wide")
        decoded = cv2.imdecode(np.frombuffer(jpeg_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        result = (decoded.shape[1], decoded.shape[0])
        print_evidence("TC-199", "A wide frame is downscaled to _FRAME_WIDTH",
                       "400x200 frame, _FRAME_WIDTH = 50", (50, 25), result)
        self.assertEqual(decoded.shape[1], 50)
        self.assertEqual(decoded.shape[0], 25)  # aspect ratio preserved (200/400 * 50)

    def test_narrow_frame_is_not_upscaled(self) -> None:
        import cv2

        with patch.object(hls_pipeline, "_FRAME_WIDTH", 480):
            frame = np.zeros((100, 100, 3), dtype=np.uint8)
            _store_annotated_frame("cam_narrow", frame, boxes=[], classes=[])

        jpeg_bytes, _ = get_latest_frame("cam_narrow")
        decoded = cv2.imdecode(np.frombuffer(jpeg_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        result = decoded.shape[1]
        print_evidence("TC-200", "A narrow frame is not upscaled beyond its own width",
                       "100x100 frame, _FRAME_WIDTH = 480", 100, result)
        self.assertEqual(result, 100)


if __name__ == "__main__":
    unittest.main()
