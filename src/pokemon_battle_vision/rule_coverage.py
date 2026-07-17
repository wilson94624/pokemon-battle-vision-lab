"""Checkpoint 1J：以 v2 additive knowledge 擴充既有 Battle Facts 的可審查解釋。"""

from dataclasses import replace
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .rule_interpretation import (
    _confidence,
    _knowledge,
    _metadata,
    _observed,
    _participant,
    _same_identity,
    _unique_knowledge,
)
from .rule_interpretation_models import (
    ObservationRequirement,
    RuleInterpretation,
)
from .rule_knowledge import PokemonRuleKnowledgeBase


EXPANDED_INTERPRETATION_VERSION = "0.1.0"


def _matches_metadata(
    metadata: Mapping[str, Any], pattern: Mapping[str, Any]
) -> bool:
    return all(metadata.get(key) == value for key, value in pattern.items())


def _requirement(
    requirement_id: str,
    description: str,
    source_fact_id: Optional[str],
    observed_value: Any,
) -> ObservationRequirement:
    return ObservationRequirement(
        requirement_id=requirement_id,
        description=description,
        status="satisfied",
        source_fact_id=source_fact_id,
        observed_value=observed_value,
    )


def _identity_matches(
    source_fact: Mapping[str, Any], outcome_fact: Mapping[str, Any]
) -> bool:
    source = _participant(source_fact, "actor") or _participant(
        source_fact, "target"
    )
    outcome = _participant(outcome_fact, "target") or _participant(
        outcome_fact, "actor"
    )
    return _same_identity(source, outcome)


def _linked_interpretation(
    source_fact: Mapping[str, Any],
    outcome_fact: Mapping[str, Any],
    relation: Mapping[str, Any],
    rule: Mapping[str, Any],
    kb: PokemonRuleKnowledgeBase,
) -> RuleInterpretation:
    identity_constraint = str(rule["identity_constraint"])
    identity_matches = (
        True
        if identity_constraint == "none"
        else _identity_matches(source_fact, outcome_fact)
    )
    requirements = [
        _requirement(
            "source_fact_pattern",
            "source Battle Fact type 與 metadata 必須符合版本化規則",
            str(source_fact["fact_id"]),
            dict(_metadata(source_fact)),
        ),
        _requirement(
            "outcome_fact_pattern",
            "outcome Battle Fact type 與 metadata 必須符合版本化規則",
            str(outcome_fact["fact_id"]),
            dict(_metadata(outcome_fact)),
        ),
        _requirement(
            "active_allowed_relation",
            "1H relation 必須 active 且 relation type 在規則 allowlist",
            None,
            {
                "fact_relation_id": relation["fact_relation_id"],
                "relation_type": relation["relation_type"],
            },
        ),
    ]
    if identity_constraint != "none":
        requirements.append(
            _requirement(
                "identity_continuity",
                "source actor 與 outcome target 必須是同一個已觀察 identity",
                str(outcome_fact["fact_id"]),
                identity_matches,
            )
        )
    knowledge = [_knowledge(kb, rule)]
    move_name = _metadata(source_fact).get("move")
    move = kb.resolve_move(str(move_name)) if move_name else None
    if move:
        knowledge.append(_knowledge(kb, move))
    causal_claim = rule["relation_semantics"] == "causal_relation"
    return RuleInterpretation(
        interpretation_id="",
        sequence=0,
        timestamp=min(
            float(source_fact["timestamp"]), float(outcome_fact["timestamp"])
        ),
        referenced_battle_fact_ids=(
            str(source_fact["fact_id"]),
            str(outcome_fact["fact_id"]),
        ),
        referenced_fact_relation_ids=(str(relation["fact_relation_id"]),),
        interpretation_type=str(rule["interpretation_type"]),
        rule_id=str(rule["rule_id"]),
        rule_version=str(rule["rule_version"]),
        required_observations=tuple(requirements),
        observed_evidence=(
            _observed(source_fact, "rule_source"),
            _observed(outcome_fact, "rule_outcome"),
        ),
        knowledge_evidence=_unique_knowledge(knowledge),
        conclusion={
            "code": str(rule["conclusion_code"]),
            "summary": str(rule["conclusion_summary"]),
            "derived_values": {
                "source_metadata": dict(_metadata(source_fact)),
                "outcome_metadata": dict(_metadata(outcome_fact)),
                "relation_type": relation["relation_type"],
                "relation_semantics": rule["relation_semantics"],
                "causal_claim": causal_claim,
                "identity_constraint": identity_constraint,
                "identity_matches": identity_matches,
            },
        },
        certainty="supported",
        confidence=_confidence((source_fact, outcome_fact), relation),
        unresolved_reason=None,
    )


