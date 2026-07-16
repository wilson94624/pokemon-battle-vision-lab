"""TRIGGER_NOTIFICATION 的側邊 analysis context 與 classical CV features。"""

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Tuple

import cv2
import numpy as np

from .models import PixelRoi


TRIGGER_SIDE_ROIS = {
    "player": "player_trigger_notification",
    "opponent": "opponent_trigger_notification",
}
TRIGGER_ANALYSIS_ROIS = {
    side: "{}_analysis_context".format(roi_id)
    for side, roi_id in TRIGGER_SIDE_ROIS.items()
}


@dataclass(frozen=True)
class TriggerNotificationFeatureConfig:
    # 114 秒短版通知出現在 canonical ROI 上方；保留 frozen ROI，僅推導 detector context。
    upward_extension_ratio: float = 0.75
    text_left_fraction_player: float = 0.20
    text_right_fraction_player: float = 0.72
    text_left_fraction_opponent: float = 0.32
    text_right_fraction_opponent: float = 0.80
    icon_left_fraction_player: float = 0.00
    icon_right_fraction_player: float = 0.30
    icon_left_fraction_opponent: float = 0.70
    icon_right_fraction_opponent: float = 1.00
    local_blur_sigma: float = 7.0
    local_delta_threshold: int = 8
    max_text_saturation: int = 90
    min_text_value: int = 135
    min_component_width: int = 2
    max_component_width: int = 55
    min_component_height: int = 3
    max_component_height: int = 42
    min_component_area: int = 5
    max_component_area: int = 900
    large_component_area: int = 1200
    line_half_window: int = 12
    min_weak_aligned_components: int = 4
    min_strong_aligned_components: int = 7
    min_weak_line_span: float = 0.12
    min_strong_line_span: float = 0.24
    min_weak_text_occupancy: float = 0.003
    min_strong_text_occupancy: float = 0.006
    min_secondary_aligned_components: int = 2
    min_secondary_line_span: float = 0.12
    minimum_line_separation_px: int = 30
    max_line_separation_ratio: float = 0.28
    max_text_occupancy: float = 0.18
    max_line_height_cv: float = 1.05
    min_edge_density: float = 0.030
    max_notification_line_span: float = 0.85
    min_primary_glyph_like_components: int = 4
    min_secondary_glyph_like_components: int = 2
    min_glyph_width: int = 18
    min_glyph_height: int = 18
    min_glyph_area: int = 180
    min_glyph_aspect_ratio: float = 0.55
    max_glyph_aspect_ratio: float = 1.50
    min_notification_panel_occupancy: float = 0.35
    max_large_bright_fraction: float = 0.45
    sparse_wide_span: float = 0.82
    sparse_wide_max_occupancy: float = 0.010

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


DEFAULT_TRIGGER_FEATURE_CONFIG = TriggerNotificationFeatureConfig()


@dataclass(frozen=True)
class TriggerNotificationFeatureVector:
    side: str
    analysis_roi_id: str
    analysis_bbox: Tuple[int, int, int, int]
    brightness_contrast: float
    edge_density: float
    component_count: int
    aligned_component_count: int
    line_span_ratio: float
    secondary_aligned_component_count: int
    secondary_line_span_ratio: float
    secondary_line_height_cv: float
    line_separation_ratio: float
    primary_glyph_like_count: int
    secondary_glyph_like_count: int
    line_height_cv: float
    panel_occupancy: float
    text_region_occupancy: float
    icon_region_occupancy: float
    large_bright_fraction: float
    panel_score: float
    text_score: float
    icon_score: float
    structural_weak: bool
    structural_strong: bool
    negative_reasons: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["analysis_bbox"] = list(self.analysis_bbox)
        payload["negative_reasons"] = list(self.negative_reasons)
        return payload


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def derive_trigger_analysis_roi(
    canonical_roi: PixelRoi,
    frame_width: int,
    frame_height: int,
    config: TriggerNotificationFeatureConfig = DEFAULT_TRIGGER_FEATURE_CONFIG,
) -> PixelRoi:
    """由 frozen canonical ROI 向上取得 context，不改寫 ROI config。"""
    if frame_width <= 0 or frame_height <= 0:
        raise ValueError("frame dimensions 必須大於 0")
    y1 = max(0, int(round(canonical_roi.y - canonical_roi.height * config.upward_extension_ratio)))
    x1 = max(0, canonical_roi.x)
    x2 = min(frame_width, canonical_roi.x2)
    y2 = min(frame_height, canonical_roi.y2)
    side = next(
        (side for side, roi_id in TRIGGER_SIDE_ROIS.items() if roi_id == canonical_roi.roi_id),
        None,
    )
    if side is None:
        raise ValueError("不是 trigger canonical ROI：{}".format(canonical_roi.roi_id))
    return PixelRoi(
        roi_id=TRIGGER_ANALYSIS_ROIS[side],
        x=x1,
        y=y1,
        width=max(1, x2 - x1),
        height=max(1, y2 - y1),
    )


