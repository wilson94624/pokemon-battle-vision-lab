import json
import stat
from pathlib import Path

import jsonschema

from pokemon_battle_vision.output_transaction import OutputTransaction
from pokemon_battle_vision.utils import sha256_file


PROJECT = Path(__file__).resolve().parents[2]
OUTPUT = PROJECT / "outputs/checkpoint-1g"
REVIEW = PROJECT / "outputs/checkpoint-1g-review"


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_formal_checkpoint1g_outputs_are_schema_valid_and_traceable():
    manifest = _json(OUTPUT / "checkpoint1g_manifest.json")
    assert manifest["status"] == "complete"
    for filename, row in manifest["outputs"].items():
        path = OUTPUT / filename
        schema = _json(PROJECT / "schemas" / row["schema"])
        jsonschema.Draft202012Validator(schema).validate(_json(path))
        assert sha256_file(path) == row["sha256"]
    review_manifest = _json(REVIEW / "review_manifest.json")
    schema = _json(PROJECT / "schemas/checkpoint1g_review_manifest.schema.json")
    jsonschema.Draft202012Validator(schema).validate(review_manifest)
    for pages in review_manifest["pages"].values():
        assert all((REVIEW / page).is_file() for page in pages)


def test_formal_coverage_consumes_visual_candidates_and_base_state_once():
    manifest = _json(OUTPUT / "checkpoint1g_manifest.json")
    counts = manifest["counts"]
    assert counts["candidate_counts"]["TEAM_PREVIEW"] == 1
    assert counts["candidate_counts"]["SELECTED_FOUR"] == 1
    assert counts["move_menu_observations"] == 31
    assert counts["hp_raw_samples"] > 0
    assert counts["enriched_snapshots"] == 71
    assert counts["enriched_deltas"] == 71
    cycles = _json(OUTPUT / "decision_cycles.json")["cycles"]
    event_ids = [item for cycle in cycles for item in cycle["battle_event_ids"]]
    assert len(event_ids) == len(set(event_ids)) == 102


def test_formal_apple_vision_runtime_and_knowledge_base_enrichment_are_real():
    audit = _json(OUTPUT / "checkpoint1g_audit.json")
    assert audit["ocr_probe"]["runtime_path_verified"] is True
    assert audit["counts"]["ocr_jobs"] == 1235
    assert audit["counts"]["ocr_runtime_results"] == {"success": 1235}
    assert audit["counts"]["knowledge_base_species"] == 1025
    assert audit["counts"]["knowledge_base_aliases"] == 5039

    roster = _json(OUTPUT / "team_roster.json")["entries"]
    player = [row for row in roster if row["side"] == "player"]
    assert [row["species_id"] for row in player] == [1000, 727, 302, 212, 186, 260]
    assert all(row["species_candidates"] for row in player)

    hp = _json(OUTPUT / "hp_observations.json")["observations"]
    assert any(row["value_type"] == "exact_numeric" for row in hp)
    assert any(row["value_type"] == "ocr_percentage" for row in hp)
    assert any(row.get("species_id") is not None for row in hp)

    menus = _json(OUTPUT / "move_menu_observations.json")["observations"]
    assert len(menus) == 31
    assert all(row["available_moves"] for row in menus)


def test_frozen_source_hashes_still_match_manifest():
    source = _json(OUTPUT / "checkpoint1g_manifest.json")["source"]
    for row in source.values():
        path = PROJECT / row["path"]
        assert sha256_file(path) == row["sha256"]
    assert "knowledge/pokemon/v1/pokemon_knowledge_base.json" in source
    assert "knowledge/pokemon/v1/manifest.json" in source


def test_generated_outputs_are_visible_and_have_no_conflict_artifacts():
    hidden_flag = getattr(stat, "UF_HIDDEN", 0)
    for root in (OUTPUT, REVIEW):
        items = [root, *root.rglob("*")]
        assert not [item for item in items if hidden_flag and item.lstat().st_flags & hidden_flag]
        assert not [item for item in items if item.name == ".DS_Store"]
    outputs_root = PROJECT / "outputs"
    assert not list(outputs_root.glob("checkpoint-1g.tmp-*"))
    assert not list(outputs_root.glob("checkpoint-1g.backup-*"))
    assert not list(outputs_root.glob("checkpoint-1g-review.tmp-*"))
    assert not list(outputs_root.glob("checkpoint-1g-review.backup-*"))
    assert not (outputs_root / "checkpoint-1g 2").exists()
    assert not (outputs_root / "checkpoint-1g-review 2").exists()


def test_transaction_failure_preserves_previous_output(tmp_path, monkeypatch):
    project = tmp_path / "project"
    outputs = project / "outputs"
    outputs.mkdir(parents=True)
    target = outputs / "checkpoint-1g"
    target.mkdir()
    (target / "sentinel.txt").write_text("old", encoding="utf-8")
    transaction = OutputTransaction(project, target)
    assert not transaction.staging_dir.name.startswith(".")
    try:
        with transaction:
            (transaction.staging_dir / "new.txt").write_text("new", encoding="utf-8")
            raise RuntimeError("forced failure")
    except RuntimeError:
        pass
    assert (target / "sentinel.txt").read_text(encoding="utf-8") == "old"
