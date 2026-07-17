"""Checkpoint 1C 第一階段 orchestration：本機 OCR、驗證與 Review Pack。"""

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, DefaultDict, Dict, List, Mapping, Optional, Sequence, Tuple

from jsonschema import Draft202012Validator

from .checkpoint1c_extractor import extract_checkpoint1c_frames
from .checkpoint1c_evaluation import evaluate_initial_fixture
from .checkpoint1c_frame_selection import SUPPORTED_OCR_TYPES, select_ocr_frames
from .checkpoint1c_models import OcrFrameSelection, OcrRawResult
from .checkpoint1c_review import (
    build_classification_contact_sheets,
    build_review_record,
    render_review_card,
)
from .config import load_json, load_roi_config
from .duplicate_detection import mark_possible_duplicates
from .errors import InputError
from .ocr_aggregation import aggregate_candidate_results
from .ocr_engine import (
    APPLE_VISION_ENGINE,
    APPLE_VISION_LANGUAGE,
    APPLE_VISION_REVISION,
    AppleVisionOcrEngine,
)
from .ocr_normalization import cjk_character_count, line_count, normalize_ocr_text
from .output_transaction import OutputTransaction, finalize_generated_output
from .review_frame_extractor import load_frame_records
from .roi import pixel_rois
from .scanner import load_frame_timestamp_index
from .text_validation import validate_candidate_text
from .trigger_notification_features import trigger_analysis_rois
from .utils import project_relative, sha256_file, write_json
from .replay import DEFAULT_REPLAY_ID, normalize_replay_id, resolve_project_path


EXPECTED_CHECKPOINT1B_COUNTS = {
    "BATTLE_TEXT": 176,
    "TRIGGER_NOTIFICATION": 2,
    "MOVE_MENU": 31,
    "TEAM_PREVIEW": 1,
    "SELECTED_FOUR": 1,
    "RESULT": 1,
}

FROZEN_INPUTS = {
    "events": "outputs/checkpoint-1b/events.json",
    "frames": "outputs/checkpoint-1b/frames.jsonl",
    "roi_config": "configs/roi_2868x1320.json",
    "roi_approval": "outputs/checkpoint-1a/roi_approval.json",
    "battle_text_detector": "src/pokemon_battle_vision/battle_text_detection.py",
    "battle_text_timeline": "src/pokemon_battle_vision/battle_text_timeline.py",
    "trigger_detector": "src/pokemon_battle_vision/trigger_notification_detection.py",
    "trigger_timeline": "src/pokemon_battle_vision/trigger_notification_timeline.py",
    "checkpoint1b_review": "outputs/checkpoint-1b-review/candidate_review.json",
    "checkpoint1b_review_manifest": "outputs/checkpoint-1b-review/review_manifest.json",
}


def _frozen_inputs(
    project_root: Path,
    checkpoint1a_dir: Path,
    checkpoint1b_dir: Path,
    checkpoint1b_review_dir: Path,
    roi_config_path: Path,
) -> Dict[str, str]:
    root = project_root.resolve()
    paths = {
        "events": checkpoint1b_dir / "events.json",
        "frames": checkpoint1b_dir / "frames.jsonl",
        "roi_config": roi_config_path,
        "roi_approval": checkpoint1a_dir / "roi_approval.json",
        "battle_text_detector": root / "src/pokemon_battle_vision/battle_text_detection.py",
        "battle_text_timeline": root / "src/pokemon_battle_vision/battle_text_timeline.py",
        "trigger_detector": root / "src/pokemon_battle_vision/trigger_notification_detection.py",
        "trigger_timeline": root / "src/pokemon_battle_vision/trigger_notification_timeline.py",
        "checkpoint1b_review": checkpoint1b_review_dir / "candidate_review.json",
        "checkpoint1b_review_manifest": checkpoint1b_review_dir / "review_manifest.json",
    }
    return {name: project_relative(path.resolve(), root) for name, path in paths.items()}


def _frozen_hashes(
    project_root: Path, frozen_inputs: Optional[Mapping[str, str]] = None
) -> Dict[str, Dict[str, str]]:
    rows = {}
    for name, relative in (frozen_inputs or FROZEN_INPUTS).items():
        path = project_root / relative
        if not path.is_file():
            raise InputError("Checkpoint 1C frozen input 不存在：{}".format(path))
        rows[name] = {"path": relative, "sha256": sha256_file(path)}
    return rows


