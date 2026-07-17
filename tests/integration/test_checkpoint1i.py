import hashlib
import json
import shutil
import stat
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from pokemon_battle_vision.checkpoint1i import run_checkpoint_1i
from pokemon_battle_vision.errors import InputError
from pokemon_battle_vision.utils import sha256_file


PROJECT = Path(__file__).resolve().parents[2]
OUTPUT = PROJECT / "outputs/checkpoint-1i"
SOURCE = PROJECT / "outputs/checkpoint-1h"


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _tree_hashes(path):
    return {
        row.relative_to(path).as_posix(): hashlib.sha256(row.read_bytes()).hexdigest()
        for row in sorted(path.rglob("*"))
        if row.is_file()
    }


def _copy_project(tmp_path):
    project = tmp_path / "project"
    shutil.copytree(PROJECT / "schemas", project / "schemas")
    shutil.copytree(SOURCE, project / "outputs/checkpoint-1h")
    shutil.copytree(
        PROJECT / "knowledge/pokemon/rules", project / "knowledge/pokemon/rules"
    )
    return project


def test_formal_outputs_are_schema_valid_and_hash_traceable():
    manifest = _json(OUTPUT / "checkpoint1i_manifest.json")
    schema = _json(PROJECT / "schemas/checkpoint1i_manifest.schema.json")
    Draft202012Validator(schema).validate(manifest)
    for row in manifest["outputs"].values():
        path = OUTPUT / row["path"]
        output_schema = _json(PROJECT / "schemas" / row["schema"])
        Draft202012Validator(output_schema).validate(_json(path))
        assert sha256_file(path) == row["sha256"]


def test_formal_result_set_is_small_deterministic_and_reviewable():
    payload = _json(OUTPUT / "rule_interpretations.json")
    review = _json(OUTPUT / "interpretation_review.json")
    assert payload["source_battle_fact_count"] == 213
    assert payload["interpretation_count"] == 8
    assert payload["certainty_counts"] == {
        "supported": 6,
        "unresolved": 2,
        "conflicted": 0,
    }
    assert review["record_count"] == 8
    assert [row["interpretation_id"] for row in payload["interpretations"]] == [
        "rule-interpretation-{:04d}".format(index) for index in range(1, 9)
    ]
    assert [row["sequence"] for row in payload["interpretations"]] == list(
        range(1, 9)
    )
    assert all(row["review_questions"] for row in review["records"])


def test_observation_knowledge_and_conclusion_are_separate_and_traceable():
    payload = _json(OUTPUT / "rule_interpretations.json")
    source_facts = {
        row["fact_id"]
        for row in _json(SOURCE / "battle_facts.json")["facts"]
    }
    source_relations = {
        row["fact_relation_id"]
        for row in _json(SOURCE / "battle_fact_relations.json")["relations"]
    }
    for row in payload["interpretations"]:
        assert row["observed_evidence"]
        assert row["knowledge_evidence"]
        assert row["conclusion"]["code"]
        assert set(row["referenced_battle_fact_ids"]) <= source_facts
        assert set(row["referenced_fact_relation_ids"]) <= source_relations
        assert {
            evidence["fact_id"] for evidence in row["observed_evidence"]
        } <= set(row["referenced_battle_fact_ids"])
        assert all(
            evidence["knowledge_path"].startswith("knowledge/pokemon/rules/")
            for evidence in row["knowledge_evidence"]
        )


def test_known_formal_rules_resolve_without_fabricating_missing_abilities():
    rows = _json(OUTPUT / "rule_interpretations.json")["interpretations"]
    supported = [row for row in rows if row["certainty"] == "supported"]
    unresolved = [row for row in rows if row["certainty"] == "unresolved"]
    assert sum(row["interpretation_type"] == "TYPE_EFFECTIVENESS" for row in supported) == 2
    assert sum(row["interpretation_type"] == "TARGET_VALIDITY" for row in supported) == 1
    assert sum(row["interpretation_type"] == "EXPLICIT_RULE_OUTCOME" for row in supported) == 3
    assert {row["rule_id"] for row in unresolved} == {
        "ability_immunity.good_as_gold_status.v1",
        "ability_immunity.levitate_ground.v1",
    }
    assert all(row["unresolved_reason"] for row in unresolved)
    good_as_gold = next(
        row
        for row in unresolved
        if row["rule_id"] == "ability_immunity.good_as_gold_status.v1"
    )
    assert "candidate_outcome" in {
        evidence["observation_role"]
        for evidence in good_as_gold["observed_evidence"]
    }
    assert "failure_outcome" not in {
        evidence["observation_role"]
        for evidence in good_as_gold["observed_evidence"]
    }
    assert not [
        evidence
        for row in unresolved
        for evidence in row["observed_evidence"]
        if evidence["observation_role"] == "target_ability"
    ]
    target_validity = next(
        row for row in supported if row["interpretation_type"] == "TARGET_VALIDITY"
    )
    assert target_validity["conclusion"]["derived_values"][
        "visual_geometry_observed"
    ] is False
    disable_rows = [
        row
        for row in supported
        if row["rule_id"] == "explicit_failure.disable_prevents_move.v1"
    ]
    assert len(disable_rows) == 2
    assert all(not row["referenced_fact_relation_ids"] for row in disable_rows)


