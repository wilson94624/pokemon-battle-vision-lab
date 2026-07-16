import json
from pathlib import Path

import pytest
from jsonschema import validate

from pokemon_battle_vision.battle_text_audit import regression_coverage
from pokemon_battle_vision.battle_text_round1 import build_round1_mapping
from pokemon_battle_vision.checkpoint1b_models import EVENT_TYPES
from pokemon_battle_vision.scanner import SCAN_HZ, run_checkpoint_1b
from pokemon_battle_vision.output_transaction import OutputTransaction
from pokemon_battle_vision.utils import sha256_file


@pytest.mark.slow
@pytest.mark.integration
def test_win01_complete_checkpoint_1b_scan(tmp_path):
    root = Path(__file__).resolve().parents[2]
    project = tmp_path / "project"
    (project / "outputs").mkdir(parents=True)
    (project / "schemas").symlink_to(root / "schemas", target_is_directory=True)
    (project / "src").symlink_to(root / "src", target_is_directory=True)
    (project / "references").symlink_to(root / "references", target_is_directory=True)
    checkpoint1a = root / "outputs/checkpoint-1a"
    checkpoint1a_hashes = {
        path.relative_to(checkpoint1a): sha256_file(path)
        for path in checkpoint1a.rglob("*")
        if path.is_file()
    }
    config = root / "configs/roi_2868x1320.json"
    frozen_config_hash = sha256_file(config)
    output = project / "outputs/checkpoint-1b"
    debug_output = project / "outputs/checkpoint-1b-debug"

    report = run_checkpoint_1b(
        project_root=project,
        video_path=root / "samples/videos/win-01.mp4",
        roi_config_path=config,
        checkpoint1a_dir=checkpoint1a,
        roi_approval_path=checkpoint1a / "roi_approval.json",
        output_dir=output,
        debug_output_dir=debug_output,
    )

    assert report["status"] == "complete"
    assert report["full_video_scanned"] is True
    assert report["sampling_hz"] == SCAN_HZ == 10.0
    assert report["ocr_performed"] is False
    assert report["pts_authority"] == "ffprobe.best_effort_timestamp_time"
    assert report["counts"]["source_frames"] == 25873
    assert report["counts"]["sampled_frames"] == 5918
    assert sha256_file(config) == frozen_config_hash

    frames = [json.loads(line) for line in (output / "frames.jsonl").read_text().splitlines()]
    assert len(frames) == 5918
    assert {
        "frame_index",
        "pts",
        "timestamp",
        "roi_available",
        "ui_state",
        "visible_rois",
        "frame_hash",
    }.issubset(frames[0])
    frame_schema = json.loads(
        (root / "schemas/frame_metadata.schema.json").read_text(encoding="utf-8")
    )
    for frame in (frames[0], frames[len(frames) // 2], frames[-1]):
        validate(instance=frame, schema=frame_schema)

    events = json.loads((output / "events.json").read_text(encoding="utf-8"))
    events_schema = json.loads((root / "schemas/events.schema.json").read_text(encoding="utf-8"))
    validate(instance=events, schema=events_schema)
    assert events["ocr_performed"] is False
    assert set(events["event_counts"]) == set(EVENT_TYPES)
    assert all(count > 0 for count in events["event_counts"].values())
    assert events["event_counts"]["BATTLE_TEXT"] == 176
    assert {
        event_type: events["event_counts"][event_type]
        for event_type in EVENT_TYPES
        if event_type not in ("BATTLE_TEXT", "TRIGGER_NOTIFICATION")
    } == {
        "TEAM_PREVIEW": 1,
        "SELECTED_FOUR": 1,
        "MOVE_MENU": 31,
        "RESULT": 1,
    }
    assert events["event_counts"]["TRIGGER_NOTIFICATION"] >= 2
    assert regression_coverage(events["events"])["covered_count"] == 17
    fixture = json.loads(
        (root / "references/battle_text_human_review_round1.json").read_text()
    )
    round1 = build_round1_mapping(fixture, events["events"])
    assert round1["accepted_preservation"]["covered_count"] == 13
    assert round1["false_positive_removal"]["removed_count"] >= 12
    assert round1["case_0033_multi_text_split"]["success"] is True
    assert any(
        event["type"] == "TRIGGER_NOTIFICATION"
        and event["start_time"] <= 452.558333 <= event["end_time"] + 0.1
        and event["visible_rois"] == ["opponent_trigger_notification"]
        for event in events["events"]
    )
    assert any(
        event["type"] == "TRIGGER_NOTIFICATION"
        and event["start_time"] <= 114.0 <= event["end_time"] + 0.1
        and event["visible_rois"] == ["opponent_trigger_notification"]
        for event in events["events"]
    )
    diagnostics = [
        json.loads(line)
        for line in (debug_output / "battle_text_diagnostics.jsonl").read_text().splitlines()
    ]
    diagnostic_report = json.loads(
        (debug_output / "battle_text_detector_report.json").read_text(encoding="utf-8")
    )
    diagnostic_schema = json.loads(
        (root / "schemas/battle_text_diagnostic.schema.json").read_text(encoding="utf-8")
    )
    assert len(diagnostics) == 5918
    for row in (diagnostics[0], diagnostics[len(diagnostics) // 2], diagnostics[-1]):
        validate(instance=row, schema=diagnostic_schema)
    assert diagnostic_report["new"]["regression"]["covered_count"] == 17
    assert diagnostic_report["new"]["regression"]["still_missed"] == []
    assert diagnostic_report["duration_filter_present"] is False
    assert diagnostic_report["cooldown_present"] is False
    trigger_diagnostics = [
        json.loads(line)
        for line in (debug_output / "trigger_notification_diagnostics.jsonl").read_text().splitlines()
    ]
    trigger_schema = json.loads(
        (root / "schemas/trigger_notification_diagnostic.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(trigger_diagnostics) == 5918 * 2
    for row in (
        trigger_diagnostics[0],
        trigger_diagnostics[len(trigger_diagnostics) // 2],
        trigger_diagnostics[-1],
    ):
        validate(instance=row, schema=trigger_schema)
    trigger_audit = json.loads(
        (debug_output / "trigger_notification_audit_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert trigger_audit["required_positive_coverage"]["all_covered"] is True
    assert not list((project / "outputs").glob("*tmp-*"))
    assert not list((project / "outputs").glob("*backup-*"))
    assert OutputTransaction.hidden_items(output) == []
    assert OutputTransaction.hidden_items(debug_output) == []
    assert {
        path.relative_to(checkpoint1a): sha256_file(path)
        for path in checkpoint1a.rglob("*")
        if path.is_file()
    } == checkpoint1a_hashes
