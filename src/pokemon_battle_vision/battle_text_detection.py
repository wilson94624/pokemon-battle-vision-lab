"""將 template 與文字列 features 組合成 BATTLE_TEXT proposal evidence。"""

from dataclasses import asdict, dataclass
from typing import Any, Dict, List

import numpy as np

from .battle_text_features import (
    DEFAULT_BATTLE_TEXT_FEATURE_CONFIG,
    BattleTextFeatureConfig,
    extract_battle_text_features,
)
from .battle_text_layout import layout_hamming_distance


@dataclass(frozen=True)
class BattleTextProposalConfig:
    template_floor: float = 0.68
    template_positive: float = 0.725
    proposal_threshold: float = 0.5
    strong_score_floor: float = 0.72
    weak_template_floor: float = 0.35
    structural_strong_floor: float = 0.40
    structural_weak_floor: float = 0.15
    max_structural_strong_large_bright_fraction: float = 0.32
    feature_config: BattleTextFeatureConfig = DEFAULT_BATTLE_TEXT_FEATURE_CONFIG

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


DEFAULT_BATTLE_TEXT_CONFIG = BattleTextProposalConfig()


@dataclass(frozen=True)
class BattleTextEvidence:
    template_similarity: float
    template_strength: float
    visual_structure_strength: float
    proposal_score: float
    threshold: float
    raw_positive: bool
    strong_positive: bool
    weak_positive: bool
    evidence_level: str
    positive_reasons: List[str]
    negative_reasons: List[str]
    local_edge_density: float
    top_row_density: float
    component_count: int
    low_saturation_ratio_60: float
    low_saturation_ratio_90: float
    layout_hash: str
    layout_fingerprint: Dict[str, Any]
    text_line_strength: float
    aligned_component_count: int
    line_span_ratio: float
    line_height_cv: float
    text_mask_ratio: float
    large_bright_fraction: float
    dark_background_ratio: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def analyze_battle_text_crop(
    crop: np.ndarray,
    template_similarity: float,
    config: BattleTextProposalConfig = DEFAULT_BATTLE_TEXT_CONFIG,
) -> BattleTextEvidence:
    if crop.size == 0:
        raise ValueError("battle_text crop 不可為空")
    features = extract_battle_text_features(crop, config.feature_config)
    template_denominator = config.template_positive - config.template_floor
    template_strength = _clamp(
        0.5 * (float(template_similarity) - config.template_floor) / template_denominator
    )
    visual_strength = float(features.text_line_strength)
    structural_strong = (
        features.structural_text
        and visual_strength >= config.structural_strong_floor
        and features.large_bright_fraction
        <= config.max_structural_strong_large_bright_fraction
    )
    structural_weak = (
        features.structural_text
        and visual_strength >= config.structural_weak_floor
    )
    strong_positive = structural_strong or (
        template_strength >= config.proposal_threshold
        and features.template_supporting_text
    )
    weak_positive = (
        not strong_positive
        and (
            structural_weak
            or (
                template_strength >= config.weak_template_floor
                and features.weak_text_structure
            )
        )
    )
    if strong_positive:
        evidence_level = "strong"
        proposal_score = max(config.strong_score_floor, template_strength, visual_strength)
    elif weak_positive:
        evidence_level = "weak"
        proposal_score = config.proposal_threshold
    else:
        evidence_level = "negative"
        proposal_score = min(
            config.proposal_threshold - 0.001,
            max(template_strength * 0.49, visual_strength * 0.49),
        )
    reasons = []
    if strong_positive and structural_strong:
        reasons.append("horizontal_text_structure")
    if strong_positive and template_strength >= config.proposal_threshold:
        reasons.append("template_with_text_support")
    if weak_positive:
        reasons.append(
            "weak_horizontal_text_structure"
            if structural_weak
            else "weak_template_with_text_support"
        )
    fingerprint = features.layout_fingerprint.to_dict()
    return BattleTextEvidence(
        template_similarity=round(float(template_similarity), 6),
        template_strength=round(template_strength, 6),
        visual_structure_strength=round(visual_strength, 6),
        proposal_score=round(proposal_score, 6),
        threshold=config.proposal_threshold,
        raw_positive=evidence_level != "negative",
        strong_positive=strong_positive,
        weak_positive=weak_positive,
        evidence_level=evidence_level,
        positive_reasons=reasons,
        negative_reasons=list(features.negative_reasons),
        local_edge_density=features.local_edge_density,
        top_row_density=features.top_row_density,
        component_count=features.text_component_count,
        low_saturation_ratio_60=features.low_saturation_ratio_60,
        low_saturation_ratio_90=features.low_saturation_ratio_90,
        layout_hash=features.layout_fingerprint.layout_hash,
        layout_fingerprint=fingerprint,
        text_line_strength=features.text_line_strength,
        aligned_component_count=features.aligned_component_count,
        line_span_ratio=features.line_span_ratio,
        line_height_cv=features.line_height_cv,
        text_mask_ratio=features.text_mask_ratio,
        large_bright_fraction=features.large_bright_fraction,
        dark_background_ratio=features.dark_background_ratio,
    )
