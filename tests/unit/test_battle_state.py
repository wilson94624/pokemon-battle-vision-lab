import json
from copy import deepcopy
from pathlib import Path

import pytest

from pokemon_battle_vision.battle_state_confidence import (
    state_completeness,
    state_confidence,
)
from pokemon_battle_vision.battle_state_models import initial_battle_state, pokemon_state
from pokemon_battle_vision.battle_state_mutator import ProjectionContext
from pokemon_battle_vision.battle_state_policy import clamp_stat_stage
from pokemon_battle_vision.battle_state_projector import project_battle_state
from pokemon_battle_vision.battle_state_reducers import build_reducer_registry


ROOT = Path(__file__).resolve().parents[2]


def _event(index, event_type, metadata, confidence=0.95):
    return {
        "id": "battle-event-{:04d}".format(index),
        "event_type": event_type,
        "start_time": float(index),
        "end_time": float(index) + 0.5,
        "timestamp": float(index),
        "confidence": confidence,
        "raw_text": "fixture",
        "metadata": metadata,
    }


def _apply(event, state=None, accepted_unlinked=False, context=None):
    state = state or initial_battle_state()
    if context is None:
        context = ProjectionContext(
            state,
            "timeline-0001",
            {},
            accepted_unlinked,
            [],
            1,
        )
    build_reducer_registry().apply(context, event)
    return context


@pytest.fixture(scope="module")
def real_projection():
    return project_battle_state(
        json.loads((ROOT / "outputs/checkpoint-1d/battle_events.json").read_text()),
        json.loads((ROOT / "outputs/checkpoint-1e/battle_timeline.json").read_text()),
        json.loads((ROOT / "outputs/checkpoint-1e/timeline_relations.json").read_text()),
        json.loads(
            (ROOT / "outputs/checkpoint-1e-review/needs_review_relations.json").read_text()
        ),
        json.loads((ROOT / "outputs/checkpoint-1e-review/unlinked_events.json").read_text()),
    )


def test_switch_registers_pokemon():
    context = _apply(
        _event(1, "SWITCH", {"action": "switch_in", "targets": ["甲"]})
    )
    assert "unknown:甲" in context.state["battle"]["unassigned_pokemon"]
    assert any(item.operation == "REGISTER_POKEMON" for item in context.operations)


def test_switch_sets_observed_active():
    context = _apply(
        _event(1, "SWITCH", {"action": "switch_in", "targets": ["甲"]})
    )
    entity = context.state["battle"]["unassigned_pokemon"]["unknown:甲"]
    assert entity["active"]["knowledge"] == "known"
    assert entity["active"]["value"] is True


def test_switch_does_not_guess_slot():
    context = _apply(
        _event(1, "SWITCH", {"action": "switch_in", "targets": ["甲"]})
    )
    assert context.state["player_side"]["active_slots"]["knowledge"] == "not_applicable"
    assert context.state["opponent_side"]["active_slots"]["knowledge"] == "not_applicable"


def test_faint_marks_fainted_and_inactive():
    state = initial_battle_state()
    _apply(_event(1, "SWITCH", {"action": "switch_in", "targets": ["甲"]}), state)
    context = _apply(_event(2, "FAINT", {"action": "faint", "target": "甲"}), state)
    entity = state["battle"]["unassigned_pokemon"]["unknown:甲"]
    assert entity["fainted"]["value"] is True
    assert entity["active"]["value"] is False
    assert {item.operation for item in context.operations} == {"MARK_FAINTED", "SET_INACTIVE"}


def test_status_applies_to_explicit_target():
    context = _apply(
        _event(
            1,
            "STATUS",
            {"action": "inflict", "target": "甲", "side": "opponent", "status": "灼傷"},
        )
    )
    entity = context.state["opponent_side"]["known_pokemon"]["opponent:甲"]
    assert entity["status"]["value"] == "灼傷"


def test_status_missing_target_is_unresolved():
    context = _apply(
        _event(1, "STATUS", {"action": "inflict", "status": "灼傷"})
    )
    assert context.unresolved_updates[0].reason == "required_metadata_missing"


def test_volatile_status_applies():
    context = _apply(
        _event(1, "VOLATILE_STATUS", {"action": "start", "target": "甲", "effect": "守住"})
    )
    entity = context.state["battle"]["unassigned_pokemon"]["unknown:甲"]
    assert entity["volatile_statuses"]["守住"]["active"]["value"] is True


def test_volatile_status_removes():
    state = initial_battle_state()
    _apply(_event(1, "VOLATILE_STATUS", {"action": "start", "target": "甲", "effect": "再來一次"}), state)
    _apply(_event(2, "VOLATILE_STATUS", {"action": "end", "target": "甲", "effect": "再來一次"}), state)
    entity = state["battle"]["unassigned_pokemon"]["unknown:甲"]
    assert entity["volatile_statuses"]["再來一次"]["active"]["value"] is False


