"""Checkpoint 1I 最小 deterministic rule interpretation engine。"""

from dataclasses import replace
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .rule_interpretation_models import (
    KnowledgeEvidence,
    ObservationRequirement,
    ObservedEvidence,
    RuleInterpretation,
)
from .rule_knowledge import PokemonRuleKnowledgeBase, normalize_rule_alias


TYPE_RESULT_CODES = {
    "super_effective": "super_effective",
    "not_very_effective": "not_very_effective",
    "immune": "immune",
    "no_effect": "immune",
    "neutral": "neutral",
}


def _metadata(fact: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    if not fact:
        return {}
    attributes = fact.get("attributes") or {}
    return attributes.get("parsed_metadata") or {}


def _participant(
    fact: Optional[Mapping[str, Any]], role: str
) -> Optional[Mapping[str, Any]]:
    if not fact:
        return None
    return next(
        (row for row in fact.get("participants", []) if row.get("role") == role),
        None,
    )


def _same_identity(
    left: Optional[Mapping[str, Any]], right: Optional[Mapping[str, Any]]
) -> bool:
    """僅以已觀察 entity/species/name 比對，不用 rule knowledge 補 identity。"""
    if not left or not right:
        return False
    left_entity = left.get("entity_id")
    right_entity = right.get("entity_id")
    if left_entity and right_entity:
        return left_entity == right_entity
    left_species = left.get("canonical_species_id")
    right_species = right.get("canonical_species_id")
    if left_species is not None and right_species is not None:
        return int(left_species) == int(right_species)
    left_name = left.get("canonical_name") or left.get("observed_name")
    right_name = right.get("canonical_name") or right.get("observed_name")
    return bool(
        left_name
        and right_name
        and normalize_rule_alias(str(left_name))
        == normalize_rule_alias(str(right_name))
    )


def _requirement(
    requirement_id: str,
    description: str,
    status: str,
    source_fact_id: Optional[str] = None,
    observed_value: Any = None,
) -> ObservationRequirement:
    return ObservationRequirement(
        requirement_id=requirement_id,
        description=description,
        status=status,
        source_fact_id=source_fact_id,
        observed_value=observed_value,
    )


def _observed(
    fact: Mapping[str, Any], observation_role: str
) -> ObservedEvidence:
    return ObservedEvidence(
        fact_id=str(fact["fact_id"]),
        fact_type=str(fact["fact_type"]),
        timestamp=float(fact["timestamp"]),
        observation_role=observation_role,
        evidence_record_ids=tuple(
            str(row["record_id"]) for row in fact.get("evidence", [])
        ),
    )


def _knowledge(
    kb: PokemonRuleKnowledgeBase, row: Mapping[str, Any]
) -> KnowledgeEvidence:
    knowledge_id = str(row["knowledge_id"])
    return KnowledgeEvidence(
        knowledge_id=knowledge_id,
        knowledge_version=str(kb.payload["knowledge_version"]),
        knowledge_sha256=kb.data_sha256,
        knowledge_path=kb.knowledge_path(knowledge_id),
        source_refs=tuple(str(value) for value in row["source_refs"]),
    )


def _unique_observed(
    items: Iterable[ObservedEvidence],
) -> Tuple[ObservedEvidence, ...]:
    by_fact: Dict[str, ObservedEvidence] = {}
    for item in items:
        by_fact.setdefault(item.fact_id, item)
    return tuple(by_fact.values())


def _unique_knowledge(
    items: Iterable[KnowledgeEvidence],
) -> Tuple[KnowledgeEvidence, ...]:
    by_id: Dict[str, KnowledgeEvidence] = {}
    for item in items:
        by_id.setdefault(item.knowledge_id, item)
    return tuple(by_id.values())


def _confidence(
    facts: Iterable[Mapping[str, Any]], relation: Optional[Mapping[str, Any]] = None
) -> float:
    values = [float(row.get("confidence", 0.0)) for row in facts]
    if relation is not None:
        values.append(float(relation.get("confidence", 0.0)))
    return round(min(values) if values else 0.0, 6)


def _expected_type_result(multiplier: float) -> str:
    if multiplier == 0:
        return "immune"
    if multiplier > 1:
        return "super_effective"
    if multiplier < 1:
        return "not_very_effective"
    return "neutral"


def interpret_type_effectiveness(
    move_fact: Mapping[str, Any],
    outcome_fact: Mapping[str, Any],
    relation: Mapping[str, Any],
    kb: PokemonRuleKnowledgeBase,
) -> RuleInterpretation:
    move_name = _metadata(move_fact).get("move")
    move = kb.resolve_move(str(move_name)) if move_name else None
    target = _participant(outcome_fact, "target")
    species = kb.species_types(
        target.get("canonical_species_id") if target else None
    )
    type_matchup_known = bool(
        move
        and species
        and kb.supports_type_matchup(str(move["move_type"]), species["types"])
    )
    observed_result = TYPE_RESULT_CODES.get(str(_metadata(outcome_fact).get("result")))
    requirements = (
        _requirement(
            "move_observed",
            "MOVE_USED 必須明確包含招式名稱",
            "satisfied" if move_name else "missing",
            str(move_fact["fact_id"]),
            move_name,
        ),
        _requirement(
            "move_rule_known",
            "招式必須存在於版本化 rule knowledge",
            "satisfied" if move else "missing",
            str(move_fact["fact_id"]),
            move.get("move_id") if move else None,
        ),
        _requirement(
            "target_identity_observed",
            "MOVE_RESOLVED 必須有可解析 target identity",
            "satisfied" if target and target.get("canonical_species_id") else "missing",
            str(outcome_fact["fact_id"]),
            target.get("canonical_species_id") if target else None,
        ),
        _requirement(
            "target_types_known",
            "target species 的 types 必須存在於 rule knowledge",
            "satisfied" if species else "missing",
            str(outcome_fact["fact_id"]),
            list(species["types"]) if species else None,
        ),
        _requirement(
            "type_matchup_entries_known",
            "每個 attacking／defending type pair 必須明確版本化",
            "satisfied" if type_matchup_known else "missing",
            str(outcome_fact["fact_id"]),
            type_matchup_known,
        ),
        _requirement(
            "effectiveness_outcome_observed",
            "MOVE_RESOLVED 必須明確觀察 effectiveness result",
            "satisfied" if observed_result else "missing",
            str(outcome_fact["fact_id"]),
            observed_result,
        ),
        _requirement(
            "active_fact_relation",
            "MOVE_USED 與 MOVE_RESOLVED 必須由 active 1H relation 連結",
            "satisfied" if relation.get("active") else "contradicted",
            None,
            relation.get("fact_relation_id"),
        ),
    )
    observed = (_observed(move_fact, "move"), _observed(outcome_fact, "outcome"))
    knowledge_items: List[KnowledgeEvidence] = []
    if move:
        knowledge_items.append(_knowledge(kb, move))
    if species:
        knowledge_items.append(_knowledge(kb, species))
    knowledge_items.append(_knowledge(kb, kb.type_chart))

    missing = [row.requirement_id for row in requirements if row.status != "satisfied"]
    multiplier = None
    components: List[Dict[str, Any]] = []
    expected_result = None
    certainty = "unresolved"
    code = "TYPE_EFFECTIVENESS_UNRESOLVED"
    summary = "觀察不足，無法用 type chart 解釋結果。"
    unresolved_reason = "missing_or_invalid_observations:{}".format(
        ",".join(missing)
    ) if missing else None
    if not missing and move and species and observed_result and type_matchup_known:
        multiplier, components = kb.type_multiplier(
            str(move["move_type"]), species["types"]
        )
        expected_result = _expected_type_result(multiplier)
        if expected_result == observed_result:
            certainty = "supported"
            code = "OBSERVED_OUTCOME_CONSISTENT_WITH_TYPE_CHART"
            summary = "觀察到的招式效果與版本化 type chart 一致。"
            unresolved_reason = None
        else:
            certainty = "conflicted"
            code = "OBSERVED_OUTCOME_CONFLICTS_WITH_TYPE_CHART"
            summary = "觀察結果與目前最小 type chart 不一致；保留衝突。"
            unresolved_reason = "observed_result_does_not_match_type_chart"

    type_chart = kb.type_chart
    return RuleInterpretation(
        interpretation_id="",
        sequence=0,
        timestamp=min(float(move_fact["timestamp"]), float(outcome_fact["timestamp"])),
        referenced_battle_fact_ids=(
            str(move_fact["fact_id"]),
            str(outcome_fact["fact_id"]),
        ),
        referenced_fact_relation_ids=(str(relation["fact_relation_id"]),),
        interpretation_type="TYPE_EFFECTIVENESS",
        rule_id=str(type_chart["rule_id"]),
        rule_version=str(type_chart["rule_version"]),
        required_observations=requirements,
        observed_evidence=observed,
        knowledge_evidence=_unique_knowledge(knowledge_items),
        conclusion={
            "code": code,
            "summary": summary,
            "derived_values": {
                "move_id": move.get("move_id") if move else None,
                "move_type": move.get("move_type") if move else None,
                "target_canonical_species_id": (
                    target.get("canonical_species_id") if target else None
                ),
                "target_types": list(species["types"]) if species else None,
                "type_multiplier": multiplier,
                "type_components": components,
                "observed_result": observed_result,
                "expected_result": expected_result,
            },
        },
        certainty=certainty,
        confidence=_confidence(observed and (move_fact, outcome_fact), relation),
        unresolved_reason=unresolved_reason,
    )


def interpret_target_validity(
    move_fact: Mapping[str, Any],
    outcome_fact: Mapping[str, Any],
    relation: Mapping[str, Any],
    kb: PokemonRuleKnowledgeBase,
) -> RuleInterpretation:
    move = kb.resolve_move(str(_metadata(move_fact).get("move") or ""))
    target_rule = kb.target_rule(str(move["move_id"])) if move else None
    actor = _participant(move_fact, "actor")
    outcome_actor = _participant(outcome_fact, "actor")
    target = _participant(outcome_fact, "target")
    result = _metadata(outcome_fact).get("result")
    actor_matches = _same_identity(actor, outcome_actor)
    same_side = bool(actor and target and actor.get("side") == target.get("side"))
    explicit_success = bool(
        target_rule and result in target_rule["explicit_success_results"]
    )
    requirements = (
        _requirement("actor_observed", "actor 必須可觀察", "satisfied" if actor else "missing", str(move_fact["fact_id"]), actor.get("observed_name") if actor else None),
        _requirement("target_observed", "target 必須可觀察", "satisfied" if target else "missing", str(outcome_fact["fact_id"]), target.get("observed_name") if target else None),
        _requirement("actor_identity_continues", "result actor 必須與 move actor 一致", "satisfied" if actor_matches else "contradicted", str(outcome_fact["fact_id"]), actor_matches),
        _requirement("same_side_observed", "Helping Hand actor 與 target 必須在同一觀察 side", "satisfied" if same_side else "contradicted", str(outcome_fact["fact_id"]), same_side),
        _requirement("explicit_success_observed", "必須觀察到遊戲接受該 target 的成功結果", "satisfied" if explicit_success else "missing", str(outcome_fact["fact_id"]), result),
        _requirement("active_fact_relation", "move 與 result 必須由 active relation 連結", "satisfied" if relation.get("active") else "contradicted", None, relation.get("fact_relation_id")),
    )
    missing = [row.requirement_id for row in requirements if row.status != "satisfied"]
    certainty = "supported" if not missing and move and target_rule else "unresolved"
    code = "OBSERVED_TARGET_ACCEPTED_BY_GAME" if certainty == "supported" else "TARGET_VALIDITY_UNRESOLVED"
    summary = (
        "明確成功結果與同側 participants 支持 target 符合 Helping Hand 規則；未宣稱視覺上量測相鄰格。"
        if certainty == "supported"
        else "缺少足夠觀察，無法確認 target validity。"
    )
    knowledge_items = []
    if move:
        knowledge_items.append(_knowledge(kb, move))
    if target_rule:
        knowledge_items.append(_knowledge(kb, target_rule))
    return RuleInterpretation(
        interpretation_id="",
        sequence=0,
        timestamp=min(float(move_fact["timestamp"]), float(outcome_fact["timestamp"])),
        referenced_battle_fact_ids=(str(move_fact["fact_id"]), str(outcome_fact["fact_id"])),
        referenced_fact_relation_ids=(str(relation["fact_relation_id"]),),
        interpretation_type="TARGET_VALIDITY",
        rule_id=str(target_rule["rule_id"] if target_rule else "target_validity.unknown"),
        rule_version=str(target_rule["rule_version"] if target_rule else "0"),
        required_observations=requirements,
        observed_evidence=(
            _observed(move_fact, "move"),
            _observed(outcome_fact, "target_acceptance_outcome"),
        ),
        knowledge_evidence=_unique_knowledge(knowledge_items),
        conclusion={
            "code": code,
            "summary": summary,
            "derived_values": {
                "move_id": move.get("move_id") if move else None,
                "expected_target_class": target_rule.get("expected_target") if target_rule else None,
                "actor": actor.get("observed_name") if actor else None,
                "target": target.get("observed_name") if target else None,
                "observed_result": result,
                "visual_geometry_observed": False,
            },
        },
        certainty=certainty,
        confidence=_confidence((move_fact, outcome_fact), relation),
        unresolved_reason=(
            None if certainty == "supported" else "missing_or_invalid_observations:{}".format(",".join(missing))
        ),
    )


def _ability_fact_for_target(
    facts: Sequence[Mapping[str, Any]],
    target: Optional[Mapping[str, Any]],
    ability_rule: Mapping[str, Any],
    at_or_before: float,
    kb: PokemonRuleKnowledgeBase,
) -> Optional[Mapping[str, Any]]:
    if not target:
        return None
    matches = []
    for fact in facts:
        if fact.get("fact_type") != "ABILITY_ACTIVATED":
            continue
        if float(fact.get("timestamp", 0.0)) > at_or_before:
            continue
        ability = kb.resolve_ability_rule(str(_metadata(fact).get("ability") or ""))
        participant = _participant(fact, "actor") or _participant(fact, "target")
        if ability and ability["ability_id"] == ability_rule["ability_id"] and _same_identity(participant, target):
            matches.append(fact)
    return max(matches, key=lambda row: float(row["timestamp"])) if matches else None


def interpret_ability_immunity(
    move_fact: Mapping[str, Any],
    outcome_fact: Optional[Mapping[str, Any]],
    ability_fact: Optional[Mapping[str, Any]],
    ability_rule: Mapping[str, Any],
    kb: PokemonRuleKnowledgeBase,
    relation: Optional[Mapping[str, Any]] = None,
) -> RuleInterpretation:
    move = kb.resolve_move(str(_metadata(move_fact).get("move") or ""))
    actor = _participant(move_fact, "actor")
    target = _participant(outcome_fact, "target") if outcome_fact else None
    result = _metadata(outcome_fact).get("result") if outcome_fact else None
    applicable = bool(
        move
        and (
            not ability_rule["applies_to_move_types"]
            or move["move_type"] in ability_rule["applies_to_move_types"]
        )
        and move["category"] in ability_rule["applies_to_categories"]
    )
    explicit_failure = result in ability_rule["accepted_failure_results"]
    observed_ability = (
        kb.resolve_ability_rule(str(_metadata(ability_fact).get("ability") or ""))
        if ability_fact
        else None
    )
    ability_participant = (
        _participant(ability_fact, "actor") or _participant(ability_fact, "target")
        if ability_fact
        else None
    )
    ability_matches = bool(
        observed_ability
        and observed_ability["ability_id"] == ability_rule["ability_id"]
    )
    ability_identity_matches = bool(
        ability_matches and _same_identity(ability_participant, target)
    )
    other_source = bool(actor and target and not _same_identity(actor, target))
    other_source_status = (
        "satisfied"
        if not ability_rule["requires_other_pokemon_source"] or other_source
        else ("missing" if not actor or not target else "contradicted")
    )
    requirements = (
        _requirement("move_observed", "招式名稱必須存在於 MOVE_USED", "satisfied" if _metadata(move_fact).get("move") else "missing", str(move_fact["fact_id"]), _metadata(move_fact).get("move")),
        _requirement("move_rule_known", "招式必須存在於 rule knowledge", "satisfied" if move else "missing", str(move_fact["fact_id"]), move.get("move_id") if move else None),
        _requirement("ability_rule_applicable", "招式 type/category 必須符合 ability rule", "satisfied" if applicable else "contradicted", str(move_fact["fact_id"]), applicable),
        _requirement("target_observed", "明確失敗結果必須包含 target", "satisfied" if target else "missing", str(outcome_fact["fact_id"]) if outcome_fact else None, target.get("observed_name") if target else None),
        _requirement("explicit_failure_outcome_observed", "必須觀察 immune/no_effect/failed 結果", "satisfied" if explicit_failure else ("contradicted" if result else "missing"), str(outcome_fact["fact_id"]) if outcome_fact else None, result),
        _requirement("matching_ability_observed", "同一 target 必須有明確 ability observation", "satisfied" if ability_matches else "missing", str(ability_fact["fact_id"]) if ability_fact else None, _metadata(ability_fact).get("ability") if ability_fact else None),
        _requirement("ability_target_identity_matches", "ability observation 的 Pokémon identity 必須與 move target 一致", "satisfied" if ability_identity_matches else ("missing" if not ability_fact or not target else "contradicted"), str(ability_fact["fact_id"]) if ability_fact else None, ability_identity_matches if ability_fact and target else None),
        _requirement("other_pokemon_source_observed", "規則若要求，move source 與 target 必須可觀察為不同 Pokémon", other_source_status, str(move_fact["fact_id"]), other_source if actor and target else None),
    )
    invalid = [row.requirement_id for row in requirements if row.status != "satisfied"]
    certainty = "supported" if not invalid else "unresolved"
    code = "OBSERVED_FAILURE_EXPLAINED_BY_ABILITY_IMMUNITY" if certainty == "supported" else "ABILITY_IMMUNITY_UNRESOLVED"
    knowledge_items = [_knowledge(kb, ability_rule)]
    if move:
        knowledge_items.append(_knowledge(kb, move))
    observed_items = [_observed(move_fact, "move")]
    if outcome_fact:
        observed_items.append(
            _observed(
                outcome_fact,
                "failure_outcome" if explicit_failure else "candidate_outcome",
            )
        )
    if ability_fact:
        observed_items.append(_observed(ability_fact, "target_ability"))
    referenced_facts = tuple(item.fact_id for item in _unique_observed(observed_items))
    return RuleInterpretation(
        interpretation_id="",
        sequence=0,
        timestamp=min(item.timestamp for item in observed_items),
        referenced_battle_fact_ids=referenced_facts,
        referenced_fact_relation_ids=(str(relation["fact_relation_id"]),) if relation else (),
        interpretation_type="ABILITY_IMMUNITY",
        rule_id=str(ability_rule["rule_id"]),
        rule_version=str(ability_rule["rule_version"]),
        required_observations=requirements,
        observed_evidence=_unique_observed(observed_items),
        knowledge_evidence=_unique_knowledge(knowledge_items),
        conclusion={
            "code": code,
            "summary": (
                "觀察到的失敗、target ability 與 rule knowledge 共同支持 ability immunity。"
                if certainty == "supported"
                else "缺少必要的 target、失敗結果或 ability observation；不可宣稱免疫成立。"
            ),
            "derived_values": {
                "move_id": move.get("move_id") if move else None,
                "move_type": move.get("move_type") if move else None,
                "move_category": move.get("category") if move else None,
                "ability_id": ability_rule["ability_id"],
                "target": target.get("observed_name") if target else None,
                "observed_result": result,
            },
        },
        certainty=certainty,
        confidence=_confidence([move_fact] + ([outcome_fact] if outcome_fact else []) + ([ability_fact] if ability_fact else []), relation),
        unresolved_reason=None if certainty == "supported" else "missing_or_invalid_observations:{}".format(",".join(invalid)),
    )


def interpret_explicit_rule(
    outcome_fact: Mapping[str, Any],
    explicit_rule: Mapping[str, Any],
    kb: PokemonRuleKnowledgeBase,
    move_fact: Optional[Mapping[str, Any]] = None,
    relation: Optional[Mapping[str, Any]] = None,
) -> RuleInterpretation:
    metadata = _metadata(outcome_fact)
    target = _participant(outcome_fact, "target")
    requirements = tuple(
        _requirement(
            "metadata:{}".format(key),
            "MOVE_RESOLVED 必須明確觀察 {}={}".format(key, value),
            "satisfied" if metadata.get(key) == value else "contradicted",
            str(outcome_fact["fact_id"]),
            metadata.get(key),
        )
        for key, value in explicit_rule["required_metadata"].items()
    ) + (
        _requirement(
            "target_observed",
            "explicit result 必須包含 target",
            "satisfied" if target else "missing",
            str(outcome_fact["fact_id"]),
            target.get("observed_name") if target else None,
        ),
    )
    invalid = [row.requirement_id for row in requirements if row.status != "satisfied"]
    certainty = "supported" if not invalid else "unresolved"
    observed_items = []
    if move_fact:
        observed_items.append(_observed(move_fact, "move"))
    observed_items.append(_observed(outcome_fact, "explicit_outcome"))
    knowledge_items = [_knowledge(kb, explicit_rule)]
    move = kb.resolve_move(str(metadata.get("move") or ""))
    if move:
        knowledge_items.append(_knowledge(kb, move))
    return RuleInterpretation(
        interpretation_id="",
        sequence=0,
        timestamp=min(item.timestamp for item in observed_items),
        referenced_battle_fact_ids=tuple(item.fact_id for item in observed_items),
        referenced_fact_relation_ids=(str(relation["fact_relation_id"]),) if relation else (),
        interpretation_type="EXPLICIT_RULE_OUTCOME",
        rule_id=str(explicit_rule["rule_id"]),
        rule_version=str(explicit_rule["rule_version"]),
        required_observations=requirements,
        observed_evidence=tuple(observed_items),
        knowledge_evidence=_unique_knowledge(knowledge_items),
        conclusion={
            "code": explicit_rule["conclusion_code"] if certainty == "supported" else "EXPLICIT_RULE_UNRESOLVED",
            "summary": (
                "明確觀察到的 result/effect 符合版本化 explicit rule。"
                if certainty == "supported"
                else "explicit rule 的必要觀察不完整。"
            ),
            "derived_values": {
                "move": metadata.get("move"),
                "target": target.get("observed_name") if target else None,
                "matched_metadata": {
                    key: metadata.get(key)
                    for key in explicit_rule["required_metadata"]
                },
            },
        },
        certainty=certainty,
        confidence=_confidence([row for row in (move_fact, outcome_fact) if row], relation),
        unresolved_reason=None if certainty == "supported" else "missing_or_invalid_observations:{}".format(",".join(invalid)),
    )


def _related_outcomes(
    move_fact: Mapping[str, Any],
    facts_by_id: Mapping[str, Mapping[str, Any]],
    relations: Sequence[Mapping[str, Any]],
) -> List[Tuple[Mapping[str, Any], Mapping[str, Any]]]:
    result = []
    for relation in relations:
        if not relation.get("active") or relation.get("from_fact_id") != move_fact["fact_id"]:
            continue
        outcome = facts_by_id.get(str(relation.get("to_fact_id")))
        if outcome and outcome.get("fact_type") == "MOVE_RESOLVED":
            result.append((outcome, relation))
    return result


def build_rule_interpretations(
    facts: Sequence[Mapping[str, Any]],
    relations: Sequence[Mapping[str, Any]],
    kb: PokemonRuleKnowledgeBase,
) -> List[RuleInterpretation]:
    """從既有 facts 選出最小可驗證案例，不以 fact ID 或 timestamp 寫例外。"""
    facts_by_id = {str(row["fact_id"]): row for row in facts}
    interpretations: List[RuleInterpretation] = []
    for fact in facts:
        if fact.get("fact_type") == "MOVE_USED":
            move = kb.resolve_move(str(_metadata(fact).get("move") or ""))
            if not move:
                continue
            outcomes = _related_outcomes(fact, facts_by_id, relations)
            for outcome, relation in outcomes:
                if _metadata(outcome).get("result") in TYPE_RESULT_CODES:
                    interpretations.append(
                        interpret_type_effectiveness(fact, outcome, relation, kb)
                    )
                target_rule = kb.target_rule(str(move["move_id"]))
                if target_rule and _metadata(outcome).get("result") in target_rule["explicit_success_results"]:
                    interpretations.append(
                        interpret_target_validity(fact, outcome, relation, kb)
                    )

            for ability_rule in kb.payload["ability_rules"]:
                applicable = (
                    (not ability_rule["applies_to_move_types"] or move["move_type"] in ability_rule["applies_to_move_types"])
                    and move["category"] in ability_rule["applies_to_categories"]
                )
                if not applicable:
                    continue
                outcome, relation = outcomes[0] if outcomes else (None, None)
                target = _participant(outcome, "target") if outcome else None
                ability_fact = _ability_fact_for_target(
                    facts,
                    target,
                    ability_rule,
                    float(outcome["timestamp"] if outcome else fact["timestamp"]),
                    kb,
                )
                interpretations.append(
                    interpret_ability_immunity(
                        fact, outcome, ability_fact, ability_rule, kb, relation
                    )
                )

        if fact.get("fact_type") == "MOVE_RESOLVED":
            for explicit_rule in kb.matching_explicit_rules(
                str(fact["fact_type"]), _metadata(fact)
            ):
                incoming = next(
                    (
                        relation
                        for relation in relations
                        if relation.get("active")
                        and relation.get("to_fact_id") == fact["fact_id"]
                        and facts_by_id.get(str(relation.get("from_fact_id")), {}).get("fact_type") == "MOVE_USED"
                    ),
                    None,
                )
                move_fact = facts_by_id.get(str(incoming["from_fact_id"])) if incoming else None
                interpretations.append(
                    interpret_explicit_rule(fact, explicit_rule, kb, move_fact, incoming)
                )

    deduplicated: Dict[Tuple[str, str, Tuple[str, ...]], RuleInterpretation] = {}
    for item in interpretations:
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
            interpretation_id="rule-interpretation-{:04d}".format(index),
            sequence=index,
        )
        for index, item in enumerate(ordered, start=1)
    ]
