"""依 ffprobe PTS／ordinal 順序解碼 Review Pack 所需視覺證據。"""

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, DefaultDict, Dict, List, Mapping, Optional, Sequence, Set, Tuple

import cv2
import numpy as np

from .errors import DecodeAlignmentError, InputError
from .image_io import encode_image
from .models import FrameTimestampIndex, PixelRoi
from .review_pack_models import (
    CandidateEvidencePoint,
    CandidateFrameSelection,
    CoverageSample,
    EncodedFrameEvidence,
)
from .sampling import fixed_interval_targets
from .video import rotate_frame_clockwise
from .trigger_notification_features import (
    TRIGGER_ANALYSIS_ROIS,
    TRIGGER_SIDE_ROIS,
)


EVENT_REVIEW_ROIS: Dict[str, Tuple[str, ...]] = {
    "TEAM_PREVIEW": ("team_preview_player", "team_preview_opponent"),
    # Frozen config 的正式 ID 是 selected_four；不建立新的 alias ROI。
    "SELECTED_FOUR": ("selected_four",),
    "MOVE_MENU": ("player_status", "opponent_status", "move_menu"),
    "BATTLE_TEXT": ("battle_text",),
    "TRIGGER_NOTIFICATION": (
        "player_trigger_notification",
        "opponent_trigger_notification",
    ),
    "RESULT": (
        "result_player_banner",
        "result_opponent_banner",
        "result_player_name",
        "result_opponent_name",
    ),
}


