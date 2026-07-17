"""Checkpoint 1I direct input gates 與 frozen 1H provenance audit。"""

from pathlib import Path
from typing import Any, Dict, Mapping

from jsonschema import Draft202012Validator

from .config import load_json
from .errors import InputError
from .rule_knowledge import PokemonRuleKnowledgeBase
from .utils import project_relative, sha256_file


def _validator(project_root: Path, schema_name: str) -> Draft202012Validator:
    schema_path = project_root / "schemas" / schema_name
    if not schema_path.is_file():
        raise InputError("Checkpoint 1I schema 不存在：{}".format(schema_path))
    schema = load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise InputError("Checkpoint 1I {} 不存在：{}".format(label, path))


def load_checkpoint1i_inputs(
    project_root: Path, checkpoint1h_dir: Path
) -> Dict[str, Any]:
    """1I 直接信任 frozen 1H outputs；舊 upstream snapshot drift 只進 audit。"""
    root = project_root.resolve()
    source_dir = checkpoint1h_dir.resolve()
    manifest_path = source_dir / "checkpoint1h_manifest.json"
    _require_file(manifest_path, "Checkpoint 1H manifest")
    manifest = load_json(manifest_path)
    _validator(root, "checkpoint1h_manifest.schema.json").validate(manifest)
    if manifest.get("status") != "complete":
        raise InputError("Checkpoint 1I 只接受 complete Checkpoint 1H manifest")

    output_payloads: Dict[str, Mapping[str, Any]] = {}
    direct_paths = [manifest_path]
    for filename, reference in manifest["outputs"].items():
        path = source_dir / str(reference["path"])
        _require_file(path, "Checkpoint 1H output {}".format(filename))
        if sha256_file(path) != reference["sha256"]:
            raise InputError(
                "Checkpoint 1I direct 1H output hash 不一致：{}".format(path)
            )
        payload = load_json(path)
        _validator(root, str(reference["schema"])).validate(payload)
        output_payloads[filename] = payload
        direct_paths.append(path)

    required_outputs = {
        "battle_facts.json",
        "battle_fact_relations.json",
        "reconstructed_turns.json",
        "checkpoint1h_audit.json",
    }
    missing_outputs = sorted(required_outputs - set(output_payloads))
    if missing_outputs:
        raise InputError(
            "Checkpoint 1I 缺少必要 1H outputs：{}".format(missing_outputs)
        )

    facts_payload = output_payloads["battle_facts.json"]
    relations_payload = output_payloads["battle_fact_relations.json"]
    fact_ids = [str(row["fact_id"]) for row in facts_payload["facts"]]
    if len(fact_ids) != len(set(fact_ids)):
        raise InputError("Checkpoint 1I 來源 Battle Fact ID 重複")
    if int(facts_payload["fact_count"]) != len(fact_ids):
        raise InputError("Checkpoint 1I 來源 Battle Fact count 不一致")
    fact_id_set = set(fact_ids)
    relation_ids = []
    for relation in relations_payload["relations"]:
        relation_ids.append(str(relation["fact_relation_id"]))
        if relation["from_fact_id"] not in fact_id_set or relation["to_fact_id"] not in fact_id_set:
            raise InputError(
                "Checkpoint 1I 來源 relation 含無法解析的 Battle Fact：{}".format(
                    relation["fact_relation_id"]
                )
            )
    if len(relation_ids) != len(set(relation_ids)):
        raise InputError("Checkpoint 1I 來源 Fact Relation ID 重複")
    if int(relations_payload["relation_count"]) != len(relation_ids):
        raise InputError("Checkpoint 1I 來源 Fact Relation count 不一致")

    knowledge = PokemonRuleKnowledgeBase.from_project(root)
    direct_paths.extend([knowledge.data_path, knowledge.manifest_path])
    direct_hashes = {
        project_relative(path, root): sha256_file(path) for path in direct_paths
    }

    # 1H manifest 保存的是當時的 upstream snapshot；後續只改人工 review metadata
    # 時不重建 1H，因此 drift 必須透明記錄，但不可改寫 frozen Battle Facts。
    upstream_drift = []
    for source in manifest.get("source", {}).values():
        relative = str(source["path"])
        path = root / relative
        actual = sha256_file(path) if path.is_file() else None
        if actual != source["sha256"]:
            upstream_drift.append(
                {
                    "path": relative,
                    "checkpoint1h_snapshot_sha256": source["sha256"],
                    "current_sha256": actual,
                }
            )

    return {
        "checkpoint1h_manifest": manifest,
        "checkpoint1h_manifest_path": manifest_path,
        "battle_facts": facts_payload,
        "battle_fact_relations": relations_payload,
        "reconstructed_turns": output_payloads["reconstructed_turns.json"],
        "checkpoint1h_audit": output_payloads["checkpoint1h_audit.json"],
        "knowledge": knowledge,
        "direct_hashes": direct_hashes,
        "upstream_snapshot_drift": upstream_drift,
    }


def direct_inputs_unchanged(project_root: Path, hashes: Mapping[str, str]) -> bool:
    root = project_root.resolve()
    return all(
        (root / relative).is_file()
        and sha256_file(root / relative) == expected
        for relative, expected in hashes.items()
    )
