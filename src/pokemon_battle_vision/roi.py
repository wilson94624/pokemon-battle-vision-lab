"""Normalized ROI、raw-frame overlay 與人工 approval hash gate。"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

import cv2
import numpy as np

from .config import load_json
from .errors import RoiApprovalError
from .models import NormalizedRoi, PixelRoi
from .utils import sha256_file, write_json


def normalized_to_pixel(roi: NormalizedRoi, frame_width: int, frame_height: int) -> PixelRoi:
    if frame_width <= 0 or frame_height <= 0:
        raise ValueError("frame dimensions 必須大於 0")
    if roi.x < 0 or roi.y < 0 or roi.width <= 0 or roi.height <= 0:
        raise ValueError("normalized ROI 無效：{}".format(roi.roi_id))
    if roi.x + roi.width > 1.0 or roi.y + roi.height > 1.0:
        raise ValueError("normalized ROI 超出 frame：{}".format(roi.roi_id))
    # 起點向下、終點向上，確保 normalized 區域不因取整而被裁掉。
    x1 = int(np.floor(roi.x * frame_width))
    y1 = int(np.floor(roi.y * frame_height))
    x2 = int(np.ceil((roi.x + roi.width) * frame_width))
    y2 = int(np.ceil((roi.y + roi.height) * frame_height))
    x1 = max(0, min(x1, frame_width - 1))
    y1 = max(0, min(y1, frame_height - 1))
    x2 = max(x1 + 1, min(x2, frame_width))
    y2 = max(y1 + 1, min(y2, frame_height))
    return PixelRoi(roi.roi_id, x1, y1, x2 - x1, y2 - y1)


def pixel_rois(
    rois: Mapping[str, NormalizedRoi], frame_width: int, frame_height: int
) -> Dict[str, PixelRoi]:
    return {
        roi_id: normalized_to_pixel(roi, frame_width, frame_height)
        for roi_id, roi in rois.items()
    }


def draw_roi_overlay(
    frame: np.ndarray,
    selected_rois: Iterable[PixelRoi],
    line_thickness: int = 5,
) -> np.ndarray:
    overlay = frame.copy()
    palette = [
        (0, 255, 0),
        (0, 165, 255),
        (255, 0, 255),
        (255, 255, 0),
        (0, 0, 255),
        (255, 128, 0),
        (128, 255, 128),
        (255, 255, 255),
    ]
    font_scale = max(0.6, min(frame.shape[1], frame.shape[0]) / 1200.0)
    for index, roi in enumerate(selected_rois):
        color = palette[index % len(palette)]
        cv2.rectangle(overlay, (roi.x, roi.y), (roi.x2 - 1, roi.y2 - 1), color, line_thickness)
        label = roi.roi_id
        (text_width, text_height), baseline = cv2.getTextSize(
            label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, max(1, line_thickness // 2)
        )
        label_top = max(0, roi.y - text_height - baseline - 8)
        label_right = min(frame.shape[1], roi.x + text_width + 12)
        cv2.rectangle(
            overlay,
            (roi.x, label_top),
            (label_right, min(frame.shape[0] - 1, label_top + text_height + baseline + 8)),
            color,
            -1,
        )
        cv2.putText(
            overlay,
            label,
            (roi.x + 6, label_top + text_height + 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (0, 0, 0),
            max(1, line_thickness // 2),
            cv2.LINE_AA,
        )
    return overlay


def create_roi_approval(
    video_path: Path,
    roi_config_path: Path,
    overlay_manifest_path: Path,
    approved_by: str,
    output_path: Path,
) -> Dict[str, Any]:
    approved_by = approved_by.strip()
    if not approved_by:
        raise RoiApprovalError("--approved-by 不可為空")
    for path in (video_path, roi_config_path, overlay_manifest_path):
        if not path.is_file():
            raise RoiApprovalError("ROI approval 輸入不存在：{}".format(path))
    manifest = load_json(overlay_manifest_path)
    video_hash = sha256_file(video_path)
    config_hash = sha256_file(roi_config_path)
    if manifest.get("video_sha256") != video_hash:
        raise RoiApprovalError("影片 hash 與 overlay manifest 不一致；必須重新產生 overlays")
    if manifest.get("roi_config_sha256") != config_hash:
        raise RoiApprovalError("ROI config hash 與 overlay manifest 不一致；必須重新產生 overlays")
    overlays = manifest.get("overlays")
    if not isinstance(overlays, list) or not overlays:
        raise RoiApprovalError("overlay manifest 沒有可核准的 overlays")
    for row in overlays:
        relative_path = row.get("path") if isinstance(row, dict) else None
        expected_hash = row.get("sha256") if isinstance(row, dict) else None
        if not relative_path or not expected_hash:
            raise RoiApprovalError("overlay manifest item 缺少 path/sha256")
        overlay_path = overlay_manifest_path.parent / relative_path
        if not overlay_path.is_file():
            raise RoiApprovalError("overlay 檔不存在：{}".format(overlay_path))
        if sha256_file(overlay_path) != expected_hash:
            raise RoiApprovalError("overlay hash 已改變：{}".format(overlay_path))
    payload = {
        "schema_version": "0.1.0",
        "status": "approved",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "approved_by": approved_by,
        "video_path": str(video_path),
        "video_sha256": video_hash,
        "roi_config_path": str(roi_config_path),
        "roi_config_sha256": config_hash,
        "overlay_manifest_path": str(overlay_manifest_path),
        "overlay_manifest_sha256": sha256_file(overlay_manifest_path),
        "overlay_count": len(overlays),
    }
    write_json(output_path, payload)
    return payload

