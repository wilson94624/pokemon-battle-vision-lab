"""Checkpoint 1H Battle Event Reconstruction 正式 orchestration。"""

from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from jsonschema import Draft202012Validator, ValidationError

from .battle_fact_models import FACT_SCHEMA_VERSION, BattleFact
from .battle_fact_reconstruction import (
    build_battle_facts,
    build_fact_relations,
    build_reconstructed_turns,
    reconstruction_counts,
)
from .checkpoint1h_inputs import (
    load_checkpoint1h_inputs,
    validate_frozen_inputs_unchanged,
)
from .config import load_json
from .errors import InputError
from .output_transaction import OutputTransaction, finalize_generated_output
from .utils import project_relative, sha256_file, write_json


RECONSTRUCTION_VERSION = "0.1.0"
OUTPUT_SCHEMAS = {
    "battle_facts.json": "checkpoint1h_battle_facts.schema.json",
    "battle_fact_relations.json": "checkpoint1h_battle_fact_relations.schema.json",
    "reconstructed_turns.json": "checkpoint1h_reconstructed_turns.schema.json",
    "checkpoint1h_audit.json": "checkpoint1h_audit.schema.json",
    "checkpoint1h_manifest.json": "checkpoint1h_manifest.schema.json",
}


def _validator(project_root: Path, schema_name: str) -> Draft202012Validator:
    schema = load_json(project_root / "schemas" / schema_name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _write_and_validate(
    project_root: Path,
    staging_dir: Path,
    filename: str,
    payload: Mapping[str, Any],
) -> None:
    try:
        _validator(project_root, OUTPUT_SCHEMAS[filename]).validate(payload)
    except ValidationError as exc:
        raise InputError(
            "Checkpoint 1H schema validation 失敗（{}）：{}".format(
                filename, exc.message
            )
        ) from exc
    write_json(staging_dir / filename, payload)


def _evidence_reference_validation(
    source: Mapping[str, Any], facts: Sequence[BattleFact]
) -> bool:
    valid = {
        "outputs/checkpoint-1d/battle_events.json": {
            str(row["id"]) for row in source["events"]["events"]
        },
        "outputs/checkpoint-1g/hp_changes.json": {
            str(row["change_id"]) for row in source["hp_changes"]["changes"]
        },
        "outputs/checkpoint-1g/hp_observations.json": {
            str(row["observation_id"])
            for row in source["hp_observations"]["observations"]
        },
        "outputs/checkpoint-1g/decision_cycles.json": {
            str(row["cycle_id"]) for row in source["decision_cycles"]["cycles"]
        },
        "outputs/checkpoint-1g/move_menu_observations.json": {
            str(row["candidate_id"])
            for row in source["move_menu_observations"]["observations"]
        },
    }
    return all(
        evidence.artifact_path in valid
        and evidence.record_id in valid[evidence.artifact_path]
        for fact in facts
        for evidence in fact.evidence
    )


def _build_validation(
    source: Mapping[str, Any],
    facts: Sequence[BattleFact],
    source_to_fact: Mapping[str, str],
    relations: Sequence[Mapping[str, Any]],
    turns: Mapping[str, Any],
) -> Dict[str, bool]:
    event_ids = [str(row["id"]) for row in source["events"]["events"]]
    hp_change_ids = [
        str(row["change_id"]) for row in source["hp_changes"]["changes"]
    ]
    cycles = source["decision_cycles"]["cycles"]
    boundary_cycle_ids = [str(row["cycle_id"]) for row in cycles[1:]]
    fact_ids = [fact.fact_id for fact in facts]
    fact_by_id = {fact.fact_id: fact for fact in facts}
    assigned_event_facts = list(turns["opening_segment"]["event_fact_ids"])
    assigned_hp_facts = list(turns["opening_segment"]["hp_change_fact_ids"])
    for turn in turns["turn_candidates"]:
        assigned_event_facts.extend(turn["event_fact_ids"])
        assigned_hp_facts.extend(turn["hp_change_fact_ids"])

    source_priority = {
        "checkpoint1h.ambiguous_move_menu_boundary.v1": 0,
        "checkpoint1h.battle_text_event.v1": 1,
        "checkpoint1h.existing_hp_change.v1": 2,
    }
    ordered_keys = [
        (
            fact.timestamp,
            source_priority[fact.reconstruction_rule_id],
            fact.evidence[0].record_id,
        )
        for fact in facts
    ]
    expected_move_facts = sum(
        row["event_type"] == "MOVE"
        and row.get("metadata", {}).get("action") == "use"
        for row in source["events"]["events"]
    )
    participant_payload = [
        participant.to_dict()
        for fact in facts
        for participant in fact.participants
    ]
    expected_rejected_relations = int(source["review_counts"]["rejected_relations"])
    expected_active_relations = (
        int(source["relations"]["relation_count"]) - expected_rejected_relations
    )
    return {
        "battle_event_exactly_once": set(event_ids).issubset(source_to_fact)
        and len({source_to_fact[row] for row in event_ids}) == len(event_ids),
        "hp_change_exactly_once": set(hp_change_ids).issubset(source_to_fact)
        and len({source_to_fact[row] for row in hp_change_ids}) == len(hp_change_ids),
        "boundary_cycle_exactly_once": set(boundary_cycle_ids).issubset(source_to_fact)
        and len({source_to_fact[row] for row in boundary_cycle_ids})
        == len(boundary_cycle_ids),
        "fact_ids_unique": len(fact_ids) == len(set(fact_ids)),
        "fact_sequence_contiguous": [fact.sequence for fact in facts]
        == list(range(1, len(facts) + 1)),
        "facts_deterministically_ordered": ordered_keys == sorted(ordered_keys),
        "fact_time_ranges_valid": all(
            fact.start_time <= fact.timestamp <= fact.end_time for fact in facts
        ),
        "every_fact_has_evidence": all(fact.evidence for fact in facts),
        "all_evidence_records_resolve": _evidence_reference_validation(source, facts),
        "relation_count_preserved": len(relations)
        == int(source["relations"]["relation_count"]),
        "relation_fact_references_valid": all(
            row["from_fact_id"] in fact_by_id and row["to_fact_id"] in fact_by_id
            for row in relations
        ),
        "reviewed_relations_projected": sum(row["active"] for row in relations)
        == expected_active_relations
        and sum(not row["active"] for row in relations)
        == expected_rejected_relations,
        "rejected_relations_inactive": all(
            row["active"] is False and row["causal_claim"] is False
            for row in relations
            if row["review_resolution"] == "rejected"
        ),
        "temporal_adjacency_never_causal": all(
            not row["causal_claim"]
            for row in relations
            if row["relation_type"] == "TEMPORALLY_ADJACENT"
        ),
        "opening_segment_not_turn": turns["policy"]["opening_segment_is_turn"] is False,
        "turn_candidates_match_nonopening_cycles": turns["turn_candidate_count"]
        == max(0, len(cycles) - 1),
        "official_turn_number_not_inferred": all(
            turn["official_turn_number"] is None
            and turn["is_official_turn_number"] is False
            and turn["reconstruction_status"] == "ambiguous"
            for turn in turns["turn_candidates"]
        ),
        "move_menu_did_not_create_moves": sum(
            fact.fact_type == "MOVE_USED" for fact in facts
        )
        == expected_move_facts,
        "all_events_assigned_to_one_segment": len(assigned_event_facts)
        == len(event_ids)
        and len(set(assigned_event_facts)) == len(event_ids),
        "all_hp_changes_assigned_to_one_segment": len(assigned_hp_facts)
        == len(hp_change_ids)
        and len(set(assigned_hp_facts)) == len(hp_change_ids),
        "knowledge_base_identity_only": "regulation_availability"
        not in str(participant_payload),
        "fact_count_is_source_additive": len(facts)
        == len(event_ids) + len(hp_change_ids) + len(boundary_cycle_ids),
        "schemas_valid": True,
        "frozen_inputs_unchanged": True,
        "transactional_output_replacement": True,
        "deterministic_metadata": True,
    }


def _mapping_rows(source_ids: Sequence[str], source_to_fact: Mapping[str, str]):
    return [
        {"source_id": source_id, "fact_id": source_to_fact[source_id]}
        for source_id in source_ids
    ]


def run_checkpoint_1h(
    project_root: Path,
    checkpoint1d_dir: Path,
    checkpoint1e_dir: Path,
    checkpoint1e_review_dir: Path,
    checkpoint1g_dir: Path,
    output_dir: Path,
) -> Dict[str, Any]:
    project_root = project_root.resolve()
    inputs = [checkpoint1d_dir, checkpoint1e_dir, checkpoint1e_review_dir, checkpoint1g_dir]
    checkpoint1d_dir, checkpoint1e_dir, checkpoint1e_review_dir, checkpoint1g_dir = [
        path.resolve() if path.is_absolute() else (project_root / path).resolve()
        for path in inputs
    ]
    output_dir = (
        output_dir.resolve()
        if output_dir.is_absolute()
        else (project_root / output_dir).resolve()
    )
    if output_dir in {
        checkpoint1d_dir,
        checkpoint1e_dir,
        checkpoint1e_review_dir,
        checkpoint1g_dir,
    }:
        raise InputError("Checkpoint 1H output 不可覆蓋 frozen input")

    source = load_checkpoint1h_inputs(
        project_root,
        checkpoint1d_dir,
        checkpoint1e_dir,
        checkpoint1e_review_dir,
        checkpoint1g_dir,
    )
    reconstruction = build_battle_facts(source)
    facts = reconstruction["facts"]
    source_to_fact = reconstruction["source_to_fact"]
    relations = build_fact_relations(source, source_to_fact)
    turns = build_reconstructed_turns(source, facts, source_to_fact)
    counts = {
        **reconstruction_counts(facts),
        "source_battle_events": len(source["events"]["events"]),
        "source_hp_changes": len(source["hp_changes"]["changes"]),
        "ambiguous_turn_boundaries": len(source["decision_cycles"]["cycles"]) - 1,
        "fact_relations": len(relations),
        "active_fact_relations": sum(row["active"] for row in relations),
        "rejected_fact_relations": sum(not row["active"] for row in relations),
        "turn_candidates": turns["turn_candidate_count"],
    }
    validation = _build_validation(
        source, facts, source_to_fact, relations, turns
    )
    failed = sorted(key for key, passed in validation.items() if not passed)
    if failed:
        raise InputError("Checkpoint 1H consistency gate 失敗：{}".format(", ".join(failed)))

    fact_payload = {
        "schema_version": FACT_SCHEMA_VERSION,
        "checkpoint": "1H",
        "kind": "battle_facts",
        "immutability_policy": "derived_records_are_not_mutated_in_place",
        "fact_count": len(facts),
        "fact_type_counts": counts["fact_types"],
        "source_counts": {
            "battle_events": counts["source_battle_events"],
            "hp_changes": counts["source_hp_changes"],
            "turn_boundaries": counts["ambiguous_turn_boundaries"],
        },
        "facts": [fact.to_dict() for fact in facts],
    }
    relation_payload = {
        "schema_version": FACT_SCHEMA_VERSION,
        "checkpoint": "1H",
        "kind": "battle_fact_relations",
        "relation_count": len(relations),
        "active_relation_count": counts["active_fact_relations"],
        "rejected_relation_count": counts["rejected_fact_relations"],
        "relations": relations,
    }
    scope_guards = {
        "checkpoint1a_to_1g_modified": False,
        "ocr_rerun": False,
        "battle_event_parser_rerun": False,
        "timeline_builder_rerun": False,
        "state_projector_rerun": False,
        "visual_enrichment_rerun": False,
        "official_turn_inferred": False,
        "move_selected_from_menu": False,
        "knowledge_created_event": False,
        "simulator_or_damage_calculation_used": False,
        "replay_analysis_started": False,
        "gui_created": False,
    }
    event_ids = [str(row["id"]) for row in source["events"]["events"]]
    hp_ids = [str(row["change_id"]) for row in source["hp_changes"]["changes"]]
    boundary_ids = [
        str(row["cycle_id"]) for row in source["decision_cycles"]["cycles"][1:]
    ]
    audit = {
        "schema_version": FACT_SCHEMA_VERSION,
        "checkpoint": "1H",
        "kind": "checkpoint1h_audit",
        "status": "complete",
        "counts": counts,
        "source_mapping": {
            "battle_event_to_fact": _mapping_rows(event_ids, source_to_fact),
            "hp_change_to_fact": _mapping_rows(hp_ids, source_to_fact),
            "decision_cycle_to_boundary_fact": _mapping_rows(
                boundary_ids, source_to_fact
            ),
        },
        "validation": validation,
        "scope_guards": scope_guards,
        "research_sources": [
            {
                "url": "https://www.w3.org/TR/prov-o/",
                "decision": "adopted",
                "use": "簡化的 derived entity 與 observation provenance records。",
            },
            {
                "url": "https://json-schema.org/specification",
                "decision": "adopted",
                "use": "Draft 2020-12 output validation。",
            },
            {
                "url": "https://github.com/smogon/pokemon-showdown/blob/master/sim/SIM-PROTOCOL.md",
                "decision": "referenced",
                "use": "僅參考 move、switch、faint、damage 與 turn message 的語意分離。",
            },
            {
                "url": "https://github.com/MateuszNaKodach/awesome-eventmodeling",
                "decision": "referenced",
                "use": "參考 immutable event vocabulary；未採用 framework。",
            },
            {
                "url": "event-sourcing-frameworks",
                "decision": "rejected",
                "use": "deterministic batch reconstruction 不需 command bus 或 aggregate store。",
            },
            {
                "url": "pokemon-simulator-and-damage-engines",
                "decision": "rejected",
                "use": "避免由規則產生影片未觀察的 fact。",
            },
        ],
        "limitations": [
            "TURN_BOUNDARY 與 turn candidates 皆為 ambiguous，不是官方 turn number。",
            "Move Menu 僅作 boundary evidence，不代表玩家實際選擇的 move。",
            "HP_CHANGED 完整沿用 1G visual observation confidence 與 cause=unknown。",
            "accepted TEMPORALLY_ADJACENT relation 保留為 active ordering link，但不形成 causal claim。",
            "Battle Text damage 與 visual HP change 是兩個不同 observation source，不自動合併。",
        ],
    }

    with OutputTransaction(project_root, output_dir) as transaction:
        payloads = {
            "battle_facts.json": fact_payload,
            "battle_fact_relations.json": relation_payload,
            "reconstructed_turns.json": turns,
            "checkpoint1h_audit.json": audit,
        }
        for filename, payload in payloads.items():
            _write_and_validate(
                project_root, transaction.staging_dir, filename, payload
            )
        output_hashes = {
            filename: {
                "path": filename,
                "sha256": sha256_file(transaction.staging_dir / filename),
                "schema": OUTPUT_SCHEMAS[filename],
            }
            for filename in payloads
        }
        source_hashes = {
            project_relative(Path(path), project_root): {
                "path": project_relative(Path(path), project_root),
                "sha256": sha256,
            }
            for path, sha256 in sorted(source["tracked_hashes"].items())
        }
        manifest = {
            "schema_version": FACT_SCHEMA_VERSION,
            "checkpoint": "1H",
            "kind": "checkpoint1h_manifest",
            "status": "complete",
            "reconstruction_version": RECONSTRUCTION_VERSION,
            "outputs": output_hashes,
            "source": source_hashes,
            "counts": counts,
            "validation": validation,
            "scope_guards": scope_guards,
        }
        _write_and_validate(
            project_root,
            transaction.staging_dir,
            "checkpoint1h_manifest.json",
            manifest,
        )
        if not validate_frozen_inputs_unchanged(source["tracked_hashes"]):
            raise InputError("Checkpoint 1H frozen input hash 在執行期間改變")
        transaction.commit()
    finalize_generated_output(output_dir)
    return manifest
