"""OpenCV 順序解碼、明確 rotation 與 ffprobe ordinal 對齊。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from .errors import DecodeAlignmentError, InputError
from .image_io import encode_image
from .models import (
    AnchorDefinition,
    DecodeAlignmentReport,
    FrameTimestampIndex,
    SelectedFrame,
)


def rotate_frame_clockwise(frame: np.ndarray, rotation_degrees: int) -> np.ndarray:
    if rotation_degrees == 0:
        return frame
    if rotation_degrees == 90:
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    if rotation_degrees == 180:
        return cv2.rotate(frame, cv2.ROTATE_180)
    if rotation_degrees == 270:
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    raise ValueError("rotation_degrees 必須是 0/90/180/270")


def _small_gray(frame: np.ndarray, width: int = 320, height: int = 148) -> np.ndarray:
    small = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)


def prepare_reference_gray(image: np.ndarray) -> np.ndarray:
    """移除 design-reference screenshot 黑邊後，轉成 anchor 比對用的小圖。"""
    if image.ndim == 2:
        bgr = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    elif image.shape[2] == 4:
        bgr = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    else:
        bgr = image[:, :, :3]
    content_mask = np.max(bgr, axis=2) > 8
    ys, xs = np.where(content_mask)
    if xs.size and ys.size:
        x1, x2 = int(xs.min()), int(xs.max()) + 1
        y1, y2 = int(ys.min()), int(ys.max()) + 1
        cropped = bgr[y1:y2, x1:x2]
    else:
        cropped = bgr
    return _small_gray(cropped)


class _AnchorSelector:
    def __init__(self, definition: AnchorDefinition, reference_gray: Optional[np.ndarray] = None):
        self.definition = definition
        self.reference_gray = reference_gray
        self.previous_gray: Optional[np.ndarray] = None
        self.best: Optional[SelectedFrame] = None

    def consider(self, ordinal: int, pts_sec: float, frame: np.ndarray) -> None:
        gray = _small_gray(frame)
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        if self.previous_gray is None:
            motion = 1.0
        else:
            motion = float(np.mean(cv2.absdiff(gray, self.previous_gray)) / 255.0)
        self.previous_gray = gray
        time_distance = abs(pts_sec - self.definition.target_sec) / self.definition.tolerance_sec
        clarity = sharpness / (sharpness + 1000.0)
        reference_difference = None
        if self.reference_gray is not None:
            reference_difference = float(
                np.mean(cv2.absdiff(gray, self.reference_gray)) / 255.0
            )
            # reference 只在已知 tolerance window 內協助挑代表幀，不參與任何分析範圍或 threshold。
            score = (
                0.02 * time_distance
                + 0.10 * motion
                + 0.03 * (1.0 - clarity)
                + 0.85 * reference_difference
            )
        else:
            score = 0.45 * time_distance + 0.45 * motion + 0.10 * (1.0 - clarity)
        if self.best is None or score < float(self.best.selection_score):
            self.best = SelectedFrame(
                ordinal=ordinal,
                pts_sec=pts_sec,
                image=frame.copy(),
                target_sec=self.definition.target_sec,
                motion_score=motion,
                sharpness_score=sharpness,
                reference_difference_score=reference_difference,
                selection_score=score,
            )


@dataclass
class DecodeExtractionResult:
    report: DecodeAlignmentReport
    anchors: Dict[str, SelectedFrame]
    contact_png_bytes: Dict[int, bytes]


def _nearby_pts(index: FrameTimestampIndex, ordinal: Optional[int], radius: int = 3) -> List[Dict[str, Any]]:
    if ordinal is None or index.frame_count == 0:
        return []
    start = max(0, ordinal - radius)
    end = min(index.frame_count, ordinal + radius + 1)
    return [
        {"ordinal": current, "pts_sec": float(index.pts_sec[current])}
        for current in range(start, end)
    ]


def decode_and_extract(
    video_path: Path,
    metadata: Dict[str, Any],
    timestamp_index: FrameTimestampIndex,
    anchors: Sequence[AnchorDefinition],
    contact_ordinals: Sequence[int],
    ffmpeg_version: str,
    ffprobe_version: str,
    anchor_reference_images: Optional[Dict[str, np.ndarray]] = None,
) -> DecodeExtractionResult:
    if not video_path.is_file():
        raise InputError("找不到影片：{}".format(video_path))
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise InputError("OpenCV 無法開啟影片：{}".format(video_path))
    try:
        backend = capture.getBackendName() if hasattr(capture, "getBackendName") else "unknown"
        orientation_disabled = False
        if hasattr(cv2, "CAP_PROP_ORIENTATION_AUTO"):
            capture.set(cv2.CAP_PROP_ORIENTATION_AUTO, 0)
            orientation_disabled = abs(capture.get(cv2.CAP_PROP_ORIENTATION_AUTO)) < 0.5

        reference_images = anchor_reference_images or {}
        selectors = {
            definition.anchor_id: _AnchorSelector(
                definition,
                prepare_reference_gray(reference_images[definition.anchor_id])
                if definition.anchor_id in reference_images
                else None,
            )
            for definition in anchors
        }
        contact_set = set(int(value) for value in contact_ordinals)
        contacts: Dict[int, bytes] = {}
        ordinal_position_mismatches: List[Dict[str, Any]] = []
        decoded_count = 0
        first_decoded_dimensions = None
        first_display_dimensions = None
        rotation = int(metadata["rotation"]["clockwise_degrees"])
        expected_encoded = metadata["encoded_dimensions"]

        while True:
            success, raw_frame = capture.read()
            if not success:
                break
            ordinal = decoded_count
            decoded_count += 1
            raw_height, raw_width = raw_frame.shape[:2]
            if first_decoded_dimensions is None:
                first_decoded_dimensions = {"width": int(raw_width), "height": int(raw_height)}
                if rotation in (90, 270):
                    display_width, display_height = raw_height, raw_width
                else:
                    display_width, display_height = raw_width, raw_height
                first_display_dimensions = {
                    "width": int(display_width),
                    "height": int(display_height),
                }

            position = capture.get(cv2.CAP_PROP_POS_FRAMES)
            expected_position = float(ordinal + 1)
            if abs(position - expected_position) > 0.01 and len(ordinal_position_mismatches) < 20:
                ordinal_position_mismatches.append(
                    {
                        "ordinal": ordinal,
                        "expected_next_position": expected_position,
                        "reported_next_position": float(position),
                    }
                )

            if ordinal >= timestamp_index.frame_count:
                continue
            pts_sec = float(timestamp_index.pts_sec[ordinal])
            relevant_selectors = [
                selector
                for selector in selectors.values()
                if abs(pts_sec - selector.definition.target_sec) <= selector.definition.tolerance_sec
            ]
            if ordinal in contact_set or relevant_selectors:
                display_frame = rotate_frame_clockwise(raw_frame, rotation)
                if ordinal in contact_set:
                    contacts[ordinal] = encode_image(display_frame, "png")
                for selector in relevant_selectors:
                    selector.consider(ordinal, pts_sec, display_frame)

        dimensions_match = (
            first_decoded_dimensions is not None
            and first_decoded_dimensions["width"] == int(expected_encoded["width"])
            and first_decoded_dimensions["height"] == int(expected_encoded["height"])
        )
        display_match = (
            first_display_dimensions is not None
            and first_display_dimensions == metadata["display_dimensions"]
        )
        count_match = decoded_count == timestamp_index.frame_count
        contact_complete = len(contacts) == len(contact_set)
        selected_anchors = {
            anchor_id: selector.best
            for anchor_id, selector in selectors.items()
            if selector.best is not None
        }
        anchors_complete = len(selected_anchors) == len(selectors)

        first_mismatch: Optional[int] = None
        if ordinal_position_mismatches:
            first_mismatch = int(ordinal_position_mismatches[0]["ordinal"])
        if not count_match:
            count_mismatch = min(decoded_count, timestamp_index.frame_count)
            first_mismatch = count_mismatch if first_mismatch is None else min(first_mismatch, count_mismatch)
        if not dimensions_match or not display_match or not orientation_disabled:
            first_mismatch = 0 if first_mismatch is None else min(first_mismatch, 0)
        if not contact_complete:
            missing_contacts = sorted(contact_set.difference(contacts))
            missing_ordinal = missing_contacts[0] if missing_contacts else 0
            first_mismatch = missing_ordinal if first_mismatch is None else min(first_mismatch, missing_ordinal)

        status = "pass" if (
            count_match
            and not ordinal_position_mismatches
            and dimensions_match
            and display_match
            and orientation_disabled
            and contact_complete
            and anchors_complete
        ) else "failed"
        report = DecodeAlignmentReport(
            status=status,
            ffprobe_frame_count=timestamp_index.frame_count,
            opencv_decoded_frame_count=decoded_count,
            first_possible_mismatch_ordinal=first_mismatch,
            nearby_pts=_nearby_pts(timestamp_index, first_mismatch),
            pts_missing_count=int(timestamp_index.validation["missing_count"]),
            pts_duplicate_count=int(timestamp_index.validation["duplicate_count"]),
            pts_non_monotonic_count=int(timestamp_index.validation["non_monotonic_count"]),
            codec=str(metadata["codec"]),
            opencv_backend=backend,
            ffmpeg_version=ffmpeg_version,
            ffprobe_version=ffprobe_version,
            opencv_version=cv2.__version__,
            orientation_auto_disabled=orientation_disabled,
            encoded_dimensions={
                "width": int(expected_encoded["width"]),
                "height": int(expected_encoded["height"]),
            },
            first_decoded_dimensions=first_decoded_dimensions,
            first_display_dimensions=first_display_dimensions,
            ordinal_position_mismatches=ordinal_position_mismatches,
        )
        if status != "pass":
            details = []
            if not count_match:
                details.append("frame count 不一致")
            if ordinal_position_mismatches:
                details.append("CAP_PROP_POS_FRAMES 未依序前進")
            if not dimensions_match:
                details.append("OpenCV decoded dimensions 與 encoded dimensions 不一致")
            if not display_match:
                details.append("手動 rotation 後 dimensions 不一致")
            if not orientation_disabled:
                details.append("無法確認 CAP_PROP_ORIENTATION_AUTO 已關閉")
            if not contact_complete:
                details.append("固定間隔影格未完整解碼")
            if not anchors_complete:
                details.append("known-frame tolerance window 沒有候選影格")
            error = DecodeAlignmentError(
                "OpenCV/ffprobe ordinal 對齊失敗：{}".format("；".join(details))
            )
            error.report = report
            raise error
        return DecodeExtractionResult(report=report, anchors=selected_anchors, contact_png_bytes=contacts)
    finally:
        capture.release()
