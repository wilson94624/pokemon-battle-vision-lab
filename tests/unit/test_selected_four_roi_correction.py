import hashlib
from pathlib import Path

import numpy as np

from pokemon_battle_vision.checkpoint1g_frame_extractor import (
    derived_visual_rois,
    selection_roi_covers_full_player_roster,
)
from pokemon_battle_vision.checkpoint1g_models import (
    ExtractedVisualFrame,
    OcrObservation,
    VisualFrameRequest,
)
from pokemon_battle_vision.checkpoint1g_planning import build_visual_frame_requests
from pokemon_battle_vision.config import load_roi_config
from pokemon_battle_vision.models import FrameTimestampIndex
from pokemon_battle_vision.roi import pixel_rois
from pokemon_battle_vision.team_selection_parser import (
    parse_selected_four,
    parse_team_roster,
)


PROJECT = Path(__file__).resolve().parents[2]
LEGACY_CONFIG = PROJECT / "configs/roi_2868x1320.json"
CORRECTED_CONFIG = PROJECT / "configs/roi_2868x1320_v2.json"


def _index() -> FrameTimestampIndex:
    return FrameTimestampIndex(
        pts_sec=np.array([0.0, 0.1, 0.2, 0.3]),
        duration_sec=np.array([0.1, 0.1, 0.1, 0.1]),
        key_frame=np.array([True, False, False, False]),
        validation={},
        video_sha256="video",
        ffprobe_version="test",
    )


def _fingerprint(row: int):
    histogram = [0.0] * 24
    histogram[(row - 1) % len(histogram)] = 1.0
    return {"dhash": format(row, "016x"), "hsv_histogram": histogram}


def _frame(request_id: str, row: int, role: str) -> ExtractedVisualFrame:
    return ExtractedVisualFrame(
        request=VisualFrameRequest(
            request_id=request_id,
            source_id=(
                "team-preview-0001" if role == "team_preview" else "selected_four-0001"
            ),
            role=role,
            roi_name="{}:slot{}".format(
                "team_preview_player" if role == "team_preview" else "selected_four",
                row,
            ),
            frame_ordinal=2,
            pts=0.2,
            side="player",
            slot="slot{}".format(row),
            run_ocr=True,
            keep_evidence=True,
        ),
        crop_path="/tmp/{}.png".format(request_id),
        evidence_path="evidence/{}.jpg".format(request_id),
        fingerprint=_fingerprint(row),
    )


def _ocr(request_id: str, text: str) -> OcrObservation:
    return OcrObservation(
        request_id=request_id,
        raw_text=text,
        confidence=0.95 if text else 0.0,
        lines=([{"text": text, "confidence": 0.95}] if text else []),
        preprocessing=["test"],
        error=None,
    )


def test_corrected_roi_covers_top_and_bottom_roster_rows_with_distinct_semantics():
    config, normalized = load_roi_config(CORRECTED_CONFIG)
    selected = config["rois"]["selected_four"]
    preview = config["rois"]["team_preview_player"]
    assert {key: selected[key] for key in ("x", "y", "width", "height")} == {
        key: preview[key] for key in ("x", "y", "width", "height")
    }
    assert "選擇順序" in selected["purpose"]
    assert "不判定已選四隻" in preview["purpose"]

    base = pixel_rois(normalized, 2868, 1320)
    assert selection_roi_covers_full_player_roster(base) is True
    assert (
        base["selected_four"].x,
        base["selected_four"].y,
        base["selected_four"].width,
        base["selected_four"].height,
    ) == (
        base["team_preview_player"].x,
        base["team_preview_player"].y,
        base["team_preview_player"].width,
        base["team_preview_player"].height,
    )
    assert base["selected_four"].to_dict() == {
        "roi_id": "selected_four",
        "x": 338,
        "y": 64,
        "width": 695,
        "height": 1154,
    }
    rows = derived_visual_rois(base)
    selected_rows = [rows["selected_four:slot{}".format(index)] for index in range(1, 7)]
    assert selected_rows[0].y >= base["selected_four"].y
    assert selected_rows[-1].y2 <= base["selected_four"].y2
    assert selected_rows[0].x == selected_rows[-1].x == base["selected_four"].x
    assert selected_rows[0].width == selected_rows[-1].width == base["selected_four"].width


