"""Profile、JSON 載入與輸入 schema 的集中入口。"""

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .errors import InputError
from .models import AnchorDefinition, NormalizedRoi, VideoProfile


SUPPORTED_PROFILE = VideoProfile(
    profile_id="pokemon-champions-doubles-zh-tw-2868x1320-v1",
    display_width=2868,
    display_height=1320,
    game="Pokémon Champions",
    battle_format="Doubles",
    language="zh-TW",
)


def load_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise InputError("找不到 JSON 輸入：{}".format(path))
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError("無法讀取 JSON {}：{}".format(path, exc)) from exc
    if not isinstance(data, dict):
        raise InputError("JSON 根節點必須是 object：{}".format(path))
    return data


def load_known_frames(path: Path) -> Tuple[Dict[str, Any], List[AnchorDefinition]]:
    data = load_json(path)
    rows = data.get("known_frames")
    if not isinstance(rows, list) or len(rows) != 6:
        raise InputError("known_frames 必須恰好包含六個 validation anchors")
    anchors = []
    seen = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise InputError("known_frames[{}] 必須是 object".format(index))
        required = ("id", "timestamp_sec", "timestamp_tolerance_sec", "state", "reference_image")
        missing = [name for name in required if name not in row]
        if missing:
            raise InputError("known_frames[{}] 缺少欄位：{}".format(index, ", ".join(missing)))
        anchor_id = str(row["id"])
        if anchor_id in seen:
            raise InputError("known_frames anchor id 重複：{}".format(anchor_id))
        seen.add(anchor_id)
        try:
            target = float(row["timestamp_sec"])
            tolerance = float(row["timestamp_tolerance_sec"])
        except (TypeError, ValueError) as exc:
            raise InputError("anchor {} 的 timestamp/tolerance 必須是數字".format(anchor_id)) from exc
        if target < 0 or tolerance <= 0:
            raise InputError("anchor {} 的 timestamp/tolerance 範圍無效".format(anchor_id))
        anchors.append(
            AnchorDefinition(
                anchor_id=anchor_id,
                target_sec=target,
                tolerance_sec=tolerance,
                state=str(row["state"]),
                reference_image=str(row["reference_image"]),
                description=str(row.get("description", "")),
            )
        )
    return data, anchors


def load_roi_config(path: Path) -> Tuple[Dict[str, Any], Dict[str, NormalizedRoi]]:
    data = load_json(path)
    rois_data = data.get("rois")
    if not isinstance(rois_data, dict) or not rois_data:
        raise InputError("ROI config 的 rois 必須是非空 object")
    rois = {}
    for roi_id, row in rois_data.items():
        if not isinstance(row, dict):
            raise InputError("ROI {} 必須是 object".format(roi_id))
        try:
            roi = NormalizedRoi(
                roi_id=roi_id,
                x=float(row["x"]),
                y=float(row["y"]),
                width=float(row["width"]),
                height=float(row["height"]),
                purpose=str(row.get("purpose", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise InputError("ROI {} 缺少或含有無效座標".format(roi_id)) from exc
        if roi.x < 0 or roi.y < 0 or roi.width <= 0 or roi.height <= 0:
            raise InputError("ROI {} 的 normalized 座標必須為正且起點不得小於 0".format(roi_id))
        if roi.x + roi.width > 1.0 or roi.y + roi.height > 1.0:
            raise InputError("ROI {} 超出 normalized frame 邊界".format(roi_id))
        rois[roi_id] = roi
    return data, rois
