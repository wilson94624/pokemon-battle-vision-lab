"""Checkpoint 1F reducer registry：將 BattleEvent 投影為保守 state operations。"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Sequence, Tuple

from .battle_state_mutator import ProjectionContext
from .battle_state_policy import STAT_ALIASES


Reducer = Callable[[ProjectionContext, Mapping[str, Any]], None]


@dataclass(frozen=True)
class ReducerSpec:
    rule_id: str
    event_type: str
    required_metadata: Tuple[str, ...]
    optional_metadata: Tuple[str, ...]
    operation_policy: str
    confidence_policy: str = "event_confidence_times_rule_factor"
    conflict_policy: str = "record_conflict_without_silent_overwrite"
    unknown_policy: str = "record_unresolved"


class ReducerRegistry:
    def __init__(self) -> None:
        self._reducers: Dict[str, Tuple[ReducerSpec, Reducer]] = {}

    def register(self, spec: ReducerSpec, reducer: Reducer) -> None:
        if spec.event_type in self._reducers:
            raise ValueError("Reducer 重複註冊：{}".format(spec.event_type))
        self._reducers[spec.event_type] = (spec, reducer)

    @property
    def specs(self) -> Sequence[ReducerSpec]:
        return [self._reducers[key][0] for key in sorted(self._reducers)]

    def apply(self, context: ProjectionContext, event: Mapping[str, Any]) -> None:
        context.set_event(event)
        event_type = str(event["event_type"])
        selected = self._reducers.get(event_type)
        if selected is None:
            context.add_unresolved(
                "unsupported_event_type",
                ["event_type"],
                "state.unsupported_event",
            )
            return
        spec, reducer = selected
        metadata = event.get("metadata", {})
        missing = [name for name in spec.required_metadata if metadata.get(name) is None]
        # SWITCH 的 target 是 targets array；BATTLE_RESULT 只需 result。
        if missing:
            context.add_unresolved(
                "required_metadata_missing",
                missing,
                spec.rule_id,
            )
            return
        reducer(context, event)


def _switch(context: ProjectionContext, event: Mapping[str, Any]) -> None:
    metadata = event["metadata"]
    targets = metadata.get("targets") or []
    if not targets:
        context.add_unresolved(
            "switch_targets_missing",
            ["targets"],
            "state.switch.missing_targets",
        )
        return
    for name in targets:
        entity = context.resolve_entity(str(name), metadata.get("side"))
        if entity is not None:
            context.set_active(entity)


def _faint(context: ProjectionContext, event: Mapping[str, Any]) -> None:
    metadata = event["metadata"]
    entity = context.resolve_entity(metadata.get("target"), metadata.get("side"))
    if entity is not None:
        context.mark_fainted(entity)


def _status(context: ProjectionContext, event: Mapping[str, Any]) -> None:
    metadata = event["metadata"]
    entity = context.resolve_entity(metadata.get("target"), metadata.get("side"))
    if entity is None:
        return
    action = str(metadata.get("action"))
    if action in {"clear", "cure", "end"}:
        context.set_status(entity, None)
    elif action in {"inflict", "apply", "start"}:
        context.set_status(entity, str(metadata["status"]))
    else:
        context.add_unresolved(
            "status_action_unsupported",
            ["action"],
            "state.status.unsupported_action",
        )


def _volatile(context: ProjectionContext, event: Mapping[str, Any]) -> None:
    metadata = event["metadata"]
    entity = context.resolve_entity(metadata.get("target"), metadata.get("side"))
    if entity is None:
        return
    action = str(metadata.get("action"))
    effect = str(metadata["effect"])
    if action in {"start", "apply", "update"}:
        context.set_volatile(entity, effect, True, metadata.get("counter"))
    elif action in {"end", "remove", "clear"}:
        context.set_volatile(entity, effect, False, metadata.get("counter"))
    else:
        context.add_unresolved(
            "volatile_action_unsupported",
            ["action"],
            "state.volatile.unsupported_action",
        )


def _split_stats(raw: str) -> Sequence[str]:
    normalized = raw.replace(",", "、")
    return [item.strip() for item in normalized.split("、") if item.strip()]


def _stat_change(context: ProjectionContext, event: Mapping[str, Any]) -> None:
    metadata = event["metadata"]
    raw_targets = metadata.get("targets") or [metadata.get("target")]
    targets = [str(item) for item in raw_targets if item]
    if not targets:
        context.add_unresolved(
            "stat_target_missing",
            ["target_or_targets"],
            "state.stat_stage.missing_target",
        )
        return
    direction = str(metadata["direction"])
    magnitude = int(metadata["magnitude"])
    if direction == "lower":
        magnitude = -magnitude
    elif direction != "raise":
        context.add_unresolved(
            "stat_direction_unsupported",
            ["direction"],
            "state.stat_stage.unsupported_direction",
        )
        return
    stats = _split_stats(str(metadata["stat"]))
    unknown_stats = [stat for stat in stats if stat not in STAT_ALIASES]
    if unknown_stats:
        context.add_unresolved(
            "stat_name_unsupported",
            ["stat"],
            "state.stat_stage.unsupported_stat",
        )
        return
    for target in targets:
        entity = context.resolve_entity(target, metadata.get("side"))
        if entity is None:
            continue
        for stat in stats:
            context.change_stat_stage(entity, STAT_ALIASES[stat], magnitude)


def _weather(context: ProjectionContext, event: Mapping[str, Any]) -> None:
    metadata = event["metadata"]
    action = str(metadata["action"])
    if action == "start":
        context.set_weather(str(metadata["weather"]))
    elif action == "end":
        context.set_weather(None)
    else:
        context.add_unresolved(
            "weather_action_unsupported",
            ["action"],
            "state.weather.unsupported_action",
        )


def _side_condition(context: ProjectionContext, event: Mapping[str, Any]) -> None:
    metadata = event["metadata"]
    side = metadata.get("side")
    if side not in {"player", "opponent"}:
        context.add_unresolved(
            "side_condition_side_missing",
            ["side"],
            "state.side_condition.missing_side",
        )
        return
    action = str(metadata["action"])
    if action not in {"start", "end"}:
        context.add_unresolved(
            "side_condition_action_unsupported",
            ["action"],
            "state.side_condition.unsupported_action",
        )
        return
    context.set_side_condition(str(side), str(metadata["effect"]), action == "start")


def _field_effect(context: ProjectionContext, event: Mapping[str, Any]) -> None:
    metadata = event["metadata"]
    if str(metadata["action"]) not in {"activate", "start"}:
        context.add_unresolved(
            "field_effect_action_unsupported",
            ["action"],
            "state.field_effect.unsupported_action",
        )
        return
    context.set_field_effect(str(metadata["effect"]), metadata.get("counter"))


def _transformation(context: ProjectionContext, event: Mapping[str, Any]) -> None:
    metadata = event["metadata"]
    entity = context.resolve_entity(metadata.get("actor"), metadata.get("side"))
    if entity is None:
        return
    action = str(metadata["action"])
    if action == "activate":
        value = {
            "phase": "activated",
            "form": None,
            "item": metadata.get("item"),
            "device": metadata.get("device"),
        }
    elif action == "change":
        value = {
            "phase": "completed",
            "form": metadata.get("form"),
            "item": None,
            "device": None,
        }
    else:
        context.add_unresolved(
            "transformation_action_unsupported",
            ["action"],
            "state.transformation.unsupported_action",
        )
        return
    context.set_transformation(entity, value)


def _ability(context: ProjectionContext, event: Mapping[str, Any]) -> None:
    metadata = event["metadata"]
    entity = context.resolve_entity(metadata.get("actor"), metadata.get("side"))
    if entity is not None:
        context.append_known_evidence(
            entity,
            "known_ability",
            str(metadata["ability"]),
            "SET_KNOWN_ABILITY",
            "state.ability.observe",
        )


def _item(context: ProjectionContext, event: Mapping[str, Any]) -> None:
    metadata = event["metadata"]
    entity = context.resolve_entity(metadata.get("actor"), metadata.get("side"))
    if entity is not None:
        context.append_known_evidence(
            entity,
            "known_item",
            str(metadata["item"]),
            "SET_KNOWN_ITEM",
            "state.item.observe",
        )


def _battle_result(context: ProjectionContext, event: Mapping[str, Any]) -> None:
    metadata = event["metadata"]
    result = str(metadata["result"])
    if result == "forfeit":
        context.set_battle_fact("termination_reason", "forfeit")
    else:
        context.set_battle_fact("result", result)
    if metadata.get("winner"):
        context.set_battle_fact("winner", str(metadata["winner"]))
    if metadata.get("loser"):
        context.set_battle_fact("loser", str(metadata["loser"]))


def _move(context: ProjectionContext, event: Mapping[str, Any]) -> None:
    metadata = event["metadata"]
    if metadata.get("actor"):
        context.resolve_entity(metadata.get("actor"), metadata.get("side"))
    context.add_no_op("move_does_not_directly_change_persistent_state", "state.move.no_op")


def _move_result(context: ProjectionContext, event: Mapping[str, Any]) -> None:
    metadata = event["metadata"]
    if metadata.get("target"):
        context.resolve_entity(metadata.get("target"), metadata.get("side"))
    if context.accepted_unlinked:
        context.add_unresolved(
            "accepted_unlinked_observation",
            ["direct_parent_relation"],
            "state.move_result.unlinked_observation",
        )
        return
    context.add_no_op(
        "move_result_has_no_supported_persistent_state_update",
        "state.move_result.no_op",
    )


def _damage_result(context: ProjectionContext, event: Mapping[str, Any]) -> None:
    metadata = event["metadata"]
    if metadata.get("target"):
        context.resolve_entity(metadata.get("target"), metadata.get("side"))
    context.add_no_op(
        "damage_result_does_not_reconstruct_hp_or_status",
        "state.damage_result.no_op",
    )


def build_reducer_registry() -> ReducerRegistry:
    registry = ReducerRegistry()
    definitions = (
        (ReducerSpec("state.switch", "SWITCH", ("action",), ("targets", "side"), "REGISTER_POKEMON+SET_ACTIVE"), _switch),
        (ReducerSpec("state.faint", "FAINT", ("action", "target"), ("side",), "MARK_FAINTED+SET_INACTIVE"), _faint),
        (ReducerSpec("state.status", "STATUS", ("action", "status", "target"), ("side",), "SET_STATUS_OR_CLEAR_STATUS"), _status),
        (ReducerSpec("state.volatile", "VOLATILE_STATUS", ("action", "effect", "target"), ("side", "counter", "move"), "ADD_REMOVE_OR_UPDATE_VOLATILE"), _volatile),
        (ReducerSpec("state.stat_stage", "STAT_CHANGE", ("action", "stat", "direction", "magnitude"), ("target", "targets", "side"), "CHANGE_STAT_STAGE"), _stat_change),
        (ReducerSpec("state.weather", "WEATHER", ("action", "weather"), (), "SET_OR_CLEAR_WEATHER"), _weather),
        (ReducerSpec("state.side_condition", "SIDE_CONDITION", ("action", "effect", "side"), (), "ADD_OR_REMOVE_SIDE_CONDITION"), _side_condition),
        (ReducerSpec("state.field_effect", "FIELD_EFFECT", ("action", "effect"), ("counter",), "ADD_FIELD_EFFECT"), _field_effect),
        (ReducerSpec("state.transformation", "TRANSFORMATION", ("action", "actor"), ("side", "form", "item", "device"), "SET_TRANSFORMATION"), _transformation),
        (ReducerSpec("state.ability", "ABILITY", ("action", "actor", "ability"), ("side",), "SET_KNOWN_ABILITY"), _ability),
        (ReducerSpec("state.item", "ITEM", ("action", "actor", "item"), ("side",), "SET_KNOWN_ITEM"), _item),
        (ReducerSpec("state.battle_result", "BATTLE_RESULT", ("action", "result"), ("winner", "loser"), "SET_BATTLE_RESULT"), _battle_result),
        (ReducerSpec("state.move.no_op", "MOVE", ("action", "actor", "move"), ("side",), "NO_OP_IDENTITY_EVIDENCE_ONLY"), _move),
        (ReducerSpec("state.move_result.no_op", "MOVE_RESULT", ("action", "result"), ("target", "side", "effect", "move"), "NO_OP_OR_UNLINKED_OBSERVATION"), _move_result),
        (ReducerSpec("state.damage_result.no_op", "DAMAGE_RESULT", ("action", "target"), ("side", "status", "cause"), "NO_OP_NO_HP_RECONSTRUCTION"), _damage_result),
    )
    for spec, reducer in definitions:
        registry.register(spec, reducer)
    return registry
