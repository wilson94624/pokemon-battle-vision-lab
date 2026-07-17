import hashlib
import json
from pathlib import Path

import pytest

from pokemon_battle_vision.errors import InputError
from pokemon_battle_vision.rule_knowledge import (
    PokemonRuleKnowledgeBase,
    normalize_rule_alias,
)


PROJECT = Path(__file__).resolve().parents[2]


def test_versioned_rule_knowledge_is_hash_gated_and_minimal():
    kb = PokemonRuleKnowledgeBase.from_project(PROJECT)
    assert kb.payload["knowledge_version"] == (
        "pokemon-rule-foundation-2026.07.17-v1"
    )
    assert kb.manifest["data"]["sha256"] == kb.data_sha256
    assert len(kb.moves_by_id) == 3
    assert len(kb.ability_rules_by_id) == 2
    assert kb.payload["scope_guards"] == {
        "complete_simulator": False,
        "damage_calculator": False,
        "legality_engine": False,
        "decision_engine": False,
        "battle_facts_created": False,
        "battle_facts_modified": False,
    }


@pytest.mark.parametrize(
    ("alias", "move_id"),
    (("子彈拳", "bullet-punch"), ("Helping Hand", "helping-hand"), ("地震", "earthquake")),
)
def test_move_aliases_resolve_deterministically(alias, move_id):
    kb = PokemonRuleKnowledgeBase.from_project(PROJECT)
    assert kb.resolve_move(alias)["move_id"] == move_id


def test_type_multiplier_handles_dual_type_without_simulation():
    kb = PokemonRuleKnowledgeBase.from_project(PROJECT)
    multiplier, components = kb.type_multiplier("Steel", ["Grass", "Fairy"])
    assert multiplier == 2.0
    assert components == [
        {"attacking_type": "Steel", "defending_type": "Grass", "multiplier": 1.0},
        {"attacking_type": "Steel", "defending_type": "Fairy", "multiplier": 2.0},
    ]


def test_unversioned_type_matchup_is_not_assumed_neutral():
    kb = PokemonRuleKnowledgeBase.from_project(PROJECT)
    with pytest.raises(InputError, match="未版本化"):
        kb.type_multiplier("Fire", ["Grass"])


def test_rule_alias_normalization_is_unicode_and_punctuation_stable():
    assert normalize_rule_alias(" Good-as Gold！ ") == "goodasgold"


def _copied_knowledge(tmp_path):
    source = PROJECT / "knowledge/pokemon/rules/v1"
    data = tmp_path / "rule_knowledge.json"
    manifest = tmp_path / "manifest.json"
    data.write_bytes((source / data.name).read_bytes())
    manifest.write_bytes((source / manifest.name).read_bytes())
    return data, manifest


def test_rule_knowledge_rejects_hash_drift(tmp_path):
    data, manifest = _copied_knowledge(tmp_path)
    data.write_text(data.read_text() + "\n", encoding="utf-8")
    with pytest.raises(InputError, match="hash"):
        PokemonRuleKnowledgeBase(
            data,
            manifest,
            PROJECT / "schemas/pokemon_rule_knowledge.schema.json",
            PROJECT / "schemas/pokemon_rule_knowledge_manifest.schema.json",
        )


def test_rule_knowledge_rejects_duplicate_source_ids_even_with_current_hash(tmp_path):
    data, manifest = _copied_knowledge(tmp_path)
    payload = json.loads(data.read_text(encoding="utf-8"))
    payload["sources"][-1]["source_id"] = payload["sources"][0]["source_id"]
    data.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_payload["data"]["sha256"] = hashlib.sha256(data.read_bytes()).hexdigest()
    manifest.write_text(
        json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(InputError, match="source_ids_unique"):
        PokemonRuleKnowledgeBase(
            data,
            manifest,
            PROJECT / "schemas/pokemon_rule_knowledge.schema.json",
            PROJECT / "schemas/pokemon_rule_knowledge_manifest.schema.json",
        )
