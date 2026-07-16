import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]
REVIEW = ROOT / "outputs/checkpoint-1f-review"
OUTPUT = ROOT / "outputs/checkpoint-1f"


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _validate(schema_name, payload_path):
    schema = _load(ROOT / "schemas" / schema_name)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(_load(payload_path))


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_checkpoint1f_human_review_is_complete():
    records = _load(REVIEW / "state_review_records.json")["records"]
    needs_review = [row for row in records if row["review_status"] == "needs_review"]
    assert len(records) == 70
    assert len(needs_review) == 46
    assert all(row["human_review"]["human_decision"] == "accepted" for row in needs_review)
    assert all(
        row["human_review"]["human_action"] == "accept_projection"
        for row in needs_review
    )
    assert all(row["human_review"]["reviewed_by"] == "wilson" for row in needs_review)
    assert not [
        row
        for row in needs_review
        if row["human_review"]["human_decision"] != "accepted"
    ]


def test_checkpoint1f_review_special_decisions_are_preserved():
    records = {
        row["timeline_id"]: row
        for row in _load(REVIEW / "state_review_records.json")["records"]
    }
    assert records["timeline-0018"]["human_review"]["human_notes"] == (
        "超級姆克鷹具有唱反調，近身戰原本造成的防禦與特防下降會反轉為提高，"
        "因此此 State Delta 正確。"
    )
    for timeline_id in ("timeline-0056", "timeline-0063"):
        assert records[timeline_id]["delta_status"] == "unresolved"
        assert records[timeline_id]["human_review"]["human_decision"] == "accepted"
        assert "不重新建立到 timeline-0054" in records[timeline_id]["human_review"][
            "human_notes"
        ]


def test_checkpoint1f_review_schemas_and_statistics_validate():
    for schema_name, payload_path in (
        ("state_review_records.schema.json", REVIEW / "state_review_records.json"),
        ("checkpoint1f_review_summary.schema.json", REVIEW / "review_summary.json"),
        (
            "checkpoint1f_review_statistics.schema.json",
            REVIEW / "review_statistics.json",
        ),
        ("checkpoint1f_review_manifest.schema.json", REVIEW / "review_manifest.json"),
        ("checkpoint1f_manifest.schema.json", OUTPUT / "checkpoint1f_manifest.json"),
    ):
        _validate(schema_name, payload_path)
    statistics = _load(REVIEW / "review_statistics.json")
    assert statistics["human_review_complete"] is True
    assert statistics["manual_accepted_count"] == 46
    assert statistics["remaining_needs_review_count"] == 0


def test_checkpoint1f_review_manifest_hashes_are_current():
    manifest = _load(REVIEW / "review_manifest.json")
    for reference in manifest["outputs"].values():
        path = REVIEW / reference["path"]
        assert path.is_file()
        assert _sha256(path) == reference["sha256"]
    main_manifest = _load(OUTPUT / "checkpoint1f_manifest.json")
    review_reference = main_manifest["outputs"]["review_manifest"]
    assert _sha256(REVIEW / "review_manifest.json") == review_reference["sha256"]


def test_checkpoint1f_projection_content_remains_frozen():
    manifest = _load(OUTPUT / "checkpoint1f_manifest.json")
    for key in (
        "battle_state_snapshots",
        "state_deltas",
        "state_conflicts",
        "state_audit",
    ):
        reference = manifest["outputs"][key]
        assert _sha256(OUTPUT / reference["path"]) == reference["sha256"]
    assert not list(REVIEW.rglob(".DS_Store"))
