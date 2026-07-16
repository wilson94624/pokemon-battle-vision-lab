import json
from dataclasses import replace
from pathlib import Path

import numpy as np
from jsonschema import validate

from pokemon_battle_vision.checkpoint1c_frame_selection import select_ocr_frames
from pokemon_battle_vision.checkpoint1c import _validate_inputs
from pokemon_battle_vision.checkpoint1c_evaluation import evaluate_initial_fixture
from pokemon_battle_vision.checkpoint1c_models import (
    OcrAggregate,
    OcrRawResult,
    TextValidationRecord,
)
from pokemon_battle_vision.duplicate_detection import mark_possible_duplicates
from pokemon_battle_vision.errors import InputError
from pokemon_battle_vision.models import FrameTimestampIndex
from pokemon_battle_vision.ocr_aggregation import (
    aggregate_candidate_results,
    text_similarity,
)
from pokemon_battle_vision.ocr_normalization import (
    cjk_character_count,
    normalize_ocr_text,
)
from pokemon_battle_vision.ocr_preprocessing import build_preprocessing_variants
from pokemon_battle_vision.output_transaction import OutputTransaction
from pokemon_battle_vision.text_validation import validate_candidate_text
from pokemon_battle_vision.utils import sha256_file


ROOT = Path(__file__).resolve().parents[2]


def _index(count=8):
    pts = np.arange(count, dtype=np.float64) / 10.0
    return FrameTimestampIndex(
        pts_sec=pts,
        duration_sec=np.full(count, 0.1),
        key_frame=np.zeros(count, dtype=np.bool_),
        validation={"complete": True},
        video_sha256="v" * 64,
        ffprobe_version="8.1.2",
    )


def _event(event_type="BATTLE_TEXT", event_id="battle_text-0001"):
    visible = ["battle_text"]
    if event_type == "TRIGGER_NOTIFICATION":
        visible = ["opponent_trigger_notification"]
    return {
        "event_id": event_id,
        "type": event_type,
        "start_frame": 0,
        "end_frame": 7,
        "start_time": 0.0,
        "end_time": 0.7,
        "visible_rois": visible,
    }


def _frame(index, level="strong", score=0.8, structure=0.8, event_type="BATTLE_TEXT"):
    row = {
        "frame_index": index,
        "pts": index / 10.0,
        "candidate_scores": {event_type: score},
        "battle_text_evidence": {
            "evidence_level": level,
            "text_line_strength": structure,
        },
        "trigger_notification_evidence": {"sides": {}},
    }
    if event_type == "TRIGGER_NOTIFICATION":
        row["trigger_notification_evidence"]["sides"]["opponent"] = {
            "evidence_level": level,
            "combined_score": score,
            "text_score": structure,
        }
    return row


def _raw(
    result_id,
    frame,
    text,
    confidence=0.9,
    event_id="battle_text-0001",
    event_type="BATTLE_TEXT",
    visual=0.8,
    variant="color_upscale",
    template=0.0,
):
    normalized = normalize_ocr_text(text)
    return {
        "result_id": result_id,
        "event_id": event_id,
        "event_type": event_type,
        "frame_ordinal": frame,
        "pts": frame / 10.0,
        "roi_name": "battle_text",
        "variant_id": variant,
        "variant_operations": ["upscale"],
        "image_path": "variants/example.png",
        "raw_text": text,
        "normalized_text": normalized,
        "ocr_confidence": confidence,
        "character_count": len(normalized),
        "cjk_character_count": cjk_character_count(normalized),
        "line_count": len(normalized.splitlines()) if normalized else 0,
        "engine": "apple_vision_vnrecognizetextrequest",
        "engine_revision": "VNRecognizeTextRequestRevision3",
        "language": "zh-Hant",
        "frame_quality": 0.9,
        "variant_quality": 1.0,
        "visual_text_strength": visual,
        "detector_template_strength": template,
        "error": None,
    }


