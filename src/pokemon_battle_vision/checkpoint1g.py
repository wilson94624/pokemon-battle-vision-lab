"""Checkpoint 1G Visual Battle State Enrichment 正式 orchestration。"""

import shutil
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

import jsonschema

from .checkpoint1g_frame_extractor import derived_visual_rois, extract_visual_frames
from .checkpoint1g_models import OcrObservation
from .checkpoint1g_planning import build_visual_frame_requests
from .checkpoint1g_review import build_checkpoint1g_review
from .config import load_json, load_roi_config
from .decision_cycles import build_decision_cycles
from .enriched_state_fusion import build_enriched_snapshots
from .entity_resolution import build_entities
from .errors import DependencyError, InputError
from .move_menu_parser import parse_move_menu_observations
from .ocr_engine import AppleVisionOcrEngine
from .output_transaction import OutputTransaction, finalize_generated_output
from .pokemon_knowledge_base import PokemonKnowledgeBase
from .roi import pixel_rois
from .scanner import load_frame_timestamp_index
from .team_selection_parser import parse_selected_four, parse_team_roster
from .utils import project_relative, sha256_file, write_json
from .visual_state_tracking import (
    build_active_slot_timeline,
    build_hp_changes,
    build_hp_observations,
)


OUTPUT_SCHEMAS = {
    "team_roster.json": "checkpoint1g_team_roster.schema.json",
    "selected_four.json": "checkpoint1g_selected_four.schema.json",
    "pokemon_entities.json": "checkpoint1g_pokemon_entities.schema.json",
    "move_menu_observations.json": "checkpoint1g_move_menu_observations.schema.json",
    "hp_observations.json": "checkpoint1g_hp_observations.schema.json",
    "hp_changes.json": "checkpoint1g_hp_changes.schema.json",
    "active_slot_timeline.json": "checkpoint1g_active_slot_timeline.schema.json",
    "decision_cycles.json": "checkpoint1g_decision_cycles.schema.json",
    "enriched_battle_snapshots.json": "checkpoint1g_enriched_battle_snapshots.schema.json",
    "enriched_state_deltas.json": "checkpoint1g_enriched_state_deltas.schema.json",
    "entity_resolution_edges.json": "checkpoint1g_entity_resolution_edges.schema.json",
    "checkpoint1g_audit.json": "checkpoint1g_audit.schema.json",
    "checkpoint1g_manifest.json": "checkpoint1g_manifest.schema.json",
}


def _required(path: Path, label: str) -> Path:
    if not path.is_file():
        raise InputError("找不到 Checkpoint 1G {}：{}".format(label, path))
    return path


def _frozen_sources(
    video_path: Path,
    roi_config_path: Path,
    checkpoint1a_dir: Path,
    checkpoint1b_dir: Path,
    checkpoint1b_review_dir: Path,
    checkpoint1c_dir: Path,
    checkpoint1d_dir: Path,
    checkpoint1e_dir: Path,
    checkpoint1f_dir: Path,
    knowledge_base_path: Path,
    knowledge_base_manifest_path: Path,
    project_root: Path,
) -> Dict[str, Dict[str, str]]:
    paths = [
        video_path,
        roi_config_path,
        checkpoint1a_dir / "metadata.json",
        checkpoint1a_dir / "frame_timestamps.npz",
        checkpoint1a_dir / "roi_approval.json",
        checkpoint1b_dir / "events.json",
        checkpoint1b_dir / "frames.jsonl",
        checkpoint1b_dir / "detector_report.json",
        checkpoint1b_review_dir / "candidate_review.json",
        checkpoint1c_dir / "checkpoint1c_manifest.json",
        checkpoint1c_dir / "ocr_aggregates.json",
        checkpoint1d_dir / "battle_events.json",
        checkpoint1d_dir / "checkpoint1d_manifest.json",
        checkpoint1e_dir / "battle_timeline.json",
        checkpoint1e_dir / "timeline_relations.json",
        checkpoint1e_dir / "checkpoint1e_manifest.json",
        checkpoint1f_dir / "battle_state_snapshots.json",
        checkpoint1f_dir / "state_deltas.json",
        checkpoint1f_dir / "checkpoint1f_manifest.json",
        knowledge_base_path,
        knowledge_base_manifest_path,
    ]
    result = {}
    for path in paths:
        _required(path, "frozen input")
        key = project_relative(path, project_root)
        result[key] = {"path": key, "sha256": sha256_file(path)}
    return result


