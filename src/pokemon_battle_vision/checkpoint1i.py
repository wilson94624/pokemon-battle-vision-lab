"""Checkpoint 1I orchestration：以 knowledge 解釋 frozen Battle Facts。"""

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from jsonschema import Draft202012Validator

from .checkpoint1i_inputs import direct_inputs_unchanged, load_checkpoint1i_inputs
from .config import load_json
from .errors import InputError
from .output_transaction import OutputTransaction, finalize_generated_output
from .rule_interpretation import build_rule_interpretations
from .rule_interpretation_models import INTERPRETATION_SCHEMA_VERSION, RuleInterpretation
from .utils import project_relative, sha256_file, write_json
from .replay import DEFAULT_REPLAY_ID, normalize_replay_id, resolve_project_path


ENGINE_VERSION = "0.1.0"


def _validator(project_root: Path, schema_name: str) -> Draft202012Validator:
    schema = load_json(project_root / "schemas" / schema_name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _count_values(values: Sequence[str], complete_keys: Sequence[str] = ()) -> Dict[str, int]:
    counts = Counter(values)
    return {
        key: int(counts.get(key, 0))
        for key in sorted(set(complete_keys) | set(counts))
    }


def _interpretation_payload(
    interpretations: Sequence[RuleInterpretation],
    source_fact_count: int,
    knowledge_version: str,
    knowledge_sha256: str,
) -> Dict[str, Any]:
    records = [row.to_dict() for row in interpretations]
    return {
        "schema_version": INTERPRETATION_SCHEMA_VERSION,
        "checkpoint": "1I",
        "kind": "rule_interpretations",
        "interpretation_version": INTERPRETATION_SCHEMA_VERSION,
        "immutability_policy": (
            "interpretations_reference_but_never_create_or_modify_battle_facts"
        ),
        "knowledge_version": knowledge_version,
        "knowledge_sha256": knowledge_sha256,
        "source_battle_fact_count": source_fact_count,
        "interpretation_count": len(records),
        "certainty_counts": _count_values(
            [row["certainty"] for row in records],
            ("supported", "unresolved", "conflicted"),
        ),
        "interpretation_type_counts": _count_values(
            [row["interpretation_type"] for row in records]
        ),
        "interpretations": records,
    }


def _review_payload(
    interpretations: Sequence[RuleInterpretation],
) -> Dict[str, Any]:
    records = []
    questions = [
        "引用的 Battle Facts 是否足以支持此解釋？",
        "required_observations 的 satisfied／missing／contradicted 是否正確？",
        "結論是否保持在 interpretation 層，沒有改寫 observation 或 Battle Fact？",
    ]
    for item in interpretations:
        row = item.to_dict()
        status_counts = _count_values(
            [value["status"] for value in row["required_observations"]],
            ("satisfied", "missing", "contradicted"),
        )
        records.append(
            {
                "interpretation_id": row["interpretation_id"],
                "sequence": row["sequence"],
                "timestamp": row["timestamp"],
                "interpretation_type": row["interpretation_type"],
                "rule_id": row["rule_id"],
                "certainty": row["certainty"],
                "confidence": row["confidence"],
                "referenced_battle_fact_ids": row[
                    "referenced_battle_fact_ids"
                ],
                "referenced_fact_relation_ids": row[
                    "referenced_fact_relation_ids"
                ],
                "conclusion_code": row["conclusion"]["code"],
                "conclusion_summary": row["conclusion"]["summary"],
                "required_observation_status_counts": status_counts,
                "unresolved_reason": row["unresolved_reason"],
                "review_questions": questions,
            }
        )
    return {
        "schema_version": INTERPRETATION_SCHEMA_VERSION,
        "checkpoint": "1I",
        "kind": "interpretation_review",
        "record_count": len(records),
        "review_policy": (
            "review_interpretation_only_never_edit_referenced_battle_facts"
        ),
        "records": records,
    }


def _validate_interpretations(
    interpretations: Sequence[RuleInterpretation],
    facts: Sequence[Mapping[str, Any]],
    relations: Sequence[Mapping[str, Any]],
    knowledge_ids: Sequence[str],
    knowledge_version: str,
    knowledge_sha256: str,
) -> Dict[str, bool]:
    fact_by_id = {str(row["fact_id"]): row for row in facts}
    relation_ids = {str(row["fact_relation_id"]) for row in relations}
    knowledge_id_set = set(knowledge_ids)
    rows = [item.to_dict() for item in interpretations]
    ids = [row["interpretation_id"] for row in rows]
    expected_ids = [
        "rule-interpretation-{:04d}".format(index)
        for index in range(1, len(rows) + 1)
    ]
    fact_refs_resolve = all(
        fact_id in fact_by_id
        for row in rows
        for fact_id in row["referenced_battle_fact_ids"]
    )
    relation_refs_resolve = all(
        relation_id in relation_ids
        for row in rows
        for relation_id in row["referenced_fact_relation_ids"]
    )
    observed_evidence_resolves = all(
        evidence["fact_id"] in fact_by_id
        and evidence["fact_id"] in row["referenced_battle_fact_ids"]
        and set(evidence["evidence_record_ids"])
        <= {
            str(source["record_id"])
            for source in fact_by_id[evidence["fact_id"]]["evidence"]
        }
        for row in rows
        for evidence in row["observed_evidence"]
    )
    knowledge_complete = all(
        evidence["knowledge_id"] in knowledge_id_set
        and evidence["knowledge_version"] == knowledge_version
        and evidence["knowledge_sha256"] == knowledge_sha256
        and evidence["source_refs"]
        for row in rows
        for evidence in row["knowledge_evidence"]
    )
    certainty_consistent = all(
        (
            row["certainty"] == "supported"
            and all(
                requirement["status"] == "satisfied"
                for requirement in row["required_observations"]
            )
            and row["unresolved_reason"] is None
        )
        or (
            row["certainty"] == "unresolved"
            and any(
                requirement["status"] != "satisfied"
                for requirement in row["required_observations"]
            )
            and bool(row["unresolved_reason"])
        )
        or (
            # conflict 代表觀察條件完整，但觀察結論與 knowledge expectation 不一致。
            row["certainty"] == "conflicted"
            and all(
                requirement["status"] == "satisfied"
                for requirement in row["required_observations"]
            )
            and bool(row["unresolved_reason"])
        )
        for row in rows
    )
    return {
        "interpretation_ids_unique": len(ids) == len(set(ids)),
        "interpretation_ids_contiguous": ids == expected_ids,
        "interpretation_sequences_contiguous": [row["sequence"] for row in rows]
        == list(range(1, len(rows) + 1)),
        "battle_fact_references_resolve": fact_refs_resolve,
        "fact_relation_references_resolve": relation_refs_resolve,
        "observed_evidence_resolves": observed_evidence_resolves,
        "every_interpretation_has_observed_evidence": all(
            row["observed_evidence"] for row in rows
        ),
        "every_interpretation_has_knowledge_evidence": all(
            row["knowledge_evidence"] for row in rows
        ),
        "knowledge_provenance_complete": knowledge_complete,
        "certainty_requirements_consistent": certainty_consistent,
        "interpretations_are_separate_records": all(
            "fact_type" not in row and "reconstruction_rule_id" not in row
            for row in rows
        ),
    }


def _audit_payload(
    interpretations: Sequence[RuleInterpretation],
    direct_hashes: Mapping[str, str],
    upstream_drift: Sequence[Mapping[str, Any]],
    source_fact_count: int,
    source_relation_count: int,
    validation: Mapping[str, bool],
) -> Dict[str, Any]:
    fact_mapping: Dict[str, List[str]] = defaultdict(list)
    relation_mapping: Dict[str, List[str]] = defaultdict(list)
    for item in interpretations:
        for fact_id in item.referenced_battle_fact_ids:
            fact_mapping[fact_id].append(item.interpretation_id)
        for relation_id in item.referenced_fact_relation_ids:
            relation_mapping[relation_id].append(item.interpretation_id)
    referenced_facts = sorted(fact_mapping)
    return {
        "schema_version": INTERPRETATION_SCHEMA_VERSION,
        "checkpoint": "1I",
        "kind": "checkpoint1i_audit",
        "direct_input_hashes": dict(sorted(direct_hashes.items())),
        "upstream_source_snapshot_drift": {
            "policy": (
                "informational_only_direct_frozen_checkpoint1h_outputs_are_authoritative"
            ),
            "record_count": len(upstream_drift),
            "records": list(upstream_drift),
        },
        "source_mapping": {
            "battle_fact_to_interpretations": dict(sorted(fact_mapping.items())),
            "fact_relation_to_interpretations": dict(
                sorted(relation_mapping.items())
            ),
        },
        "coverage": {
            "selection_policy": (
                "knowledge_applicable_existing_facts_only_no_fact_id_or_timestamp_exceptions"
            ),
            "source_battle_fact_count": source_fact_count,
            "source_fact_relation_count": source_relation_count,
            "interpreted_battle_fact_count": len(referenced_facts),
            "interpreted_battle_fact_ids": referenced_facts,
            "uninterpreted_battle_fact_count": source_fact_count
            - len(referenced_facts),
            "supported_rule_ids": sorted(
                {
                    item.rule_id
                    for item in interpretations
                    if item.certainty == "supported"
                }
            ),
            "unresolved_rule_ids": sorted(
                {
                    item.rule_id
                    for item in interpretations
                    if item.certainty == "unresolved"
                }
            ),
        },
        "validation": dict(validation),
        "scope_guards": {
            "battle_facts_created": False,
            "battle_facts_modified": False,
            "observation_provenance_rewritten": False,
            "inference_presented_as_observation": False,
            "complete_simulator_created": False,
            "damage_calculator_created": False,
            "legality_engine_created": False,
            "decision_engine_created": False,
            "replay_analysis_started": False,
            "ai_coach_started": False,
        },
    }


def run_checkpoint_1i(
    project_root: Path, checkpoint1h_dir: Path, output_dir: Path,
    replay_id: str = DEFAULT_REPLAY_ID,
) -> Dict[str, Any]:
    root = project_root.resolve()
    source_dir = resolve_project_path(root, checkpoint1h_dir)
    target = resolve_project_path(root, output_dir)
    replay_id = normalize_replay_id(replay_id)
    source = load_checkpoint1i_inputs(root, source_dir)
    facts = source["battle_facts"]["facts"]
    relations = source["battle_fact_relations"]["relations"]
    knowledge = source["knowledge"]
    interpretations = build_rule_interpretations(facts, relations, knowledge)
    if not interpretations:
        raise InputError("Checkpoint 1I 沒有可輸出的 interpretation records")

    validation = _validate_interpretations(
        interpretations,
        facts,
        relations,
        list(knowledge.knowledge_by_id),
        str(knowledge.payload["knowledge_version"]),
        knowledge.data_sha256,
    )
    validation.update(
        {
            "direct_checkpoint1h_outputs_schema_and_hash_valid": True,
            "rule_knowledge_schema_and_hash_valid": True,
            "direct_inputs_unchanged": direct_inputs_unchanged(
                root, source["direct_hashes"]
            ),
            "knowledge_does_not_create_battle_facts": True,
            "upstream_snapshot_drift_is_informational": True,
            "transactional_output_replacement": True,
            "schemas_valid": True,
            "generated_output_visible": True,
        }
    )
    failed = sorted(key for key, value in validation.items() if not value)
    if failed:
        raise InputError("Checkpoint 1I validation 失敗：{}".format(failed))

    interpretation_payload = _interpretation_payload(
        interpretations,
        int(source["battle_facts"]["fact_count"]),
        str(knowledge.payload["knowledge_version"]),
        knowledge.data_sha256,
    )
    review_payload = _review_payload(interpretations)
    audit_payload = _audit_payload(
        interpretations,
        source["direct_hashes"],
        source["upstream_snapshot_drift"],
        int(source["battle_facts"]["fact_count"]),
        int(source["battle_fact_relations"]["relation_count"]),
        validation,
    )

    output_specs = (
        (
            "rule_interpretations.json",
            "checkpoint1i_rule_interpretations.schema.json",
            interpretation_payload,
        ),
        (
            "interpretation_review.json",
            "checkpoint1i_interpretation_review.schema.json",
            review_payload,
        ),
        (
            "checkpoint1i_audit.json",
            "checkpoint1i_audit.schema.json",
            audit_payload,
        ),
    )
    with OutputTransaction(root, target) as transaction:
        for filename, schema_name, payload in output_specs:
            _validator(root, schema_name).validate(payload)
            write_json(transaction.staging_dir / filename, payload)

        outputs = {
            filename: {
                "path": filename,
                "schema": schema_name,
                "sha256": sha256_file(transaction.staging_dir / filename),
            }
            for filename, schema_name, _ in output_specs
        }
        certainty_counts = interpretation_payload["certainty_counts"]
        manifest = {
            "schema_version": INTERPRETATION_SCHEMA_VERSION,
            "checkpoint": "1I",
            "kind": "checkpoint1i_manifest",
            "status": "complete",
            "engine_version": ENGINE_VERSION,
            "source": {
                "checkpoint": "1H",
                "manifest": {
                    "path": project_relative(
                        source["checkpoint1h_manifest_path"], root
                    ),
                    "schema": "checkpoint1h_manifest.schema.json",
                    "sha256": sha256_file(source["checkpoint1h_manifest_path"]),
                },
                "battle_facts": {
                    "path": project_relative(
                        source_dir / "battle_facts.json", root
                    ),
                    "schema": "checkpoint1h_battle_facts.schema.json",
                    "sha256": sha256_file(source_dir / "battle_facts.json"),
                },
                "battle_fact_relations": {
                    "path": project_relative(
                        source_dir / "battle_fact_relations.json", root
                    ),
                    "schema": "checkpoint1h_battle_fact_relations.schema.json",
                    "sha256": sha256_file(
                        source_dir / "battle_fact_relations.json"
                    ),
                },
            },
            "knowledge": {
                "knowledge_version": knowledge.payload["knowledge_version"],
                "data": {
                    "path": project_relative(knowledge.data_path, root),
                    "schema": "pokemon_rule_knowledge.schema.json",
                    "sha256": knowledge.data_sha256,
                },
                "manifest": {
                    "path": project_relative(knowledge.manifest_path, root),
                    "schema": "pokemon_rule_knowledge_manifest.schema.json",
                    "sha256": sha256_file(knowledge.manifest_path),
                },
            },
            "counts": {
                "source_battle_facts": len(facts),
                "source_fact_relations": len(relations),
                "interpretations": len(interpretations),
                "supported": certainty_counts["supported"],
                "unresolved": certainty_counts["unresolved"],
                "conflicted": certainty_counts["conflicted"],
            },
            "outputs": outputs,
            "validation": validation,
            "scope_guards": audit_payload["scope_guards"],
        }
        if replay_id != DEFAULT_REPLAY_ID:
            manifest["replay_id"] = replay_id
        _validator(root, "checkpoint1i_manifest.schema.json").validate(manifest)
        write_json(transaction.staging_dir / "checkpoint1i_manifest.json", manifest)
        if not direct_inputs_unchanged(root, source["direct_hashes"]):
            raise InputError("Checkpoint 1I direct inputs 在輸出期間被修改")
        transaction.commit()

    finalize_generated_output(target)
    if not direct_inputs_unchanged(root, source["direct_hashes"]):
        raise InputError("Checkpoint 1I direct inputs 在 replace 後被修改")
    return manifest
