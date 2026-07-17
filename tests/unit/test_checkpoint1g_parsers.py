from pokemon_battle_vision.checkpoint1g_models import (
    ExtractedVisualFrame,
    OcrObservation,
    VisualFrameRequest,
)
from pokemon_battle_vision.checkpoint1c_models import OcrEngineResult
from pokemon_battle_vision.checkpoint1g import _run_ocr
from pokemon_battle_vision.errors import DependencyError
from pokemon_battle_vision.hp_status_tracker import (
    measure_hp_bar,
    parse_exact_hp,
    parse_percentage,
    rolling_consensus,
)
from pokemon_battle_vision.move_menu_parser import conservative_match, parse_visible_moves
from pokemon_battle_vision.team_selection_parser import parse_selected_four, parse_team_roster

import numpy as np
import pytest


def _frame(request_id, side, slot, fingerprint=None):
    request = VisualFrameRequest(
        request_id=request_id,
        source_id="candidate-1",
        role="team_preview",
        roi_name="roi",
        frame_ordinal=10,
        pts=1.0,
        side=side,
        slot=slot,
    )
    return ExtractedVisualFrame(
        request=request,
        crop_path="",
        evidence_path="evidence/{}.jpg".format(request_id),
        fingerprint=fingerprint or {"dhash": "0" * 16, "hsv_histogram": [1.0] + [0.0] * 23},
    )


def _ocr(request_id, text, confidence=0.95):
    return OcrObservation(
        request_id=request_id,
        raw_text=text,
        confidence=confidence,
        lines=[{"text": text, "confidence": confidence}],
        preprocessing=["raw"],
        error=None,
    )


def test_hp_numeric_and_percentage_parsing():
    assert parse_exact_hp("勾魂眼 48／157") == (48, 157, 30.573)
    assert parse_exact_hp("999/120") is None
    assert parse_percentage("烈咬陸鯊 77％") == 77.0
    assert parse_percentage("120%") is None


def test_visual_bar_estimate_separates_long_health_run_from_large_bright_block():
    image = np.zeros((100, 400, 3), dtype=np.uint8)
    image[15:18, 40:360] = (255, 255, 255)
    image[50:56, 160:360] = (0, 255, 0)
    measured = measure_hp_bar(image)
    assert measured["panel_visible"] is True
    assert measured["colored_run_px"] >= 180
    bright = np.full((100, 400, 3), 255, dtype=np.uint8)
    assert measure_hp_bar(bright)["colored_run_px"] == 0


def test_temporal_consensus_removes_single_ocr_jitter():
    assert rolling_consensus([82, 82, 28, 82, 82]) == [82, 82, 82, 82, 82]


def test_team_preview_parses_partial_roster_without_guessing_unknown_species():
    frames = [_frame("player-1", "player", "slot1"), _frame("opponent-1", "opponent", "slot1")]
    payload = parse_team_roster(frames, {"player-1": _ocr("player-1", "勾魂眼")})
    assert payload["entry_count"] == 2
    assert next(row for row in payload["entries"] if row["side"] == "player")["species_text"] == "勾魂眼"
    assert next(row for row in payload["entries"] if row["side"] == "opponent")["species_text"] is None


def test_selected_four_resolution_is_one_to_one_and_preserves_order():
    first = {"dhash": "0" * 16, "hsv_histogram": [1.0] + [0.0] * 23}
    second = {"dhash": "f" * 16, "hsv_histogram": [0.0, 1.0] + [0.0] * 22}
    roster = parse_team_roster(
        [_frame("p1", "player", "slot1", first), _frame("p2", "player", "slot2", second)],
        {"p1": _ocr("p1", "甲甲"), "p2": _ocr("p2", "乙乙")},
    )
    selected, edges = parse_selected_four(
        [_frame("s1", "player", "slot1", first), _frame("s2", "player", "slot2", second)], roster
    )
    assert [row["selection_order"] for row in selected["player_selected"]] == [1, 2]
    assert [row["species"] for row in selected["player_selected"]] == ["甲甲", "乙乙"]
    assert len(edges) == 2


def test_move_fuzzy_correction_keeps_raw_and_does_not_force_legality():
    assert conservative_match("子彈奉", ["子彈拳"])[0] == "子彈拳"
    assert conservative_match("完全不同", ["子彈拳"])[0] is None
    rows = parse_visible_moves(_ocr("menu", "子彈奉\n守住"), ["子彈拳", "守住"])
    assert rows[0]["raw_text"] == "子彈奉"
    assert rows[0]["value"] == "子彈拳"


def test_checkpoint1g_does_not_hide_production_ocr_runtime_failure():
    class FailingEngine:
        def recognize(self, jobs):
            return [
                OcrEngineResult(
                    job_id=jobs[0]["job_id"],
                    raw_text="",
                    confidence=0.0,
                    lines=[],
                    error="Vision request failed",
                )
            ]

    frame = _frame("runtime-failure", "player", "slot1")
    frame = ExtractedVisualFrame(
        request=VisualFrameRequest(
            request_id=frame.request.request_id,
            source_id=frame.request.source_id,
            role=frame.request.role,
            roi_name=frame.request.roi_name,
            frame_ordinal=frame.request.frame_ordinal,
            pts=frame.request.pts,
            side=frame.request.side,
            slot=frame.request.slot,
            run_ocr=True,
        ),
        crop_path="runtime-failure.png",
        evidence_path=frame.evidence_path,
        fingerprint=frame.fingerprint,
    )
    with pytest.raises(DependencyError, match="production OCR 失敗"):
        _run_ocr(FailingEngine(), [frame])
