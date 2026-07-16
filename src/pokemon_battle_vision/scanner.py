"""Checkpoint 1B 固定 10 Hz 全片 scanner 與 frame metadata writer。"""

import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any, DefaultDict, Dict, List, Mapping, Optional, Sequence, Tuple

import cv2
import numpy as np
from jsonschema import Draft202012Validator

from .battle_text_audit import build_detector_diagnostic_report
from .battle_text_detection import DEFAULT_BATTLE_TEXT_CONFIG
from .battle_text_timeline import DEFAULT_BATTLE_TEXT_TEMPORAL_CONFIG
from .candidate_detection import (
    DEFAULT_THRESHOLDS,
    CandidateDetector,
    load_approved_templates,
)
from .checkpoint1b_models import FrameScanRecord, SamplePlanItem
from .config import load_json, load_roi_config
from .errors import DecodeAlignmentError, InputError, RoiApprovalError, TimestampIndexError
from .models import FrameTimestampIndex
from .output_transaction import OutputTransaction, finalize_generated_output
from .roi import pixel_rois
from .sampling import fixed_interval_targets
from .timeline import build_event_timeline_with_diagnostics, format_timestamp
from .timeline import build_event_timeline_with_all_diagnostics
from .trigger_notification_detection import DEFAULT_TRIGGER_PROPOSAL_CONFIG
from .trigger_notification_audit import (
    build_trigger_round1_comparison,
    load_trigger_round1_fixture,
)
from .trigger_notification_timeline import DEFAULT_TRIGGER_TEMPORAL_CONFIG
from .utils import project_relative, sha256_file, write_json
from .video import rotate_frame_clockwise


SCAN_HZ = 10.0
SCAN_INTERVAL_SEC = 1.0 / SCAN_HZ


def _require_files(paths: Sequence[Path]) -> None:
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise InputError("Checkpoint 1B 缺少必要輸入：{}".format("、".join(missing)))