def test_perish_counter_updates():
    context = _apply(
        _event(1, "VOLATILE_STATUS", {"action": "update", "target": "甲", "effect": "滅亡計時", "counter": 2})
    )
    entity = context.state["battle"]["unassigned_pokemon"]["unknown:甲"]
    assert entity["volatile_statuses"]["滅亡計時"]["counter"]["value"] == 2


def test_stat_change_positive_preserves_unknown_absolute():
    context = _apply(
        _event(1, "STAT_CHANGE", {"action": "change", "target": "甲", "stat": "攻擊", "direction": "raise", "magnitude": 2})
    )
    stage = context.state["battle"]["unassigned_pokemon"]["unknown:甲"]["stat_stages"]["attack"]
    assert stage["knowledge"] == "unknown"
    assert stage["observed_net_change"] == 2


def test_stat_change_negative():
    context = _apply(
        _event(1, "STAT_CHANGE", {"action": "change", "target": "甲", "stat": "攻擊", "direction": "lower", "magnitude": 1})
    )
    stage = context.state["battle"]["unassigned_pokemon"]["unknown:甲"]["stat_stages"]["attack"]
    assert stage["observed_net_change"] == -1


def test_stat_stage_clamp_policy():
    assert clamp_stat_stage(9) == 6
    assert clamp_stat_stage(-9) == -6
    assert clamp_stat_stage(3) == 3


def test_weather_start():
    context = _apply(_event(1, "WEATHER", {"action": "start", "weather": "雨"}))
    assert context.state["field"]["weather"]["value"] == "雨"


def test_weather_end():
    state = initial_battle_state()
    _apply(_event(1, "WEATHER", {"action": "start", "weather": "雨"}), state)
    _apply(_event(2, "WEATHER", {"action": "end", "weather": "雨"}), state)
    assert state["field"]["weather"]["knowledge"] == "known"
    assert state["field"]["weather"]["value"] is None


def test_side_condition_start():
    context = _apply(_event(1, "SIDE_CONDITION", {"action": "start", "effect": "順風", "side": "opponent"}))
    assert context.state["opponent_side"]["side_conditions"]["順風"]["active"]["value"] is True


def test_side_condition_end():
    state = initial_battle_state()
    _apply(_event(1, "SIDE_CONDITION", {"action": "start", "effect": "順風", "side": "opponent"}), state)
    _apply(_event(2, "SIDE_CONDITION", {"action": "end", "effect": "順風", "side": "opponent"}), state)
    assert state["opponent_side"]["side_conditions"]["順風"]["active"]["value"] is False


def test_transformation_chain():
    state = initial_battle_state()
    _apply(_event(1, "TRANSFORMATION", {"action": "activate", "actor": "甲", "item": "進化石"}), state)
    _apply(_event(2, "TRANSFORMATION", {"action": "change", "actor": "甲", "form": "超級甲"}), state)
    entity = state["battle"]["unassigned_pokemon"]["unknown:甲"]
    assert entity["transformation"]["value"]["phase"] == "completed"
    assert entity["transformation"]["value"]["form"] == "超級甲"


def test_ability_records_observed_evidence():
    context = _apply(_event(1, "ABILITY", {"action": "activate", "actor": "甲", "ability": "威嚇"}))
    entity = context.state["battle"]["unassigned_pokemon"]["unknown:甲"]
    assert entity["known_ability"]["value"] == ["威嚇"]


def test_item_records_observed_evidence():
    context = _apply(_event(1, "ITEM", {"action": "activate", "actor": "甲", "item": "生命寶珠"}))
    entity = context.state["battle"]["unassigned_pokemon"]["unknown:甲"]
    assert entity["known_item"]["value"] == ["生命寶珠"]


def test_battle_result_keeps_partial_winner_unknown():
    context = _apply(_event(1, "BATTLE_RESULT", {"action": "end", "result": "win", "loser": "對手"}))
    assert context.state["battle"]["result"]["value"] == "win"
    assert context.state["battle"]["loser"]["value"] == "對手"
    assert context.state["battle"]["winner"]["knowledge"] == "unknown"


def test_rejected_relation_not_accepted(real_projection):
    policy = real_projection["audit"]["relation_policy"]
    assert set(policy["rejected_relation_ids"]) == {"relation-0019", "relation-0030", "relation-0036", "relation-0041"}
    assert not set(policy["rejected_relation_ids"]) & set(policy["accepted_relation_ids"])


