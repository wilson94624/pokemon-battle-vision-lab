"""Checkpoint 1J orchestration：Interpretation Review 與 evidence-backed rule coverage。"""

from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from jsonschema import Draft202012Validator

from .checkpoint1j_inputs import direct_inputs_unchanged, load_checkpoint1j_inputs
from .config import load_json
from .errors import InputError
from .interpretation_review import (
    apply_review_decisions,
    build_review_records,
    review_summary,
    validate_review_records,
    write_review_pack,
)
from .interpretation_review_models import (
    CONFLICT_CATEGORIES,
    REVIEW_SCHEMA_VERSION,
)
from .output_transaction import OutputTransaction, finalize_generated_output
from .rule_coverage import (
    EXPANDED_INTERPRETATION_VERSION,
    build_expanded_rule_interpretations,
    build_rule_coverage_audit,
)
from .utils import project_relative, sha256_file, write_json
from .replay import DEFAULT_REPLAY_ID, normalize_replay_id, resolve_project_path


ENGINE_VERSION = "0.1.0"


def _validator(project_root: Path, schema_name: str) -> Draft202012Validator:
    schema = load_json(project_root / "schemas" / schema_name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _counts(values: Sequence[str], keys: Sequence[str]) -> Dict[str, int]:
    counts = Counter(values)
    return {key: int(counts.get(key, 0)) for key in keys}


def _expanded_payload(
    interpretations,
    knowledge_version: str,
    knowledge_sha256: str,
) -> Dict[str, Any]:
    rows = [row.to_dict() for row in interpretations]
    types = sorted({str(row["interpretation_type"]) for row in rows})
    return {
        "schema_version": EXPANDED_INTERPRETATION_VERSION,
        "checkpoint": "1J",
        "kind": "expanded_rule_interpretations",
        "interpretation_version": EXPANDED_INTERPRETATION_VERSION,
        "immutability_policy": "new_v2_interpretations_are_separate_existing_v1_interpretations_remain_unchanged",
        "knowledge_version": knowledge_version,
        "knowledge_sha256": knowledge_sha256,
        "interpretation_count": len(rows),
        "certainty_counts": _counts(
            [str(row["certainty"]) for row in rows],
            ("supported", "unresolved", "conflicted"),
        ),
        "interpretation_type_counts": _counts(
            [str(row["interpretation_type"]) for row in rows], types
        ),
        "interpretations": rows,
    }


def _combined_interpretations(
    existing_payload: Mapping[str, Any],
    expanded_payload: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    combined = [
        {
            "origin_checkpoint": "1I",
            "interpretation_version": existing_payload["interpretation_version"],
            "knowledge_version": existing_payload["knowledge_version"],
            "payload": row,
        }
        for row in existing_payload["interpretations"]
    ]
    combined.extend(
        {
            "origin_checkpoint": "1J",
            "interpretation_version": expanded_payload["interpretation_version"],
            "knowledge_version": expanded_payload["knowledge_version"],
            "payload": row,
        }
        for row in expanded_payload["interpretations"]
    )
    return combined


def _validate_expanded(
    payload: Mapping[str, Any],
    facts: Sequence[Mapping[str, Any]],
    relations: Sequence[Mapping[str, Any]],
    knowledge_sha256: str,
) -> Dict[str, bool]:
    rows = payload["interpretations"]
    fact_ids = {str(row["fact_id"]) for row in facts}
    relation_by_id = {
        str(row["fact_relation_id"]): row for row in relations
    }
    ids = [str(row["interpretation_id"]) for row in rows]
    temporal_causality_safe = True
    for row in rows:
        causal_claim = bool(row["conclusion"]["derived_values"]["causal_claim"])
        for relation_id in row["referenced_fact_relation_ids"]:
            relation = relation_by_id[relation_id]
            if relation["relation_type"] == "TEMPORALLY_ADJACENT":
                temporal_causality_safe &= not causal_claim
    return {
        "expanded_interpretation_ids_unique": len(ids) == len(set(ids)),
        "expanded_sequences_contiguous": [row["sequence"] for row in rows]
        == list(range(1, len(rows) + 1)),
        "expanded_fact_references_resolve": all(
            fact_id in fact_ids
            for row in rows
            for fact_id in row["referenced_battle_fact_ids"]
        ),
        "expanded_relation_references_resolve": all(
            relation_id in relation_by_id
            for row in rows
            for relation_id in row["referenced_fact_relation_ids"]
        ),
        "expanded_knowledge_hashes_match": all(
            evidence["knowledge_sha256"] == knowledge_sha256
            for row in rows
            for evidence in row["knowledge_evidence"]
        ),
        "all_expanded_requirements_satisfied": all(
            requirement["status"] == "satisfied"
            for row in rows
            for requirement in row["required_observations"]
        ),
        "temporal_adjacency_never_promoted_to_causality": bool(
            temporal_causality_safe
        ),
        "expanded_interpretations_do_not_embed_battle_facts": all(
            "fact_type" not in row for row in rows
        ),
    }


def _conflict_policy(records) -> Dict[str, Any]:
    conflicted = [record for record in records if record.certainty == "conflicted"]
    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "checkpoint": "1J",
        "kind": "conflict_review_policy",
        "policy": "classify_conflict_without_overwriting_observation_or_knowledge_expectation",
        "categories": list(CONFLICT_CATEGORIES),
        "required_preserved_fields": [
            "observed_conclusion",
            "knowledge_derived_expectation",
            "conflicting_fields",
            "evidence_references",
            "review_status_and_conflict_category",
        ],
        "review_rule": (
            "reviewer may classify or defer but must never overwrite the immutable "
            "interpretation conclusion, certainty, observed evidence, or knowledge evidence"
        ),
        "production_conflicted_count": len(conflicted),
        "production_conflict_review_ids": [
            record.review_record_id for record in conflicted
        ],
    }


def _file_ref(root: Path, path: Path, schema: str) -> Dict[str, str]:
    return {
        "path": project_relative(path, root),
        "schema": schema,
        "sha256": sha256_file(path),
    }


def run_checkpoint_1j(
    project_root: Path,
    checkpoint1h_dir: Path,
    checkpoint1i_dir: Path,
    output_dir: Path,
    review_decisions_path: Optional[Path] = None,
    checkpoint1g_dir: Optional[Path] = None,
    replay_id: str = DEFAULT_REPLAY_ID,
) -> Dict[str, Any]:
    root = project_root.resolve()
    target = resolve_project_path(root, output_dir)
    replay_id = normalize_replay_id(replay_id)
    source = load_checkpoint1j_inputs(
        root,
        checkpoint1h_dir,
        checkpoint1i_dir,
        review_decisions_path,
        checkpoint1g_dir,
        replay_id,
    )
    facts = source["battle_facts"]["facts"]
    relations = source["battle_fact_relations"]["relations"]
    v2 = source["v2_knowledge"]
    expanded = build_expanded_rule_interpretations(facts, relations, v2)
    if not expanded:
        raise InputError("Checkpoint 1J 沒有 evidence-supported expanded interpretations")
    expanded_payload = _expanded_payload(
        expanded,
        str(v2.payload["knowledge_version"]),
        v2.data_sha256,
    )
    combined = _combined_interpretations(
        source["existing_interpretations"], expanded_payload
    )
    records = apply_review_decisions(
        build_review_records(combined), review_decisions_path
    )
    review_validation = validate_review_records(records, combined)
    expanded_validation = _validate_expanded(
        expanded_payload, facts, relations, v2.data_sha256
    )
    failed = sorted(
        key
        for key, value in {**review_validation, **expanded_validation}.items()
        if not value
    )
    if failed:
        raise InputError("Checkpoint 1J interpretation validation 失敗：{}".format(failed))

    review_payload = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "checkpoint": "1J",
        "kind": "interpretation_review_records",
        "review_schema_version": REVIEW_SCHEMA_VERSION,
        "record_count": len(records),
        "review_policy": "human_decisions_are_separate_and_never_rewrite_interpretation_payloads",
        "records": [record.to_dict() for record in records],
    }
    summary_payload, statistics_payload = review_summary(records)
    conflict_payload = _conflict_policy(records)
    coverage_payload = build_rule_coverage_audit(
        facts,
        relations,
        source["existing_interpretations"]["interpretations"],
        expanded,
    )
    drift_payload = source["historical_drift_audit"]
    validation = {
        **source["migration_validation"],
        **review_validation,
        **expanded_validation,
        "checkpoint1i_interpretations_immutable": True,
        "checkpoint1h_battle_facts_immutable": True,
        "checkpoint1h_fact_relations_immutable": True,
        "review_records_separate_from_interpretations": True,
        "direct_inputs_unchanged": direct_inputs_unchanged(
            root, source["direct_hashes"]
        ),
        "historical_drift_exactly_approved": all(
            drift_payload["validation"].values()
        ),
        "schemas_valid": True,
        "transactional_output_replacement": True,
        "generated_output_visible": True,
    }
    if not all(validation.values()):
        raise InputError("Checkpoint 1J pre-output validation 失敗")
    audit_payload = {
        "schema_version": "0.1.0",
        "checkpoint": "1J",
        "kind": "checkpoint1j_audit",
        "direct_input_hashes": dict(sorted(source["direct_hashes"].items())),
        "immutability": {
            "battle_facts_unchanged": True,
            "battle_fact_relations_unchanged": True,
            "checkpoint1i_interpretations_unchanged": True,
            "checkpoint1i_observation_provenance_unchanged": True,
            "checkpoint1i_knowledge_provenance_unchanged": True,
            "v1_knowledge_unchanged": True,
        },
        "counts": {
            "source_battle_facts": len(facts),
            "source_fact_relations": len(relations),
            "existing_interpretations": len(
                source["existing_interpretations"]["interpretations"]
            ),
            "expanded_interpretations": len(expanded),
            "review_records": len(records),
            "approved_historical_drift_records": drift_payload[
                "approved_drift_count"
            ],
        },
        "validation": validation,
        "scope_guards": {
            "battle_facts_created": False,
            "battle_facts_modified": False,
            "rule_interpretations_modified": False,
            "observation_provenance_modified": False,
            "knowledge_provenance_modified": False,
            "complete_simulator_created": False,
            "hidden_information_inferred": False,
            "replay_analysis_started": False,
            "gui_created": False,
        },
    }

    specs = (
        (
            "expanded_rule_interpretations.json",
            "checkpoint1j_expanded_interpretations.schema.json",
            expanded_payload,
        ),
        (
            "interpretation_review_records.json",
            "checkpoint1j_interpretation_reviews.schema.json",
            review_payload,
        ),
        (
            "review_summary.json",
            "checkpoint1j_review_summary.schema.json",
            summary_payload,
        ),
        (
            "review_statistics.json",
            "checkpoint1j_review_statistics.schema.json",
            statistics_payload,
        ),
        (
            "conflict_review_policy.json",
            "checkpoint1j_conflict_policy.schema.json",
            conflict_payload,
        ),
        (
            "rule_coverage_audit.json",
            "checkpoint1j_rule_coverage_audit.schema.json",
            coverage_payload,
        ),
        (
            "historical_snapshot_drift_audit.json",
            "checkpoint1j_historical_drift.schema.json",
            drift_payload,
        ),
        (
            "checkpoint1j_audit.json",
            "checkpoint1j_audit.schema.json",
            audit_payload,
        ),
    )
    with OutputTransaction(root, target) as transaction:
        for filename, schema_name, payload in specs:
            _validator(root, schema_name).validate(payload)
            write_json(transaction.staging_dir / filename, payload)
        review_files = write_review_pack(
            transaction.staging_dir,
            records,
            combined,
            facts,
            relations,
        )
        outputs = {
            filename: {
                "path": filename,
                "schema": schema_name,
                "sha256": sha256_file(transaction.staging_dir / filename),
            }
            for filename, schema_name, _ in specs
        }
        cards = sorted(
            path for path in review_files if path.parent.name == "cards"
        )
        index = transaction.staging_dir / "review_pack/review_index.md"
        worksheet = transaction.staging_dir / "review_pack/review_worksheet.csv"
        manifest = {
            "schema_version": "0.1.0",
            "checkpoint": "1J",
            "kind": "checkpoint1j_manifest",
            "status": (
                "complete"
                if statistics_payload["human_review_complete"]
                else "complete_pending_human_review"
            ),
            "engine_version": ENGINE_VERSION,
            "source": {
                "checkpoint1i_manifest": _file_ref(
                    root,
                    source["checkpoint1i_manifest_path"],
                    "checkpoint1i_manifest.schema.json",
                ),
                "checkpoint1i_interpretations": _file_ref(
                    root,
                    checkpoint1i_dir.resolve() / "rule_interpretations.json",
                    "checkpoint1i_rule_interpretations.schema.json",
                ),
                "checkpoint1h_battle_facts": _file_ref(
                    root,
                    checkpoint1h_dir.resolve() / "battle_facts.json",
                    "checkpoint1h_battle_facts.schema.json",
                ),
                "checkpoint1h_fact_relations": _file_ref(
                    root,
                    checkpoint1h_dir.resolve() / "battle_fact_relations.json",
                    "checkpoint1h_battle_fact_relations.schema.json",
                ),
            },
            "knowledge": {
                "v1_data": _file_ref(
                    root,
                    source["v1_knowledge"].data_path,
                    "pokemon_rule_knowledge.schema.json",
                ),
                "v1_manifest": _file_ref(
                    root,
                    source["v1_knowledge"].manifest_path,
                    "pokemon_rule_knowledge_manifest.schema.json",
                ),
                "v2_data": _file_ref(
                    root,
                    v2.data_path,
                    "pokemon_rule_knowledge_v2.schema.json",
                ),
                "v2_manifest": _file_ref(
                    root,
                    v2.manifest_path,
                    "pokemon_rule_knowledge_manifest_v2.schema.json",
                ),
            },
            "counts": {
                "source_battle_facts": len(facts),
                "source_fact_relations": len(relations),
                "existing_interpretations": len(
                    source["existing_interpretations"]["interpretations"]
                ),
                "expanded_interpretations": len(expanded),
                "review_records": len(records),
                "accepted_reviews": statistics_payload["accepted_count"],
                "rejected_reviews": statistics_payload["rejected_count"],
                "needs_review": statistics_payload["needs_review_count"],
                "deferred_reviews": statistics_payload["deferred_count"],
                "conflicted_interpretations": statistics_payload[
                    "conflicted_interpretation_count"
                ],
            },
            "outputs": outputs,
            "review_pack": {
                "index": {
                    "path": index.relative_to(transaction.staging_dir).as_posix(),
                    "sha256": sha256_file(index),
                },
                "worksheet": {
                    "path": worksheet.relative_to(
                        transaction.staging_dir
                    ).as_posix(),
                    "sha256": sha256_file(worksheet),
                },
                "card_count": len(cards),
                "cards": [
                    {
                        "path": path.relative_to(
                            transaction.staging_dir
                        ).as_posix(),
                        "sha256": sha256_file(path),
                    }
                    for path in cards
                ],
            },
            "validation": validation,
            "scope_guards": audit_payload["scope_guards"],
        }
        if replay_id != DEFAULT_REPLAY_ID:
            manifest["replay_id"] = replay_id
        _validator(root, "checkpoint1j_manifest.schema.json").validate(manifest)
        write_json(transaction.staging_dir / "checkpoint1j_manifest.json", manifest)
        if not direct_inputs_unchanged(root, source["direct_hashes"]):
            raise InputError("Checkpoint 1J direct inputs 在輸出期間被修改")
        transaction.commit()

    finalize_generated_output(target)
    if not direct_inputs_unchanged(root, source["direct_hashes"]):
        raise InputError("Checkpoint 1J direct inputs 在 replace 後被修改")
    return manifest
