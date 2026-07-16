"""Checkpoint 1E 的保守事件關聯規則與集中式 thresholds。"""

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .battle_timeline_models import RelationProposal


AUTO_ACCEPT_THRESHOLD = 0.85
MIN_TIME_GAP_SEC = -2.0
CHAIN_BARRIER_TYPES = {"MOVE", "SWITCH", "TRANSFORMATION", "BATTLE_RESULT"}
PRIMARY_EVENT_TYPES = set(CHAIN_BARRIER_TYPES)
INDEPENDENT_EVENT_TYPES = {
    "WEATHER",
    "TERRAIN",
    "FIELD_EFFECT",
    "SIDE_CONDITION",
    "ABILITY",
    "ITEM",
}


@dataclass(frozen=True)
class MetadataMatch:
    source_key: str
    target_key: str
    label: str
    required: bool = True
    reject_conflict: bool = True


@dataclass(frozen=True)
class CorrelationRule:
    rule_id: str
    source_event_types: Tuple[str, ...]
    target_event_types: Tuple[str, ...]
    maximum_time_gap_sec: float
    required_metadata_matches: Tuple[MetadataMatch, ...]
    optional_metadata_matches: Tuple[MetadataMatch, ...]
    relation_type: str
    base_confidence: float
    stop_on_major_action: bool
    ambiguity_behavior: str
    source_conditions: Tuple[Tuple[str, Any], ...] = ()
    target_conditions: Tuple[Tuple[str, Any], ...] = ()


def _values(event: Mapping[str, Any], key: str) -> Set[str]:
    metadata = event.get("metadata", {})
    if key == "participants":
        keys = ("actor", "target", "targets")
    elif key == "target":
        keys = ("target", "targets")
    else:
        keys = (key,)
    values: Set[str] = set()
    for name in keys:
        value = metadata.get(name)
        if isinstance(value, list):
            values.update(str(item).strip() for item in value if str(item).strip())
        elif value is not None and str(value).strip():
            values.add(str(value).strip())
    return values


def _conditions_match(
    event: Mapping[str, Any], conditions: Sequence[Tuple[str, Any]]
) -> bool:
    metadata = event.get("metadata", {})
    return all(metadata.get(key) == expected for key, expected in conditions)


def _match_evidence(
    source: Mapping[str, Any],
    target: Mapping[str, Any],
    match: MetadataMatch,
) -> Tuple[str, Optional[str]]:
    source_values = _values(source, match.source_key)
    target_values = _values(target, match.target_key)
    if not source_values or not target_values:
        return "missing", None
    common = sorted(source_values & target_values)
    if common:
        return "match", "metadata_match:{}={}".format(match.label, ",".join(common))
    detail = "metadata_conflict:{}:{}!={}".format(
        match.label, ",".join(sorted(source_values)), ",".join(sorted(target_values))
    )
    return "conflict", detail


def evaluate_rule(
    rule: CorrelationRule,
    source: Mapping[str, Any],
    target: Mapping[str, Any],
    has_intervening_major: bool,
    rule_order: int,
) -> Optional[RelationProposal]:
    if source["event_type"] not in rule.source_event_types:
        return None
    if target["event_type"] not in rule.target_event_types:
        return None
    if rule.stop_on_major_action and has_intervening_major:
        return None
    if not _conditions_match(source, rule.source_conditions):
        return None
    if not _conditions_match(target, rule.target_conditions):
        return None
    gap = float(target["start_time"]) - float(source["end_time"])
    if gap < MIN_TIME_GAP_SEC or gap > rule.maximum_time_gap_sec:
        return None

    evidence = ["time_gap_sec={:.6f}".format(gap)]
    optional_matches = 0
    for match in rule.required_metadata_matches:
        outcome, detail = _match_evidence(source, target, match)
        if outcome != "match":
            return None
        if detail:
            evidence.append(detail)
    for match in rule.optional_metadata_matches:
        outcome, detail = _match_evidence(source, target, match)
        if outcome == "match":
            optional_matches += 1
            if detail:
                evidence.append(detail)
        elif outcome == "conflict" and match.reject_conflict:
            return None
        elif detail:
            evidence.append(detail)

    event_confidence = min(float(source["confidence"]), float(target["confidence"]))
    structural_confidence = min(0.99, rule.base_confidence + optional_matches * 0.02)
    confidence = round(0.8 * structural_confidence + 0.2 * event_confidence, 6)
    evidence.append("source_event_confidence={:.6f}".format(float(source["confidence"])))
    evidence.append("target_event_confidence={:.6f}".format(float(target["confidence"])))
    return RelationProposal(
        from_event_id=str(source["id"]),
        to_event_id=str(target["id"]),
        relation_type=rule.relation_type,
        rule_id=rule.rule_id,
        confidence=confidence,
        evidence=evidence,
        ambiguity_behavior=rule.ambiguity_behavior,
        rule_order=rule_order,
    )


