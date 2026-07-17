"""Checkpoint 1J direct gates、knowledge migration 與 historical drift audit。"""

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from jsonschema import Draft202012Validator

from .approved_drift import (
    REGISTRY_PATH,
    REGISTRY_SCHEMA_PATH,
    ApprovedDriftRegistry,
)
from .checkpoint1i_inputs import load_checkpoint1i_inputs
from .config import load_json
from .errors import InputError
from .rule_knowledge import PokemonRuleKnowledgeBase
from .utils import project_relative, sha256_file
from .replay import DEFAULT_REPLAY_ID, normalize_replay_id, resolve_project_path


def _validator(project_root: Path, schema_name: str) -> Draft202012Validator:
    path = project_root / "schemas" / schema_name
    if not path.is_file():
        raise InputError("Checkpoint 1J schema 不存在：{}".format(path))
    schema = load_json(path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise InputError("Checkpoint 1J {} 不存在：{}".format(label, path))


def _validate_file_ref(
    project_root: Path,
    base: Path,
    reference: Mapping[str, Any],
    label: str,
) -> Path:
    path = (base / str(reference["path"])).resolve()
    _require_file(path, label)
    if sha256_file(path) != reference["sha256"]:
        raise InputError("Checkpoint 1J {} hash 不一致".format(label))
    schema_name = str(reference.get("schema") or "")
    if schema_name:
        _validator(project_root, Path(schema_name).name).validate(load_json(path))
    return path


def _validate_additive_migration(
    project_root: Path,
    v1: PokemonRuleKnowledgeBase,
    v2: PokemonRuleKnowledgeBase,
) -> Dict[str, bool]:
    migration = v2.manifest["migration"]
    previous = migration["previous_version"]
    for label in ("data", "manifest"):
        ref = previous[label]
        path = project_root / ref["path"]
        _require_file(path, "v1 migration {}".format(label))
        if sha256_file(path) != ref["sha256"]:
            raise InputError("Checkpoint 1J v1 migration {} hash 不一致".format(label))
    preserved = all(
        knowledge_id in v2.knowledge_by_id
        and v2.knowledge_by_id[knowledge_id] == row
        for knowledge_id, row in v1.knowledge_by_id.items()
    )
    actual_added = sorted(set(v2.knowledge_by_id) - set(v1.knowledge_by_id))
    declared_added = sorted(str(value) for value in migration["added_knowledge_ids"])
    validation = {
        "v1_data_hash_valid": sha256_file(v1.data_path)
        == previous["data"]["sha256"],
        "v1_manifest_hash_valid": sha256_file(v1.manifest_path)
        == previous["manifest"]["sha256"],
        "existing_knowledge_ids_and_payloads_preserved": preserved,
        "declared_added_knowledge_ids_exact": actual_added == declared_added,
        "existing_interpretations_require_no_regeneration": migration[
            "interpretation_regeneration"
        ]["required_existing_interpretation_ids"]
        == [],
        "existing_knowledge_semantics_unchanged": migration[
            "existing_knowledge_semantics_changed"
        ]
        is False,
    }
    failed = sorted(key for key, value in validation.items() if not value)
    if failed:
        raise InputError("Checkpoint 1J knowledge migration validation 失敗：{}".format(failed))
    return validation


def _validate_frozen_outputs(
    root: Path,
    checkpoint: str,
    output_dir: Path,
    manifest: Mapping[str, Any],
) -> List[Path]:
    paths = []
    for name, reference in manifest["outputs"].items():
        path = output_dir / str(reference.get("path") or name)
        _require_file(path, "Checkpoint {} direct output".format(checkpoint))
        if sha256_file(path) != reference["sha256"]:
            raise InputError(
                "Checkpoint 1J direct frozen {} payload hash 不一致：{}".format(
                    checkpoint, path
                )
            )
        paths.append(path)
    return paths


def _historical_drift_audit(
    root: Path,
    registry: ApprovedDriftRegistry,
    checkpoint1g_dir: Optional[Path] = None,
    checkpoint1h_dir: Optional[Path] = None,
    enforce_registry_completeness: bool = True,
) -> Dict[str, Any]:
    records = []
    direct_paths = []
    used_ids = set()
    directories = {
        "1G": (checkpoint1g_dir or (root / "outputs/checkpoint-1g")).resolve(),
        "1H": (checkpoint1h_dir or (root / "outputs/checkpoint-1h")).resolve(),
    }
    for checkpoint in ("1G", "1H"):
        directory = directories[checkpoint]
        manifest_path = directory / "checkpoint{}_manifest.json".format(
            checkpoint.lower()
        )
        _require_file(manifest_path, "Checkpoint {} manifest".format(checkpoint))
        manifest = load_json(manifest_path)
        _validator(
            root, "checkpoint{}_manifest.schema.json".format(checkpoint.lower())
        ).validate(manifest)
        direct_paths.append(manifest_path)
        direct_paths.extend(
            _validate_frozen_outputs(root, checkpoint, directory, manifest)
        )
        for source in manifest["source"].values():
            path = root / str(source["path"])
            _require_file(path, "Checkpoint {} upstream source".format(checkpoint))
            actual = sha256_file(path)
            direct_paths.append(path)
            approved = registry.verify(
                checkpoint,
                str(source["path"]),
                str(source["sha256"]),
                actual,
            )
            if approved is not None:
                used_ids.add(str(approved["drift_id"]))
                records.append(
                    {
                        "drift_id": approved["drift_id"],
                        "consumer_checkpoint": checkpoint,
                        "source_path": source["path"],
                        "frozen_snapshot_sha256": source["sha256"],
                        "current_sha256": actual,
                        "change_class": approved["change_class"],
                        "authorization": approved["authorization"],
                        "reviewed_by": approved["reviewed_by"],
                        "reviewed_at": approved["reviewed_at"],
                        "reason": approved["reason"],
                    }
                )
    registry_ids = {str(row["drift_id"]) for row in registry.payload["records"]}
    validation = {
        "direct_frozen_1g_and_1h_payload_hashes_blocking": True,
        "all_current_drift_exactly_approved": True,
        "all_registry_records_consumed": (used_ids == registry_ids)
        if enforce_registry_completeness
        else True,
        "unexpected_drift_rejected": True,
        "frozen_manifests_not_rewritten": True,
    }
    if not all(validation.values()):
        raise InputError("Checkpoint 1J historical drift registry 未完全對應現況")
    payload = {
        "schema_version": "0.1.0",
        "checkpoint": "1J",
        "kind": "historical_snapshot_drift_audit",
        "registry": {
            "path": REGISTRY_PATH.as_posix(),
            "schema": REGISTRY_SCHEMA_PATH.name,
            "sha256": sha256_file(root / REGISTRY_PATH),
        },
        "policy": "exact_approved_metadata_drift_only_direct_frozen_payload_hashes_remain_blocking",
        "approved_drift_count": len(records),
        "records": sorted(records, key=lambda row: row["drift_id"]),
        "validation": validation,
    }
    return {"payload": payload, "direct_paths": direct_paths}


def load_checkpoint1j_inputs(
    project_root: Path,
    checkpoint1h_dir: Path,
    checkpoint1i_dir: Path,
    review_decisions_path: Optional[Path] = None,
    checkpoint1g_dir: Optional[Path] = None,
    replay_id: str = DEFAULT_REPLAY_ID,
) -> Dict[str, Any]:
    root = project_root.resolve()
    replay_id = normalize_replay_id(replay_id)
    source_1i = resolve_project_path(root, checkpoint1i_dir)
    source_1h = resolve_project_path(root, checkpoint1h_dir)
    source = load_checkpoint1i_inputs(root, source_1h)

    manifest_1i_path = source_1i / "checkpoint1i_manifest.json"
    _require_file(manifest_1i_path, "Checkpoint 1I manifest")
    manifest_1i = load_json(manifest_1i_path)
    _validator(root, "checkpoint1i_manifest.schema.json").validate(manifest_1i)
    if manifest_1i.get("status") != "complete":
        raise InputError("Checkpoint 1J 只接受 complete Checkpoint 1I")
    direct_paths = [manifest_1i_path]
    outputs_1i = {}
    for name, reference in manifest_1i["outputs"].items():
        path = _validate_file_ref(root, source_1i, reference, "Checkpoint 1I {}".format(name))
        outputs_1i[name] = load_json(path)
        direct_paths.append(path)
    interpretations_payload = outputs_1i.get("rule_interpretations.json")
    if not interpretations_payload:
        raise InputError("Checkpoint 1J 缺少 1I rule_interpretations.json")

    for label in ("manifest", "battle_facts", "battle_fact_relations"):
        ref = manifest_1i["source"][label]
        path = root / ref["path"]
        _require_file(path, "Checkpoint 1I source {}".format(label))
        if sha256_file(path) != ref["sha256"]:
            raise InputError("Checkpoint 1J direct 1H {} hash 不一致".format(label))

    v1 = source["knowledge"]
    v2 = PokemonRuleKnowledgeBase.from_version(root, "v2")
    migration_validation = _validate_additive_migration(root, v1, v2)
    direct_paths.extend([v1.data_path, v1.manifest_path, v2.data_path, v2.manifest_path])

    registry = ApprovedDriftRegistry.from_project(root)
    direct_paths.extend([root / REGISTRY_PATH, root / REGISTRY_SCHEMA_PATH])
    drift = _historical_drift_audit(
        root,
        registry,
        checkpoint1g_dir,
        source_1h,
        enforce_registry_completeness=replay_id == DEFAULT_REPLAY_ID,
    )
    direct_paths.extend(drift["direct_paths"])
    if review_decisions_path is not None:
        decisions = resolve_project_path(root, review_decisions_path)
        _require_file(decisions, "review decisions CSV")
        # worksheet 可位於即將被 transaction 替換的 output tree；內容會在
        # replace 前載入與驗證，因此不納入 replace 後的 immutable input gate。
    unique_paths = list(dict.fromkeys(path.resolve() for path in direct_paths))
    direct_hashes = {
        project_relative(path, root): sha256_file(path) for path in unique_paths
    }
    return {
        "battle_facts": source["battle_facts"],
        "battle_fact_relations": source["battle_fact_relations"],
        "checkpoint1i_manifest": manifest_1i,
        "checkpoint1i_manifest_path": manifest_1i_path,
        "existing_interpretations": interpretations_payload,
        "v1_knowledge": v1,
        "v2_knowledge": v2,
        "migration_validation": migration_validation,
        "historical_drift_audit": drift["payload"],
        "direct_hashes": direct_hashes,
    }


def direct_inputs_unchanged(project_root: Path, hashes: Mapping[str, str]) -> bool:
    root = project_root.resolve()
    return all(
        (root / relative).is_file()
        and sha256_file(root / relative) == expected
        for relative, expected in hashes.items()
    )
