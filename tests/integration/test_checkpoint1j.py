import csv
import hashlib
import json
import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from pokemon_battle_vision.checkpoint1j import run_checkpoint_1j
from pokemon_battle_vision.errors import InputError
from pokemon_battle_vision.interpretation_review import canonical_payload_hash
from pokemon_battle_vision.output_transaction import OutputTransaction


ROOT = Path(__file__).resolve().parents[2]
CHECKPOINT1H = ROOT / "outputs/checkpoint-1h"
CHECKPOINT1I = ROOT / "outputs/checkpoint-1i"


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_hashes(path):
    return {
        item.relative_to(path).as_posix(): _sha256(item)
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def _new_output(label="checkpoint-1j-test"):
    return ROOT / "outputs" / "{}-{}".format(label, uuid4().hex)


def _cleanup_output(path):
    if path.exists():
        shutil.rmtree(str(path))
    for pattern in (
        "{}.tmp-*".format(path.name),
        "{}.backup-*".format(path.name),
    ):
        for item in path.parent.glob(pattern):
            if item.is_dir():
                shutil.rmtree(str(item))
    conflict = path.with_name(path.name + " 2")
    if conflict.exists() and conflict.is_dir() and not any(conflict.iterdir()):
        conflict.rmdir()


@pytest.fixture(scope="module")
def generated():
    target = _new_output()
    frozen_before = {
        "checkpoint1h": _tree_hashes(CHECKPOINT1H),
        "checkpoint1i": _tree_hashes(CHECKPOINT1I),
    }
    try:
        manifest = run_checkpoint_1j(
            ROOT,
            CHECKPOINT1H,
            CHECKPOINT1I,
            target,
        )
        yield target, manifest, frozen_before
    finally:
        _cleanup_output(target)


@pytest.mark.integration
def test_real_213_fact_pipeline_builds_18_pending_reviews(generated):
    output, manifest, _ = generated
    assert manifest["status"] == "complete_pending_human_review"
    assert manifest["counts"] == {
        "source_battle_facts": 213,
        "source_fact_relations": 50,
        "existing_interpretations": 8,
        "expanded_interpretations": 10,
        "review_records": 18,
        "accepted_reviews": 0,
        "rejected_reviews": 0,
        "needs_review": 18,
        "deferred_reviews": 0,
        "conflicted_interpretations": 0,
    }
    records = _load(output / "interpretation_review_records.json")["records"]
    assert len(records) == 18
    assert sum(row["interpretation_origin"] == "1I" for row in records) == 8
    assert sum(row["interpretation_origin"] == "1J" for row in records) == 10
    assert all(row["review_status"] == "needs_review" for row in records)
    assert all(row["reviewer"] is None for row in records)
    assert all(row["reviewed_at"] is None for row in records)
    assert all(row["review_reason"] is None for row in records)


@pytest.mark.integration
def test_all_formal_schemas_and_manifest_hashes_validate(generated):
    output, manifest, _ = generated
    manifest_schema = _load(ROOT / "schemas/checkpoint1j_manifest.schema.json")
    Draft202012Validator.check_schema(manifest_schema)
    Draft202012Validator(manifest_schema).validate(manifest)
    for reference in manifest["outputs"].values():
        path = output / reference["path"]
        schema = _load(ROOT / "schemas" / reference["schema"])
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(_load(path))
        assert _sha256(path) == reference["sha256"]
    for reference in (
        manifest["review_pack"]["index"],
        manifest["review_pack"]["worksheet"],
        *manifest["review_pack"]["cards"],
    ):
        path = output / reference["path"]
        assert path.is_file()
        assert _sha256(path) == reference["sha256"]


@pytest.mark.integration
def test_every_review_hash_resolves_to_an_immutable_interpretation(generated):
    output, _, _ = generated
    existing = _load(CHECKPOINT1I / "rule_interpretations.json")["interpretations"]
    expanded = _load(output / "expanded_rule_interpretations.json")["interpretations"]
    by_id = {
        row["interpretation_id"]: row for row in [*existing, *expanded]
    }
    records = _load(output / "interpretation_review_records.json")["records"]
    assert {row["interpretation_id"] for row in records} == set(by_id)
    for record in records:
        interpretation = by_id[record["interpretation_id"]]
        assert record["interpretation_payload_hash"] == canonical_payload_hash(
            interpretation
        )
        assert record["certainty"] == interpretation["certainty"]
        assert "conclusion" not in record


@pytest.mark.integration
def test_review_pack_has_traceable_cards_for_existing_eight(generated):
    output, manifest, _ = generated
    assert manifest["review_pack"]["card_count"] == 18
    records = _load(output / "interpretation_review_records.json")["records"]
    existing_cards = [
        output / row["review_card_path"]
        for row in records
        if row["interpretation_origin"] == "1I"
    ]
    assert len(existing_cards) == 8
    for card in existing_cards:
        text = card.read_text(encoding="utf-8")
        for heading in (
            "Referenced Battle Facts",
            "Referenced Fact Relations",
            "Required Observations",
            "Knowledge Evidence",
            "Derived Conclusion（唯讀）",
            "Human Review",
        ):
            assert heading in text
    worksheet = output / "review_pack/review_worksheet.csv"
    with worksheet.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 18
    assert {row["review_status"] for row in rows} == {"needs_review"}


@pytest.mark.integration
def test_conflict_policy_and_coverage_decisions_are_explicit(generated):
    output, _, _ = generated
    policy = _load(output / "conflict_review_policy.json")
    assert policy["production_conflicted_count"] == 0
    assert policy["production_conflict_review_ids"] == []
    assert policy["categories"] == [
        "observation_error_suspected",
        "identity_resolution_error_suspected",
        "knowledge_data_error_suspected",
        "rule_engine_error_suspected",
        "version_mismatch",
        "insufficient_evidence",
        "unresolved_other",
    ]
    coverage = _load(output / "rule_coverage_audit.json")
    assert coverage["source_battle_fact_count"] == 213
    assert coverage["expanded_interpretation_count"] == 10
    assert len(coverage["adopted"]) == 9
    assert sum(row["decision"] == "rejected" for row in coverage["not_adopted"]) == 6
    assert sum(row["decision"] == "deferred" for row in coverage["not_adopted"]) == 1
    assert all(row["matching_interpretation_ids"] for row in coverage["adopted"])


@pytest.mark.integration
def test_v1_v2_migration_drift_and_direct_hash_audit(generated):
    output, manifest, frozen_before = generated
    v1_data = ROOT / manifest["knowledge"]["v1_data"]["path"]
    v1_manifest = ROOT / manifest["knowledge"]["v1_manifest"]["path"]
    assert _sha256(v1_data) == "ac3cfc8205c6f75ecab20b954346303c28db43e665db36ba653042fdbb0e506d"
    assert _sha256(v1_manifest) == "7ae4ab9a6f8e476e40d4392664a94a1991b00b2a1b6a97ff53fbd0e5e12f2393"
    v2_manifest = _load(ROOT / manifest["knowledge"]["v2_manifest"]["path"])
    assert v2_manifest["migration"]["existing_knowledge_semantics_changed"] is False
    assert v2_manifest["migration"]["interpretation_regeneration"][
        "required_existing_interpretation_ids"
    ] == []

    drift = _load(output / "historical_snapshot_drift_audit.json")
    assert drift["approved_drift_count"] == 7
    assert all(drift["validation"].values())
    audit = _load(output / "checkpoint1j_audit.json")
    assert len(audit["direct_input_hashes"]) >= 50
    assert all(audit["validation"].values())
    assert all(audit["immutability"].values())
    assert not any(audit["scope_guards"].values())
    assert _tree_hashes(CHECKPOINT1H) == frozen_before["checkpoint1h"]
    assert _tree_hashes(CHECKPOINT1I) == frozen_before["checkpoint1i"]


@pytest.mark.integration
def test_deterministic_rerun_reproduces_identical_tree(generated):
    output, _, _ = generated
    before = _tree_hashes(output)
    run_checkpoint_1j(ROOT, CHECKPOINT1H, CHECKPOINT1I, output)
    assert _tree_hashes(output) == before


@pytest.mark.integration
def test_review_csv_can_be_imported_without_rewriting_interpretations():
    output = _new_output("checkpoint-1j-review-import-test")
    try:
        run_checkpoint_1j(ROOT, CHECKPOINT1H, CHECKPOINT1I, output)
        immutable_before = _sha256(output / "expanded_rule_interpretations.json")
        worksheet = output / "review_pack/review_worksheet.csv"
        with worksheet.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            fieldnames = list(reader.fieldnames or [])
        rows[0]["review_status"] = "deferred"
        rows[0]["reviewer"] = "wilson"
        rows[0]["reviewed_at"] = "2026-07-18T01:00:00+08:00"
        rows[0]["review_reason"] = "等待更多可觀察 evidence"
        with worksheet.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        run_checkpoint_1j(
            ROOT,
            CHECKPOINT1H,
            CHECKPOINT1I,
            output,
            review_decisions_path=worksheet,
        )
        records = _load(output / "interpretation_review_records.json")["records"]
        assert records[0]["review_status"] == "deferred"
        assert records[0]["reviewer"] == "wilson"
        assert records[0]["review_reason"] == "等待更多可觀察 evidence"
        assert sum(row["review_status"] == "needs_review" for row in records) == 17
        assert _sha256(output / "expanded_rule_interpretations.json") == immutable_before
    finally:
        _cleanup_output(output)


@pytest.mark.integration
def test_transaction_failure_preserves_previous_output(monkeypatch):
    output = _new_output("checkpoint-1j-rollback-test")
    output.mkdir()
    marker = output / "previous.txt"
    marker.write_text("previous valid output", encoding="utf-8")

    def fail_review_pack(*args, **kwargs):
        raise RuntimeError("fixture review pack failure")

    monkeypatch.setattr(
        "pokemon_battle_vision.checkpoint1j.write_review_pack", fail_review_pack
    )
    try:
        with pytest.raises(RuntimeError, match="fixture review pack failure"):
            run_checkpoint_1j(ROOT, CHECKPOINT1H, CHECKPOINT1I, output)
        assert marker.read_text(encoding="utf-8") == "previous valid output"
        assert not list(output.parent.glob("{}.tmp-*".format(output.name)))
        assert not list(output.parent.glob("{}.backup-*".format(output.name)))
    finally:
        _cleanup_output(output)


@pytest.mark.integration
def test_direct_frozen_payload_hash_mismatch_is_blocking():
    copied_1h = _new_output("checkpoint-1h-tampered-test")
    output = _new_output("checkpoint-1j-tampered-input-test")
    try:
        shutil.copytree(str(CHECKPOINT1H), str(copied_1h))
        facts = copied_1h / "battle_facts.json"
        facts.write_text(facts.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with pytest.raises(InputError, match="hash"):
            run_checkpoint_1j(ROOT, copied_1h, CHECKPOINT1I, output)
        assert not output.exists()
    finally:
        _cleanup_output(copied_1h)
        _cleanup_output(output)


@pytest.mark.integration
def test_missing_inputs_have_clear_error_and_staging_is_visible():
    output = _new_output("checkpoint-1j-missing-input-test")
    transaction = OutputTransaction(ROOT, output)
    assert not transaction.staging_dir.name.startswith(".")
    try:
        with pytest.raises(InputError, match="Checkpoint 1I manifest"):
            run_checkpoint_1j(
                ROOT,
                CHECKPOINT1H,
                ROOT / "outputs/checkpoint-1i-missing",
                output,
            )
        assert not output.exists()
    finally:
        _cleanup_output(output)


@pytest.mark.integration
def test_generated_tree_is_visible_and_has_no_conflict_artifacts(generated):
    output, _, _ = generated
    assert OutputTransaction.hidden_items(output) == []
    assert not list(output.rglob(".DS_Store"))
    assert not list(output.parent.glob("{}.tmp-*".format(output.name)))
    assert not list(output.parent.glob("{}.backup-*".format(output.name)))
    assert not output.with_name(output.name + " 2").exists()