def _match(
    source_key: str,
    target_key: str,
    label: str,
    required: bool = True,
    reject_conflict: bool = True,
) -> MetadataMatch:
    return MetadataMatch(source_key, target_key, label, required, reject_conflict)


# 強關聯規則在前；只有這些規則可把事件收進同一 group。
CORRELATION_RULES: Tuple[CorrelationRule, ...] = (
    CorrelationRule(
        "transformation.same_actor_phase",
        ("TRANSFORMATION",),
        ("TRANSFORMATION",),
        10.0,
        (_match("actor", "actor", "actor"),),
        (),
        "SAME_ACTION",
        0.98,
        True,
        "link",
        (("action", "activate"),),
        (("action", "change"),),
    ),
    CorrelationRule(
        "switch.triggered_ability",
        ("SWITCH",),
        ("ABILITY",),
        4.0,
        (_match("participants", "actor", "switch_target_actor"),),
        (),
        "TRIGGERED_BY",
        0.97,
        True,
        "link",
    ),
    CorrelationRule(
        "move.actor_result_actor",
        ("MOVE",),
        ("MOVE_RESULT",),
        4.0,
        (_match("actor", "actor", "actor"),),
        (_match("move", "move", "move"),),
        "RESULT_OF",
        0.96,
        True,
        "link",
    ),
    CorrelationRule(
        "move.explicit_target_result",
        ("MOVE",),
        ("MOVE_RESULT",),
        6.0,
        (_match("target", "target", "target"),),
        (_match("move", "move", "move"),),
        "RESULT_OF",
        0.97,
        True,
        "link",
    ),
    CorrelationRule(
        "move.explicit_target_damage",
        ("MOVE",),
        ("DAMAGE_RESULT",),
        6.0,
        (_match("target", "target", "target"),),
        (),
        "DAMAGE_FROM",
        0.97,
        True,
        "link",
    ),
    CorrelationRule(
        "move.explicit_target_status",
        ("MOVE",),
        ("STATUS",),
        7.0,
        (_match("target", "target", "target"),),
        (),
        "STATUS_FROM",
        0.97,
        True,
        "link",
    ),
    CorrelationRule(
        "move.explicit_target_stat_change",
        ("MOVE",),
        ("STAT_CHANGE",),
        6.0,
        (_match("target", "target", "target"),),
        (),
        "STAT_CHANGE_FROM",
        0.96,
        True,
        "link",
    ),
    CorrelationRule(
        "switch.same_target_stat_change",
        ("SWITCH",),
        ("STAT_CHANGE",),
        4.0,
        (_match("participants", "target", "switch_target"),),
        (),
        "STAT_CHANGE_FROM",
        0.94,
        True,
        "link",
    ),
    CorrelationRule(
        "move.effect_match",
        ("MOVE",),
        ("VOLATILE_STATUS", "SIDE_CONDITION", "FIELD_EFFECT"),
        6.0,
        (_match("move", "effect", "move_effect"),),
        (),
        "STATUS_FROM",
        0.96,
        True,
        "link",
    ),
    CorrelationRule(
        "move.self_stat_change",
        ("MOVE",),
        ("STAT_CHANGE",),
        6.0,
        (_match("actor", "target", "actor_target"),),
        (),
        "STAT_CHANGE_FROM",
        0.95,
        True,
        "link",
    ),
    CorrelationRule(
        "move.recoil_damage",
        ("MOVE",),
        ("DAMAGE_RESULT",),
        6.0,
        (_match("actor", "target", "actor_target"),),
        (),
        "DAMAGE_FROM",
        0.98,
        True,
        "link",
        (),
        (("cause", "recoil"),),
    ),
    CorrelationRule(
        "move.triggered_item",
        ("MOVE",),
        ("ITEM",),
        4.0,
        (_match("actor", "actor", "actor"),),
        (),
        "TRIGGERED_BY",
        0.94,
        True,
        "link",
    ),
    CorrelationRule(
        "item.damage_same_actor",
        ("ITEM",),
        ("DAMAGE_RESULT",),
        2.0,
        (_match("actor", "target", "actor_target"),),
        (),
        "DAMAGE_FROM",
        0.95,
        True,
        "link",
    ),
    CorrelationRule(
        "damage.faint_same_target",
        ("DAMAGE_RESULT",),
        ("FAINT",),
        2.0,
        (_match("target", "target", "target"),),
        (),
        "RESULT_OF",
        0.98,
        True,
        "link",
    ),
    CorrelationRule(
        "move_result.faint_same_target",
        ("MOVE_RESULT",),
        ("FAINT",),
        2.0,
        (_match("target", "target", "target"),),
        (),
        "RESULT_OF",
        0.96,
        True,
        "link",
    ),
    CorrelationRule(
        "damage.same_residual_batch",
        ("DAMAGE_RESULT",),
        ("DAMAGE_RESULT",),
        0.8,
        (
            _match("cause", "cause", "cause"),
            _match("action", "action", "action"),
        ),
        (_match("status", "status", "status"),),
        "SAME_ACTION",
        0.97,
        False,
        "link",
    ),
    CorrelationRule(
        "volatile.same_counter_batch",
        ("VOLATILE_STATUS",),
        ("VOLATILE_STATUS",),
        0.8,
        (
            _match("effect", "effect", "effect"),
            _match("action", "action", "action"),
            _match("counter", "counter", "counter"),
        ),
        (),
        "SAME_ACTION",
        0.98,
        False,
        "link",
        (("action", "update"),),
        (("action", "update"),),
    ),
    CorrelationRule(
        "battle_result.forfeit_then_win",
        ("BATTLE_RESULT",),
        ("BATTLE_RESULT",),
        2.0,
        (),
        (),
        "FOLLOWED_BY",
        0.96,
        True,
        "link",
        (("result", "forfeit"),),
        (("result", "win"),),
    ),
    # 下列規則只建立待審查 temporal edge，不會把兩個 group 合併。
    CorrelationRule(
        "temporal.move_result",
        ("MOVE",),
        ("MOVE_RESULT",),
        5.0,
        (),
        (
            _match("move", "move", "move", required=False),
            _match("target", "target", "target", required=False),
        ),
        "TEMPORALLY_ADJACENT",
        0.68,
        True,
        "review",
    ),
    CorrelationRule(
        "temporal.move_damage",
        ("MOVE",),
        ("DAMAGE_RESULT",),
        6.0,
        (),
        (_match("target", "target", "target", required=False),),
        "TEMPORALLY_ADJACENT",
        0.61,
        True,
        "review",
    ),
    CorrelationRule(
        "temporal.move_status",
        ("MOVE",),
        ("STATUS",),
        7.0,
        (),
        (_match("target", "target", "target", required=False),),
        "TEMPORALLY_ADJACENT",
        0.66,
        True,
        "review",
    ),
    CorrelationRule(
        "temporal.move_stat_change",
        ("MOVE",),
        ("STAT_CHANGE",),
        5.0,
        (),
        (_match("target", "target", "target", required=False),),
        "TEMPORALLY_ADJACENT",
        0.64,
        True,
        "review",
    ),
    CorrelationRule(
        "temporal.move_faint",
        ("MOVE",),
        ("FAINT",),
        8.0,
        (),
        (_match("target", "target", "target", required=False),),
        "TEMPORALLY_ADJACENT",
        0.58,
        True,
        "review",
    ),
    CorrelationRule(
        "temporal.move_weather",
        ("MOVE",),
        ("WEATHER",),
        4.0,
        (),
        (),
        "TEMPORALLY_ADJACENT",
        0.62,
        True,
        "review",
    ),
    CorrelationRule(
        "temporal.ability_stat_change",
        ("ABILITY",),
        ("STAT_CHANGE",),
        2.0,
        (),
        (),
        "TEMPORALLY_ADJACENT",
        0.65,
        True,
        "review",
    ),
)


