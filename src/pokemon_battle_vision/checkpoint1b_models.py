"""Checkpoint 1B 的 frame metadata 與候選事件資料模型。"""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List


EVENT_TYPES = (
    "TEAM_PREVIEW",
    "SELECTED_FOUR",
    "MOVE_MENU",
    "BATTLE_TEXT",
    "TRIGGER_NOTIFICATION",
    "RESULT",
)


@dataclass(frozen=True)
class SamplePlanItem:
    sample_index: int
    target_time: float
    frame_index: int
    pts: float


@dataclass(frozen=True)
class FrameScanRecord:
    sample_index: int
    frame_index: int
    target_time: float
    pts: float
    timestamp: str
    roi_available: bool
    ui_state: str
    visible_rois: List[str]
    frame_hash: str
    candidate_scores: Dict[str, float]
    battle_text_evidence: Dict[str, Any] = field(default_factory=dict)
    trigger_notification_evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EventCandidate:
    event_id: str
    type: str
    start_frame: int
    end_frame: int
    start_time: float
    end_time: float
    start_timestamp: str
    end_timestamp: str
    duration_sec: float
    confidence: float
    sample_count: int
    visible_rois: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