def _single_interpretation(
    fact: Mapping[str, Any],
    rule: Mapping[str, Any],
    kb: PokemonRuleKnowledgeBase,
) -> RuleInterpretation:
    return RuleInterpretation(
        interpretation_id="",
        sequence=0,
        timestamp=float(fact["timestamp"]),
        referenced_battle_fact_ids=(str(fact["fact_id"]),),
        referenced_fact_relation_ids=(),
        interpretation_type=str(rule["interpretation_type"]),
        rule_id=str(rule["rule_id"]),
        rule_version=str(rule["rule_version"]),
        required_observations=(
            _requirement(
                "explicit_fact_pattern",
                "Battle Fact type 與 metadata 必須明確符合版本化 lifecycle rule",
                str(fact["fact_id"]),
                dict(_metadata(fact)),
            ),
        ),
        observed_evidence=(_observed(fact, "explicit_lifecycle_observation"),),
        knowledge_evidence=(_knowledge(kb, rule),),
        conclusion={
            "code": str(rule["conclusion_code"]),
            "summary": str(rule["conclusion_summary"]),
            "derived_values": {
                "observed_metadata": dict(_metadata(fact)),
                "causal_claim": False,
                "duration_inferred": False,
            },
        },
        certainty="supported",
        confidence=_confidence((fact,)),
        unresolved_reason=None,
    )


def build_expanded_rule_interpretations(
    facts: Sequence[Mapping[str, Any]],
    relations: Sequence[Mapping[str, Any]],
    kb: PokemonRuleKnowledgeBase,
) -> List[RuleInterpretation]:
    """只依 declarative patterns 選取；不含 fact ID 或 timestamp exceptions。"""
    facts_by_id = {str(fact["fact_id"]): fact for fact in facts}
    selected: List[RuleInterpretation] = []
    for rule in kb.linked_rules():
        for relation in relations:
            if not relation.get("active"):
                continue
            if relation.get("relation_type") not in rule["allowed_relation_types"]:
                continue
            source = facts_by_id.get(str(relation.get("from_fact_id")))
            outcome = facts_by_id.get(str(relation.get("to_fact_id")))
            if not source or not outcome:
                continue
            if source.get("fact_type") != rule["source_fact_type"]:
                continue
            if outcome.get("fact_type") != rule["outcome_fact_type"]:
                continue
            if not _matches_metadata(_metadata(source), rule["source_metadata"]):
                continue
            if not _matches_metadata(_metadata(outcome), rule["outcome_metadata"]):
                continue
            if (
                rule["identity_constraint"]
                == "source_actor_matches_outcome_target"
                and not _identity_matches(source, outcome)
            ):
                continue
            selected.append(_linked_interpretation(source, outcome, relation, rule, kb))

    for rule in kb.single_rules():
        for fact in facts:
            if fact.get("fact_type") != rule["fact_type"]:
                continue
            if _matches_metadata(_metadata(fact), rule["required_metadata"]):
                selected.append(_single_interpretation(fact, rule, kb))

    deduplicated: Dict[Tuple[str, str, Tuple[str, ...]], RuleInterpretation] = {}
    for item in selected:
        key = (
            item.interpretation_type,
            item.rule_id,
            item.referenced_battle_fact_ids,
        )
        deduplicated.setdefault(key, item)
    ordered = sorted(
        deduplicated.values(),
        key=lambda item: (
            item.timestamp,
            item.interpretation_type,
            item.rule_id,
            item.referenced_battle_fact_ids,
        ),
    )
    return [
        replace(
            item,
            interpretation_id="rule-interpretation-1j-{:04d}".format(index),
            sequence=index,
        )
        for index, item in enumerate(ordered, start=1)
    ]


