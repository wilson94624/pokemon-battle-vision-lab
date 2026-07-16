"""Checkpoint 1D 的可維護 rule registry 與事件 metadata builders。"""

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Match, Optional, Pattern, Sequence, Tuple

from .battle_event_models import ParseResult
from .battle_event_normalization import parse_go_switch_targets, parse_subjects


MetadataBuilder = Callable[[Match[str], str], Dict[str, Any]]


@dataclass(frozen=True)
class BattleEventRule:
    rule_id: str
    event_type: str
    pattern: Pattern[str]
    builder: MetadataBuilder
    confidence: float = 0.95
    input_event_types: Tuple[str, ...] = ("BATTLE_TEXT", "TRIGGER_NOTIFICATION")

    def parse(
        self, compact_text: str, normalized_text: str, input_event_type: str
    ) -> Optional[ParseResult]:
        if input_event_type not in self.input_event_types:
            return None
        match = self.pattern.fullmatch(compact_text)
        if match is None:
            return None
        metadata = self.builder(match, normalized_text)
        metadata["rule_id"] = self.rule_id
        return ParseResult(
            event_type=self.event_type,
            rule_id=self.rule_id,
            rule_confidence=self.confidence,
            metadata=metadata,
        )


def _subject_metadata(value: str, field: str = "actor") -> Dict[str, Any]:
    names, side = parse_subjects(value)
    metadata: Dict[str, Any] = {}
    if len(names) == 1:
        metadata[field] = names[0]
    elif names:
        metadata["targets"] = names
    if side:
        metadata["side"] = side
    return metadata


def _move(match: Match[str], _: str) -> Dict[str, Any]:
    metadata = _subject_metadata(match.group("subject"))
    metadata.update({"move": match.group("move"), "action": "use"})
    return metadata


def _ability(match: Match[str], _: str) -> Dict[str, Any]:
    metadata = _subject_metadata(match.group("subject"))
    metadata.update({"ability": match.group("ability"), "action": "activate"})
    return metadata


def _item(match: Match[str], _: str) -> Dict[str, Any]:
    metadata = _subject_metadata(match.group("subject"))
    metadata.update({"item": match.group("item"), "action": "activate"})
    return metadata


def _go_switch(_: Match[str], normalized_text: str) -> Dict[str, Any]:
    targets = parse_go_switch_targets(normalized_text)
    metadata: Dict[str, Any] = {"action": "switch_in", "targets": targets}
    if len(targets) == 1:
        metadata["actor"] = targets[0]
    return metadata


def _sent_out(match: Match[str], _: str) -> Dict[str, Any]:
    targets, side = parse_subjects(match.group("pokemon"))
    metadata: Dict[str, Any] = {
        "action": "switch_in",
        "trainer": match.group("trainer"),
        "targets": targets,
    }
    if side:
        metadata["side"] = side
    return metadata


def _faint(match: Match[str], _: str) -> Dict[str, Any]:
    metadata = _subject_metadata(match.group("subject"), field="target")
    metadata["action"] = "faint"
    return metadata


def _status(match: Match[str], _: str) -> Dict[str, Any]:
    metadata = _subject_metadata(match.group("subject"), field="target")
    metadata.update({"status": match.group("status"), "action": "inflict"})
    return metadata


def _status_damage(match: Match[str], _: str) -> Dict[str, Any]:
    metadata = _subject_metadata(match.group("subject"), field="target")
    metadata.update(
        {
            "status": match.group("status"),
            "cause": "status",
            "action": "damage",
        }
    )
    return metadata


def _recoil_damage(match: Match[str], _: str) -> Dict[str, Any]:
    metadata = _subject_metadata(match.group("subject"), field="target")
    metadata.update({"cause": "recoil", "action": "damage"})
    return metadata


def _unspecified_hp_loss(match: Match[str], _: str) -> Dict[str, Any]:
    metadata = _subject_metadata(match.group("subject"), field="target")
    metadata.update({"cause": "unspecified", "action": "damage"})
    return metadata


def _move_result_target(result: str) -> MetadataBuilder:
    def builder(match: Match[str], _: str) -> Dict[str, Any]:
        metadata = _subject_metadata(match.group("target"), field="target")
        metadata.update({"result": result, "action": "resolve"})
        return metadata

    return builder


