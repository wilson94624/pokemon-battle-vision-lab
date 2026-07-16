"""Checkpoint 1F orchestration：由 reviewed Timeline 建立保守 Battle State。"""

from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from jsonschema import Draft202012Validator

from .battle_state_models import STATE_VERSION
from .battle_state_projector import project_battle_state
from .battle_state_review import build_state_review_pack
from .config import load_json
from .errors import InputError
from .output_transaction import OutputTransaction, finalize_generated_output
from .utils import project_relative, sha256_file, write_json


PROJECTOR_VERSION = "0.1.0"


def _validator(project_root: Path, name: str) -> Draft202012Validator:
    schema = load_json(project_root / "schemas" / name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise InputError("Checkpoint 1F {} 不存在：{}".format(label, path))


def _validate_file_ref(base: Path, reference: Mapping[str, Any], label: str) -> Path:
    path = (base / str(reference["path"])).resolve()
    _require_file(path, label)
    if sha256_file(path) != reference["sha256"]:
        raise InputError("Checkpoint 1F {} hash 與 manifest 不一致".format(label))
    return path


def _validate_inputs(
    project_root: Path,
    events_path: Path,
    timeline_path: Path,
    relations_path: Path,
    timeline_review_dir: Path,
) -> Dict[str, Any]:
    events_manifest_path = events_path.parent / "checkpoint1d_manifest.json"
    timeline_manifest_path = timeline_path.parent / "checkpoint1e_manifest.json"
    review_manifest_path = timeline_review_dir / "review_manifest.json"
    for path, label in (
        (events_path, "Checkpoint 1D events"),
        (events_manifest_path, "Checkpoint 1D manifest"),
        (timeline_path, "Checkpoint 1E timeline"),
        (relations_path, "Checkpoint 1E relations"),
        (timeline_manifest_path, "Checkpoint 1E manifest"),
        (review_manifest_path, "Checkpoint 1E review manifest"),
    ):
        _require_file(path, label)

    events = load_json(events_path)
    events_manifest = load_json(events_manifest_path)
    timeline = load_json(timeline_path)
    relations = load_json(relations_path)
    timeline_manifest = load_json(timeline_manifest_path)
    review_manifest = load_json(review_manifest_path)
    for schema_name, payload in (
        ("battle_event.schema.json", events),
        ("checkpoint1d_manifest.schema.json", events_manifest),
        ("battle_timeline.schema.json", timeline),
        ("timeline_relations.schema.json", relations),
        ("checkpoint1e_manifest.schema.json", timeline_manifest),
        ("checkpoint1e_review_manifest.schema.json", review_manifest),
    ):
        _validator(project_root, schema_name).validate(payload)

    if sha256_file(events_path) != events_manifest["output"]["sha256"]:
        raise InputError("Checkpoint 1D events hash gate 失敗")
    if sha256_file(events_path) != timeline_manifest["source"]["events_sha256"]:
        raise InputError("Checkpoint 1E source events hash gate 失敗")
    if sha256_file(events_manifest_path) != timeline_manifest["source"]["manifest_sha256"]:
        raise InputError("Checkpoint 1E source manifest hash gate 失敗")
    if sha256_file(timeline_path) != timeline_manifest["outputs"]["battle_timeline"]["sha256"]:
        raise InputError("Checkpoint 1E timeline hash gate 失敗")
    if sha256_file(relations_path) != timeline_manifest["outputs"]["timeline_relations"]["sha256"]:
        raise InputError("Checkpoint 1E relations hash gate 失敗")
    if sha256_file(review_manifest_path) != timeline_manifest["outputs"]["review_manifest"]["sha256"]:
        raise InputError("Checkpoint 1E review manifest hash gate 失敗")

    review_files = {}
    for key in (
        "needs_review_relations",
        "unlinked_events",
        "review_summary",
        "review_statistics",
    ):
        if key not in review_manifest["outputs"]:
            raise InputError("Checkpoint 1E review manifest 缺少 {}".format(key))
        path = _validate_file_ref(
            timeline_review_dir,
            review_manifest["outputs"][key],
            "review {}".format(key),
        )
        review_files[key] = load_json(path)

    relation_reviews = review_files["needs_review_relations"]
    unlinked_reviews = review_files["unlinked_events"]
    review_statistics = review_files["review_statistics"]
    unresolved_relations = [
        item["relation_id"]
        for item in relation_reviews["records"]
        if item.get("human_decision") not in {"accepted", "rejected"}
    ]
    unresolved_unlinked = [
        item["timeline_id"]
        for item in unlinked_reviews["records"]
        if item.get("human_decision") != "accepted_unlinked"
        or item.get("human_action") != "keep_unlinked"
    ]
    derived_accepted = sum(
        item.get("human_decision") == "accepted"
        for item in relation_reviews["records"]
    )
    derived_rejected = sum(
        item.get("human_decision") == "rejected"
        for item in relation_reviews["records"]
    )
    if unresolved_relations:
        raise InputError("Checkpoint 1E 尚有 needs_review relation：{}".format(unresolved_relations))
    if unresolved_unlinked:
        raise InputError("Checkpoint 1E 尚有未審查 unlinked group：{}".format(unresolved_unlinked))
    if not review_statistics.get("human_review_complete"):
        raise InputError("Checkpoint 1E Human Review completion gate 失敗")
    expected_counts = (
        review_statistics.get("accepted_relation_count"),
        review_statistics.get("rejected_relation_count"),
        review_statistics.get("accepted_unlinked_group_count"),
        review_statistics.get("remaining_needs_review_relation_count"),
        review_statistics.get("remaining_unreviewed_unlinked_group_count"),
    )
    derived_counts = (
        derived_accepted,
        derived_rejected,
        len(unlinked_reviews["records"]),
        len(unresolved_relations),
        len(unresolved_unlinked),
    )
    if expected_counts != derived_counts:
        raise InputError(
            "Checkpoint 1E Human Review statistics 不一致：{} != {}".format(
                expected_counts, derived_counts
            )
        )
    if int(timeline["timeline_count"]) != len(timeline["groups"]):
        raise InputError("Checkpoint 1E timeline_count 不一致")
    if int(relations["relation_count"]) != len(relations["relations"]):
        raise InputError("Checkpoint 1E relation_count 不一致")
    if timeline["all_source_event_ids"] != [event["id"] for event in events["events"]]:
        raise InputError("Checkpoint 1E source event order 與 Checkpoint 1D 不一致")

    tracked_paths = [
        events_path,
        events_manifest_path,
        timeline_path,
        relations_path,
        timeline_manifest_path,
        review_manifest_path,
    ] + [
        timeline_review_dir / review_manifest["outputs"][key]["path"]
        for key in review_files
    ]
    return {
        "events": events,
        "timeline": timeline,
        "relations": relations,
        "relation_reviews": relation_reviews,
        "unlinked_reviews": unlinked_reviews,
        "events_manifest_path": events_manifest_path,
        "timeline_manifest_path": timeline_manifest_path,
        "review_manifest_path": review_manifest_path,
        "tracked_hashes": {str(path.resolve()): sha256_file(path) for path in tracked_paths},
        "review_counts": {
            "accepted_relations": derived_accepted,
            "rejected_relations": derived_rejected,
            "accepted_unlinked": len(unlinked_reviews["records"]),
        },
    }


def _validate_unchanged(tracked_hashes: Mapping[str, str]) -> bool:
    return all(
        Path(path).is_file() and sha256_file(Path(path)) == expected
        for path, expected in tracked_hashes.items()
    )


def run_checkpoint_1f(
    project_root: Path,
    events_path: Path,
    timeline_path: Path,
    relations_path: Path,
    timeline_review_dir: Path,
    output_dir: Path,
    review_output_dir: Path,
) -> Dict[str, Any]:
    project_root = project_root.resolve()
    events_path = events_path.resolve()
    timeline_path = timeline_path.resolve()
    relations_path = relations_path.resolve()
    timeline_review_dir = timeline_review_dir.resolve()
    output_dir = output_dir.resolve()
    review_output_dir = review_output_dir.resolve()
    if output_dir == review_output_dir:
        raise InputError("Checkpoint 1F output 與 review-output 不可相同")

    source = _validate_inputs(
        project_root,
        events_path,
        timeline_path,
        relations_path,
        timeline_review_dir,
    )
    projection = project_battle_state(
        source["events"],
        source["timeline"],
        source["relations"],
        source["relation_reviews"],
        source["unlinked_reviews"],
    )
    snapshots_payload = {
        "schema_version": STATE_VERSION,
        "checkpoint": "1F",
        "kind": "battle_state_snapshots",
        "state_version": STATE_VERSION,
        "snapshot_count": len(projection["snapshots"]),
        "projected_group_count": len(projection["deltas"]),
        "snapshots": projection["snapshots"],
    }
    deltas_payload = {
        "schema_version": STATE_VERSION,
        "checkpoint": "1F",
        "kind": "state_deltas",
        "delta_count": len(projection["deltas"]),
        "deltas": projection["deltas"],
    }
    conflicts_payload = {
        "schema_version": STATE_VERSION,
        "checkpoint": "1F",
        "kind": "state_conflicts",
        "conflict_count": len(projection["conflicts"]),
        "conflicts": projection["conflicts"],
    }
    audit_payload = projection["audit"]

    with OutputTransaction(project_root, output_dir) as output_transaction:
        with OutputTransaction(project_root, review_output_dir) as review_transaction:
            output_staging = output_transaction.staging_dir
            review_staging = review_transaction.staging_dir
            output_files = {
                "battle_state_snapshots": (
                    "battle_state_snapshots.json",
                    snapshots_payload,
                    "battle_state_snapshots.schema.json",
                ),
                "state_deltas": (
                    "state_deltas.json",
                    deltas_payload,
                    "state_deltas.schema.json",
                ),
                "state_conflicts": (
                    "state_conflicts.json",
                    conflicts_payload,
                    "state_conflicts.schema.json",
                ),
                "state_audit": (
                    "state_audit.json",
                    audit_payload,
                    "state_audit.schema.json",
                ),
            }
            for _, (relative, payload, schema_name) in output_files.items():
                _validator(project_root, schema_name).validate(payload)
                write_json(output_staging / relative, payload)

            review_manifest = build_state_review_pack(
                review_staging,
                projection["snapshots"],
                projection["deltas"],
                projection["conflicts"],
                source["events"]["events"],
                sha256_file(output_staging / "battle_state_snapshots.json"),
                sha256_file(output_staging / "state_deltas.json"),
            )
            if not all(review_manifest["validation"].values()):
                raise InputError(
                    "Checkpoint 1F Review Pack validation 失敗：{}".format(
                        review_manifest["validation"]
                    )
                )
            _validator(
                project_root, "state_review_records.schema.json"
            ).validate(load_json(review_staging / "state_review_records.json"))
            _validator(
                project_root, "checkpoint1f_review_manifest.schema.json"
            ).validate(review_manifest)
            write_json(review_staging / "review_manifest.json", review_manifest)

            frozen_unchanged = _validate_unchanged(source["tracked_hashes"])
            validation = {
                **audit_payload["validation"],
                "checkpoint1d_hash_gate_passed": True,
                "checkpoint1e_hash_gate_passed": True,
                "human_review_completion_gate_passed": True,
                "schemas_valid": True,
                "review_pack_valid": all(review_manifest["validation"].values()),
                "frozen_inputs_unchanged": frozen_unchanged,
                "paired_output_transaction": True,
                "deterministic_metadata": True,
            }
            if not all(validation.values()):
                raise InputError("Checkpoint 1F final validation 失敗：{}".format(validation))
            outputs = {
                key: {"path": relative, "sha256": sha256_file(output_staging / relative)}
                for key, (relative, _, _) in output_files.items()
            }
            outputs["review_manifest"] = {
                "path": "../checkpoint-1f-review/review_manifest.json",
                "sha256": sha256_file(review_staging / "review_manifest.json"),
            }
            manifest = {
                "schema_version": STATE_VERSION,
                "checkpoint": "1F",
                "kind": "checkpoint1f_manifest",
                "status": "complete",
                "projector_version": PROJECTOR_VERSION,
                "source": {
                    "events_path": project_relative(events_path, project_root),
                    "events_sha256": sha256_file(events_path),
                    "events_manifest_sha256": sha256_file(source["events_manifest_path"]),
                    "timeline_path": project_relative(timeline_path, project_root),
                    "timeline_sha256": sha256_file(timeline_path),
                    "relations_path": project_relative(relations_path, project_root),
                    "relations_sha256": sha256_file(relations_path),
                    "timeline_manifest_sha256": sha256_file(
                        source["timeline_manifest_path"]
                    ),
                    "timeline_review_path": project_relative(
                        timeline_review_dir, project_root
                    ),
                    "timeline_review_manifest_sha256": sha256_file(
                        source["review_manifest_path"]
                    ),
                    "human_review_counts": source["review_counts"],
                },
                "snapshot_count": len(projection["snapshots"]),
                "projected_group_count": len(projection["deltas"]),
                "delta_count": len(projection["deltas"]),
                "conflict_count": len(projection["conflicts"]),
                "unresolved_update_count": len(projection["unresolved_updates"]),
                "outputs": outputs,
                "content_digests": {
                    "snapshots": outputs["battle_state_snapshots"]["sha256"],
                    "deltas": outputs["state_deltas"]["sha256"],
                    "conflicts": outputs["state_conflicts"]["sha256"],
                    "review_metadata": outputs["review_manifest"]["sha256"],
                },
                "validation": validation,
                "scope_guards": {
                    "ocr_rerun": False,
                    "video_scanned": False,
                    "parser_rerun": False,
                    "timeline_builder_rerun": False,
                    "checkpoint1e_human_review_modified": False,
                    "llm_used": False,
                    "hp_reconstructed": False,
                    "turn_inferred": False,
                    "speed_order_inferred": False,
                    "move_choice_reconstructed": False,
                    "damage_calculation_performed": False,
                    "rule_checker_created": False,
                    "replay_analysis_performed": False,
                    "gui_created": False,
                },
            }
            _validator(project_root, "checkpoint1f_manifest.schema.json").validate(manifest)
            write_json(output_staging / "checkpoint1f_manifest.json", manifest)
            if not _validate_unchanged(source["tracked_hashes"]):
                raise InputError("Checkpoint 1D／1E frozen input 在 projection 期間被修改")
            OutputTransaction.commit_group((output_transaction, review_transaction))

    finalize_generated_output(review_output_dir)
    finalize_generated_output(output_dir)
    if not _validate_unchanged(source["tracked_hashes"]):
        raise InputError("Checkpoint 1D／1E frozen input 在 projection 完成後被修改")
    return manifest
