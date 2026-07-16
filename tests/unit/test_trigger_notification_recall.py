from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

from pokemon_battle_vision.checkpoint1b_models import EVENT_TYPES, FrameScanRecord
from pokemon_battle_vision.models import FrameTimestampIndex, PixelRoi
from pokemon_battle_vision.review_frame_extractor import select_candidate_frames
from pokemon_battle_vision.trigger_notification_detection import (
    DEFAULT_TRIGGER_PROPOSAL_CONFIG,
    analyze_trigger_notification_crop,
)
from pokemon_battle_vision.trigger_notification_features import (
    DEFAULT_TRIGGER_FEATURE_CONFIG,
    derive_trigger_analysis_roi,
)
from pokemon_battle_vision.trigger_notification_round1 import (
    build_trigger_round1_mapping,
)
from pokemon_battle_vision.trigger_notification_timeline import (
    build_trigger_notification_timeline,
)


ROOT = Path(__file__).resolve().parents[2]


def _analysis_roi(side="opponent"):
    return PixelRoi(
        "{}_trigger_notification_analysis_context".format(side),
        0,
        0,
        873,
        425,
    )


def _synthetic_notification(short=True, with_icon=False):
    rng = np.random.default_rng(7)
    image = np.full((425, 873, 3), 52, dtype=np.int16)
    image += rng.integers(-8, 9, image.shape, dtype=np.int16)
    image = np.clip(image, 0, 255).astype(np.uint8)
    first_count = 5 if short else 9
    second_count = 2 if short else 5
    for row_y, count, start_x in ((95, first_count, 390), (155, second_count, 455)):
        for index in range(count):
            x = start_x + index * 38
            cv2.rectangle(image, (x, row_y), (x + 27, row_y + 31), (225, 225, 225), 3)
            cv2.line(image, (x + 8, row_y + 4), (x + 20, row_y + 27), (225, 225, 225), 3)
            cv2.line(image, (x + 20, row_y + 4), (x + 8, row_y + 27), (225, 225, 225), 3)
    if with_icon:
        cv2.rectangle(image, (760, 105), (825, 175), (60, 120, 220), -1)
    return image


def _proposal(image, template_score=0.68, feature_config=None):
    feature_config = feature_config or replace(
        DEFAULT_TRIGGER_FEATURE_CONFIG,
        min_edge_density=0.004,
        min_strong_text_occupancy=0.001,
        min_weak_text_occupancy=0.0005,
    )
    config = replace(DEFAULT_TRIGGER_PROPOSAL_CONFIG, feature_config=feature_config)
    return analyze_trigger_notification_crop(
        image,
        side="opponent",
        canonical_roi_id="opponent_trigger_notification",
        analysis_roi=_analysis_roi(),
        template_score=template_score,
        config=config,
    )


def _side_payload(level, score=None):
    proposal_score = score if score is not None else (
        0.8 if level == "strong" else (0.499 if level == "continuation" else 0.5)
    )
    return {
        "analysis_roi_id": "opponent_trigger_notification_analysis_context",
        "analysis_bbox": [0, 0, 873, 425],
        "template_score": 0.7,
        "brightness_contrast": 0.3,
        "edge_density": 0.04,
        "component_count": 12,
        "aligned_component_count": 8,
        "line_span_ratio": 0.4,
        "secondary_aligned_component_count": 3,
        "secondary_line_span_ratio": 0.2,
        "secondary_line_height_cv": 0.2,
        "line_separation_ratio": 0.1,
        "panel_occupancy": 0.8,
        "text_region_occupancy": 0.03,
        "icon_region_occupancy": 0.0,
        "panel_score": 0.9,
        "text_score": 0.85,
        "icon_score": 0.0,
        "combined_score": proposal_score,
        "proposal_score": proposal_score if level != "negative" else 0.2,
        "evidence_level": level,
    }


def _record(index, opponent="negative", player="negative"):
    sides = {
        "player": {**_side_payload(player), "analysis_roi_id": "player_trigger_notification_analysis_context"},
        "opponent": _side_payload(opponent),
    }
    score = max(value["proposal_score"] for value in sides.values())
    visible = []
    for side, value in sides.items():
        if value["evidence_level"] != "negative":
            visible.append("{}_trigger_notification".format(side))
    scores = {event_type: 0.0 for event_type in EVENT_TYPES}
    scores["TRIGGER_NOTIFICATION"] = score
    return FrameScanRecord(
        sample_index=index,
        frame_index=index,
        target_time=index / 10.0,
        pts=index / 10.0,
        timestamp="00:00:00.{:03d}".format(index * 100),
        roi_available=True,
        ui_state="TRIGGER_NOTIFICATION" if visible else "UNKNOWN",
        visible_rois=visible,
        frame_hash="a" * 64,
        candidate_scores=scores,
        trigger_notification_evidence={
            "proposal_score": score,
            "threshold": 0.5,
            "visible_rois": visible,
            "sides": sides,
        },
    )


