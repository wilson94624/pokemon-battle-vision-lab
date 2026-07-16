"""以 verified ordinal／PTS 順序解碼 Checkpoint 1C OCR 影格。"""

from collections import defaultdict
from pathlib import Path
from typing import Any, DefaultDict, Dict, List, Mapping, Sequence, Tuple

import cv2

from .checkpoint1c_models import OcrFrameSelection, PreprocessingVariant
from .errors import DecodeAlignmentError, InputError
from .image_io import encode_image
from .models import FrameTimestampIndex, PixelRoi
from .ocr_preprocessing import build_preprocessing_variants
from .video import rotate_frame_clockwise


def _resize_within(image, max_width: int, max_height: int):
    height, width = image.shape[:2]
    scale = min(max_width / float(width), max_height / float(height), 1.0)
    if scale >= 1.0:
        return image
    return cv2.resize(
        image,
        (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
        interpolation=cv2.INTER_AREA,
    )


def extract_checkpoint1c_frames(
    video_path: Path,
    metadata: Mapping[str, Any],
    timestamp_index: FrameTimestampIndex,
    pixel_rois: Mapping[str, PixelRoi],
    selections: Sequence[OcrFrameSelection],
    output_staging: Path,
    review_staging: Path,
) -> Tuple[Dict[str, List[PreprocessingVariant]], Dict[str, str], Dict[str, Any]]:
    if not video_path.is_file():
        raise InputError("找不到 Checkpoint 1C 影片：{}".format(video_path))
    by_ordinal: DefaultDict[int, List[OcrFrameSelection]] = defaultdict(list)
    for selection in selections:
        by_ordinal[selection.frame_ordinal].append(selection)
    requested = set(by_ordinal)
    if not requested:
        raise InputError("Checkpoint 1C 沒有 OCR frame requests")
    if min(requested) < 0 or max(requested) >= timestamp_index.frame_count:
        raise InputError("Checkpoint 1C OCR frame ordinal 超出 PTS index")

    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise InputError("OpenCV 無法開啟 Checkpoint 1C 影片")
    rotation = int(metadata["rotation"]["clockwise_degrees"])
    orientation_disabled = False
    decoded_count = 0
    first_decoded_dimensions = None
    first_display_dimensions = None
    position_mismatches = []
    extracted = set()
    variants_by_frame_key: Dict[str, List[PreprocessingVariant]] = {}
    full_frame_paths: Dict[str, str] = {}
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
            raw_height, raw_width = raw_frame.shape[:2]
            if first_decoded_dimensions is None:
                first_decoded_dimensions = {"width": raw_width, "height": raw_height}
                display_width = raw_height if rotation in (90, 270) else raw_width
                display_height = raw_width if rotation in (90, 270) else raw_height
                first_display_dimensions = {
                    "width": display_width,
                    "height": display_height,
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
            if ordinal not in requested:
                continue
            display_frame = rotate_frame_clockwise(raw_frame, rotation)
            for selection in by_ordinal[ordinal]:
                roi = pixel_rois.get(selection.roi_name)
                if roi is None:
                    raise InputError("Checkpoint 1C 使用未知 ROI：{}".format(selection.roi_name))
                crop = display_frame[roi.y : roi.y2, roi.x : roi.x2]
                if crop.size == 0:
                    raise InputError("Checkpoint 1C ROI crop 為空：{}".format(selection.roi_name))
                raw_path = output_staging / selection.image_path
                raw_path.parent.mkdir(parents=True, exist_ok=True)
                raw_path.write_bytes(encode_image(crop, "png"))

                frame_key = "{}:{:06d}".format(selection.event_id, ordinal)
                variant_rows: List[PreprocessingVariant] = []
                for variant_id, operations, quality_weight, image in build_preprocessing_variants(
                    crop, selection.event_type
                ):
                    relative_path = "variants/{}/{}/{:06d}__{}.png".format(
                        selection.event_type,
                        selection.event_id,
                        ordinal,
                        variant_id,
                    )
                    variant_path = output_staging / relative_path
                    variant_path.parent.mkdir(parents=True, exist_ok=True)
                    variant_path.write_bytes(encode_image(image, "png"))
                    variant_rows.append(
                        PreprocessingVariant(
                            variant_id=variant_id,
                            operations=operations,
                            image_path=relative_path,
                            quality_weight=quality_weight,
                        )
                    )
                variants_by_frame_key[frame_key] = variant_rows

                full_relative = "evidence/{}/{}/{:06d}__full.jpg".format(
                    selection.event_type, selection.event_id, ordinal
                )
                full_path = review_staging / full_relative
                full_path.parent.mkdir(parents=True, exist_ok=True)
                thumbnail = _resize_within(display_frame, 960, 442)
                full_path.write_bytes(encode_image(thumbnail, "jpeg", jpeg_quality=90))
                full_frame_paths[frame_key] = full_relative
            extracted.add(ordinal)
    finally:
        capture.release()

    expected_encoded = metadata["encoded_dimensions"]
    count_match = decoded_count == timestamp_index.frame_count
    dimensions_match = first_decoded_dimensions == {
        "width": int(expected_encoded["width"]),
        "height": int(expected_encoded["height"]),
    }
    display_match = first_display_dimensions == {
        "width": int(metadata["display_dimensions"]["width"]),
        "height": int(metadata["display_dimensions"]["height"]),
    }
    complete = requested == extracted
    if not (
        count_match
        and dimensions_match
        and display_match
        and orientation_disabled
        and not position_mismatches
        and complete
    ):
        error = DecodeAlignmentError("Checkpoint 1C 全片順序解碼或 ordinal 對齊失敗")
        error.report = {
            "decoded_frame_count": decoded_count,
            "expected_frame_count": timestamp_index.frame_count,
            "dimensions_match": dimensions_match,
            "display_match": display_match,
            "orientation_auto_disabled": orientation_disabled,
            "position_mismatches": position_mismatches,
            "missing_ordinals": sorted(requested.difference(extracted))[:20],
        }
        raise error
    return variants_by_frame_key, full_frame_paths, {
        "status": "pass",
        "decoded_frame_count": decoded_count,
        "pts_frame_count": timestamp_index.frame_count,
        "requested_unique_frame_count": len(requested),
        "selection_count": len(selections),
        "extracted_unique_frame_count": len(extracted),
        "orientation_auto_disabled": orientation_disabled,
        "rotation_clockwise_degrees": rotation,
        "ordinal_position_mismatches": position_mismatches,
        "pts_authority": "ffprobe.best_effort_timestamp_time",
        "extraction_method": "single_full_sequential_decode",
    }

