import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from pokemon_battle_vision.checkpoint1c import FROZEN_INPUTS, run_checkpoint_1c
from pokemon_battle_vision.ocr_engine import AppleVisionOcrEngine
from pokemon_battle_vision.ocr_normalization import cjk_character_count, normalize_ocr_text
from pokemon_battle_vision.output_transaction import OutputTransaction
from pokemon_battle_vision.scanner import load_frame_timestamp_index
from pokemon_battle_vision.utils import sha256_file


@pytest.mark.slow
@pytest.mark.integration
def test_apple_vision_engine_available_and_traditional_chinese_smoke(tmp_path):
    image = Image.new("RGB", (900, 240), "#18202b")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("/System/Library/Fonts/STHeiti Medium.ttc", 74)
    draw.text((50, 55), "繁體中文測試", font=font, fill="white")
    image_path = tmp_path / "traditional-chinese.png"
    image.save(image_path)
    engine = AppleVisionOcrEngine()
    probe = engine.probe()
    assert probe["available"]
    assert probe["language"] == "zh-Hant"
    result = engine.recognize([{"job_id": "smoke", "image_path": str(image_path)}])[0]
    assert result.error is None
    assert cjk_character_count(normalize_ocr_text(result.raw_text)) >= 4


@pytest.mark.slow
@pytest.mark.integration
def test_win01_complete_checkpoint1c_and_review_pack(tmp_path):
    root = Path(__file__).resolve().parents[2]
    project = tmp_path / "project"
    (project / "outputs").mkdir(parents=True)
    for directory in ("schemas", "src", "configs", "references"):
        (project / directory).symlink_to(root / directory, target_is_directory=True)
    for name in ("checkpoint-1a", "checkpoint-1b", "checkpoint-1b-review"):
        (project / "outputs" / name).symlink_to(root / "outputs" / name, target_is_directory=True)
    before = {
        name: sha256_file(project / relative) for name, relative in FROZEN_INPUTS.items()
    }
    output = project / "outputs/checkpoint-1c"
    review_output = project / "outputs/checkpoint-1c-review"
    manifest = run_checkpoint_1c(
        project_root=project,
        video_path=root / "samples/videos/win-01.mp4",
        checkpoint1b_dir=project / "outputs/checkpoint-1b",
        checkpoint1b_review_dir=project / "outputs/checkpoint-1b-review",
        output_dir=output,
        review_output_dir=review_output,
    )
    assert manifest["processed_candidate_count"] == 178
    assert manifest["processed_candidate_counts"] == {
        "BATTLE_TEXT": 176,
        "TRIGGER_NOTIFICATION": 2,
    }
    assert sum(manifest["validation_counts"].values()) == 178
    assert sum(manifest["workflow_counts"].values()) == 178
    assert manifest["raw_result_count"] >= 178
    assert manifest["validation"]["minimum_multiframe_policy_met"]
    assert manifest["validation"]["raw_results_traceable"]
    assert manifest["frozen_inputs_unchanged"]
    selections = json.loads((output / "ocr_frame_selections.json").read_text())
    source_events = json.loads(
        (project / "outputs/checkpoint-1b/events.json").read_text()
    )
    timestamp_index = load_frame_timestamp_index(
        project / "outputs/checkpoint-1a/frame_timestamps.npz",
        source_events["video_sha256"],
    )
    selections_by_event = {}
    for row in selections["records"]:
        selections_by_event.setdefault(row["event_id"], []).append(row)
    assert len(selections_by_event) == 178
    assert all(
        len(rows) >= 2 or all(row["insufficient_frame_reason"] for row in rows)
        for rows in selections_by_event.values()
    )
    assert all(
        abs(
            float(timestamp_index.pts_sec[int(row["frame_ordinal"])])
            - float(row["pts"])
        )
        <= 1e-6
        and row["detector_template_strength"] >= 0.0
        for row in selections["records"]
    )
    validations = json.loads((output / "text_validations.json").read_text())
    trigger_rows = [
        row for row in validations["records"] if row["event_type"] == "TRIGGER_NOTIFICATION"
    ]
    assert len(trigger_rows) == 2
    assert all(row["validation_label"] != "NO_TEXT" for row in trigger_rows)
    trigger_by_id = {row["event_id"]: row for row in trigger_rows}
    assert "威嚇" in trigger_by_id["trigger_notification-0001"]["ocr_text"]
    assert "生命寶珠" in trigger_by_id["trigger_notification-0002"]["ocr_text"]
    evaluation = json.loads((output / "initial_evaluation_report.json").read_text())
    assert evaluation["inference_feedback_used"] is False
    assert evaluation["failed_count"] == 0
    effect_rows = {
        row["event_id"]: row
        for row in validations["records"]
        if row["event_id"]
        in {
            "battle_text-0049",
            "battle_text-0050",
            "battle_text-0051",
            "battle_text-0086",
            "battle_text-0087",
        }
    }
    assert len(effect_rows) == 5
    assert all(row["validation_label"] == "NO_TEXT" for row in effect_rows.values())
    review = json.loads((review_output / "checkpoint1c_review.json").read_text())
    assert review["record_count"] == 178
    assert all((review_output / row["review_card_path"]).is_file() for row in review["records"])
    assert review["contact_sheets"]["tile_count"] == 178
    assert set(review["contact_sheets"]["page_counts"]) == {
        "BATTLE_TEXT_VALID_TEXT",
        "BATTLE_TEXT_NO_TEXT",
        "BATTLE_TEXT_UNCERTAIN",
        "TRIGGER_NOTIFICATION_VALID_TEXT",
        "TRIGGER_NOTIFICATION_NO_TEXT",
        "TRIGGER_NOTIFICATION_UNCERTAIN",
    }
    assert all(
        row["human_text"] is None
        and row["human_decision"] is None
        and row["human_action"] is None
        and row["reviewed_at"] is None
        and row["reviewed_by"] is None
        for row in review["records"]
    )
    assert all(
        row["review_card_path"].startswith(
            "candidates/{}/".format(row["workflow_status"])
        )
        for row in review["records"]
    )
    assert {name: sha256_file(project / relative) for name, relative in FROZEN_INPUTS.items()} == before
    assert OutputTransaction.hidden_items(output) == []
    assert OutputTransaction.hidden_items(review_output) == []
    assert not list((project / "outputs").glob("*tmp-*"))
    assert not list((project / "outputs").glob("*backup-*"))
    assert not list((project / "outputs").glob("* 2"))
    assert not list(output.rglob(".DS_Store"))
    assert not list(review_output.rglob(".DS_Store"))
