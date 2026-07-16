"""Checkpoint 1C OCR、聚合、驗證與人工審查資料模型。"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


VALIDATION_LABELS = ("VALID_TEXT", "NO_TEXT", "UNCERTAIN")
WORKFLOW_STATUSES = ("auto_accepted", "needs_review", "rejected")
HUMAN_ACTIONS = (
    "accept",
    "edit_text",
    "mark_no_text",
    "merge_previous",
    "merge_next",
    "split",
)


@dataclass(frozen=True)
class OcrFrameSelection:
    event_id: str
    event_type: str
    frame_ordinal: int
    pts: float
    selection_reason: str
    selection_reasons: List[str]
    roi_name: str
    image_path: str
    frame_quality: float
    visual_text_strength: float
    detector_template_strength: float
    insufficient_frame_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PreprocessingVariant:
    variant_id: str
    operations: List[str]
    image_path: str
    quality_weight: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OcrRawResult:
    result_id: str
    event_id: str
    event_type: str
    frame_ordinal: int
    pts: float
    roi_name: str
    variant_id: str
    variant_operations: List[str]
    image_path: str
    raw_text: str
    normalized_text: str
    ocr_confidence: float
    character_count: int
    cjk_character_count: int
    line_count: int
    engine: str
    engine_revision: str
    language: str
    frame_quality: float
    variant_quality: float
    visual_text_strength: float
    detector_template_strength: float
    error: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OcrAggregate:
    event_id: str
    event_type: str
    best_text: str
    best_confidence: float
    consensus_confidence: float
    supporting_result_ids: List[str]
    supporting_frame_ordinals: List[int]
    disagreement_score: float
    selected_frame_ordinal: Optional[int]
    selected_variant_id: str
    candidate_status: str
    review_reasons: List[str]
    nonempty_result_count: int
    distinct_text_count: int
    cjk_character_count: int
    line_count: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TextValidationRecord:
    event_id: str
    event_type: str
    start_time: float
    end_time: float
    validation_label: str
    workflow_status: str
    ocr_text: str
    ocr_confidence: float
    consensus_confidence: float
    validation_confidence: float
    review_reasons: List[str]
    supporting_result_ids: List[str]
    duplicate_group_id: Optional[str] = None
    possible_duplicate_of: Optional[str] = None
    duplicate_confidence: float = 0.0
    human_text: Optional[str] = None
    human_decision: Optional[str] = None
    human_action: Optional[str] = None
    merge_with_event_id: Optional[str] = None
    split_points: Optional[List[float]] = None
    reviewed_at: Optional[str] = None
    reviewed_by: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OcrEngineResult:
    job_id: str
    raw_text: str
    confidence: float
    lines: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
