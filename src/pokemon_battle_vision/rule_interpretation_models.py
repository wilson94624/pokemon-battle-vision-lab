"""Checkpoint 1I rule interpretation 的 immutable 純資料模型。"""

from dataclasses import asdict, dataclass
from typing import Any, Dict, Mapping, Optional, Tuple


INTERPRETATION_SCHEMA_VERSION = "0.1.0"
INTERPRETATION_CERTAINTIES = ("supported", "unresolved", "conflicted")
OBSERVATION_REQUIREMENT_STATUSES = ("satisfied", "missing", "contradicted")


@dataclass(frozen=True)
class ObservationRequirement:
    """規則成立前必須由 Battle Fact 提供的觀察條件。"""

    requirement_id: str
    description: str
    status: str
    source_fact_id: Optional[str]
    observed_value: Any

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ObservedEvidence:
    """只指向既有 Battle Fact 與其 observation evidence。"""

    fact_id: str
    fact_type: str
    timestamp: float
    observation_role: str
    evidence_record_ids: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["timestamp"] = round(float(self.timestamp), 6)
        payload["evidence_record_ids"] = list(self.evidence_record_ids)
        return payload


@dataclass(frozen=True)
class KnowledgeEvidence:
    """版本化 rule knowledge 的精確引用，與觀察證據分開保存。"""

    knowledge_id: str
    knowledge_version: str
    knowledge_sha256: str
    knowledge_path: str
    source_refs: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["source_refs"] = list(self.source_refs)
        return payload


@dataclass(frozen=True)
class RuleInterpretation:
    """Knowledge 對既有 Battle Facts 的解釋；絕不代表新增觀察。"""

    interpretation_id: str
    sequence: int
    timestamp: float
    referenced_battle_fact_ids: Tuple[str, ...]
    referenced_fact_relation_ids: Tuple[str, ...]
    interpretation_type: str
    rule_id: str
    rule_version: str
    required_observations: Tuple[ObservationRequirement, ...]
    observed_evidence: Tuple[ObservedEvidence, ...]
    knowledge_evidence: Tuple[KnowledgeEvidence, ...]
    conclusion: Mapping[str, Any]
    certainty: str
    confidence: float
    unresolved_reason: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "interpretation_id": self.interpretation_id,
            "sequence": self.sequence,
            "timestamp": round(float(self.timestamp), 6),
            "referenced_battle_fact_ids": list(self.referenced_battle_fact_ids),
            "referenced_fact_relation_ids": list(
                self.referenced_fact_relation_ids
            ),
            "interpretation_type": self.interpretation_type,
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "required_observations": [
                item.to_dict() for item in self.required_observations
            ],
            "observed_evidence": [item.to_dict() for item in self.observed_evidence],
            "knowledge_evidence": [
                item.to_dict() for item in self.knowledge_evidence
            ],
            "conclusion": dict(self.conclusion),
            "certainty": self.certainty,
            "confidence": round(float(self.confidence), 6),
            "unresolved_reason": self.unresolved_reason,
        }
