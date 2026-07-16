"""Checkpoint 1D Battle Event Parser 的單元與小型 pipeline 測試。"""

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from pokemon_battle_vision.battle_event_normalization import normalize_battle_text
from pokemon_battle_vision.battle_event_parser import BattleEventParser
from pokemon_battle_vision.checkpoint1d import (
    READ_ONLY_INPUTS,
    acceptance_for_record,
    run_checkpoint_1d,
    select_accepted_review_records,
)
from pokemon_battle_vision.errors import InputError


@pytest.mark.parametrize(
    "text,input_type,event_type,expected",
    [
        ("烈咬陸鯊使出了\n地震!", "BATTLE_TEXT", "MOVE", {"move": "地震"}),
        ("姆克鷹的\n威嚇", "TRIGGER_NOTIFICATION", "ABILITY", {"ability": "威嚇"}),
        (
            "烈咬陸鯊使用了\n生命寶珠!",
            "TRIGGER_NOTIFICATION",
            "ITEM",
            {"item": "生命寶珠"},
        ),
        (
            "對手的姆克鷹\n被灼傷了!",
            "BATTLE_TEXT",
            "STATUS",
            {"status": "灼傷", "action": "inflict"},
        ),
        (
            "對手的姆克鷹的\n攻擊大幅提高了!",
            "BATTLE_TEXT",
            "STAT_CHANGE",
            {"stat": "攻擊", "direction": "raise", "magnitude": 2},
        ),
        ("開始下雨了!", "BATTLE_TEXT", "WEATHER", {"weather": "雨", "action": "start"}),
        (
            "電氣場地展開了!",
            "BATTLE_TEXT",
            "TERRAIN",
            {"terrain": "電氣場地", "action": "start"},
        ),
        (
            "對手的烈咬陸鯊的\n滅亡計時變成3了!",
            "BATTLE_TEXT",
            "FIELD_EFFECT",
            {"effect": "滅亡計時", "counter": 3},
        ),
        ("上吧!巨沼怪!", "BATTLE_TEXT", "SWITCH", {"actor": "巨沼怪"}),
        ("對手的風妖精倒下了!", "BATTLE_TEXT", "FAINT", {"target": "風妖精"}),
        ("擊中了要害!", "BATTLE_TEXT", "UNKNOWN_EVENT", {}),
    ],
)
def test_parser_supports_mvp_event_types(text, input_type, event_type, expected):
    result = BattleEventParser().parse(text, input_type)
    assert result.event_type == event_type
    for key, value in expected.items():
        assert result.metadata[key] == value


def test_normalization_is_conservative_and_preserves_line_boundary():
    assert normalize_battle_text(" 烈咬陸鯊 使用了\r\n 生命寶珠！ ") == (
        "烈咬陸鯊使用了\n生命寶珠!"
    )


def test_double_switch_uses_layout_line_as_entity_boundary():
    result = BattleEventParser().parse("上吧!勾魂眼\n巨鉗螳螂!", "BATTLE_TEXT")
    assert result.event_type == "SWITCH"
    assert result.metadata["targets"] == ["勾魂眼", "巨鉗螳螂"]


def test_trigger_only_ability_rule_does_not_guess_battle_text():
    result = BattleEventParser().parse("姆克鷹的\n威嚇", "BATTLE_TEXT")
    assert result.event_type == "UNKNOWN_EVENT"
    assert result.metadata == {"rule_id": "unknown.unmatched"}


def _minimal_record(
    event_id: str,
    workflow_status: str = "auto_accepted",
    human_decision=None,
    merge_with_event_id=None,
) -> Dict[str, Any]:
    return {
        "event_id": event_id,
        "event_type": "BATTLE_TEXT",
        "start_time": 1.0,
        "end_time": 1.2,
        "validation_label": "VALID_TEXT",
        "workflow_status": workflow_status,
        "ocr_text": "烈咬陸鯊使出了\n地震!",
        "ocr_confidence": 0.9,
        "consensus_confidence": 0.9,
        "validation_confidence": 0.9,
        "review_reasons": [],
        "supporting_result_ids": ["r1"],
        "duplicate_group_id": None,
        "possible_duplicate_of": None,
        "duplicate_confidence": 0.0,
        "human_text": None,
        "human_decision": human_decision,
        "human_action": None,
        "merge_with_event_id": merge_with_event_id,
        "split_points": None,
        "reviewed_at": None,
        "reviewed_by": None,
        "review_card_path": "card.jpg",
        "ocr_frame_count": 1,
        "ocr_frame_ordinals": [1],
        "ocr_frame_pts": [1.0],
        "selected_frame_ordinal": 1,
        "selected_variant_id": "original",
        "supporting_frame_ordinals": [1],
    }


