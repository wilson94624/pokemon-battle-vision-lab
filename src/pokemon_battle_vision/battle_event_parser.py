"""由 registry 驅動的 Checkpoint 1D rule-based parser。"""

from typing import Sequence

from .battle_event_models import ParseResult
from .battle_event_normalization import compact_battle_text, normalize_battle_text
from .battle_event_rules import BATTLE_EVENT_RULES, BattleEventRule


class BattleEventParser:
    """解析單一已接受 OCR 訊息；不保存或推演 battle state。"""

    def __init__(self, rules: Sequence[BattleEventRule] = BATTLE_EVENT_RULES) -> None:
        self.rules = tuple(rules)

    def parse(self, raw_text: str, input_event_type: str) -> ParseResult:
        normalized = normalize_battle_text(raw_text)
        compact = compact_battle_text(normalized)
        for rule in self.rules:
            result = rule.parse(compact, normalized, input_event_type)
            if result is not None:
                return result
        return ParseResult(
            event_type="UNKNOWN_EVENT",
            rule_id="unknown.unmatched",
            rule_confidence=0.0,
            metadata={"rule_id": "unknown.unmatched"},
        )