def validate_frozen_roi_approval(
    video_path: Path,
    roi_config_path: Path,
    overlay_manifest_path: Path,
    approval_path: Path,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Read-only 驗證 1A approval 與所有 frozen hashes。"""
    _require_files([video_path, roi_config_path, overlay_manifest_path, approval_path])
    manifest = load_json(overlay_manifest_path)
    approval = load_json(approval_path)
    if approval.get("status") != "approved":
        raise RoiApprovalError("Checkpoint 1B 只接受 status=approved 的 ROI approval")
    expected = {
        "video_sha256": sha256_file(video_path),
        "roi_config_sha256": sha256_file(roi_config_path),
        "overlay_manifest_sha256": sha256_file(overlay_manifest_path),
    }
    for key, actual in expected.items():
        if approval.get(key) != actual:
            raise RoiApprovalError("Frozen Baseline hash 不一致：{}".format(key))
    if manifest.get("video_sha256") != expected["video_sha256"]:
        raise RoiApprovalError("overlay manifest 的 video hash 與 approval 不一致")
    if manifest.get("roi_config_sha256") != expected["roi_config_sha256"]:
        raise RoiApprovalError("overlay manifest 的 ROI config hash 與 approval 不一致")
    overlays = manifest.get("overlays")
    if not isinstance(overlays, list) or len(overlays) != int(approval.get("overlay_count", -1)):
        raise RoiApprovalError("overlay count 與 approval 不一致")
    for row in overlays:
        if not isinstance(row, dict) or not row.get("path") or not row.get("sha256"):
            raise RoiApprovalError("overlay manifest item 缺少 path/sha256")
        overlay_path = overlay_manifest_path.parent / str(row["path"])
        if not overlay_path.is_file() or sha256_file(overlay_path) != row["sha256"]:
            raise RoiApprovalError("核准 overlay 遺失或 hash 改變：{}".format(overlay_path))
    return approval, manifest


def load_frame_timestamp_index(path: Path, expected_video_sha256: str) -> FrameTimestampIndex:
    if not path.is_file():
        raise InputError("找不到 Checkpoint 1A PTS index：{}".format(path))
    try:
        with np.load(str(path), allow_pickle=False) as payload:
            pts = np.asarray(payload["pts_sec"], dtype=np.float64)
            duration = np.asarray(payload["duration_sec"], dtype=np.float64)
            key_frame = np.asarray(payload["key_frame"], dtype=np.bool_)
            raw_metadata = str(payload["metadata_json"].item())
    except (OSError, KeyError, ValueError) as exc:
        raise TimestampIndexError("無法載入 Checkpoint 1A PTS index：{}".format(exc)) from exc
    try:
        metadata = json.loads(raw_metadata)
    except json.JSONDecodeError as exc:
        raise TimestampIndexError("PTS index metadata_json 不是有效 JSON") from exc
    if metadata.get("video_sha256") != expected_video_sha256:
        raise TimestampIndexError("PTS index 的 video hash 與 Frozen Baseline 不一致")
    validation = metadata.get("validation")
    if not isinstance(validation, dict):
        raise TimestampIndexError("PTS index 缺少 validation metadata")
    if not validation.get("complete") or not validation.get("strictly_monotonic"):
        raise TimestampIndexError("PTS index 不完整或不是嚴格單調")
    if not (pts.size == duration.size == key_frame.size) or pts.size == 0:
        raise TimestampIndexError("PTS index arrays 長度不一致或為空")
    return FrameTimestampIndex(
        pts_sec=pts,
        duration_sec=duration,
        key_frame=key_frame,
        validation=validation,
        video_sha256=str(metadata["video_sha256"]),
        ffprobe_version=str(metadata.get("ffprobe_version", "unknown")),
    )


def build_fixed_10hz_sample_plan(index: FrameTimestampIndex) -> List[SamplePlanItem]:
    if index.frame_count == 0:
        return []
    targets = fixed_interval_targets(
        float(index.pts_sec[0]), float(index.pts_sec[-1]), SCAN_INTERVAL_SEC
    )
    return [
        SamplePlanItem(
            sample_index=sample_index,
            target_time=round(target, 6),
            frame_index=index.nearest_ordinal(target),
            pts=float(index.pts_sec[index.nearest_ordinal(target)]),
        )
        for sample_index, target in enumerate(targets)
    ]


def frame_fingerprint(frame: np.ndarray) -> str:
    small = cv2.resize(frame, (64, 36), interpolation=cv2.INTER_AREA)
    return hashlib.sha256(small.tobytes()).hexdigest()


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    os.replace(str(temporary), str(path))


def _read_jsonl_if_present(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise InputError(
                    "舊版 JSONL 第 {} 列無效：{}".format(line_number, path)
                ) from exc
            if not isinstance(row, dict):
                raise InputError("舊版 JSONL row 不是 object：{}".format(path))
            rows.append(row)
    return rows


def _schema_validator(project_root: Path, schema_name: str) -> Draft202012Validator:
    schema_path = project_root / "schemas" / schema_name
    _require_files([schema_path])
    return Draft202012Validator(load_json(schema_path))


def scan_video_10hz(
    video_path: Path,
    metadata: Mapping[str, Any],
    timestamp_index: FrameTimestampIndex,
    sample_plan: Sequence[SamplePlanItem],
    detector: CandidateDetector,
) -> Tuple[List[FrameScanRecord], Dict[str, Any]]:
    by_ordinal: DefaultDict[int, List[SamplePlanItem]] = defaultdict(list)
    for item in sample_plan:
        by_ordinal[item.frame_index].append(item)
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise InputError("OpenCV 無法開啟影片：{}".format(video_path))
    records: List[FrameScanRecord] = []
    decoded_count = 0
    first_decoded_dimensions = None
    first_display_dimensions = None
    position_mismatches: List[Dict[str, Any]] = []
    rotation = int(metadata["rotation"]["clockwise_degrees"])
    orientation_disabled = False
    try:
        if hasattr(cv2, "CAP_PROP_ORIENTATION_AUTO"):
            capture.set(cv2.CAP_PROP_ORIENTATION_AUTO, 0)
            orientation_disabled = abs(capture.get(cv2.CAP_PROP_ORIENTATION_AUTO)) < 0.5
        while True:
            success, raw_frame = capture.read()
            if not success:
                break
            frame_index = decoded_count
            decoded_count += 1
            raw_height, raw_width = raw_frame.shape[:2]
            if first_decoded_dimensions is None:
                first_decoded_dimensions = {"width": int(raw_width), "height": int(raw_height)}
                display_width = raw_height if rotation in (90, 270) else raw_width
                display_height = raw_width if rotation in (90, 270) else raw_height
                first_display_dimensions = {
                    "width": int(display_width),
                    "height": int(display_height),
                }
            position = capture.get(cv2.CAP_PROP_POS_FRAMES)
            if abs(position - float(frame_index + 1)) > 0.01 and len(position_mismatches) < 20:
                position_mismatches.append(
                    {
                        "frame_index": frame_index,
                        "expected_next_position": float(frame_index + 1),
                        "reported_next_position": float(position),
                    }
                )
            if frame_index not in by_ordinal:
                continue
            display_frame = rotate_frame_clockwise(raw_frame, rotation)
            if hasattr(detector, "score_frame_detailed"):
                scores, visible_by_event, detector_evidence = detector.score_frame_detailed(
                    display_frame
                )
            else:
                scores, visible_by_event = detector.score_frame(display_frame)
                detector_evidence = {}
            ui_state, visible_rois = detector.classify(scores, visible_by_event)
            fingerprint = frame_fingerprint(display_frame)
            for item in by_ordinal[frame_index]:
                records.append(
                    FrameScanRecord(
                        sample_index=item.sample_index,
                        frame_index=frame_index,
                        target_time=item.target_time,
                        pts=round(item.pts, 6),
                        timestamp=format_timestamp(item.pts),
                        roi_available=True,
                        ui_state=ui_state,
                        visible_rois=visible_rois,
                        frame_hash=fingerprint,
                        candidate_scores=scores,
                        battle_text_evidence=dict(
                            detector_evidence.get("BATTLE_TEXT", {})
                        ),
                        trigger_notification_evidence=dict(
                            detector_evidence.get("TRIGGER_NOTIFICATION", {})
                        ),
                    )
                )
    finally:
        capture.release()

    expected_encoded = metadata["encoded_dimensions"]
    dimensions_match = first_decoded_dimensions == {
        "width": int(expected_encoded["width"]),
        "height": int(expected_encoded["height"]),
    }
    display_match = first_display_dimensions == metadata["display_dimensions"]
    count_match = decoded_count == timestamp_index.frame_count
    records.sort(key=lambda row: row.sample_index)
    samples_complete = len(records) == len(sample_plan)
    if not (
        count_match
        and dimensions_match
        and display_match
        and orientation_disabled
        and not position_mismatches
        and samples_complete
    ):
        error = DecodeAlignmentError(
            "Checkpoint 1B 全片順序解碼、rotation 或 10 Hz sample 對齊失敗"
        )
        error.report = {
            "decoded_count": decoded_count,
            "expected_count": timestamp_index.frame_count,
            "dimensions_match": dimensions_match,
            "display_match": display_match,
            "orientation_auto_disabled": orientation_disabled,
            "position_mismatches": position_mismatches,
            "sample_count": len(records),
            "expected_sample_count": len(sample_plan),
        }
        raise error
    return records, {
        "status": "pass",
        "decoded_frame_count": decoded_count,
        "pts_frame_count": timestamp_index.frame_count,
        "sample_count": len(records),
        "sampling_hz": SCAN_HZ,
        "sampling_interval_sec": SCAN_INTERVAL_SEC,
        "sampling_strategy": "fixed_10_hz_nearest_authoritative_pts",
        "pts_authority": "ffprobe.best_effort_timestamp_time",
        "ffprobe_version": timestamp_index.ffprobe_version,
        "frame_hash_method": "sha256_of_64x36_bgr_inter_area",
        "orientation_auto_disabled": orientation_disabled,
        "rotation_clockwise_degrees": rotation,
        "first_decoded_dimensions": first_decoded_dimensions,
        "first_display_dimensions": first_display_dimensions,
        "ordinal_position_mismatches": position_mismatches,
    }


def run_checkpoint_1b(
    project_root: Path,
    video_path: Path,
    roi_config_path: Path,
    checkpoint1a_dir: Path,
    roi_approval_path: Path,
    output_dir: Path,
    debug_output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """驗證 Frozen Baseline，固定 10 Hz 掃描全片並輸出 candidate timeline。"""
    overlay_manifest_path = checkpoint1a_dir / "roi_overlay_manifest.json"
    metadata_path = checkpoint1a_dir / "metadata.json"
    pts_index_path = checkpoint1a_dir / "frame_timestamps.npz"
    _require_files([metadata_path, pts_index_path])
    approval, manifest = validate_frozen_roi_approval(
        video_path, roi_config_path, overlay_manifest_path, roi_approval_path
    )
    roi_config, normalized_rois = load_roi_config(roi_config_path)
    metadata = load_json(metadata_path)
    if metadata.get("video_sha256") != approval["video_sha256"]:
        raise InputError("Checkpoint 1A metadata 與 Frozen Baseline video hash 不一致")
    display_dimensions = metadata.get("display_dimensions")
    if display_dimensions != roi_config.get("display_dimensions"):
        raise InputError("ROI config 與影片 display dimensions 不一致")
    converted_rois = pixel_rois(
        normalized_rois,
        int(display_dimensions["width"]),
        int(display_dimensions["height"]),
    )
    timestamp_index = load_frame_timestamp_index(pts_index_path, approval["video_sha256"])
    sample_plan = build_fixed_10hz_sample_plan(timestamp_index)
    templates = load_approved_templates(checkpoint1a_dir, manifest, converted_rois)
    detector = CandidateDetector(converted_rois, templates)
    trigger_fixture_path = (
        project_root / "references" / "trigger_notification_human_review_round1.json"
    )
    _require_files([trigger_fixture_path])
    trigger_fixture = load_trigger_round1_fixture(trigger_fixture_path)

    debug_output_dir = debug_output_dir or (output_dir.parent / "checkpoint-1b-debug")
    old_events_payload = (
        load_json(output_dir / "events.json")
        if (output_dir / "events.json").is_file()
        else {"events": []}
    )
    old_records = _read_jsonl_if_present(output_dir / "frames.jsonl")
    old_detector_report = (
        load_json(output_dir / "detector_report.json")
        if (output_dir / "detector_report.json").is_file()
        else {"thresholds": {"BATTLE_TEXT": 0.76}}
    )
    records, scan_validation = scan_video_10hz(
        video_path, metadata, timestamp_index, sample_plan, detector
    )
    events, diagnostics, trigger_diagnostics = build_event_timeline_with_all_diagnostics(
        records, scan_hz=SCAN_HZ
    )
    frames_path = output_dir / "frames.jsonl"
    events_path = output_dir / "events.json"
    event_counts = {
        event_type: sum(1 for event in events if event.type == event_type)
        for event_type in DEFAULT_THRESHOLDS
    }
    frozen_expected_counts = {
        "TEAM_PREVIEW": 1,
        "SELECTED_FOUR": 1,
        "MOVE_MENU": 31,
        "BATTLE_TEXT": 176,
        "RESULT": 1,
    }
    frozen_mismatches = {
        event_type: {"expected": expected, "actual": event_counts[event_type]}
        for event_type, expected in frozen_expected_counts.items()
        if event_counts[event_type] != expected
    }
    if frozen_mismatches:
        raise InputError(
            "TRIGGER_NOTIFICATION 修正改變 frozen event counts：{}".format(
                frozen_mismatches
            )
        )
    events_payload = {
        "schema_version": "0.1.0",
        "checkpoint": "1B",
        "kind": "event_candidates",
        "video_path": project_relative(video_path, project_root),
        "video_sha256": approval["video_sha256"],
        "sampling_hz": SCAN_HZ,
        "ocr_performed": False,
        "event_count": len(events),
        "event_counts": event_counts,
        "events": [event.to_dict() for event in events],
    }
    detector_report = {
        "schema_version": "0.1.0",
        "method": "approved_template_similarity_plus_battle_text_and_trigger_structure_proposals",
        "ai_or_ocr_used": False,
        "thresholds": DEFAULT_THRESHOLDS,
        "battle_text_proposal_config": DEFAULT_BATTLE_TEXT_CONFIG.to_dict(),
        "battle_text_temporal_config": DEFAULT_BATTLE_TEXT_TEMPORAL_CONFIG.to_dict(),
        "trigger_notification_proposal_config": DEFAULT_TRIGGER_PROPOSAL_CONFIG.to_dict(),
        "trigger_notification_temporal_config": DEFAULT_TRIGGER_TEMPORAL_CONFIG.to_dict(),
        "battle_text_cooldown_present": False,
        "battle_text_suppression_present": False,
        "battle_text_duration_filter_present": False,
        "templates": {
            event_type: [template.source_id for template in event_templates]
            for event_type, event_templates in templates.items()
        },
    }
    report = {
        "schema_version": "0.1.0",
        "checkpoint": "1B",
        "status": "complete",
        "sampling_hz": SCAN_HZ,
        "full_video_scanned": True,
        "ocr_performed": False,
        "pts_authority": "ffprobe.best_effort_timestamp_time",
        "roi_config_path": project_relative(roi_config_path, project_root),
        "roi_config_sha256": approval["roi_config_sha256"],
        "roi_approval_path": project_relative(roi_approval_path, project_root),
        "roi_approval_sha256": sha256_file(roi_approval_path),
        "frame_metadata_path": project_relative(frames_path, project_root),
        "events_path": project_relative(events_path, project_root),
        "counts": {
            "source_frames": timestamp_index.frame_count,
            "sampled_frames": len(records),
            "event_candidates": len(events),
        },
        "event_counts": event_counts,
        "scan_validation": scan_validation,
        "next_checkpoint": "先完成人工 Recall Gate；本流程未開始 Checkpoint 1C",
    }
    diagnostic_report = build_detector_diagnostic_report(
        old_events=old_events_payload.get("events", []),
        old_records=old_records,
        old_threshold=float(
            old_detector_report.get("thresholds", {}).get("BATTLE_TEXT", 0.76)
        ),
        new_events=events,
        new_records=records,
        diagnostics=diagnostics,
        proposal_config=DEFAULT_BATTLE_TEXT_CONFIG.to_dict(),
        temporal_config=DEFAULT_BATTLE_TEXT_TEMPORAL_CONFIG.to_dict(),
    )
    diagnostic_report["diagnostics_path"] = project_relative(
        debug_output_dir / "battle_text_diagnostics.jsonl", project_root
    )
    trigger_audit_report = build_trigger_round1_comparison(
        fixture=trigger_fixture,
        old_events=old_events_payload.get("events", []),
        old_records=old_records,
        new_events=[event.to_dict() for event in events],
        new_diagnostics=trigger_diagnostics,
    )
    trigger_audit_report["diagnostics_path"] = project_relative(
        debug_output_dir / "trigger_notification_diagnostics.jsonl", project_root
    )
    if not trigger_audit_report["required_positive_coverage"]["all_covered"]:
        raise InputError("Trigger round-1 required positive windows 未全部覆蓋")

    frame_rows = [record.to_dict() for record in records]
    frame_validator = _schema_validator(project_root, "frame_metadata.schema.json")
    event_validator = _schema_validator(project_root, "events.schema.json")
    diagnostic_validator = _schema_validator(
        project_root, "battle_text_diagnostic.schema.json"
    )
    trigger_diagnostic_validator = _schema_validator(
        project_root, "trigger_notification_diagnostic.schema.json"
    )
    for row in frame_rows:
        frame_validator.validate(row)
    event_validator.validate(events_payload)
    for row in diagnostics:
        diagnostic_validator.validate(row)
    for row in trigger_diagnostics:
        trigger_diagnostic_validator.validate(row)

    with OutputTransaction(project_root, output_dir) as output_transaction:
        with OutputTransaction(project_root, debug_output_dir) as debug_transaction:
            staged_frames = output_transaction.staging_dir / "frames.jsonl"
            staged_events = output_transaction.staging_dir / "events.json"
            _write_jsonl(staged_frames, frame_rows)
            write_json(staged_events, events_payload)
            write_json(output_transaction.staging_dir / "detector_report.json", detector_report)
            write_json(output_transaction.staging_dir / "checkpoint_1b_report.json", report)
            _write_jsonl(
                debug_transaction.staging_dir / "battle_text_diagnostics.jsonl",
                diagnostics,
            )
            _write_jsonl(
                debug_transaction.staging_dir
                / "trigger_notification_diagnostics.jsonl",
                trigger_diagnostics,
            )
            write_json(
                debug_transaction.staging_dir / "battle_text_detector_report.json",
                diagnostic_report,
            )
            write_json(
                debug_transaction.staging_dir
                / "trigger_notification_audit_report.json",
                trigger_audit_report,
            )
            if len(_read_jsonl_if_present(staged_frames)) != len(records):
                raise InputError("Checkpoint 1B staged frames 數量驗證失敗")
            if len(
                _read_jsonl_if_present(
                    debug_transaction.staging_dir / "battle_text_diagnostics.jsonl"
                )
            ) != len(records):
                raise InputError("BATTLE_TEXT staged diagnostics 數量驗證失敗")
            if len(
                _read_jsonl_if_present(
                    debug_transaction.staging_dir
                    / "trigger_notification_diagnostics.jsonl"
                )
            ) != len(records) * 2:
                raise InputError("TRIGGER_NOTIFICATION staged diagnostics 數量驗證失敗")
            debug_transaction.commit()
            output_transaction.commit()
    finalize_generated_output(debug_output_dir)
    finalize_generated_output(output_dir)
    return report
