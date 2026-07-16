import hashlib
import json
import shutil
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from pokemon_battle_vision.checkpoint1f import run_checkpoint_1f
from pokemon_battle_vision.errors import InputError
from pokemon_battle_vision.output_transaction import OutputTransaction


ROOT = Path(__file__).resolve().parents[2]


def _copy_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _copy_real_inputs(tmp_path):
    project = tmp_path / "project"
    shutil.copytree(ROOT / "schemas", project / "schemas")
    for relative in (
        "outputs/checkpoint-1d/battle_events.json",
        "outputs/checkpoint-1d/checkpoint1d_manifest.json",
        "outputs/checkpoint-1e/battle_timeline.json",
        "outputs/checkpoint-1e/timeline_relations.json",
        "outputs/checkpoint-1e/timeline_audit.json",
        "outputs/checkpoint-1e/checkpoint1e_manifest.json",
        "outputs/checkpoint-1e-review/review_manifest.json",
        "outputs/checkpoint-1e-review/needs_review_relations.json",
        "outputs/checkpoint-1e-review/unlinked_events.json",
        "outputs/checkpoint-1e-review/review_summary.json",
        "outputs/checkpoint-1e-review/review_statistics.json",
    ):
        _copy_file(ROOT / relative, project / relative)
    return project


def _args(project):
    return (
        project,
        project / "outputs/checkpoint-1d/battle_events.json",
        project / "outputs/checkpoint-1e/battle_timeline.json",
        project / "outputs/checkpoint-1e/timeline_relations.json",
        project / "outputs/checkpoint-1e-review",
        project / "outputs/checkpoint-1f",
        project / "outputs/checkpoint-1f-review",
    )


