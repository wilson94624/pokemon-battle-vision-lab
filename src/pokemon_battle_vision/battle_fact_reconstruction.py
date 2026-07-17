"""Checkpoint 1H deterministic Battle Fact reconstruction。"""

from collections import Counter
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .battle_fact_identity import FactParticipantResolver
from .battle_fact_models import BattleFact, EvidenceReference
from .errors import InputError


CAUSAL_RELATION_TYPES = {
    "DAMAGE_FROM",
    "RESULT_OF",
    "STATUS_FROM",
    "STAT_CHANGE_FROM",
    "TRIGGERED_BY",
}


EVENT_FACT_TYPES = {
    ("MOVE", "use"): "MOVE_USED",
    ("MOVE_RESULT", "block"): "MOVE_RESOLVED",
    ("MOVE_RESULT", "prepare"): "MOVE_RESOLVED",
    ("MOVE_RESULT", "resolve"): "MOVE_RESOLVED",
    ("DAMAGE_RESULT", "damage"): "DAMAGE_OBSERVED",
    ("SWITCH", "switch_in"): "SWITCH_IN",
    ("ABILITY", "activate"): "ABILITY_ACTIVATED",
    ("ITEM", "activate"): "ITEM_ACTIVATED",
    ("STATUS", "inflict"): "STATUS_APPLIED",
    ("STATUS", "remove"): "STATUS_REMOVED",
    ("STATUS", "change"): "STATUS_CHANGED",
    ("STAT_CHANGE", "change"): "STAT_CHANGED",
    ("WEATHER", "start"): "WEATHER_STARTED",
    ("WEATHER", "end"): "WEATHER_ENDED",
    ("WEATHER", "change"): "WEATHER_CHANGED",
    ("TERRAIN", "start"): "TERRAIN_STARTED",
    ("TERRAIN", "end"): "TERRAIN_ENDED",
    ("TERRAIN", "change"): "TERRAIN_CHANGED",
    ("FIELD_EFFECT", "activate"): "FIELD_EFFECT_STARTED",
    ("FIELD_EFFECT", "end"): "FIELD_EFFECT_ENDED",
    ("FIELD_EFFECT", "update"): "FIELD_EFFECT_UPDATED",
    ("SIDE_CONDITION", "start"): "SIDE_CONDITION_STARTED",
    ("SIDE_CONDITION", "end"): "SIDE_CONDITION_ENDED",
    ("SIDE_CONDITION", "update"): "SIDE_CONDITION_UPDATED",
    ("VOLATILE_STATUS", "apply"): "VOLATILE_STATUS_APPLIED",
    ("VOLATILE_STATUS", "start"): "VOLATILE_STATUS_APPLIED",
    ("VOLATILE_STATUS", "end"): "VOLATILE_STATUS_REMOVED",
    ("VOLATILE_STATUS", "update"): "VOLATILE_STATUS_UPDATED",
    ("TRANSFORMATION", "activate"): "TRANSFORMATION_OCCURRED",
    ("TRANSFORMATION", "change"): "TRANSFORMATION_OCCURRED",
    ("FAINT", "faint"): "KO",
    ("BATTLE_RESULT", "end"): "BATTLE_ENDED",
}


def fact_type_for_event(event: Mapping[str, Any]) -> str:
    key = (
        str(event.get("event_type") or "UNKNOWN_EVENT"),
        str(event.get("metadata", {}).get("action") or "unknown"),
    )
    return EVENT_FACT_TYPES.get(key, "UNRESOLVED_EVENT")


def _review_decisions(source: Mapping[str, Any]) -> Dict[str, str]:
    return {
        str(row["relation_id"]): str(row["human_decision"])
        for row in source["relation_reviews"]["records"]
    }


