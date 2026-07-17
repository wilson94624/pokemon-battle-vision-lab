"""Checkpoint 1H frozen input validation 與 provenance index。"""

from pathlib import Path
from typing import Any, Dict, Mapping

from jsonschema import Draft202012Validator

from .checkpoint1f import _validate_inputs as validate_reviewed_timeline_inputs
from .config import load_json
from .errors import InputError
from .pokemon_knowledge_base import PokemonKnowledgeBase
from .utils import project_relative, sha256_file


REQUIRED_1G_OUTPUTS = (
    "hp_changes.json",
    "hp_observations.json",
    "pokemon_entities.json",
    "decision_cycles.json",
    "move_menu_observations.json",
)


def _validator(project_root: Path, schema_name: str) -> Draft202012Validator:
    schema = load_json(project_root / "schemas" / schema_name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _required(path: Path, label: str) -> Path:
    if not path.is_file():
        raise InputError("Checkpoint 1H {} 不存在：{}".format(label, path))
    return path


def _load_checkpoint1g(
    project_root: Path,
    checkpoint1g_dir: Path,
) -> Dict[str, Any]:
    manifest_path = _required(
        checkpoint1g_dir / "checkpoint1g_manifest.json",
        "Checkpoint 1G manifest",
    )
    manifest = load_json(manifest_path)
    _validator(project_root, "checkpoint1g_manifest.schema.json").validate(manifest)
    if manifest.get("status") != "complete":
        raise InputError("Checkpoint 1G manifest 尚未完成")

    payloads: Dict[str, Any] = {}
    tracked = {str(manifest_path.resolve()): sha256_file(manifest_path)}
    for reference in manifest["source"].values():
        path = _required(project_root / str(reference["path"]), "1G frozen source")
        actual_hash = sha256_file(path)
        if actual_hash != reference["sha256"]:
            raise InputError(
                "Checkpoint 1G frozen source hash gate 失敗：{}".format(path)
            )
        tracked[str(path.resolve())] = actual_hash
    for filename, reference in sorted(manifest["outputs"].items()):
        path = _required(checkpoint1g_dir / str(reference["path"]), filename)
        actual_hash = sha256_file(path)
        if actual_hash != reference["sha256"]:
            raise InputError("Checkpoint 1G output hash gate 失敗：{}".format(filename))
        _validator(project_root, str(reference["schema"])).validate(load_json(path))
        tracked[str(path.resolve())] = actual_hash
        if filename in REQUIRED_1G_OUTPUTS:
            payloads[filename] = load_json(path)
    missing = sorted(set(REQUIRED_1G_OUTPUTS) - set(payloads))
    if missing:
        raise InputError("Checkpoint 1G manifest 缺少必要輸出：{}".format(missing))
    return {
        "manifest": manifest,
        "manifest_path": manifest_path,
        "payloads": payloads,
        "tracked_hashes": tracked,
    }


def _validate_references(source: Mapping[str, Any]) -> None:
    events = source["events"]["events"]
    timeline = source["timeline"]["groups"]
    relations = source["relations"]["relations"]
    hp_changes = source["hp_changes"]["changes"]
    hp_observations = source["hp_observations"]["observations"]
    entities = source["pokemon_entities"]["entities"]
    cycles = source["decision_cycles"]["cycles"]
    menus = source["move_menu_observations"]["observations"]

    def ids(rows, key):
        values = [str(row[key]) for row in rows]
        if len(values) != len(set(values)):
            raise InputError("Checkpoint 1H source ID 重複：{}".format(key))
        return set(values)

    event_ids = ids(events, "id")
    timeline_ids = ids(timeline, "timeline_id")
    relation_ids = ids(relations, "relation_id")
    hp_change_ids = ids(hp_changes, "change_id")
    hp_observation_ids = ids(hp_observations, "observation_id")
    entity_ids = ids(entities, "entity_id")
    cycle_ids = ids(cycles, "cycle_id")
    menu_ids = ids(menus, "candidate_id")

    if not cycles:
        raise InputError("Checkpoint 1G Decision Cycle 不可為空")
    if any(set(row["event_ids"]) - event_ids for row in timeline):
        raise InputError("Checkpoint 1E timeline 引用不存在的 BattleEvent")
    if any(
        {str(row["from_event_id"]), str(row["to_event_id"])} - event_ids
        for row in relations
    ):
        raise InputError("Checkpoint 1E relation 引用不存在的 BattleEvent")
    if any(
        (set(map(str, row["source_observation_ids"])) - hp_observation_ids)
        or (set(map(str, row.get("linked_timeline_ids", []))) - timeline_ids)
        or (
            row.get("pokemon_entity_id") is not None
            and str(row["pokemon_entity_id"]) not in entity_ids
        )
        for row in hp_changes
    ):
        raise InputError("Checkpoint 1G HP change provenance 引用無效")
    if any(set(map(str, row["battle_event_ids"])) - event_ids for row in cycles):
        raise InputError("Checkpoint 1G Decision Cycle 引用不存在的 BattleEvent")
    cycle_event_ids = [
        str(event_id) for row in cycles for event_id in row["battle_event_ids"]
    ]
    if (
        len(cycle_event_ids) != len(set(cycle_event_ids))
        or set(cycle_event_ids) != event_ids
    ):
        raise InputError("Checkpoint 1G Decision Cycle 未恰好涵蓋全部 BattleEvents")
    if any(set(map(str, row["timeline_ids"])) - timeline_ids for row in cycles):
        raise InputError("Checkpoint 1G Decision Cycle 引用不存在的 timeline group")
    boundary_candidate_ids = {
        str(candidate_id)
        for row in cycles
        for evidence in row["boundary_evidence"]
        for candidate_id in evidence.get("candidate_ids", [])
    }
    if boundary_candidate_ids - menu_ids:
        raise InputError("Checkpoint 1G Decision Cycle 引用不存在的 Move Menu")
    if len(hp_change_ids) != int(source["hp_changes"]["change_count"]):
        raise InputError("Checkpoint 1G hp_changes count 不一致")
    if len(cycle_ids) != int(source["decision_cycles"]["cycle_count"]):
        raise InputError("Checkpoint 1G decision_cycles count 不一致")
    if len(relation_ids) != int(source["relations"]["relation_count"]):
        raise InputError("Checkpoint 1E relation count 不一致")


def load_checkpoint1h_inputs(
    project_root: Path,
    checkpoint1d_dir: Path,
    checkpoint1e_dir: Path,
    checkpoint1e_review_dir: Path,
    checkpoint1g_dir: Path,
) -> Dict[str, Any]:
    project_root = project_root.resolve()
    timeline_source = validate_reviewed_timeline_inputs(
        project_root,
        checkpoint1d_dir / "battle_events.json",
        checkpoint1e_dir / "battle_timeline.json",
        checkpoint1e_dir / "timeline_relations.json",
        checkpoint1e_review_dir,
    )
    visual_source = _load_checkpoint1g(project_root, checkpoint1g_dir)
    payloads = visual_source["payloads"]
    knowledge_base = PokemonKnowledgeBase.from_project(project_root)

    kb_expected = visual_source["manifest"]["source"]
    for kb_path in (knowledge_base.data_path, knowledge_base.manifest_path):
        try:
            key = str(kb_path.relative_to(project_root))
        except ValueError as exc:
            raise InputError("Checkpoint 1H Knowledge Base 必須位於專案內") from exc
        if key not in kb_expected or sha256_file(kb_path) != kb_expected[key]["sha256"]:
            raise InputError("Checkpoint 1H Knowledge Base 與 1G frozen source 不一致")

    source = {
        **timeline_source,
        "hp_changes": payloads["hp_changes.json"],
        "hp_observations": payloads["hp_observations.json"],
        "pokemon_entities": payloads["pokemon_entities.json"],
        "decision_cycles": payloads["decision_cycles.json"],
        "move_menu_observations": payloads["move_menu_observations.json"],
        "checkpoint1g_manifest": visual_source["manifest"],
        "checkpoint1g_manifest_path": visual_source["manifest_path"],
        "knowledge_base": knowledge_base,
    }
    tracked = {
        **timeline_source["tracked_hashes"],
        **visual_source["tracked_hashes"],
        str(knowledge_base.data_path): sha256_file(knowledge_base.data_path),
        str(knowledge_base.manifest_path): sha256_file(knowledge_base.manifest_path),
    }
    source["tracked_hashes"] = tracked
    source["artifact_paths"] = {
        "events": project_relative(checkpoint1d_dir / "battle_events.json", project_root),
        "hp_changes": project_relative(checkpoint1g_dir / "hp_changes.json", project_root),
        "hp_observations": project_relative(checkpoint1g_dir / "hp_observations.json", project_root),
        "decision_cycles": project_relative(checkpoint1g_dir / "decision_cycles.json", project_root),
        "move_menu_observations": project_relative(checkpoint1g_dir / "move_menu_observations.json", project_root),
    }
    _validate_references(source)
    return source


def validate_frozen_inputs_unchanged(tracked_hashes: Mapping[str, str]) -> bool:
    return all(
        Path(path).is_file() and sha256_file(Path(path)) == expected
        for path, expected in tracked_hashes.items()
    )
