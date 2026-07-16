"""Checkpoint 1F Battle State、delta、conflict 與人工審查資料模型。"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


STATE_VERSION = "0.1.0"
KNOWLEDGE_STATES = ("known", "unknown", "conflicted", "not_applicable")


def human_review_defaults() -> Dict[str, Optional[str]]:
    return {
        "human_decision": None,
        "human_action": None,
        "human_entity": None,
        "human_field": None,
        "human_value": None,
        "reviewed_by": None,
        "reviewed_at": None,
        "review_note": None,
    }


def knowledge_value(
    knowledge: str = "unknown",
    value: Any = None,
    confidence: float = 0.0,
    source_event_ids: Optional[List[str]] = None,
    source_timeline_ids: Optional[List[str]] = None,
    observed_at: Optional[float] = None,
) -> Dict[str, Any]:
    if knowledge not in KNOWLEDGE_STATES:
        raise ValueError("未知 knowledge state：{}".format(knowledge))
    return {
        "knowledge": knowledge,
        "value": value,
        "confidence": round(float(confidence), 6),
        "source_event_ids": list(source_event_ids or []),
        "source_timeline_ids": list(source_timeline_ids or []),
        "observed_at": observed_at,
    }


def pokemon_state(entity_id: str, name: str, side: Optional[str]) -> Dict[str, Any]:
    side_field = knowledge_value("known", side) if side else knowledge_value()
    return {
        "entity_id": entity_id,
        "name": name,
        "side": side_field,
        "active": knowledge_value(),
        "fainted": knowledge_value(),
        "status": knowledge_value(),
        "volatile_statuses": {},
        "stat_stages": {},
        "transformation": knowledge_value(),
        "known_item": knowledge_value(),
        "known_ability": knowledge_value(),
        "hp": knowledge_value(),
        "provenance": [],
    }


def side_state() -> Dict[str, Any]:
    return {
        "active": knowledge_value(value=[]),
        "known_pokemon": {},
        "fainted": knowledge_value(value=[]),
        "side_conditions": {},
        "complete_roster": knowledge_value(),
        "active_slots": knowledge_value("not_applicable"),
        "unknown_active_slots": True,
    }


def initial_battle_state() -> Dict[str, Any]:
    return {
        "battle": {
            "result": knowledge_value(),
            "termination_reason": knowledge_value(),
            "winner": knowledge_value(),
            "loser": knowledge_value(),
            "official_turn": knowledge_value("not_applicable"),
            "exact_hp_tracking": knowledge_value("not_applicable"),
            "move_choice_tracking": knowledge_value("not_applicable"),
            "speed_order_tracking": knowledge_value("not_applicable"),
            "unassigned_pokemon": {},
        },
        "player_side": side_state(),
        "opponent_side": side_state(),
        "field": {
            "weather": knowledge_value(),
            "field_effects": {},
            "terrain": knowledge_value(),
        },
    }


@dataclass
class StateOperation:
    operation: str
    entity: str
    field: str
    before: Any
    after: Any
    confidence: float
    rule_id: str
    source_event_ids: List[str]
    source_relation_ids: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "operation": self.operation,
            "entity": self.entity,
            "field": self.field,
            "before": self.before,
            "after": self.after,
            "confidence": round(float(self.confidence), 6),
            "rule_id": self.rule_id,
            "source_event_ids": list(self.source_event_ids),
            "source_relation_ids": list(self.source_relation_ids),
            "evidence": list(self.evidence),
        }


@dataclass
class UnresolvedUpdate:
    unresolved_id: str
    timeline_id: str
    event_id: str
    event_type: str
    reason: str
    missing_fields: List[str]
    evidence: Dict[str, Any]
    confidence: float
    human_review: Dict[str, Any] = field(default_factory=human_review_defaults)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "unresolved_id": self.unresolved_id,
            "timeline_id": self.timeline_id,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "reason": self.reason,
            "missing_fields": list(self.missing_fields),
            "evidence": self.evidence,
            "confidence": round(float(self.confidence), 6),
            "human_review": dict(self.human_review),
        }


@dataclass
class StateConflict:
    conflict_id: str
    timeline_id: str
    event_id: str
    conflict_type: str
    entity: str
    field: str
    existing: Any
    proposed: Any
    evidence: Dict[str, Any]
    confidence: float
    human_review: Dict[str, Any] = field(default_factory=human_review_defaults)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conflict_id": self.conflict_id,
            "timeline_id": self.timeline_id,
            "event_id": self.event_id,
            "conflict_type": self.conflict_type,
            "entity": self.entity,
            "field": self.field,
            "existing": self.existing,
            "proposed": self.proposed,
            "evidence": self.evidence,
            "confidence": round(float(self.confidence), 6),
            "review_status": "needs_review",
            "human_review": dict(self.human_review),
        }


@dataclass
class StateDelta:
    delta_id: str
    sequence: int
    snapshot_before: str
    snapshot_after: str
    timeline_id: str
    timestamp: float
    source_group_start_time: float
    source_group_end_time: float
    source_event_ids: List[str]
    operations: List[StateOperation]
    unresolved_updates: List[UnresolvedUpdate]
    conflict_ids: List[str]
    projection_rule_ids: List[str]
    status: str
    confidence: float
    no_op_reasons: List[str]
    review_status: str
    review_reasons: List[str]
    human_review: Dict[str, Any] = field(default_factory=human_review_defaults)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "delta_id": self.delta_id,
            "sequence": self.sequence,
            "snapshot_before": self.snapshot_before,
            "snapshot_after": self.snapshot_after,
            "timeline_id": self.timeline_id,
            "timestamp": round(float(self.timestamp), 6),
            "source_group_start_time": round(float(self.source_group_start_time), 6),
            "source_group_end_time": round(float(self.source_group_end_time), 6),
            "source_event_ids": list(self.source_event_ids),
            "operations": [operation.to_dict() for operation in self.operations],
            "unresolved_updates": [item.to_dict() for item in self.unresolved_updates],
            "conflict_ids": list(self.conflict_ids),
            "projection_rule_ids": list(self.projection_rule_ids),
            "status": self.status,
            "confidence": round(float(self.confidence), 6),
            "no_op_reasons": list(self.no_op_reasons),
            "review_status": self.review_status,
            "review_reasons": list(self.review_reasons),
            "human_review": dict(self.human_review),
        }