def trigger_analysis_rois(
    pixel_rois: Dict[str, PixelRoi],
    frame_width: int,
    frame_height: int,
    config: TriggerNotificationFeatureConfig = DEFAULT_TRIGGER_FEATURE_CONFIG,
) -> Dict[str, PixelRoi]:
    return {
        TRIGGER_ANALYSIS_ROIS[side]: derive_trigger_analysis_roi(
            pixel_rois[canonical_id], frame_width, frame_height, config
        )
        for side, canonical_id in TRIGGER_SIDE_ROIS.items()
    }


def _best_horizontal_lines(
    components: List[Tuple[int, int, int, int, int]],
    width: int,
    height: int,
    half_window: int,
    minimum_line_separation: int,
    preferred_secondary_span: float,
) -> Tuple[int, float, float, int, float, float, int, int]:
    best_count = 0
    best_span = 0.0
    best_height_cv = 9.0
    best_center_y = 0
    candidates = []
    for center_y in range(height):
        line = [
            component
            for component in components
            if abs(component[1] + component[3] / 2.0 - center_y) <= half_window
        ]
        if not line:
            continue
        span = (
            max(component[0] + component[2] for component in line)
            - min(component[0] for component in line)
        ) / float(max(1, width))
        heights = np.asarray([component[3] for component in line], dtype=np.float32)
        height_cv = float(np.std(heights) / max(1.0, float(np.mean(heights))))
        candidates.append((len(line), span, height_cv, center_y))
        if (len(line), span, -height_cv) > (best_count, best_span, -best_height_cv):
            best_count = len(line)
            best_span = span
            best_height_cv = height_cv
            best_center_y = center_y
    second_candidates = [
        candidate
        for candidate in candidates
        if abs(candidate[3] - best_center_y) >= minimum_line_separation
        and candidate[1] >= 0.08
    ]
    if second_candidates:
        second = max(
            second_candidates,
            key=lambda value: (
                value[1] >= preferred_secondary_span,
                value[0] + 4.0 * value[1],
                value[1],
                -value[2],
            ),
        )
        second_count, second_span, second_height_cv, second_center_y = (
            second[0],
            second[1],
            second[2],
            second[3],
        )
    else:
        second_count, second_span, second_height_cv, second_center_y = 0, 0.0, 9.0, 0
    return (
        best_count,
        best_span,
        best_height_cv,
        second_count,
        second_span,
        second_height_cv,
        best_center_y,
        second_center_y,
    )


def _side_fractions(
    side: str, config: TriggerNotificationFeatureConfig
) -> Tuple[float, float, float, float]:
    if side == "player":
        return (
            config.text_left_fraction_player,
            config.text_right_fraction_player,
            config.icon_left_fraction_player,
            config.icon_right_fraction_player,
        )
    if side == "opponent":
        return (
            config.text_left_fraction_opponent,
            config.text_right_fraction_opponent,
            config.icon_left_fraction_opponent,
            config.icon_right_fraction_opponent,
        )
    raise ValueError("未知 trigger side：{}".format(side))


