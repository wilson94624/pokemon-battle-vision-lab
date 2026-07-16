"""保守的 Text Validation Gate；OCR 失敗不等同永久刪除。"""

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Sequence

from .checkpoint1c_models import OcrAggregate, TextValidationRecord
from .ocr_normalization import cjk_character_count, comparable_text


@dataclass(frozen=True)
class TextValidationConfig:
    minimum_cjk_characters: int = 2
    minimum_valid_confidence: float = 0.55
    minimum_valid_consensus: float = 0.5
    auto_accept_confidence: float = 0.85
    auto_accept_consensus: float = 0.76
    no_text_max_visual_strength: float = 0.68
    no_text_min_frames: int = 2
    no_text_min_variants_per_frame: int = 2
    noise_max_ocr_confidence: float = 0.5
    no_text_max_template_strength: float = 0.1
    minimum_battle_text_cjk_ratio: float = 0.6


DEFAULT_TEXT_VALIDATION_CONFIG = TextValidationConfig()


def validate_candidate_text(
    event: Mapping[str, Any],
    aggregate: OcrAggregate,
    raw_results: Sequence[Mapping[str, Any]],
    config: TextValidationConfig = DEFAULT_TEXT_VALIDATION_CONFIG,
) -> TextValidationRecord:
    event_type = str(event["type"])
    errors = [row for row in raw_results if row.get("error")]
    frame_ordinals = {int(row["frame_ordinal"]) for row in raw_results}
    variants_by_frame: Dict[int, set] = {}
    for row in raw_results:
        variants_by_frame.setdefault(int(row["frame_ordinal"]), set()).add(
            str(row["variant_id"])
        )
    max_visual = max(
        (float(row.get("visual_text_strength", 0.0)) for row in raw_results),
        default=0.0,
    )
    all_empty = bool(raw_results) and all(
        not str(row.get("normalized_text", "")) for row in raw_results
    )
    all_raw_cjk_count = sum(
        int(row.get("cjk_character_count", 0)) for row in raw_results
    )
    maximum_raw_confidence = max(
        (float(row.get("ocr_confidence", 0.0)) for row in raw_results), default=0.0
    )
    maximum_template_strength = max(
        (float(row.get("detector_template_strength", 0.0)) for row in raw_results),
        default=0.0,
    )
    enough_empty_evidence = (
        len(frame_ordinals) >= config.no_text_min_frames
        and variants_by_frame
        and min(len(values) for values in variants_by_frame.values())
        >= config.no_text_min_variants_per_frame
    )
    reasons = list(aggregate.review_reasons)
    if errors:
        reasons.append("engine_error")
    cjk_frame_ordinals = {
        int(row["frame_ordinal"])
        for row in raw_results
        if int(row.get("cjk_character_count", 0)) > 0
    }
    if cjk_frame_ordinals and cjk_frame_ordinals != frame_ordinals:
        reasons.append("fade_in_or_fade_out")

    valid_support = (
        aggregate.cjk_character_count >= config.minimum_cjk_characters
        and aggregate.best_confidence >= config.minimum_valid_confidence
        and aggregate.consensus_confidence >= config.minimum_valid_consensus
        and (
            len(aggregate.supporting_frame_ordinals) >= 2
            or len(frame_ordinals) == 1
        )
    )
    comparable = comparable_text(aggregate.best_text)
    cjk_ratio = (
        cjk_character_count(comparable) / float(len(comparable)) if comparable else 0.0
    )
    text_lines = [line for line in aggregate.best_text.splitlines() if line]
    numeric_lines = sum(
        1
        for line in text_lines
        if any(character.isdigit() for character in line)
        and cjk_character_count(line) == 0
    )
    # 多影格／多 variant 只讀到低信心非 CJK 雜訊，是比單純空字串更常見的純特效型態。
    stable_non_text_noise = (
        event_type == "BATTLE_TEXT"
        and enough_empty_evidence
        and not errors
        and all_raw_cjk_count == 0
        and maximum_raw_confidence <= config.noise_max_ocr_confidence
        and maximum_template_strength <= config.no_text_max_template_strength
    )
    # 中央 battle ROI 若穩定讀到數字狀態列加少量周邊 UI 字樣，不能當 battle message。
    implausible_battle_ui = (
        event_type == "BATTLE_TEXT"
        and enough_empty_evidence
        and not errors
        and numeric_lines > 0
        and cjk_ratio < config.minimum_battle_text_cjk_ratio
    )
    if stable_non_text_noise or implausible_battle_ui:
        label = "NO_TEXT"
        validation_confidence = min(
            0.98,
            0.78
            + 0.08 * min(1.0, len(frame_ordinals) / 3.0)
            + 0.08 * (1.0 - maximum_raw_confidence),
        )
        workflow = "rejected"
        reasons.append("suspected_visual_effect")
        if stable_non_text_noise:
            reasons.extend(["ocr_empty" if all_empty else "too_few_cjk_characters"])
        if implausible_battle_ui:
            reasons.append("implausible_line_structure")
    elif valid_support and not errors:
        label = "VALID_TEXT"
        validation_confidence = min(
            1.0, 0.55 * aggregate.best_confidence + 0.45 * aggregate.consensus_confidence
        )
        auto_accept = (
            aggregate.best_confidence >= config.auto_accept_confidence
            and aggregate.consensus_confidence >= config.auto_accept_consensus
            and aggregate.disagreement_score <= 0.35
        )
        workflow = "auto_accepted" if auto_accept else "needs_review"
        if not auto_accept:
            reasons.append("low_confidence")
    elif (
        event_type != "TRIGGER_NOTIFICATION"
        and all_empty
        and enough_empty_evidence
        and not errors
        and max_visual <= config.no_text_max_visual_strength
        and maximum_template_strength <= config.no_text_max_template_strength
    ):
        label = "NO_TEXT"
        validation_confidence = min(
            1.0,
            0.65
            + 0.2 * (1.0 - max_visual)
            + 0.15 * min(1.0, len(frame_ordinals) / 3.0),
        )
        workflow = "rejected"
        reasons.extend(["ocr_empty", "suspected_visual_effect"])
    else:
        label = "UNCERTAIN"
        validation_confidence = max(
            0.2, min(0.75, aggregate.consensus_confidence)
        )
        workflow = "needs_review"
        if all_empty:
            reasons.append("ocr_empty")
        if aggregate.cjk_character_count < config.minimum_cjk_characters:
            reasons.append("too_few_cjk_characters")
        if 0 < aggregate.cjk_character_count < config.minimum_cjk_characters:
            reasons.append("partial_text")
        if aggregate.best_confidence < config.minimum_valid_confidence:
            reasons.append("low_confidence")
        if max_visual > config.no_text_max_visual_strength and all_empty:
            reasons.append("partial_text")
    reasons = list(dict.fromkeys(reasons))
    return TextValidationRecord(
        event_id=str(event["event_id"]),
        event_type=event_type,
        start_time=float(event["start_time"]),
        end_time=float(event["end_time"]),
        validation_label=label,
        workflow_status=workflow,
        ocr_text=aggregate.best_text,
        ocr_confidence=aggregate.best_confidence,
        consensus_confidence=aggregate.consensus_confidence,
        validation_confidence=round(validation_confidence, 6),
        review_reasons=reasons,
        supporting_result_ids=aggregate.supporting_result_ids,
    )
