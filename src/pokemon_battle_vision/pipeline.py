"""Checkpoint 1A 唯一資料流；ROI overlay 後即停在人工核准 gate。"""

import importlib.metadata
from pathlib import Path
from typing import Any, Dict, List, Sequence

import cv2
import numpy as np

from .config import SUPPORTED_PROFILE, load_json, load_known_frames, load_roi_config
from .contact_sheet import build_contact_sheets
from .errors import (
    CompatibilityError,
    DecodeAlignmentError,
    InputError,
    TimestampIndexError,
)
from .image_io import detect_image_format_bytes, read_image, write_image
from .media_probe import (
    dependency_preflight,
    probe_frame_timestamps,
    probe_metadata,
    save_frame_timestamp_index,
)
from .roi import draw_roi_overlay, pixel_rois
from .sampling import fixed_interval_targets
from .utils import project_relative, sha256_file, write_json
from .video import decode_and_extract


REQUIRED_SCREENSHOTS = (
    "player_team_details.jpeg",
    "team_preview.jpeg",
    "selected_four.jpeg",
    "move_selection_player_left.jpeg",
    "move_selection_player_right.jpeg",
    "battle_text.jpeg",
    "result.jpeg",
)

STATE_ROIS = {
    "TEAM_PREVIEW": ["team_preview_player", "team_preview_opponent"],
    "PLAYER_FOUR_CONFIRMED": ["selected_four"],
    "MOVE_SELECTION_PLAYER_LEFT": ["player_status", "opponent_status", "move_menu"],
    "MOVE_SELECTION_PLAYER_RIGHT": ["player_status", "opponent_status", "move_menu"],
    "BATTLE_TEXT": ["battle_text"],
    "RESULT": [
        "result_player_banner",
        "result_opponent_banner",
        "result_player_name",
        "result_opponent_name",
    ],
}


def _validate_inputs(paths: Sequence[Path]) -> None:
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise InputError("缺少必要輸入：{}".format("、".join(missing)))


def _prepare_empty_output(output_dir: Path) -> None:
    if output_dir.exists():
        existing = list(output_dir.iterdir())
        if existing:
            raise InputError(
                "輸出目錄必須為空，避免混用舊證據：{}（目前有 {} 個項目）".format(
                    output_dir, len(existing)
                )
            )
    output_dir.mkdir(parents=True, exist_ok=True)


def _image_and_provenance_report(
    screenshots_dir: Path,
    known_frames_data: Dict[str, Any],
    match_reference_path: Path,
) -> Dict[str, Any]:
    match_reference = load_json(match_reference_path)
    image_rows = []
    by_name = {}
    for filename in REQUIRED_SCREENSHOTS:
        path = screenshots_dir / filename
        _, report = read_image(path)
        report["path"] = str(path)
        image_rows.append(report)
        by_name[filename] = report

    known_reference_names = {
        Path(row["reference_image"]).name for row in known_frames_data["known_frames"]
    }
    expected_video_reference_names = set(REQUIRED_SCREENSHOTS).difference({"player_team_details.jpeg"})
    if known_reference_names != expected_video_reference_names:
        raise InputError(
            "known_frames reference_image 與六張影片 state screenshots 不一致：{}".format(
                sorted(known_reference_names)
            )
        )
    sources = match_reference.get("sources")
    player_team = match_reference.get("player_team")
    provenance_source = sources.get("player_team_details") if isinstance(sources, dict) else None
    provenance_valid = (
        isinstance(provenance_source, str)
        and bool(provenance_source.strip())
        and isinstance(player_team, list)
        and len(player_team) == 6
        and by_name["player_team_details.jpeg"]["readable"]
    )
    if not provenance_valid:
        raise InputError("player team reference 與 match reference provenance 無法驗證")
    return {
        "schema_version": "0.1.0",
        "status": "pass",
        "images": image_rows,
        "warning_count": sum(len(row["warnings"]) for row in image_rows),
        "player_team_details_provenance": {
            "status": "validated",
            "dataset_id": match_reference.get("dataset_id"),
            "match_reference_path": str(match_reference_path),
            "match_reference_sha256": sha256_file(match_reference_path),
            "source_description": provenance_source,
            "image_path": str(screenshots_dir / "player_team_details.jpeg"),
            "image_sha256": by_name["player_team_details.jpeg"]["sha256"],
            "player_team_record_count": len(player_team),
            "role": "future_ocr_sample",
            "belongs_to_video_timeline": False,
            "has_video_roi": False,
        },
    }


def _update_metadata_from_alignment(metadata: Dict[str, Any], report: Dict[str, Any]) -> None:
    metadata["opencv_decoded_dimensions"] = report["first_decoded_dimensions"]
    metadata["opencv_display_dimensions_after_manual_rotation"] = report["first_display_dimensions"]
    metadata["opencv_backend"] = report["opencv_backend"]
    metadata["opencv_orientation_auto_disabled"] = report["orientation_auto_disabled"]
    metadata["opencv_decoded_frame_count"] = report["opencv_decoded_frame_count"]
    metadata["ffprobe_opencv_ordinal_alignment"] = report["status"]


