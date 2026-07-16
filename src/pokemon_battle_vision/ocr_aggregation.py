"""以多影格一致性、OCR confidence 與影像品質聚合 raw OCR results。"""

from collections import defaultdict
from typing import DefaultDict, Dict, List, Mapping, Sequence, Tuple

from .checkpoint1c_models import OcrAggregate
from .ocr_normalization import cjk_character_count, comparable_text, line_count


def edit_distance(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1]
                    + (0 if left_character == right_character else 1),
                )
            )
        previous = current
    return previous[-1]


def _cjk_overlap(left: str, right: str) -> float:
    left_chars = [character for character in comparable_text(left) if cjk_character_count(character)]
    right_chars = [character for character in comparable_text(right) if cjk_character_count(character)]
    if not left_chars and not right_chars:
        return 1.0
    if not left_chars or not right_chars:
        return 0.0
    left_counts: DefaultDict[str, int] = defaultdict(int)
    right_counts: DefaultDict[str, int] = defaultdict(int)
    for character in left_chars:
        left_counts[character] += 1
    for character in right_chars:
        right_counts[character] += 1
    overlap = sum(min(count, right_counts[character]) for character, count in left_counts.items())
    return 2.0 * overlap / float(len(left_chars) + len(right_chars))


def text_similarity(left: str, right: str) -> float:
    left_value = comparable_text(left)
    right_value = comparable_text(right)
    if left_value == right_value:
        return 1.0
    if not left_value or not right_value:
        return 0.0
    edit = 1.0 - edit_distance(left_value, right_value) / float(max(len(left_value), len(right_value)))
    return round(max(0.0, min(1.0, 0.72 * edit + 0.28 * _cjk_overlap(left, right))), 6)


def _effective_weight(row: Mapping[str, object]) -> float:
    confidence = float(row.get("ocr_confidence", 0.0))
    frame_quality = float(row.get("frame_quality", 0.0))
    variant_quality = float(row.get("variant_quality", 0.0))
    return confidence * (0.45 + 0.35 * frame_quality + 0.2 * variant_quality)


def aggregate_candidate_results(
    event_id: str,
    event_type: str,
    raw_results: Sequence[Mapping[str, object]],
    selected_frame_count: int,
) -> OcrAggregate:
    usable = [
        row
        for row in raw_results
        if not row.get("error") and str(row.get("normalized_text", ""))
    ]
    if not usable:
        return OcrAggregate(
            event_id=event_id,
            event_type=event_type,
            best_text="",
            best_confidence=0.0,
            consensus_confidence=0.0,
            supporting_result_ids=[],
            supporting_frame_ordinals=[],
            disagreement_score=0.0,
            selected_frame_ordinal=None,
            selected_variant_id="",
            candidate_status="EMPTY",
            review_reasons=["ocr_empty"],
            nonempty_result_count=0,
            distinct_text_count=0,
            cjk_character_count=0,
            line_count=0,
        )

    clusters: List[List[Mapping[str, object]]] = []
    for row in sorted(usable, key=lambda item: str(item["result_id"])):
        matching = None
        for cluster in clusters:
            representative = max(cluster, key=_effective_weight)
            if text_similarity(
                str(row["normalized_text"]), str(representative["normalized_text"])
            ) >= 0.82:
                matching = cluster
                break
        if matching is None:
            clusters.append([row])
        else:
            matching.append(row)

    total_frames = max(1, selected_frame_count)

    def cluster_score(cluster: Sequence[Mapping[str, object]]) -> Tuple[float, str]:
        best_per_frame: Dict[int, float] = {}
        for row in cluster:
            ordinal = int(row["frame_ordinal"])
            best_per_frame[ordinal] = max(best_per_frame.get(ordinal, 0.0), _effective_weight(row))
        support_ratio = len(best_per_frame) / float(total_frames)
        confidence = sum(best_per_frame.values()) / float(max(1, len(best_per_frame)))
        return 0.58 * support_ratio + 0.42 * confidence, str(
            max(cluster, key=_effective_weight)["normalized_text"]
        )

    best_cluster = max(clusters, key=lambda cluster: cluster_score(cluster))
    representative = max(best_cluster, key=_effective_weight)
    supporting_frames = sorted({int(row["frame_ordinal"]) for row in best_cluster})
    frame_best_confidence: Dict[int, float] = {}
    for row in best_cluster:
        ordinal = int(row["frame_ordinal"])
        frame_best_confidence[ordinal] = max(
            frame_best_confidence.get(ordinal, 0.0), float(row["ocr_confidence"])
        )
    mean_confidence = sum(frame_best_confidence.values()) / float(len(frame_best_confidence))
    support_ratio = len(supporting_frames) / float(total_frames)
    dominant_ratio = len(best_cluster) / float(len(usable))
    disagreement = 1.0 - dominant_ratio
    consensus = 0.45 * mean_confidence + 0.35 * support_ratio + 0.2 * dominant_ratio
    best_text = str(representative["normalized_text"])
    reasons = []
    if disagreement > 0.4:
        reasons.append("multi_frame_disagreement")
    if cjk_character_count(best_text) < 2:
        reasons.append("too_few_cjk_characters")
    if line_count(best_text) > 3:
        reasons.append("implausible_line_structure")
    if float(representative["ocr_confidence"]) < 0.65:
        reasons.append("low_confidence")
    return OcrAggregate(
        event_id=event_id,
        event_type=event_type,
        best_text=best_text,
        best_confidence=round(float(representative["ocr_confidence"]), 6),
        consensus_confidence=round(max(0.0, min(1.0, consensus)), 6),
        supporting_result_ids=sorted(str(row["result_id"]) for row in best_cluster),
        supporting_frame_ordinals=supporting_frames,
        disagreement_score=round(max(0.0, min(1.0, disagreement)), 6),
        selected_frame_ordinal=int(representative["frame_ordinal"]),
        selected_variant_id=str(representative["variant_id"]),
        candidate_status="AGGREGATED",
        review_reasons=reasons,
        nonempty_result_count=len(usable),
        distinct_text_count=len({str(row["normalized_text"]) for row in usable}),
        cjk_character_count=cjk_character_count(best_text),
        line_count=line_count(best_text),
    )