def _critical_result(_: Match[str], __: str) -> Dict[str, Any]:
    return {"result": "critical_hit", "action": "resolve"}


def _helping_hand_result(match: Match[str], _: str) -> Dict[str, Any]:
    metadata = _subject_metadata(match.group("subject"))
    targets, target_side = parse_subjects(match.group("target"))
    if targets:
        metadata["target"] = targets[0]
    if target_side and "side" not in metadata:
        metadata["side"] = target_side
    metadata.update({"result": "helping_hand_ready", "action": "prepare"})
    return metadata


def _stat_change(match: Match[str], _: str) -> Dict[str, Any]:
    metadata = _subject_metadata(match.group("subjects"), field="target")
    change = match.group("change")
    metadata.update(
        {
            "stat": match.group("stat"),
            "direction": "raise" if "提高" in change else "lower",
            "magnitude": 2 if "大幅" in change else 1,
            "action": "change",
        }
    )
    return metadata


def _weather_start(match: Match[str], _: str) -> Dict[str, Any]:
    return {"weather": match.group("weather"), "action": "start"}


def _weather_end(match: Match[str], _: str) -> Dict[str, Any]:
    return {"weather": match.group("weather"), "action": "end"}


def _terrain(match: Match[str], _: str) -> Dict[str, Any]:
    verb = match.group("verb")
    action = "end" if verb in {"消失", "結束", "恢復原狀"} else "start"
    return {"terrain": match.group("terrain"), "action": action}


def _tailwind_start(match: Match[str], _: str) -> Dict[str, Any]:
    side = "opponent" if match.group("side") else "player"
    return {"effect": "順風", "side": side, "action": "start"}


def _tailwind_end(match: Match[str], _: str) -> Dict[str, Any]:
    side = "opponent" if match.group("side") else "player"
    return {"effect": "順風", "side": side, "action": "end"}


def _effect_counter(match: Match[str], _: str) -> Dict[str, Any]:
    metadata = _subject_metadata(match.group("subject"), field="target")
    metadata.update(
        {
            "effect": match.group("effect"),
            "counter": int(match.group("counter")),
            "action": "update",
        }
    )
    return metadata


def _effect_end(match: Match[str], _: str) -> Dict[str, Any]:
    metadata = _subject_metadata(match.group("subject"), field="target")
    effect = match.group("effect")
    if effect.endswith("狀態"):
        effect = effect[: -len("狀態")]
    metadata.update({"effect": effect, "action": "end"})
    return metadata


def _target_effect(effect: str, action: str) -> MetadataBuilder:
    def builder(match: Match[str], _: str) -> Dict[str, Any]:
        metadata = _subject_metadata(match.group("subject"), field="target")
        metadata.update({"effect": effect, "action": action})
        return metadata

    return builder


def _protected_result(match: Match[str], _: str) -> Dict[str, Any]:
    metadata = _subject_metadata(match.group("subject"), field="target")
    metadata.update({"result": "protected", "effect": "守住", "action": "block"})
    return metadata


def _move_disabled(match: Match[str], _: str) -> Dict[str, Any]:
    metadata = _subject_metadata(match.group("subject"), field="target")
    metadata.update(
        {"effect": "定身法", "move": match.group("move"), "action": "apply"}
    )
    return metadata


def _move_prevented(match: Match[str], _: str) -> Dict[str, Any]:
    metadata = _subject_metadata(match.group("subject"), field="target")
    metadata.update(
        {
            "effect": match.group("effect"),
            "move": match.group("move"),
            "result": "prevented",
            "action": "resolve",
        }
    )
    return metadata


def _form_change(match: Match[str], _: str) -> Dict[str, Any]:
    metadata = _subject_metadata(match.group("subject"))
    metadata.update(
        {"effect": "form_change", "form": match.group("form"), "action": "change"}
    )
    return metadata


def _transformation_activation(match: Match[str], _: str) -> Dict[str, Any]:
    metadata = _subject_metadata(match.group("subject"))
    metadata.update(
        {
            "item": match.group("item"),
            "trainer": match.group("trainer"),
            "device": match.group("device"),
            "action": "activate",
        }
    )
    return metadata