def _aggregate(text="對手的風妖精擺出了\n幫助仙子伊布的架勢！", confidence=0.9, consensus=0.85):
    return OcrAggregate(
        event_id="battle_text-0001",
        event_type="BATTLE_TEXT",
        best_text=text,
        best_confidence=confidence,
        consensus_confidence=consensus,
        supporting_result_ids=["r1", "r2"],
        supporting_frame_ordinals=[1, 2],
        disagreement_score=0.1,
        selected_frame_ordinal=1,
        selected_variant_id="color_upscale",
        candidate_status="AGGREGATED",
        review_reasons=[],
        nonempty_result_count=2,
        distinct_text_count=1,
        cjk_character_count=cjk_character_count(text),
        line_count=2,
    )


def test_multiframe_selection_is_deterministic_and_deduplicates_ordinals():
    event = _event()
    rows = [
        _frame(index, level="strong" if 1 <= index <= 6 else "negative", score=0.5 + index / 20)
        for index in range(8)
    ]
    review = {
        "evidence_frames": [
            {"frame_index": 1, "roles": ["first_strong_positive"]},
            {"frame_index": 4, "roles": ["peak_score_structure", "evidence_strip"]},
            {"frame_index": 6, "roles": ["last_strong_positive"]},
        ]
    }
    first = select_ocr_frames(event, review, rows, _index())
    second = select_ocr_frames(event, review, rows, _index())
    assert [row.to_dict() for row in first] == [row.to_dict() for row in second]
    assert len({row.frame_ordinal for row in first}) == len(first)
    assert 3 <= len(first) <= 7
    reasons = {reason for row in first for reason in row.selection_reasons}
    assert {"first_strong_positive", "peak_score_structure", "last_strong_positive"} <= reasons


def test_trigger_selection_uses_analysis_context_and_peak_neighbors():
    event = _event("TRIGGER_NOTIFICATION", "trigger_notification-0001")
    rows = [
        _frame(index, score=0.5 + index / 20, event_type="TRIGGER_NOTIFICATION")
        for index in range(8)
    ]
    selected = select_ocr_frames(event, {"evidence_frames": []}, rows, _index())
    assert all(row.roi_name == "opponent_trigger_notification_analysis_context" for row in selected)
    reasons = {reason for row in selected for reason in row.selection_reasons}
    assert "trigger_peak_evidence" in reasons
    assert "before_peak" in reasons


def test_preprocessing_variants_are_bounded_and_deterministic():
    image = np.full((80, 260, 3), (35, 35, 35), dtype=np.uint8)
    image[20:30, 80:180] = 235
    first = build_preprocessing_variants(image, "BATTLE_TEXT")
    second = build_preprocessing_variants(image, "BATTLE_TEXT")
    assert [row[0] for row in first] == [
        "color_upscale",
        "clahe_sharpen",
        "white_text_mask",
        "adaptive_binary",
    ]
    assert len(first) == 4
    assert all(np.array_equal(left[3], right[3]) for left, right in zip(first, second))
    trigger = build_preprocessing_variants(image, "TRIGGER_NOTIFICATION")
    assert "dark_panel_normalization" in trigger[1][1]
    assert "dark_panel_normalization" not in first[1][1]
    assert not np.array_equal(trigger[1][3], first[1][3])


def test_unicode_normalization_and_cjk_count_are_correct():
    assert normalize_ocr_text("  Ａ Ｂ　姆 克 鷹\r\n 威 嚇  ") == "AB姆克鷹\n威嚇"
    assert cjk_character_count("AB姆克鷹\n威嚇！") == 5


def test_raw_ocr_result_schema_accepts_traceable_record():
    schema = json.loads((ROOT / "schemas/ocr_raw_result.schema.json").read_text())
    validate(_raw("r1", 1, "姆克鷹的\n威嚇"), schema)


