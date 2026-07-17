"""Checkpoint 1E orchestration：從 frozen BattleEvent 建立可審查 Timeline。"""

from collections import Counter
from pathlib import Path
from typing import Any, Dict, Mapping

from jsonschema import Draft202012Validator

from .battle_timeline_audit import build_timeline_audit
from .battle_timeline_builder import build_battle_timeline, timeline_counts
from .battle_timeline_models import TIMELINE_SCHEMA_VERSION
from .battle_timeline_review import build_timeline_review_pack
from .config import load_json
from .errors import InputError
from .output_transaction import OutputTransaction, finalize_generated_output
from .utils import project_relative, sha256_file, write_json


BUILDER_VERSION = "0.1.0"


def _schema_validator(project_root: Path, name: str) -> Draft202012Validator:
    schema = load_json(project_root / "schemas" / name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _validate_checkpoint1d_source(
    project_root: Path, events_path: Path
) -> Dict[str, Any]:
    manifest_path = events_path.parent / "checkpoint1d_manifest.json"
    if not events_path.is_file():
        raise InputError("Checkpoint 1E events input 不存在：{}".format(events_path))
    if not manifest_path.is_file():
        raise InputError("Checkpoint 1E 找不到 Checkpoint 1D manifest：{}".format(manifest_path))
    events_payload = load_json(events_path)
    manifest = load_json(manifest_path)
    _schema_validator(project_root, "battle_event.schema.json").validate(events_payload)
    _schema_validator(project_root, "checkpoint1d_manifest.schema.json").validate(manifest)
    events_sha256 = sha256_file(events_path)
    if manifest["output"]["sha256"] != events_sha256:
        raise InputError("Checkpoint 1D events hash 與 manifest 不一致")
    if int(events_payload["event_count"]) != len(events_payload["events"]):
        raise InputError("Checkpoint 1D event_count 與 events 長度不一致")
    if int(manifest["event_count"]) != int(events_payload["event_count"]):
        raise InputError("Checkpoint 1D manifest event_count 不一致")
    timestamps = [float(event["timestamp"]) for event in events_payload["events"]]
    if timestamps != sorted(timestamps):
        raise InputError("Checkpoint 1D timestamps 不單調")
    return {
        "events_payload": events_payload,
        "manifest": manifest,
        "manifest_path": manifest_path,
        "events_sha256": events_sha256,
        "manifest_sha256": sha256_file(manifest_path),
    }


def _contains_turn_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(key == "turn" or _contains_turn_key(child) for key, child in value.items())
    if isinstance(value, list):
        return any(_contains_turn_key(child) for child in value)
    return False


def run_checkpoint_1e(
    project_root: Path,
    events_path: Path,
    output_dir: Path,
    review_output_dir: Path,
) -> Dict[str, Any]:
    project_root = project_root.resolve()
    events_path = events_path.resolve()
    output_dir = output_dir.resolve()
    review_output_dir = review_output_dir.resolve()
    source = _validate_checkpoint1d_source(project_root, events_path)
    events_payload = source["events_payload"]
    events = list(events_payload["events"])
    groups, relations = build_battle_timeline(events)
    counts = timeline_counts(groups, relations)
    source_ids = [str(event["id"]) for event in events]
    group_rows = [group.to_dict() for group in groups]
    relation_rows = [edge.to_dict() for edge in relations]

    timeline_payload = {
        "schema_version": TIMELINE_SCHEMA_VERSION,
        "checkpoint": "1E",
        "kind": "ordered_battle_timeline",
        "builder_version": BUILDER_VERSION,
        "source": {
            "checkpoint": "1D",
            "events_path": project_relative(events_path, project_root),
            "events_sha256": source["events_sha256"],
            "manifest_path": project_relative(source["manifest_path"], project_root),
            "manifest_sha256": source["manifest_sha256"],
            "event_count": len(events),
        },
        "source_event_count": len(events),
        "all_source_event_ids": source_ids,
        "timeline_count": len(groups),
        "relation_count": len(relations),
        "group_status_counts": counts["group_status_counts"],
        "unlinked_event_count": counts["unlinked_event_count"],
        "groups": group_rows,
    }
    relations_payload = {
        "schema_version": TIMELINE_SCHEMA_VERSION,
        "checkpoint": "1E",
        "kind": "timeline_relations",
        "source_event_count": len(events),
        "relation_count": len(relations),
        "relation_status_counts": counts["relation_status_counts"],
        "relations": relation_rows,
    }
    audit_payload = build_timeline_audit(events, groups, relations)
    if not all(audit_payload["validation"].values()):
        raise InputError("Checkpoint 1E timeline audit 失敗：{}".format(audit_payload["validation"]))
    if _contains_turn_key(timeline_payload) or _contains_turn_key(relations_payload):
        raise InputError("Checkpoint 1E 禁止推算正式 turn")

    events_hash_before = sha256_file(events_path)
    manifest_hash_before = sha256_file(source["manifest_path"])
    with OutputTransaction(project_root, output_dir) as output_transaction:
        with OutputTransaction(project_root, review_output_dir) as review_transaction:
            timeline_path = output_transaction.staging_dir / "battle_timeline.json"
            relations_path = output_transaction.staging_dir / "timeline_relations.json"
            audit_path = output_transaction.staging_dir / "timeline_audit.json"
            write_json(timeline_path, timeline_payload)
            write_json(relations_path, relations_payload)
            write_json(audit_path, audit_payload)
            _schema_validator(project_root, "battle_timeline.schema.json").validate(
                timeline_payload
            )
            _schema_validator(project_root, "timeline_relations.schema.json").validate(
                relations_payload
            )
            _schema_validator(project_root, "timeline_audit.schema.json").validate(
                audit_payload
            )

            review_manifest = build_timeline_review_pack(
                review_transaction.staging_dir,
                groups,
                relations,
                events,
                sha256_file(timeline_path),
                project_relative(output_dir / "battle_timeline.json", project_root),
            )
            _schema_validator(
                project_root, "checkpoint1e_review_manifest.schema.json"
            ).validate(review_manifest)

            consumed_ids = [event_id for group in groups for event_id in group.event_ids]
            validation = {
                "source_schema_valid": True,
                "source_manifest_valid": True,
                "source_hash_matches_manifest": True,
                "source_event_count_matches": len(events)
                == int(source["manifest"]["event_count"]),
                "timestamps_monotonic": [float(event["timestamp"]) for event in events]
                == sorted(float(event["timestamp"]) for event in events),
                "all_source_events_covered": len(consumed_ids) == len(source_ids)
                and set(consumed_ids) == set(source_ids),
                "events_consumed_once": len(consumed_ids) == len(set(consumed_ids)),
                "groups_monotonic": [group.start_time for group in groups]
                == sorted(group.start_time for group in groups),
                "relation_order_deterministic": [edge.relation_id for edge in relations]
                == sorted(edge.relation_id for edge in relations),
                "no_turn_inference": not _contains_turn_key(timeline_payload)
                and not _contains_turn_key(relations_payload),
                "human_fields_default_null": review_manifest["validation"][
                    "all_human_fields_null"
                ],
                "review_cards_complete": review_manifest["validation"][
                    "all_groups_have_cards"
                ]
                and review_manifest["validation"][
                    "all_needs_review_relations_have_cards"
                ],
                "source_events_unchanged": sha256_file(events_path) == events_hash_before,
                "source_manifest_unchanged": sha256_file(source["manifest_path"])
                == manifest_hash_before,
            }
            if not all(validation.values()):
                raise InputError("Checkpoint 1E validation 失敗：{}".format(validation))
            manifest = {
                "schema_version": TIMELINE_SCHEMA_VERSION,
                "checkpoint": "1E",
                "kind": "checkpoint1e_manifest",
                "status": "complete",
                "builder_version": BUILDER_VERSION,
                "source": timeline_payload["source"],
                "source_event_count": len(events),
                "timeline_count": len(groups),
                "relation_count": len(relations),
                "group_status_counts": counts["group_status_counts"],
                "relation_status_counts": counts["relation_status_counts"],
                "unlinked_event_count": counts["unlinked_event_count"],
                "outputs": {
                    "battle_timeline": {
                        "path": "battle_timeline.json",
                        "sha256": sha256_file(timeline_path),
                    },
                    "timeline_relations": {
                        "path": "timeline_relations.json",
                        "sha256": sha256_file(relations_path),
                    },
                    "timeline_audit": {
                        "path": "timeline_audit.json",
                        "sha256": sha256_file(audit_path),
                    },
                    "review_manifest": {
                        "path": "../checkpoint-1e-review/review_manifest.json",
                        "sha256": sha256_file(
                            review_transaction.staging_dir / "review_manifest.json"
                        ),
                    },
                },
                "validation": validation,
                "scope_guards": {
                    "ocr_rerun": False,
                    "video_scanned": False,
                    "human_review_modified": False,
                    "llm_used": False,
                    "turn_inferred": False,
                    "battle_state_performed": False,
                    "replay_analysis_performed": False,
                    "gui_created": False,
                },
            }
            _schema_validator(project_root, "checkpoint1e_manifest.schema.json").validate(
                manifest
            )
            write_json(output_transaction.staging_dir / "checkpoint1e_manifest.json", manifest)
            if sha256_file(events_path) != events_hash_before:
                raise InputError("Checkpoint 1D battle_events.json 在 1E 執行期間被修改")
            if sha256_file(source["manifest_path"]) != manifest_hash_before:
                raise InputError("Checkpoint 1D manifest 在 1E 執行期間被修改")
            OutputTransaction.commit_group((output_transaction, review_transaction))

    finalize_generated_output(review_output_dir)
    finalize_generated_output(output_dir)
    if sha256_file(events_path) != events_hash_before:
        raise InputError("Checkpoint 1D battle_events.json 在 1E 完成後被修改")
    if sha256_file(source["manifest_path"]) != manifest_hash_before:
        raise InputError("Checkpoint 1D manifest 在 1E 完成後被修改")
    return manifest
