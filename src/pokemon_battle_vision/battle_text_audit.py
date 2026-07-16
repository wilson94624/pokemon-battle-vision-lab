"""Regression windows、diagnostic comparison 與 dense recall audit 選樣。"""

import statistics
from collections import Counter
from typing import Any, Dict, List, Mapping, Optional, Sequence


# 只供 regression 報告與 review audit；production detector 不匯入本模組。
REGRESSION_TIMESTAMPS_SEC = (
    57.988,
    163.998,
    190.000,
    228.005,
    238.005,
    242.005,
    258.007,
    262.007,
    318.013,
    324.013,
    326.013,
    331.997,
    333.997,
    354.000,
    401.987,
    409.988,
    418.005,
)
REGRESSION_WINDOW_RADIUS_SEC = 1.0
REGRESSION_MATCH_TOLERANCE_SEC = 0.05


def _value(row: Any, name: str) -> Any:
    return row.get(name) if isinstance(row, Mapping) else getattr(row, name)


def _battle_events(events: Sequence[Any]) -> List[Any]:
    return [event for event in events if _value(event, "type") == "BATTLE_TEXT"]


def candidate_statistics(events: Sequence[Any]) -> Dict[str, Any]:
    battle = _battle_events(events)
    durations = [float(_value(event, "duration_sec")) for event in battle]
    return {
        "candidate_count": len(battle),
        "mean_duration_sec": round(statistics.mean(durations), 6) if durations else 0.0,
        "median_duration_sec": round(statistics.median(durations), 6) if durations else 0.0,
        "short_candidate_count_0_1_to_0_3_sec": sum(
            0.1 <= duration <= 0.300001 for duration in durations
        ),
        "long_candidate_count_5_sec_or_more": sum(duration >= 5.0 for duration in durations),
    }


def regression_coverage(events: Sequence[Any]) -> Dict[str, Any]:
    battle = _battle_events(events)
    rows = []
    for target in REGRESSION_TIMESTAMPS_SEC:
        exact = [
            str(_value(event, "event_id"))
            for event in battle
            if float(_value(event, "start_time")) - REGRESSION_MATCH_TOLERANCE_SEC
            <= target
            <= float(_value(event, "end_time")) + REGRESSION_MATCH_TOLERANCE_SEC
        ]
        nearby = [
            str(_value(event, "event_id"))
            for event in battle
            if float(_value(event, "end_time")) >= target - REGRESSION_WINDOW_RADIUS_SEC
            and float(_value(event, "start_time")) <= target + REGRESSION_WINDOW_RADIUS_SEC
        ]
        rows.append(
            {
                "target_time": target,
                "covered": bool(exact),
                "covering_candidate_ids": exact,
                "nearby_candidate_ids_within_1_sec": nearby,
            }
        )
    return {
        "window_radius_sec": REGRESSION_WINDOW_RADIUS_SEC,
        "boundary_tolerance_sec": REGRESSION_MATCH_TOLERANCE_SEC,
        "window_count": len(rows),
        "covered_count": sum(row["covered"] for row in rows),
        "still_missed": [row["target_time"] for row in rows if not row["covered"]],
        "windows": rows,
    }


def nearest_sample_reason(
    target: float,
    records: Sequence[Any],
    threshold: float,
) -> Optional[Dict[str, Any]]:
    if not records:
        return None
    nearest = min(records, key=lambda row: abs(float(_value(row, "pts")) - target))
    scores = _value(nearest, "candidate_scores")
    score = float(scores["BATTLE_TEXT"])
    return {
        "pts": float(_value(nearest, "pts")),
        "frame_ordinal": int(_value(nearest, "frame_index")),
        "battle_text_score": score,
        "threshold": float(threshold),
        "raw_positive": score >= threshold,
        "reason": "below_threshold" if score < threshold else "timeline_boundary_or_min_samples",
    }


def build_detector_diagnostic_report(
    old_events: Sequence[Any],
    old_records: Sequence[Any],
    old_threshold: float,
    new_events: Sequence[Any],
    new_records: Sequence[Any],
    diagnostics: Sequence[Mapping[str, Any]],
    proposal_config: Mapping[str, Any],
    temporal_config: Mapping[str, Any],
) -> Dict[str, Any]:
    old_coverage = regression_coverage(old_events)
    new_coverage = regression_coverage(new_events)
    old_reasons = [
        {
            "target_time": target,
            "nearest_sample": nearest_sample_reason(target, old_records, old_threshold),
        }
        for target in REGRESSION_TIMESTAMPS_SEC
    ]
    new_by_target = []
    for target in REGRESSION_TIMESTAMPS_SEC:
        nearest = min(diagnostics, key=lambda row: abs(float(row["pts"]) - target))
        new_by_target.append(
            {
                "target_time": target,
                "nearest_diagnostic": {
                    key: nearest.get(key)
                    for key in (
                        "pts",
                        "frame_ordinal",
                        "battle_text_score",
                        "threshold",
                        "raw_positive",
                        "candidate_id",
                        "decision",
                        "boundary_reason",
                        "template_similarity",
                        "template_strength",
                        "visual_structure_strength",
                    )
                },
            }
        )
    visual_by_candidate: Dict[str, List[float]] = {}
    for row in diagnostics:
        candidate_id = str(row.get("candidate_id", ""))
        value = row.get("visual_structure_strength")
        if candidate_id and value is not None:
            visual_by_candidate.setdefault(candidate_id, []).append(float(value))
    low_structure = sum(
        not values or max(values) < 0.5 for values in visual_by_candidate.values()
    )
    return {
        "schema_version": "0.1.0",
        "kind": "battle_text_detector_diagnostics",
        "sampling_hz": 10.0,
        "diagnostic_sample_count": len(diagnostics),
        "all_samples_traceable": len(diagnostics) == len(new_records),
        "proposal_config": dict(proposal_config),
        "temporal_config": dict(temporal_config),
        "cooldown_present": False,
        "suppression_present": False,
        "duration_filter_present": False,
        "decision_counts": dict(Counter(str(row["decision"]) for row in diagnostics)),
        "old": {
            **candidate_statistics(old_events),
            "regression": old_coverage,
            "regression_nearest_sample_reasons": old_reasons,
            "empty_blank_candidate_count": None,
            "empty_blank_estimate_method": "舊版未保存文字結構特徵，無法可靠估計",
        },
        "new": {
            **candidate_statistics(new_events),
            "regression": new_coverage,
            "regression_nearest_diagnostics": new_by_target,
            "empty_blank_candidate_count": low_structure,
            "empty_blank_estimate_method": "candidate 內沒有 visual_structure_strength >= 0.5 的 heuristic 提示；須人工確認",
        },
        "recall_gate": "pending_human_review",
    }


def select_dense_audit_diagnostics(
    diagnostics: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    selected = []
    for row in diagnostics:
        pts = float(row["pts"])
        targets = [
            target
            for target in REGRESSION_TIMESTAMPS_SEC
            if abs(pts - target) <= REGRESSION_WINDOW_RADIUS_SEC
        ]
        if targets:
            selected.append({**dict(row), "regression_targets": targets})
    return selected
