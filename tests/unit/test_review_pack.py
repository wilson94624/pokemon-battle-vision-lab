import csv
import json

import numpy as np
import pytest

from pokemon_battle_vision.errors import InputError
from pokemon_battle_vision.image_io import encode_image
from pokemon_battle_vision.models import FrameTimestampIndex
from pokemon_battle_vision.review_frame_extractor import (
    EVENT_REVIEW_ROIS,
    build_coverage_samples,
    roi_ids_for_event,
    select_candidate_frames,
)
from pokemon_battle_vision.review_pack import (
    _load_events,
    _require_files,
    _write_candidate_csv,
)
from pokemon_battle_vision.review_pack_models import (
    CandidateEvidencePoint,
    CandidateFrameSelection,
    CandidateReviewRecord,
    CoverageSample,
    EncodedFrameEvidence,
)
from pokemon_battle_vision.review_pack_render import (
    build_candidate_contact_sheets,
    build_coverage_contact_sheets,
    build_dense_recall_audit_sheets,
)


def _index(pts):
    values = np.asarray(pts, dtype=np.float64)
    return FrameTimestampIndex(
        pts_sec=values,
        duration_sec=np.full(values.size, 0.1, dtype=np.float64),
        key_frame=np.zeros(values.size, dtype=np.bool_),
        validation={"complete": True, "strictly_monotonic": True},
        video_sha256="v" * 64,
        ffprobe_version="8.1.2",
    )


def _event(candidate_id="battle_text-0001", event_type="BATTLE_TEXT", frame=0):
    return {
        "event_id": candidate_id,
        "type": event_type,
        "start_frame": frame,
        "end_frame": frame,
        "start_time": float(frame),
        "end_time": float(frame),
        "duration_sec": 0.1,
        "confidence": 0.9,
        "visible_rois": list(EVENT_REVIEW_ROIS[event_type]),
    }


def _selection(candidate_id, frame=0):
    point = CandidateEvidencePoint(
        roles=("start", "middle", "end", "peak_score_structure"),
        frame_index=frame,
        pts=float(frame),
        score=0.9,
        text_structure_strength=0.8,
        evidence_level="strong",
        decision="open_candidate",
    )
    return CandidateFrameSelection(
        candidate_id=candidate_id,
        start_frame=frame,
        middle_frame=frame,
        end_frame=frame,
        start_pts=float(frame),
        middle_pts=float(frame),
        end_pts=float(frame),
        representative_frame=frame,
        representative_pts=float(frame),
        strategy="battle_text_peak_structure_and_boundaries",
        evidence_points=(point,),
    )


def _review_record(candidate_id, event_type="BATTLE_TEXT", frame=0):
    return CandidateReviewRecord(
        candidate_id=candidate_id,
        predicted_type=event_type,
        start_frame=frame,
        middle_frame=frame,
        end_frame=frame,
        start_time=float(frame),
        middle_time=float(frame),
        end_time=float(frame),
        duration_sec=0.1,
        confidence=0.9,
        visible_rois=list(EVENT_REVIEW_ROIS[event_type]),
        representative_time=float(frame),
        representative_frame=frame,
        review_image_path="candidates/{}/{}__review.jpg".format(event_type, candidate_id),
        review_frame_strategy="battle_text_peak_structure_and_boundaries",
        evidence_frames=[_selection(candidate_id, frame).evidence_points[0].to_dict()],
    )


def _evidence(frame=0):
    full = np.full((72, 128, 3), (30, 60, 90), dtype=np.uint8)
    crop = np.full((30, 80, 3), (80, 110, 140), dtype=np.uint8)
    roi_jpegs = {
        roi_id: encode_image(crop, "jpeg", jpeg_quality=90)
        for rois in EVENT_REVIEW_ROIS.values()
        for roi_id in rois
    }
    return EncodedFrameEvidence(
        frame_index=frame,
        pts=float(frame),
        full_frame_jpeg=encode_image(full, "jpeg", jpeg_quality=90),
        roi_jpegs=roi_jpegs,
    )


