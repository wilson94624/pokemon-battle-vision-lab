"""Checkpoint 1F immutable snapshot orchestration 與 relation review policy。"""

from collections import Counter, defaultdict
from copy import deepcopy
from statistics import mean
from typing import Any, Dict, List, Mapping, Sequence, Set, Tuple

from .battle_state_confidence import (
    state_completeness,
    state_confidence,
    unknown_field_paths,
)
from .battle_state_models import (
    STATE_VERSION,
    StateConflict,
    StateDelta,
    human_review_defaults,
    initial_battle_state,
)
from .battle_state_mutator import ProjectionContext
from .battle_state_policy import IMPORTANT_OPERATIONS, LOW_COMPLETENESS_THRESHOLD
from .battle_state_reducers import ReducerRegistry, build_reducer_registry
from .errors import InputError


def _relation_policy(
    relations: Sequence[Mapping[str, Any]],
    relation_reviews: Sequence[Mapping[str, Any]],
) -> Tuple[Dict[str, List[str]], Set[str], Set[str]]:
    decisions = {
        str(record["relation_id"]): record.get("human_decision")
        for record in relation_reviews
    }
    accepted: Set[str] = set()
    rejected: Set[str] = set()
    by_event: Dict[str, List[str]] = defaultdict(list)
    for relation in relations:
        relation_id = str(relation["relation_id"])
        if relation["review_status"] == "auto_accepted":
            accepted.add(relation_id)
        elif decisions.get(relation_id) == "accepted":
            accepted.add(relation_id)
        elif decisions.get(relation_id) == "rejected":
            rejected.add(relation_id)
        else:
            raise InputError("Checkpoint 1E relation 尚未完成人工審查：{}".format(relation_id))
        if relation_id in accepted:
            by_event[str(relation["from_event_id"])].append(relation_id)
            by_event[str(relation["to_event_id"])].append(relation_id)
    return {key: sorted(value) for key, value in by_event.items()}, accepted, rejected


def _accepted_unlinked_ids(
    unlinked_reviews: Sequence[Mapping[str, Any]],
) -> Set[str]:
    selected = set()
    for record in unlinked_reviews:
        timeline_id = str(record["timeline_id"])
        if (
            record.get("human_decision") != "accepted_unlinked"
            or record.get("human_action") != "keep_unlinked"
        ):
            raise InputError("Checkpoint 1E unlinked group 尚未完成人工審查：{}".format(timeline_id))
        selected.add(timeline_id)
    return selected


def _snapshot(
    snapshot_id: str,
    sequence: int,
    timestamp: float,
    previous_snapshot_id: Any,
    timeline_id: Any,
    event_ids: Sequence[str],
    state: Dict[str, Any],
    review_status: str,
    review_reasons: Sequence[str],
    conflict_ids: Sequence[str],
    unresolved_ids: Sequence[str],
) -> Dict[str, Any]:
    return {
        "snapshot_id": snapshot_id,
        "sequence": sequence,
        "timestamp": round(float(timestamp), 6),
        "previous_snapshot_id": previous_snapshot_id,
        "source_timeline_id": timeline_id,
        "source_event_ids": list(event_ids),
        "state_version": STATE_VERSION,
        "battle": deepcopy(state["battle"]),
        "player_side": deepcopy(state["player_side"]),
        "opponent_side": deepcopy(state["opponent_side"]),
        "field": deepcopy(state["field"]),
        "confidence": state_confidence(state),
        "completeness": state_completeness(state),
        "review_status": review_status,
        "review_reasons": sorted(set(review_reasons)),
        "conflict_ids": list(conflict_ids),
        "unresolved_update_ids": list(unresolved_ids),
        "unknown_fields": unknown_field_paths(state),
        "human_review": human_review_defaults(),
    }


