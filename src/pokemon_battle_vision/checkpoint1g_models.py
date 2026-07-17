"""Checkpoint 1G 視覺 observation 的 typed 小型模型。"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class VisualFrameRequest:
    request_id: str
    source_id: str
    role: str
    roi_name: str
    frame_ordinal: int
    pts: float
    side: Optional[str] = None
    slot: Optional[str] = None
    run_ocr: bool = False
    keep_evidence: bool = False


@dataclass
class ExtractedVisualFrame:
    request: VisualFrameRequest
    crop_path: str
    evidence_path: Optional[str]
    fingerprint: Dict[str, Any]
    bar_measurement: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["request"] = asdict(self.request)
        return payload


@dataclass
class OcrObservation:
    request_id: str
    raw_text: str
    confidence: float
    lines: List[Dict[str, Any]]
    preprocessing: List[str]
    error: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class ResolutionEdge:
    edge_id: str
    source_ref: str
    target_entity_id: str
    rule_id: str
    confidence: float
    evidence: List[str] = field(default_factory=list)
    provenance: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
