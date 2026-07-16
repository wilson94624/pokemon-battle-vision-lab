"""人工 trigger fixture 的舊／新輸出比較；不得由 production detector 匯入。"""

import json
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Mapping, Sequence

from .errors import InputError


METRIC_FIELDS = (
    "template_score",
    "brightness_contrast",
    "edge_density",
    "component_count",
    "panel_occupancy",
    "text_region_occupancy",
    "icon_region_occupancy",
    "combined_score",
)


def load_trigger_round1_fixture(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise InputError("找不到 Trigger round-1 fixture：{}".format(path))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InputError("Trigger round-1 fixture 不是有效 JSON") from exc
    windows = payload.get("positive_windows")
    if not isinstance(windows, list) or len(windows) < 2:
        raise InputError("Trigger round-1 fixture 至少需要兩個 positive windows")
    return payload


def _overlaps(event: Mapping[str, Any], window: Mapping[str, Any]) -> bool:
    return (
        str(event.get("type")) == "TRIGGER_NOTIFICATION"
        and str(window["side"]) in str(event.get("visible_rois", []))
        and float(event["end_time"]) >= float(window["approx_start_sec"])
        and float(event["start_time"]) <= float(window["approx_end_sec"])
    )


def _metric_summary(
    diagnostics: Sequence[Mapping[str, Any]], window: Mapping[str, Any]
) -> Dict[str, Any]:
    rows = [
        row
        for row in diagnostics
        if row.get("side") == window["side"]
        and float(window["approx_start_sec"]) <= float(row["pts"]) <= float(window["approx_end_sec"])
    ]
    summary: Dict[str, Any] = {"sample_count": len(rows)}
    for field in METRIC_FIELDS:
        values = [float(row.get(field, 0.0)) for row in rows]
        summary[field] = {
            "min": round(min(values), 6) if values else 0.0,
            "median": round(float(median(values)), 6) if values else 0.0,
            "max": round(max(values), 6) if values else 0.0,
        }
    summary["positive_sample_count"] = sum(
        str(row.get("evidence_level")) in ("weak", "strong") for row in rows
    )
    summary["strong_sample_count"] = sum(
        str(row.get("evidence_level")) == "strong" for row in rows
    )
    positive_flags = [
        str(row.get("evidence_level")) in ("weak", "strong", "continuation")
        for row in rows
    ]
    longest_run = current_run = 0
    for value in positive_flags:
        current_run = current_run + 1 if value else 0
        longest_run = max(longest_run, current_run)
    summary["temporal_stability"] = {
        "positive_ratio": round(sum(positive_flags) / float(max(1, len(rows))), 6),
        "longest_positive_run_samples": longest_run,
        "longest_positive_run_sec_at_10hz": round(longest_run / 10.0, 3),
    }
    summary["decisions"] = sorted({str(row.get("decision", "")) for row in rows})
    return summary


def build_trigger_round1_comparison(
    fixture: Mapping[str, Any],
    old_events: Sequence[Mapping[str, Any]],
    old_records: Sequence[Mapping[str, Any]],
    new_events: Sequence[Mapping[str, Any]],
    new_diagnostics: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    old_threshold = float(fixture["source_baseline"]["proposal_threshold"])
    baseline_count = int(
        fixture["source_baseline"]["trigger_notification_candidate_count"]
    )
    old_trigger_events = [
        event for event in old_events if event.get("type") == "TRIGGER_NOTIFICATION"
    ]
    old_event_source_valid = len(old_trigger_events) == baseline_count
    for window in fixture["positive_windows"]:
        old_matches = (
            [event for event in old_trigger_events if _overlaps(event, window)]
            if old_event_source_valid
            else []
        )
        new_matches = [event for event in new_events if _overlaps(event, window)]
        old_samples = [
            record
            for record in old_records
            if float(window["approx_start_sec"]) <= float(record.get("pts", -1)) <= float(window["approx_end_sec"])
        ]
        old_scores = []
        for record in old_samples:
            trigger_root = record.get("trigger_notification_evidence", {})
            sides = trigger_root.get("sides", {}) if isinstance(trigger_root, dict) else {}
            side_evidence = sides.get(window["side"], {}) if isinstance(sides, dict) else {}
            old_scores.append(
                float(
                    side_evidence.get(
                        "template_score",
                        record.get("candidate_scores", {}).get(
                            "TRIGGER_NOTIFICATION", 0.0
                        ),
                    )
                )
            )
        rows.append(
            {
                "case_id": window["case_id"],
                "side": window["side"],
                "window": [window["approx_start_sec"], window["approx_end_sec"]],
                "previous_status": window["previous_status"],
                "human_roi_audit": window["human_roi_audit"],
                "old_candidate_ids": [str(event["event_id"]) for event in old_matches],
                "fixture_baseline_candidate_ids": list(
                    window.get("baseline_candidate_ids", [])
                ),
                "fixture_baseline_candidate_times": list(
                    window.get("baseline_candidate_times", [])
                ),
                "old_event_source": (
                    "prior_formal_output"
                    if old_event_source_valid
                    else "fixture_baseline_due_to_nonbaseline_prior_output"
                ),
                "old_template_score": {
                    "median": round(float(median(old_scores)), 6) if old_scores else 0.0,
                    "max": round(max(old_scores), 6) if old_scores else 0.0,
                    "proposal_count_at_0_82": sum(score >= old_threshold for score in old_scores),
                },
                "new_candidate_ids": [str(event["event_id"]) for event in new_matches],
                "new_candidate_times": [
                    [event["start_time"], event["end_time"]] for event in new_matches
                ],
                "new_candidate_durations": [
                    event["duration_sec"] for event in new_matches
                ],
                "new_status": "covered" if new_matches else "missed",
                "metrics": _metric_summary(new_diagnostics, window),
            }
        )
    ability = next(row for row in rows if "ability" in row["case_id"])
    item = next(row for row in rows if "item" in row["case_id"])
    return {
        "schema_version": "0.1.0",
        "kind": "trigger_notification_round1_comparison",
        "production_detector_reads_fixture": False,
        "audit_answers": {
            "roi_coverage_114s": ability["human_roi_audit"],
            "weak_proposal_114s_before_change": ability["old_template_score"]["proposal_count_at_0_82"] > 0,
            "proposal_failure_114s": "canonical ROI 未完整覆蓋短版文字，整張道具 template score 未達 0.82",
            "timeline_open_114s_before_change": False,
            "legacy_dependency": [
                "long_text_width",
                "large_high_saturation_item_icon",
                "whole_roi_appearance_similarity"
            ],
            "side_parameters": "共用 thresholds 與 feature 規則；text/icon subregions 鏡像設定，timeline state 彼此獨立"
        },
        "rows": rows,
        "required_positive_coverage": {
            "window_count": len(rows),
            "covered_count": sum(row["new_status"] == "covered" for row in rows),
            "all_covered": all(row["new_status"] == "covered" for row in rows),
        },
        "ability_vs_item": {
            "ability_case_id": ability["case_id"],
            "item_case_id": item["case_id"],
            "metrics": {
                field: {
                    "ability": ability["metrics"][field],
                    "item": item["metrics"][field],
                }
                for field in METRIC_FIELDS
            },
        },
    }
