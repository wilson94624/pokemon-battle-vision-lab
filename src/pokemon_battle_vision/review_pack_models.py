"""Checkpoint 1B Human Review Pack 的資料模型與允許值。"""

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Tuple


HUMAN_STATUSES = (
    "pending",
    "correct",
    "false_positive",
    "wrong_type",
    "needs_split",
    "needs_merge",
    "uncertain",
)

BOUNDARY_QUALITIES = (
    "",
    "good",
    "starts_too_early",
    "starts_too_late",
    "ends_too_early",
    "ends_too_late",
    "both_inaccurate",
    "uncertain",
)


@dataclass(frozen=True)
class CandidateEvidencePoint:
    roles: Tuple[str, ...]
    frame_index: int
    pts: float
    score: float
    text_structure_strength: float
    evidence_level: str
    decision: str
    side: str = ""
    panel_score: float = 0.0
    text_score: float = 0.0
    icon_score: float = 0.0
    combined_score: float = 0.0
    analysis_roi_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["roles"] = list(self.roles)
        return payload


@dataclass(frozen=True)
class CandidateFrameSelection:
    candidate_id: str
    start_frame: int
    middle_frame: int
    end_frame: int
    start_pts: float
    middle_pts: float
    end_pts: float
    representative_frame: int
    representative_pts: float
    strategy: str
    evidence_points: Tuple[CandidateEvidencePoint, ...]


@dataclass(frozen=True)
class CandidateReviewRecord:
    candidate_id: str
    predicted_type: str
    start_frame: int
    middle_frame: int
    end_frame: int
    start_time: float
    middle_time: float
    end_time: float
    duration_sec: float
    confidence: float
    visible_rois: List[str]
    representative_time: float
    representative_frame: int
    review_image_path: str
    review_frame_strategy: str
    evidence_frames: List[Dict[str, Any]]
    human_status: str = "pending"
    corrected_type: str = ""
    boundary_quality: str = ""
    merge_with_candidate_id: str = ""
    split_required: bool = False
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CoverageSample:
    sample_index: int
    target_time: float
    frame_index: int
    pts: float
    candidate_ids: List[str]
    candidate_types: List[str]

    @property
    def label(self) -> str:
        return "NO_CANDIDATE" if not self.candidate_ids else ", ".join(self.candidate_ids)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["label"] = self.label
        return payload


@dataclass(frozen=True)
class EncodedFrameEvidence:
    frame_index: int
    pts: float
    full_frame_jpeg: bytes
    roi_jpegs: Dict[str, bytes]
