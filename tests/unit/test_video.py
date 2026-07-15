from pathlib import Path

import cv2
import numpy as np
import pytest

from pokemon_battle_vision.errors import DecodeAlignmentError
from pokemon_battle_vision.models import FrameTimestampIndex
from pokemon_battle_vision.video import decode_and_extract, rotate_frame_clockwise


class FakeCapture:
    def __init__(self, frames):
        self.frames = list(frames)
        self.position = 0
        self.orientation = 1.0
        self.opened = True

    def isOpened(self):
        return self.opened

    def set(self, prop, value):
        if prop == cv2.CAP_PROP_ORIENTATION_AUTO:
            self.orientation = float(value)
        return True

    def get(self, prop):
        if prop == cv2.CAP_PROP_ORIENTATION_AUTO:
            return self.orientation
        if prop == cv2.CAP_PROP_POS_FRAMES:
            return float(self.position)
        return 0.0

    def getBackendName(self):
        return "FAKE"

    def read(self):
        if self.position >= len(self.frames):
            return False, None
        frame = self.frames[self.position]
        self.position += 1
        return True, frame.copy()

    def release(self):
        self.opened = False


def _index(count):
    return FrameTimestampIndex(
        pts_sec=np.arange(count, dtype=np.float64) * 0.1,
        duration_sec=np.full(count, 0.1),
        key_frame=np.zeros(count, dtype=np.bool_),
        validation={"missing_count": 0, "duplicate_count": 0, "non_monotonic_count": 0},
        video_sha256="c" * 64,
        ffprobe_version="8.1.2",
    )


def _metadata():
    return {
        "rotation": {"clockwise_degrees": 90},
        "encoded_dimensions": {"width": 2, "height": 3},
        "display_dimensions": {"width": 3, "height": 2},
        "codec": "fake",
    }


def test_manual_rotation_clockwise():
    frame = np.arange(18, dtype=np.uint8).reshape(3, 2, 3)
    rotated = rotate_frame_clockwise(frame, 90)
    assert rotated.shape == (2, 3, 3)
    np.testing.assert_array_equal(rotated[:, :, 0], np.rot90(frame[:, :, 0], k=-1))


def test_decode_disables_autorotation_and_aligns(monkeypatch, tmp_path):
    frames = [np.full((3, 2, 3), value, dtype=np.uint8) for value in (10, 20)]
    monkeypatch.setattr(cv2, "VideoCapture", lambda path: FakeCapture(frames))
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")
    result = decode_and_extract(video, _metadata(), _index(2), [], [0, 1], "8.1.2", "8.1.2")
    assert result.report.status == "pass"
    assert result.report.orientation_auto_disabled is True
    assert result.report.first_decoded_dimensions == {"width": 2, "height": 3}
    assert result.report.first_display_dimensions == {"width": 3, "height": 2}
    assert set(result.contact_png_bytes) == {0, 1}


def test_decode_count_mismatch_has_diagnostic(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cv2, "VideoCapture", lambda path: FakeCapture([np.zeros((3, 2, 3), dtype=np.uint8)])
    )
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")
    with pytest.raises(DecodeAlignmentError) as captured:
        decode_and_extract(video, _metadata(), _index(2), [], [0], "8.1.2", "8.1.2")
    report = captured.value.report
    assert report.ffprobe_frame_count == 2
    assert report.opencv_decoded_frame_count == 1
    assert report.first_possible_mismatch_ordinal == 1
    assert report.nearby_pts

