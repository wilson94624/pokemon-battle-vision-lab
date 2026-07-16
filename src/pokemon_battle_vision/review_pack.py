"""Checkpoint 1B Human Review Pack 的只讀輸入驗證與輸出 orchestration。"""

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from jsonschema import validate

from .battle_text_audit import select_dense_audit_diagnostics
from .battle_text_round1 import (
    build_round1_mapping,
    build_round1_reference_frames,
    load_round1_fixture,
    write_round1_mapping_csv,
)
from .checkpoint1b_models import EVENT_TYPES
from .config import load_json, load_roi_config
from .errors import InputError
from .review_frame_extractor import (
    build_coverage_samples,
    build_evidence_requests,
    extract_review_evidence,
    load_frame_records,
    roi_ids_for_event,
    select_candidate_frames,
)
from .review_pack_models import (
    BOUNDARY_QUALITIES,
    HUMAN_STATUSES,
    CandidateReviewRecord,
)
from .review_pack_render import (
    build_candidate_contact_sheets,
    build_coverage_contact_sheets,
    build_dense_recall_audit_sheets,
    build_round1_regression_sheets,
    render_candidate_review_image,
    build_trigger_round1_regression_sheets,
)
from .output_transaction import OutputTransaction, finalize_generated_output
from .roi import pixel_rois
from .scanner import load_frame_timestamp_index, validate_frozen_roi_approval
from .utils import project_relative, sha256_file, write_json
from .trigger_notification_audit import load_trigger_round1_fixture
from .trigger_notification_features import (
    TRIGGER_ANALYSIS_ROIS,
    trigger_analysis_rois,
)
from .trigger_notification_round1 import (
    build_trigger_round1_mapping,
    build_trigger_round1_reference_frames,
    write_trigger_round1_mapping_csv,
)


IMMUTABLE_DETECTOR_FILES = (
    "src/pokemon_battle_vision/candidate_detection.py",
    "src/pokemon_battle_vision/scanner.py",
    "src/pokemon_battle_vision/timeline.py",
    "src/pokemon_battle_vision/trigger_notification_features.py",
    "src/pokemon_battle_vision/trigger_notification_detection.py",
    "src/pokemon_battle_vision/trigger_notification_timeline.py",
)


def _require_files(paths: Sequence[Path]) -> None:
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise InputError("Human Review Pack 缺少必要輸入：{}".format("、".join(missing)))


def _load_events(path: Path) -> Dict[str, Any]:
    payload = load_json(path)
    events = payload.get("events")
    if payload.get("checkpoint") != "1B" or not isinstance(events, list):
        raise InputError("events.json 不是 Checkpoint 1B event candidates")
    if int(payload.get("event_count", -1)) != len(events):
        raise InputError("events.json event_count 與 events 長度不一致")
    candidate_ids = [str(event.get("event_id", "")) for event in events if isinstance(event, dict)]
    if len(candidate_ids) != len(events) or any(not value for value in candidate_ids):
        raise InputError("events.json 含缺少 event_id 的 candidate")
    if len(set(candidate_ids)) != len(candidate_ids):
        raise InputError("events.json candidate ID 不可重複")
    for event in events:
        if event.get("type") not in EVENT_TYPES:
            raise InputError("events.json 含未知 predicted type：{}".format(event.get("type")))
        if float(event["start_time"]) > float(event["end_time"]):
            raise InputError("candidate {} 時間順序錯誤".format(event["event_id"]))
        roi_ids_for_event(event)
    return payload