def test_corrected_planning_requests_all_six_rows_and_marker_ocr():
    requests = build_visual_frame_requests(
        [{"event_id": "selected_four-0001", "type": "SELECTED_FOUR"}],
        [{"candidate_id": "selected_four-0001", "representative_frame": 2}],
        [],
        _index(),
        selected_four_row_count=6,
        selected_four_marker_ocr=True,
    )
    assert [row.request_id for row in requests] == [
        "selected-four-row1",
        "selected-four-row2",
        "selected-four-row3",
        "selected-four-row4",
        "selected-four-row5",
        "selected-four-row6",
    ]
    assert [row.roi_name for row in requests] == [
        "selected_four:slot{}".format(index) for index in range(1, 7)
    ]
    assert all(row.run_ocr and row.keep_evidence for row in requests)


def test_non_contiguous_rows_1_3_5_6_preserve_observed_selection_order():
    roster_frames = [_frame("preview-row{}".format(row), row, "team_preview") for row in range(1, 7)]
    roster_ocr = {
        frame.request.request_id: _ocr(frame.request.request_id, "寶可夢{}".format(index))
        for index, frame in enumerate(roster_frames, 1)
    }
    roster = parse_team_roster(roster_frames, roster_ocr)

    selected_frames = [_frame("selected-row{}".format(row), row, "selected_four") for row in range(1, 7)]
    marker_by_row = {1: "2", 2: "", 3: "４", 4: "", 5: "1", 6: "3"}
    selected_ocr = {
        frame.request.request_id: _ocr(
            frame.request.request_id,
            marker_by_row[int(str(frame.request.slot).replace("slot", ""))],
        )
        for frame in selected_frames
    }
    selected, edges = parse_selected_four(selected_frames, roster, selected_ocr)

    assert selected["selection_complete"] is True
    assert selected["ordering_semantics"] == "observed_ui_order"
    assert [row["selection_order"] for row in selected["player_selected"]] == [1, 2, 3, 4]
    assert [row["roster_row"] for row in selected["player_selected"]] == [5, 1, 6, 3]
    assert {row["roster_row"] for row in selected["player_selected"]} == {1, 3, 5, 6}
    assert len(selected["row_observations"]) == 6
    assert len(edges) == 4
    assert all(
        row["resolution_rule_id"] == "selected_four.marker_and_roster_row_alignment.v2"
        for row in selected["player_selected"]
    )


def test_frozen_legacy_config_remains_byte_stable_and_explicitly_is_not_full_roster():
    assert (
        hashlib.sha256(LEGACY_CONFIG.read_bytes()).hexdigest()
        == "8465d6f877757a2392b2def2dab2d167b89edab5685906e669e04b09b649b499"
    )
    _, normalized = load_roi_config(LEGACY_CONFIG)
    base = pixel_rois(normalized, 2868, 1320)
    assert selection_roi_covers_full_player_roster(base) is False
    derived = derived_visual_rois(base)
    assert "selected_four:slot4" in derived
    assert "selected_four:slot5" not in derived

    requests = build_visual_frame_requests(
        [{"event_id": "selected_four-0001", "type": "SELECTED_FOUR"}],
        [{"candidate_id": "selected_four-0001", "representative_frame": 2}],
        [],
        _index(),
        selected_four_row_count=4,
        selected_four_marker_ocr=False,
    )
    assert [row.request_id for row in requests] == [
        "selected-four-slot1",
        "selected-four-slot2",
        "selected-four-slot3",
        "selected-four-slot4",
    ]
    assert not any(row.run_ocr for row in requests)
