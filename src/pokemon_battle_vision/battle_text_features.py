"""BATTLE_TEXT 白字、字形元件與水平文字列的 classical CV features。"""

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Tuple

import cv2
import numpy as np

from .battle_text_layout import (
    DEFAULT_BATTLE_TEXT_LAYOUT_CONFIG,
    BattleTextLayoutFingerprint,
    build_layout_fingerprint,
)


@dataclass(frozen=True)
class BattleTextFeatureConfig:
    focus_width_fraction: float = 0.50
    focus_top_fraction: float = 0.08
    focus_bottom_fraction: float = 0.90
    local_blur_sigma: float = 8.0
    local_delta_threshold: int = 8
    max_saturation: int = 90
    min_value: int = 130
    max_local_background: int = 145
    min_component_width: int = 2
    max_component_width: int = 45
    min_component_height: int = 3
    max_component_height: int = 36
    min_component_area: int = 5
    max_component_area: int = 600
    large_component_area: int = 1000
    large_component_width: int = 80
    large_component_height: int = 55
    line_half_window: int = 10
    min_structural_components: int = 7
    min_template_supported_components: int = 6
    min_weak_components: int = 3
    min_structural_span: float = 0.18
    min_template_supported_span: float = 0.12
    min_weak_span: float = 0.08
    max_line_height_cv: float = 0.95
    min_mask_ratio: float = 0.0005
    max_mask_ratio: float = 0.14
    max_large_bright_fraction: float = 0.38
    max_weak_large_bright_fraction: float = 0.45
    sparse_wide_min_span: float = 0.70
    sparse_wide_min_components: int = 7
    sparse_wide_max_components: int = 14
    sparse_wide_max_mask_ratio: float = 0.035
    sparse_wide_max_large_fraction: float = 0.10
    sparse_ultra_wide_min_span: float = 0.88
    sparse_ultra_wide_max_mask_ratio: float = 0.04
    sparse_ultra_wide_max_large_fraction: float = 0.25

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


DEFAULT_BATTLE_TEXT_FEATURE_CONFIG = BattleTextFeatureConfig()


@dataclass(frozen=True)
class BattleTextFeatureVector:
    text_line_strength: float
    text_component_count: int
    aligned_component_count: int
    line_span_ratio: float
    line_height_cv: float
    text_mask_ratio: float
    large_bright_fraction: float
    dark_background_ratio: float
    local_edge_density: float
    top_row_density: float
    low_saturation_ratio_60: float
    low_saturation_ratio_90: float
    structural_text: bool
    template_supporting_text: bool
    weak_text_structure: bool
    negative_reasons: Tuple[str, ...]
    layout_fingerprint: BattleTextLayoutFingerprint

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["negative_reasons"] = list(self.negative_reasons)
        return payload


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _best_horizontal_line(
    components: List[Tuple[int, int, int, int, int]],
    focus_width: int,
    focus_height: int,
    half_window: int,
) -> Tuple[int, float, float]:
    best_count = 0
    best_span = 0.0
    best_height_cv = 9.0
    for center_y in range(focus_height):
        line = [
            component
            for component in components
            if abs((component[1] + component[3] / 2.0) - center_y) <= half_window
        ]
        if not line:
            continue
        left = min(component[0] for component in line)
        right = max(component[0] + component[2] for component in line)
        span = (right - left) / float(max(1, focus_width))
        heights = np.asarray([component[3] for component in line], dtype=np.float32)
        height_cv = float(np.std(heights) / max(1.0, float(np.mean(heights))))
        quality = (len(line), span, -height_cv)
        current = (best_count, best_span, -best_height_cv)
        if quality > current:
            best_count = len(line)
            best_span = span
            best_height_cv = height_cv
    return best_count, best_span, best_height_cv