def _immutable_hashes(
    project_root: Path,
    events_path: Path,
    frames_path: Path,
    detector_report_path: Path,
    roi_config_path: Path,
    approval_path: Path,
    diagnostics_path: Path,
    diagnostic_report_path: Path,
    trigger_diagnostics_path: Path,
    trigger_audit_report_path: Path,
) -> Dict[str, Dict[str, str]]:
    paths = {
        "events": events_path,
        "frames": frames_path,
        "detector_report": detector_report_path,
        "roi_config": roi_config_path,
        "roi_approval": approval_path,
        "battle_text_diagnostics": diagnostics_path,
        "battle_text_detector_report": diagnostic_report_path,
        "trigger_notification_diagnostics": trigger_diagnostics_path,
        "trigger_notification_audit_report": trigger_audit_report_path,
    }
    for relative in IMMUTABLE_DETECTOR_FILES:
        paths[Path(relative).stem] = project_root / relative
    _require_files(list(paths.values()))
    return {
        name: {
            "path": project_relative(path, project_root),
            "sha256": sha256_file(path),
        }
        for name, path in paths.items()
    }


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise InputError(
                        "diagnostics 第 {} 列不是 object".format(line_number)
                    )
                rows.append(row)
    except json.JSONDecodeError as exc:
        raise InputError("battle_text diagnostics 含無效 JSON：{}".format(exc)) from exc
    if not rows:
        raise InputError("battle_text diagnostics 不可為空：{}".format(path))
    return rows