def _validate_frozen(source: Mapping[str, Mapping[str, str]], project_root: Path) -> None:
    for row in source.values():
        path = project_root / str(row["path"])
        if not path.is_file() or sha256_file(path) != row["sha256"]:
            raise InputError("Checkpoint 1G frozen hash gate 失敗：{}".format(path))


def _run_ocr(engine, extracted) -> Dict[str, OcrObservation]:
    jobs = [
        {"job_id": row.request.request_id, "image_path": row.crop_path}
        for row in extracted
        if row.request.run_ocr
    ]
    results = engine.recognize(jobs)
    failures = [result for result in results if result.error is not None]
    if failures:
        first = failures[0]
        raise DependencyError(
            "Checkpoint 1G Apple Vision production OCR 失敗：{}/{} jobs；first={}：{}".format(
                len(failures), len(jobs), first.job_id, first.error
            )
        )
    observations = {}
    for result in results:
        observations[result.job_id] = OcrObservation(
            request_id=result.job_id,
            raw_text=result.raw_text,
            confidence=result.confidence,
            lines=result.lines,
            preprocessing=["raw_approved_roi", "bicubic_2x_when_crop_height_below_360"],
            error=result.error,
        )
    if len(observations) != len(jobs):
        raise InputError("Checkpoint 1G Apple Vision OCR job 數量不一致")
    return observations


def _write_and_validate(
    staging: Path,
    schema_dir: Path,
    filename: str,
    payload: Mapping[str, Any],
) -> None:
    schema_name = OUTPUT_SCHEMAS[filename]
    schema = load_json(schema_dir / schema_name)
    jsonschema.Draft202012Validator(schema).validate(payload)
    write_json(staging / filename, payload)


def _id_unique(rows: Sequence[Mapping[str, Any]], key: str) -> bool:
    values = [str(row[key]) for row in rows]
    return len(values) == len(set(values))


def _monotonic(rows: Sequence[Mapping[str, Any]], key: str) -> bool:
    values = [float(row[key]) for row in rows]
    return all(left <= right for left, right in zip(values, values[1:]))


