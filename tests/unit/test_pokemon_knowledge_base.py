import json
import stat
from pathlib import Path

from pokemon_battle_vision.pokemon_knowledge_base import PokemonKnowledgeBase


PROJECT = Path(__file__).resolve().parents[2]


def test_versioned_knowledge_base_is_complete_and_schema_valid():
    knowledge = PokemonKnowledgeBase.from_project(PROJECT)
    assert knowledge.payload["counts"] == {
        "species": 1025,
        "forms": 1654,
        "pokeapi_forms": 1579,
        "champions_mega_forms": 75,
        "aliases": 5039,
        "regulation_entries": 235,
        "sprite_metadata": 1351,
    }
    assert all(knowledge.manifest["validation"].values())


def test_traditional_chinese_aliases_resolve_to_canonical_species_top_k():
    knowledge = PokemonKnowledgeBase.from_project(PROJECT)
    expected = {
        "勾魂眼": 302,
        "蚊香蛙皇": 186,
        "烈咬陸鯊": 445,
        "賽富豪": 1000,
    }
    for text, species_id in expected.items():
        candidates = knowledge.resolve_species(text)
        assert candidates[0]["canonical_species_id"] == species_id
        assert candidates[0]["confidence"] == 1.0
        assert "pokeapi.zh-hant" in candidates[0]["source_ids"]


def test_regulation_and_visual_domains_remain_separate():
    knowledge = PokemonKnowledgeBase.from_project(PROJECT)
    garchomp = knowledge.resolve_species("Garchomp")[0]
    assert garchomp["regulation_availability"]["status"] == "eligible"
    sprite = knowledge.sprite_metadata(garchomp["default_pokemon_id"])
    assert set(sprite["domains"]) == {
        "battle_sprite_default",
        "home_artwork",
        "official_artwork",
        "showdown_animation",
        "generation_viii_icon",
        "pokemon_champions_icon",
    }
    assert sprite["domains"]["pokemon_champions_icon"]["available"] is False


def test_knowledge_directory_contains_no_vendored_images_or_vector_assets():
    files = [path for path in (PROJECT / "knowledge/pokemon").rglob("*") if path.is_file()]
    assert files
    assert not [
        path
        for path in files
        if path.suffix.lower() in {".png", ".jpg", ".gif", ".npy", ".npz", ".index"}
    ]
    payload = json.loads(
        (PROJECT / "knowledge/pokemon/v1/manifest.json").read_text(encoding="utf-8")
    )
    assert all(value is False for value in payload["scope_guards"].values())


def test_knowledge_output_is_visible_and_has_no_transaction_artifacts():
    root = PROJECT / "knowledge/pokemon/v1"
    hidden_flag = getattr(stat, "UF_HIDDEN", 0)
    items = [root, *root.rglob("*")]
    assert not [item for item in items if item.name == ".DS_Store"]
    assert not [item for item in items if hidden_flag and item.lstat().st_flags & hidden_flag]
    assert not list(root.parent.glob("v1.tmp-*"))
    assert not list(root.parent.glob("v1.backup-*"))
    assert not (root.parent / "v1 2").exists()
