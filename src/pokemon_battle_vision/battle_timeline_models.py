"""Checkpoint 1E Timeline、relation 與人工審查資料模型。"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


TIMELINE_SCHEMA_VERSION = "0.1.0"
REVIEW_STATUSES = ("auto_accepted", "needs_review", "unlinked")
GROUP_TYPES = (
    "ACTION_CHAIN",
    "EVENT_BATCH",
    "STANDALONE_ACTION",
    "STANDALONE_EVENT",
    "UNLINKED_EVENT",
)
RELATION_TYPES = (
    "RESULT_OF",
    "DAMAGE_FROM",
    "STATUS_FROM",
    "STAT_CHANGE_FROM",
    "TRIGGERED_BY",
    "FOLLOWED_BY",
    "SAME_ACTION",
    "TEMPORALLY_ADJACENT",
)


def human_review_defaults() -> Dict[str, Optional[str]]:
    """所有人工欄位以 null 開始，避免把 auto 結果冒充人工結論。"""
    return {
        "human_action": None,
        "human_decision": None,
        "human_relation_type": None,
        "human_primary_event_id": None,
        "human_group_id": None,
        "reviewed_by": None,
        "reviewed_at": None,
        "review_note": None,
    }


@dataclass(frozen=True)
class RelationProposal:
    from_event_id: str
    to_event_id: str
    relation_type: str
    rule_id: str
    confidence: float
    evidence: List[str]
    ambiguity_behavior: str
    rule_order: int


@dataclass
class RelationEdge:
    relation_id: str
    from_event_id: str
    to_event_id: str
    relation_type: str
    rule_id: str
    confidence: float
    evidence: List[str]
    review_status: str
    group_id: Optional[str] = None
    human_review: Dict[str, Optional[str]] = field(default_factory=human_review_defaults)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "relation_id": self.relation_id,
            "from_event_id": self.from_event_id,
            "to_event_id": self.to_event_id,
            "relation_type": self.relation_type,
            "rule_id": self.rule_id,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
            "review_status": self.review_status,
            "group_id": self.group_id,
            "human_review": dict(self.human_review),
        }


@dataclass
class TimelineGroup:
    timeline_id: str
    sequence: int
    start_time: float
    end_time: float
    primary_event_id: str
    event_ids: List[str]
    relation_edge_ids: List[str]
    group_type: str
    confidence: float
    review_status: str
    review_reasons: List[str]
    source_event_count: int
    human_review: Dict[str, Optional[str]] = field(default_factory=human_review_defaults)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timeline_id": self.timeline_id,
            "sequence": self.sequence,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "primary_event_id": self.primary_event_id,
            "event_ids": list(self.event_ids),
            "relation_edge_ids": list(self.relation_edge_ids),
            "group_type": self.group_type,
            "confidence": self.confidence,
            "review_status": self.review_status,
            "review_reasons": list(self.review_reasons),
            "source_event_count": self.source_event_count,
            "human_review": dict(self.human_review),
        }