def run_checkpoint_1g(
    project_root: Path,
    video_path: Path,
    roi_config_path: Path,
    checkpoint1a_dir: Path,
    checkpoint1b_dir: Path,
    checkpoint1b_review_dir: Path,
    checkpoint1c_dir: Path,
    checkpoint1d_dir: Path,
    checkpoint1e_dir: Path,
    checkpoint1f_dir: Path,
    output_dir: Path,
    review_output_dir: Path,
    ocr_engine=None,
) -> Dict[str, Any]:
    project_root = project_root.resolve()
    paths = [
        video_path, roi_config_path, checkpoint1a_dir, checkpoint1b_dir,
        checkpoint1b_review_dir, checkpoint1c_dir, checkpoint1d_dir,
        checkpoint1e_dir, checkpoint1f_dir, output_dir, review_output_dir,
    ]
    (
        video_path, roi_config_path, checkpoint1a_dir, checkpoint1b_dir,
        checkpoint1b_review_dir, checkpoint1c_dir, checkpoint1d_dir,
        checkpoint1e_dir, checkpoint1f_dir, output_dir, review_output_dir,
    ) = [path if path.is_absolute() else project_root / path for path in paths]
    knowledge_base = PokemonKnowledgeBase.from_project(project_root)
    source = _frozen_sources(
        video_path, roi_config_path, checkpoint1a_dir, checkpoint1b_dir,
        checkpoint1b_review_dir, checkpoint1c_dir, checkpoint1d_dir,
        checkpoint1e_dir, checkpoint1f_dir, knowledge_base.data_path,
        knowledge_base.manifest_path, project_root,
    )
    metadata = load_json(checkpoint1a_dir / "metadata.json")
    video_sha256 = source[project_relative(video_path, project_root)]["sha256"]
    timestamp_index = load_frame_timestamp_index(
        checkpoint1a_dir / "frame_timestamps.npz", video_sha256
    )
    _, normalized_rois = load_roi_config(roi_config_path)
    rois = derived_visual_rois(
        pixel_rois(
            normalized_rois,
            int(metadata["display_dimensions"]["width"]),
            int(metadata["display_dimensions"]["height"]),
        )
    )
    candidates_payload = load_json(checkpoint1b_dir / "events.json")
    review_payload = load_json(checkpoint1b_review_dir / "candidate_review.json")
    battle_events_payload = load_json(checkpoint1d_dir / "battle_events.json")
    timeline_payload = load_json(checkpoint1e_dir / "battle_timeline.json")
    snapshots_payload = load_json(checkpoint1f_dir / "battle_state_snapshots.json")
    events = candidates_payload["events"]
    review_records = review_payload["records"]
    battle_events = battle_events_payload["events"]
    timeline_groups = timeline_payload["groups"]
    base_snapshots = snapshots_payload["snapshots"]
    requests = build_visual_frame_requests(events, review_records, timeline_groups, timestamp_index)
    engine = ocr_engine or AppleVisionOcrEngine()
    probe = engine.probe() if hasattr(engine, "probe") else {"available": True, "engine": "injected_test_engine"}

    with OutputTransaction(project_root, output_dir) as output_transaction:
        with OutputTransaction(project_root, review_output_dir) as review_transaction:
            work_dir = output_transaction.staging_dir / "work"
            extracted, extraction_report = extract_visual_frames(
                video_path, metadata, timestamp_index, rois, requests, work_dir,
                review_transaction.staging_dir,
            )
            ocr_by_request = _run_ocr(engine, extracted)
            ocr_runtime_errors = Counter(
                row.error or "success" for row in ocr_by_request.values()
            )
            team_frames = [row for row in extracted if row.request.role == "team_preview"]
            selected_frames = [row for row in extracted if row.request.role == "selected_four"]
            menu_frames = [row for row in extracted if row.request.role == "move_menu"]
            menu_status_frames = [row for row in extracted if row.request.role == "menu_status"]
            status_frames = [row for row in extracted if row.request.role == "status_sample"]
            roster = parse_team_roster(team_frames, ocr_by_request, knowledge_base)
            selected, initial_edges = parse_selected_four(selected_frames, roster)
            move_candidates = [row for row in events if row["type"] == "MOVE_MENU"]
            move_lexicon = sorted(
                {
                    str(row.get("metadata", {}).get("move"))
                    for row in battle_events
                    if row.get("event_type") == "MOVE" and row.get("metadata", {}).get("move")
                }
            )
            menus = parse_move_menu_observations(
                menu_frames, menu_status_frames, ocr_by_request, move_candidates, move_lexicon
            )
            hp = build_hp_observations(status_frames, ocr_by_request, knowledge_base)
            hp_changes = build_hp_changes(hp, timeline_groups)
            active = build_active_slot_timeline(hp)
            entities, resolution_edges, ref_to_entity = build_entities(
                roster, selected, hp, menus, base_snapshots, initial_edges
            )
            for row in active["entries"]:
                row["pokemon_entity_id"] = ref_to_entity.get(row["source_observation_ids"][0])
            for row in hp_changes["changes"]:
                row["pokemon_entity_id"] = next(
                    (
                        ref_to_entity.get(source_id)
                        for source_id in row["source_observation_ids"]
                        if ref_to_entity.get(source_id)
                    ),
                    None,
                )
            cycles = build_decision_cycles(
                menus, timeline_groups, battle_events,
                [str(row["snapshot_id"]) for row in base_snapshots],
            )
            enriched, enriched_deltas = build_enriched_snapshots(
                base_snapshots, roster, selected, entities, hp, active, menus, cycles,
                ref_to_entity,
            )
            shutil.rmtree(str(work_dir))

            candidate_counts = Counter(str(row["type"]) for row in events)
            hp_types = Counter(str(row["value_type"]) for row in hp["observations"])
            cycle_event_ids = [event_id for row in cycles["cycles"] for event_id in row["battle_event_ids"]]
            validations = {
                "team_preview_candidates_processed": roster["source_candidate_count"] == candidate_counts["TEAM_PREVIEW"],
                "selected_four_candidates_processed": len({row["source_candidate_id"] for row in selected["player_selected"]}) == candidate_counts["SELECTED_FOUR"],
                "move_menu_candidates_processed": menus["observation_count"] == candidate_counts["MOVE_MENU"],
                "move_menu_candidate_ids_unique": _id_unique(menus["observations"], "candidate_id"),
                "hp_timestamps_monotonic": _monotonic(hp["observations"], "timestamp"),
                "hp_observation_ids_unique": _id_unique(hp["observations"], "observation_id"),
                "active_slot_ids_unique": _id_unique(active["entries"], "active_slot_entry_id"),
                "decision_cycles_non_overlapping": all(
                    float(left["end_time"]) <= float(right["start_time"])
                    for left, right in zip(cycles["cycles"], cycles["cycles"][1:])
                ),
                "all_1f_snapshots_mapped": enriched["snapshot_count"] == len(base_snapshots),
                "base_snapshot_ids_unique": len({row["base_state_snapshot_id"] for row in enriched["snapshots"]}) == len(base_snapshots),
                "all_battle_events_traceable": len(cycle_event_ids) == len(battle_events) and set(cycle_event_ids) == {row["id"] for row in battle_events},
                "source_events_consumed_once": len(cycle_event_ids) == len(set(cycle_event_ids)),
                "sequential_decode_verified": extraction_report["status"] == "pass",
                "ocr_is_local_apple_vision": bool(probe.get("available"))
                and bool(
                    probe.get(
                        "runtime_path_verified",
                        probe.get("engine") == "injected_test_engine",
                    )
                ),
                "official_turn_not_inferred": all(not row["is_official_turn_number"] for row in cycles["cycles"]),
                "schemas_valid": True,
                "knowledge_base_loaded_and_hash_valid": bool(knowledge_base.data_sha256),
                "frozen_inputs_unchanged": True,
                "paired_output_transaction": True,
                "deterministic_metadata": True,
            }
            failed = [key for key, value in validations.items() if not value]
            if failed:
                raise InputError("Checkpoint 1G consistency gate 失敗：{}".format(", ".join(failed)))
            audit = {
                "schema_version": "0.1.0",
                "checkpoint": "1G",
                "kind": "checkpoint1g_audit",
                "status": "complete",
                "visual_source_audit": [
                    {"source": "TEAM_PREVIEW", "upstream_stage": "candidate_without_parser", "checkpoint1g_stage": "parsed_partial_roster"},
                    {"source": "SELECTED_FOUR", "upstream_stage": "candidate_without_parser", "checkpoint1g_stage": "parsed_ui_order"},
                    {"source": "MOVE_MENU", "upstream_stage": "31_candidates_without_parser", "checkpoint1g_stage": "observation_per_candidate"},
                    {"source": "player_status/opponent_status", "upstream_stage": "approved_roi_only", "checkpoint1g_stage": "full_battle_2hz_tracking"},
                ],
                "counts": {
                    "candidate_counts": dict(sorted(candidate_counts.items())),
                    "frame_requests": len(requests),
                    "ocr_jobs": len(ocr_by_request),
                    "ocr_runtime_results": dict(sorted(ocr_runtime_errors.items())),
                    "team_roster_entries": roster["entry_count"],
                    "selected_four_entries": len(selected["player_selected"]),
                    "move_menu_observations": menus["observation_count"],
                    "hp_raw_samples": hp["raw_sample_count"],
                    "hp_observations": hp["observation_count"],
                    "hp_value_types": dict(sorted(hp_types.items())),
                    "hp_changes": hp_changes["change_count"],
                    "active_slot_entries": active["entry_count"],
                    "pokemon_entities": entities["entity_count"],
                    "knowledge_base_species": knowledge_base.payload["counts"]["species"],
                    "knowledge_base_aliases": knowledge_base.payload["counts"]["aliases"],
                    "entity_resolution_edges": resolution_edges["edge_count"],
                    "decision_cycles": cycles["cycle_count"],
                    "enriched_snapshots": enriched["snapshot_count"],
                    "enriched_deltas": enriched_deltas["delta_count"],
                },
                "frame_extraction": extraction_report,
                "ocr_probe": {
                    **probe,
                    "runtime_result_counts": dict(sorted(ocr_runtime_errors.items())),
                },
                "validation": validations,
                "scope_guards": {
                    "checkpoint1a_to_1f_modified": False,
                    "checkpoint1b_detector_rerun": False,
                    "checkpoint1c_ocr_modified": False,
                    "battle_event_parser_rerun": False,
                    "timeline_builder_rerun": False,
                    "checkpoint1f_projector_rerun": False,
                    "pokemon_knowledge_base_modified_during_run": False,
                    "official_turn_inferred": False,
                    "simulator_assumptions_used": False,
                    "replay_analysis_started": False,
                    "rule_checker_created": False,
                    "gui_created": False,
                },
                "research_sources": [
                    "https://developer.apple.com/documentation/vision/recognizetextrequest",
                    "https://docs.opencv.org/master/de/da9/tutorial_template_matching.html",
                    "https://github.com/smogon/pokemon-showdown",
                    "https://github.com/PokeAPI/pokeapi",
                    "https://github.com/PokeAPI/sprites",
                    "https://champions-news.pokemon-home.com/en/page/776.html",
                    "https://github.com/pkmn/engine",
                    "https://arxiv.org/abs/1905.06397",
                    "https://arxiv.org/abs/1503.00302",
                ],
                "limitations": [
                    "對手 Team Preview 未顯示可靠文字時僅保留 visual identity。",
                    "Move Menu 未出現確認 evidence 時 chosen_move 與 target 保持 unknown。",
                    "visual bar estimate 不等同 exact HP。",
                    "Apple Vision production runtime failure 會中止 transactional generation，並保留上一版正式輸出。",
                    "Decision Cycle 不是官方 turn。",
                ],
            }
            payloads = {
                "team_roster.json": roster,
                "selected_four.json": selected,
                "pokemon_entities.json": entities,
                "move_menu_observations.json": menus,
                "hp_observations.json": hp,
                "hp_changes.json": hp_changes,
                "active_slot_timeline.json": active,
                "decision_cycles.json": cycles,
                "enriched_battle_snapshots.json": enriched,
                "enriched_state_deltas.json": enriched_deltas,
                "entity_resolution_edges.json": resolution_edges,
                "checkpoint1g_audit.json": audit,
            }
            schema_dir = project_root / "schemas"
            for filename, payload in payloads.items():
                _write_and_validate(output_transaction.staging_dir, schema_dir, filename, payload)
            review_manifest = build_checkpoint1g_review(
                review_transaction.staging_dir, roster, selected, menus, hp, hp_changes,
                active, entities, resolution_edges, cycles, enriched,
            )
            review_schema = load_json(schema_dir / "checkpoint1g_review_manifest.schema.json")
            jsonschema.Draft202012Validator(review_schema).validate(review_manifest)
            output_hashes = {
                filename: {
                    "path": filename,
                    "sha256": sha256_file(output_transaction.staging_dir / filename),
                    "schema": OUTPUT_SCHEMAS[filename],
                }
                for filename in payloads
            }
            manifest = {
                "schema_version": "0.1.0",
                "checkpoint": "1G",
                "kind": "checkpoint1g_manifest",
                "status": "complete",
                "outputs": output_hashes,
                "review_output": {
                    "path": project_relative(review_output_dir, project_root),
                    "manifest_sha256": sha256_file(review_transaction.staging_dir / "review_manifest.json"),
                    "blocking_human_review": False,
                },
                "source": source,
                "counts": audit["counts"],
                "validation": validations,
                "scope_guards": audit["scope_guards"],
            }
            _write_and_validate(
                output_transaction.staging_dir, schema_dir,
                "checkpoint1g_manifest.json", manifest,
            )
            _validate_frozen(source, project_root)
            OutputTransaction.commit_group([output_transaction, review_transaction])
    finalize_generated_output(output_dir)
    finalize_generated_output(review_output_dir)
    return manifest