def test_select_candidate_frames_uses_authoritative_pts_and_sampled_middle():
    timestamp_index = _index([0.0, 0.1, 0.2, 0.3, 0.4])
    event = {
        **_event(frame=1),
        "start_frame": 1,
        "end_frame": 4,
        "start_time": 0.1,
        "end_time": 0.4,
    }
    frame_records = [
        {"sample_index": 0, "frame_index": 1, "pts": 0.1},
        {"sample_index": 1, "frame_index": 2, "pts": 0.2},
        {"sample_index": 2, "frame_index": 4, "pts": 0.4},
    ]

    selection = select_candidate_frames(event, frame_records, timestamp_index)

    assert (selection.start_frame, selection.middle_frame, selection.end_frame) == (1, 2, 4)
    assert (selection.start_pts, selection.middle_pts, selection.end_pts) == (0.1, 0.2, 0.4)


def test_battle_text_selection_uses_first_peak_last_strong_not_only_middle():
    timestamp_index = _index([0.0, 0.1, 0.2, 0.3, 0.4])
    event = {
        **_event(frame=0),
        "start_frame": 0,
        "end_frame": 4,
        "start_time": 0.0,
        "end_time": 0.4,
    }
    rows = []
    for frame, (level, score, structure) in enumerate(
        [
            ("weak", 0.5, 0.3),
            ("strong", 0.95, 0.95),
            ("strong", 0.8, 0.7),
            ("strong", 0.75, 0.6),
            ("weak", 0.5, 0.2),
        ]
    ):
        rows.append(
            {
                "sample_index": frame,
                "frame_index": frame,
                "pts": frame / 10.0,
                "candidate_scores": {"BATTLE_TEXT": score},
                "battle_text_evidence": {
                    "evidence_level": level,
                    "strong_positive": level == "strong",
                    "text_line_strength": structure,
                },
            }
        )
    selection = select_candidate_frames(event, rows, timestamp_index)
    roles = {
        role: point.frame_index
        for point in selection.evidence_points
        for role in point.roles
    }
    assert roles["start"] == 0
    assert roles["first_strong_positive"] == 1
    assert roles["peak_score_structure"] == 1
    assert roles["last_strong_positive"] == 3
    assert roles["end"] == 4
    assert selection.representative_frame == 1
    assert selection.middle_frame == 2


def test_long_battle_text_selection_adds_unique_evidence_strip():
    pts = [index / 10.0 for index in range(41)]
    event = {
        **_event(frame=0),
        "start_frame": 0,
        "end_frame": 40,
        "start_time": 0.0,
        "end_time": 4.0,
        "duration_sec": 4.1,
    }
    rows = [
        {
            "sample_index": frame,
            "frame_index": frame,
            "pts": pts[frame],
            "candidate_scores": {"BATTLE_TEXT": 0.8},
            "battle_text_evidence": {
                "evidence_level": "strong",
                "strong_positive": True,
                "text_line_strength": 0.8,
            },
        }
        for frame in range(41)
    ]
    selection = select_candidate_frames(event, rows, _index(pts))
    strip = [
        point for point in selection.evidence_points if "evidence_strip" in point.roles
    ]
    assert 8 <= len(strip) <= 10
    assert len({point.frame_index for point in selection.evidence_points}) == len(
        selection.evidence_points
    )


def test_select_candidate_frames_rejects_pts_ordinal_mismatch():
    event = {**_event(frame=1), "start_time": 0.11, "end_time": 0.11}
    frame_records = [{"sample_index": 0, "frame_index": 1, "pts": 0.1}]
    with pytest.raises(InputError, match="start frame／PTS"):
        select_candidate_frames(event, frame_records, _index([0.0, 0.1]))


@pytest.mark.parametrize(
    ("event_type", "expected"),
    [(event_type, list(rois)) for event_type, rois in EVENT_REVIEW_ROIS.items() if event_type != "TRIGGER_NOTIFICATION"],
)
def test_each_event_type_uses_frozen_review_rois(event_type, expected):
    assert roi_ids_for_event(_event(event_type=event_type)) == expected


def test_trigger_notification_uses_only_actual_visible_roi():
    event = _event(event_type="TRIGGER_NOTIFICATION")
    event["visible_rois"] = ["opponent_trigger_notification"]
    assert roi_ids_for_event(event) == ["opponent_trigger_notification"]