def _write_candidate_csv(path: Path, records: Sequence[CandidateReviewRecord]) -> None:
    if not records:
        raise InputError("沒有 candidate review records 可寫入 CSV")
    rows = []
    for record in records:
        row = record.to_dict()
        row["visible_rois"] = json.dumps(row["visible_rois"], ensure_ascii=False)
        row["evidence_frames"] = json.dumps(row["evidence_frames"], ensure_ascii=False)
        row["split_required"] = json.dumps(row["split_required"])
        rows.append(row)
    temporary = path.with_name(path.name + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    os.replace(str(temporary), str(path))


def _validate_schema(project_root: Path, schema_name: str, payload: Mapping[str, Any]) -> None:
    schema_path = project_root / "schemas" / schema_name
    _require_files([schema_path])
    validate(instance=payload, schema=load_json(schema_path))


def _validate_review_outputs(
    output_dir: Path,
    events: Sequence[Mapping[str, Any]],
    records: Sequence[CandidateReviewRecord],
    contact_index: Mapping[str, Any],
    coverage_index: Mapping[str, Any],
    dense_index: Mapping[str, Any],
    round1_index: Mapping[str, Any],
    trigger_round1_index: Mapping[str, Any],
) -> Dict[str, Any]:
    event_ids = [str(event["event_id"]) for event in events]
    record_ids = [record.candidate_id for record in records]
    if len(record_ids) != len(set(record_ids)):
        raise InputError("Review records 的 candidate ID 重複")
    if event_ids != record_ids:
        raise InputError("Review records 未與 events.json 保持一一對應及原始順序")
    if any(
        not (record.start_time <= record.middle_time <= record.end_time)
        or not (record.start_frame <= record.middle_frame <= record.end_frame)
        for record in records
    ):
        raise InputError("Review records 的 start／middle／end 時間或 ordinal 順序錯誤")
    if any(
        record.human_status != "pending"
        or record.corrected_type
        or record.boundary_quality
        or record.merge_with_candidate_id
        or record.split_required
        or record.notes
        for record in records
    ):
        raise InputError("Review records 的人工欄位預設值錯誤")
    for record in records:
        frame_ids = [int(point["frame_index"]) for point in record.evidence_frames]
        if len(frame_ids) != len(set(frame_ids)):
            raise InputError("Review evidence frames 不可重複：{}".format(record.candidate_id))
        if record.predicted_type == "BATTLE_TEXT":
            roles = {
                role
                for point in record.evidence_frames
                for role in point.get("roles", [])
            }
            required = {
                "start",
                "peak_score_structure",
                "end",
            }
            if not required.issubset(roles):
                raise InputError(
                    "BATTLE_TEXT evidence 缺少 boundary／peak：{}".format(
                        record.candidate_id
                    )
                )
        if record.predicted_type == "TRIGGER_NOTIFICATION":
            roles = {
                role
                for point in record.evidence_frames
                for role in point.get("roles", [])
            }
            if not {"start", "peak_evidence", "end"}.issubset(roles):
                raise InputError(
                    "TRIGGER_NOTIFICATION evidence 缺少 boundary／peak：{}".format(
                        record.candidate_id
                    )
                )
    missing_images = [
        record.review_image_path
        for record in records
        if not (output_dir / record.review_image_path).is_file()
    ]
    if missing_images:
        raise InputError("Review images 遺失：{}".format(missing_images[:5]))
    candidate_lookup = contact_index.get("candidate_lookup")
    if not isinstance(candidate_lookup, dict) or set(candidate_lookup) != set(event_ids):
        raise InputError("Contact sheet index 無法追溯全部 candidates")
    for candidate_id, location in candidate_lookup.items():
        page_path = output_dir / str(location["page_path"])
        if not page_path.is_file():
            raise InputError("Contact sheet page 遺失：{}".format(page_path))
        if int(location["tile_index"]) < 0:
            raise InputError("Contact sheet tile index 無效：{}".format(candidate_id))
    coverage_pages = coverage_index.get("pages")
    if not isinstance(coverage_pages, list) or not coverage_pages:
        raise InputError("Coverage review 沒有 pages")
    for page in coverage_pages:
        path = output_dir / "coverage_review" / str(page["path"])
        if not path.is_file():
            raise InputError("Coverage page 遺失：{}".format(path))
    dense_pages = dense_index.get("pages")
    if not isinstance(dense_pages, list) or not dense_pages:
        raise InputError("Dense recall audit 沒有 pages")
    for page in dense_pages:
        path = output_dir / "battle_text_recall_audit" / str(page["path"])
        if not path.is_file():
            raise InputError("Dense recall audit page 遺失：{}".format(path))
    round1_pages = round1_index.get("pages")
    if not isinstance(round1_pages, list) or not round1_pages:
        raise InputError("Round-1 regression 沒有 pages")
    for page in round1_pages:
        path = output_dir / "battle_text_round1_regression" / str(page["path"])
        if not path.is_file():
            raise InputError("Round-1 regression page 遺失：{}".format(path))
    trigger_round1_pages = trigger_round1_index.get("pages")
    if not isinstance(trigger_round1_pages, list) or not trigger_round1_pages:
        raise InputError("Trigger round-1 regression 沒有 pages")
    for page in trigger_round1_pages:
        path = output_dir / "trigger_notification_round1_regression" / str(page["path"])
        if not path.is_file():
            raise InputError("Trigger round-1 regression page 遺失：{}".format(path))
    return {
        "candidate_records_match_events": True,
        "candidate_ids_unique": True,
        "frame_and_time_order_valid": True,
        "human_field_defaults_valid": True,
        "battle_text_evidence_frames_valid": True,
        "review_images_exist": True,
        "contact_sheet_traceability": True,
        "coverage_pages_exist": True,
        "dense_recall_audit_pages_exist": True,
        "round1_regression_pages_exist": True,
        "trigger_notification_evidence_frames_valid": True,
        "trigger_round1_regression_pages_exist": True,
    }


def _build_review_pack_into(
    project_root: Path,
    video_path: Path,
    events_path: Path,
    frames_path: Path,
    checkpoint1a_dir: Path,
    roi_config_path: Path,
    output_dir: Path,
    final_output_dir: Path,
    diagnostics_path: Path,
    coverage_interval_sec: float = 0.5,
) -> Dict[str, Any]:
    """忠實視覺化既有 1B candidates；不重新偵測、分類或調整邊界。"""
    metadata_path = checkpoint1a_dir / "metadata.json"
    pts_path = checkpoint1a_dir / "frame_timestamps.npz"
    approval_path = checkpoint1a_dir / "roi_approval.json"
    overlay_manifest_path = checkpoint1a_dir / "roi_overlay_manifest.json"
    detector_report_path = events_path.parent / "detector_report.json"
    diagnostic_report_path = diagnostics_path.parent / "battle_text_detector_report.json"
    trigger_diagnostics_path = (
        diagnostics_path.parent / "trigger_notification_diagnostics.jsonl"
    )
    trigger_audit_report_path = (
        diagnostics_path.parent / "trigger_notification_audit_report.json"
    )
    round1_fixture_path = project_root / "references" / "battle_text_human_review_round1.json"
    trigger_fixture_path = (
        project_root / "references" / "trigger_notification_human_review_round1.json"
    )
    _require_files(
        [
            video_path,
            events_path,
            frames_path,
            metadata_path,
            pts_path,
            approval_path,
            overlay_manifest_path,
            detector_report_path,
            roi_config_path,
            diagnostics_path,
            diagnostic_report_path,
            round1_fixture_path,
            trigger_diagnostics_path,
            trigger_audit_report_path,
            trigger_fixture_path,
        ]
    )
    before_hashes = _immutable_hashes(
        project_root,
        events_path,
        frames_path,
        detector_report_path,
        roi_config_path,
        approval_path,
        diagnostics_path,
        diagnostic_report_path,
        trigger_diagnostics_path,
        trigger_audit_report_path,
    )
    before_hashes["round1_fixture"] = {
        "path": project_relative(round1_fixture_path, project_root),
        "sha256": sha256_file(round1_fixture_path),
    }
    before_hashes["trigger_round1_fixture"] = {
        "path": project_relative(trigger_fixture_path, project_root),
        "sha256": sha256_file(trigger_fixture_path),
    }

    approval, _ = validate_frozen_roi_approval(
        video_path, roi_config_path, overlay_manifest_path, approval_path
    )
    events_payload = _load_events(events_path)
    events = events_payload["events"]
    frame_records = load_frame_records(frames_path)
    detector_report = load_json(detector_report_path)
    diagnostic_report = load_json(diagnostic_report_path)
    diagnostics = _load_jsonl(diagnostics_path)
    round1_fixture = load_round1_fixture(round1_fixture_path)
    round1_report = build_round1_mapping(round1_fixture, events)
    trigger_diagnostics = _load_jsonl(trigger_diagnostics_path)
    trigger_fixture = load_trigger_round1_fixture(trigger_fixture_path)
    trigger_round1_report = build_trigger_round1_mapping(trigger_fixture, events)
    if len(diagnostics) != len(frame_records):
        raise InputError("diagnostics 與 frames.jsonl 必須一一對應")
    if len(trigger_diagnostics) != len(frame_records) * 2:
        raise InputError("trigger diagnostics 必須每個 sampled frame 各含 player/opponent 一列")
    dense_diagnostics = select_dense_audit_diagnostics(diagnostics)
    if detector_report.get("ai_or_ocr_used") is not False:
        raise InputError("Review Pack 只接受 ai_or_ocr_used=false 的 detector report")
    metadata = load_json(metadata_path)
    roi_config, normalized_rois = load_roi_config(roi_config_path)
    display_dimensions = metadata.get("display_dimensions")
    if display_dimensions != roi_config.get("display_dimensions"):
        raise InputError("Review Pack 的 ROI 與影片 display dimensions 不一致")
    timestamp_index = load_frame_timestamp_index(pts_path, approval["video_sha256"])
    converted_rois = pixel_rois(
        normalized_rois,
        int(display_dimensions["width"]),
        int(display_dimensions["height"]),
    )
    converted_rois.update(
        trigger_analysis_rois(
            converted_rois,
            int(display_dimensions["width"]),
            int(display_dimensions["height"]),
        )
    )
    diagnostics_by_frame = {
        int(row["frame_ordinal"]): row for row in diagnostics
    }
    trigger_diagnostics_by_frame_side = {
        (int(row["frame_ordinal"]), str(row["side"])): row
        for row in trigger_diagnostics
    }

    selections = {
        str(event["event_id"]): select_candidate_frames(
            event,
            frame_records,
            timestamp_index,
            diagnostics_by_frame=diagnostics_by_frame,
            trigger_diagnostics_by_frame_side=trigger_diagnostics_by_frame_side,
        )
        for event in events
    }
    round1_reference_frames = build_round1_reference_frames(
        round1_fixture, timestamp_index
    )
    trigger_reference_frames = build_trigger_round1_reference_frames(
        trigger_fixture, timestamp_index
    )
    coverage_samples = build_coverage_samples(
        timestamp_index, events, coverage_interval_sec, candidate_type="BATTLE_TEXT"
    )
    roi_requests, full_frame_requests = build_evidence_requests(
        events,
        selections,
        coverage_samples,
        coverage_roi_ids=("battle_text",),
        dense_diagnostics=dense_diagnostics,
    )
    for frame_indices in round1_reference_frames.values():
        for frame_index in frame_indices:
            full_frame_requests.add(frame_index)
            roi_requests.setdefault(frame_index, set()).add("battle_text")
    for window in trigger_fixture["positive_windows"]:
        analysis_roi_id = TRIGGER_ANALYSIS_ROIS[str(window["side"])]
        for frame_index in trigger_reference_frames[str(window["case_id"])]:
            full_frame_requests.add(frame_index)
            roi_requests.setdefault(frame_index, set()).add(analysis_roi_id)

    evidence, extraction_validation = extract_review_evidence(
        video_path,
        metadata,
        timestamp_index,
        converted_rois,
        roi_requests,
        full_frame_requests,
    )

    records: List[CandidateReviewRecord] = []
    record_by_id: Dict[str, CandidateReviewRecord] = {}
    for event in events:
        candidate_id = str(event["event_id"])
        event_type = str(event["type"])
        selection = selections[candidate_id]
        relative_image_path = Path("candidates") / event_type / "{}__review.jpg".format(candidate_id)
        render_candidate_review_image(
            event,
            selection,
            evidence,
            converted_rois,
            int(display_dimensions["width"]),
            int(display_dimensions["height"]),
            output_dir / relative_image_path,
        )
        record = CandidateReviewRecord(
            candidate_id=candidate_id,
            predicted_type=event_type,
            start_frame=selection.start_frame,
            middle_frame=selection.middle_frame,
            end_frame=selection.end_frame,
            start_time=round(selection.start_pts, 6),
            middle_time=round(selection.middle_pts, 6),
            end_time=round(selection.end_pts, 6),
            duration_sec=float(event["duration_sec"]),
            confidence=float(event["confidence"]),
            visible_rois=roi_ids_for_event(event),
            representative_time=round(selection.representative_pts, 6),
            representative_frame=selection.representative_frame,
            review_image_path=relative_image_path.as_posix(),
            review_frame_strategy=selection.strategy,
            evidence_frames=[point.to_dict() for point in selection.evidence_points],
        )
        records.append(record)
        record_by_id[candidate_id] = record

    review_payload = {
        "schema_version": "0.1.0",
        "checkpoint": "1B_HUMAN_REVIEW",
        "source_events_sha256": before_hashes["events"]["sha256"],
        "record_count": len(records),
        "allowed_values": {
            "human_status": list(HUMAN_STATUSES),
            "corrected_type": [""] + list(EVENT_TYPES),
            "boundary_quality": list(BOUNDARY_QUALITIES),
        },
        "records": [record.to_dict() for record in records],
    }
    review_json_path = output_dir / "candidate_review.json"
    review_csv_path = output_dir / "candidate_review.csv"
    _validate_schema(project_root, "candidate_review.schema.json", review_payload)
    write_json(review_json_path, review_payload)
    _write_candidate_csv(review_csv_path, records)

    contact_dir = output_dir / "contact_sheets"
    contact_index = build_candidate_contact_sheets(
        events, record_by_id, selections, evidence, contact_dir
    )
    contact_index_path = contact_dir / "contact_sheet_index.json"
    write_json(contact_index_path, contact_index)

    coverage_dir = output_dir / "coverage_review"
    coverage_index = build_coverage_contact_sheets(
        coverage_samples, evidence, coverage_dir
    )
    coverage_index["coverage_interval_sec"] = float(coverage_interval_sec)
    coverage_index["candidate_overlap_rule"] = "start_time <= pts <= end_time"
    coverage_index_path = coverage_dir / "coverage_index.json"
    write_json(coverage_index_path, coverage_index)

    dense_dir = output_dir / "battle_text_recall_audit"
    dense_index = build_dense_recall_audit_sheets(
        dense_diagnostics, evidence, dense_dir
    )
    dense_index["regression_window_radius_sec"] = 1.0
    dense_index_path = dense_dir / "battle_text_recall_audit_index.json"
    write_json(dense_index_path, dense_index)

    for row in round1_report["rows"]:
        row["new_representatives"] = [
            {
                "candidate_id": candidate_id,
                "frame_index": selections[candidate_id].representative_frame,
                "pts": round(selections[candidate_id].representative_pts, 6),
                "strategy": selections[candidate_id].strategy,
            }
            for candidate_id in row["mapped_candidate_ids"]
            if candidate_id in selections
        ]
    round1_report["representative_frame_comparison"] = {
        "baseline_candidate_id": "battle_text-0035",
        "old_middle_time": round1_fixture["diagnostic_questions"][
            "weak_representative_case"
        ]["old_middle_time"],
        "new_representatives": next(
            row["new_representatives"]
            for row in round1_report["rows"]
            if row["baseline_candidate_id"] == "battle_text-0035"
        ),
    }
    round1_dir = output_dir / "battle_text_round1_regression"
    round1_json_path = round1_dir / "round1_mapping.json"
    round1_csv_path = round1_dir / "round1_mapping.csv"
    write_json(round1_json_path, round1_report)
    write_round1_mapping_csv(round1_csv_path, round1_report)
    round1_visual_index = build_round1_regression_sheets(
        round1_report,
        round1_reference_frames,
        selections,
        evidence,
        round1_dir,
    )
    round1_index_path = round1_dir / "round1_visual_index.json"
    write_json(round1_index_path, round1_visual_index)

    for row in trigger_round1_report["rows"]:
        row["new_representatives"] = [
            {
                "candidate_id": candidate_id,
                "frame_index": selections[candidate_id].representative_frame,
                "pts": round(selections[candidate_id].representative_pts, 6),
                "strategy": selections[candidate_id].strategy,
                "evidence": next(
                    (
                        point.to_dict()
                        for point in selections[candidate_id].evidence_points
                        if "peak_evidence" in point.roles
                    ),
                    {},
                ),
            }
            for candidate_id in row["mapped_candidate_ids"]
            if candidate_id in selections
        ]
    trigger_round1_dir = output_dir / "trigger_notification_round1_regression"
    trigger_round1_json_path = trigger_round1_dir / "round1_mapping.json"
    trigger_round1_csv_path = trigger_round1_dir / "round1_mapping.csv"
    write_json(trigger_round1_json_path, trigger_round1_report)
    write_trigger_round1_mapping_csv(
        trigger_round1_csv_path, trigger_round1_report
    )
    trigger_round1_visual_index = build_trigger_round1_regression_sheets(
        trigger_round1_report,
        trigger_reference_frames,
        selections,
        evidence,
        trigger_round1_dir,
    )
    trigger_round1_index_path = trigger_round1_dir / "round1_visual_index.json"
    write_json(trigger_round1_index_path, trigger_round1_visual_index)

    new_summary = diagnostic_report.get("new", {})
    new_regression = new_summary.get("regression", {})
    recall_summary = {
        "schema_version": "0.1.0",
        "recall_gate": "pending_human_review",
        "old_candidate_count": int(
            round1_fixture["source_baseline"]["battle_text_candidate_count"]
        ),
        "new_candidate_count": int(new_summary.get("candidate_count", 0)),
        "regression_window_count": int(new_regression.get("window_count", 0)),
        "regression_windows_covered": int(new_regression.get("covered_count", 0)),
        "regression_windows_still_missed": list(new_regression.get("still_missed", [])),
        "coverage_interval_sec": float(coverage_interval_sec),
        "dense_audit_interval_sec": 0.1,
        "short_candidate_count": int(
            new_summary.get("short_candidate_count_0_1_to_0_3_sec", 0)
        ),
        "long_candidate_count": int(
            new_summary.get("long_candidate_count_5_sec_or_more", 0)
        ),
        "diagnostics_path": project_relative(diagnostics_path, project_root),
        "review_pack_path": project_relative(final_output_dir, project_root),
        "dense_audit_page_count": dense_index["page_count"],
        "coverage_page_count": coverage_index["page_count"],
        "empty_blank_candidate_heuristic": new_summary.get(
            "empty_blank_candidate_count"
        ),
    }
    recall_summary_path = output_dir / "battle_text_recall_summary.json"
    _validate_schema(
        project_root, "battle_text_recall_summary.schema.json", recall_summary
    )
    write_json(recall_summary_path, recall_summary)

    after_hashes = _immutable_hashes(
        project_root,
        events_path,
        frames_path,
        detector_report_path,
        roi_config_path,
        approval_path,
        diagnostics_path,
        diagnostic_report_path,
        trigger_diagnostics_path,
        trigger_audit_report_path,
    )
    after_hashes["round1_fixture"] = {
        "path": project_relative(round1_fixture_path, project_root),
        "sha256": sha256_file(round1_fixture_path),
    }
    after_hashes["trigger_round1_fixture"] = {
        "path": project_relative(trigger_fixture_path, project_root),
        "sha256": sha256_file(trigger_fixture_path),
    }
    if before_hashes != after_hashes:
        changed = [name for name in before_hashes if before_hashes[name] != after_hashes[name]]
        raise InputError("Review Pack 不可修改的來源發生變更：{}".format(changed))
    output_validation = _validate_review_outputs(
        output_dir,
        events,
        records,
        contact_index,
        coverage_index,
        dense_index,
        round1_visual_index,
        trigger_round1_visual_index,
    )

    type_counts = {
        event_type: sum(1 for event in events if event["type"] == event_type)
        for event_type in EVENT_TYPES
    }
    manifest = {
        "schema_version": "0.1.0",
        "checkpoint": "1B_HUMAN_REVIEW",
        "status": "complete_pending_human_review",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "pokemon_battle_vision.review_pack",
        "source_candidates_preserved": True,
        "detector_rerun": False,
        "ocr_performed": False,
        "checkpoint_1c_started": False,
        "candidate_count": len(records),
        "candidate_counts_by_type": type_counts,
        "source_hashes_before": before_hashes,
        "source_hashes_after": after_hashes,
        "immutable_sources_unchanged": before_hashes == after_hashes,
        "frame_extraction": extraction_validation,
        "candidate_review": {
            "json_path": review_json_path.relative_to(output_dir).as_posix(),
            "json_sha256": sha256_file(review_json_path),
            "csv_path": review_csv_path.relative_to(output_dir).as_posix(),
            "csv_sha256": sha256_file(review_csv_path),
            "record_count": len(records),
            "review_image_count": len(records),
        },
        "contact_sheets": {
            "index_path": contact_index_path.relative_to(output_dir).as_posix(),
            "index_sha256": sha256_file(contact_index_path),
            "page_counts": contact_index["page_counts"],
            "total_page_count": sum(contact_index["page_counts"].values()),
        },
        "coverage_review": {
            "index_path": coverage_index_path.relative_to(output_dir).as_posix(),
            "index_sha256": sha256_file(coverage_index_path),
            "interval_sec": float(coverage_interval_sec),
            "tile_count": coverage_index["tile_count"],
            "page_count": coverage_index["page_count"],
        },
        "dense_recall_audit": {
            "index_path": dense_index_path.relative_to(output_dir).as_posix(),
            "index_sha256": sha256_file(dense_index_path),
            "interval_sec": 0.1,
            "tile_count": dense_index["tile_count"],
            "page_count": dense_index["page_count"],
        },
        "battle_text_round1_regression": {
            "mapping_path": round1_json_path.relative_to(output_dir).as_posix(),
            "mapping_sha256": sha256_file(round1_json_path),
            "csv_path": round1_csv_path.relative_to(output_dir).as_posix(),
            "csv_sha256": sha256_file(round1_csv_path),
            "visual_index_path": round1_index_path.relative_to(output_dir).as_posix(),
            "visual_index_sha256": sha256_file(round1_index_path),
            "page_count": round1_visual_index["page_count"],
            "false_positive_removed_count": round1_report["false_positive_removal"][
                "removed_count"
            ],
            "accepted_covered_count": round1_report["accepted_preservation"][
                "covered_count"
            ],
            "case_0033_split_success": round1_report[
                "case_0033_multi_text_split"
            ]["success"],
        },
        "trigger_notification_round1_regression": {
            "mapping_path": trigger_round1_json_path.relative_to(output_dir).as_posix(),
            "mapping_sha256": sha256_file(trigger_round1_json_path),
            "csv_path": trigger_round1_csv_path.relative_to(output_dir).as_posix(),
            "csv_sha256": sha256_file(trigger_round1_csv_path),
            "visual_index_path": trigger_round1_index_path.relative_to(output_dir).as_posix(),
            "visual_index_sha256": sha256_file(trigger_round1_index_path),
            "page_count": trigger_round1_visual_index["page_count"],
            **trigger_round1_report["summary"],
        },
        "battle_text_recall_summary": {
            "path": recall_summary_path.relative_to(output_dir).as_posix(),
            "sha256": sha256_file(recall_summary_path),
            **recall_summary,
        },
        "validation": output_validation,
    }
    manifest_path = output_dir / "review_manifest.json"
    _validate_schema(project_root, "review_manifest.schema.json", manifest)
    write_json(manifest_path, manifest)
    return manifest


def build_review_pack(
    project_root: Path,
    video_path: Path,
    events_path: Path,
    frames_path: Path,
    checkpoint1a_dir: Path,
    roi_config_path: Path,
    output_dir: Path,
    coverage_interval_sec: float = 0.5,
    diagnostics_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """以 transaction 安全替換 Review Pack；失敗時保留上一版正式輸出。"""
    if abs(float(coverage_interval_sec) - 0.5) > 1e-9:
        raise InputError("一般 Recall Coverage Review interval 固定為 0.5 秒")
    resolved_diagnostics = diagnostics_path or (
        events_path.parent.parent
        / "checkpoint-1b-debug"
        / "battle_text_diagnostics.jsonl"
    )
    with OutputTransaction(project_root, output_dir) as transaction:
        manifest = _build_review_pack_into(
            project_root=project_root,
            video_path=video_path,
            events_path=events_path,
            frames_path=frames_path,
            checkpoint1a_dir=checkpoint1a_dir,
            roi_config_path=roi_config_path,
            output_dir=transaction.staging_dir,
            final_output_dir=output_dir,
            diagnostics_path=resolved_diagnostics,
            coverage_interval_sec=coverage_interval_sec,
        )
        transaction.commit()
    finalize_generated_output(output_dir)
    return manifest