def _tree_hashes(path):
    return {
        item.relative_to(path).as_posix(): hashlib.sha256(item.read_bytes()).hexdigest()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def _input_hashes(project):
    result = {}
    for directory in (
        project / "outputs/checkpoint-1d",
        project / "outputs/checkpoint-1e",
        project / "outputs/checkpoint-1e-review",
    ):
        result.update(
            {
                "{}/{}".format(directory.name, key): value
                for key, value in _tree_hashes(directory).items()
            }
        )
    return result


def _validate(project, schema_name, payload_path):
    schema = json.loads((project / "schemas" / schema_name).read_text(encoding="utf-8"))
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(payload)


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    project = _copy_real_inputs(tmp_path_factory.mktemp("checkpoint1f-real"))
    source_hashes = _input_hashes(project)
    manifest = run_checkpoint_1f(*_args(project))
    return project, manifest, source_hashes


@pytest.mark.integration
def test_real_70_group_projection_is_complete(generated):
    project, manifest, source_hashes = generated
    assert manifest["snapshot_count"] == 71
    assert manifest["projected_group_count"] == 70
    assert manifest["delta_count"] == 70
    assert manifest["conflict_count"] == 0
    assert manifest["unresolved_update_count"] == 2
    assert _input_hashes(project) == source_hashes


@pytest.mark.integration
def test_real_projection_has_no_missing_or_duplicate_events(generated):
    project, _, _ = generated
    audit = json.loads((project / "outputs/checkpoint-1f/state_audit.json").read_text())
    rows = audit["event_processing"]
    assert len(rows) == 102
    assert len({row["event_id"] for row in rows}) == 102
    assert all(audit["validation"].values())


@pytest.mark.integration
def test_real_projection_schemas_validate(generated):
    project, _, _ = generated
    output = project / "outputs/checkpoint-1f"
    review = project / "outputs/checkpoint-1f-review"
    for schema, path in (
        ("battle_state_snapshots.schema.json", output / "battle_state_snapshots.json"),
        ("state_deltas.schema.json", output / "state_deltas.json"),
        ("state_conflicts.schema.json", output / "state_conflicts.json"),
        ("state_audit.schema.json", output / "state_audit.json"),
        ("checkpoint1f_manifest.schema.json", output / "checkpoint1f_manifest.json"),
        ("checkpoint1f_review_manifest.schema.json", review / "review_manifest.json"),
        ("state_review_records.schema.json", review / "state_review_records.json"),
    ):
        _validate(project, schema, path)


@pytest.mark.integration
def test_manifest_hashes_are_traceable(generated):
    project, manifest, _ = generated
    output = project / "outputs/checkpoint-1f"
    review = project / "outputs/checkpoint-1f-review"
    for key, reference in manifest["outputs"].items():
        base = review if key == "review_manifest" else output
        path = (base / reference["path"]).resolve()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == reference["sha256"]


@pytest.mark.integration
def test_review_pack_smoke_has_70_cards_and_indexes(generated):
    project, _, _ = generated
    review = project / "outputs/checkpoint-1f-review"
    manifest = json.loads((review / "review_manifest.json").read_text())
    assert manifest["card_count"] == 70
    assert len(list((review / "snapshot_review_cards").rglob("*.png"))) == 70
    assert manifest["indexes"]["unresolved_update_count"] == 2
    assert manifest["indexes"]["accepted_unlinked_event_count"] == 2
    assert manifest["contact_sheets"] == {
        "all_page_count": 6,
        "needs_review_page_count": 4,
        "total_page_count": 10,
        "cards_per_page": 12,
    }


@pytest.mark.integration
def test_generated_human_review_fields_are_null(generated):
    project, _, _ = generated
    records = json.loads(
        (project / "outputs/checkpoint-1f-review/state_review_records.json").read_text()
    )["records"]
    assert len(records) == 70
    assert all(all(value is None for value in row["human_review"].values()) for row in records)


@pytest.mark.integration
def test_rejected_relations_and_unlinked_policy(generated):
    project, _, _ = generated
    audit = json.loads((project / "outputs/checkpoint-1f/state_audit.json").read_text())
    assert audit["relation_policy"]["rejected_relation_ids"] == [
        "relation-0019",
        "relation-0030",
        "relation-0036",
        "relation-0041",
    ]
    assert audit["accepted_unlinked_timeline_ids"] == ["timeline-0056", "timeline-0063"]


@pytest.mark.integration
def test_deterministic_rerun_has_identical_trees(generated):
    project, _, _ = generated
    output = project / "outputs/checkpoint-1f"
    review = project / "outputs/checkpoint-1f-review"
    first_output = _tree_hashes(output)
    first_review = _tree_hashes(review)
    run_checkpoint_1f(*_args(project))
    assert _tree_hashes(output) == first_output
    assert _tree_hashes(review) == first_review


@pytest.mark.integration
def test_generated_outputs_are_visible_and_clean(generated):
    project, _, _ = generated
    output = project / "outputs/checkpoint-1f"
    review = project / "outputs/checkpoint-1f-review"
    assert OutputTransaction.hidden_items(output) == []
    assert OutputTransaction.hidden_items(review) == []
    assert not list(output.rglob(".DS_Store"))
    assert not list(review.rglob(".DS_Store"))
    assert not list((project / "outputs").glob("checkpoint-1f*.tmp-*"))
    assert not list((project / "outputs").glob("checkpoint-1f*.backup-*"))
    assert not (project / "outputs/checkpoint-1f 2").exists()
    assert not (project / "outputs/checkpoint-1f-review 2").exists()


def test_staging_directory_is_not_dot_prefixed(tmp_path):
    project = tmp_path / "project"
    (project / "outputs").mkdir(parents=True)
    transaction = OutputTransaction(project, project / "outputs/checkpoint-1f")
    assert not transaction.staging_dir.name.startswith(".")


@pytest.mark.integration
def test_checkpoint1d_hash_gate(tmp_path):
    project = _copy_real_inputs(tmp_path)
    path = project / "outputs/checkpoint-1d/battle_events.json"
    path.write_text(path.read_text() + "\n", encoding="utf-8")
    with pytest.raises(InputError, match="Checkpoint 1D events hash gate"):
        run_checkpoint_1f(*_args(project))


@pytest.mark.integration
def test_checkpoint1e_hash_gate(tmp_path):
    project = _copy_real_inputs(tmp_path)
    path = project / "outputs/checkpoint-1e/battle_timeline.json"
    path.write_text(path.read_text() + "\n", encoding="utf-8")
    with pytest.raises(InputError, match="timeline hash gate"):
        run_checkpoint_1f(*_args(project))


@pytest.mark.integration
def test_human_review_completion_gate(tmp_path):
    project = _copy_real_inputs(tmp_path)
    review_dir = project / "outputs/checkpoint-1e-review"
    relations_path = review_dir / "needs_review_relations.json"
    relations = json.loads(relations_path.read_text())
    relations["records"][0]["human_decision"] = None
    relations_path.write_text(json.dumps(relations, ensure_ascii=False, indent=2) + "\n")
    review_manifest_path = review_dir / "review_manifest.json"
    review_manifest = json.loads(review_manifest_path.read_text())
    review_manifest["outputs"]["needs_review_relations"]["sha256"] = hashlib.sha256(
        relations_path.read_bytes()
    ).hexdigest()
    review_manifest_path.write_text(
        json.dumps(review_manifest, ensure_ascii=False, indent=2) + "\n"
    )
    timeline_manifest_path = project / "outputs/checkpoint-1e/checkpoint1e_manifest.json"
    timeline_manifest = json.loads(timeline_manifest_path.read_text())
    timeline_manifest["outputs"]["review_manifest"]["sha256"] = hashlib.sha256(
        review_manifest_path.read_bytes()
    ).hexdigest()
    timeline_manifest_path.write_text(
        json.dumps(timeline_manifest, ensure_ascii=False, indent=2) + "\n"
    )
    with pytest.raises(InputError, match="needs_review relation"):
        run_checkpoint_1f(*_args(project))


@pytest.mark.integration
def test_generation_failure_preserves_previous_outputs(tmp_path, monkeypatch):
    project = _copy_real_inputs(tmp_path)
    output = project / "outputs/checkpoint-1f"
    review = project / "outputs/checkpoint-1f-review"
    output.mkdir()
    review.mkdir()
    (output / "previous.txt").write_text("previous state")
    (review / "previous.txt").write_text("previous review")

    def fail_review(*args, **kwargs):
        raise RuntimeError("fixture review failure")

    monkeypatch.setattr(
        "pokemon_battle_vision.checkpoint1f.build_state_review_pack", fail_review
    )
    with pytest.raises(RuntimeError, match="fixture review failure"):
        run_checkpoint_1f(*_args(project))
    assert (output / "previous.txt").read_text() == "previous state"
    assert (review / "previous.txt").read_text() == "previous review"


@pytest.mark.integration
def test_paired_commit_failure_rolls_back_both_outputs(tmp_path, monkeypatch):
    project = _copy_real_inputs(tmp_path)
    output = project / "outputs/checkpoint-1f"
    review = project / "outputs/checkpoint-1f-review"
    output.mkdir()
    review.mkdir()
    (output / "previous.txt").write_text("previous state")
    (review / "previous.txt").write_text("previous review")
    original = OutputTransaction.validate_no_hidden_flags.__func__

    def fail_after_review_swap(cls, path):
        if path == review:
            raise InputError("fixture paired failure")
        return original(cls, path)

    monkeypatch.setattr(
        OutputTransaction,
        "validate_no_hidden_flags",
        classmethod(fail_after_review_swap),
    )
    with pytest.raises(InputError, match="fixture paired failure"):
        run_checkpoint_1f(*_args(project))
    assert (output / "previous.txt").read_text() == "previous state"
    assert (review / "previous.txt").read_text() == "previous review"
    assert not list((project / "outputs").glob("checkpoint-1f*.tmp-*"))
    assert not list((project / "outputs").glob("checkpoint-1f*.backup-*"))
