"""Checkpoint 1D Battle Event 的純資料模型。"""

from dataclasses import asdict, dataclass
from typing import Any, Dict


EVENT_TYPES = (
    "MOVE",
    "MOVE_RESULT",
    "DAMAGE_RESULT",
    "ABILITY",
    "ITEM",
    "STATUS",
    "STAT_CHANGE",
    "WEATHER",
    "TERRAIN",
    "FIELD_EFFECT",
    "SIDE_CONDITION",
    "VOLATILE_STATUS",
    "TRANSFORMATION",
    "SWITCH",
    "FAINT",
    "BATTLE_RESULT",
    "UNKNOWN_EVENT",
)


@dataclass(frozen=True)
class ParseResult:
    """單一規則的解析結果；不包含來源 candidate 的時間與 provenance。"""

    event_type: str
    rule_id: str
    rule_confidence: float
    metadata: Dict[str, Any]


@dataclass(frozen=True)
class BattleEvent:
    """後續模組唯一應依賴的事件中介格式。"""

    id: str
    timestamp: float
    start_time: float
    end_time: float
    candidate_id: str
    event_type: str
    raw_text: str
    normalized_text: str
    confidence: float
    source: Dict[str, Any]
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
