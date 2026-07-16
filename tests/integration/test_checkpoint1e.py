import hashlib
import json
import shutil
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from pokemon_battle_vision.checkpoint1e import run_checkpoint_1e
from pokemon_battle_vision.errors import InputError
from pokemon_battle_vision.output_transaction import OutputTransaction


ROOT = Path(__file__).resolve().parents[2]


def _copy_real_checkpoint1d(tmp_path):
    project = tmp_path / "project"
    shutil.copytree(ROOT / "schemas", project / "schemas")
    shutil.copytree(ROOT / "outputs/checkpoint-1d", project / "outputs/checkpoint-1d")
    return project


def _tree_hashes(path):
    return {
        item.relative_to(path).as_posix(): hashlib.sha256(item.read_bytes()).hexdigest()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def _validate(project, schema_name, payload_path):
    schema = json.loads((project / "schemas" / schema_name).read_text(encoding="utf-8"))
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(payload)


@pytest.mark.integration
def test_real_102_event_pipeline_and_review_pack_are_complete_and_deterministic(tmp_path):
    project = _copy_real_checkpoint1d(tmp_path)
    events_path = project / "outputs/checkpoint-1d/battle_events.json"
    manifest_path = project / "outputs/checkpoint-1d/checkpoint1d_manifest.json"
    source_hashes = (_tree_hashes(events_path.parent),)
    output = project / "outputs/checkpoint-1e"
    review = project / "outputs/checkpoint-1e-review"

    manifest = run_checkpoint_1e(project, events_path, output, review)
    assert manifest["source_event_count"] == 102
    assert manifest["timeline_count"] == 70
    assert manifest["relation_count"] == 50
    assert manifest["group_status_counts"] == {
        "auto_accepted": 34,
        "needs_review": 34,
        "unlinked": 2,
    }
    assert manifest["relation_status_counts"] == {
        "auto_accepted": 32,
        "needs_review": 18,
    }
    assert manifest["unlinked_event_count"] == 2
    assert _tree_hashes(events_path.parent) == source_hashes[0]

    timeline = json.loads((output / "battle_timeline.json").read_text(encoding="utf-8"))
    relations = json.loads((output / "timeline_relations.json").read_text(encoding="utf-8"))
    consumed = [event_id for group in timeline["groups"] for event_id in group["event_ids"]]
    assert len(consumed) == len(set(consumed)) == 102
    assert set(consumed) == set(timeline["all_source_event_ids"])
    assert "\"turn\"" not in json.dumps(timeline, ensure_ascii=False)
    assert all(row["rule_id"] and row["evidence"] for row in relations["relations"])

    review_manifest = json.loads((review / "review_manifest.json").read_text(encoding="utf-8"))
    assert review_manifest["group_review_count"] == 70
    assert review_manifest["needs_review_relation_count"] == 18
    assert review_manifest["unlinked_event_count"] == 2
    assert review_manifest["contact_sheets"] == {
        "group_page_count": 6,
        "needs_review_page_count": 2,
        "total_page_count": 8,
        "tile_count": 88,
    }
    assert len(list((review / "cards/groups").glob("*.jpg"))) == 70
    assert len(list((review / "cards/relations").glob("*.jpg"))) == 18

    _validate(project, "battle_timeline.schema.json", output / "battle_timeline.json")
    _validate(project, "timeline_relations.schema.json", output / "timeline_relations.json")
    _validate(project, "timeline_audit.schema.json", output / "timeline_audit.json")
    _validate(project, "checkpoint1e_manifest.schema.json", output / "checkpoint1e_manifest.json")
    _validate(project, "checkpoint1e_review_manifest.schema.json", review / "review_manifest.json")

    first_output_hashes = _tree_hashes(output)
    first_review_hashes = _tree_hashes(review)
    run_checkpoint_1e(project, events_path, output, review)
    assert _tree_hashes(output) == first_output_hashes
    assert _tree_hashes(review) == first_review_hashes
    assert OutputTransaction.hidden_items(output) == []
    assert OutputTransaction.hidden_items(review) == []
    assert not list((project / "outputs").glob("checkpoint-1e.tmp-*"))
    assert not list((project / "outputs").glob("checkpoint-1e.backup-*"))
    assert not list((project / "outputs").glob("checkpoint-1e-review.tmp-*"))
    assert not list((project / "outputs").glob("checkpoint-1e-review.backup-*"))
    assert not (project / "outputs/checkpoint-1e 2").exists()
    assert not (project / "outputs/checkpoint-1e-review 2").exists()
    assert not list(project.rglob(".DS_Store"))


@pytest.mark.integration
def test_transaction_failure_preserves_previous_outputs(tmp_path, monkeypatch):
    project = _copy_real_checkpoint1d(tmp_path)
    events_path = project / "outputs/checkpoint-1d/battle_events.json"
    output = project / "outputs/checkpoint-1e"
    review = project / "outputs/checkpoint-1e-review"
    output.mkdir()
    review.mkdir()
    (output / "previous.txt").write_text("previous output", encoding="utf-8")
    (review / "previous.txt").write_text("previous review", encoding="utf-8")

    def fail_review(*args, **kwargs):
        raise RuntimeError("fixture failure")

    monkeypatch.setattr(
        "pokemon_battle_vision.checkpoint1e.build_timeline_review_pack", fail_review
    )
    with pytest.raises(RuntimeError, match="fixture failure"):
        run_checkpoint_1e(project, events_path, output, review)
    assert (output / "previous.txt").read_text(encoding="utf-8") == "previous output"
    assert (review / "previous.txt").read_text(encoding="utf-8") == "previous review"
    assert not list((project / "outputs").glob("checkpoint-1e*.tmp-*"))
    assert not list((project / "outputs").glob("checkpoint-1e*.backup-*"))


@pytest.mark.integration
def test_paired_commit_failure_rolls_back_both_outputs(tmp_path, monkeypatch):
    project = _copy_real_checkpoint1d(tmp_path)
    events_path = project / "outputs/checkpoint-1d/battle_events.json"
    output = project / "outputs/checkpoint-1e"
    review = project / "outputs/checkpoint-1e-review"
    output.mkdir()
    review.mkdir()
    (output / "previous.txt").write_text("previous output", encoding="utf-8")
    (review / "previous.txt").write_text("previous review", encoding="utf-8")
    original = OutputTransaction.validate_no_hidden_flags.__func__

    def fail_after_review_swap(cls, path):
        if path == review:
            raise InputError("fixture commit failure")
        return original(cls, path)

    monkeypatch.setattr(
        OutputTransaction,
        "validate_no_hidden_flags",
        classmethod(fail_after_review_swap),
    )
    with pytest.raises(InputError, match="fixture commit failure"):
        run_checkpoint_1e(project, events_path, output, review)
    assert (output / "previous.txt").read_text(encoding="utf-8") == "previous output"
    assert (review / "previous.txt").read_text(encoding="utf-8") == "previous review"
    assert not list((project / "outputs").glob("checkpoint-1e*.tmp-*"))
    assert not list((project / "outputs").glob("checkpoint-1e*.backup-*"))


def test_output_transaction_staging_directory_is_visible(tmp_path):
    project = tmp_path / "project"
    (project / "outputs").mkdir(parents=True)
    transaction = OutputTransaction(project, project / "outputs/checkpoint-1e")
    assert not transaction.staging_dir.name.startswith(".")


@pytest.mark.integration
def test_checkpoint1d_hash_mismatch_is_rejected_before_output(tmp_path):
    project = _copy_real_checkpoint1d(tmp_path)
    events_path = project / "outputs/checkpoint-1d/battle_events.json"
    events_path.write_text(events_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(InputError, match="events hash"):
        run_checkpoint_1e(
            project,
            events_path,
            project / "outputs/checkpoint-1e",
            project / "outputs/checkpoint-1e-review",
        )
    assert not (project / "outputs/checkpoint-1e").exists()
    assert not (project / "outputs/checkpoint-1e-review").exists()
