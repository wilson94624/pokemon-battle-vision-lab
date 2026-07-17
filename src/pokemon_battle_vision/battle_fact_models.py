"""Checkpoint 1H immutable Battle Fact 純資料模型。"""

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple


FACT_SCHEMA_VERSION = "0.1.0"
FACT_TYPES = (
    "MOVE_USED",
    "MOVE_RESOLVED",
    "DAMAGE_OBSERVED",
    "SWITCH_IN",
    "ABILITY_ACTIVATED",
    "ITEM_ACTIVATED",
    "STATUS_APPLIED",
    "STATUS_REMOVED",
    "STATUS_CHANGED",
    "STAT_CHANGED",
    "WEATHER_STARTED",
    "WEATHER_ENDED",
    "WEATHER_CHANGED",
    "TERRAIN_STARTED",
    "TERRAIN_ENDED",
    "TERRAIN_CHANGED",
    "FIELD_EFFECT_STARTED",
    "FIELD_EFFECT_ENDED",
    "FIELD_EFFECT_UPDATED",
    "SIDE_CONDITION_STARTED",
    "SIDE_CONDITION_ENDED",
    "SIDE_CONDITION_UPDATED",
    "VOLATILE_STATUS_APPLIED",
    "VOLATILE_STATUS_REMOVED",
    "VOLATILE_STATUS_UPDATED",
    "TRANSFORMATION_OCCURRED",
    "HP_CHANGED",
    "KO",
    "BATTLE_ENDED",
    "TURN_BOUNDARY",
    "UNRESOLVED_EVENT",
)
CERTAINTIES = ("observed", "ambiguous", "unknown")


@dataclass(frozen=True)
class EvidenceReference:
    """指向 frozen observation record；不內嵌外部推論。"""

    checkpoint: str
    artifact_path: str
    record_id: str
    observation_kind: str
    evidence_role: str
    confidence: float
    timestamp: float
    upstream_record_ids: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["confidence"] = round(float(self.confidence), 6)
        payload["timestamp"] = round(float(self.timestamp), 6)
        payload["upstream_record_ids"] = list(self.upstream_record_ids)
        return payload


@dataclass(frozen=True)
class FactParticipant:
    """事件存在與 identity resolution 分離；resolution 可保持 ambiguous。"""

    role: str
    participant_kind: str
    observed_name: str
    side: str
    entity_id: Optional[str]
    canonical_species_id: Optional[int]
    canonical_name: Optional[str]
    resolution_status: str
    confidence: float
    entity_candidate_ids: Tuple[str, ...] = ()
    species_candidates: Tuple[Dict[str, Any], ...] = ()
    resolution_source_ids: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["entity_candidate_ids"] = list(self.entity_candidate_ids)
        payload["species_candidates"] = [dict(item) for item in self.species_candidates]
        payload["resolution_source_ids"] = list(self.resolution_source_ids)
        return payload


@dataclass(frozen=True)
class BattleFact:
    """由 observation 重建的 immutable factual record。"""

    fact_id: str
    sequence: int
    fact_type: str
    timestamp: float
    start_time: float
    end_time: float
    certainty: str
    confidence: float
    participants: Tuple[FactParticipant, ...]
    attributes: Dict[str, Any]
    evidence: Tuple[EvidenceReference, ...]
    source_timeline_ids: Tuple[str, ...]
    source_relation_ids: Tuple[str, ...]
    source_decision_cycle_ids: Tuple[str, ...]
    reconstruction_rule_id: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "sequence": self.sequence,
            "fact_type": self.fact_type,
            "timestamp": round(float(self.timestamp), 6),
            "start_time": round(float(self.start_time), 6),
            "end_time": round(float(self.end_time), 6),
            "certainty": self.certainty,
            "confidence": round(float(self.confidence), 6),
            "participants": [item.to_dict() for item in self.participants],
            "attributes": dict(self.attributes),
            "evidence": [item.to_dict() for item in self.evidence],
            "source_timeline_ids": list(self.source_timeline_ids),
            "source_relation_ids": list(self.source_relation_ids),
            "source_decision_cycle_ids": list(self.source_decision_cycle_ids),
            "reconstruction_rule_id": self.reconstruction_rule_id,
        }


def count_fact_types(facts: List[BattleFact]) -> Dict[str, int]:
    counts = {fact_type: 0 for fact_type in FACT_TYPES}
    for fact in facts:
        counts[fact.fact_type] += 1
    return counts