def test_review_record_and_csv_human_defaults(tmp_path):
    record = _review_record("battle_text-0001")
    assert record.human_status == "pending"
    assert record.corrected_type == ""
    assert record.boundary_quality == ""
    assert record.merge_with_candidate_id == ""
    assert record.split_required is False
    assert record.notes == ""

    output = tmp_path / "candidate_review.csv"
    _write_candidate_csv(output, [record])
    with output.open(encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["human_status"] == "pending"
    assert row["corrected_type"] == ""
    assert row["split_required"] == "false"


def test_candidate_contact_sheet_paginates_and_indexes_every_candidate(tmp_path):
    events = [_event("battle_text-{:04d}".format(index), frame=index) for index in range(13)]
    selections = {event["event_id"]: _selection(event["event_id"], index) for index, event in enumerate(events)}
    records = {event["event_id"]: _review_record(event["event_id"], frame=index) for index, event in enumerate(events)}
    evidence = {index: _evidence(index) for index in range(13)}

    index = build_candidate_contact_sheets(
        events, records, selections, evidence, tmp_path / "contact_sheets"
    )

    assert index["page_counts"]["BATTLE_TEXT"] == 2
    assert len(index["candidate_lookup"]) == 13
    assert index["candidate_lookup"]["battle_text-0012"]["page"] == 2
    assert index["pages_by_type"]["BATTLE_TEXT"][1]["tile_count"] == 1


def test_coverage_overlap_marks_candidate_and_no_candidate():
    event = {
        **_event(),
        "start_time": 1.0,
        "end_time": 2.0,
        "start_frame": 1,
        "end_frame": 2,
    }
    samples = build_coverage_samples(_index([0.0, 1.0, 2.0, 3.0]), [event], 1.0)
    assert [sample.candidate_ids for sample in samples] == [
        [],
        ["battle_text-0001"],
        ["battle_text-0001"],
        [],
    ]
    assert samples[0].label == "NO_CANDIDATE"


def test_half_second_coverage_marks_only_battle_text_overlap():
    battle = {
        **_event(),
        "start_time": 0.5,
        "end_time": 1.0,
        "start_frame": 1,
        "end_frame": 2,
    }
    move = {
        **_event("move_menu-0001", "MOVE_MENU"),
        "start_time": 0.0,
        "end_time": 1.5,
        "start_frame": 0,
        "end_frame": 3,
    }
    samples = build_coverage_samples(
        _index([0.0, 0.5, 1.0, 1.5]), [battle, move], 0.5
    )
    assert [sample.candidate_ids for sample in samples] == [
        [],
        ["battle_text-0001"],
        ["battle_text-0001"],
        [],
    ]


def test_coverage_contact_sheet_paginates_and_keeps_tile_index(tmp_path):
    samples = [
        CoverageSample(index, float(index), index, float(index), [], [])
        for index in range(17)
    ]
    evidence = {index: _evidence(index) for index in range(17)}
    index = build_coverage_contact_sheets(samples, evidence, tmp_path / "coverage_review")
    assert index["page_count"] == 2
    assert index["tile_count"] == 17
    assert index["pages"][1]["tiles"][0]["sample_index"] == 16
    assert index["pages"][1]["tiles"][0]["label"] == "NO_CANDIDATE"


def test_dense_recall_audit_outputs_score_decision_and_index(tmp_path):
    row = {
        "pts": 57.988,
        "frame_ordinal": 5,
        "candidate_id": "battle_text-0001",
        "battle_text_score": 0.72,
        "threshold": 0.5,
        "decision": "continue_candidate",
        "regression_targets": [57.988],
    }
    index = build_dense_recall_audit_sheets(
        [row], {5: _evidence(5)}, tmp_path / "battle_text_recall_audit"
    )
    assert index["tile_count"] == 1
    assert index["page_count"] == 1
    assert index["pages"][0]["tiles"][0]["frame_ordinal"] == 5


def test_duplicate_candidate_ids_are_rejected(tmp_path):
    path = tmp_path / "events.json"
    event = _event()
    path.write_text(
        json.dumps({"checkpoint": "1B", "event_count": 2, "events": [event, event]}),
        encoding="utf-8",
    )
    with pytest.raises(InputError, match="不可重複"):
        _load_events(path)


@pytest.mark.parametrize(
    "filename",
    ["missing.mp4", "events.json", "frames.jsonl", "roi_2868x1320.json"],
)
def test_missing_review_pack_inputs_have_clear_errors(tmp_path, filename):
    missing = tmp_path / filename
    with pytest.raises(InputError, match="缺少必要輸入"):
        _require_files([missing])
