"""HP/status OCR parsing、血條估算與保守 temporal smoothing。"""

import re
from collections import Counter
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import cv2
import numpy as np


EXACT_HP_PATTERN = re.compile(r"(?<!\d)(\d{1,4})\s*[/／]\s*(\d{1,4})(?!\d)")
PERCENT_PATTERN = re.compile(r"(?<!\d)(\d{1,3})\s*[%％]")
STATUS_WORDS = ("灼傷", "中毒", "劇毒", "麻痺", "睡眠", "冰凍")


def parse_exact_hp(raw_text: str) -> Optional[Tuple[int, int, float]]:
    match = EXACT_HP_PATTERN.search(raw_text.replace(" ", ""))
    if not match:
        return None
    current, maximum = int(match.group(1)), int(match.group(2))
    if maximum <= 0 or current < 0 or current > maximum:
        return None
    return current, maximum, round(100.0 * current / maximum, 3)


def parse_percentage(raw_text: str) -> Optional[float]:
    match = PERCENT_PATTERN.search(raw_text.replace(" ", ""))
    if not match:
        return None
    value = int(match.group(1))
    return float(value) if 0 <= value <= 100 else None


def parse_status(raw_text: str) -> Optional[str]:
    return next((status for status in STATUS_WORDS if status in raw_text), None)


def measure_hp_bar(image: np.ndarray) -> Dict[str, Any]:
    """只量測健康色水平 run；結果永遠是估算 evidence，不是 exact HP。"""
    if image.size == 0:
        return {"colored_run_px": 0, "row_width_px": 0, "quality": 0.0}
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    height, width = hsv.shape[:2]
    outline_region = hsv[: int(height * 0.75), :]
    outline_mask = (
        (outline_region[:, :, 1] < 60) & (outline_region[:, :, 2] > 180)
    ).astype(np.uint8)
    outline_run = 0
    for row in outline_mask:
        padded = np.pad(row, (1, 1))
        changes = np.flatnonzero(padded[1:] != padded[:-1])
        lengths = changes[1::2] - changes[::2]
        if lengths.size:
            outline_run = max(outline_run, int(lengths.max()))
    panel_visible = outline_run >= int(width * 0.35)

    # 固定 HUD 的 HP bar 位於 slot crop 中央偏下；先排除場地與角色的健康色。
    x1, x2 = int(width * 0.38), int(width * 0.95)
    y1, y2 = int(height * 0.45), int(height * 0.72)
    bar_hsv = hsv[y1:y2, x1:x2]
    hue, saturation, value = cv2.split(bar_hsv)
    green = (hue >= 32) & (hue <= 95)
    yellow = (hue >= 15) & (hue < 32)
    red = (hue <= 14) | (hue >= 170)
    mask = ((green | yellow | red) & (saturation >= 90) & (value >= 75)).astype(np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 1))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    best_run = 0
    best_row = -1
    for row_index, row in enumerate(mask):
        padded = np.pad(row, (1, 1))
        changes = np.flatnonzero(padded[1:] != padded[:-1])
        lengths = changes[1::2] - changes[::2]
        if lengths.size and int(lengths.max()) > best_run:
            best_run = int(lengths.max())
            best_row = row_index
    bar_width = int(mask.shape[1])
    if not panel_visible:
        best_run = 0
        best_row = -1
    return {
        "colored_run_px": best_run,
        "row_width_px": bar_width,
        "row_index": best_row,
        "panel_outline_run_px": outline_run,
        "panel_visible": panel_visible,
        "quality": round(min(1.0, best_run / max(1.0, bar_width * 0.75)), 6) if panel_visible else 0.0,
        "rule_id": "visual.hp_bar.longest_health_color_run.v1",
    }


def normalize_bar_estimates(rows: Sequence[Dict[str, Any]]) -> None:
    maxima: Dict[Tuple[str, str], int] = {}
    for row in rows:
        key = (str(row["side"]), str(row["slot"]))
        run = int(row.get("bar_measurement", {}).get("colored_run_px", 0))
        maxima[key] = max(maxima.get(key, 0), run)
    for row in rows:
        key = (str(row["side"]), str(row["slot"]))
        run = int(row.get("bar_measurement", {}).get("colored_run_px", 0))
        maximum = maxima.get(key, 0)
        row["visual_bar_percent"] = (
            round(min(100.0, 100.0 * run / maximum), 3) if run >= 8 and maximum else None
        )
    # 0.5 秒 sampling 的相鄰可見值取 median，降低漸層與動畫造成的單幀 jitter。
    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((str(row["side"]), str(row["slot"])), []).append(row)
    for group in groups.values():
        values = [row.get("visual_bar_percent") for row in group]
        smoothed = []
        for index, value in enumerate(values):
            if value is None:
                smoothed.append(None)
                continue
            window = [
                float(values[current])
                for current in range(max(0, index - 1), min(len(values), index + 2))
                if values[current] is not None
                and abs(float(group[current]["timestamp"]) - float(group[index]["timestamp"])) <= 0.6
            ]
            smoothed.append(round(float(np.median(window)), 3))
        for row, value in zip(group, smoothed):
            row["visual_bar_percent"] = value


def rolling_consensus(values: Sequence[Optional[Any]], radius: int = 1) -> List[Optional[Any]]:
    result: List[Optional[Any]] = []
    for index in range(len(values)):
        window = [
            item
            for item in values[max(0, index - radius) : index + radius + 1]
            if item is not None
        ]
        if not window:
            result.append(None)
            continue
        result.append(Counter(window).most_common(1)[0][0])
    return result


def classify_hp_change(before: Mapping[str, Any], after: Mapping[str, Any]) -> str:
    before_value = before.get("hp_percent")
    after_value = after.get("hp_percent")
    if before_value is None or after_value is None:
        return "unknown"
    if float(after_value) <= 0:
        return "faint"
    if float(after_value) < float(before_value):
        return "damage"
    if float(after_value) > float(before_value):
        return "heal"
    return "unchanged"
