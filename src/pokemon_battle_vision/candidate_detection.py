"""以 OpenCV／NumPy 外觀相似度建立 UI event candidates；不做 OCR。"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

import cv2
import numpy as np

from .checkpoint1b_models import EVENT_TYPES
from .errors import InputError
from .image_io import read_image
from .models import PixelRoi


EVENT_ROIS: Dict[str, Tuple[str, ...]] = {
    "TEAM_PREVIEW": ("team_preview_player", "team_preview_opponent"),
    "SELECTED_FOUR": ("selected_four",),
    "MOVE_MENU": ("move_menu",),
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

# 這些是 classical CV candidate thresholds，不是 ROI 座標，也不改變 10 Hz 掃描策略。
DEFAULT_THRESHOLDS: Dict[str, float] = {
    "TEAM_PREVIEW": 0.84,
    "SELECTED_FOUR": 0.83,
    "MOVE_MENU": 0.80,
    "BATTLE_TEXT": 0.76,
    "TRIGGER_NOTIFICATION": 0.82,
    "RESULT": 0.84,
}


@dataclass(frozen=True)
class AppearanceSignature:
    gray: np.ndarray
    edge: np.ndarray
    histogram: np.ndarray


@dataclass(frozen=True)
class EventTemplate:
    event_type: str
    source_id: str
    roi_signatures: Dict[str, AppearanceSignature]


def crop_roi(frame: np.ndarray, roi: PixelRoi) -> np.ndarray:
    crop = frame[roi.y : roi.y2, roi.x : roi.x2]
    if crop.size == 0:
        raise ValueError("ROI crop 為空：{}".format(roi.roi_id))
    return crop


def appearance_signature(image: np.ndarray, width: int = 48, height: int = 24) -> AppearanceSignature:
    if image.ndim == 2:
        bgr = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    elif image.shape[2] == 4:
        bgr = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    else:
        bgr = image[:, :, :3]
    small = cv2.resize(bgr, (width, height), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    edge = cv2.Canny(gray, 50, 140)
    histogram = cv2.calcHist([small], [0, 1], None, [12, 12], [0, 256, 0, 256])
    cv2.normalize(histogram, histogram, alpha=1.0, norm_type=cv2.NORM_L1)
    return AppearanceSignature(gray=gray, edge=edge, histogram=histogram.reshape(-1))


def signature_similarity(current: AppearanceSignature, reference: AppearanceSignature) -> float:
    current_gray = current.gray.astype(np.float32)
    reference_gray = reference.gray.astype(np.float32)
    mae_similarity = 1.0 - float(np.mean(np.abs(current_gray - reference_gray)) / 255.0)

    current_centered = current_gray - float(np.mean(current_gray))
    reference_centered = reference_gray - float(np.mean(reference_gray))
    denominator = float(
        np.linalg.norm(current_centered.reshape(-1))
        * np.linalg.norm(reference_centered.reshape(-1))
    )
    correlation = (
        float(np.dot(current_centered.reshape(-1), reference_centered.reshape(-1)) / denominator)
        if denominator > 1e-9
        else 0.0
    )
    correlation_similarity = (max(-1.0, min(1.0, correlation)) + 1.0) / 2.0

    edge_similarity = 1.0 - float(
        np.mean(
            np.abs(current.edge.astype(np.float32) - reference.edge.astype(np.float32))
        )
        / 255.0
    )
    histogram_similarity = float(
        cv2.compareHist(
            current.histogram.astype(np.float32),
            reference.histogram.astype(np.float32),
            cv2.HISTCMP_INTERSECT,
        )
    )
    score = (
        0.50 * mae_similarity
        + 0.25 * correlation_similarity
        + 0.15 * edge_similarity
        + 0.10 * histogram_similarity
    )
    return max(0.0, min(1.0, float(score)))


def _event_type_for_roi_ids(roi_ids: Iterable[str]) -> str:
    roi_set = set(roi_ids)
    matches = [
        event_type
        for event_type, expected in EVENT_ROIS.items()
        if roi_set.intersection(expected)
    ]
    if len(matches) != 1:
        raise InputError("無法由 overlay ROI 唯一判斷 event type：{}".format(sorted(roi_set)))
    return matches[0]


def load_approved_templates(
    checkpoint1a_dir: Path,
    overlay_manifest: Mapping[str, Any],
    pixel_rois: Mapping[str, PixelRoi],
) -> Dict[str, List[EventTemplate]]:
    templates: Dict[str, List[EventTemplate]] = {event_type: [] for event_type in EVENT_TYPES}
    rows = overlay_manifest.get("overlays")
    if not isinstance(rows, list):
        raise InputError("ROI overlay manifest 缺少 overlays")
    for row in rows:
        if not isinstance(row, dict):
            continue
        roi_ids = row.get("roi_ids")
        if not isinstance(roi_ids, list):
            continue
        relevant = set(roi_ids).intersection(
            roi_id for values in EVENT_ROIS.values() for roi_id in values
        )
        if not relevant:
            continue
        event_type = _event_type_for_roi_ids(relevant)
        expected_ids = set(EVENT_ROIS[event_type])
        selected_ids = [roi_id for roi_id in roi_ids if roi_id in expected_ids]

        if event_type == "TRIGGER_NOTIFICATION":
            validation = row.get("roi_validation", {})
            selected_ids = [
                roi_id
                for roi_id in selected_ids
                if isinstance(validation.get(roi_id), dict)
                and validation[roi_id].get("positive_example_verified") is True
            ]
        if not selected_ids:
            continue

        source = row.get("source") if isinstance(row.get("source"), dict) else {}
        anchor_path = source.get("anchor_path")
        if anchor_path:
            image_path = checkpoint1a_dir / str(anchor_path)
        else:
            image_path = checkpoint1a_dir / str(row.get("path", ""))
        frame, _ = read_image(image_path)

        signatures = {}
        for roi_id in selected_ids:
            if roi_id not in pixel_rois:
                raise InputError("核准 overlay 使用未知 ROI：{}".format(roi_id))
            signatures[roi_id] = appearance_signature(crop_roi(frame, pixel_rois[roi_id]))
        templates[event_type].append(
            EventTemplate(
                event_type=event_type,
                source_id=str(row.get("id", image_path.name)),
                roi_signatures=signatures,
            )
        )

    missing = [event_type for event_type, values in templates.items() if not values]
    if missing:
        raise InputError("Checkpoint 1A 核准證據缺少 1B templates：{}".format(missing))
    return templates


class CandidateDetector:
    """以核准 1A 正例 templates 計算每個 sampled frame 的候選分數。"""

    def __init__(
        self,
        pixel_rois: Mapping[str, PixelRoi],
        templates: Mapping[str, Sequence[EventTemplate]],
        thresholds: Mapping[str, float] = DEFAULT_THRESHOLDS,
    ) -> None:
        self.pixel_rois = dict(pixel_rois)
        self.templates = {key: list(value) for key, value in templates.items()}
        self.thresholds = dict(thresholds)

    def _frame_signatures(self, frame: np.ndarray) -> Dict[str, AppearanceSignature]:
        required_ids = {
            roi_id for roi_ids in EVENT_ROIS.values() for roi_id in roi_ids
        }
        return {
            roi_id: appearance_signature(crop_roi(frame, self.pixel_rois[roi_id]))
            for roi_id in required_ids
        }

    def _template_score(
        self,
        event_type: str,
        current: Mapping[str, AppearanceSignature],
        template: EventTemplate,
    ) -> Tuple[float, Dict[str, float]]:
        if event_type == "TRIGGER_NOTIFICATION":
            reference = next(iter(template.roi_signatures.values()))
            by_roi = {
                roi_id: signature_similarity(current[roi_id], reference)
                for roi_id in EVENT_ROIS[event_type]
            }
            return max(by_roi.values()), by_roi
        by_roi = {
            roi_id: signature_similarity(current[roi_id], reference)
            for roi_id, reference in template.roi_signatures.items()
        }
        return float(np.mean(list(by_roi.values()))), by_roi

    def score_frame(self, frame: np.ndarray) -> Tuple[Dict[str, float], Dict[str, List[str]]]:
        signatures = self._frame_signatures(frame)
        event_scores: Dict[str, float] = {}
        visible_by_event: Dict[str, List[str]] = {}
        for event_type in EVENT_TYPES:
            best_score = -1.0
            best_by_roi: Dict[str, float] = {}
            for template in self.templates[event_type]:
                score, by_roi = self._template_score(event_type, signatures, template)
                if score > best_score:
                    best_score = score
                    best_by_roi = by_roi
            event_scores[event_type] = round(max(0.0, best_score), 6)
            threshold = self.thresholds[event_type]
            visible_by_event[event_type] = sorted(
                roi_id for roi_id, score in best_by_roi.items() if score >= threshold
            )
        return event_scores, visible_by_event

    def classify(
        self, event_scores: Mapping[str, float], visible_by_event: Mapping[str, Sequence[str]]
    ) -> Tuple[str, List[str]]:
        active = [
            event_type
            for event_type in EVENT_TYPES
            if float(event_scores[event_type]) >= self.thresholds[event_type]
        ]
        if not active:
            return "UNKNOWN", []
        ui_state = max(active, key=lambda event_type: float(event_scores[event_type]))
        visible = sorted(
            {
                roi_id
                for event_type in active
                for roi_id in visible_by_event.get(event_type, [])
            }
        )
        return ui_state, visible