def _index(count):
    pts = np.asarray([index / 10.0 for index in range(count)], dtype=np.float64)
    return FrameTimestampIndex(
        pts_sec=pts,
        duration_sec=np.full(count, 0.1),
        key_frame=np.zeros(count, dtype=np.bool_),
        validation={"complete": True, "strictly_monotonic": True},
        video_sha256="v" * 64,
        ffprobe_version="test",
    )


def test_short_and_long_two_line_notifications_are_supported():
    assert _proposal(_synthetic_notification(short=True)).raw_positive
    assert _proposal(_synthetic_notification(short=False)).raw_positive


def test_icon_is_optional_but_can_raise_evidence():
    without_icon = _proposal(_synthetic_notification(with_icon=False))
    with_icon = _proposal(_synthetic_notification(with_icon=True))
    assert without_icon.raw_positive
    assert with_icon.raw_positive
    assert with_icon.icon_score > without_icon.icon_score


def test_single_large_bright_blob_is_not_notification_text():
    image = np.full((425, 873, 3), 40, dtype=np.uint8)
    cv2.rectangle(image, (300, 80), (700, 330), (245, 245, 245), -1)
    evidence = _proposal(image)
    assert evidence.raw_positive is False


def test_one_row_stage_lights_fail_two_line_layout():
    image = np.full((425, 873, 3), 45, dtype=np.uint8)
    for x in range(330, 700, 55):
        cv2.circle(image, (x, 140), 7, (240, 240, 240), -1)
    evidence = _proposal(image)
    assert evidence.raw_positive is False
    assert "missing_second_text_line" in evidence.negative_reasons


def test_two_rows_of_small_highlights_fail_glyph_geometry():
    image = np.full((425, 873, 3), 45, dtype=np.uint8)
    for y in (120, 175):
        for x in range(350, 670, 42):
            cv2.circle(image, (x, y), 6, (240, 240, 240), -1)
    evidence = _proposal(image)
    assert evidence.raw_positive is False
    assert "insufficient_primary_glyph_geometry" in evidence.negative_reasons


def test_two_text_rows_without_dark_notification_panel_are_rejected():
    image = _synthetic_notification()
    image[:] = np.maximum(image, 185)
    evidence = _proposal(image)
    assert evidence.raw_positive is False
    assert "missing_dark_notification_panel" in evidence.negative_reasons


def test_derived_analysis_context_preserves_frozen_canonical_roi():
    canonical = PixelRoi("opponent_trigger_notification", 1848, 556, 873, 243)
    derived = derive_trigger_analysis_roi(canonical, 2868, 1320)
    assert canonical == PixelRoi("opponent_trigger_notification", 1848, 556, 873, 243)
    assert derived.y < canonical.y
    assert derived.y2 == canonical.y2


def test_strong_single_sample_opens_and_short_notification_is_kept():
    events, diagnostics = build_trigger_notification_timeline([_record(0, opponent="strong")])
    assert len(events) == 1
    assert events[0].duration_sec == 0.1
    assert diagnostics[1]["decision"] == "open_strong"


def test_weak_requires_temporal_confirmation():
    events, _ = build_trigger_notification_timeline(
        [_record(0, opponent="weak"), _record(1, opponent="weak")]
    )
    assert len(events) == 1
    events, diagnostics = build_trigger_notification_timeline(
        [_record(0, opponent="weak"), _record(1)]
    )
    assert events == []
    assert diagnostics[1]["decision"] == "discard_unconfirmed_weak"


def test_single_negative_gap_bridges_without_duplicate_candidate():
    records = [
        _record(0, opponent="strong"),
        _record(1),
        _record(2, opponent="strong"),
    ]
    events, diagnostics = build_trigger_notification_timeline(records)
    assert len(events) == 1
    assert events[0].start_time == 0.0
    assert events[0].end_time == 0.2
    assert diagnostics[3]["decision"] == "bridge_confirmed"


def test_multiple_negatives_close_at_last_positive():
    events, diagnostics = build_trigger_notification_timeline(
        [_record(0, opponent="strong"), _record(1), _record(2)]
    )
    assert len(events) == 1
    assert events[0].end_time == 0.0
    assert any(row["close_reason"] for row in diagnostics if row["side"] == "opponent")


