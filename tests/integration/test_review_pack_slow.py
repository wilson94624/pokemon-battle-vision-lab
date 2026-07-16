import csv
import json
import math
from pathlib import Path

import pytest
from jsonschema import validate

from pokemon_battle_vision.review_pack import IMMUTABLE_DETECTOR_FILES, build_review_pack
from pokemon_battle_vision.output_transaction import OutputTransaction
from pokemon_battle_vision.utils import sha256_file


@pytest.mark.slow
@pytest.mark.integration
def test_win01_complete_current_candidate_review_pack(tmp_path):
    root = Path(__file__).resolve().parents[2]
    project = tmp_path / "project"
    (project / "outputs").mkdir(parents=True)
    (project / "schemas").symlink_to(root / "schemas", target_is_directory=True)
    (project / "src").symlink_to(root / "src", target_is_directory=True)
    (project / "references").symlink_to(root / "references", target_is_directory=True)
    events_path = root / "outputs/checkpoint-1b/events.json"
    frames_path = root / "outputs/checkpoint-1b/frames.jsonl"
    roi_config_path = root / "configs/roi_2868x1320.json"
    approval_path = root / "outputs/checkpoint-1a/roi_approval.json"
    immutable_paths = [
        events_path,
        frames_path,
        roi_config_path,
        approval_path,
        *(root / path for path in IMMUTABLE_DETECTOR_FILES),
    ]
    before = {path: sha256_file(path) for path in immutable_paths}
    output = project / "outputs/checkpoint-1b-review"
    source_events = json.loads(events_path.read_text(encoding="utf-8"))

    manifest = build_review_pack(
        project_root=project,
        video_path=root / "samples/videos/win-01.mp4",
        events_path=events_path,
        frames_path=frames_path,
        checkpoint1a_dir=root / "outputs/checkpoint-1a",
        roi_config_path=roi_config_path,
        output_dir=output,
        coverage_interval_sec=0.5,
        diagnostics_path=root
        / "outputs/checkpoint-1b-debug/battle_text_diagnostics.jsonl",
    )

    assert manifest["candidate_count"] == source_events["event_count"]
    assert manifest["candidate_counts_by_type"] == source_events["event_counts"]
    assert manifest["contact_sheets"]["page_counts"] == {
        event_type: math.ceil(count / 12.0)
        for event_type, count in source_events["event_counts"].items()
    }
    assert manifest["coverage_review"]["interval_sec"] == 0.5
    assert manifest["coverage_review"]["tile_count"] == 1184
    assert manifest["coverage_review"]["page_count"] == 74
    assert manifest["dense_recall_audit"]["interval_sec"] == 0.1
    assert manifest["dense_recall_audit"]["tile_count"] > 0
    assert manifest["battle_text_recall_summary"]["regression_windows_covered"] == 17
    assert manifest["battle_text_recall_summary"]["regression_windows_still_missed"] == []
    assert manifest["frame_extraction"]["decoded_frame_count"] == 25873
    assert manifest["frame_extraction"]["extraction_method"] == "single_full_sequential_decode"
    assert manifest["immutable_sources_unchanged"] is True
    assert manifest["detector_rerun"] is False
    assert manifest["ocr_performed"] is False
    assert manifest["checkpoint_1c_started"] is False
    assert manifest["battle_text_round1_regression"]["page_count"] == 9
    assert manifest["battle_text_round1_regression"][
        "false_positive_removed_count"
    ] >= 12
    assert manifest["trigger_notification_round1_regression"]["page_count"] == 1
    assert manifest["trigger_notification_round1_regression"]["all_covered"] is True

    review = json.loads((output / "candidate_review.json").read_text(encoding="utf-8"))
    schema = json.loads((root / "schemas/candidate_review.schema.json").read_text(encoding="utf-8"))
    validate(instance=review, schema=schema)
    assert review["record_count"] == len(review["records"]) == source_events["event_count"]
    assert len({row["candidate_id"] for row in review["records"]}) == source_events["event_count"]
    assert all((output / row["review_image_path"]).is_file() for row in review["records"])
    assert all(row["human_status"] == "pending" for row in review["records"])
    battle_rows = [row for row in review["records"] if row["predicted_type"] == "BATTLE_TEXT"]
    assert all(
        row["review_frame_strategy"]
        == "battle_text_peak_structure_and_boundaries"
        for row in battle_rows
    )
    trigger_rows = [
        row for row in review["records"] if row["predicted_type"] == "TRIGGER_NOTIFICATION"
    ]
    assert all(
        row["review_frame_strategy"]
        == "trigger_notification_peak_evidence_and_boundaries"
        for row in trigger_rows
    )
    assert all(
        "peak_evidence"
        in {role for point in row["evidence_frames"] for role in point["roles"]}
        for row in trigger_rows
    )
    assert all(
        "peak_score_structure"
        in {role for point in row["evidence_frames"] for role in point["roles"]}
        for row in battle_rows
    )

    with (output / "candidate_review.csv").open(encoding="utf-8-sig", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    assert len(csv_rows) == source_events["event_count"]
    assert [row["candidate_id"] for row in csv_rows] == [row["candidate_id"] for row in review["records"]]
    assert {path: sha256_file(path) for path in immutable_paths} == before
    assert not list((project / "outputs").glob("*tmp-*"))
    assert not list((project / "outputs").glob("*backup-*"))
    assert OutputTransaction.hidden_items(output) == []
