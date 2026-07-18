"""Checkpoint 1G 單次全片順序解碼與 verified ordinal／PTS 擷取。"""

from collections import defaultdict
from pathlib import Path
from typing import Any, DefaultDict, Dict, List, Mapping, Sequence, Tuple

import cv2

from .checkpoint1g_models import ExtractedVisualFrame, VisualFrameRequest
from .errors import DecodeAlignmentError, InputError
from .hp_status_tracker import measure_hp_bar
from .image_io import encode_image
from .models import FrameTimestampIndex, PixelRoi
from .video import rotate_frame_clockwise
from .visual_identity import visual_fingerprint


def derived_visual_rois(base: Mapping[str, PixelRoi]) -> Dict[str, PixelRoi]:
    result = dict(base)

    def horizontal(source: str, count: int) -> None:
        roi = base[source]
        boundaries = [round(index * roi.width / count) for index in range(count + 1)]
        for index in range(count):
            name = "{}:slot{}".format(source, index + 1)
            result[name] = PixelRoi(
                name,
                roi.x + boundaries[index],
                roi.y,
                boundaries[index + 1] - boundaries[index],
                roi.height,
            )

    def vertical(source: str, count: int) -> None:
        roi = base[source]
        boundaries = [round(index * roi.height / count) for index in range(count + 1)]
        for index in range(count):
            name = "{}:slot{}".format(source, index + 1)
            result[name] = PixelRoi(
                name,
                roi.x,
                roi.y + boundaries[index],
                roi.width,
                boundaries[index + 1] - boundaries[index],
            )

    def vertical_with_margins(
        source: str, count: int, top_fraction: float, bottom_fraction: float
    ) -> None:
        roi = base[source]
        top = round(roi.height * top_fraction)
        bottom = round(roi.height * bottom_fraction)
        available = roi.height - top - bottom
        boundaries = [top + round(index * available / count) for index in range(count + 1)]
        for index in range(count):
            name = "{}:slot{}".format(source, index + 1)
            result[name] = PixelRoi(
                name, roi.x, roi.y + boundaries[index], roi.width,
                boundaries[index + 1] - boundaries[index],
            )

    vertical_with_margins("team_preview_player", 6, 0.07, 0.13)
    vertical_with_margins("team_preview_opponent", 6, 0.07, 0.12)
    if selection_roi_covers_full_player_roster(base):
        # Team Selection 與 Team Preview 語意不同，但六列在同一畫面位置。
        vertical_with_margins("selected_four", 6, 0.07, 0.13)
    else:
        # frozen revision 3 的窄 ROI 只供既有 canonical artifact 重現。
        roster = base["team_preview_player"]
        roster_height = roster.height - round(roster.height * 0.07) - round(
            roster.height * 0.13
        )
        legacy_visible_rows = max(
            1,
            min(
                6,
                round(base["selected_four"].height / (roster_height / 6.0)),
            ),
        )
        vertical("selected_four", legacy_visible_rows)
    horizontal("player_status", 2)
    horizontal("opponent_status", 2)
    return result


def selection_roi_covers_full_player_roster(
    base: Mapping[str, PixelRoi],
) -> bool:
    """確認 Team Selection ROI 已涵蓋 player roster 的完整六列。"""

    selected = base["selected_four"]
    roster = base["team_preview_player"]
    return (
        selected.x <= roster.x
        and selected.y <= roster.y
        and selected.x2 >= roster.x2
        and selected.y2 >= roster.y2
    )