def _schema_validator(project_root: Path, name: str) -> Draft202012Validator:
    path = project_root / "schemas" / name
    schema = load_json(path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _validate_inputs(
    project_root: Path,
    video_path: Path,
    checkpoint1b_dir: Path,
    checkpoint1b_review_dir: Path,
    checkpoint1a_dir: Optional[Path] = None,
    roi_config_path: Optional[Path] = None,
    replay_id: str = DEFAULT_REPLAY_ID,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
    checkpoint1a_dir = (checkpoint1a_dir or (project_root / "outputs/checkpoint-1a")).resolve()
    roi_config_path = (roi_config_path or (project_root / "configs/roi_2868x1320.json")).resolve()
    events_path = checkpoint1b_dir / "events.json"
    frames_path = checkpoint1b_dir / "frames.jsonl"
    review_path = checkpoint1b_review_dir / "candidate_review.json"
    for path in (video_path, events_path, frames_path, review_path):
        if not path.is_file():
            raise InputError("Checkpoint 1C 輸入不存在：{}".format(path))
    events_payload = load_json(events_path)
    if int(events_payload.get("event_count", -1)) != len(events_payload.get("events", [])):
        raise InputError("Checkpoint 1B events event_count 與 events 長度不一致")
    if events_payload.get("ocr_performed") is not False:
        raise InputError("Checkpoint 1B events contract 無效")
    if replay_id == DEFAULT_REPLAY_ID:
        if events_payload.get("event_counts") != EXPECTED_CHECKPOINT1B_COUNTS:
            raise InputError(
                "Checkpoint 1B candidate counts 不是 frozen baseline：{}".format(
                    events_payload.get("event_counts")
                )
            )
        if events_payload.get("event_count") != 212:
            raise InputError("Checkpoint 1B events contract 無效")
    review_payload = load_json(review_path)
    if review_payload.get("source_events_sha256") != sha256_file(events_path):
        raise InputError("Checkpoint 1B Review Pack 與 events.json hash 不一致")
    review_records = list(review_payload.get("records", []))
    if len(review_records) != int(events_payload.get("event_count", -1)):
        raise InputError("Checkpoint 1B Review Pack record_count 與 events 不一致")
    frames = load_frame_records(frames_path)
    if not frames:
        raise InputError("Checkpoint 1B frames.jsonl 不可為空")
    if replay_id == DEFAULT_REPLAY_ID and len(frames) != 5918:
        raise InputError("Checkpoint 1B frames.jsonl 應有 5,918 records")
    approval = load_json(checkpoint1a_dir / "roi_approval.json")
    if approval.get("roi_config_sha256") != sha256_file(roi_config_path):
        raise InputError("ROI approval 與 frozen ROI config hash 不一致")
    if approval.get("video_sha256") != events_payload.get("video_sha256"):
        raise InputError("ROI approval 與 Checkpoint 1B video hash 不一致")
    if sha256_file(video_path) != events_payload.get("video_sha256"):
        raise InputError("Checkpoint 1C video hash 與 Checkpoint 1B 不一致")
    return events_payload, frames, review_payload, approval


def _build_selections(
    events: Sequence[Mapping[str, Any]],
    review_records: Sequence[Mapping[str, Any]],
    frame_records: Sequence[Mapping[str, Any]],
    timestamp_index,
) -> Tuple[List[OcrFrameSelection], Dict[str, List[OcrFrameSelection]]]:
    review_by_id = {str(row["candidate_id"]): row for row in review_records}
    all_rows: List[OcrFrameSelection] = []
    by_event: Dict[str, List[OcrFrameSelection]] = {}
    for event in events:
        if str(event["type"]) not in SUPPORTED_OCR_TYPES:
            continue
        event_id = str(event["event_id"])
        if event_id not in review_by_id:
            raise InputError("Checkpoint 1C 找不到 Review evidence：{}".format(event_id))
        rows = select_ocr_frames(
            event, review_by_id[event_id], frame_records, timestamp_index
        )
        if not rows:
            raise InputError("Checkpoint 1C 沒有選到 OCR frame：{}".format(event_id))
        if len({row.frame_ordinal for row in rows}) != len(rows):
            raise InputError("Checkpoint 1C frame ordinal 未去重：{}".format(event_id))
        by_event[event_id] = rows
        all_rows.extend(rows)
    if len(by_event) != sum(
        str(event["type"]) in SUPPORTED_OCR_TYPES for event in events
    ):
        raise InputError("Checkpoint 1C OCR candidate selection 不完整")
    return all_rows, by_event


def _build_raw_results(
    engine,
    selections: Sequence[OcrFrameSelection],
    variants_by_frame_key,
    output_staging: Path,
) -> List[OcrRawResult]:
    jobs = []
    metadata = {}
    for selection in selections:
        frame_key = "{}:{:06d}".format(selection.event_id, selection.frame_ordinal)
        for variant in variants_by_frame_key[frame_key]:
            result_id = "{}__f{:06d}__{}".format(
                selection.event_id, selection.frame_ordinal, variant.variant_id
            )
            jobs.append(
                {
                    "job_id": result_id,
                    "image_path": str(output_staging / variant.image_path),
                }
            )
            metadata[result_id] = (selection, variant)
    engine_results = engine.recognize(jobs)
    if len(engine_results) != len(jobs):
        raise InputError("OCR engine result count 與 jobs 不一致")
    raw_results = []
    for engine_result in engine_results:
        if engine_result.job_id not in metadata:
            raise InputError("OCR engine 回傳未知 job_id：{}".format(engine_result.job_id))
        selection, variant = metadata[engine_result.job_id]
        normalized = normalize_ocr_text(engine_result.raw_text)
        raw_results.append(
            OcrRawResult(
                result_id=engine_result.job_id,
                event_id=selection.event_id,
                event_type=selection.event_type,
                frame_ordinal=selection.frame_ordinal,
                pts=selection.pts,
                roi_name=selection.roi_name,
                variant_id=variant.variant_id,
                variant_operations=variant.operations,
                image_path=variant.image_path,
                raw_text=engine_result.raw_text,
                normalized_text=normalized,
                ocr_confidence=engine_result.confidence,
                character_count=len(normalized),
                cjk_character_count=cjk_character_count(normalized),
                line_count=line_count(normalized),
                engine=APPLE_VISION_ENGINE,
                engine_revision=APPLE_VISION_REVISION,
                language=APPLE_VISION_LANGUAGE,
                frame_quality=selection.frame_quality,
                variant_quality=variant.quality_weight,
                visual_text_strength=selection.visual_text_strength,
                detector_template_strength=selection.detector_template_strength,
                error=engine_result.error,
            )
        )
    return raw_results


def _output_hash(path: Path, root: Path) -> Dict[str, str]:
    return {"path": path.relative_to(root).as_posix(), "sha256": sha256_file(path)}


def run_checkpoint_1c(
    project_root: Path,
    video_path: Path,
    checkpoint1b_dir: Path,
    checkpoint1b_review_dir: Path,
    output_dir: Path,
    review_output_dir: Path,
    ocr_engine=None,
    checkpoint1a_dir: Optional[Path] = None,
    roi_config_path: Optional[Path] = None,
    replay_id: str = DEFAULT_REPLAY_ID,
) -> Dict[str, Any]:
    project_root = project_root.resolve()
    video_path = resolve_project_path(project_root, video_path)
    checkpoint1b_dir = resolve_project_path(project_root, checkpoint1b_dir)
    checkpoint1b_review_dir = resolve_project_path(project_root, checkpoint1b_review_dir)
    checkpoint1a_dir = resolve_project_path(
        project_root, checkpoint1a_dir or (project_root / "outputs/checkpoint-1a")
    )
    roi_config_path = resolve_project_path(
        project_root, roi_config_path or (project_root / "configs/roi_2868x1320.json")
    )
    replay_id = normalize_replay_id(replay_id)
    output_dir = resolve_project_path(project_root, output_dir)
    review_output_dir = resolve_project_path(project_root, review_output_dir)
    if output_dir == review_output_dir:
        raise InputError("Checkpoint 1C data output 與 review output 不可相同")
    frozen_inputs = _frozen_inputs(
        project_root, checkpoint1a_dir, checkpoint1b_dir, checkpoint1b_review_dir, roi_config_path
    )
    frozen_before = _frozen_hashes(project_root, frozen_inputs)
    events_payload, frame_records, review_payload, _ = _validate_inputs(
        project_root, video_path, checkpoint1b_dir, checkpoint1b_review_dir,
        checkpoint1a_dir, roi_config_path,
        replay_id,
    )
    metadata = load_json(checkpoint1a_dir / "metadata.json")
    timestamp_index = load_frame_timestamp_index(
        checkpoint1a_dir / "frame_timestamps.npz",
        str(events_payload["video_sha256"]),
    )
    _, normalized_rois = load_roi_config(roi_config_path)
    display_width = int(metadata["display_dimensions"]["width"])
    display_height = int(metadata["display_dimensions"]["height"])
    pixels = pixel_rois(normalized_rois, display_width, display_height)
    pixels.update(trigger_analysis_rois(pixels, display_width, display_height))
    source_events = list(events_payload["events"])
    target_events = [
        event for event in source_events if str(event["type"]) in SUPPORTED_OCR_TYPES
    ]
    selections, selections_by_event = _build_selections(
        source_events, review_payload["records"], frame_records, timestamp_index
    )
    engine = ocr_engine or AppleVisionOcrEngine()
    engine_probe = engine.probe()

    with OutputTransaction(project_root, output_dir) as output_transaction:
        with OutputTransaction(project_root, review_output_dir) as review_transaction:
            variants_by_frame_key, full_frame_paths, extraction_report = (
                extract_checkpoint1c_frames(
                    video_path=video_path,
                    metadata=metadata,
                    timestamp_index=timestamp_index,
                    pixel_rois=pixels,
                    selections=selections,
                    output_staging=output_transaction.staging_dir,
                    review_staging=review_transaction.staging_dir,
                )
            )
            selection_payload = {
                "schema_version": "0.1.0",
                "checkpoint": "1C",
                "kind": "ocr_frame_selections",
                "candidate_count": len(selections_by_event),
                "selection_count": len(selections),
                "records": [row.to_dict() for row in selections],
            }
            write_json(
                output_transaction.staging_dir / "ocr_frame_selections.json",
                selection_payload,
            )
            raw_models = _build_raw_results(
                engine, selections, variants_by_frame_key, output_transaction.staging_dir
            )
            raw_rows = [row.to_dict() for row in raw_models]
            raw_path = output_transaction.staging_dir / "ocr_raw_results.jsonl"
            _write_jsonl(raw_path, raw_rows)
            raw_by_event: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)
            for row in raw_rows:
                raw_by_event[str(row["event_id"])].append(row)
            events_by_id = {str(event["event_id"]): event for event in target_events}
            aggregates = []
            validations = []
            for event in target_events:
                event_id = str(event["event_id"])
                aggregate = aggregate_candidate_results(
                    event_id,
                    str(event["type"]),
                    raw_by_event[event_id],
                    len(selections_by_event[event_id]),
                )
                aggregates.append(aggregate)
                validations.append(
                    validate_candidate_text(event, aggregate, raw_by_event[event_id])
                )
            validations, duplicate_groups = mark_possible_duplicates(validations)
            aggregate_rows = [row.to_dict() for row in aggregates]
            validation_rows = [row.to_dict() for row in validations]
            validation_counts = dict(Counter(row.validation_label for row in validations))
            workflow_counts = dict(Counter(row.workflow_status for row in validations))
            for label in ("VALID_TEXT", "NO_TEXT", "UNCERTAIN"):
                validation_counts.setdefault(label, 0)
            for status in ("auto_accepted", "needs_review", "rejected"):
                workflow_counts.setdefault(status, 0)
            aggregate_payload = {
                "schema_version": "0.1.0",
                "checkpoint": "1C",
                "kind": "multi_frame_ocr_aggregates",
                "record_count": len(aggregate_rows),
                "records": aggregate_rows,
            }
            validation_payload = {
                "schema_version": "0.1.0",
                "checkpoint": "1C",
                "kind": "text_validation_results",
                "record_count": len(validation_rows),
                "validation_counts": validation_counts,
                "workflow_counts": workflow_counts,
                "records": validation_rows,
            }
            aggregate_path = output_transaction.staging_dir / "ocr_aggregates.json"
            validation_path = output_transaction.staging_dir / "text_validations.json"
            duplicate_path = output_transaction.staging_dir / "duplicate_groups.json"
            write_json(aggregate_path, aggregate_payload)
            write_json(validation_path, validation_payload)
            write_json(
                duplicate_path,
                {
                    "schema_version": "0.1.0",
                    "kind": "possible_duplicate_groups",
                    "automatic_merge_performed": False,
                    "group_count": len(duplicate_groups),
                    "groups": duplicate_groups,
                },
            )
            # 人工 fixture 只在所有 inference 已完成後比較，絕不回饋 validation 結果。
            evaluation_fixture_path = (
                project_root / "references/checkpoint1c_initial_evaluation.json"
            )
            evaluation_payload = evaluate_initial_fixture(
                load_json(evaluation_fixture_path), validation_rows
            )
            evaluation_path = output_transaction.staging_dir / "initial_evaluation_report.json"
            write_json(evaluation_path, evaluation_payload)

            _schema_validator(project_root, "ocr_frame_selection.schema.json").validate(
                selection_payload
            )
            raw_validator = _schema_validator(project_root, "ocr_raw_result.schema.json")
            for row in raw_rows:
                raw_validator.validate(row)
            _schema_validator(project_root, "ocr_aggregate.schema.json").validate(
                aggregate_payload
            )
            _schema_validator(project_root, "text_validation.schema.json").validate(
                validation_payload
            )
            _schema_validator(project_root, "checkpoint1c_evaluation.schema.json").validate(
                evaluation_payload
            )

            frozen_after = _frozen_hashes(project_root, frozen_inputs)
            if frozen_after != frozen_before:
                raise InputError("Checkpoint 1C 執行期間 frozen Checkpoint 1B inputs 發生變更")
            processed_counts = dict(Counter(str(event["type"]) for event in target_events))
            manifest = {
                "schema_version": "0.1.0",
                "checkpoint": "1C",
                "kind": "checkpoint1c_manifest",
                "status": "complete",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "ocr_engine": engine_probe,
                "input_candidate_counts": events_payload["event_counts"],
                "processed_candidate_counts": processed_counts,
                "processed_candidate_count": len(target_events),
                "raw_result_count": len(raw_rows),
                "validation_counts": validation_counts,
                "workflow_counts": workflow_counts,
                "duplicate_group_count": len(duplicate_groups),
                "initial_evaluation_fixture": {
                    "path": project_relative(evaluation_fixture_path, project_root),
                    "sha256": sha256_file(evaluation_fixture_path),
                    "production_usage_forbidden": True,
                    "inference_feedback_used": False,
                },
                "frame_extraction": extraction_report,
                "outputs": {
                    "frame_selections": _output_hash(
                        output_transaction.staging_dir / "ocr_frame_selections.json",
                        output_transaction.staging_dir,
                    ),
                    "raw_results": _output_hash(raw_path, output_transaction.staging_dir),
                    "aggregates": _output_hash(aggregate_path, output_transaction.staging_dir),
                    "validations": _output_hash(validation_path, output_transaction.staging_dir),
                    "duplicates": _output_hash(duplicate_path, output_transaction.staging_dir),
                    "initial_evaluation": _output_hash(
                        evaluation_path, output_transaction.staging_dir
                    ),
                },
                "frozen_hashes_before": frozen_before,
                "frozen_hashes_after": frozen_after,
                "frozen_inputs_unchanged": True,
                "detector_rerun": False,
                "semantic_parser_performed": False,
                "cloud_or_llm_vision_used": False,
                "source_candidates_deleted": False,
                "validation": {
                    "all_178_candidates_processed": len(target_events) == 178,
                    "candidate_ids_unique": len(events_by_id) == len(target_events),
                    "raw_results_traceable": all(
                        row["event_id"] in events_by_id
                        and abs(
                            float(timestamp_index.pts_sec[int(row["frame_ordinal"])])
                            - float(row["pts"])
                        )
                        <= 1e-6
                        for row in raw_rows
                    ),
                    "all_frames_have_variants": all(
                        variants_by_frame_key[
                            "{}:{:06d}".format(row.event_id, row.frame_ordinal)
                        ]
                        for row in selections
                    ),
                    "minimum_multiframe_policy_met": all(
                        len(rows) >= 2
                        or all(row.insufficient_frame_reason for row in rows)
                        for rows in selections_by_event.values()
                    ),
                    "human_fields_default_null": all(
                        row.human_text is None
                        and row.human_decision is None
                        and row.human_action is None
                        and row.reviewed_at is None
                        and row.reviewed_by is None
                        for row in validations
                    ),
                    "trigger_not_high_confidence_no_text": all(
                        row.validation_label != "NO_TEXT"
                        for row in validations
                        if row.event_type == "TRIGGER_NOTIFICATION"
                    ),
                    "automatic_duplicate_merge_performed": False,
                },
            }
            manifest_path = output_transaction.staging_dir / "checkpoint1c_manifest.json"
            if replay_id != DEFAULT_REPLAY_ID:
                manifest["replay_id"] = replay_id
                manifest["validation"].pop("all_178_candidates_processed", None)
                manifest["validation"]["all_candidates_processed"] = (
                    len(target_events) == len(selections_by_event)
                )
            write_json(manifest_path, manifest)
            _schema_validator(project_root, "checkpoint1c_manifest.schema.json").validate(
                manifest
            )

            aggregate_by_id = {row.event_id: row.to_dict() for row in aggregates}
            validation_by_id = {row.event_id: row for row in validations}
            cards_by_event = {}
            review_records = []
            for event in target_events:
                event_id = str(event["event_id"])
                validation = validation_by_id[event_id]
                card_relative = "candidates/{}/{}/{}__review.jpg".format(
                    validation.workflow_status, validation.event_type, event_id
                )
                event_raw_by_frame: DefaultDict[int, List[Dict[str, Any]]] = defaultdict(list)
                for row in raw_by_event[event_id]:
                    event_raw_by_frame[int(row["frame_ordinal"])].append(row)
                render_review_card(
                    output_path=review_transaction.staging_dir / card_relative,
                    event=event,
                    selections=selections_by_event[event_id],
                    variants_by_frame_key=variants_by_frame_key,
                    raw_results_by_frame=event_raw_by_frame,
                    aggregate=aggregate_by_id[event_id],
                    validation=validation,
                    output_staging=output_transaction.staging_dir,
                    review_staging=review_transaction.staging_dir,
                    full_frame_paths=full_frame_paths,
                )
                cards_by_event[event_id] = card_relative
                review_records.append(
                    build_review_record(
                        validation,
                        selections_by_event[event_id],
                        aggregate_by_id[event_id],
                        card_relative,
                    )
                )
            contact_sheets = build_classification_contact_sheets(
                review_transaction.staging_dir, review_records, cards_by_event
            )
            review_result = {
                "schema_version": "0.1.0",
                "checkpoint": "1C",
                "kind": "checkpoint1c_human_review",
                "record_count": len(review_records),
                "source_manifest_sha256": sha256_file(manifest_path),
                "contact_sheets": contact_sheets,
                "records": review_records,
            }
            review_json_path = review_transaction.staging_dir / "checkpoint1c_review.json"
            write_json(review_json_path, review_result)
            write_json(review_transaction.staging_dir / "review_manifest.json", review_result)
            _schema_validator(project_root, "checkpoint1c_review.schema.json").validate(
                review_result
            )
            if not all(
                (review_transaction.staging_dir / row["review_card_path"]).is_file()
                for row in review_records
            ):
                raise InputError("Checkpoint 1C Review Pack 缺少 candidate card")
            if len(review_records) != len(target_events) or contact_sheets["tile_count"] != len(target_events):
                raise InputError("Checkpoint 1C Review Pack 未完整涵蓋 candidates")
            contact_index = load_json(
                review_transaction.staging_dir / contact_sheets["index_path"]
            )
            review_ids = {str(row["event_id"]) for row in review_records}
            indexed_ids = {str(row["event_id"]) for row in contact_index["rows"]}
            if indexed_ids != review_ids or len(contact_index["rows"]) != len(target_events):
                raise InputError("Checkpoint 1C contact sheet index 無法一一追溯 candidates")
            if not all(
                (review_transaction.staging_dir / row["page"]).is_file()
                for row in contact_index["rows"]
            ):
                raise InputError("Checkpoint 1C contact sheet index 指向不存在頁面")
            output_transaction.commit()
            review_transaction.commit()
    finalize_generated_output(output_dir)
    finalize_generated_output(review_output_dir)
    return manifest