def test_acceptance_gate_gives_human_decision_priority():
    assert acceptance_for_record(_minimal_record("a")) == "auto_accepted"
    assert (
        acceptance_for_record(
            _minimal_record("b", workflow_status="needs_review", human_decision="accepted")
        )
        == "human_accepted"
    )
    assert (
        acceptance_for_record(
            _minimal_record("c", human_decision="rejected")
        )
        is None
    )
    with pytest.raises(InputError, match="尚有未完成"):
        acceptance_for_record(_minimal_record("d", workflow_status="needs_review"))


def test_duplicate_is_excluded_and_must_point_to_accepted_record():
    accepted = _minimal_record("battle_text-0001")
    duplicate = _minimal_record(
        "battle_text-0002",
        workflow_status="needs_review",
        human_decision="duplicate",
        merge_with_event_id="battle_text-0001",
    )
    selected = select_accepted_review_records([accepted, duplicate])
    assert [row[0]["event_id"] for row in selected] == ["battle_text-0001"]
    duplicate["merge_with_event_id"] = "missing"
    with pytest.raises(InputError, match="未指向有效"):
        select_accepted_review_records([accepted, duplicate])


def _write_pipeline_project(project: Path, repository_root: Path) -> Path:
    for relative in READ_ONLY_INPUTS.values():
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("read-only fixture\n", encoding="utf-8")
    schemas = project / "schemas"
    schemas.mkdir(parents=True)
    for name in (
        "checkpoint1c_review.schema.json",
        "battle_event.schema.json",
        "checkpoint1d_manifest.schema.json",
    ):
        (schemas / name).write_bytes((repository_root / "schemas" / name).read_bytes())
    records = []
    for index in range(178):
        status = "auto_accepted" if index < 2 else "rejected"
        record = _minimal_record("battle_text-{:04d}".format(index + 1), status)
        record["start_time"] = float(index)
        record["end_time"] = float(index) + 0.2
        record["ocr_frame_ordinals"] = [index]
        record["ocr_frame_pts"] = [float(index)]
        record["selected_frame_ordinal"] = index
        record["supporting_frame_ordinals"] = [index]
        if index == 1:
            record["ocr_text"] = "無法可靠分類的文字。"
        records.append(record)
    review = {
        "schema_version": "0.1.0",
        "checkpoint": "1C",
        "kind": "checkpoint1c_human_review",
        "record_count": 178,
        "source_manifest_sha256": "0" * 64,
        "contact_sheets": {},
        "records": records,
    }
    review_path = project / "outputs/checkpoint-1c-review/checkpoint1c_review.json"
    review_path.parent.mkdir(parents=True)
    review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    return review_path


def test_checkpoint1d_pipeline_writes_schema_valid_traceable_output(tmp_path):
    repository_root = Path(__file__).resolve().parents[2]
    project = tmp_path / "project"
    review_path = _write_pipeline_project(project, repository_root)
    original = review_path.read_bytes()
    manifest = run_checkpoint_1d(
        project_root=project,
        review_path=review_path,
        output_dir=project / "outputs/checkpoint-1d",
    )
    result = json.loads(
        (project / "outputs/checkpoint-1d/battle_events.json").read_text(encoding="utf-8")
    )
    assert manifest["event_count"] == result["event_count"] == 2
    assert result["event_counts"]["MOVE"] == 1
    assert result["event_counts"]["UNKNOWN_EVENT"] == 1
    assert [event["candidate_id"] for event in result["events"]] == [
        "battle_text-0001",
        "battle_text-0002",
    ]
    assert result["events"][1]["raw_text"] == "無法可靠分類的文字。"
    assert review_path.read_bytes() == original
    assert not list((project / "outputs").glob("checkpoint-1d.tmp-*"))
    assert not list((project / "outputs").glob("checkpoint-1d.backup-*"))


def test_checkpoint1d_missing_review_has_clear_error(tmp_path):
    with pytest.raises(InputError, match="review input 不存在"):
        run_checkpoint_1d(tmp_path, tmp_path / "missing.json", tmp_path / "outputs/x")
