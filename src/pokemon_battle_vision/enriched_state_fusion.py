"""1F sparse snapshots 與 1G visual observations 的 deterministic overlay。"""

import copy
from typing import Any, Dict, List, Mapping, Sequence, Tuple


def _latest(rows: Sequence[Mapping[str, Any]], timestamp: float, keys: Sequence[str]) -> Dict[str, Mapping[str, Any]]:
    result: Dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if float(row["timestamp"]) > timestamp:
            break
        key = ":".join(str(row.get(name)) for name in keys)
        result[key] = row
    return result


def build_enriched_snapshots(
    base_snapshots: Sequence[Mapping[str, Any]],
    roster: Mapping[str, Any],
    selected: Mapping[str, Any],
    entities: Mapping[str, Any],
    hp: Mapping[str, Any],
    active: Mapping[str, Any],
    menus: Mapping[str, Any],
    cycles: Mapping[str, Any],
    ref_to_entity: Mapping[str, str],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    hp_rows = sorted(hp["observations"], key=lambda row: row["timestamp"])
    active_rows = sorted(active["entries"], key=lambda row: row["timestamp"])
    menu_rows = sorted(menus["observations"], key=lambda row: row["end_time"])
    snapshots: List[Dict[str, Any]] = []
    deltas: List[Dict[str, Any]] = []
    previous_visual = None
    for index, base in enumerate(base_snapshots):
        timestamp = float(base["timestamp"])
        hp_state = _latest(hp_rows, timestamp, ("side", "slot"))
        active_state = _latest(active_rows, timestamp, ("side", "slot"))
        for row in active_state.values():
            row["pokemon_entity_id"] = ref_to_entity.get(
                row["source_observation_ids"][0]
            )
        observed_moves: Dict[str, List[str]] = {}
        for row in menu_rows:
            if float(row["end_time"]) > timestamp:
                break
            key = row.get("pokemon") or "{}:active".format(row.get("selecting_slot", "unknown"))
            values = observed_moves.setdefault(str(key), [])
            for move in row["available_moves"]:
                if move["value"] not in values:
                    values.append(move["value"])
        cycle = next(
            (
                row
                for row in cycles["cycles"]
                if float(row["start_time"]) <= timestamp <= float(row["end_time"])
            ),
            None,
        )
        visual = {
            "active_slots": {key: copy.deepcopy(value) for key, value in active_state.items()},
            "hp_state": {key: copy.deepcopy(value) for key, value in hp_state.items()},
            "observed_movesets": observed_moves,
            "decision_cycle_id": cycle["cycle_id"] if cycle else None,
        }
        unknown_fields = list(base.get("unknown_fields", []))
        if not hp_state:
            unknown_fields.append("visual.hp_state")
        if not active_state:
            unknown_fields.append("visual.active_slots")
        snapshot = {
            "enriched_snapshot_id": "enriched-state-{:04d}".format(index),
            "sequence": index,
            "timestamp": timestamp,
            "base_state_snapshot_id": base["snapshot_id"],
            "base_state": copy.deepcopy(base),
            "roster_state": {
                "source": "team_roster.json",
                "entry_count": roster["entry_count"],
            },
            "selected_four_state": copy.deepcopy(selected),
            "active_slots": visual["active_slots"],
            "pokemon_entities": [row["entity_id"] for row in entities["entities"]],
            "hp_state": visual["hp_state"],
            "observed_movesets": visual["observed_movesets"],
            "decision_cycle": visual["decision_cycle_id"],
            "unknown_fields": sorted(set(unknown_fields)),
            "conflicts": [],
            "confidence": round(
                0.55 * float(base.get("confidence", 0.0))
                + 0.45 * (
                    sum(float(row.get("confidence", 0.0)) for row in hp_state.values())
                    / max(1, len(hp_state))
                ),
                6,
            ),
            "completeness": round(
                min(1.0, float(base.get("completeness", 0.0)) + 0.08 * bool(hp_state) + 0.08 * bool(active_state)),
                6,
            ),
            "provenance": {
                "base_checkpoint": "1F",
                "visual_checkpoint": "1G",
                "hp_observation_ids": [row["observation_id"] for row in hp_state.values()],
                "active_slot_entry_ids": [row["active_slot_entry_id"] for row in active_state.values()],
            },
        }
        snapshots.append(snapshot)
        changes = []
        if previous_visual is not None:
            for field in ("active_slots", "hp_state", "observed_movesets", "decision_cycle_id"):
                if previous_visual[field] != visual[field]:
                    changes.append(field)
        deltas.append(
            {
                "enriched_delta_id": "enriched-delta-{:04d}".format(index),
                "sequence": index,
                "timestamp": timestamp,
                "base_state_snapshot_id": base["snapshot_id"],
                "snapshot_after": snapshot["enriched_snapshot_id"],
                "snapshot_before": snapshots[index - 1]["enriched_snapshot_id"] if index else None,
                "changed_visual_fields": changes,
                "source_observation_ids": sorted(
                    set(snapshot["provenance"]["hp_observation_ids"])
                ),
                "rule_id": "enriched_state.visual_overlay_on_1f_snapshot.v1",
            }
        )
        previous_visual = visual
    return (
        {
            "schema_version": "0.1.0",
            "checkpoint": "1G",
            "kind": "enriched_battle_snapshots",
            "base_snapshot_count": len(base_snapshots),
            "snapshot_count": len(snapshots),
            "snapshots": snapshots,
        },
        {
            "schema_version": "0.1.0",
            "checkpoint": "1G",
            "kind": "enriched_state_deltas",
            "delta_count": len(deltas),
            "deltas": deltas,
        },
    )
