import copy
from dataclasses import replace
from pathlib import Path

from pokemon_battle_vision.checkpoint1i import _validate_interpretations
from pokemon_battle_vision.rule_interpretation import (
    build_rule_interpretations,
    interpret_ability_immunity,
    interpret_type_effectiveness,
)
from pokemon_battle_vision.rule_knowledge import PokemonRuleKnowledgeBase


PROJECT = Path(__file__).resolve().parents[2]


def _participant(role, name, side, entity_id, species_id=None):
    return {
        "role": role,
        "participant_kind": "pokemon",
        "observed_name": name,
        "side": side,
        "entity_id": entity_id,
        "canonical_species_id": species_id,
        "canonical_name": name,
    }


def _fact(fact_id, fact_type, timestamp, metadata, participants, confidence=0.95):
    return {
        "fact_id": fact_id,
        "fact_type": fact_type,
        "timestamp": timestamp,
        "confidence": confidence,
        "attributes": {"parsed_metadata": metadata},
        "participants": participants,
        "evidence": [{"record_id": "evidence-{}".format(fact_id)}],
    }


def _relation(from_fact, to_fact, relation_id="battle-fact-relation-9001"):
    return {
        "fact_relation_id": relation_id,
        "from_fact_id": from_fact["fact_id"],
        "to_fact_id": to_fact["fact_id"],
        "active": True,
        "confidence": 0.9,
    }


def test_ground_failure_with_observed_levitate_resolves():
    kb = PokemonRuleKnowledgeBase.from_project(PROJECT)
    move = _fact(
        "battle-fact-9001",
        "MOVE_USED",
        10.0,
        {"move": "地震"},
        [_participant("actor", "烈咬陸鯊", "opponent", "actor", 445)],
    )
    outcome = _fact(
        "battle-fact-9002",
        "MOVE_RESOLVED",
        10.5,
        {"result": "immune", "target": "洛托姆"},
        [_participant("target", "洛托姆", "player", "target", 479)],
    )
    ability = _fact(
        "battle-fact-9003",
        "ABILITY_ACTIVATED",
        9.5,
        {"ability": "飄浮"},
        [_participant("actor", "洛托姆", "player", "target", 479)],
    )
    row = interpret_ability_immunity(
        move,
        outcome,
        ability,
        kb.ability_rule("levitate"),
        kb,
        _relation(move, outcome),
    )
    assert row.certainty == "supported"
    assert row.conclusion["code"] == (
        "OBSERVED_FAILURE_EXPLAINED_BY_ABILITY_IMMUNITY"
    )
    assert all(item.status == "satisfied" for item in row.required_observations)


def test_helping_hand_failure_with_observed_good_as_gold_resolves():
    kb = PokemonRuleKnowledgeBase.from_project(PROJECT)
    move = _fact(
        "battle-fact-9011",
        "MOVE_USED",
        20.0,
        {"move": "幫助"},
        [_participant("actor", "風妖精", "player", "actor", 547)],
    )
    outcome = _fact(
        "battle-fact-9012",
        "MOVE_RESOLVED",
        20.5,
        {"result": "failed", "target": "賽富豪"},
        [_participant("target", "賽富豪", "player", "target", 1000)],
    )
    ability = _fact(
        "battle-fact-9013",
        "ABILITY_ACTIVATED",
        19.0,
        {"ability": "黃金之軀"},
        [_participant("actor", "賽富豪", "player", "target", 1000)],
    )
    row = interpret_ability_immunity(
        move,
        outcome,
        ability,
        kb.ability_rule("good-as-gold"),
        kb,
        _relation(move, outcome),
    )
    assert row.certainty == "supported"
    assert row.conclusion["derived_values"]["ability_id"] == "good-as-gold"


def test_missing_target_outcome_and_ability_remain_unresolved():
    kb = PokemonRuleKnowledgeBase.from_project(PROJECT)
    move = _fact(
        "battle-fact-9021",
        "MOVE_USED",
        30.0,
        {"move": "地震"},
        [_participant("actor", "烈咬陸鯊", "opponent", "actor", 445)],
    )
    row = interpret_ability_immunity(
        move, None, None, kb.ability_rule("levitate"), kb
    )
    assert row.certainty == "unresolved"
    assert "target_observed" in row.unresolved_reason
    assert "matching_ability_observed" in row.unresolved_reason


def test_missing_move_name_cannot_be_filled_by_rule_knowledge():
    kb = PokemonRuleKnowledgeBase.from_project(PROJECT)
    move = _fact(
        "battle-fact-9022",
        "MOVE_USED",
        31.0,
        {},
        [_participant("actor", "烈咬陸鯊", "opponent", "actor", 445)],
    )
    row = interpret_ability_immunity(
        move, None, None, kb.ability_rule("levitate"), kb
    )
    assert row.certainty == "unresolved"
    assert "move_observed" in row.unresolved_reason
    assert "move_rule_known" in row.unresolved_reason


