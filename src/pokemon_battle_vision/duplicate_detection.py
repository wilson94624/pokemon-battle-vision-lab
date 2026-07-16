"""相鄰 OCR 文字的輕量 duplicate hint；只標記，不合併或刪除。"""

from dataclasses import replace
from typing import Dict, List, Sequence, Tuple

from .checkpoint1c_models import TextValidationRecord
from .ocr_aggregation import text_similarity


def mark_possible_duplicates(
    records: Sequence[TextValidationRecord],
    maximum_gap_sec: float = 0.8,
    minimum_similarity: float = 0.88,
) -> Tuple[List[TextValidationRecord], List[Dict[str, object]]]:
    ordered = sorted(records, key=lambda row: (row.start_time, row.event_id))
    parent = list(range(len(ordered)))
    edge_confidence: Dict[int, float] = {}
    duplicate_of: Dict[int, str] = {}

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for index in range(1, len(ordered)):
        previous = ordered[index - 1]
        current = ordered[index]
        gap = current.start_time - previous.end_time
        if (
            current.event_type != previous.event_type
            or current.validation_label == "NO_TEXT"
            or previous.validation_label == "NO_TEXT"
            or gap < -0.05
            or gap > maximum_gap_sec
            or not previous.ocr_text
            or not current.ocr_text
        ):
            continue
        similarity = text_similarity(previous.ocr_text, current.ocr_text)
        if similarity < minimum_similarity:
            continue
        temporal_score = max(0.0, 1.0 - max(0.0, gap) / maximum_gap_sec)
        confidence = round(0.8 * similarity + 0.2 * temporal_score, 6)
        union(index - 1, index)
        duplicate_of[index] = previous.event_id
        edge_confidence[index] = confidence

    groups: Dict[int, List[int]] = {}
    for index in range(len(ordered)):
        root = find(index)
        groups.setdefault(root, []).append(index)
    duplicate_groups = [indices for indices in groups.values() if len(indices) > 1]
    group_id_by_index: Dict[int, str] = {}
    summaries: List[Dict[str, object]] = []
    for sequence, indices in enumerate(duplicate_groups, start=1):
        group_id = "duplicate-group-{:04d}".format(sequence)
        for index in indices:
            group_id_by_index[index] = group_id
        summaries.append(
            {
                "duplicate_group_id": group_id,
                "event_ids": [ordered[index].event_id for index in indices],
                "automatic_merge_performed": False,
                "maximum_confidence": max(
                    (edge_confidence.get(index, 0.0) for index in indices), default=0.0
                ),
            }
        )

    updated: List[TextValidationRecord] = []
    for index, record in enumerate(ordered):
        if index not in group_id_by_index:
            updated.append(record)
            continue
        reasons = list(dict.fromkeys(record.review_reasons + ["suspected_duplicate"]))
        updated.append(
            replace(
                record,
                duplicate_group_id=group_id_by_index[index],
                possible_duplicate_of=duplicate_of.get(index),
                duplicate_confidence=edge_confidence.get(index, 0.0),
                workflow_status="needs_review",
                review_reasons=reasons,
            )
        )
    by_id = {row.event_id: row for row in updated}
    return [by_id[row.event_id] for row in records], summaries
