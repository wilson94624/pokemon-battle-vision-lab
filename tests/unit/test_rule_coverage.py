import inspect
import json
from pathlib import Path

from pokemon_battle_vision.rule_coverage import (
    build_expanded_rule_interpretations,
    build_rule_coverage_audit,
)
from pokemon_battle_vision.rule_knowledge import PokemonRuleKnowledgeBase
from pokemon_battle_vision.utils import sha256_file


PROJECT = Path(__file__).resolve().parents[2]


def _source():
    facts = json.loads(
        (PROJECT / "outputs/checkpoint-1h/battle_facts.json").read_text()
    )["facts"]
    relations = json.loads(
        (PROJECT / "outputs/checkpoint-1h/battle_fact_relations.json").read_text()
    )["relations"]
    existing = json.loads(
        (PROJECT / "outputs/checkpoint-1i/rule_interpretations.json").read_text()
    )["interpretations"]
    return facts, relations, existing


def test_v2_is_additive_and_v1_bytes_are_unchanged():
    v1 = PokemonRuleKnowledgeBase.from_version(PROJECT, "v1")
    v2 = PokemonRuleKnowledgeBase.from_version(PROJECT, "v2")
    assert sha256_file(v1.data_path) == (
        "ac3cfc8205c6f75ecab20b954346303c28db43e665db36ba653042fdbb0e506d"
    )
    assert sha256_file(v1.manifest_path) == (
        "7ae4ab9a6f8e476e40d4392664a94a1991b00b2a1b6a97ff53fbd0e5e12f2393"
    )
    assert all(v2.knowledge_by_id[key] == value for key, value in v1.knowledge_by_id.items())
    assert v2.manifest["migration"]["interpretation_regeneration"][
        "required_existing_interpretation_ids"
    ] == []


def test_existing_facts_produce_ten_new_reviewable_interpretations():
    facts, relations, _ = _source()
    kb = PokemonRuleKnowledgeBase.from_version(PROJECT, "v2")
    rows = build_expanded_rule_interpretations(facts, relations, kb)
    assert len(rows) == 10
    assert [row.interpretation_id for row in rows] == [
        "rule-interpretation-1j-{:04d}".format(index)
        for index in range(1, 11)
    ]
    assert all(row.certainty == "supported" for row in rows)
    assert {row.interpretation_type for row in rows} == {
        "STATUS_OUTCOME",
        "MOVE_FAILURE",
        "ABILITY_CONSEQUENCE",
        "FIELD_LIFECYCLE",
        "DAMAGE_CONSEQUENCE",
        "ITEM_CONSEQUENCE",
    }


def test_temporal_adjacency_is_never_promoted_to_causality():
    facts, relations, _ = _source()
    relation_by_id = {row["fact_relation_id"]: row for row in relations}
    kb = PokemonRuleKnowledgeBase.from_version(PROJECT, "v2")
    for row in build_expanded_rule_interpretations(facts, relations, kb):
        values = row.conclusion["derived_values"]
        for relation_id in row.referenced_fact_relation_ids:
            if relation_by_id[relation_id]["relation_type"] == "TEMPORALLY_ADJACENT":
                assert values["causal_claim"] is False
                assert values["relation_semantics"] == "consistency_only"


def test_causal_item_and_recoil_rules_require_identity_continuity():
    facts, relations, _ = _source()
    kb = PokemonRuleKnowledgeBase.from_version(PROJECT, "v2")
    rows = build_expanded_rule_interpretations(facts, relations, kb)
    selected = [
        row
        for row in rows
        if row.interpretation_type in {"ITEM_CONSEQUENCE", "DAMAGE_CONSEQUENCE"}
    ]
    assert len(selected) == 2
    assert all(row.conclusion["derived_values"]["causal_claim"] for row in selected)
    assert all(row.conclusion["derived_values"]["identity_matches"] for row in selected)


def test_coverage_audit_records_adopted_rejected_and_deferred_candidates():
    facts, relations, existing = _source()
    kb = PokemonRuleKnowledgeBase.from_version(PROJECT, "v2")
    rows = build_expanded_rule_interpretations(facts, relations, kb)
    audit = build_rule_coverage_audit(facts, relations, existing, rows)
    assert len(audit["adopted"]) == 9
    assert sum(row["decision"] == "rejected" for row in audit["not_adopted"]) == 6
    assert sum(row["decision"] == "deferred" for row in audit["not_adopted"]) == 1
    assert "complete_type_chart" in {row["candidate"] for row in audit["not_adopted"]}


def test_production_selector_has_no_fact_id_or_timestamp_exceptions():
    source = inspect.getsource(build_expanded_rule_interpretations)
    assert "battle-fact-" not in source
    assert "timestamp ==" not in source