def _indexes(source: Mapping[str, Any]) -> Dict[str, Any]:
    event_to_timeline: Dict[str, List[str]] = {}
    for group in source["timeline"]["groups"]:
        for event_id in group["event_ids"]:
            event_to_timeline.setdefault(str(event_id), []).append(
                str(group["timeline_id"])
            )
    event_to_relations: Dict[str, List[str]] = {}
    for relation in source["relations"]["relations"]:
        for event_id in (relation["from_event_id"], relation["to_event_id"]):
            event_to_relations.setdefault(str(event_id), []).append(
                str(relation["relation_id"])
            )
    event_to_cycle: Dict[str, str] = {}
    for cycle in source["decision_cycles"]["cycles"]:
        for event_id in cycle["battle_event_ids"]:
            if str(event_id) in event_to_cycle:
                raise InputError("BattleEvent 同時屬於多個 Decision Cycle：{}".format(event_id))
            event_to_cycle[str(event_id)] = str(cycle["cycle_id"])
    observations = {
        str(row["observation_id"]): row
        for row in source["hp_observations"]["observations"]
    }
    menus = {
        str(row["candidate_id"]): row
        for row in source["move_menu_observations"]["observations"]
    }
    return {
        "event_to_timeline": event_to_timeline,
        "event_to_relations": event_to_relations,
        "event_to_cycle": event_to_cycle,
        "hp_observations": observations,
        "menus": menus,
        "artifact_paths": source.get(
            "artifact_paths",
            {
                "events": "outputs/checkpoint-1d/battle_events.json",
                "hp_changes": "outputs/checkpoint-1g/hp_changes.json",
                "hp_observations": "outputs/checkpoint-1g/hp_observations.json",
                "decision_cycles": "outputs/checkpoint-1g/decision_cycles.json",
                "move_menu_observations": "outputs/checkpoint-1g/move_menu_observations.json",
            },
        ),
    }


def _event_draft(
    event: Mapping[str, Any],
    resolver: FactParticipantResolver,
    indexes: Mapping[str, Any],
) -> Dict[str, Any]:
    event_id = str(event["id"])
    metadata = dict(event.get("metadata", {}))
    return {
        "sort_key": (float(event["timestamp"]), 1, event_id),
        "source_id": event_id,
        "fact_type": fact_type_for_event(event),
        "timestamp": float(event["timestamp"]),
        "start_time": float(event["start_time"]),
        "end_time": float(event["end_time"]),
        "certainty": "observed",
        "confidence": float(event["confidence"]),
        "participants": resolver.from_event(event),
        "attributes": {
            "source_event_type": str(event["event_type"]),
            "source_action": str(metadata.get("action") or "unknown"),
            "candidate_id": str(event["candidate_id"]),
            "raw_text": str(event.get("raw_text") or ""),
            "normalized_text": str(event.get("normalized_text") or ""),
            "parsed_metadata": metadata,
            "source_acceptance": str(event.get("source", {}).get("acceptance") or "unknown"),
        },
        "evidence": (
            EvidenceReference(
                checkpoint="1D",
                artifact_path=indexes["artifact_paths"]["events"],
                record_id=event_id,
                observation_kind="battle_text_parsed_event",
                evidence_role="primary",
                confidence=float(event["confidence"]),
                timestamp=float(event["timestamp"]),
                upstream_record_ids=(str(event["candidate_id"]),),
            ),
        ),
        "source_timeline_ids": tuple(indexes["event_to_timeline"].get(event_id, [])),
        "source_relation_ids": tuple(indexes["event_to_relations"].get(event_id, [])),
        "source_decision_cycle_ids": (
            (indexes["event_to_cycle"][event_id],)
            if event_id in indexes["event_to_cycle"]
            else ()
        ),
        "reconstruction_rule_id": "checkpoint1h.battle_text_event.v1",
    }


def _hp_draft(
    change: Mapping[str, Any],
    resolver: FactParticipantResolver,
    indexes: Mapping[str, Any],
) -> Dict[str, Any]:
    change_id = str(change["change_id"])
    observation_ids = tuple(map(str, change["source_observation_ids"]))
    evidence = [
        EvidenceReference(
            checkpoint="1G",
            artifact_path=indexes["artifact_paths"]["hp_changes"],
            record_id=change_id,
            observation_kind="derived_hp_change_observation",
            evidence_role="primary",
            confidence=float(change["confidence"]),
            timestamp=float(change["timestamp"]),
            upstream_record_ids=observation_ids,
        )
    ]
    for observation_id in observation_ids:
        observation = indexes["hp_observations"][observation_id]
        evidence.append(
            EvidenceReference(
                checkpoint="1G",
                artifact_path=indexes["artifact_paths"]["hp_observations"],
                record_id=observation_id,
                observation_kind="hp_observation",
                evidence_role="supporting",
                confidence=float(observation["confidence"]),
                timestamp=float(observation["timestamp"]),
            )
        )
    return {
        "sort_key": (float(change["timestamp"]), 2, change_id),
        "source_id": change_id,
        "fact_type": "HP_CHANGED",
        "timestamp": float(change["timestamp"]),
        "start_time": float(change["timestamp"]),
        "end_time": float(change["timestamp"]),
        "certainty": "observed",
        "confidence": float(change["confidence"]),
        "participants": resolver.from_hp_change(change),
        "attributes": {
            key: change.get(key)
            for key in (
                "change_type",
                "side",
                "slot",
                "before_hp",
                "after_hp",
                "delta_hp",
                "before_percent",
                "after_percent",
                "delta_percent",
                "cause",
                "rule_id",
                "pokemon_entity_id",
                "identity_text",
            )
        },
        "evidence": tuple(evidence),
        "source_timeline_ids": tuple(map(str, change.get("linked_timeline_ids", []))),
        "source_relation_ids": (),
        "source_decision_cycle_ids": (),
        "reconstruction_rule_id": "checkpoint1h.existing_hp_change.v1",
    }