def test_formal_audit_proves_scope_guards_and_direct_source_hashes():
    audit = _json(OUTPUT / "checkpoint1i_audit.json")
    manifest = _json(OUTPUT / "checkpoint1i_manifest.json")
    assert all(audit["validation"].values())
    assert all(value is False for value in audit["scope_guards"].values())
    assert all(value is False for value in manifest["scope_guards"].values())
    for relative, expected in audit["direct_input_hashes"].items():
        assert sha256_file(PROJECT / relative) == expected
    assert audit["upstream_source_snapshot_drift"]["policy"] == (
        "informational_only_direct_frozen_checkpoint1h_outputs_are_authoritative"
    )
    drift = audit["upstream_source_snapshot_drift"]
    assert drift["record_count"] == len(drift["records"])
    source_by_path = {
        row["path"]: row
        for row in _json(SOURCE / "checkpoint1h_manifest.json")["source"].values()
    }
    for row in drift["records"]:
        assert row["checkpoint1h_snapshot_sha256"] == source_by_path[row["path"]][
            "sha256"
        ]
        # current_sha256 是 1I 產生當下的 informational snapshot；後續核准的
        # 1E review metadata 變更不可回頭使 frozen 1I audit 失效。
        assert row["current_sha256"] is None or len(row["current_sha256"]) == 64
        assert row["current_sha256"] != row["checkpoint1h_snapshot_sha256"]


def test_checkpoint1h_battle_facts_and_relations_remain_unchanged():
    manifest = _json(OUTPUT / "checkpoint1i_manifest.json")
    for key in ("battle_facts", "battle_fact_relations"):
        row = manifest["source"][key]
        assert sha256_file(PROJECT / row["path"]) == row["sha256"]
    assert _json(SOURCE / "battle_facts.json")["fact_count"] == 213
    assert _json(SOURCE / "battle_fact_relations.json")["relation_count"] == 50


def test_generated_output_is_visible_and_has_no_conflict_artifacts():
    hidden_flag = getattr(stat, "UF_HIDDEN", 0)
    assert not [
        row
        for row in [OUTPUT, *OUTPUT.rglob("*")]
        if hidden_flag and row.lstat().st_flags & hidden_flag
    ]
    assert not list(OUTPUT.rglob(".DS_Store"))
    assert not list((PROJECT / "outputs").glob("checkpoint-1i.tmp-*"))
    assert not list((PROJECT / "outputs").glob("checkpoint-1i.backup-*"))
    assert not (PROJECT / "outputs/checkpoint-1i 2").exists()


def test_missing_checkpoint1h_input_has_clear_error(tmp_path):
    with pytest.raises(InputError, match="不存在"):
        run_checkpoint_1i(
            PROJECT,
            tmp_path / "missing-checkpoint-1h",
            PROJECT / "outputs/checkpoint-1i-missing-test",
        )


def test_tampered_direct_checkpoint1h_output_fails_hash_gate(tmp_path):
    project = _copy_project(tmp_path)
    facts_path = project / "outputs/checkpoint-1h/battle_facts.json"
    facts_path.write_text(facts_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(InputError, match="direct 1H output hash"):
        run_checkpoint_1i(
            project,
            project / "outputs/checkpoint-1h",
            project / "outputs/checkpoint-1i",
        )


def test_transaction_failure_preserves_previous_output(tmp_path, monkeypatch):
    project = _copy_project(tmp_path)
    output = project / "outputs/checkpoint-1i"
    output.mkdir()
    (output / "sentinel.txt").write_text("previous", encoding="utf-8")
    from pokemon_battle_vision import checkpoint1i as module

    original = module.write_json

    def fail_on_review(path, payload):
        if path.name == "interpretation_review.json":
            raise RuntimeError("forced interpretation review failure")
        original(path, payload)

    monkeypatch.setattr(module, "write_json", fail_on_review)
    with pytest.raises(RuntimeError, match="forced interpretation review failure"):
        run_checkpoint_1i(
            project,
            project / "outputs/checkpoint-1h",
            output,
        )
    assert (output / "sentinel.txt").read_text(encoding="utf-8") == "previous"
    assert _tree_hashes(output) == {
        "sentinel.txt": hashlib.sha256(b"previous").hexdigest()
    }


def test_rerun_in_isolated_project_is_byte_deterministic(tmp_path):
    project = _copy_project(tmp_path)
    output = project / "outputs/checkpoint-1i"
    run_checkpoint_1i(project, project / "outputs/checkpoint-1h", output)
    first = _tree_hashes(output)
    run_checkpoint_1i(project, project / "outputs/checkpoint-1h", output)
    assert _tree_hashes(output) == first
