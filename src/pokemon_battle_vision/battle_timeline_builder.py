"""將 frozen BattleEvent 以保守規則投影成 Action Groups 與 relation edges。"""

from collections import Counter
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .battle_timeline_models import RelationEdge, TimelineGroup
from .battle_timeline_rules import (
    AUTO_ACCEPT_THRESHOLD,
    PRIMARY_EVENT_TYPES,
    intrinsically_standalone,
    relation_proposals,
)
from .errors import InputError


def validate_source_events(events: Sequence[Mapping[str, Any]]) -> None:
    ids = [str(event.get("id")) for event in events]
    if len(ids) != len(set(ids)):
        raise InputError("Checkpoint 1E source event id 不可重複")
    order = [
        (float(event["start_time"]), float(event["end_time"]), str(event["id"]))
        for event in events
    ]
    if order != sorted(order):
        raise InputError("Checkpoint 1E source events 必須依時間單調排序")
    for event in events:
        if float(event["end_time"]) < float(event["start_time"]):
            raise InputError("Checkpoint 1E source event 時間順序錯誤：{}".format(event["id"]))


def _proposal_sort_key(proposal) -> Tuple[float, int, str]:
    return (-float(proposal.confidence), int(proposal.rule_order), proposal.from_event_id)


def _temporary_group(event: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "event_ids": [str(event["id"])],
        "relation_edge_ids": [],
        "review_reasons": [],
    }


def _group_type(
    event_ids: Sequence[str],
    event_by_id: Mapping[str, Mapping[str, Any]],
    intra_edges: Sequence[RelationEdge],
    review_status: str,
) -> str:
    if review_status == "unlinked":
        return "UNLINKED_EVENT"
    if len(event_ids) > 1:
        batch_rule_ids = {
            "damage.same_residual_batch",
            "volatile.same_counter_batch",
        }
        if intra_edges and all(edge.rule_id in batch_rule_ids for edge in intra_edges):
            return "EVENT_BATCH"
        return "ACTION_CHAIN"
    event_type = str(event_by_id[event_ids[0]]["event_type"])
    if event_type in PRIMARY_EVENT_TYPES:
        return "STANDALONE_ACTION"
    return "STANDALONE_EVENT"


def _finalize_groups(
    temporary_groups: Sequence[Dict[str, Any]],
    event_by_id: Mapping[str, Mapping[str, Any]],
    edges: Sequence[RelationEdge],
) -> List[TimelineGroup]:
    group_by_event: Dict[str, Dict[str, Any]] = {}
    for group in temporary_groups:
        for event_id in group["event_ids"]:
            group_by_event[event_id] = group

    pending_by_group: Dict[int, List[RelationEdge]] = {}
    intra_by_group: Dict[int, List[RelationEdge]] = {}
    for edge in edges:
        source_group = group_by_event[edge.from_event_id]
        target_group = group_by_event[edge.to_event_id]
        if source_group is target_group:
            intra_by_group.setdefault(id(source_group), []).append(edge)
        else:
            pending_by_group.setdefault(id(source_group), []).append(edge)
            pending_by_group.setdefault(id(target_group), []).append(edge)

    groups = sorted(
        temporary_groups,
        key=lambda row: (
            float(event_by_id[row["event_ids"][0]]["start_time"]),
            str(row["event_ids"][0]),
        ),
    )
    final_groups: List[TimelineGroup] = []
    for sequence, temporary in enumerate(groups, start=1):
        timeline_id = "timeline-{:04d}".format(sequence)
        event_ids = list(temporary["event_ids"])
        intra = sorted(intra_by_group.get(id(temporary), []), key=lambda edge: edge.relation_id)
        pending = sorted(pending_by_group.get(id(temporary), []), key=lambda edge: edge.relation_id)
        for edge in intra:
            edge.group_id = timeline_id
        relation_ids = [edge.relation_id for edge in intra + pending]
        review_reasons = list(temporary["review_reasons"])
        if pending:
            review_status = "needs_review"
            review_reasons.extend(
                "pending_relation:{}".format(edge.relation_id) for edge in pending
            )
        elif intra:
            review_status = "auto_accepted"
        elif intrinsically_standalone(event_by_id[event_ids[0]]):
            review_status = "auto_accepted"
            review_reasons.append("intrinsically_standalone")
        else:
            review_status = "unlinked"
            review_reasons.append("no_reliable_parent")

        relevant_confidences = [edge.confidence for edge in intra + pending]
        if relevant_confidences:
            confidence = round(min(relevant_confidences), 6)
        else:
            confidence = round(
                min(float(event_by_id[event_id]["confidence"]) for event_id in event_ids),
                6,
            )
        start_time = min(float(event_by_id[event_id]["start_time"]) for event_id in event_ids)
        end_time = max(float(event_by_id[event_id]["end_time"]) for event_id in event_ids)
        final_groups.append(
            TimelineGroup(
                timeline_id=timeline_id,
                sequence=sequence,
                start_time=start_time,
                end_time=end_time,
                primary_event_id=event_ids[0],
                event_ids=event_ids,
                relation_edge_ids=sorted(set(relation_ids)),
                group_type=_group_type(event_ids, event_by_id, intra, review_status),
                confidence=confidence,
                review_status=review_status,
                review_reasons=sorted(set(review_reasons)),
                source_event_count=len(event_ids),
            )
        )
    return final_groups


