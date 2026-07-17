from pokemon_battle_vision.decision_cycles import build_decision_cycles, cluster_move_windows
from pokemon_battle_vision.entity_resolution import build_entities
from pokemon_battle_vision.visual_state_tracking import build_active_slot_timeline, build_hp_changes


def _hp_observation(identifier, track, time, percent, identity="甲"):
    return {
        "observation_id": identifier,
        "track_id": track,
        "side": "player",
        "slot": "left",
        "timestamp": time,
        "frame_ordinal": int(time * 10),
        "identity_text": identity,
        "visual_identity": "visual:{}".format(track),
        "current_hp": int(percent),
        "max_hp": 100,
        "hp_percent": percent,
        "confidence": 0.9,
    }


def test_hp_changes_keep_unknown_cause_without_direct_evidence():
    hp = {"observations": [_hp_observation("o1", "t1", 1.0, 100), _hp_observation("o2", "t1", 2.0, 70)]}
    payload = build_hp_changes(hp, [])
    assert payload["change_count"] == 1
    assert payload["changes"][0]["change_type"] == "damage"
    assert payload["changes"][0]["cause"] == "unknown"


def test_switch_track_boundary_changes_active_slot_but_short_absence_does_not_clear():
    hp = {
        "observations": [
            _hp_observation("o1", "t1", 1.0, 100, "甲"),
            _hp_observation("o2", "t1", 2.0, 90, "甲"),
            _hp_observation("o3", "t2", 5.0, 100, "乙"),
        ]
    }
    active = build_active_slot_timeline(hp)
    assert [row["identity_text"] for row in active["entries"]] == ["甲", "乙"]


def test_decision_cycle_clusters_double_slot_menus_and_never_calls_them_turns():
    menus = [
        {"decision_window_id": "w1", "candidate_id": "m1", "start_time": 10.0, "end_time": 11.0},
        {"decision_window_id": "w2", "candidate_id": "m2", "start_time": 13.0, "end_time": 14.0},
        {"decision_window_id": "w3", "candidate_id": "m3", "start_time": 40.0, "end_time": 41.0},
    ]
    assert [len(row) for row in cluster_move_windows(menus)] == [2, 1]
    timeline = [
        {"timeline_id": "t1", "start_time": 5.0, "end_time": 6.0},
        {"timeline_id": "t2", "start_time": 20.0, "end_time": 21.0},
        {"timeline_id": "t3", "start_time": 50.0, "end_time": 51.0},
    ]
    events = [
        {"id": "e1", "start_time": 5.0}, {"id": "e2", "start_time": 20.0}, {"id": "e3", "start_time": 50.0}
    ]
    payload = build_decision_cycles({"observations": menus}, timeline, events, ["s0", "s1", "s2", "s3"])
    assert payload["cycle_count"] == 3
    assert all(not row["is_official_turn_number"] for row in payload["cycles"])
    assert len({event for row in payload["cycles"] for event in row["battle_event_ids"]}) == 3


def test_duplicate_species_are_not_merged_by_name_only():
    roster = {
        "entries": [
            {"side": "player", "slot_index": 1, "species_text": "甲", "confidence": 1.0, "visual_identity": "v1", "source_candidate_id": "c"},
            {"side": "player", "slot_index": 2, "species_text": "甲", "confidence": 1.0, "visual_identity": "v2", "source_candidate_id": "c"},
        ]
    }
    selected = {"player_selected": []}
    hp = {"observations": [_hp_observation("o1", "t1", 1.0, 100, "甲")]}
    entities, edges, refs = build_entities(roster, selected, hp, {"observations": []}, [], [])
    assert entities["entity_count"] == 3
    assert refs["o1"] not in {"pokemon-entity-0001", "pokemon-entity-0002"}
