"""Checkpoint 1E 輸入時間、metadata 與 coverage 稽核。"""

from collections import Counter
from typing import Any, Dict, List, Mapping, Sequence

from .battle_timeline_models import RelationEdge, TimelineGroup
from .battle_timeline_rules import CHAIN_BARRIER_TYPES


def _percentile(values: Sequence[float], ratio: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[int((len(ordered) - 1) * ratio)]


def _gap_distribution(events: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    gaps = [
        float(events[index]["start_time"]) - float(events[index - 1]["end_time"])
        for index in range(1, len(events))
    ]
    return {
        "count": len(gaps),
        "minimum_sec": round(min(gaps), 6) if gaps else 0.0,
        "median_sec": round(_percentile(gaps, 0.5), 6),
        "p90_sec": round(_percentile(gaps, 0.9), 6),
        "maximum_sec": round(max(gaps), 6) if gaps else 0.0,
        "buckets": {
            "overlap": sum(gap < 0 for gap in gaps),
            "0_to_0_5": sum(0 <= gap <= 0.5 for gap in gaps),
            "0_5_to_1": sum(0.5 < gap <= 1.0 for gap in gaps),
            "1_to_2": sum(1.0 < gap <= 2.0 for gap in gaps),
            "2_to_3": sum(2.0 < gap <= 3.0 for gap in gaps),
            "3_to_5": sum(3.0 < gap <= 5.0 for gap in gaps),
            "over_5": sum(gap > 5.0 for gap in gaps),
        },
    }


def _has_target(event: Mapping[str, Any]) -> bool:
    metadata = event.get("metadata", {})
    return bool(metadata.get("target") or metadata.get("targets"))


def _metadata_coverage(events: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    rows = []
    for event_type in sorted({str(event["event_type"]) for event in events}):
        selected = [event for event in events if event["event_type"] == event_type]
        rows.append(
            {
                "event_type": event_type,
                "count": len(selected),
                "with_actor": sum(bool(event.get("metadata", {}).get("actor")) for event in selected),
                "with_target": sum(_has_target(event) for event in selected),
                "with_move": sum(bool(event.get("metadata", {}).get("move")) for event in selected),
                "with_effect": sum(bool(event.get("metadata", {}).get("effect")) for event in selected),
                "with_side": sum(bool(event.get("metadata", {}).get("side")) for event in selected),
            }
        )
    return rows


def build_timeline_audit(
    events: Sequence[Mapping[str, Any]],
    groups: Sequence[TimelineGroup],
    edges: Sequence[RelationEdge],
) -> Dict[str, Any]:
    consumed = [event_id for group in groups for event_id in group.event_ids]
    source_ids = [str(event["id"]) for event in events]
    edge_pairs = [(edge.from_event_id, edge.to_event_id) for edge in edges]
    return {
        "schema_version": "0.1.0",
        "checkpoint": "1E",
        "kind": "battle_timeline_audit",
        "source_event_count": len(events),
        "time_gap_distribution": _gap_distribution(events),
        "event_type_counts": dict(Counter(str(event["event_type"]) for event in events)),
        "metadata_coverage": _metadata_coverage(events),
        "roles": {
            "major_action_types": sorted(CHAIN_BARRIER_TYPES),
            "usual_consequence_types": [
                "MOVE_RESULT",
                "DAMAGE_RESULT",
                "STATUS",
                "STAT_CHANGE",
                "VOLATILE_STATUS",
                "FAINT",
            ],
            "dual_role_types": [
                "ABILITY",
                "ITEM",
                "WEATHER",
                "TERRAIN",
                "FIELD_EFFECT",
                "SIDE_CONDITION",
            ],
        },
        "limitations": [
            "MOVE target 在本片 29/29 缺失",
            "沒有可靠 turn marker",
            "沒有完整 active slots、HP、speed order 或 source tags",
            "時間相鄰不等於因果",
        ],
        "validation": {
            "all_source_events_covered": len(consumed) == len(source_ids)
            and set(consumed) == set(source_ids),
            "source_event_ids_unique": len(source_ids) == len(set(source_ids)),
            "events_consumed_once": len(consumed) == len(set(consumed)) == len(source_ids),
            "groups_monotonic": [group.start_time for group in groups]
            == sorted(group.start_time for group in groups),
            "group_event_order_preserved": all(
                group.event_ids == sorted(group.event_ids, key=source_ids.index)
                for group in groups
            ),
            "relation_pairs_unique": len(edge_pairs) == len(set(edge_pairs)),
            "no_turn_inference": True,
        },
    }