def test_continuation_only_cannot_open_but_prevents_false_split_when_active():
    events, _ = build_trigger_notification_timeline(
        [_record(0, opponent="continuation"), _record(1, opponent="continuation")]
    )
    assert events == []
    events, diagnostics = build_trigger_notification_timeline(
        [
            _record(0, opponent="strong"),
            _record(1, opponent="continuation"),
            _record(2, opponent="continuation"),
            _record(3, opponent="strong"),
        ]
    )
    assert len(events) == 1
    assert events[0].start_time == 0.0
    assert events[0].end_time == 0.3
    assert any(row["decision"] == "continue_continuation" for row in diagnostics)


def test_player_and_opponent_are_independent_candidates_with_visible_rois():
    records = [
        _record(0, player="strong"),
        _record(1, player="strong", opponent="strong"),
        _record(2, opponent="strong"),
    ]
    events, _ = build_trigger_notification_timeline(records)
    assert len(events) == 2
    assert {tuple(event.visible_rois) for event in events} == {
        ("player_trigger_notification",),
        ("opponent_trigger_notification",),
    }


def test_candidate_duration_follows_evidence_not_fixed_length():
    short, _ = build_trigger_notification_timeline([_record(0, opponent="strong")])
    long, _ = build_trigger_notification_timeline(
        [_record(index, opponent="strong") for index in range(5)]
    )
    assert short[0].duration_sec == 0.1
    assert long[0].duration_sec == 0.5


def test_diagnostics_contain_required_features_and_temporal_reasons():
    _, diagnostics = build_trigger_notification_timeline(
        [_record(0, opponent="strong"), _record(1), _record(2)]
    )
    row = next(value for value in diagnostics if value["side"] == "opponent")
    required = {
        "frame_ordinal",
        "pts",
        "side",
        "template_score",
        "brightness_contrast",
        "edge_density",
        "component_count",
        "panel_occupancy",
        "text_region_occupancy",
        "icon_region_occupancy",
        "temporal_state",
        "decision",
        "open_reason",
        "continue_reason",
        "close_reason",
    }
    assert required.issubset(row)


def test_trigger_review_selects_peak_evidence_and_deduplicates_short_frames():
    event = {
        "event_id": "trigger_notification-0001",
        "type": "TRIGGER_NOTIFICATION",
        "start_frame": 0,
        "end_frame": 2,
        "start_time": 0.0,
        "end_time": 0.2,
        "duration_sec": 0.3,
        "confidence": 0.8,
        "visible_rois": ["opponent_trigger_notification"],
    }
    rows = []
    for index, score in enumerate((0.6, 0.95, 0.7)):
        record = _record(index, opponent="strong")
        payload = record.to_dict()
        payload["trigger_notification_evidence"]["sides"]["opponent"][
            "combined_score"
        ] = score
        payload["candidate_scores"]["TRIGGER_NOTIFICATION"] = score
        rows.append(payload)
    selection = select_candidate_frames(event, rows, _index(3))
    assert selection.representative_frame == 1
    assert selection.strategy == "trigger_notification_peak_evidence_and_boundaries"
    assert len({point.frame_index for point in selection.evidence_points}) == len(
        selection.evidence_points
    )
    roles = {role for point in selection.evidence_points for role in point.roles}
    assert {"start", "peak_evidence", "end"}.issubset(roles)


def test_round1_mapping_uses_side_and_time_overlap():
    fixture = {
        "positive_windows": [
            {
                "case_id": "ability",
                "side": "opponent",
                "trigger_kind": "ability",
                "previous_status": "missed",
                "approx_start_sec": 10.0,
                "approx_end_sec": 12.0,
            }
        ]
    }
    events = [
        {
            "event_id": "trigger_notification-0001",
            "type": "TRIGGER_NOTIFICATION",
            "start_time": 10.5,
            "end_time": 11.5,
            "visible_rois": ["opponent_trigger_notification"],
        }
    ]
    report = build_trigger_round1_mapping(fixture, events)
    assert report["summary"]["all_covered"] is True
    assert report["rows"][0]["mapped_candidate_ids"] == [
        "trigger_notification-0001"
    ]


def test_production_trigger_modules_do_not_hardcode_human_names_or_times():
    files = [
        ROOT / "src/pokemon_battle_vision/trigger_notification_features.py",
        ROOT / "src/pokemon_battle_vision/trigger_notification_detection.py",
        ROOT / "src/pokemon_battle_vision/trigger_notification_timeline.py",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in files)
    for forbidden in ("姆克鷹", "威嚇", "烈咬陸鯊", "生命寶珠", "113.0", "450.0"):
        assert forbidden not in source
    assert "trigger_notification_human_review_round1" not in source
