"""Milestone 2A read-only replay discovery tests."""

import hashlib
import json
from pathlib import Path

from battle_vision_product.adapters import (
    FilesystemArtifactReader,
    FilesystemEngineGateway,
    FilesystemReplayCatalog,
)
from battle_vision_product.application import ReplayDiscoveryService
from battle_vision_product.domain import CheckpointState, ReplayLayout, ReplayStatus


CHECKPOINTS = tuple("1{}".format(letter) for letter in "ABCDEFGHIJ")


def _descriptor_name(checkpoint: str) -> str:
    normalized = checkpoint.lower()
    if normalized in {"1a", "1b"}:
        return "checkpoint_{}_report.json".format(normalized)
    return "checkpoint{}_manifest.json".format(normalized)


def _write_checkpoint(
    workspace: Path,
    checkpoint: str,
    status: str = "complete",
    counts=None,
) -> Path:
    directory = workspace / "checkpoint-{}".format(checkpoint.lower())
    directory.mkdir(parents=True, exist_ok=True)
    descriptor = directory / _descriptor_name(checkpoint)
    payload = {
        "checkpoint": checkpoint,
        "status": status,
    }
    if counts is not None:
        payload["counts"] = counts
    descriptor.write_text(json.dumps(payload), encoding="utf-8")
    return descriptor


def _write_complete_replay(workspace: Path, counts=None) -> None:
    for checkpoint in CHECKPOINTS:
        _write_checkpoint(workspace, checkpoint, counts=counts)


def _service(project_root: Path) -> ReplayDiscoveryService:
    reader = FilesystemArtifactReader()
    return ReplayDiscoveryService(
        catalog=FilesystemReplayCatalog(project_root),
        engine_gateway=FilesystemEngineGateway(reader),
    )


def _tree_fingerprint(root: Path):
    rows = {}
    for path in sorted((row for row in root.rglob("*") if row.is_file())):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        stat = path.stat()
        rows[path.relative_to(root).as_posix()] = (
            digest,
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_mode,
        )
    return rows


def test_win01_and_official02_are_discovered_with_distinct_identities(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    _write_complete_replay(outputs)
    official = outputs / "replays" / "official-02"
    _write_checkpoint(official, "1A", status="complete_pending_roi_approval")

    inspections = _service(tmp_path).discover()
    by_id = {row.replay_id: row for row in inspections}

    assert tuple(sorted(by_id)) == ("official-02", "win-01")
    assert by_id["win-01"].layout is ReplayLayout.LEGACY
    assert by_id["win-01"].status is ReplayStatus.READY
    assert by_id["official-02"].layout is ReplayLayout.NAMESPACED
    assert by_id["official-02"].status is ReplayStatus.NEEDS_REVIEW
    assert by_id["official-02"].checkpoint_ids == ("1A",)


def test_product_inspection_does_not_modify_engine_artifacts(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    _write_complete_replay(outputs, counts={"event_candidates": 17})
    official = outputs / "replays" / "official-02"
    _write_checkpoint(official, "1A", status="complete_pending_roi_approval")
    before = _tree_fingerprint(outputs)

    _service(tmp_path).discover()

    assert _tree_fingerprint(outputs) == before


def test_missing_checkpoint_artifacts_return_incomplete_status(tmp_path: Path) -> None:
    incomplete = tmp_path / "outputs" / "replays" / "partial-03"
    (incomplete / "inputs").mkdir(parents=True)

    inspection = _service(tmp_path).discover()[0]

    assert inspection.replay_id == "partial-03"
    assert inspection.status is ReplayStatus.INCOMPLETE
    assert inspection.checkpoints == ()
    assert inspection.issues == ("尚未發現任何 checkpoint artifact",)


def test_missing_descriptor_and_invalid_json_are_explicit_not_crashes(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs" / "replays"
    missing = outputs / "missing-report"
    (missing / "checkpoint-1a").mkdir(parents=True)
    invalid = outputs / "invalid-report"
    invalid_descriptor = invalid / "checkpoint-1a" / "checkpoint_1a_report.json"
    invalid_descriptor.parent.mkdir(parents=True)
    invalid_descriptor.write_text("{not-json", encoding="utf-8")

    by_id = {row.replay_id: row for row in _service(tmp_path).discover()}

    assert by_id["missing-report"].status is ReplayStatus.INCOMPLETE
    assert by_id["missing-report"].checkpoints[0].state is CheckpointState.INCOMPLETE
    assert "缺少 checkpoint descriptor" in by_id["missing-report"].issues[0]
    assert by_id["invalid-report"].status is ReplayStatus.INVALID
    assert by_id["invalid-report"].checkpoints[0].state is CheckpointState.INVALID
    assert "無法讀取 JSON artifact" in by_id["invalid-report"].issues[0]


def test_status_does_not_depend_on_replay_specific_counts(tmp_path: Path) -> None:
    outputs = tmp_path / "outputs"
    _write_complete_replay(outputs, counts={"event_candidates": 1, "source_frames": 2})
    other = outputs / "replays" / "arbitrary-counts"
    _write_complete_replay(
        other,
        counts={"event_candidates": 999_999, "source_frames": 123_456_789},
    )

    inspections = _service(tmp_path).discover()

    assert {row.status for row in inspections} == {ReplayStatus.READY}


def test_approved_roi_artifact_completes_the_existing_1a_gate(tmp_path: Path) -> None:
    workspace = tmp_path / "outputs" / "replays" / "approved-1a"
    _write_checkpoint(workspace, "1A", status="complete_pending_roi_approval")
    approval = workspace / "checkpoint-1a" / "roi_approval.json"
    approval.write_text(json.dumps({"status": "approved"}), encoding="utf-8")

    inspection = _service(tmp_path).discover()[0]

    assert inspection.status is ReplayStatus.INCOMPLETE
    assert inspection.checkpoints[0].state is CheckpointState.COMPLETE
    assert inspection.checkpoints[0].evidence_paths[-1] == approval
    assert "缺少完成證據" in inspection.issues[-1]
