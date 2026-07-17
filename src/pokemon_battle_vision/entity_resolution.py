"""多來源 Pokémon canonical entity 與 resolution edge。"""

from collections import Counter, defaultdict
from typing import Any, DefaultDict, Dict, List, Mapping, Sequence, Tuple

from .checkpoint1g_models import ResolutionEdge


def build_entities(
    roster: Mapping[str, Any],
    selected: Mapping[str, Any],
    hp: Mapping[str, Any],
    move_menus: Mapping[str, Any],
    base_snapshots: Sequence[Mapping[str, Any]],
    initial_edges: Sequence[ResolutionEdge],
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, str]]:
    entities: List[Dict[str, Any]] = []
    ref_to_entity: Dict[str, str] = {}
    name_counts = Counter(
        (row["side"], row.get("species_id") or row.get("species_text"))
        for row in roster["entries"]
        if row.get("species_id") or row.get("species_text")
    )
    for row in roster["entries"]:
        entity_id = "pokemon-entity-{:04d}".format(len(entities) + 1)
        ref = "roster:{}:{}".format(row["side"], row["slot_index"])
        ref_to_entity[ref] = entity_id
        entities.append(
            {
                "entity_id": entity_id,
                "species": {
                    "value": row.get("canonical_species_name") or row.get("species_text"),
                    "raw_text": row.get("species_text"),
                    "canonical_species_id": row.get("species_id"),
                    "knowledge": (
                        "observed_and_knowledge_base_resolved"
                        if row.get("species_id")
                        else ("observed" if row.get("species_text") else "unknown")
                    ),
                    "confidence": row["confidence"],
                },
                "side": {"value": row["side"], "knowledge": "known", "confidence": 1.0},
                "team_slot": row["slot_index"],
                "selected_order": None,
                "observed_moves": [],
                "ability_observations": [],
                "item_observations": [],
                "aliases": [row["visual_identity"]],
                "source_ids": [row["source_candidate_id"]],
                "conflicts": [],
            }
        )
    edges = []
    for edge in initial_edges:
        target = ref_to_entity.get(edge.target_entity_id)
        if target:
            payload = edge.to_dict()
            payload["edge_id"] = "entity-resolution-edge-{:04d}".format(len(edges) + 1)
            payload["target_entity_id"] = target
            accepted = float(edge.confidence) >= 0.68
            payload["resolution_status"] = "accepted" if accepted else "unresolved_candidate"
            edges.append(payload)
            if accepted:
                ref_to_entity[edge.source_ref] = target
    for selected_row in selected["player_selected"]:
        ref = selected_row.get("roster_ref")
        entity_id = ref_to_entity.get(ref) if ref else None
        if entity_id:
            next(row for row in entities if row["entity_id"] == entity_id)["selected_order"] = selected_row["selection_order"]

    # 名稱只有在 side 內唯一時才可合併；相同 species roster 永遠保持不同 canonical entity。
    for observation in hp["observations"]:
        name = observation.get("identity_text")
        species_id = observation.get("species_id")
        side = observation["side"]
        matches = [
            row
            for row in entities
            if row["side"]["value"] == side
            and (
                (
                    species_id is not None
                    and row["species"].get("canonical_species_id") == species_id
                )
                or (
                    species_id is None
                    and row["species"].get("raw_text") == name
                )
            )
        ]
        identity_key = species_id or name
        if identity_key and len(matches) == 1 and name_counts[(side, identity_key)] <= 1:
            entity_id = matches[0]["entity_id"]
            ref_to_entity[observation["observation_id"]] = entity_id
            edges.append(
                {
                    "edge_id": "entity-resolution-edge-{:04d}".format(len(edges) + 1),
                    "source_ref": observation["observation_id"],
                    "target_entity_id": entity_id,
                    "rule_id": "entity.status_name_unique_within_side.v1",
                    "confidence": round(max(0.7, observation["confidence"]), 6),
                    "evidence": ["side={} name={} unique".format(side, name)],
                    "provenance": [
                        {
                            "frame_ordinal": observation["frame_ordinal"],
                            "pts": observation["timestamp"],
                        }
                    ],
                }
            )
        elif identity_key:
            new_id = "pokemon-entity-{:04d}".format(len(entities) + 1)
            entities.append(
                {
                    "entity_id": new_id,
                    "species": {
                        "value": observation.get("canonical_species_name") or name,
                        "raw_text": name,
                        "canonical_species_id": species_id,
                        "knowledge": (
                            "observed_and_knowledge_base_resolved"
                            if species_id is not None
                            else "observed"
                        ),
                        "confidence": observation["confidence"],
                    },
                    "side": {"value": side, "knowledge": "observed", "confidence": observation["confidence"]},
                    "team_slot": None,
                    "selected_order": None,
                    "observed_moves": [],
                    "ability_observations": [],
                    "item_observations": [],
                    "aliases": [observation["visual_identity"]],
                    "source_ids": [observation["observation_id"]],
                    "conflicts": ["duplicate_or_unresolved_species"] if len(matches) > 1 else [],
                }
            )
            ref_to_entity[observation["observation_id"]] = new_id
    # 同一 status track 後續 observation 繼承第一個已 resolution 的 entity。
    by_track: DefaultDict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for observation in hp["observations"]:
        by_track[str(observation["track_id"])].append(observation)
    for track_rows in by_track.values():
        resolved = next((ref_to_entity.get(row["observation_id"]) for row in track_rows if ref_to_entity.get(row["observation_id"])), None)
        if resolved:
            for row in track_rows:
                ref_to_entity.setdefault(row["observation_id"], resolved)
    for observation in hp["observations"]:
        observation["pokemon_entity_id"] = ref_to_entity.get(observation["observation_id"])
    return (
        {
            "schema_version": "0.1.0",
            "checkpoint": "1G",
            "kind": "pokemon_entities",
            "entity_count": len(entities),
            "entities": entities,
        },
        {
            "schema_version": "0.1.0",
            "checkpoint": "1G",
            "kind": "entity_resolution_edges",
            "edge_count": len(edges),
            "edges": edges,
        },
        ref_to_entity,
    )
