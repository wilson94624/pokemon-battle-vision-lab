import json
from pathlib import Path

import pytest
from jsonschema import validate

from pokemon_battle_vision.checkpoint1b_models import EVENT_TYPES
from pokemon_battle_vision.scanner import SCAN_HZ, run_checkpoint_1b
from pokemon_battle_vision.utils import sha256_file


@pytest.mark.slow
@pytest.mark.integration
def test_win01_complete_checkpoint_1b_scan(tmp_path):
    root = Path(__file__).resolve().parents[2]
    checkpoint1a = root / "outputs/checkpoint-1a"
    config = root / "configs/roi_2868x1320.json"
    frozen_config_hash = sha256_file(config)
    output = tmp_path / "checkpoint-1b"

    report = run_checkpoint_1b(
        project_root=root,
        video_path=root / "samples/videos/win-01.mp4",
        roi_config_path=config,
        checkpoint1a_dir=checkpoint1a,
        roi_approval_path=checkpoint1a / "roi_approval.json",
        output_dir=output,
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
    assert any(
        event["type"] == "TRIGGER_NOTIFICATION"
        and event["start_time"] <= 452.558333 <= event["end_time"] + 0.1
        and event["visible_rois"] == ["opponent_trigger_notification"]
        for event in events["events"]
    )