def load_frame_records(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        raise InputError("找不到 Checkpoint 1B frame metadata：{}".format(path))
    rows: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise InputError("frames.jsonl 第 {} 列不是 object".format(line_number))
                rows.append(row)
    except json.JSONDecodeError as exc:
        raise InputError("frames.jsonl 含無效 JSON：{}".format(exc)) from exc
    if not rows:
        raise InputError("frames.jsonl 沒有 frame records")
    sample_indices = [int(row["sample_index"]) for row in rows]
    if sample_indices != list(range(len(rows))):
        raise InputError("frames.jsonl sample_index 必須由 0 連續遞增")
    frame_indices = [int(row["frame_index"]) for row in rows]
    if frame_indices != sorted(frame_indices):
        raise InputError("frames.jsonl frame_index 必須按時間遞增")
    return rows


def roi_ids_for_event(event: Mapping[str, Any]) -> List[str]:
    event_type = str(event.get("type", ""))
    if event_type not in EVENT_REVIEW_ROIS:
        raise InputError("Review Pack 不支援 event type：{}".format(event_type))
    if event_type != "TRIGGER_NOTIFICATION":
        return list(EVENT_REVIEW_ROIS[event_type])
    visible = event.get("visible_rois")
    if not isinstance(visible, list):
        raise InputError("TRIGGER_NOTIFICATION 缺少 visible_rois")
    allowed = set(EVENT_REVIEW_ROIS[event_type])
    actual = [str(roi_id) for roi_id in visible if str(roi_id) in allowed]
    if not actual:
        raise InputError("TRIGGER_NOTIFICATION 沒有實際可見的 trigger ROI")
    return actual


def select_candidate_frames(
    event: Mapping[str, Any],
    frame_records: Sequence[Mapping[str, Any]],
    timestamp_index: FrameTimestampIndex,
    diagnostics_by_frame: Optional[Mapping[int, Mapping[str, Any]]] = None,
    trigger_diagnostics_by_frame_side: Optional[
        Mapping[Tuple[int, str], Mapping[str, Any]]
    ] = None,
) -> CandidateFrameSelection:
    candidate_id = str(event["event_id"])
    start_frame = int(event["start_frame"])
    end_frame = int(event["end_frame"])
    start_time = float(event["start_time"])
    end_time = float(event["end_time"])
    if start_frame > end_frame or start_time > end_time:
        raise InputError("candidate {} 的開始晚於結束".format(candidate_id))
    if start_frame < 0 or end_frame >= timestamp_index.frame_count:
        raise InputError("candidate {} 的 frame ordinal 超出 PTS index".format(candidate_id))
    if abs(float(timestamp_index.pts_sec[start_frame]) - start_time) > 1e-6:
        raise InputError("candidate {} start frame／PTS 不一致".format(candidate_id))
    if abs(float(timestamp_index.pts_sec[end_frame]) - end_time) > 1e-6:
        raise InputError("candidate {} end frame／PTS 不一致".format(candidate_id))

    by_frame = {int(row["frame_index"]): row for row in frame_records}
    if start_frame not in by_frame or end_frame not in by_frame:
        raise InputError("candidate {} 的 start/end 不在 frames.jsonl".format(candidate_id))
    within = [
        row
        for row in frame_records
        if start_frame <= int(row["frame_index"]) <= end_frame
        and start_time <= float(row["pts"]) <= end_time
    ]
    if not within:
        raise InputError("candidate {} 區間內沒有 sampled frame".format(candidate_id))
    midpoint = (start_time + end_time) / 2.0
    middle = min(
        within,
        key=lambda row: (abs(float(row["pts"]) - midpoint), int(row["frame_index"])),
    )
    event_type = str(event["type"])
    trigger_side = ""
    if event_type == "TRIGGER_NOTIFICATION":
        visible = set(roi_ids_for_event(event))
        trigger_side = next(
            side
            for side, roi_id in TRIGGER_SIDE_ROIS.items()
            if roi_id in visible
        )

    def point(row: Mapping[str, Any], roles: Sequence[str]) -> CandidateEvidencePoint:
        frame_index = int(row["frame_index"])
        scores = row.get("candidate_scores", {})
        evidence = row.get("battle_text_evidence", {})
        diagnostic = (diagnostics_by_frame or {}).get(frame_index, {})
        trigger_root = row.get("trigger_notification_evidence", {})
        trigger_sides = (
            trigger_root.get("sides", {}) if isinstance(trigger_root, dict) else {}
        )
        trigger_evidence = (
            trigger_sides.get(trigger_side, {})
            if isinstance(trigger_sides, dict) and trigger_side
            else {}
        )
        trigger_diagnostic = (trigger_diagnostics_by_frame_side or {}).get(
            (frame_index, trigger_side), {}
        )
        point_evidence_level = str(
            trigger_evidence.get("evidence_level", "negative")
            if event_type == "TRIGGER_NOTIFICATION"
            else (
                evidence.get("evidence_level")
                or diagnostic.get("evidence_level")
                or "not_applicable"
            )
        )
        point_decision = str(
            trigger_diagnostic.get("decision", "not_applicable")
            if event_type == "TRIGGER_NOTIFICATION"
            else diagnostic.get("decision", "not_applicable")
        )
        return CandidateEvidencePoint(
            roles=tuple(roles),
            frame_index=frame_index,
            pts=float(row["pts"]),
            score=float(scores.get(event_type, scores.get("BATTLE_TEXT", 0.0))),
            text_structure_strength=float(
                trigger_evidence.get("text_score", 0.0)
                if event_type == "TRIGGER_NOTIFICATION"
                else (
                    evidence.get("text_line_strength")
                    or evidence.get("visual_structure_strength")
                    or 0.0
                )
            ),
            evidence_level=point_evidence_level,
            decision=point_decision,
            side=trigger_side,
            panel_score=float(trigger_evidence.get("panel_score", 0.0)),
            text_score=float(trigger_evidence.get("text_score", 0.0)),
            icon_score=float(trigger_evidence.get("icon_score", 0.0)),
            combined_score=float(trigger_evidence.get("combined_score", 0.0)),
            analysis_roi_id=str(trigger_evidence.get("analysis_roi_id", "")),
        )

    if event_type == "TRIGGER_NOTIFICATION":
        positive = [
            row
            for row in within
            if str(
                row.get("trigger_notification_evidence", {})
                .get("sides", {})
                .get(trigger_side, {})
                .get("evidence_level", "negative")
            )
            in ("strong", "weak")
        ]
        ranked = positive or within
        representative = max(
            ranked,
            key=lambda row: (
                float(
                    row.get("trigger_notification_evidence", {})
                    .get("sides", {})
                    .get(trigger_side, {})
                    .get("combined_score", 0.0)
                ),
                float(row.get("candidate_scores", {}).get(event_type, 0.0)),
                -int(row["frame_index"]),
            ),
        )
        roles_by_frame: Dict[int, List[str]] = {}
        roles_by_frame.setdefault(start_frame, []).append("start")
        roles_by_frame.setdefault(int(representative["frame_index"]), []).append(
            "peak_evidence"
        )
        roles_by_frame.setdefault(end_frame, []).append("end")
        points = tuple(
            point(by_frame[index], roles)
            for index, roles in sorted(roles_by_frame.items())
        )
        strategy = "trigger_notification_peak_evidence_and_boundaries"
    elif event_type != "BATTLE_TEXT":
        roles_by_frame: Dict[int, List[str]] = {}
        roles_by_frame.setdefault(start_frame, []).append("start")
        roles_by_frame.setdefault(int(middle["frame_index"]), []).append("middle")
        roles_by_frame.setdefault(end_frame, []).append("end")
        points = tuple(point(by_frame[index], roles) for index, roles in roles_by_frame.items())
        representative = middle
        strategy = "start_middle_end"
    else:
        strong = [
            row
            for row in within
            if str(row.get("battle_text_evidence", {}).get("evidence_level", ""))
            == "strong"
            or bool(row.get("battle_text_evidence", {}).get("strong_positive"))
        ]
        ranked = strong or within
        representative = max(
            ranked,
            key=lambda row: (
                0.7 * float(row.get("candidate_scores", {}).get("BATTLE_TEXT", 0.0))
                + 0.3
                * float(
                    row.get("battle_text_evidence", {}).get("text_line_strength")
                    or row.get("battle_text_evidence", {}).get(
                        "visual_structure_strength", 0.0
                    )
                ),
                float(row.get("candidate_scores", {}).get("BATTLE_TEXT", 0.0)),
                -int(row["frame_index"]),
            ),
        )
        roles_by_frame: Dict[int, List[str]] = {}

        def add_role(row: Mapping[str, Any], role: str) -> None:
            roles_by_frame.setdefault(int(row["frame_index"]), []).append(role)

        add_role(by_frame[start_frame], "start")
        if strong:
            add_role(strong[0], "first_strong_positive")
        else:
            add_role(by_frame[start_frame], "first_strong_positive_unavailable")
        add_role(representative, "peak_score_structure")
        if strong:
            add_role(strong[-1], "last_strong_positive")
        else:
            add_role(by_frame[end_frame], "last_strong_positive_unavailable")
        add_role(by_frame[end_frame], "end")
        if end_time - start_time > 3.0:
            strip_targets = np.arange(start_time, end_time + 1e-9, 0.5).tolist()
            if len(strip_targets) > 10:
                keep = np.linspace(0, len(strip_targets) - 1, 10).round().astype(int)
                strip_targets = [strip_targets[index] for index in keep]
            for target in strip_targets:
                strip_row = min(
                    within,
                    key=lambda row: (
                        abs(float(row["pts"]) - float(target)),
                        int(row["frame_index"]),
                    ),
                )
                add_role(strip_row, "evidence_strip")
        points = tuple(
            point(by_frame[index], roles)
            for index, roles in sorted(roles_by_frame.items())
        )
        strategy = "battle_text_peak_structure_and_boundaries"
    return CandidateFrameSelection(
        candidate_id=candidate_id,
        start_frame=start_frame,
        middle_frame=int(middle["frame_index"]),
        end_frame=end_frame,
        start_pts=start_time,
        middle_pts=float(middle["pts"]),
        end_pts=end_time,
        representative_frame=int(representative["frame_index"]),
        representative_pts=float(representative["pts"]),
        strategy=strategy,
        evidence_points=points,
    )


def build_coverage_samples(
    timestamp_index: FrameTimestampIndex,
    events: Sequence[Mapping[str, Any]],
    interval_sec: float,
    candidate_type: str = "BATTLE_TEXT",
) -> List[CoverageSample]:
    if interval_sec <= 0:
        raise InputError("--coverage-interval-sec 必須大於 0")
    targets = fixed_interval_targets(
        float(timestamp_index.pts_sec[0]),
        float(timestamp_index.pts_sec[-1]),
        interval_sec,
    )
    samples = []
    for sample_index, target in enumerate(targets):
        frame_index = timestamp_index.nearest_ordinal(target)
        pts = float(timestamp_index.pts_sec[frame_index])
        overlapping = [
            event
            for event in events
            if str(event["type"]) == candidate_type
            and float(event["start_time"]) <= pts <= float(event["end_time"])
        ]
        samples.append(
            CoverageSample(
                sample_index=sample_index,
                target_time=round(float(target), 6),
                frame_index=frame_index,
                pts=round(pts, 6),
                candidate_ids=[str(event["event_id"]) for event in overlapping],
                candidate_types=[str(event["type"]) for event in overlapping],
            )
        )
    return samples


def build_evidence_requests(
    events: Sequence[Mapping[str, Any]],
    selections: Mapping[str, CandidateFrameSelection],
    coverage_samples: Sequence[CoverageSample],
    coverage_roi_ids: Sequence[str] = ("battle_text",),
    dense_diagnostics: Sequence[Mapping[str, Any]] = (),
) -> Tuple[Dict[int, Set[str]], Set[int]]:
    roi_requests: DefaultDict[int, Set[str]] = defaultdict(set)
    full_frame_requests: Set[int] = set()
    for event in events:
        candidate_id = str(event["event_id"])
        selection = selections[candidate_id]
        roi_ids = roi_ids_for_event(event)
        if str(event.get("type")) == "TRIGGER_NOTIFICATION":
            visible = set(roi_ids)
            roi_ids = list(roi_ids) + [
                TRIGGER_ANALYSIS_ROIS[side]
                for side, canonical_id in TRIGGER_SIDE_ROIS.items()
                if canonical_id in visible
            ]
        for point in selection.evidence_points:
            frame_index = point.frame_index
            full_frame_requests.add(frame_index)
            roi_requests[frame_index].update(roi_ids)
    for sample in coverage_samples:
        full_frame_requests.add(sample.frame_index)
        roi_requests[sample.frame_index].update(coverage_roi_ids)
    for row in dense_diagnostics:
        frame_index = int(row["frame_ordinal"])
        roi_requests[frame_index].add("battle_text")
    return dict(roi_requests), full_frame_requests


def _resize_within(image: np.ndarray, max_width: int, max_height: int) -> np.ndarray:
    height, width = image.shape[:2]
    scale = min(max_width / float(width), max_height / float(height), 1.0)
    target = (max(1, int(round(width * scale))), max(1, int(round(height * scale))))
    if target == (width, height):
        return image
    return cv2.resize(image, target, interpolation=cv2.INTER_AREA)


def extract_review_evidence(
    video_path: Path,
    metadata: Mapping[str, Any],
    timestamp_index: FrameTimestampIndex,
    pixel_rois: Mapping[str, PixelRoi],
    roi_requests: Mapping[int, Set[str]],
    full_frame_requests: Set[int],
) -> Tuple[Dict[int, EncodedFrameEvidence], Dict[str, Any]]:
    if not video_path.is_file():
        raise InputError("找不到 Review Pack 影片：{}".format(video_path))
    requested = set(full_frame_requests).union(roi_requests)
    if not requested:
        raise InputError("Review Pack 沒有任何待擷取 frame")
    if min(requested) < 0 or max(requested) >= timestamp_index.frame_count:
        raise InputError("Review Pack 擷取 ordinal 超出 PTS index")

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise InputError("OpenCV 無法開啟 Review Pack 影片：{}".format(video_path))
    rotation = int(metadata["rotation"]["clockwise_degrees"])
    orientation_disabled = False
    decoded_count = 0
    position_mismatches: List[Dict[str, Any]] = []
    evidence: Dict[int, EncodedFrameEvidence] = {}
    first_decoded_dimensions = None
    first_display_dimensions = None
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
            if frame_index not in requested:
                continue
            display_frame = rotate_frame_clockwise(raw_frame, rotation)
            full_thumb = _resize_within(display_frame, 720, 332)
            encoded_rois = {}
            for roi_id in sorted(roi_requests.get(frame_index, set())):
                if roi_id not in pixel_rois:
                    raise InputError("Review Pack 使用未知 ROI：{}".format(roi_id))
                roi = pixel_rois[roi_id]
                crop = display_frame[roi.y : roi.y2, roi.x : roi.x2]
                if crop.size == 0:
                    raise InputError("Review Pack ROI crop 為空：{}".format(roi_id))
                encoded_rois[roi_id] = encode_image(
                    _resize_within(crop, 720, 240), "jpeg", jpeg_quality=92
                )
            evidence[frame_index] = EncodedFrameEvidence(
                frame_index=frame_index,
                pts=float(timestamp_index.pts_sec[frame_index]),
                full_frame_jpeg=encode_image(full_thumb, "jpeg", jpeg_quality=90),
                roi_jpegs=encoded_rois,
            )
    finally:
        capture.release()

    expected_encoded = metadata["encoded_dimensions"]
    dimensions_match = first_decoded_dimensions == {
        "width": int(expected_encoded["width"]),
        "height": int(expected_encoded["height"]),
    }
    display_match = first_display_dimensions == metadata["display_dimensions"]
    complete = requested == set(evidence)
    count_match = decoded_count == timestamp_index.frame_count
    if not (
        count_match
        and dimensions_match
        and display_match
        and orientation_disabled
        and not position_mismatches
        and complete
    ):
        error = DecodeAlignmentError("Review Pack 全片順序解碼或 evidence ordinal 對齊失敗")
        error.report = {
            "decoded_frame_count": decoded_count,
            "expected_frame_count": timestamp_index.frame_count,
            "dimensions_match": dimensions_match,
            "display_match": display_match,
            "orientation_auto_disabled": orientation_disabled,
            "position_mismatches": position_mismatches,
            "requested_evidence_count": len(requested),
            "extracted_evidence_count": len(evidence),
            "missing_ordinals": sorted(requested.difference(evidence))[:20],
        }
        raise error
    return evidence, {
        "status": "pass",
        "decoded_frame_count": decoded_count,
        "pts_frame_count": timestamp_index.frame_count,
        "requested_evidence_count": len(requested),
        "extracted_evidence_count": len(evidence),
        "orientation_auto_disabled": orientation_disabled,
        "rotation_clockwise_degrees": rotation,
        "ordinal_position_mismatches": position_mismatches,
        "pts_authority": "ffprobe.best_effort_timestamp_time",
        "extraction_method": "single_full_sequential_decode",
    }