def extract_trigger_notification_features(
    analysis_crop: np.ndarray,
    side: str,
    analysis_roi: PixelRoi,
    config: TriggerNotificationFeatureConfig = DEFAULT_TRIGGER_FEATURE_CONFIG,
) -> TriggerNotificationFeatureVector:
    if analysis_crop.size == 0:
        raise ValueError("trigger analysis crop 不可為空")
    height, width = analysis_crop.shape[:2]
    text_left, text_right, icon_left, icon_right = _side_fractions(side, config)
    tx1 = max(0, min(width - 1, int(round(width * text_left))))
    tx2 = max(tx1 + 1, min(width, int(round(width * text_right))))
    ix1 = max(0, min(width - 1, int(round(width * icon_left))))
    ix2 = max(ix1 + 1, min(width, int(round(width * icon_right))))
    text_region = analysis_crop[:, tx1:tx2]
    hsv = cv2.cvtColor(text_region, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(text_region, cv2.COLOR_BGR2GRAY)
    local_background = cv2.GaussianBlur(gray, (0, 0), config.local_blur_sigma)
    local_delta = cv2.subtract(gray, local_background)
    text_mask = (
        (hsv[:, :, 1] <= config.max_text_saturation)
        & (hsv[:, :, 2] >= config.min_text_value)
        & (local_delta >= config.local_delta_threshold)
    )
    _, _, stats, _ = cv2.connectedComponentsWithStats(text_mask.astype(np.uint8), 8)
    components: List[Tuple[int, int, int, int, int]] = []
    large_area = 0
    for x, y, component_width, component_height, area in stats[1:]:
        values = tuple(map(int, (x, y, component_width, component_height, area)))
        x, y, component_width, component_height, area = values
        if area >= config.large_component_area:
            large_area += area
        if (
            config.min_component_width <= component_width <= config.max_component_width
            and config.min_component_height <= component_height <= config.max_component_height
            and config.min_component_area <= area <= config.max_component_area
        ):
            components.append(values)
    (
        aligned_count,
        line_span,
        line_height_cv,
        secondary_aligned_count,
        secondary_line_span,
        secondary_line_height_cv,
        primary_line_center,
        secondary_line_center,
    ) = _best_horizontal_lines(
        components,
        text_region.shape[1],
        height,
        config.line_half_window,
        config.minimum_line_separation_px,
        config.min_secondary_line_span,
    )
    text_occupancy = float(np.mean(text_mask))
    line_separation_ratio = (
        abs(primary_line_center - secondary_line_center) / float(max(1, height))
        if secondary_aligned_count
        else 0.0
    )
    mask_pixels = int(np.count_nonzero(text_mask))
    large_fraction = large_area / float(max(1, mask_pixels))
    value = hsv[:, :, 2]
    brightness_contrast = (
        float(np.percentile(value, 90)) - float(np.percentile(value, 50))
    ) / 255.0
    edge_density = float(np.mean(cv2.Canny(gray, 45, 110) > 0))

    def is_glyph_like(component: Tuple[int, int, int, int, int]) -> bool:
        _, _, component_width, component_height, area = component
        aspect_ratio = component_width / float(max(1, component_height))
        return (
            component_width >= config.min_glyph_width
            and component_height >= config.min_glyph_height
            and area >= config.min_glyph_area
            and config.min_glyph_aspect_ratio
            <= aspect_ratio
            <= config.max_glyph_aspect_ratio
        )

    primary_line_components = [
        component
        for component in components
        if abs(component[1] + component[3] / 2.0 - primary_line_center)
        <= config.line_half_window
    ]
    secondary_line_components = [
        component
        for component in components
        if abs(component[1] + component[3] / 2.0 - secondary_line_center)
        <= config.line_half_window
    ]
    primary_glyph_like_count = sum(map(is_glyph_like, primary_line_components))
    secondary_glyph_like_count = sum(map(is_glyph_like, secondary_line_components))
    panel_occupancy = float(np.mean((hsv[:, :, 2] < 145) & (hsv[:, :, 1] < 180)))
    icon_hsv = cv2.cvtColor(analysis_crop[:, ix1:ix2], cv2.COLOR_BGR2HSV)
    icon_occupancy = float(
        np.mean((icon_hsv[:, :, 1] > 70) & (icon_hsv[:, :, 2] > 80))
    )
    sparse_wide = (
        line_span >= config.sparse_wide_span
        and text_occupancy <= config.sparse_wide_max_occupancy
    )
    common_geometry = (
        text_occupancy <= config.max_text_occupancy
        and line_height_cv <= config.max_line_height_cv
        and large_fraction <= config.max_large_bright_fraction
        and not sparse_wide
        and secondary_aligned_count >= config.min_secondary_aligned_components
        and secondary_line_span >= config.min_secondary_line_span
        and line_separation_ratio <= config.max_line_separation_ratio
        and secondary_line_height_cv <= config.max_line_height_cv
        and edge_density >= config.min_edge_density
        and line_span <= config.max_notification_line_span
        and secondary_line_span <= config.max_notification_line_span
        and primary_glyph_like_count >= config.min_primary_glyph_like_components
        and secondary_glyph_like_count >= config.min_secondary_glyph_like_components
        and panel_occupancy >= config.min_notification_panel_occupancy
    )
    structural_weak = common_geometry and (
        aligned_count >= config.min_weak_aligned_components
        and line_span >= config.min_weak_line_span
        and text_occupancy >= config.min_weak_text_occupancy
    )
    structural_strong = common_geometry and (
        aligned_count >= config.min_strong_aligned_components
        and line_span >= config.min_strong_line_span
        and text_occupancy >= config.min_strong_text_occupancy
    )
    component_strength = _clamp(aligned_count / 10.0)
    span_strength = _clamp((line_span - 0.10) / 0.42)
    occupancy_strength = _clamp((text_occupancy - 0.003) / 0.040)
    consistency_strength = _clamp(1.0 - line_height_cv / 1.25)
    text_score = _clamp(
        0.38 * component_strength
        + 0.32 * span_strength
        + 0.20 * occupancy_strength
        + 0.10 * consistency_strength
    )
    if not common_geometry:
        text_score *= 0.35
    panel_score = _clamp((panel_occupancy - 0.30) / 0.55)
    icon_score = _clamp(icon_occupancy / 0.45)
    negative_reasons = []
    if text_occupancy < config.min_weak_text_occupancy:
        negative_reasons.append("insufficient_white_text_occupancy")
    if aligned_count < config.min_weak_aligned_components:
        negative_reasons.append("insufficient_aligned_components")
    if line_span < config.min_weak_line_span:
        negative_reasons.append("insufficient_text_line_span")
    if (
        secondary_aligned_count < config.min_secondary_aligned_components
        or secondary_line_span < config.min_secondary_line_span
    ):
        negative_reasons.append("missing_second_text_line")
    if line_separation_ratio > config.max_line_separation_ratio:
        negative_reasons.append("text_lines_too_far_apart")
    if edge_density < config.min_edge_density:
        negative_reasons.append("insufficient_local_text_edges")
    if line_span > config.max_notification_line_span:
        negative_reasons.append("line_span_too_wide_for_notification")
    if secondary_line_span > config.max_notification_line_span:
        negative_reasons.append("secondary_line_too_wide_for_notification")
    if primary_glyph_like_count < config.min_primary_glyph_like_components:
        negative_reasons.append("insufficient_primary_glyph_geometry")
    if secondary_glyph_like_count < config.min_secondary_glyph_like_components:
        negative_reasons.append("insufficient_secondary_glyph_geometry")
    if panel_occupancy < config.min_notification_panel_occupancy:
        negative_reasons.append("missing_dark_notification_panel")
    if sparse_wide:
        negative_reasons.append("sparse_wide_stage_highlights")
    if large_fraction > config.max_large_bright_fraction:
        negative_reasons.append("large_bright_effect")
    if line_height_cv > config.max_line_height_cv:
        negative_reasons.append("inconsistent_component_heights")
    return TriggerNotificationFeatureVector(
        side=side,
        analysis_roi_id=analysis_roi.roi_id,
        analysis_bbox=(analysis_roi.x, analysis_roi.y, analysis_roi.x2, analysis_roi.y2),
        brightness_contrast=round(brightness_contrast, 6),
        edge_density=round(edge_density, 6),
        component_count=len(components),
        aligned_component_count=aligned_count,
        line_span_ratio=round(line_span, 6),
        secondary_aligned_component_count=secondary_aligned_count,
        secondary_line_span_ratio=round(secondary_line_span, 6),
        secondary_line_height_cv=round(secondary_line_height_cv, 6),
        line_separation_ratio=round(line_separation_ratio, 6),
        primary_glyph_like_count=primary_glyph_like_count,
        secondary_glyph_like_count=secondary_glyph_like_count,
        line_height_cv=round(line_height_cv, 6),
        panel_occupancy=round(panel_occupancy, 6),
        text_region_occupancy=round(text_occupancy, 6),
        icon_region_occupancy=round(icon_occupancy, 6),
        large_bright_fraction=round(large_fraction, 6),
        panel_score=round(panel_score, 6),
        text_score=round(text_score, 6),
        icon_score=round(icon_score, 6),
        structural_weak=structural_weak,
        structural_strong=structural_strong,
        negative_reasons=tuple(negative_reasons),
    )