def test_aggregate_and_text_validation_schemas_accept_models():
    aggregate_schema = json.loads(
        (ROOT / "schemas/ocr_aggregate.schema.json").read_text()
    )
    aggregate_payload = {
        "schema_version": "0.1.0",
        "checkpoint": "1C",
        "kind": "multi_frame_ocr_aggregates",
        "record_count": 1,
        "records": [_aggregate().to_dict()],
    }
    validate(aggregate_payload, aggregate_schema)
    validation_schema = json.loads(
        (ROOT / "schemas/text_validation.schema.json").read_text()
    )
    validation_payload = {
        "schema_version": "0.1.0",
        "checkpoint": "1C",
        "kind": "text_validation_results",
        "record_count": 1,
        "validation_counts": {"VALID_TEXT": 1, "NO_TEXT": 0, "UNCERTAIN": 0},
        "workflow_counts": {
            "auto_accepted": 1,
            "needs_review": 0,
            "rejected": 0,
        },
        "records": [_validation("battle_text-0001", 1.0, 2.0, "求雨").to_dict()],
    }
    validate(validation_payload, validation_schema)


def test_checkpoint_manifest_schema_accepts_contract():
    schema = json.loads(
        (ROOT / "schemas/checkpoint1c_manifest.schema.json").read_text()
    )
    payload = {
        "schema_version": "0.1.0",
        "checkpoint": "1C",
        "kind": "checkpoint1c_manifest",
        "status": "complete",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "ocr_engine": {},
        "input_candidate_counts": {},
        "processed_candidate_counts": {
            "BATTLE_TEXT": 176,
            "TRIGGER_NOTIFICATION": 2,
        },
        "processed_candidate_count": 178,
        "raw_result_count": 178,
        "validation_counts": {},
        "workflow_counts": {},
        "duplicate_group_count": 0,
        "initial_evaluation_fixture": {
            "path": "references/checkpoint1c_initial_evaluation.json",
            "sha256": "a" * 64,
            "production_usage_forbidden": True,
            "inference_feedback_used": False,
        },
        "frame_extraction": {},
        "outputs": {},
        "frozen_hashes_before": {},
        "frozen_hashes_after": {},
        "frozen_inputs_unchanged": True,
        "detector_rerun": False,
        "semantic_parser_performed": False,
        "cloud_or_llm_vision_used": False,
        "source_candidates_deleted": False,
        "validation": {},
    }
    validate(payload, schema)


def test_multiframe_agreement_increases_consensus_and_uses_distinct_frames():
    one = aggregate_candidate_results("battle_text-0001", "BATTLE_TEXT", [_raw("r1", 1, "求雨")], 3)
    many = aggregate_candidate_results(
        "battle_text-0001",
        "BATTLE_TEXT",
        [_raw("r1", 1, "求雨"), _raw("r2", 2, "求雨"), _raw("r3", 3, "求雨")],
        3,
    )
    assert many.consensus_confidence > one.consensus_confidence
    assert many.supporting_frame_ordinals == [1, 2, 3]


def test_multiframe_disagreement_becomes_uncertain():
    raw = [
        _raw("r1", 1, "求雨", confidence=0.75),
        _raw("r2", 2, "巨鉗螳螂使用了子彈拳", confidence=0.75),
        _raw("r3", 3, "完全不同訊息", confidence=0.75),
    ]
    aggregate = aggregate_candidate_results("battle_text-0001", "BATTLE_TEXT", raw, 3)
    validation = validate_candidate_text(_event(), aggregate, raw)
    assert validation.validation_label == "UNCERTAIN"
    assert validation.workflow_status == "needs_review"


def test_multiple_empty_frames_can_be_no_text_but_are_preserved():
    raw = []
    for frame in (1, 2):
        raw.extend(
            [
                _raw("r{}a".format(frame), frame, "", confidence=0.0, visual=0.2),
                _raw("r{}b".format(frame), frame, "", confidence=0.0, visual=0.2, variant="clahe_sharpen"),
            ]
        )
    aggregate = aggregate_candidate_results("battle_text-0001", "BATTLE_TEXT", raw, 2)
    result = validate_candidate_text(_event(), aggregate, raw)
    assert result.validation_label == "NO_TEXT"
    assert result.workflow_status == "rejected"
    assert result.supporting_result_ids == []
    assert all(row["event_id"] == result.event_id for row in raw)