def test_ability_identity_mismatch_cannot_resolve_immunity():
    kb = PokemonRuleKnowledgeBase.from_project(PROJECT)
    move = _fact(
        "battle-fact-9031",
        "MOVE_USED",
        40.0,
        {"move": "地震"},
        [_participant("actor", "烈咬陸鯊", "opponent", "actor", 445)],
    )
    outcome = _fact(
        "battle-fact-9032",
        "MOVE_RESOLVED",
        40.5,
        {"result": "immune"},
        [_participant("target", "洛托姆", "player", "target", 479)],
    )
    wrong_identity = _fact(
        "battle-fact-9033",
        "ABILITY_ACTIVATED",
        39.0,
        {"ability": "飄浮"},
        [_participant("actor", "克雷色利亞", "player", "other", 488)],
    )
    row = interpret_ability_immunity(
        move,
        outcome,
        wrong_identity,
        kb.ability_rule("levitate"),
        kb,
        _relation(move, outcome),
    )
    assert row.certainty == "unresolved"
    assert "ability_target_identity_matches" in row.unresolved_reason


def test_type_effectiveness_uses_observed_target_and_active_relation():
    kb = PokemonRuleKnowledgeBase.from_project(PROJECT)
    move = _fact(
        "battle-fact-9041",
        "MOVE_USED",
        50.0,
        {"move": "子彈拳"},
        [_participant("actor", "巨鉗螳螂", "player", "actor", 212)],
    )
    outcome = _fact(
        "battle-fact-9042",
        "MOVE_RESOLVED",
        50.5,
        {"result": "super_effective"},
        [_participant("target", "風妖精", "opponent", "target", 547)],
    )
    row = interpret_type_effectiveness(move, outcome, _relation(move, outcome), kb)
    assert row.certainty == "supported"
    assert row.conclusion["derived_values"]["type_multiplier"] == 2.0
    assert row.conclusion["derived_values"]["target_types"] == ["Grass", "Fairy"]


def test_type_effectiveness_conflict_keeps_complete_observation_evidence():
    kb = PokemonRuleKnowledgeBase.from_project(PROJECT)
    move = _fact(
        "battle-fact-9051",
        "MOVE_USED",
        52.0,
        {"move": "子彈拳"},
        [_participant("actor", "巨鉗螳螂", "player", "actor", 212)],
    )
    outcome = _fact(
        "battle-fact-9052",
        "MOVE_RESOLVED",
        52.5,
        {"result": "not_very_effective"},
        [_participant("target", "仙子伊布", "opponent", "target", 700)],
    )
    relation = _relation(move, outcome, "battle-fact-relation-9051")
    row = replace(
        interpret_type_effectiveness(move, outcome, relation, kb),
        interpretation_id="rule-interpretation-0001",
        sequence=1,
    )
    assert row.certainty == "conflicted"
    assert all(item.status == "satisfied" for item in row.required_observations)
    validation = _validate_interpretations(
        [row],
        [move, outcome],
        [relation],
        list(kb.knowledge_by_id),
        kb.payload["knowledge_version"],
        kb.data_sha256,
    )
    assert all(validation.values())


def test_incomplete_minimal_type_chart_keeps_outcome_unresolved():
    kb = PokemonRuleKnowledgeBase.from_project(PROJECT)
    move = _fact(
        "battle-fact-9043",
        "MOVE_USED",
        51.0,
        {"move": "地震"},
        [_participant("actor", "烈咬陸鯊", "opponent", "actor", 445)],
    )
    outcome = _fact(
        "battle-fact-9044",
        "MOVE_RESOLVED",
        51.5,
        {"result": "super_effective"},
        [_participant("target", "仙子伊布", "player", "target", 700)],
    )
    row = interpret_type_effectiveness(move, outcome, _relation(move, outcome), kb)
    assert row.certainty == "unresolved"
    assert "type_matchup_entries_known" in row.unresolved_reason


def test_formal_selection_is_deterministic_and_does_not_mutate_inputs():
    import json

    kb = PokemonRuleKnowledgeBase.from_project(PROJECT)
    facts = json.loads(
        (PROJECT / "outputs/checkpoint-1h/battle_facts.json").read_text()
    )["facts"]
    relations = json.loads(
        (PROJECT / "outputs/checkpoint-1h/battle_fact_relations.json").read_text()
    )["relations"]
    facts_before = copy.deepcopy(facts)
    relations_before = copy.deepcopy(relations)
    first = [row.to_dict() for row in build_rule_interpretations(facts, relations, kb)]
    second = [row.to_dict() for row in build_rule_interpretations(facts, relations, kb)]
    assert first == second
    assert facts == facts_before
    assert relations == relations_before
    assert len(first) == 8
    assert sum(row["certainty"] == "supported" for row in first) == 6
    assert sum(row["certainty"] == "unresolved" for row in first) == 2