def build_battle_timeline(
    events: Sequence[Mapping[str, Any]],
) -> Tuple[List[TimelineGroup], List[RelationEdge]]:
    """單次順序投影；只有 auto accepted edge 會合併 group。"""
    validate_source_events(events)
    event_by_id = {str(event["id"]): event for event in events}
    temporary_groups: List[Dict[str, Any]] = []
    group_by_event: Dict[str, Dict[str, Any]] = {}
    edges: List[RelationEdge] = []

    for target_index, event in enumerate(events):
        target_id = str(event["id"])
        proposals = relation_proposals(events, target_index)
        accepted = sorted(
            [
                proposal
                for proposal in proposals
                if proposal.ambiguity_behavior == "link"
                and proposal.confidence >= AUTO_ACCEPT_THRESHOLD
            ],
            key=_proposal_sort_key,
        )
        reviews = sorted(
            [proposal for proposal in proposals if proposal.ambiguity_behavior == "review"],
            key=_proposal_sort_key,
        )

        chosen = accepted[0] if accepted else (reviews[0] if reviews else None)
        relation_id = None
        if chosen is not None:
            relation_id = "relation-{:04d}".format(len(edges) + 1)
            edges.append(
                RelationEdge(
                    relation_id=relation_id,
                    from_event_id=chosen.from_event_id,
                    to_event_id=chosen.to_event_id,
                    relation_type=chosen.relation_type,
                    rule_id=chosen.rule_id,
                    confidence=chosen.confidence,
                    evidence=list(chosen.evidence),
                    review_status=(
                        "auto_accepted" if chosen in accepted else "needs_review"
                    ),
                )
            )

        if accepted:
            source_group = group_by_event[accepted[0].from_event_id]
            source_group["event_ids"].append(target_id)
            if relation_id:
                source_group["relation_edge_ids"].append(relation_id)
            group_by_event[target_id] = source_group
        else:
            group = _temporary_group(event)
            if relation_id:
                group["relation_edge_ids"].append(relation_id)
            temporary_groups.append(group)
            group_by_event[target_id] = group

    groups = _finalize_groups(temporary_groups, event_by_id, edges)
    consumed = [event_id for group in groups for event_id in group.event_ids]
    source_ids = [str(event["id"]) for event in events]
    if len(consumed) != len(source_ids) or set(consumed) != set(source_ids):
        raise InputError("Checkpoint 1E group 未完整覆蓋 source events")
    if len(consumed) != len(set(consumed)):
        raise InputError("Checkpoint 1E event 被重複消費")
    source_index = {event_id: index for index, event_id in enumerate(source_ids)}
    if any(
        [source_index[event_id] for event_id in group.event_ids]
        != sorted(source_index[event_id] for event_id in group.event_ids)
        for group in groups
    ):
        raise InputError("Checkpoint 1E group 內 source order 被反轉")
    return groups, edges


def timeline_counts(
    groups: Sequence[TimelineGroup], edges: Sequence[RelationEdge]
) -> Dict[str, Any]:
    return {
        "group_count": len(groups),
        "relation_count": len(edges),
        "group_status_counts": dict(Counter(group.review_status for group in groups)),
        "relation_status_counts": dict(Counter(edge.review_status for edge in edges)),
        "group_type_counts": dict(Counter(group.group_type for group in groups)),
        "unlinked_event_count": sum(
            group.source_event_count for group in groups if group.review_status == "unlinked"
        ),
    }