def test_accepted_unlinked_is_unresolved_without_parent(real_projection):
    rows = [item for item in real_projection["unresolved_updates"] if item["reason"] == "accepted_unlinked_observation"]
    assert {item["timeline_id"] for item in rows} == {"timeline-0056", "timeline-0063"}


def test_missing_target_conflict_is_not_silently_applied():
    context = _apply(_event(1, "FAINT", {"action": "faint"}))
    assert context.unresolved_updates
    assert not context.operations


def test_target_identity_conflict_is_recorded():
    state = initial_battle_state()
    state["player_side"]["known_pokemon"]["player:甲"] = pokemon_state(
        "player:甲", "甲", "player"
    )
    state["opponent_side"]["known_pokemon"]["opponent:甲"] = pokemon_state(
        "opponent:甲", "甲", "opponent"
    )
    context = _apply(
        _event(1, "FAINT", {"action": "faint", "target": "甲"}),
        state,
    )
    assert not context.operations
    assert context.global_conflicts[-1].conflict_type == "entity_identity_ambiguous"


def test_side_conflict_is_recorded():
    state = initial_battle_state()
    context = _apply(_event(1, "STATUS", {"action": "inflict", "target": "甲", "side": "player", "status": "灼傷"}), state)
    context.set_event(_event(2, "STATUS", {"action": "inflict", "target": "甲", "side": "opponent", "status": "麻痺"}))
    assert context.resolve_entity("甲", "opponent") is None
    assert context.global_conflicts[-1].conflict_type == "entity_side_conflict"


def test_fainted_pokemon_reactivation_conflict():
    state = initial_battle_state()
    context = _apply(_event(1, "SWITCH", {"action": "switch_in", "targets": ["甲"]}), state)
    _apply(_event(2, "FAINT", {"action": "faint", "target": "甲"}), state)
    entity = state["battle"]["unassigned_pokemon"]["unknown:甲"]
    context.set_event(_event(3, "SWITCH", {"action": "switch_in", "targets": ["甲"]}))
    context.set_active(entity)
    assert context.global_conflicts[-1].conflict_type == "fainted_pokemon_reactivated"


def test_weather_conflict():
    state = initial_battle_state()
    context = _apply(_event(1, "WEATHER", {"action": "start", "weather": "雨"}), state)
    context.set_event(_event(2, "WEATHER", {"action": "start", "weather": "雪"}))
    context.set_weather("雪")
    assert context.global_conflicts[-1].conflict_type == "weather_conflict"


def test_projection_never_writes_hp(real_projection):
    operations = [op["operation"] for delta in real_projection["deltas"] for op in delta["operations"]]
    assert "SET_HP" not in operations
    assert "CHANGE_HP" not in operations


def test_projection_never_infers_turn(real_projection):
    assert real_projection["snapshots"][-1]["battle"]["official_turn"]["knowledge"] == "not_applicable"


def test_projection_never_infers_active_slot(real_projection):
    final = real_projection["snapshots"][-1]
    assert final["player_side"]["active_slots"]["knowledge"] == "not_applicable"
    assert final["opponent_side"]["active_slots"]["knowledge"] == "not_applicable"


def test_all_70_groups_are_covered(real_projection):
    assert len(real_projection["deltas"]) == 70
    assert len(real_projection["snapshots"]) == 71


def test_snapshot_order_is_deterministic(real_projection):
    assert [item["sequence"] for item in real_projection["snapshots"]] == list(range(71))
    timestamps = [item["timestamp"] for item in real_projection["snapshots"]]
    assert timestamps == sorted(timestamps)


def test_delta_order_is_deterministic(real_projection):
    assert [item["delta_id"] for item in real_projection["deltas"]] == ["delta-{:04d}".format(i) for i in range(1, 71)]


def test_previous_snapshot_chain(real_projection):
    snapshots = real_projection["snapshots"]
    assert all(snapshots[i]["previous_snapshot_id"] == snapshots[i - 1]["snapshot_id"] for i in range(1, len(snapshots)))


def test_confidence_calculation_uses_known_facts():
    state = initial_battle_state()
    assert state_confidence(state) == 0.0
    _apply(_event(1, "WEATHER", {"action": "start", "weather": "雨"}, confidence=0.8), state)
    assert state_confidence(state) == 0.8


def test_completeness_is_independent_from_confidence():
    state = initial_battle_state()
    _apply(_event(1, "WEATHER", {"action": "start", "weather": "雨"}, confidence=0.99), state)
    assert state_confidence(state) == 0.99
    assert state_completeness(state) == pytest.approx(0.08)


def test_human_fields_default_null(real_projection):
    rows = real_projection["snapshots"] + real_projection["deltas"] + real_projection["conflicts"]
    assert all(all(value is None for value in row["human_review"].values()) for row in rows)
