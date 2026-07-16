from copy import deepcopy

import pytest

from pokemon_battle_vision.battle_timeline_builder import build_battle_timeline
from pokemon_battle_vision.battle_timeline_models import human_review_defaults


def _event(index, event_type, start, metadata, end=None, confidence=0.99):
    return {
        "id": "battle-event-{:04d}".format(index),
        "timestamp": float(start),
        "start_time": float(start),
        "end_time": float(end if end is not None else start + 0.5),
        "candidate_id": "candidate-{:04d}".format(index),
        "event_type": event_type,
        "raw_text": "event {}".format(index),
        "normalized_text": "event {}".format(index),
        "confidence": confidence,
        "source": {},
        "metadata": {"rule_id": "fixture.{}".format(index), **metadata},
    }


def _edge(relations, source, target):
    return next(
        edge
        for edge in relations
        if edge.from_event_id == source and edge.to_event_id == target
    )


@pytest.mark.parametrize(
    "target_type,target_metadata,expected_relation",
    [
        ("MOVE_RESULT", {"target": "A", "result": "miss"}, "RESULT_OF"),
        ("DAMAGE_RESULT", {"target": "A", "action": "damage"}, "DAMAGE_FROM"),
        ("STATUS", {"target": "A", "status": "burn"}, "STATUS_FROM"),
        ("STAT_CHANGE", {"target": "A", "stat": "attack"}, "STAT_CHANGE_FROM"),
    ],
)
def test_move_correlates_only_with_explicit_matching_metadata(
    target_type, target_metadata, expected_relation
):
    events = [
        _event(1, "MOVE", 1.0, {"actor": "User", "move": "Move", "target": "A"}),
        _event(2, target_type, 2.0, target_metadata),
    ]
    groups, relations = build_battle_timeline(events)
    edge = _edge(relations, "battle-event-0001", "battle-event-0002")
    assert edge.relation_type == expected_relation
    assert edge.review_status == "auto_accepted"
    assert len(groups) == 1


def test_move_damage_faint_chain_preserves_order():
    events = [
        _event(1, "MOVE", 1.0, {"actor": "User", "move": "Move", "target": "A"}),
        _event(2, "DAMAGE_RESULT", 2.0, {"target": "A", "action": "damage"}),
        _event(3, "FAINT", 2.7, {"target": "A", "action": "faint"}),
    ]
    groups, relations = build_battle_timeline(events)
    assert len(groups) == 1
    assert groups[0].event_ids == [
        "battle-event-0001",
        "battle-event-0002",
        "battle-event-0003",
    ]
    assert [edge.relation_type for edge in relations] == ["DAMAGE_FROM", "RESULT_OF"]


def test_move_volatile_status_uses_effect_match():
    events = [
        _event(1, "MOVE", 1.0, {"actor": "User", "move": "守住"}),
        _event(2, "VOLATILE_STATUS", 2.0, {"target": "User", "effect": "守住", "action": "apply"}),
    ]
    groups, relations = build_battle_timeline(events)
    assert len(groups) == 1
    assert relations[0].relation_type == "STATUS_FROM"


def test_switch_can_trigger_ability_and_matching_stat_change():
    switch_ability = [
        _event(1, "SWITCH", 1.0, {"actor": "A", "targets": ["A"], "action": "switch_in"}),
        _event(2, "ABILITY", 2.0, {"actor": "A", "ability": "Ability"}),
    ]
    groups, relations = build_battle_timeline(switch_ability)
    assert len(groups) == 1
    assert relations[0].rule_id == "switch.triggered_ability"

    switch_stat = [
        _event(1, "SWITCH", 1.0, {"actor": "A", "targets": ["A"], "action": "switch_in"}),
        _event(2, "STAT_CHANGE", 2.0, {"target": "A", "stat": "speed"}),
    ]
    groups, relations = build_battle_timeline(switch_stat)
    assert len(groups) == 1
    assert relations[0].rule_id == "switch.same_target_stat_change"


def test_transformation_chain_uses_actor_and_phase_not_short_fixed_gap():
    events = [
        _event(1, "TRANSFORMATION", 1.0, {"actor": "A", "action": "activate"}),
        _event(2, "TRANSFORMATION", 9.0, {"actor": "A", "action": "change"}),
    ]
    groups, relations = build_battle_timeline(events)
    assert len(groups) == 1
    assert groups[0].group_type == "ACTION_CHAIN"
    assert relations[0].relation_type == "SAME_ACTION"


def test_side_condition_can_be_move_consequence_or_standalone():
    triggered = [
        _event(1, "MOVE", 1.0, {"actor": "A", "move": "順風"}),
        _event(2, "SIDE_CONDITION", 2.0, {"effect": "順風", "side": "player"}),
    ]
    groups, relations = build_battle_timeline(triggered)
    assert len(groups) == 1
    assert relations[0].review_status == "auto_accepted"

    groups, relations = build_battle_timeline(
        [_event(1, "SIDE_CONDITION", 1.0, {"effect": "順風", "side": "player"})]
    )
    assert relations == []
    assert groups[0].review_status == "auto_accepted"