def has_intervening_major_action(
    events: Sequence[Mapping[str, Any]], source_index: int, target_index: int
) -> bool:
    return any(
        events[index]["event_type"] in CHAIN_BARRIER_TYPES
        for index in range(source_index + 1, target_index)
    )


def relation_proposals(
    events: Sequence[Mapping[str, Any]], target_index: int
) -> List[RelationProposal]:
    target = events[target_index]
    proposals: List[RelationProposal] = []
    for source_index in range(target_index - 1, -1, -1):
        source = events[source_index]
        if float(target["start_time"]) - float(source["end_time"]) > 10.0:
            break
        intervening = has_intervening_major_action(events, source_index, target_index)
        for rule_order, rule in enumerate(CORRELATION_RULES):
            proposal = evaluate_rule(rule, source, target, intervening, rule_order)
            if proposal is not None:
                proposals.append(proposal)
    return proposals


def intrinsically_standalone(event: Mapping[str, Any]) -> bool:
    event_type = str(event["event_type"])
    if event_type in PRIMARY_EVENT_TYPES or event_type in INDEPENDENT_EVENT_TYPES:
        return True
    metadata = event.get("metadata", {})
    if event_type == "DAMAGE_RESULT" and metadata.get("cause") == "status":
        return True
    if event_type == "VOLATILE_STATUS" and metadata.get("action") in {"update", "end"}:
        return True
    return False
