"""以 Move Menu boundary 建立非官方 Decision Cycle。"""

from typing import Any, Dict, List, Mapping, Sequence


MENU_CLUSTER_GAP_SEC = 20.0


def cluster_move_windows(observations: Sequence[Mapping[str, Any]]) -> List[List[Mapping[str, Any]]]:
    clusters: List[List[Mapping[str, Any]]] = []
    for row in sorted(observations, key=lambda item: (item["start_time"], item["candidate_id"])):
        if not clusters or float(row["start_time"]) - float(clusters[-1][-1]["end_time"]) > MENU_CLUSTER_GAP_SEC:
            clusters.append([row])
        else:
            clusters[-1].append(row)
    return clusters


def build_decision_cycles(
    menu_payload: Mapping[str, Any],
    timeline_groups: Sequence[Mapping[str, Any]],
    battle_events: Sequence[Mapping[str, Any]],
    snapshot_ids: Sequence[str],
) -> Dict[str, Any]:
    clusters = cluster_move_windows(menu_payload["observations"])
    if not timeline_groups:
        return {"schema_version": "0.1.0", "checkpoint": "1G", "kind": "decision_cycles", "cycle_count": 0, "cycles": []}
    battle_start = float(timeline_groups[0]["start_time"])
    battle_end = float(timeline_groups[-1]["end_time"])
    boundaries = [float(cluster[0]["start_time"]) for cluster in clusters]
    windows = []
    if boundaries and battle_start < boundaries[0]:
        windows.append((battle_start, boundaries[0], [], "opening"))
    for index, cluster in enumerate(clusters):
        start = float(cluster[0]["start_time"])
        end = boundaries[index + 1] if index + 1 < len(boundaries) else battle_end
        windows.append((start, max(start, end), cluster, "final" if index == len(clusters) - 1 else "decision"))
    if not windows:
        windows.append((battle_start, battle_end, [], "opening_and_final"))
    cycles = []
    for index, (start, end, cluster, phase) in enumerate(windows, start=1):
        is_last = index == len(windows)
        groups = [
            row
            for row in timeline_groups
            if float(row["start_time"]) >= start
            and (float(row["start_time"]) < end or (is_last and float(row["start_time"]) <= end))
        ]
        events = [
            row
            for row in battle_events
            if float(row["start_time"]) >= start
            and (float(row["start_time"]) < end or (is_last and float(row["start_time"]) <= end))
        ]
        before_index = max(0, min(len(snapshot_ids) - 1, index - 1))
        after_index = max(0, min(len(snapshot_ids) - 1, index))
        cycles.append(
            {
                "cycle_id": "decision-cycle-{:03d}".format(index),
                "cycle_index": index,
                "phase": phase,
                "is_official_turn_number": False,
                "start_time": round(start, 6),
                "end_time": round(end, 6),
                "decision_window_ids": [row["decision_window_id"] for row in cluster],
                "timeline_ids": [row["timeline_id"] for row in groups],
                "battle_event_ids": [row["id"] for row in events],
                "before_state_id": snapshot_ids[before_index],
                "after_state_id": snapshot_ids[after_index],
                "confidence": round(0.95 if cluster else 0.8, 6),
                "boundary_evidence": [
                    {
                        "rule_id": "decision_cycle.move_menu_cluster_boundary.v1",
                        "menu_cluster_gap_sec": MENU_CLUSTER_GAP_SEC,
                        "candidate_ids": [row["candidate_id"] for row in cluster],
                    }
                ],
            }
        )
    return {
        "schema_version": "0.1.0",
        "checkpoint": "1G",
        "kind": "decision_cycles",
        "policy": {
            "boundary_source": "MOVE_MENU appearance",
            "menu_cluster_gap_sec": MENU_CLUSTER_GAP_SEC,
            "official_turn_inferred": False,
            "overlap_policy": "half_open_intervals_except_final_closed",
        },
        "cycle_count": len(cycles),
        "cycles": cycles,
    }
