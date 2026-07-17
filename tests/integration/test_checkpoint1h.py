import json
import stat
from pathlib import Path

import jsonschema
import pytest

from pokemon_battle_vision.approved_drift import ApprovedDriftRegistry
from pokemon_battle_vision.checkpoint1h import run_checkpoint_1h
from pokemon_battle_vision.errors import InputError
from pokemon_battle_vision.output_transaction import OutputTransaction
from pokemon_battle_vision.utils import sha256_file


PROJECT = Path(__file__).resolve().parents[2]
OUTPUT = PROJECT / "outputs/checkpoint-1h"


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_formal_checkpoint1h_outputs_are_schema_valid_and_hash_traceable():
    manifest = _json(OUTPUT / "checkpoint1h_manifest.json")
    assert manifest["status"] == "complete"
    for filename, row in manifest["outputs"].items():
        path = OUTPUT / filename
        schema = _json(PROJECT / "schemas" / row["schema"])
        jsonschema.Draft202012Validator(schema).validate(_json(path))
        assert sha256_file(path) == row["sha256"]
    manifest_schema = _json(PROJECT / "schemas/checkpoint1h_manifest.schema.json")
    jsonschema.Draft202012Validator(manifest_schema).validate(manifest)


def test_formal_facts_are_complete_immutable_and_observation_traceable():
    payload = _json(OUTPUT / "battle_facts.json")
    audit = _json(OUTPUT / "checkpoint1h_audit.json")
    assert payload["fact_count"] == 213
    assert payload["source_counts"] == {
        "battle_events": 102,
        "hp_changes": 103,
        "turn_boundaries": 8,
    }
    assert payload["fact_type_counts"]["MOVE_USED"] == 29
    assert payload["fact_type_counts"]["HP_CHANGED"] == 103
    assert payload["fact_type_counts"]["TURN_BOUNDARY"] == 8
    assert all(row["evidence"] for row in payload["facts"])
    assert len({row["fact_id"] for row in payload["facts"]}) == 213
    assert len(audit["source_mapping"]["battle_event_to_fact"]) == 102
    assert len(audit["source_mapping"]["hp_change_to_fact"]) == 103
    assert len(audit["source_mapping"]["decision_cycle_to_boundary_fact"]) == 8
    assert all(audit["validation"].values())
    assert "regulation_availability" not in json.dumps(payload, ensure_ascii=False)


def test_reviewed_relations_are_preserved_without_promoting_adjacency_to_cause():
    payload = _json(OUTPUT / "battle_fact_relations.json")
    assert payload["relation_count"] == 50
    assert payload["active_relation_count"] == 46
    assert payload["rejected_relation_count"] == 4
    rejected = [row for row in payload["relations"] if not row["active"]]
    assert {row["source_relation_id"] for row in rejected} == {
        "relation-0019",
        "relation-0030",
        "relation-0036",
        "relation-0041",
    }
    assert all(not row["causal_claim"] for row in rejected)
    assert all(
        not row["causal_claim"]
        for row in payload["relations"]
        if row["relation_type"] == "TEMPORALLY_ADJACENT"
    )


def test_turn_reconstruction_never_claims_official_turn_or_selected_move():
    payload = _json(OUTPUT / "reconstructed_turns.json")
    assert payload["turn_candidate_count"] == 8
    assert payload["opening_segment"]["is_official_turn_number"] is False
    assert payload["policy"]["selected_move_inferred_from_menu"] is False
    assert all(row["official_turn_number"] is None for row in payload["turn_candidates"])
    assert all(row["reconstruction_status"] == "ambiguous" for row in payload["turn_candidates"])
    event_facts = payload["opening_segment"]["event_fact_ids"] + [
        fact_id
        for turn in payload["turn_candidates"]
        for fact_id in turn["event_fact_ids"]
    ]
    hp_facts = payload["opening_segment"]["hp_change_fact_ids"] + [
        fact_id
        for turn in payload["turn_candidates"]
        for fact_id in turn["hp_change_fact_ids"]
    ]
    assert len(event_facts) == len(set(event_facts)) == 102
    assert len(hp_facts) == len(set(hp_facts)) == 103


def test_frozen_source_hashes_match_or_have_exact_drift_approval():
    registry = ApprovedDriftRegistry.from_project(PROJECT)
    source = _json(OUTPUT / "checkpoint1h_manifest.json")["source"]
    for row in source.values():
        registry.verify(
            "1H",
            row["path"],
            row["sha256"],
            sha256_file(PROJECT / row["path"]),
        )


def test_generated_outputs_are_visible_and_have_no_conflict_artifacts():
    hidden_flag = getattr(stat, "UF_HIDDEN", 0)
    items = [OUTPUT, *OUTPUT.rglob("*")]
    assert not [
        item for item in items if hidden_flag and item.lstat().st_flags & hidden_flag
    ]
    assert not [item for item in items if item.name == ".DS_Store"]
    outputs_root = PROJECT / "outputs"
    assert not list(outputs_root.glob("checkpoint-1h.tmp-*"))
    assert not list(outputs_root.glob("checkpoint-1h.backup-*"))
    assert not (outputs_root / "checkpoint-1h 2").exists()


def test_missing_frozen_input_has_clear_error(tmp_path):
    with pytest.raises(InputError, match="不存在"):
        run_checkpoint_1h(
            project_root=PROJECT,
            checkpoint1d_dir=tmp_path / "missing-1d",
            checkpoint1e_dir=PROJECT / "outputs/checkpoint-1e",
            checkpoint1e_review_dir=PROJECT / "outputs/checkpoint-1e-review",
            checkpoint1g_dir=PROJECT / "outputs/checkpoint-1g",
            output_dir=PROJECT / "outputs/checkpoint-1h-missing-test",
        )


def test_transaction_failure_preserves_previous_checkpoint1h_output(tmp_path):
    project = tmp_path / "project"
    target = project / "outputs/checkpoint-1h"
    target.mkdir(parents=True)
    (target / "sentinel.txt").write_text("old", encoding="utf-8")
    transaction = OutputTransaction(project, target)
    assert not transaction.staging_dir.name.startswith(".")
    with pytest.raises(RuntimeError, match="forced"):
        with transaction:
            (transaction.staging_dir / "new.txt").write_text("new", encoding="utf-8")
            raise RuntimeError("forced")
    assert (target / "sentinel.txt").read_text(encoding="utf-8") == "old"
