"""事後評估人工 fixture；本模組不得參與正式 OCR inference。"""

from collections import Counter
from typing import Any, Dict, Mapping, Sequence

from .ocr_normalization import comparable_text


def evaluate_initial_fixture(
    fixture: Mapping[str, Any],
    validations: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    """在所有 validation 完成後比較人工期望，結果只供報告與回歸測試。"""
    if fixture.get("production_usage_forbidden") is not True:
        raise ValueError("Checkpoint 1C evaluation fixture 必須禁止 production usage")
    by_id = {str(row["event_id"]): row for row in validations}
    rows = []
    for case in fixture.get("cases", []):
        candidate_ids = [str(value) for value in case["candidate_ids"]]
        missing_ids = [value for value in candidate_ids if value not in by_id]
        observed = [by_id[value] for value in candidate_ids if value in by_id]
        observed_labels = [str(row["validation_label"]) for row in observed]
        expected_labels = set(str(value) for value in case["expected_labels"])
        labels_match = bool(observed) and all(
            label in expected_labels for label in observed_labels
        )
        fragments = [str(value) for value in case.get("expected_text_fragments", [])]
        observed_text = "\n".join(str(row.get("ocr_text", "")) for row in observed)
        comparable_observed = comparable_text(observed_text)
        fragments_match = all(
            comparable_text(fragment) in comparable_observed for fragment in fragments
        )
        expected_duplicate = case.get("expected_duplicate_marking")
        duplicate_match = True
        if expected_duplicate is not None:
            duplicate_match = all(
                bool(row.get("duplicate_group_id")) is bool(expected_duplicate)
                for row in observed
            )
        passed = (
            not missing_ids and labels_match and fragments_match and duplicate_match
        )
        rows.append(
            {
                "case_id": str(case["case_id"]),
                "diagnostic_category": str(case["diagnostic_category"]),
                "candidate_ids": candidate_ids,
                "expected_labels": sorted(expected_labels),
                "observed_labels": observed_labels,
                "expected_text_fragments": fragments,
                "observed_text": observed_text,
                "missing_candidate_ids": missing_ids,
                "labels_match": labels_match,
                "text_fragments_match": fragments_match,
                "duplicate_marking_match": duplicate_match,
                "passed": passed,
            }
        )
    counts = Counter("passed" if row["passed"] else "failed" for row in rows)
    return {
        "schema_version": "0.1.0",
        "checkpoint": "1C",
        "kind": "checkpoint1c_initial_evaluation_report",
        "inference_feedback_used": False,
        "case_count": len(rows),
        "passed_count": counts["passed"],
        "failed_count": counts["failed"],
        "records": rows,
    }
