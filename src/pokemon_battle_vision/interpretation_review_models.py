"""Checkpoint 1J Interpretation Human Review 的獨立資料模型。"""

from dataclasses import asdict, dataclass
from typing import Any, Dict, Mapping, Optional, Tuple


REVIEW_SCHEMA_VERSION = "0.1.0"
REVIEW_STATUSES = ("accepted", "rejected", "needs_review", "deferred")
ISSUE_CODES = (
    "evidence_incomplete",
    "fact_reference_error",
    "relation_reference_error",
    "knowledge_reference_error",
    "conclusion_overreach",
    "certainty_incorrect",
    "unresolved_outcome_correct",
    "conflict_requires_investigation",
    "other",
)
CONFLICT_CATEGORIES = (
    "observation_error_suspected",
    "identity_resolution_error_suspected",
    "knowledge_data_error_suspected",
    "rule_engine_error_suspected",
    "version_mismatch",
    "insufficient_evidence",
    "unresolved_other",
)


@dataclass(frozen=True)
class InterpretationReviewRecord:
    """Human decision 與 immutable interpretation payload 分離保存。"""

    review_record_id: str
    interpretation_id: str
    interpretation_origin: str
    interpretation_payload_hash: str
    certainty: str
    review_status: str
    reviewer: Optional[str]
    reviewed_at: Optional[str]
    review_reason: Optional[str]
    issue_codes: Tuple[str, ...]
    conflict_category: Optional[str]
    interpretation_version: str
    knowledge_version: str
    review_schema_version: str
    review_card_path: str
    conflict_context: Optional[Mapping[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["issue_codes"] = list(self.issue_codes)
        payload["conflict_context"] = (
            dict(self.conflict_context) if self.conflict_context else None
        )
        return payload
