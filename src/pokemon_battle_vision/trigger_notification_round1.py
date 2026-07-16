"""Trigger round-1 人工正例與新版 candidates 的時間／side mapping。"""

import csv
import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from .models import FrameTimestampIndex


def _event_side(event: Mapping[str, Any]) -> str:
    visible = set(map(str, event.get("visible_rois", [])))
    if "player_trigger_notification" in visible:
        return "player"
    if "opponent_trigger_notification" in visible:
        return "opponent"
    return ""


def build_trigger_round1_mapping(
    fixture: Mapping[str, Any], events: Sequence[Mapping[str, Any]]
) -> Dict[str, Any]:
    trigger_events = [event for event in events if event.get("type") == "TRIGGER_NOTIFICATION"]
    rows = []
    for window in fixture["positive_windows"]:
        matches = [
            event
            for event in trigger_events
            if _event_side(event) == window["side"]
            and float(event["end_time"]) >= float(window["approx_start_sec"])
            and float(event["start_time"]) <= float(window["approx_end_sec"])
        ]
        rows.append(
            {
                "case_id": window["case_id"],
                "side": window["side"],
                "trigger_kind": window["trigger_kind"],
                "previous_status": window["previous_status"],
                "baseline_candidate_ids": list(
                    window.get("baseline_candidate_ids", [])
                ),
                "baseline_candidate_times": list(
                    window.get("baseline_candidate_times", [])
                ),
                "window_start": window["approx_start_sec"],
                "window_end": window["approx_end_sec"],
                "mapped_candidate_ids": [str(event["event_id"]) for event in matches],
                "mapped_candidate_times": [
                    [event["start_time"], event["end_time"]] for event in matches
                ],
                "new_status": "covered" if matches else "missed",
            }
        )
    return {
        "schema_version": "0.1.0",
        "kind": "trigger_notification_round1_regression",
        "mapping_rule": "same side and positive time overlap",
        "production_detector_reads_fixture": False,
        "rows": rows,
        "summary": {
            "case_count": len(rows),
            "covered_count": sum(row["new_status"] == "covered" for row in rows),
            "all_covered": all(row["new_status"] == "covered" for row in rows),
            "previously_missed_now_covered": sum(
                row["previous_status"] == "missed" and row["new_status"] == "covered"
                for row in rows
            ),
            "previously_detected_preserved": sum(
                row["previous_status"] == "detected" and row["new_status"] == "covered"
                for row in rows
            ),
        },
    }


def build_trigger_round1_reference_frames(
    fixture: Mapping[str, Any], timestamp_index: FrameTimestampIndex
) -> Dict[str, list[int]]:
    result = {}
    for window in fixture["positive_windows"]:
        targets = (
            float(window["approx_start_sec"]),
            (float(window["approx_start_sec"]) + float(window["approx_end_sec"])) / 2.0,
            float(window["approx_end_sec"]),
        )
        result[str(window["case_id"])] = sorted(
            {timestamp_index.nearest_ordinal(target) for target in targets}
        )
    return result


def write_trigger_round1_mapping_csv(path: Path, report: Mapping[str, Any]) -> None:
    rows = []
    for source in report["rows"]:
        row = dict(source)
        row["mapped_candidate_ids"] = json.dumps(
            row["mapped_candidate_ids"], ensure_ascii=False
        )
        row["mapped_candidate_times"] = json.dumps(
            row["mapped_candidate_times"], ensure_ascii=False
        )
        rows.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    os.replace(str(temporary), str(path))
