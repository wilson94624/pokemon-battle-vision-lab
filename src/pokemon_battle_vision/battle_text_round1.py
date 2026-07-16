"""Round-1 人工樣本的時間區間回歸；正式 detector 不得匯入本模組。"""

import csv
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from .errors import InputError
from .models import FrameTimestampIndex


def load_round1_fixture(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise InputError("找不到 BATTLE_TEXT round-1 fixture：{}".format(path))
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("production_usage_forbidden") is not True:
        raise InputError("round-1 fixture 必須明確禁止 production inference 使用")
    cases = payload.get("baseline_cases")
    if not isinstance(cases, list) or len(cases) != 35:
        raise InputError("round-1 fixture 必須包含 35 個 baseline cases")
    return payload


def _overlap(start: float, end: float, event: Mapping[str, Any]) -> float:
    return max(
        0.0,
        min(end, float(event["end_time"])) - max(start, float(event["start_time"])),
    )


def _expected_spans(case: Mapping[str, Any]) -> List[Dict[str, float]]:
    spans = case.get("expected_text_visible_spans") or [
        {"start_time": case["start_time"], "end_time": case["end_time"]}
    ]
    return [
        {"start_time": float(span["start_time"]), "end_time": float(span["end_time"])}
        for span in spans
    ]


def build_round1_mapping(
    fixture: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    battle_events = [event for event in events if event["type"] == "BATTLE_TEXT"]
    rows = []
    for case in fixture["baseline_cases"]:
        spans = _expected_spans(case)
        span_mappings = []
        mapped_ids: List[str] = []
        for span in spans:
            matches = [
                event
                for event in battle_events
                if _overlap(span["start_time"], span["end_time"], event) > 0.0
            ]
            ids = [str(event["event_id"]) for event in matches]
            mapped_ids.extend(ids)
            best = max(
                matches,
                key=lambda event: _overlap(
                    span["start_time"], span["end_time"], event
                ),
                default=None,
            )
            span_mappings.append(
                {
                    **span,
                    "mapped_candidate_ids": ids,
                    "best_candidate_id": str(best["event_id"]) if best else "",
                    "new_start_time": float(best["start_time"]) if best else None,
                    "new_end_time": float(best["end_time"]) if best else None,
                }
            )
        unique_ids = list(dict.fromkeys(mapped_ids))
        category = str(case["human_category"])
        if category == "false_positive":
            result = "removed" if not unique_ids else "retained"
        else:
            result = (
                "covered"
                if all(mapping["mapped_candidate_ids"] for mapping in span_mappings)
                else "missing"
            )
        old_start_error = sum(
            abs(float(case["start_time"]) - span["start_time"]) for span in spans
        )
        old_end_error = sum(
            abs(float(case["end_time"]) - span["end_time"]) for span in spans
        )
        new_start_error = sum(
            abs(mapping["new_start_time"] - mapping["start_time"])
            for mapping in span_mappings
            if mapping["new_start_time"] is not None
        )
        new_end_error = sum(
            abs(mapping["new_end_time"] - mapping["end_time"])
            for mapping in span_mappings
            if mapping["new_end_time"] is not None
        )
        rows.append(
            {
                "baseline_candidate_id": str(case["candidate_id"]),
                "human_category": category,
                "old_start_time": float(case["start_time"]),
                "old_end_time": float(case["end_time"]),
                "expected_text_visible_spans": spans,
                "mapped_candidate_ids": unique_ids,
                "mapping_result": result,
                "span_mappings": span_mappings,
                "old_boundary_error_sec": round(old_start_error + old_end_error, 6),
                "new_boundary_error_sec": round(new_start_error + new_end_error, 6),
                "boundary_improved": bool(unique_ids)
                and new_start_error + new_end_error < old_start_error + old_end_error,
            }
        )
    false_rows = [row for row in rows if row["human_category"] == "false_positive"]
    accepted_rows = [row for row in rows if row["human_category"] == "accepted"]
    case_0033 = next(
        row for row in rows if row["baseline_candidate_id"] == "battle_text-0033"
    )
    mapped_sets = [set(span["mapped_candidate_ids"]) for span in case_0033["span_mappings"]]
    multi_text_split = (
        len(mapped_sets) == 2
        and all(mapped_sets)
        and mapped_sets[0].isdisjoint(mapped_sets[1])
    )
    pair = fixture["diagnostic_questions"]["possible_over_split_pair"]
    interruption = pair["interruption_span"]
    bridging = [
        str(event["event_id"])
        for event in battle_events
        if float(event["start_time"]) <= float(interruption["start_time"])
        and float(event["end_time"]) >= float(interruption["end_time"])
    ]
    return {
        "schema_version": "0.1.0",
        "kind": "battle_text_round1_regression",
        "production_inference_used_fixture": False,
        "baseline_case_count": len(rows),
        "new_battle_text_candidate_count": len(battle_events),
        "false_positive_removal": {
            "reviewed_count": len(false_rows),
            "removed_count": sum(row["mapping_result"] == "removed" for row in false_rows),
            "retained": [
                row["baseline_candidate_id"]
                for row in false_rows
                if row["mapping_result"] == "retained"
            ],
        },
        "accepted_preservation": {
            "reviewed_count": len(accepted_rows),
            "covered_count": sum(row["mapping_result"] == "covered" for row in accepted_rows),
            "missing": [
                row["baseline_candidate_id"]
                for row in accepted_rows
                if row["mapping_result"] != "covered"
            ],
        },
        "case_0033_multi_text_split": {
            "success": multi_text_split,
            "span_mappings": case_0033["span_mappings"],
        },
        "case_0021_0022_visual_decision": {
            "decision": "merge" if bridging else "keep_split",
            "bridging_candidate_ids": bridging,
            "reason": (
                "iOS 控制中心短暫遮擋前後的 layout fingerprint 高度一致；"
                "通用 same-layout reopen 規則已合併，未讀取文字內容。"
                if bridging
                else "遮擋前後沒有通過通用 same-layout reopen 規則，因此維持分段。"
            ),
        },
        "rows": rows,
    }


def build_round1_reference_frames(
    fixture: Mapping[str, Any], timestamp_index: FrameTimestampIndex
) -> Dict[str, List[int]]:
    result = {}
    for case in fixture["baseline_cases"]:
        frames = []
        for span in _expected_spans(case):
            midpoint = (span["start_time"] + span["end_time"]) / 2.0
            frames.append(timestamp_index.nearest_ordinal(midpoint))
        result[str(case["candidate_id"])] = list(dict.fromkeys(frames))
    return result


def write_round1_mapping_csv(path: Path, report: Mapping[str, Any]) -> None:
    rows = []
    for row in report["rows"]:
        rows.append(
            {
                "baseline_candidate_id": row["baseline_candidate_id"],
                "human_category": row["human_category"],
                "old_start_time": row["old_start_time"],
                "old_end_time": row["old_end_time"],
                "mapped_candidate_ids": json.dumps(row["mapped_candidate_ids"]),
                "mapping_result": row["mapping_result"],
                "old_boundary_error_sec": row["old_boundary_error_sec"],
                "new_boundary_error_sec": row["new_boundary_error_sec"],
                "boundary_improved": row["boundary_improved"],
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    os.replace(str(temporary), str(path))