def _field_activation(match: Match[str], _: str) -> Dict[str, Any]:
    return {
        "effect": match.group("effect"),
        "counter": int(match.group("counter")),
        "action": "activate",
    }


def _forfeit_result(_: Match[str], __: str) -> Dict[str, Any]:
    return {"result": "forfeit", "action": "end"}


def _win_result(match: Match[str], _: str) -> Dict[str, Any]:
    return {"result": "win", "loser": match.group("loser"), "action": "end"}


def _rule(
    rule_id: str,
    event_type: str,
    pattern: str,
    builder: MetadataBuilder,
    confidence: float = 0.95,
    input_event_types: Sequence[str] = ("BATTLE_TEXT", "TRIGGER_NOTIFICATION"),
) -> BattleEventRule:
    return BattleEventRule(
        rule_id=rule_id,
        event_type=event_type,
        pattern=re.compile(pattern),
        builder=builder,
        confidence=confidence,
        input_event_types=tuple(input_event_types),
    )


# 高專一規則必須排在廣義規則前；新增語法時只需擴充 registry。
BATTLE_EVENT_RULES = (
    _rule("move.used", "MOVE", r"(?P<subject>.+?)使出了(?P<move>.+?)[!]", _move, 0.99),
    _rule(
        "item.activated",
        "ITEM",
        r"(?P<subject>.+?)使用了(?P<item>.+?)[!]",
        _item,
        0.99,
        ("TRIGGER_NOTIFICATION",),
    ),
    _rule(
        "ability.activated",
        "ABILITY",
        r"(?P<subject>.+?)的(?P<ability>[^!]+)[!]?",
        _ability,
        0.94,
        ("TRIGGER_NOTIFICATION",),
    ),
    _rule("switch.go", "SWITCH", r"上吧!(?P<body>.+)!", _go_switch, 0.98),
    _rule(
        "switch.sent_out",
        "SWITCH",
        r"(?P<trainer>.+?)派出了(?P<pokemon>.+?)[!]",
        _sent_out,
        0.98,
    ),
    _rule("faint.fell", "FAINT", r"(?P<subject>.+?)倒下了[!]", _faint, 0.99),
    _rule(
        "status.damage",
        "DAMAGE_RESULT",
        r"(?P<subject>.+?)受到了(?P<status>灼傷|中毒|劇毒)的傷害[!]",
        _status_damage,
        0.99,
    ),
    _rule(
        "damage.recoil",
        "DAMAGE_RESULT",
        r"(?P<subject>.+?)受到了反作用力造成的傷害[!]",
        _recoil_damage,
        0.99,
    ),
    _rule(
        "damage.hp_loss",
        "DAMAGE_RESULT",
        r"(?P<subject>.+)的生命被削減了一些[!]",
        _unspecified_hp_loss,
        0.93,
    ),
    _rule(
        "status.inflicted",
        "STATUS",
        r"(?P<subject>.+?)被(?P<status>灼傷|中毒|劇毒|麻痺|睡眠|冰凍|混亂)了[!]",
        _status,
        0.99,
    ),
    _rule(
        "stat.changed",
        "STAT_CHANGE",
        r"(?P<subjects>.+?)的(?P<stat>攻擊|防禦、特防|防禦|特攻|特防|速度|命中率|閃避率)(?P<change>大幅提高|提高|大幅降低|降低)了[!]",
        _stat_change,
        0.98,
    ),
    _rule(
        "move_result.helping_hand",
        "MOVE_RESULT",
        r"(?P<subject>.+?)擺出了幫助(?P<target>.+?)的架勢[!]",
        _helping_hand_result,
        0.97,
    ),
    _rule(
        "move_result.super_effective",
        "MOVE_RESULT",
        r"對(?P<target>.+?)效果絕佳[!]",
        _move_result_target("super_effective"),
        0.99,
    ),
    _rule(
        "move_result.not_very_effective",
        "MOVE_RESULT",
        r"對(?P<target>.+?)效果不太好[!]",
        _move_result_target("not_very_effective"),
        0.96,
    ),
    _rule(
        "move_result.miss",
        "MOVE_RESULT",
        r"沒有擊中(?P<target>.+?)[!]",
        _move_result_target("miss"),
        0.99,
    ),
    _rule(
        "move_result.critical",
        "MOVE_RESULT",
        r"擊中了要害[!]",
        _critical_result,
        0.99,
    ),
    _rule(
        "weather.started",
        "WEATHER",
        r"開始下(?P<weather>雨|雪|冰雹)了[!]",
        _weather_start,
        0.98,
    ),
    _rule(
        "weather.ended",
        "WEATHER",
        r"(?P<weather>雨|雪|冰雹|日照)停了[!]",
        _weather_end,
        0.98,
    ),
    _rule(
        "terrain.changed",
        "TERRAIN",
        r"(?P<terrain>青草場地|電氣場地|精神場地|薄霧場地)(?P<verb>出現|展開|消失|結束|恢復原狀)了?[!]",
        _terrain,
        0.94,
    ),
    _rule(
        "field.activated",
        "FIELD_EFFECT",
        r"聽過(?P<effect>滅亡之歌)的寶可夢(?P<counter>[0-9]+)回合後就會滅亡[!]",
        _field_activation,
        0.97,
    ),
    _rule(
        "side_condition.tailwind_started",
        "SIDE_CONDITION",
        r"從(?P<side>對手)?身後吹起了順風[!]",
        _tailwind_start,
        0.98,
    ),
    _rule(
        "side_condition.tailwind_ended",
        "SIDE_CONDITION",
        r"(?P<side>對手的)?順風停止了[!]",
        _tailwind_end,
        0.98,
    ),
    _rule(
        "volatile.counter_updated",
        "VOLATILE_STATUS",
        r"(?P<subject>.+?)的(?P<effect>滅亡計時)變成(?P<counter>[0-9]+)了?[!]",
        _effect_counter,
        0.98,
    ),
    _rule(
        "volatile.effect_ended",
        "VOLATILE_STATUS",
        r"(?P<subject>.+?)的(?P<effect>再來一次狀態|定身法)解除了[!]",
        _effect_end,
        0.97,
    ),
    _rule(
        "volatile.protect_started",
        "VOLATILE_STATUS",
        r"(?P<subject>.+?)擺出了防守的架勢[!]",
        _target_effect("守住", "start"),
        0.97,
    ),
    _rule(
        "volatile.encore_started",
        "VOLATILE_STATUS",
        r"(?P<subject>.+?)接受了再來一次[!]",
        _target_effect("再來一次", "start"),
        0.97,
    ),
    _rule(
        "move_result.protected",
        "MOVE_RESULT",
        r"(?P<subject>.+?)在攻擊中守護住了自己[!]",
        _protected_result,
        0.98,
    ),
    _rule(
        "volatile.move_disabled",
        "VOLATILE_STATUS",
        r"封住了(?P<subject>.+)的(?P<move>.+?)[!]?",
        _move_disabled,
        0.97,
    ),
    _rule(
        "move_result.prevented",
        "MOVE_RESULT",
        r"(?P<subject>.+?)因(?P<effect>定身法)而無法使出(?P<move>.+?)[!]",
        _move_prevented,
        0.98,
    ),
    _rule(
        "transformation.activated",
        "TRANSFORMATION",
        r"(?P<subject>.+)的(?P<item>.+?進化石)和(?P<trainer>.+?)的(?P<device>.+?)產生了反應[!]",
        _transformation_activation,
        0.96,
    ),
    _rule(
        "transformation.completed",
        "TRANSFORMATION",
        r"(?P<subject>.+?)超級進化成了(?P<form>.+?)[!]",
        _form_change,
        0.96,
    ),
    _rule(
        "battle_result.forfeit",
        "BATTLE_RESULT",
        r"有一方選擇了投降[。!]",
        _forfeit_result,
        0.99,
    ),
    _rule(
        "battle_result.win",
        "BATTLE_RESULT",
        r"成功戰勝了(?P<loser>.+?)[!]",
        _win_result,
        0.98,
    ),
)