def test_residual_damage_is_not_attached_to_conflicting_move():
    events = [
        _event(1, "MOVE", 1.0, {"actor": "User", "move": "Move", "target": "A"}),
        _event(2, "DAMAGE_RESULT", 2.0, {"target": "B", "cause": "status", "action": "damage"}),
    ]
    groups, relations = build_battle_timeline(events)
    assert relations == []
    assert len(groups) == 2
    assert groups[1].review_status == "auto_accepted"


def test_multi_target_move_uses_sibling_edges_without_duplicate_consumption():
    events = [
        _event(1, "MOVE", 1.0, {"actor": "User", "move": "Spread", "targets": ["A", "B"]}),
        _event(2, "DAMAGE_RESULT", 2.0, {"target": "A", "action": "damage"}),
        _event(3, "DAMAGE_RESULT", 2.8, {"target": "B", "action": "damage"}),
        _event(4, "FAINT", 3.4, {"target": "A", "action": "faint"}),
    ]
    groups, relations = build_battle_timeline(events)
    assert len(groups) == 1
    assert len(groups[0].event_ids) == len(set(groups[0].event_ids)) == 4
    damage_edges = [edge for edge in relations if edge.relation_type == "DAMAGE_FROM"]
    assert {(edge.from_event_id, edge.to_event_id) for edge in damage_edges} == {
        ("battle-event-0001", "battle-event-0002"),
        ("battle-event-0001", "battle-event-0003"),
    }


def test_new_major_action_stops_previous_chain():
    events = [
        _event(1, "MOVE", 1.0, {"actor": "X", "move": "First", "target": "A"}),
        _event(2, "MOVE", 2.0, {"actor": "Y", "move": "Second", "target": "B"}),
        _event(3, "DAMAGE_RESULT", 3.0, {"target": "A", "action": "damage"}),
    ]
    groups, relations = build_battle_timeline(events)
    assert relations == []
    assert groups[-1].review_status == "unlinked"


def test_temporal_adjacency_does_not_merge_groups_and_metadata_match_is_stronger():
    weak_events = [
        _event(1, "MOVE", 1.0, {"actor": "User", "move": "Move"}),
        _event(2, "MOVE_RESULT", 2.0, {"target": "A", "result": "critical"}),
    ]
    weak_groups, weak_relations = build_battle_timeline(weak_events)
    assert len(weak_groups) == 2
    assert weak_relations[0].relation_type == "TEMPORALLY_ADJACENT"
    assert weak_relations[0].review_status == "needs_review"

    strong_events = deepcopy(weak_events)
    strong_events[0]["metadata"]["target"] = "A"
    strong_groups, strong_relations = build_battle_timeline(strong_events)
    assert len(strong_groups) == 1
    assert strong_relations[0].confidence > weak_relations[0].confidence


def test_metadata_conflict_rejects_nearest_move_and_preserves_unlinked_event():
    events = [
        _event(1, "MOVE", 1.0, {"actor": "A", "move": "羽棲"}),
        _event(2, "MOVE_RESULT", 2.0, {"target": "B", "move": "地震", "result": "prevented"}),
    ]
    groups, relations = build_battle_timeline(events)
    assert relations == []
    assert groups[1].group_type == "UNLINKED_EVENT"
    assert groups[1].event_ids == ["battle-event-0002"]


def test_short_fluctuation_same_effect_batches_but_different_effect_does_not():
    events = [
        _event(1, "VOLATILE_STATUS", 1.0, {"target": "A", "effect": "滅亡計時", "counter": 3, "action": "update"}),
        _event(2, "VOLATILE_STATUS", 1.6, {"target": "B", "effect": "滅亡計時", "counter": 3, "action": "update"}),
        _event(3, "VOLATILE_STATUS", 2.2, {"target": "C", "effect": "滅亡計時", "counter": 2, "action": "update"}),
    ]
    groups, relations = build_battle_timeline(events)
    assert groups[0].event_ids == ["battle-event-0001", "battle-event-0002"]
    assert groups[0].group_type == "EVENT_BATCH"
    assert "battle-event-0003" not in groups[0].event_ids
    assert len(relations) == 1


def test_builder_is_deterministic_and_human_fields_are_null():
    events = [
        _event(1, "MOVE", 1.0, {"actor": "A", "move": "守住"}),
        _event(2, "VOLATILE_STATUS", 2.0, {"target": "A", "effect": "守住", "action": "apply"}),
    ]
    first_groups, first_relations = build_battle_timeline(events)
    second_groups, second_relations = build_battle_timeline(events)
    assert [group.to_dict() for group in first_groups] == [
        group.to_dict() for group in second_groups
    ]
    assert [edge.to_dict() for edge in first_relations] == [
        edge.to_dict() for edge in second_relations
    ]
    assert all(value is None for value in human_review_defaults().values())
    assert all(value is None for value in first_groups[0].human_review.values())
    assert all(value is None for value in first_relations[0].human_review.values())
    assert "turn" not in str(first_groups[0].to_dict()).lower()