def _boundary_draft(cycle: Mapping[str, Any], indexes: Mapping[str, Any]) -> Dict[str, Any]:
    cycle_id = str(cycle["cycle_id"])
    candidate_ids = tuple(
        str(candidate_id)
        for row in cycle["boundary_evidence"]
        for candidate_id in row.get("candidate_ids", [])
    )
    evidence = [
        EvidenceReference(
            checkpoint="1G",
            artifact_path=indexes["artifact_paths"]["decision_cycles"],
            record_id=cycle_id,
            observation_kind="decision_cycle_boundary",
            evidence_role="primary",
            confidence=float(cycle["confidence"]),
            timestamp=float(cycle["start_time"]),
            upstream_record_ids=candidate_ids,
        )
    ]
    for candidate_id in candidate_ids:
        menu = indexes["menus"][candidate_id]
        evidence.append(
            EvidenceReference(
                checkpoint="1G",
                artifact_path=indexes["artifact_paths"]["move_menu_observations"],
                record_id=candidate_id,
                observation_kind="move_menu_boundary_observation",
                evidence_role="boundary_support",
                confidence=float(menu["confidence"]),
                timestamp=float(menu["start_time"]),
            )
        )
    return {
        "sort_key": (float(cycle["start_time"]), 0, cycle_id),
        "source_id": cycle_id,
        "fact_type": "TURN_BOUNDARY",
        "timestamp": float(cycle["start_time"]),
        "start_time": float(cycle["start_time"]),
        "end_time": float(cycle["start_time"]),
        "certainty": "ambiguous",
        "confidence": float(cycle["confidence"]),
        "participants": (),
        "attributes": {
            "boundary_kind": "move_menu_cluster_appearance",
            "official_turn_number": None,
            "is_official_turn_number": False,
            "source_cycle_index": int(cycle["cycle_index"]),
            "move_menu_candidate_ids": list(candidate_ids),
        },
        "evidence": tuple(evidence),
        "source_timeline_ids": tuple(map(str, cycle["timeline_ids"])),
        "source_relation_ids": (),
        "source_decision_cycle_ids": (cycle_id,),
        "reconstruction_rule_id": "checkpoint1h.ambiguous_move_menu_boundary.v1",
    }


def build_battle_facts(source: Mapping[str, Any]) -> Dict[str, Any]:
    resolver = FactParticipantResolver(
        source["knowledge_base"], source["pokemon_entities"]["entities"]
    )
    indexes = _indexes(source)
    drafts = [
        _event_draft(event, resolver, indexes)
        for event in source["events"]["events"]
    ]
    drafts.extend(
        _hp_draft(change, resolver, indexes)
        for change in source["hp_changes"]["changes"]
    )
    # opening segment 不冒充 turn；只從後續 cycle 產生 ambiguous boundary。
    drafts.extend(
        _boundary_draft(cycle, indexes)
        for cycle in source["decision_cycles"]["cycles"][1:]
    )
    facts: List[BattleFact] = []
    source_to_fact: Dict[str, str] = {}
    for sequence, draft in enumerate(sorted(drafts, key=lambda row: row["sort_key"]), 1):
        fact_id = "battle-fact-{:04d}".format(sequence)
        fact = BattleFact(
            fact_id=fact_id,
            sequence=sequence,
            fact_type=draft["fact_type"],
            timestamp=draft["timestamp"],
            start_time=draft["start_time"],
            end_time=draft["end_time"],
            certainty=draft["certainty"],
            confidence=draft["confidence"],
            participants=draft["participants"],
            attributes=draft["attributes"],
            evidence=draft["evidence"],
            source_timeline_ids=draft["source_timeline_ids"],
            source_relation_ids=draft["source_relation_ids"],
            source_decision_cycle_ids=draft["source_decision_cycle_ids"],
            reconstruction_rule_id=draft["reconstruction_rule_id"],
        )
        facts.append(fact)
        source_to_fact[draft["source_id"]] = fact_id
    return {"facts": facts, "source_to_fact": source_to_fact}