def extract_visual_frames(
    video_path: Path,
    metadata: Mapping[str, Any],
    timestamp_index: FrameTimestampIndex,
    rois: Mapping[str, PixelRoi],
    requests: Sequence[VisualFrameRequest],
    work_dir: Path,
    review_dir: Path,
) -> Tuple[List[ExtractedVisualFrame], Dict[str, Any]]:
    if not video_path.is_file():
        raise InputError("找不到 Checkpoint 1G 影片：{}".format(video_path))
    if not requests:
        raise InputError("Checkpoint 1G 沒有 frame requests")
    by_ordinal: DefaultDict[int, List[VisualFrameRequest]] = defaultdict(list)
    for request in requests:
        if request.frame_ordinal < 0 or request.frame_ordinal >= timestamp_index.frame_count:
            raise InputError("Checkpoint 1G frame ordinal 超出 PTS index")
        expected_pts = float(timestamp_index.pts_sec[request.frame_ordinal])
        if abs(expected_pts - request.pts) > 1e-6:
            raise InputError("Checkpoint 1G request ordinal／PTS mapping 不一致：{}".format(request.request_id))
        if request.roi_name not in rois:
            raise InputError("Checkpoint 1G 使用未知 ROI：{}".format(request.roi_name))
        by_ordinal[request.frame_ordinal].append(request)

    work_dir.mkdir(parents=True, exist_ok=True)
    review_dir.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise InputError("OpenCV 無法開啟 Checkpoint 1G 影片")
    rotation = int(metadata["rotation"]["clockwise_degrees"])
    orientation_disabled = False
    decoded_count = 0
    position_mismatches: List[Dict[str, Any]] = []
    first_dimensions = None
    display_dimensions = None
    extracted: List[ExtractedVisualFrame] = []
    extracted_ids = set()
    try:
        if hasattr(cv2, "CAP_PROP_ORIENTATION_AUTO"):
            capture.set(cv2.CAP_PROP_ORIENTATION_AUTO, 0)
            orientation_disabled = abs(capture.get(cv2.CAP_PROP_ORIENTATION_AUTO)) < 0.5
        while True:
            success, raw_frame = capture.read()
            if not success:
                break
            ordinal = decoded_count
            decoded_count += 1
            if first_dimensions is None:
                height, width = raw_frame.shape[:2]
                first_dimensions = {"width": width, "height": height}
                display_dimensions = {
                    "width": height if rotation in (90, 270) else width,
                    "height": width if rotation in (90, 270) else height,
                }
            position = capture.get(cv2.CAP_PROP_POS_FRAMES)
            if abs(position - float(ordinal + 1)) > 0.01 and len(position_mismatches) < 20:
                position_mismatches.append(
                    {
                        "frame_ordinal": ordinal,
                        "expected_next_position": float(ordinal + 1),
                        "reported_next_position": float(position),
                    }
                )
            current = by_ordinal.get(ordinal)
            if not current:
                continue
            frame = rotate_frame_clockwise(raw_frame, rotation)
            for request in current:
                roi = rois[request.roi_name]
                crop = frame[roi.y : roi.y2, roi.x : roi.x2]
                if crop.size == 0:
                    raise InputError("Checkpoint 1G ROI crop 為空：{}".format(request.roi_name))
                crop_relative = "ocr_inputs/{}.png".format(request.request_id)
                crop_path = work_dir / crop_relative
                if request.run_ocr:
                    crop_path.parent.mkdir(parents=True, exist_ok=True)
                    ocr_crop = crop
                    if request.role == "selected_four":
                        height, width = crop.shape[:2]
                        # 只送入順序 marker 窄帶；完整六列 crop 仍另存為 evidence。
                        ocr_crop = crop[
                            int(height * 0.05) : int(height * 0.95),
                            int(width * 0.30) : int(width * 0.50),
                        ]
                    scale = 2 if ocr_crop.shape[0] < 360 else 1
                    ocr_crop = (
                        cv2.resize(
                            ocr_crop,
                            None,
                            fx=scale,
                            fy=scale,
                            interpolation=cv2.INTER_CUBIC,
                        )
                        if scale > 1
                        else ocr_crop
                    )
                    crop_path.write_bytes(encode_image(ocr_crop, "png"))
                evidence_relative = None
                if request.keep_evidence:
                    evidence_relative = "evidence/{}.jpg".format(request.request_id)
                    evidence_path = review_dir / evidence_relative
                    evidence_path.parent.mkdir(parents=True, exist_ok=True)
                    evidence_path.write_bytes(encode_image(crop, "jpeg", jpeg_quality=92))
                fingerprint_crop = crop
                if request.role == "status_sample" or request.role == "menu_status":
                    h, w = crop.shape[:2]
                    fingerprint_crop = crop[int(h * 0.2) : int(h * 0.95), int(w * 0.03) : int(w * 0.40)]
                elif request.role == "team_preview" and request.side == "player":
                    h, w = crop.shape[:2]
                    fingerprint_crop = crop[int(h * 0.05) : int(h * 0.95), int(w * 0.72) : int(w * 0.99)]
                elif request.role == "selected_four":
                    h, w = crop.shape[:2]
                    fingerprint_crop = crop[int(h * 0.03) : int(h * 0.97), int(w * 0.22) : int(w * 0.58)]
                extracted.append(
                    ExtractedVisualFrame(
                        request=request,
                        crop_path=(str(crop_path) if request.run_ocr else ""),
                        evidence_path=evidence_relative,
                        fingerprint=visual_fingerprint(fingerprint_crop),
                        bar_measurement=(
                            measure_hp_bar(crop)
                            if request.role == "status_sample"
                            else None
                        ),
                    )
                )
                extracted_ids.add(request.request_id)
    finally:
        capture.release()

    expected_dimensions = metadata["encoded_dimensions"]
    expected_display = metadata["display_dimensions"]
    checks = {
        "frame_count_match": decoded_count == timestamp_index.frame_count,
        "encoded_dimensions_match": first_dimensions
        == {"width": int(expected_dimensions["width"]), "height": int(expected_dimensions["height"])},
        "display_dimensions_match": display_dimensions
        == {"width": int(expected_display["width"]), "height": int(expected_display["height"])},
        "orientation_auto_disabled": orientation_disabled,
        "ordinal_positions_match": not position_mismatches,
        "requests_complete": extracted_ids == {request.request_id for request in requests},
    }
    if not all(checks.values()):
        error = DecodeAlignmentError("Checkpoint 1G 全片順序解碼或 ordinal 對齊失敗")
        error.report = {
            "checks": checks,
            "decoded_frame_count": decoded_count,
            "expected_frame_count": timestamp_index.frame_count,
            "position_mismatches": position_mismatches,
        }
        raise error
    return extracted, {
        "status": "pass",
        "extraction_method": "single_full_sequential_decode",
        "pts_authority": "ffprobe.best_effort_timestamp_time",
        "decoded_frame_count": decoded_count,
        "requested_unique_frame_count": len(by_ordinal),
        "request_count": len(requests),
        "rotation_clockwise_degrees": rotation,
        "checks": checks,
    }