def test_visible_text_structure_with_ocr_failure_needs_review():
    raw = []
    for frame in (1, 2):
        for variant in ("color_upscale", "clahe_sharpen", "white_text_mask", "adaptive_binary"):
            raw.append(
                _raw(
                    "r{}{}".format(frame, variant),
                    frame,
                    "",
                    confidence=0.0,
                    visual=0.95,
                    variant=variant,
                    template=0.8,
                )
            )
    aggregate = aggregate_candidate_results("battle_text-0001", "BATTLE_TEXT", raw, 2)
    result = validate_candidate_text(_event(), aggregate, raw)
    assert result.validation_label == "UNCERTAIN"
    assert result.workflow_status == "needs_review"
    assert "partial_text" in result.review_reasons


def test_low_quality_noise_text_is_never_auto_accepted():
    raw = []
    for frame in (1, 2):
        raw.extend(
            [
                _raw("r{}a".format(frame), frame, "...", confidence=0.3, visual=0.8),
                _raw("r{}b".format(frame), frame, ")", confidence=0.3, visual=0.8, variant="clahe_sharpen"),
            ]
        )
    aggregate = aggregate_candidate_results("battle_text-0001", "BATTLE_TEXT", raw, 2)
    result = validate_candidate_text(_event(), aggregate, raw)
    assert result.validation_label == "NO_TEXT"
    assert result.workflow_status == "rejected"
    assert result.validation_confidence > 0.8


def test_numeric_status_ui_with_short_cjk_label_is_not_battle_text():
    raw = []
    for frame in (1, 2):
        for variant in ("color_upscale", "clahe_sharpen"):
            raw.append(
                _raw(
                    "r{}{}".format(frame, variant),
                    frame,
                    "06:05\n超級進化",
                    confidence=0.8,
                    visual=0.8,
                    variant=variant,
                )
            )
    aggregate = aggregate_candidate_results("battle_text-0001", "BATTLE_TEXT", raw, 2)
    result = validate_candidate_text(_event(), aggregate, raw)
    assert result.validation_label == "NO_TEXT"
    assert "implausible_line_structure" in result.review_reasons


def test_trigger_positive_is_never_high_confidence_no_text():
    event = _event("TRIGGER_NOTIFICATION", "trigger_notification-0001")
    raw = [
        _raw("r1", 1, "", event_id=event["event_id"], event_type=event["type"], visual=0.1),
        _raw("r2", 2, "", event_id=event["event_id"], event_type=event["type"], visual=0.1, variant="clahe_sharpen"),
    ]
    aggregate = aggregate_candidate_results(event["event_id"], event["type"], raw, 2)
    result = validate_candidate_text(event, aggregate, raw)
    assert result.validation_label == "UNCERTAIN"
    assert result.workflow_status == "needs_review"


def test_engine_error_is_preserved_for_human_review():
    raw = [_raw("r1", 1, "", confidence=0.0, template=0.8)]
    raw[0]["error"] = "injected engine failure"
    aggregate = aggregate_candidate_results("battle_text-0001", "BATTLE_TEXT", raw, 1)
    result = validate_candidate_text(_event(), aggregate, raw)
    assert result.validation_label == "UNCERTAIN"
    assert result.workflow_status == "needs_review"
    assert "engine_error" in result.review_reasons


def _validation(event_id, start, end, text):
    return TextValidationRecord(
        event_id=event_id,
        event_type="BATTLE_TEXT",
        start_time=start,
        end_time=end,
        validation_label="VALID_TEXT",
        workflow_status="auto_accepted",
        ocr_text=text,
        ocr_confidence=0.95,
        consensus_confidence=0.9,
        validation_confidence=0.92,
        review_reasons=[],
        supporting_result_ids=["r"],
    )


def test_possible_duplicate_is_marked_without_automatic_merge():
    rows = [
        _validation("battle_text-0001", 1.0, 2.0, "巨鉗螳螂使用了子彈拳"),
        _validation("battle_text-0002", 2.1, 3.0, "巨鉗螳螂使用了子彈拳！"),
    ]
    updated, groups = mark_possible_duplicates(rows)
    assert len(groups) == 1
    assert groups[0]["automatic_merge_performed"] is False
    assert updated[1].possible_duplicate_of == "battle_text-0001"
    assert all(row.workflow_status == "needs_review" for row in updated)
    assert len(updated) == 2