def extract_battle_text_features(
    crop: np.ndarray,
    config: BattleTextFeatureConfig = DEFAULT_BATTLE_TEXT_FEATURE_CONFIG,
) -> BattleTextFeatureVector:
    if crop.size == 0:
        raise ValueError("battle_text crop 不可為空")
    height, width = crop.shape[:2]
    y1 = max(0, min(height - 1, int(round(height * config.focus_top_fraction))))
    y2 = max(y1 + 1, min(height, int(round(height * config.focus_bottom_fraction))))
    x2 = max(1, min(width, int(round(width * config.focus_width_fraction))))
    focus = crop[y1:y2, :x2]
    hsv = cv2.cvtColor(focus, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(focus, cv2.COLOR_BGR2GRAY)
    local_background = cv2.GaussianBlur(gray, (0, 0), config.local_blur_sigma)
    local_delta = cv2.subtract(gray, local_background)
    text_mask = (
        (hsv[:, :, 1] <= config.max_saturation)
        & (hsv[:, :, 2] >= config.min_value)
        & (local_delta >= config.local_delta_threshold)
        & (local_background <= config.max_local_background)
    )
    component_mask = text_mask.astype(np.uint8) * 255
    _, _, stats, _ = cv2.connectedComponentsWithStats(component_mask, 8)
    components: List[Tuple[int, int, int, int, int]] = []
    large_area = 0
    for x, y, component_width, component_height, area in stats[1:]:
        x = int(x)
        y = int(y)
        component_width = int(component_width)
        component_height = int(component_height)
        area = int(area)
        if (
            area >= config.large_component_area
            or component_width >= config.large_component_width
            or component_height >= config.large_component_height
        ):
            large_area += area
        if (
            config.min_component_width <= component_width <= config.max_component_width
            and config.min_component_height <= component_height <= config.max_component_height
            and config.min_component_area <= area <= config.max_component_area
        ):
            components.append((x, y, component_width, component_height, area))
    aligned_count, line_span, line_height_cv = _best_horizontal_line(
        components,
        focus.shape[1],
        focus.shape[0],
        config.line_half_window,
    )
    mask_pixels = int(np.count_nonzero(text_mask))
    mask_ratio = mask_pixels / float(max(1, text_mask.size))
    large_fraction = large_area / float(max(1, mask_pixels))
    dark_ratio = float(np.mean(local_background <= config.max_local_background))
    edges = cv2.Canny(gray, 45, 110) > 0
    local_edges = edges & (hsv[:, :, 1] <= 135) & (hsv[:, :, 2] >= 35)
    row_density = np.mean(text_mask, axis=1)
    top_count = min(20, row_density.size)
    top_row_density = float(np.mean(np.sort(row_density)[-top_count:]))
    component_strength = _clamp(aligned_count / 10.0)
    span_strength = _clamp(line_span / 0.40)
    consistency_strength = _clamp(1.0 - line_height_cv / 1.25)
    large_penalty = _clamp(
        1.0 - large_fraction / max(1e-6, config.max_large_bright_fraction)
    )
    text_line_strength = _clamp(
        (0.45 * component_strength + 0.35 * span_strength + 0.20 * consistency_strength)
        * large_penalty
    )
    common_geometry = (
        config.min_mask_ratio <= mask_ratio <= config.max_mask_ratio
        and line_height_cv <= config.max_line_height_cv
    )
    # 場地燈點與選單刻度常形成「很寬、很稀疏」的一列；真實句子通常有更密集的字形遮罩。
    sparse_component_count = (
        config.sparse_wide_min_components
        <= aligned_count
        <= config.sparse_wide_max_components
    )
    sparse_wide_highlights = sparse_component_count and (
        (
            line_span >= config.sparse_wide_min_span
            and mask_ratio <= config.sparse_wide_max_mask_ratio
            and large_fraction <= config.sparse_wide_max_large_fraction
        )
        or (
            line_span >= config.sparse_ultra_wide_min_span
            and mask_ratio <= config.sparse_ultra_wide_max_mask_ratio
            and large_fraction <= config.sparse_ultra_wide_max_large_fraction
        )
    )
    structural_text = (
        common_geometry
        and not sparse_wide_highlights
        and aligned_count >= config.min_structural_components
        and line_span >= config.min_structural_span
        and large_fraction <= config.max_large_bright_fraction
    )
    template_supporting_text = (
        common_geometry
        and not sparse_wide_highlights
        and aligned_count >= config.min_template_supported_components
        and line_span >= config.min_template_supported_span
        and large_fraction <= config.max_large_bright_fraction
    )
    weak_text_structure = (
        config.min_mask_ratio <= mask_ratio <= config.max_mask_ratio
        and not sparse_wide_highlights
        and aligned_count >= config.min_weak_components
        and line_span >= config.min_weak_span
        and large_fraction <= config.max_weak_large_bright_fraction
    )
    negative_reasons = []
    if mask_ratio < config.min_mask_ratio:
        negative_reasons.append("empty_text_mask")
    if mask_ratio > config.max_mask_ratio:
        negative_reasons.append("overbright_mask")
    if aligned_count < config.min_weak_components or line_span < config.min_weak_span:
        negative_reasons.append("no_horizontal_text_line")
    if large_fraction > config.max_weak_large_bright_fraction:
        negative_reasons.append("large_bright_blob")
    if line_height_cv > config.max_line_height_cv:
        negative_reasons.append("inconsistent_component_heights")
    if sparse_wide_highlights:
        negative_reasons.append("sparse_wide_highlights")
    fingerprint = build_layout_fingerprint(
        text_mask, len(components), DEFAULT_BATTLE_TEXT_LAYOUT_CONFIG
    )
    return BattleTextFeatureVector(
        text_line_strength=round(text_line_strength, 6),
        text_component_count=len(components),
        aligned_component_count=aligned_count,
        line_span_ratio=round(line_span, 6),
        line_height_cv=round(line_height_cv, 6),
        text_mask_ratio=round(mask_ratio, 6),
        large_bright_fraction=round(large_fraction, 6),
        dark_background_ratio=round(dark_ratio, 6),
        local_edge_density=round(float(np.mean(local_edges)), 6),
        top_row_density=round(top_row_density, 6),
        low_saturation_ratio_60=round(
            float(np.mean((hsv[:, :, 1] <= 115) & (hsv[:, :, 2] >= 60))), 6
        ),
        low_saturation_ratio_90=round(
            float(np.mean((hsv[:, :, 1] <= 100) & (hsv[:, :, 2] >= 90))), 6
        ),
        structural_text=structural_text,
        template_supporting_text=template_supporting_text,
        weak_text_structure=weak_text_structure,
        negative_reasons=tuple(negative_reasons),
        layout_fingerprint=fingerprint,
    )
