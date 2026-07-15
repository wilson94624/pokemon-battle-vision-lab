import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from pokemon_battle_vision.candidate_detection import (
    DEFAULT_THRESHOLDS,
    appearance_signature,
    signature_similarity,
)
from pokemon_battle_vision.checkpoint1b_models import EVENT_TYPES, FrameScanRecord
from pokemon_battle_vision.errors import RoiApprovalError
from pokemon_battle_vision.models import FrameTimestampIndex
from pokemon_battle_vision.scanner import (
    build_fixed_10hz_sample_plan,
    scan_video_10hz,
    validate_frozen_roi_approval,
)
from pokemon_battle_vision.timeline import build_event_timeline, format_timestamp
from pokemon_battle_vision.utils import sha256_file


def _index(pts):
    values = np.asarray(pts, dtype=np.float64)
    return FrameTimestampIndex(
        pts_sec=values,
        duration_sec=np.full(values.size, 0.05, dtype=np.float64),
        key_frame=np.zeros(values.size, dtype=np.bool_),
        validation={"complete": True, "strictly_monotonic": True},
        video_sha256="v" * 64,
        ffprobe_version="8.1.2",
    )


def _record(sample_index, event_type, score, pts=None):
    current_pts = sample_index * 0.1 if pts is None else pts
    scores = {name: 0.0 for name in EVENT_TYPES}
    scores[event_type] = score
    return FrameScanRecord(
        sample_index=sample_index,
        frame_index=sample_index * 3,
        target_time=current_pts,
        pts=current_pts,
        timestamp=format_timestamp(current_pts),
        roi_available=True,
        ui_state=event_type if score >= DEFAULT_THRESHOLDS[event_type] else "UNKNOWN",
        visible_rois=[],
        frame_hash="h" * 64,
        candidate_scores=scores,
    )


def test_fixed_10hz_plan_uses_nearest_authoritative_pts():
    index = _index([0.0, 0.04, 0.11, 0.19, 0.31])
    plan = build_fixed_10hz_sample_plan(index)
    assert [row.target_time for row in plan] == [0.0, 0.1, 0.2, 0.3]
    assert [row.frame_index for row in plan] == [0, 2, 3, 4]
    assert [row.pts for row in plan] == [0.0, 0.11, 0.19, 0.31]


def test_classical_appearance_similarity_has_no_ocr_dependency():
    reference = np.zeros((100, 200, 3), dtype=np.uint8)
    reference[20:80, 30:170] = (50, 80, 160)
    same = appearance_signature(reference)
    different = appearance_signature(np.full_like(reference, 255))
    assert signature_similarity(same, same) == pytest.approx(1.0)
    assert signature_similarity(different, same) < 0.75


def test_timeline_bridges_short_gap_and_records_boundaries():
    records = [
        _record(0, "BATTLE_TEXT", 0.1),
        _record(1, "BATTLE_TEXT", 0.90),
        _record(2, "BATTLE_TEXT", 0.91),
        _record(3, "BATTLE_TEXT", 0.20),
        _record(4, "BATTLE_TEXT", 0.92),
        _record(5, "BATTLE_TEXT", 0.93),
        _record(6, "BATTLE_TEXT", 0.1),
    ]
    events = build_event_timeline(records, max_gap_samples=1)
    assert len(events) == 1
    event = events[0]
    assert event.type == "BATTLE_TEXT"
    assert event.start_frame == 3
    assert event.end_frame == 15
    assert event.start_time == 0.1
    assert event.end_time == 0.5
    assert event.duration_sec == pytest.approx(0.5)


def test_timeline_preserves_observed_trigger_side():
    records = []
    for sample_index in range(3):
        record = _record(sample_index, "TRIGGER_NOTIFICATION", 0.9)
        records.append(
            FrameScanRecord(
                **{
                    **record.to_dict(),
                    "visible_rois": ["opponent_trigger_notification"],
                }
            )
        )
    events = build_event_timeline(records)
    assert len(events) == 1
    assert events[0].visible_rois == ["opponent_trigger_notification"]


def test_frozen_approval_gate_rejects_changed_roi_config(tmp_path):
    video = tmp_path / "video.mp4"
    config = tmp_path / "roi.json"
    manifest_path = tmp_path / "roi_overlay_manifest.json"
    approval_path = tmp_path / "roi_approval.json"
    overlay = tmp_path / "overlay.png"
    video.write_bytes(b"video")
    config.write_text('{"rois": {}}', encoding="utf-8")
    overlay.write_bytes(b"overlay")
    manifest = {
        "video_sha256": sha256_file(video),
        "roi_config_sha256": sha256_file(config),
        "overlays": [{"path": overlay.name, "sha256": sha256_file(overlay)}],
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    approval = {
        "status": "approved",
        "video_sha256": sha256_file(video),
        "roi_config_sha256": sha256_file(config),
        "overlay_manifest_sha256": sha256_file(manifest_path),
        "overlay_count": 1,
    }
    approval_path.write_text(json.dumps(approval), encoding="utf-8")
    validate_frozen_roi_approval(video, config, manifest_path, approval_path)
    config.write_text('{"rois": {"changed": {}}}', encoding="utf-8")
    with pytest.raises(RoiApprovalError, match="roi_config_sha256"):
        validate_frozen_roi_approval(video, config, manifest_path, approval_path)


class FakeCapture:
    def __init__(self, frames):
        self.frames = frames
        self.position = 0
        self.orientation = 1.0

    def isOpened(self):
        return True

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

    def read(self):
        if self.position >= len(self.frames):
            return False, None
        frame = self.frames[self.position]
        self.position += 1
        return True, frame.copy()

    def release(self):
        pass


class FakeDetector:
    def score_frame(self, frame):
        return ({name: (0.9 if name == "MOVE_MENU" else 0.1) for name in EVENT_TYPES}, {})

    def classify(self, scores, visible):
        return "MOVE_MENU", ["move_menu"]


def test_scanner_reads_every_source_frame_but_records_fixed_plan(monkeypatch, tmp_path):
    frames = [np.full((2, 3, 3), value, dtype=np.uint8) for value in range(5)]
    monkeypatch.setattr(cv2, "VideoCapture", lambda path: FakeCapture(frames))
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")
    index = _index([0.0, 0.05, 0.1, 0.15, 0.2])
    plan = build_fixed_10hz_sample_plan(index)
    metadata = {
        "rotation": {"clockwise_degrees": 0},
        "encoded_dimensions": {"width": 3, "height": 2},
        "display_dimensions": {"width": 3, "height": 2},
    }
    records, validation = scan_video_10hz(video, metadata, index, plan, FakeDetector())
    assert validation["decoded_frame_count"] == 5
    assert validation["sample_count"] == 3
    assert [record.frame_index for record in records] == [0, 2, 4]
    assert all(record.ui_state == "MOVE_MENU" for record in records)
