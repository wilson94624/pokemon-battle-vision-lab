"""將 template 與 panel／text／icon features 組合成 trigger proposals。"""

from dataclasses import asdict, dataclass
from typing import Any, Dict

import numpy as np

from .models import PixelRoi
from .trigger_notification_features import (
    DEFAULT_TRIGGER_FEATURE_CONFIG,
    TriggerNotificationFeatureConfig,
    extract_trigger_notification_features,
)


@dataclass(frozen=True)
class TriggerNotificationProposalConfig:
    proposal_threshold: float = 0.50
    strong_score_floor: float = 0.72
    strong_combined_floor: float = 0.68
    weak_combined_floor: float = 0.47
    template_strong_floor: float = 0.82
    template_weak_floor: float = 0.76
    feature_config: TriggerNotificationFeatureConfig = DEFAULT_TRIGGER_FEATURE_CONFIG

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


DEFAULT_TRIGGER_PROPOSAL_CONFIG = TriggerNotificationProposalConfig()


@dataclass(frozen=True)
class TriggerNotificationEvidence:
    side: str
    canonical_roi_id: str
    analysis_roi_id: str
    analysis_bbox: list[int]
    template_score: float
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
    combined_score: float
    proposal_score: float
    threshold: float
    raw_positive: bool
    strong_positive: bool
    weak_positive: bool
    continuation_support: bool
    evidence_level: str
    positive_reasons: list[str]
    negative_reasons: list[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def analyze_trigger_notification_crop(
    analysis_crop: np.ndarray,
    side: str,
    canonical_roi_id: str,
    analysis_roi: PixelRoi,
    template_score: float,
    config: TriggerNotificationProposalConfig = DEFAULT_TRIGGER_PROPOSAL_CONFIG,
) -> TriggerNotificationEvidence:
    features = extract_trigger_notification_features(
        analysis_crop, side, analysis_roi, config.feature_config
    )
    brightness_score = _clamp((features.brightness_contrast - 0.08) / 0.25)
    template_support = _clamp((float(template_score) - 0.65) / 0.25)
    # icon 是加分項；短版能力通知即使沒有大型圓形 icon 仍可由文字結構成立。
    combined = _clamp(
        0.56 * features.text_score
        + 0.18 * features.panel_score
        + 0.12 * brightness_score
        + 0.09 * template_support
        + 0.05 * features.icon_score
    )
    strong = (
        features.structural_strong and combined >= config.strong_combined_floor
    ) or (
        float(template_score) >= config.template_strong_floor
        and features.structural_weak
    )
    weak = not strong and (
        (features.structural_weak and combined >= config.weak_combined_floor)
        or (
            float(template_score) >= config.template_weak_floor
            and features.structural_weak
        )
    )
    continuation_support = (
        not strong
        and not weak
        and features.primary_glyph_like_count
        >= config.feature_config.min_primary_glyph_like_components
        and config.feature_config.min_strong_line_span
        <= features.line_span_ratio
        <= config.feature_config.max_notification_line_span
        and features.edge_density >= config.feature_config.min_edge_density
        and features.text_region_occupancy
        >= config.feature_config.min_weak_text_occupancy
        and features.panel_occupancy
        >= config.feature_config.min_notification_panel_occupancy
        and combined >= 0.46
    )
    if strong:
        level = "strong"
        proposal_score = max(config.strong_score_floor, combined, float(template_score))
    elif weak:
        level = "weak"
        proposal_score = config.proposal_threshold
    elif continuation_support:
        level = "continuation"
        proposal_score = config.proposal_threshold - 0.001
    else:
        level = "negative"
        proposal_score = min(
            config.proposal_threshold - 0.001,
            max(0.0, 0.49 * combined, 0.24 * float(template_score)),
        )
    reasons = []
    if features.structural_strong:
        reasons.append("stable_horizontal_text_line")
    elif features.structural_weak:
        reasons.append("weak_horizontal_text_line")
    if features.panel_score >= 0.45:
        reasons.append("dark_notification_panel")
    if float(template_score) >= config.template_strong_floor:
        reasons.append("approved_template_support")
    if features.icon_score >= 0.45:
        reasons.append("optional_icon_support")
    return TriggerNotificationEvidence(
        side=side,
        canonical_roi_id=canonical_roi_id,
        analysis_roi_id=features.analysis_roi_id,
        analysis_bbox=list(features.analysis_bbox),
        template_score=round(float(template_score), 6),
        brightness_contrast=features.brightness_contrast,
        edge_density=features.edge_density,
        component_count=features.component_count,
        aligned_component_count=features.aligned_component_count,
        line_span_ratio=features.line_span_ratio,
        secondary_aligned_component_count=features.secondary_aligned_component_count,
        secondary_line_span_ratio=features.secondary_line_span_ratio,
        secondary_line_height_cv=features.secondary_line_height_cv,
        line_separation_ratio=features.line_separation_ratio,
        primary_glyph_like_count=features.primary_glyph_like_count,
        secondary_glyph_like_count=features.secondary_glyph_like_count,
        line_height_cv=features.line_height_cv,
        panel_occupancy=features.panel_occupancy,
        text_region_occupancy=features.text_region_occupancy,
        icon_region_occupancy=features.icon_region_occupancy,
        large_bright_fraction=features.large_bright_fraction,
        panel_score=features.panel_score,
        text_score=features.text_score,
        icon_score=features.icon_score,
        combined_score=round(combined, 6),
        proposal_score=round(proposal_score, 6),
        threshold=config.proposal_threshold,
        raw_positive=level in ("strong", "weak"),
        strong_positive=strong,
        weak_positive=weak,
        continuation_support=continuation_support,
        evidence_level=level,
        positive_reasons=reasons,
        negative_reasons=list(features.negative_reasons),
    )