def _delta_confidence(context: ProjectionContext, events: Sequence[Mapping[str, Any]]) -> float:
    values = [operation.confidence for operation in context.operations]
    values.extend(item.confidence for item in context.unresolved_updates)
    if not values:
        values = [float(event.get("confidence", 0.0)) for event in events]
    return round(mean(values), 6) if values else 0.0


def _delta_status(context: ProjectionContext, new_conflicts: Sequence[StateConflict]) -> str:
    if new_conflicts:
        return "conflicted"
    if context.unresolved_updates and context.operations:
        return "partial"
    if context.unresolved_updates:
        return "unresolved"
    if context.operations:
        return "applied"
    return "no_op"


def _stats(values: Sequence[float]) -> Dict[str, float]:
    if not values:
        return {"minimum": 0.0, "mean": 0.0, "maximum": 0.0}
    return {
        "minimum": round(min(values), 6),
        "mean": round(mean(values), 6),
        "maximum": round(max(values), 6),
    }


def project_battle_state(
    events_payload: Mapping[str, Any],
    timeline_payload: Mapping[str, Any],
    relations_payload: Mapping[str, Any],
    relation_review_payload: Mapping[str, Any],
    unlinked_review_payload: Mapping[str, Any],
    registry: ReducerRegistry = None,
) -> Dict[str, Any]:
    registry = registry or build_reducer_registry()
    events = list(events_payload["events"])
    groups = list(timeline_payload["groups"])
    relations = list(relations_payload["relations"])
    event_by_id = {str(event["id"]): event for event in events}
    if len(event_by_id) != len(events):
        raise InputError("Checkpoint 1F source event ID 不可重複")

    relation_ids_by_event, accepted_relations, rejected_relations = _relation_policy(
        relations,
        relation_review_payload["records"],
    )
    accepted_unlinked = _accepted_unlinked_ids(unlinked_review_payload["records"])
    relation_by_id = {str(item["relation_id"]): item for item in relations}
    rejected_by_event: Dict[str, List[str]] = defaultdict(list)
    for relation_id in rejected_relations:
        relation = relation_by_id[relation_id]
        rejected_by_event[str(relation["from_event_id"])].append(relation_id)
        rejected_by_event[str(relation["to_event_id"])].append(relation_id)

    state = initial_battle_state()
    conflicts: List[StateConflict] = []
    snapshots = [
        _snapshot(
            "state-0000",
            0,
            0.0,
            None,
            None,
            [],
            state,
            "auto_accepted",
            ["initial_unknown_state"],
            [],
            [],
        )
    ]
    deltas: List[StateDelta] = []
    event_processing: List[Dict[str, Any]] = []
    consumed_event_ids: List[str] = []
    unresolved_next = 1

    for sequence, group in enumerate(groups, 1):
        timeline_id = str(group["timeline_id"])
        group_event_ids = [str(item) for item in group["event_ids"]]
        group_events = []
        for event_id in group_event_ids:
            if event_id not in event_by_id:
                raise InputError("Timeline 引用不存在的 BattleEvent：{}".format(event_id))
            group_events.append(event_by_id[event_id])
        if [event["id"] for event in group_events] != group_event_ids:
            raise InputError("Timeline group event order 不一致：{}".format(timeline_id))

        next_state = deepcopy(state)
        conflict_start = len(conflicts)
        context = ProjectionContext(
            next_state,
            timeline_id,
            relation_ids_by_event,
            timeline_id in accepted_unlinked,
            conflicts,
            unresolved_next,
        )
        for event in group_events:
            operation_start = len(context.operations)
            unresolved_start = len(context.unresolved_updates)
            conflict_count_start = len(conflicts)
            no_op_start = len(context.no_op_reasons)
            registry.apply(context, event)
            event_processing.append(
                {
                    "event_id": event["id"],
                    "timeline_id": timeline_id,
                    "event_type": event["event_type"],
                    "operation_count": len(context.operations) - operation_start,
                    "unresolved_count": len(context.unresolved_updates)
                    - unresolved_start,
                    "conflict_count": len(conflicts) - conflict_count_start,
                    "no_op_count": len(context.no_op_reasons) - no_op_start,
                }
            )
            consumed_event_ids.append(str(event["id"]))
        unresolved_next = context.unresolved_next

        touched_rejected = sorted(
            {
                relation_id
                for event_id in group_event_ids
                for relation_id in rejected_by_event.get(event_id, [])
            }
        )
        if touched_rejected:
            context.review_reasons.append("rejected_relation_separation")
        if timeline_id in accepted_unlinked:
            context.review_reasons.append("accepted_unlinked_event")

        new_conflicts = conflicts[conflict_start:]
        completeness = state_completeness(next_state)
        important = any(
            operation.operation in IMPORTANT_OPERATIONS for operation in context.operations
        )
        if important and completeness < LOW_COMPLETENESS_THRESHOLD:
            context.review_reasons.append("low_completeness_important_snapshot")
        if new_conflicts or context.unresolved_updates or context.review_reasons:
            review_status = "needs_review"
        else:
            review_status = "auto_accepted"

        snapshot_before = snapshots[-1]["snapshot_id"]
        snapshot_after = "state-{:04d}".format(sequence)
        projection_timestamp = max(
            float(snapshots[-1]["timestamp"]), float(group["end_time"])
        )
        conflict_ids = [item.conflict_id for item in new_conflicts]
        unresolved_ids = [
            item.unresolved_id for item in context.unresolved_updates
        ]
        delta = StateDelta(
            delta_id="delta-{:04d}".format(sequence),
            sequence=sequence,
            snapshot_before=snapshot_before,
            snapshot_after=snapshot_after,
            timeline_id=timeline_id,
            timestamp=projection_timestamp,
            source_group_start_time=float(group["start_time"]),
            source_group_end_time=float(group["end_time"]),
            source_event_ids=group_event_ids,
            operations=context.operations,
            unresolved_updates=context.unresolved_updates,
            conflict_ids=conflict_ids,
            projection_rule_ids=sorted(set(context.projection_rule_ids)),
            status=_delta_status(context, new_conflicts),
            confidence=_delta_confidence(context, group_events),
            no_op_reasons=context.no_op_reasons,
            review_status=review_status,
            review_reasons=sorted(set(context.review_reasons)),
        )
        delta_row = delta.to_dict()
        if touched_rejected:
            delta_row["excluded_rejected_relation_ids"] = touched_rejected
        else:
            delta_row["excluded_rejected_relation_ids"] = []
        deltas.append(delta_row)
        snapshots.append(
            _snapshot(
                snapshot_after,
                sequence,
                projection_timestamp,
                snapshot_before,
                timeline_id,
                group_event_ids,
                next_state,
                review_status,
                context.review_reasons,
                conflict_ids,
                unresolved_ids,
            )
        )
        state = next_state

    source_ids = [str(event["id"]) for event in events]
    validation = {
        "all_timeline_groups_processed": len(deltas) == len(groups),
        "all_source_events_processed": len(consumed_event_ids) == len(source_ids)
        and set(consumed_event_ids) == set(source_ids),
        "source_events_processed_once": len(consumed_event_ids)
        == len(set(consumed_event_ids)),
        "snapshot_count_matches": len(snapshots) == len(groups) + 1,
        "delta_count_matches": len(deltas) == len(groups),
        "snapshot_sequence_monotonic": [item["sequence"] for item in snapshots]
        == list(range(len(snapshots))),
        "snapshot_timestamps_monotonic": [item["timestamp"] for item in snapshots]
        == sorted(item["timestamp"] for item in snapshots),
        "previous_snapshot_chain_valid": all(
            snapshots[index]["previous_snapshot_id"]
            == snapshots[index - 1]["snapshot_id"]
            for index in range(1, len(snapshots))
        ),
        "delta_snapshot_links_valid": all(
            delta["snapshot_before"] == snapshots[index]["snapshot_id"]
            and delta["snapshot_after"] == snapshots[index + 1]["snapshot_id"]
            for index, delta in enumerate(deltas)
        ),
        "human_rejected_relations_excluded": all(
            relation_id not in accepted_relations for relation_id in rejected_relations
        ),
        "accepted_unlinked_kept_independent": accepted_unlinked
        == {
            str(record["timeline_id"])
            for record in unlinked_review_payload["records"]
        },
        "no_hp_reconstruction": all(
            operation["operation"] not in {"SET_HP", "CHANGE_HP"}
            for delta in deltas
            for operation in delta["operations"]
        ),
        "no_turn_inference": all("turn" not in path for item in snapshots for path in item["unknown_fields"])
        and snapshots[-1]["battle"]["official_turn"]["knowledge"]
        == "not_applicable",
        "human_review_fields_default_null": all(
            all(value is None for value in item["human_review"].values())
            for item in snapshots + deltas + [c.to_dict() for c in conflicts]
        ),
    }
    if not all(validation.values()):
        raise InputError("Checkpoint 1F projection validation 失敗：{}".format(validation))

    operation_counts = Counter(
        operation["operation"] for delta in deltas for operation in delta["operations"]
    )
    delta_status_counts = Counter(delta["status"] for delta in deltas)
    review_status_counts = Counter(snapshot["review_status"] for snapshot in snapshots[1:])
    unresolved = [item for delta in deltas for item in delta["unresolved_updates"]]
    return {
        "snapshots": snapshots,
        "deltas": deltas,
        "conflicts": [item.to_dict() for item in conflicts],
        "unresolved_updates": unresolved,
        "audit": {
            "schema_version": STATE_VERSION,
            "checkpoint": "1F",
            "kind": "battle_state_projection_audit",
            "source_event_count": len(events),
            "timeline_group_count": len(groups),
            "snapshot_count": len(snapshots),
            "delta_count": len(deltas),
            "conflict_count": len(conflicts),
            "unresolved_update_count": len(unresolved),
            "no_op_delta_count": delta_status_counts.get("no_op", 0),
            "operation_counts": dict(sorted(operation_counts.items())),
            "delta_status_counts": dict(sorted(delta_status_counts.items())),
            "review_status_counts": dict(sorted(review_status_counts.items())),
            "confidence_statistics": _stats(
                [snapshot["confidence"] for snapshot in snapshots[1:]]
            ),
            "completeness_statistics": _stats(
                [snapshot["completeness"] for snapshot in snapshots[1:]]
            ),
            "relation_policy": {
                "accepted_relation_ids": sorted(accepted_relations),
                "rejected_relation_ids": sorted(rejected_relations),
                "temporal_adjacency_is_not_state_causality": True,
            },
            "accepted_unlinked_timeline_ids": sorted(accepted_unlinked),
            "event_processing": event_processing,
            "reducer_registry": [spec.__dict__ for spec in registry.specs],
            "unsupported_state": [
                "exact_hp",
                "hp_percent",
                "pp",
                "ev_iv",
                "complete_moveset",
                "unobserved_item_or_ability",
                "speed_order",
                "official_turn",
                "active_slot",
                "move_choice",
                "target_selection",
                "complete_team_roster",
                "damage_calculation",
            ],
            "limitations": [
                "TEAM_PREVIEW 與 SELECTED_FOUR 未進入 BattleEvent。",
                "SWITCH metadata 沒有明示 side 或 slot。",
                "Active collection 只代表已觀察成員，不代表完整 lineup。",
                "沒有 end event 的 volatile 不推算 duration。",
                "Stat stage 保存 observed net change，absolute stage 維持 unknown。",
                "Battle result 無法安全補出 winner side。",
            ],
            "validation": validation,
        },
    }