def run_checkpoint_1a(
    project_root: Path,
    video_path: Path,
    known_frames_path: Path,
    match_reference_path: Path,
    screenshots_dir: Path,
    roi_config_path: Path,
    output_dir: Path,
    interval_sec: float = 30.0,
    dependency_timeout_sec: float = 15.0,
    ffprobe_timeout_sec: float = 240.0,
) -> Dict[str, Any]:
    """產生 1A 全部證據；成功結果的 ROI gate 必定仍是 pending。"""
    if interval_sec <= 0:
        raise InputError("--interval-sec 必須大於 0")
    _validate_inputs([video_path, known_frames_path, match_reference_path, roi_config_path])
    _validate_inputs([screenshots_dir / filename for filename in REQUIRED_SCREENSHOTS])
    _prepare_empty_output(output_dir)

    environment = dependency_preflight(timeout_sec=dependency_timeout_sec)
    environment["python_packages"] = {
        "numpy": np.__version__,
        "opencv-python-headless": importlib.metadata.version("opencv-python-headless"),
        "cv2_module": cv2.__version__,
        "jsonschema": importlib.metadata.version("jsonschema"),
    }
    environment["expected_versions"] = {
        "python": "3.9.6",
        "numpy": "2.0.2",
        "opencv-python-headless": "4.13.0.92",
        "jsonschema": "4.23.0",
    }
    write_json(output_dir / "environment_report.json", environment)

    video_hash = sha256_file(video_path)
    metadata = probe_metadata(
        video_path,
        video_hash,
        SUPPORTED_PROFILE,
        environment,
        timeout_sec=ffprobe_timeout_sec,
    )
    compatibility = {
        "schema_version": "0.1.0",
        "status": "pass" if metadata["expected_resolution_match"] else "failed",
        "profile": SUPPORTED_PROFILE.to_dict(),
        "encoded_dimensions": metadata["encoded_dimensions"],
        "rotation": metadata["rotation"],
        "display_dimensions": metadata["display_dimensions"],
        "gate_action": (
            "continue_checkpoint_1a"
            if metadata["expected_resolution_match"]
            else "stop_before_pts_roi_anchors_and_video_analysis"
        ),
        "silent_resize_performed": False,
    }
    write_json(output_dir / "metadata.json", metadata)
    write_json(output_dir / "compatibility_report.json", compatibility)
    if not metadata["expected_resolution_match"]:
        raise CompatibilityError(
            "display resolution {}×{} 不符合唯一支援規格 {}×{}；已寫出 metadata/mismatch 報告，未執行 ROI 或 anchors。".format(
                metadata["display_dimensions"]["width"],
                metadata["display_dimensions"]["height"],
                SUPPORTED_PROFILE.display_width,
                SUPPORTED_PROFILE.display_height,
            )
        )

    known_frames_data, anchor_definitions = load_known_frames(known_frames_path)
    input_report = _image_and_provenance_report(
        screenshots_dir, known_frames_data, match_reference_path
    )
    write_json(output_dir / "input_image_report.json", input_report)

    timestamp_index = probe_frame_timestamps(
        video_path,
        video_hash,
        environment,
        timeout_sec=ffprobe_timeout_sec,
    )
    save_frame_timestamp_index(output_dir / "frame_timestamps.npz", timestamp_index)
    pts_validation = timestamp_index.validation
    write_json(output_dir / "pts_validation_report.json", pts_validation)
    metadata["pts_index_frame_count"] = timestamp_index.frame_count
    metadata["vfr"] = bool(pts_validation["vfr_diagnostics"]["is_vfr"])
    metadata["vfr_diagnostics"] = pts_validation["vfr_diagnostics"]
    write_json(output_dir / "metadata.json", metadata)
    if not pts_validation["complete"] or not pts_validation["strictly_monotonic"]:
        raise TimestampIndexError(
            "frame PTS index 驗證失敗：missing={}、duplicate={}、non-monotonic={}".format(
                pts_validation["missing_count"],
                pts_validation["duplicate_count"],
                pts_validation["non_monotonic_count"],
            )
        )

    targets = fixed_interval_targets(
        float(timestamp_index.pts_sec[0]),
        float(timestamp_index.pts_sec[-1]),
        interval_sec,
    )
    target_by_ordinal: Dict[int, float] = {}
    for target in targets:
        ordinal = timestamp_index.nearest_ordinal(target)
        if ordinal not in target_by_ordinal:
            target_by_ordinal[ordinal] = target

    anchor_reference_images = {}
    for definition in anchor_definitions:
        reference_path = Path(definition.reference_image)
        if not reference_path.is_absolute():
            reference_path = project_root / reference_path
        anchor_reference_images[definition.anchor_id] = read_image(reference_path)[0]

    try:
        extraction = decode_and_extract(
            video_path,
            metadata,
            timestamp_index,
            anchor_definitions,
            sorted(target_by_ordinal),
            environment["versions"]["ffmpeg"],
            environment["versions"]["ffprobe"],
            anchor_reference_images=anchor_reference_images,
        )
    except DecodeAlignmentError as exc:
        report_object = getattr(exc, "report", None)
        if report_object is not None:
            report = report_object.to_dict()
            write_json(output_dir / "decode_alignment_report.json", report)
            _update_metadata_from_alignment(metadata, report)
            write_json(output_dir / "metadata.json", metadata)
        raise

    alignment_report = extraction.report.to_dict()
    write_json(output_dir / "decode_alignment_report.json", alignment_report)
    _update_metadata_from_alignment(metadata, alignment_report)
    write_json(output_dir / "metadata.json", metadata)

    contact_dir = output_dir / "contact_frames"
    contact_dir.mkdir(parents=True, exist_ok=True)
    contact_items = []
    for ordinal in sorted(extraction.contact_png_bytes):
        pts_sec = float(timestamp_index.pts_sec[ordinal])
        filename = "frame_{:06d}__t_{:010.3f}.png".format(ordinal, pts_sec)
        path = contact_dir / filename
        data = extraction.contact_png_bytes[ordinal]
        path.write_bytes(data)
        if detect_image_format_bytes(path.read_bytes()[:8]) != "png":
            raise InputError("contact frame 寫入後 magic bytes 驗證失敗：{}".format(path))
        contact_items.append(
            {
                "ordinal": ordinal,
                "pts_sec": pts_sec,
                "target_sec": float(target_by_ordinal[ordinal]),
                "delta_sec": pts_sec - float(target_by_ordinal[ordinal]),
                "path": project_relative(path, output_dir),
                "absolute_path": str(path.resolve()),
                "sha256": sha256_file(path),
                "encoding": "png",
            }
        )
    contact_index = build_contact_sheets(contact_items, output_dir / "contact_sheets")
    contact_index["sampling"] = {
        "authority": "ffprobe.best_effort_timestamp_time",
        "interval_sec": interval_sec,
        "first_pts_sec": float(timestamp_index.pts_sec[0]),
        "last_pts_sec": float(timestamp_index.pts_sec[-1]),
        "tie_break": "earlier_ordinal",
    }
    contact_index["frames"] = [
        {key: value for key, value in item.items() if key != "absolute_path"}
        for item in contact_items
    ]
    for page in contact_index["pages"]:
        page["path"] = "contact_sheets/{}".format(page["path"])
    write_json(output_dir / "contact_sheet_index.json", contact_index)

    anchor_dir = output_dir / "anchors"
    anchor_dir.mkdir(parents=True, exist_ok=True)
    anchor_rows = []
    anchor_by_id = {}
    definitions_by_id = {row.anchor_id: row for row in anchor_definitions}
    for anchor_id, selected in extraction.anchors.items():
        definition = definitions_by_id[anchor_id]
        delta = selected.pts_sec - definition.target_sec
        filename = "{}__frame_{:06d}__t_{:010.3f}.png".format(
            anchor_id, selected.ordinal, selected.pts_sec
        )
        path = anchor_dir / filename
        file_report = write_image(path, selected.image)
        row = {
            "id": anchor_id,
            "state": definition.state,
            "target_sec": definition.target_sec,
            "tolerance_sec": definition.tolerance_sec,
            "actual_pts_sec": selected.pts_sec,
            "delta_sec": delta,
            "within_tolerance": abs(delta) <= definition.tolerance_sec,
            "ordinal": selected.ordinal,
            "selection": {
                "method": "PTS tolerance window + reference difference + temporal stability + clarity",
                "motion_score": selected.motion_score,
                "sharpness_score": selected.sharpness_score,
                "reference_difference_score": selected.reference_difference_score,
                "selection_score": selected.selection_score,
            },
            "reference_image": definition.reference_image,
            "description": definition.description,
            "path": project_relative(path, output_dir),
            "sha256": file_report["sha256"],
            "encoding": "png",
        }
        if not row["within_tolerance"]:
            raise InputError("anchor {} 未落入 tolerance".format(anchor_id))
        anchor_rows.append(row)
        anchor_by_id[anchor_id] = (row, selected)
    anchor_rows.sort(key=lambda row: row["target_sec"])
    anchor_report = {
        "schema_version": "0.1.0",
        "status": "pass" if len(anchor_rows) == 6 else "failed",
        "authority": "ffprobe.best_effort_timestamp_time",
        "selection_policy": "known timestamps 僅作 validation window；不參與 segmentation、threshold 或分析範圍",
        "anchor_count": len(anchor_rows),
        "all_within_tolerance": all(row["within_tolerance"] for row in anchor_rows),
        "anchors": anchor_rows,
    }
    write_json(output_dir / "anchor_report.json", anchor_report)

    roi_config_data, normalized_rois = load_roi_config(roi_config_path)
    config_dimensions = roi_config_data.get("display_dimensions")
    if config_dimensions != metadata["display_dimensions"]:
        raise InputError("ROI config display_dimensions 與影片 display dimensions 不一致")
    converted_rois = pixel_rois(
        normalized_rois,
        metadata["display_dimensions"]["width"],
        metadata["display_dimensions"]["height"],
    )
    conversion_report = {
        "schema_version": "0.1.0",
        "frame_dimensions": metadata["display_dimensions"],
        "rounding_policy": "floor start, ceil end, clamp to frame",
        "rois": [
            {
                "id": roi_id,
                "normalized": normalized_rois[roi_id].to_dict(),
                "pixel": converted_rois[roi_id].to_dict(),
            }
            for roi_id in normalized_rois
        ],
    }
    write_json(output_dir / "roi_pixel_conversion.json", conversion_report)

    overlay_dir = output_dir / "roi_overlays"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    overlay_rows: List[Dict[str, Any]] = []
    for anchor_row in anchor_rows:
        anchor_id = anchor_row["id"]
        state = anchor_row["state"]
        roi_ids = STATE_ROIS.get(state)
        if not roi_ids:
            raise InputError("state {} 沒有 ROI overlay mapping".format(state))
        missing_rois = [roi_id for roi_id in roi_ids if roi_id not in converted_rois]
        if missing_rois:
            raise InputError("ROI config 缺少 state {} 所需 ROI：{}".format(state, missing_rois))
        selected = anchor_by_id[anchor_id][1]
        overlaid = draw_roi_overlay(selected.image, [converted_rois[roi_id] for roi_id in roi_ids])
        path = overlay_dir / "{}__roi_overlay.png".format(anchor_id)
        file_report = write_image(path, overlaid)
        overlay_rows.append(
            {
                "id": anchor_id,
                "state": state,
                "path": project_relative(path, output_dir),
                "sha256": file_report["sha256"],
                "encoding": "png",
                "source": {
                    "type": "raw_video_frame_after_explicit_rotation",
                    "anchor_path": anchor_row["path"],
                    "frame_ordinal": anchor_row["ordinal"],
                    "pts_sec": anchor_row["actual_pts_sec"],
                },
                "roi_ids": roi_ids,
                "pixel_rois": [converted_rois[roi_id].to_dict() for roi_id in roi_ids],
            }
        )
    overlay_manifest = {
        "schema_version": "0.1.0",
        "status": "pending_human_approval",
        "checkpoint": "1A",
        "video_path": str(video_path),
        "video_sha256": video_hash,
        "roi_config_path": str(roi_config_path),
        "roi_config_sha256": sha256_file(roi_config_path),
        "roi_config_calibration_revision": roi_config_data.get("calibration_revision"),
        "display_dimensions": metadata["display_dimensions"],
        "design_reference_is_ground_truth": False,
        "raw_video_overlay_count": len(overlay_rows),
        "overlays": overlay_rows,
        "approval": {
            "required_before_checkpoint_1b": True,
            "approval_file": "roi_approval.json",
            "current_status": "not_approved",
        },
    }
    write_json(output_dir / "roi_overlay_manifest.json", overlay_manifest)

    report = {
        "schema_version": "0.1.0",
        "checkpoint": "1A",
        "status": "complete_pending_roi_approval",
        "checkpoint_1b_executed": False,
        "video_sha256": video_hash,
        "checks": {
            "dependency_preflight": "pass",
            "display_compatibility": "pass",
            "image_magic_bytes_and_provenance": "pass",
            "pts_validation": "pass",
            "opencv_ffprobe_ordinal_alignment": "pass",
            "six_known_anchors": "pass",
            "fixed_interval_contact_sheets": "pass",
            "raw_video_roi_overlays": "pass",
            "roi_human_approval": "pending",
        },
        "counts": {
            "pts_frames": timestamp_index.frame_count,
            "opencv_decoded_frames": extraction.report.opencv_decoded_frame_count,
            "anchors": len(anchor_rows),
            "contact_frames": len(contact_items),
            "contact_sheets": contact_index["page_count"],
            "roi_overlays": len(overlay_rows),
        },
        "next_action": "人工檢查 roi_overlays 與 roi_overlay_manifest.json；確認後另行明確執行 approve-roi。不得開始 Checkpoint 1B。",
    }
    write_json(output_dir / "checkpoint_1a_report.json", report)
    return report