def _fact_ids(
    facts: Sequence[Mapping[str, Any]],
    fact_type: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> List[str]:
    return [
        str(fact["fact_id"])
        for fact in facts
        if (fact_type is None or fact.get("fact_type") == fact_type)
        and (metadata is None or _matches_metadata(_metadata(fact), metadata))
    ]


def build_rule_coverage_audit(
    facts: Sequence[Mapping[str, Any]],
    relations: Sequence[Mapping[str, Any]],
    existing_interpretations: Sequence[Mapping[str, Any]],
    expanded_interpretations: Sequence[RuleInterpretation],
) -> Dict[str, Any]:
    expanded = [item.to_dict() for item in expanded_interpretations]
    by_rule: Dict[str, List[Mapping[str, Any]]] = {}
    for row in expanded:
        by_rule.setdefault(str(row["rule_id"]), []).append(row)
    adopted = []
    for rule_id in sorted(by_rule):
        rows = by_rule[rule_id]
        adopted.append(
            {
                "coverage_id": "coverage-adopted-{:04d}".format(len(adopted) + 1),
                "category": rows[0]["interpretation_type"],
                "decision": "adopted",
                "rule_id": rule_id,
                "reason": "現有 Battle Facts、explicit metadata 與允許的 active relation 足以 deterministic 驗證。",
                "matching_interpretation_ids": [
                    row["interpretation_id"] for row in rows
                ],
                "matching_fact_ids": sorted(
                    {
                        fact_id
                        for row in rows
                        for fact_id in row["referenced_battle_fact_ids"]
                    }
                ),
                "matching_relation_ids": sorted(
                    {
                        relation_id
                        for row in rows
                        for relation_id in row["referenced_fact_relation_ids"]
                    }
                ),
            }
        )

    rejected_specs = [
        (
            "close_combat_stat_change",
            "STAT_CHANGE_CONSEQUENCE",
            "rejected",
            "現有 parsed observation 顯示防禦、特防提高，與外部 Gen 9 reference 不一致；可能涉及 OCR、Champions 規則差異或未觀察 modifier，不能直接套用。",
            _fact_ids(facts, "MOVE_USED", {"move": "近身戰"}),
        ),
        (
            "charm_stat_change",
            "STAT_CHANGE_CONSEQUENCE",
            "rejected",
            "撒嬌後觀察到攻擊提高，缺少可排除反射／能力／規則差異的明確 evidence。",
            _fact_ids(facts, "MOVE_USED", {"move": "撒嬌"}),
        ),
        (
            "weather_ball_dynamic_type",
            "TYPE_EFFECTIVENESS",
            "rejected",
            "氣象球 fact 沒有明確 target、type 或 effectiveness outcome；推導會依賴 simulator mechanics。",
            _fact_ids(facts, "MOVE_USED", {"move": "氣象球"}),
        ),
        (
            "burn_residual_parent_move",
            "STATUS_DAMAGE_CAUSALITY",
            "rejected",
            "殘餘灼傷傷害已是 Battle Fact；人工拒絕的 temporal adjacency 不可重新包裝成 move causality。",
            _fact_ids(facts, "DAMAGE_OBSERVED", {"cause": "status", "status": "灼傷"}),
        ),
        (
            "perish_song_future_ko",
            "FIELD_EFFECT_CONSEQUENCE",
            "rejected",
            "影片在倒數完成前結束，沒有可歸因於滅亡之歌的 observed KO。",
            _fact_ids(facts, "FIELD_EFFECT_STARTED", {"effect": "滅亡之歌"}),
        ),
        (
            "complete_type_chart",
            "TYPE_EFFECTIVENESS",
            "rejected",
            "目前 facts 沒有更多明確 effectiveness outcomes；完整 type chart 超出 selected evidence scope。",
            [],
        ),
        (
            "levitate_and_good_as_gold",
            "ABILITY_IMMUNITY",
            "deferred",
            "既有 1I records 已正確 unresolved；仍缺 target／failure outcome／matching ability observations。",
            sorted(
                {
                    fact_id
                    for row in existing_interpretations
                    if row["rule_id"]
                    in {
                        "ability_immunity.levitate_ground.v1",
                        "ability_immunity.good_as_gold_status.v1",
                    }
                    for fact_id in row["referenced_battle_fact_ids"]
                }
            ),
        ),
    ]
    rejected = [
        {
            "coverage_id": "coverage-nonadopted-{:04d}".format(index),
            "candidate": candidate,
            "category": category,
            "decision": decision,
            "reason": reason,
            "matching_fact_ids": fact_ids,
        }
        for index, (candidate, category, decision, reason, fact_ids) in enumerate(
            rejected_specs, start=1
        )
    ]
    fact_type_counts: Dict[str, int] = {}
    for fact in facts:
        key = str(fact["fact_type"])
        fact_type_counts[key] = fact_type_counts.get(key, 0) + 1
    return {
        "schema_version": "0.1.0",
        "checkpoint": "1J",
        "kind": "rule_coverage_audit",
        "source_battle_fact_count": len(facts),
        "source_fact_relation_count": len(relations),
        "source_fact_type_counts": dict(sorted(fact_type_counts.items())),
        "existing_interpretation_count": len(existing_interpretations),
        "expanded_interpretation_count": len(expanded),
        "policy": "existing_explicit_evidence_only_no_hidden_information_no_simulator_inference",
        "adopted": adopted,
        "not_adopted": rejected,
    }