def build_fact_relations(
    source: Mapping[str, Any],
    source_to_fact: Mapping[str, str],
) -> List[Dict[str, Any]]:
    decisions = _review_decisions(source)
    records = []
    for sequence, relation in enumerate(source["relations"]["relations"], 1):
        source_relation_id = str(relation["relation_id"])
        decision = decisions.get(source_relation_id, "auto_accepted")
        active = decision != "rejected"
        relation_type = str(relation["relation_type"])
        records.append(
            {
                "fact_relation_id": "battle-fact-relation-{:04d}".format(sequence),
                "sequence": sequence,
                "relation_type": relation_type,
                "from_fact_id": source_to_fact[str(relation["from_event_id"])],
                "to_fact_id": source_to_fact[str(relation["to_event_id"])],
                "active": active,
                "causal_claim": active and relation_type in CAUSAL_RELATION_TYPES,
                "confidence": round(float(relation["confidence"]), 6),
                "review_resolution": decision,
                "source_relation_id": source_relation_id,
                "source_timeline_id": relation.get("group_id"),
                "source_rule_id": str(relation["rule_id"]),
                "source_evidence": list(relation["evidence"]),
                "reconstruction_rule_id": "checkpoint1h.reviewed_relation_projection.v1",
            }
        )
    return records


def _facts_in_span(
    facts: Sequence[BattleFact],
    start: float,
    end: float,
    fact_type: str,
    is_last: bool,
) -> List[str]:
    return [
        fact.fact_id
        for fact in facts
        if fact.fact_type == fact_type
        and fact.timestamp >= start
        and (fact.timestamp < end or (is_last and fact.timestamp <= end))
    ]


def build_reconstructed_turns(
    source: Mapping[str, Any],
    facts: Sequence[BattleFact],
    source_to_fact: Mapping[str, str],
) -> Dict[str, Any]:
    cycles = source["decision_cycles"]["cycles"]

    def event_fact_ids(cycle):
        return [source_to_fact[str(event_id)] for event_id in cycle["battle_event_ids"]]

    opening = cycles[0]
    opening_segment = {
        "segment_type": "opening_segment",
        "source_cycle_id": str(opening["cycle_id"]),
        "start_time": float(opening["start_time"]),
        "end_time": float(opening["end_time"]),
        "official_turn_number": None,
        "is_official_turn_number": False,
        "event_fact_ids": event_fact_ids(opening),
        "hp_change_fact_ids": _facts_in_span(
            facts,
            float(opening["start_time"]),
            float(opening["end_time"]),
            "HP_CHANGED",
            False,
        ),
        "source_timeline_ids": list(opening["timeline_ids"]),
    }
    candidates = []
    for index, cycle in enumerate(cycles[1:], 1):
        is_last = cycle is cycles[-1]
        candidates.append(
            {
                "turn_candidate_id": "turn-candidate-{:03d}".format(index),
                "source_cycle_id": str(cycle["cycle_id"]),
                "source_cycle_index": int(cycle["cycle_index"]),
                "start_time": float(cycle["start_time"]),
                "end_time": float(cycle["end_time"]),
                "official_turn_number": None,
                "is_official_turn_number": False,
                "reconstruction_status": "ambiguous",
                "confidence": round(float(cycle["confidence"]), 6),
                "start_boundary_fact_id": source_to_fact[str(cycle["cycle_id"])],
                "event_fact_ids": event_fact_ids(cycle),
                "hp_change_fact_ids": _facts_in_span(
                    facts,
                    float(cycle["start_time"]),
                    float(cycle["end_time"]),
                    "HP_CHANGED",
                    is_last,
                ),
                "source_timeline_ids": list(cycle["timeline_ids"]),
                "boundary_evidence": list(cycle["boundary_evidence"]),
                "limitations": [
                    "Move Menu 只證明 decision boundary，不能證明 selected move。",
                    "source cycle index 不是遊戲顯示的官方回合編號。",
                ],
            }
        )
    return {
        "schema_version": "0.1.0",
        "checkpoint": "1H",
        "kind": "reconstructed_turn_candidates",
        "policy": {
            "boundary_source": "Checkpoint 1G Move Menu decision cycles",
            "official_turn_inferred": False,
            "opening_segment_is_turn": False,
            "selected_move_inferred_from_menu": False,
        },
        "opening_segment": opening_segment,
        "turn_candidate_count": len(candidates),
        "turn_candidates": candidates,
    }


def reconstruction_counts(facts: Sequence[BattleFact]) -> Dict[str, Any]:
    return {
        "fact_count": len(facts),
        "fact_types": dict(sorted(Counter(fact.fact_type for fact in facts).items())),
        "certainty": dict(sorted(Counter(fact.certainty for fact in facts).items())),
    }