def test_no_text_noise_is_not_marked_as_duplicate():
    rows = [
        replace(
            _validation("battle_text-0001", 1.0, 2.0, "•"),
            validation_label="NO_TEXT",
            workflow_status="rejected",
        ),
        replace(
            _validation("battle_text-0002", 2.1, 3.0, "•"),
            validation_label="NO_TEXT",
            workflow_status="rejected",
        ),
    ]
    updated, groups = mark_possible_duplicates(rows)
    assert groups == []
    assert all(row.workflow_status == "rejected" for row in updated)
    assert all(row.duplicate_group_id is None for row in updated)


def test_human_fields_default_to_null():
    row = _validation("battle_text-0001", 1.0, 2.0, "求雨")
    assert row.human_text is None
    assert row.human_decision is None
    assert row.human_action is None
    assert row.merge_with_event_id is None
    assert row.split_points is None
    assert row.reviewed_at is None
    assert row.reviewed_by is None


def test_checkpoint1c_output_transaction_uses_visible_staging_and_rolls_back(tmp_path):
    project = tmp_path / "project"
    target = project / "outputs/checkpoint-1c"
    target.mkdir(parents=True)
    (target / "old.txt").write_text("old")
    try:
        with OutputTransaction(project, target) as transaction:
            assert not transaction.staging_dir.name.startswith(".")
            (transaction.staging_dir / "new.txt").write_text("new")
            raise RuntimeError("injected failure")
    except RuntimeError:
        pass
    assert (target / "old.txt").read_text() == "old"
    assert not list((project / "outputs").glob("*tmp-*"))
    assert not list((project / "outputs").glob("*backup-*"))
    assert OutputTransaction.hidden_items(target) == []


def test_similarity_uses_text_not_candidate_ids_or_timestamps():
    assert text_similarity("烈咬陸鯊使用了\n生命寶珠！", "烈咬陸鯊使用了生命寶珠") > 0.9
    assert text_similarity("求雨", "子彈拳") < 0.5


def test_initial_fixture_is_evaluated_only_against_finished_validations():
    fixture = {
        "production_usage_forbidden": True,
        "cases": [
            {
                "case_id": "clear",
                "candidate_ids": ["battle_text-0001"],
                "expected_labels": ["VALID_TEXT"],
                "expected_text_fragments": ["求雨"],
                "diagnostic_category": "clear_text",
            }
        ],
    }
    finished = [_validation("battle_text-0001", 1.0, 2.0, "求雨").to_dict()]
    report = evaluate_initial_fixture(fixture, finished)
    assert report["passed_count"] == 1
    assert report["failed_count"] == 0
    assert report["inference_feedback_used"] is False


def test_initial_fixture_cannot_be_declared_for_production_usage():
    try:
        evaluate_initial_fixture(
            {"production_usage_forbidden": False, "cases": []}, []
        )
    except ValueError as exc:
        assert "禁止 production usage" in str(exc)
    else:
        raise AssertionError("evaluation fixture 應拒絕 production usage")


def test_inference_modules_do_not_read_human_evaluation_fixture():
    inference_files = [
        "checkpoint1c_frame_selection.py",
        "ocr_preprocessing.py",
        "ocr_engine.py",
        "ocr_aggregation.py",
        "text_validation.py",
        "duplicate_detection.py",
    ]
    source_root = ROOT / "src/pokemon_battle_vision"
    for name in inference_files:
        source = (source_root / name).read_text(encoding="utf-8")
        assert "checkpoint1c_initial_evaluation.json" not in source


def test_checkpoint1c_missing_inputs_have_clear_error(tmp_path):
    try:
        _validate_inputs(
            tmp_path,
            tmp_path / "missing.mp4",
            tmp_path / "checkpoint-1b",
            tmp_path / "checkpoint-1b-review",
        )
    except InputError as exc:
        assert "Checkpoint 1C 輸入不存在" in str(exc)
        assert "missing.mp4" in str(exc)
    else:
        raise AssertionError("缺少輸入時應停止")
