"""Checkpoint 1F state mutation、entity resolution 與 conflict detection。"""

from copy import deepcopy
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .battle_state_models import (
    StateConflict,
    StateOperation,
    UnresolvedUpdate,
    knowledge_value,
    pokemon_state,
)
from .battle_state_policy import (
    MAX_OBSERVED_ACTIVE_PER_SIDE,
    RULE_CONFIDENCE_FACTORS,
    clamp_stat_stage,
)


SIDE_KEYS = {"player": "player_side", "opponent": "opponent_side"}


class ProjectionContext:
    """單一 Timeline Group 的 mutation context；所有更新都留下 provenance。"""

    def __init__(
        self,
        state: Dict[str, Any],
        timeline_id: str,
        accepted_relation_ids_by_event: Mapping[str, Sequence[str]],
        accepted_unlinked: bool,
        global_conflicts: List[StateConflict],
        unresolved_start: int,
    ) -> None:
        self.state = state
        self.timeline_id = timeline_id
        self.accepted_relation_ids_by_event = accepted_relation_ids_by_event
        self.accepted_unlinked = accepted_unlinked
        self.global_conflicts = global_conflicts
        self.unresolved_next = unresolved_start
        self.operations: List[StateOperation] = []
        self.unresolved_updates: List[UnresolvedUpdate] = []
        self.no_op_reasons: List[str] = []
        self.projection_rule_ids: List[str] = []
        self.review_reasons: List[str] = []
        self.event: Mapping[str, Any] = {}

    def set_event(self, event: Mapping[str, Any]) -> None:
        self.event = event

    @property
    def event_id(self) -> str:
        return str(self.event["id"])

    @property
    def event_confidence(self) -> float:
        return float(self.event.get("confidence", 0.0))

    @property
    def timestamp(self) -> float:
        return float(self.event["end_time"])

    def relation_ids(self) -> List[str]:
        return list(self.accepted_relation_ids_by_event.get(self.event_id, []))

    def rule_confidence(self, policy: str = "explicit_metadata") -> float:
        return round(
            self.event_confidence * RULE_CONFIDENCE_FACTORS[policy],
            6,
        )

    def add_operation(
        self,
        operation: str,
        entity: str,
        field: str,
        before: Any,
        after: Any,
        rule_id: str,
        confidence_policy: str = "explicit_metadata",
        evidence: Optional[List[str]] = None,
    ) -> None:
        self.projection_rule_ids.append(rule_id)
        self.operations.append(
            StateOperation(
                operation=operation,
                entity=entity,
                field=field,
                before=deepcopy(before),
                after=deepcopy(after),
                confidence=self.rule_confidence(confidence_policy),
                rule_id=rule_id,
                source_event_ids=[self.event_id],
                source_relation_ids=self.relation_ids(),
                evidence=list(evidence or []),
            )
        )

    def add_no_op(self, reason: str, rule_id: str) -> None:
        self.no_op_reasons.append("{}:{}".format(self.event_id, reason))
        self.projection_rule_ids.append(rule_id)

    def add_unresolved(
        self,
        reason: str,
        missing_fields: Sequence[str],
        rule_id: str,
    ) -> None:
        unresolved = UnresolvedUpdate(
            unresolved_id="unresolved-{:04d}".format(self.unresolved_next),
            timeline_id=self.timeline_id,
            event_id=self.event_id,
            event_type=str(self.event["event_type"]),
            reason=reason,
            missing_fields=list(missing_fields),
            evidence={
                "raw_text": self.event.get("raw_text"),
                "metadata": deepcopy(self.event.get("metadata", {})),
                "source_relation_ids": self.relation_ids(),
            },
            confidence=self.rule_confidence("unresolved"),
        )
        self.unresolved_next += 1
        self.unresolved_updates.append(unresolved)
        self.review_reasons.append(reason)
        self.projection_rule_ids.append(rule_id)

    def add_conflict(
        self,
        conflict_type: str,
        entity: str,
        field: str,
        existing: Any,
        proposed: Any,
        rule_id: str,
    ) -> StateConflict:
        conflict = StateConflict(
            conflict_id="conflict-{:04d}".format(len(self.global_conflicts) + 1),
            timeline_id=self.timeline_id,
            event_id=self.event_id,
            conflict_type=conflict_type,
            entity=entity,
            field=field,
            existing=deepcopy(existing),
            proposed=deepcopy(proposed),
            evidence={
                "raw_text": self.event.get("raw_text"),
                "metadata": deepcopy(self.event.get("metadata", {})),
                "rule_id": rule_id,
            },
            confidence=self.event_confidence,
        )
        self.global_conflicts.append(conflict)
        self.review_reasons.append(conflict_type)
        self.projection_rule_ids.append(rule_id)
        return conflict

    def _entity_locations(self, name: str) -> List[Tuple[Optional[str], str, Dict[str, Any]]]:
        locations: List[Tuple[Optional[str], str, Dict[str, Any]]] = []
        for side, side_key in SIDE_KEYS.items():
            for entity_id, entity in self.state[side_key]["known_pokemon"].items():
                if entity["name"] == name:
                    locations.append((side, entity_id, entity))
        for entity_id, entity in self.state["battle"]["unassigned_pokemon"].items():
            if entity["name"] == name:
                locations.append((None, entity_id, entity))
        return locations

    def _register_entity(self, name: str, side: Optional[str]) -> Dict[str, Any]:
        entity_id = "{}:{}".format(side or "unknown", name)
        entity = pokemon_state(entity_id, name, side)
        entity["provenance"].append(
            {"event_id": self.event_id, "timeline_id": self.timeline_id}
        )
        if side:
            self.state[SIDE_KEYS[side]]["known_pokemon"][entity_id] = entity
        else:
            self.state["battle"]["unassigned_pokemon"][entity_id] = entity
        self.add_operation(
            "REGISTER_POKEMON",
            entity_id,
            "identity",
            None,
            {"name": name, "side": side},
            "state.entity.register",
            "explicit_metadata" if side else "unknown_side_entity",
            ["name={} ".format(name).strip(), "side={}".format(side)],
        )
        if not side:
            self.review_reasons.append("side_unknown")
        return entity

    def resolve_entity(
        self,
        name: Optional[str],
        side: Optional[str],
        create: bool = True,
    ) -> Optional[Dict[str, Any]]:
        if not name:
            self.add_unresolved(
                "target_missing",
                ["target_or_actor"],
                "state.entity.missing_target",
            )
            return None
        if side not in {None, "player", "opponent"}:
            self.add_unresolved(
                "side_invalid",
                ["side"],
                "state.entity.invalid_side",
            )
            return None
        matches = self._entity_locations(str(name))
        if side:
            target_matches = [item for item in matches if item[0] == side]
            other_known = [item for item in matches if item[0] not in {None, side}]
            unassigned = [item for item in matches if item[0] is None]
            if len(target_matches) == 1:
                return target_matches[0][2]
            if len(target_matches) > 1 or other_known:
                self.add_conflict(
                    "entity_side_conflict",
                    str(name),
                    "side",
                    [item[0] for item in matches],
                    side,
                    "state.entity.side_conflict",
                )
                return None
            if len(unassigned) == 1:
                _, old_id, entity = unassigned[0]
                del self.state["battle"]["unassigned_pokemon"][old_id]
                new_id = "{}:{}".format(side, name)
                before = {"entity_id": old_id, "side": deepcopy(entity["side"])}
                entity["entity_id"] = new_id
                entity["side"] = knowledge_value(
                    "known",
                    side,
                    self.rule_confidence("unique_entity_resolution"),
                    [self.event_id],
                    [self.timeline_id],
                    self.timestamp,
                )
                entity["provenance"].append(
                    {"event_id": self.event_id, "timeline_id": self.timeline_id}
                )
                self.state[SIDE_KEYS[side]]["known_pokemon"][new_id] = entity
                self._replace_active_entity_id(old_id, new_id, side, entity)
                self.add_operation(
                    "RESOLVE_ENTITY_SIDE",
                    new_id,
                    "side",
                    before,
                    {"entity_id": new_id, "side": side},
                    "state.entity.resolve_side",
                    "unique_entity_resolution",
                )
                return entity
            if len(unassigned) > 1:
                self.add_conflict(
                    "entity_identity_ambiguous",
                    str(name),
                    "identity",
                    [item[1] for item in unassigned],
                    side,
                    "state.entity.ambiguous",
                )
                return None
            return self._register_entity(str(name), side) if create else None

        if len(matches) == 1:
            return matches[0][2]
        if len(matches) > 1:
            self.add_conflict(
                "entity_identity_ambiguous",
                str(name),
                "identity",
                [item[1] for item in matches],
                None,
                "state.entity.ambiguous",
            )
            return None
        return self._register_entity(str(name), None) if create else None

    def _replace_active_entity_id(
        self,
        old_id: str,
        new_id: str,
        side: str,
        entity: Dict[str, Any],
    ) -> None:
        active = self.state[SIDE_KEYS[side]]["active"]
        values = list(active["value"] or [])
        if old_id in values:
            values[values.index(old_id)] = new_id
        elif entity["active"]["knowledge"] == "known" and entity["active"]["value"]:
            if len(values) < MAX_OBSERVED_ACTIVE_PER_SIDE:
                values.append(new_id)
        active["value"] = sorted(set(values))

    def _set_entity_fact(
        self,
        entity: Dict[str, Any],
        field: str,
        value: Any,
        operation: str,
        rule_id: str,
        allow_replace: bool = False,
    ) -> bool:
        current = entity[field]
        before = deepcopy(current)
        if (
            current["knowledge"] == "known"
            and current["value"] != value
            and not allow_replace
        ):
            self.add_conflict(
                "field_value_conflict",
                entity["entity_id"],
                field,
                current,
                value,
                rule_id,
            )
            current["knowledge"] = "conflicted"
            current["value"] = {
                "existing": before["value"],
                "proposed": value,
            }
            return False
        entity[field] = knowledge_value(
            "known",
            value,
            self.rule_confidence(),
            sorted(set(current.get("source_event_ids", []) + [self.event_id])),
            sorted(
                set(current.get("source_timeline_ids", []) + [self.timeline_id])
            ),
            self.timestamp,
        )
        entity["provenance"].append(
            {"event_id": self.event_id, "timeline_id": self.timeline_id}
        )
        self.add_operation(
            operation,
            entity["entity_id"],
            field,
            before,
            entity[field],
            rule_id,
        )
        return True

    def set_active(self, entity: Dict[str, Any]) -> None:
        if entity["fainted"]["knowledge"] == "known" and entity["fainted"]["value"]:
            self.add_conflict(
                "fainted_pokemon_reactivated",
                entity["entity_id"],
                "active",
                entity["fainted"],
                True,
                "state.active.fainted_conflict",
            )
            return
        side = entity["side"]["value"] if entity["side"]["knowledge"] == "known" else None
        if side:
            active = self.state[SIDE_KEYS[side]]["active"]
            values = list(active["value"] or [])
            if entity["entity_id"] not in values and len(values) >= MAX_OBSERVED_ACTIVE_PER_SIDE:
                self.add_conflict(
                    "active_set_overflow",
                    side,
                    "active",
                    values,
                    entity["entity_id"],
                    "state.active.overflow",
                )
                active["knowledge"] = "conflicted"
                return
            if entity["entity_id"] not in values:
                values.append(entity["entity_id"])
            active["value"] = sorted(values)
            # 無 slot／完整 switch-out 證據，side active collection 仍是 partial unknown。
            active["knowledge"] = "unknown"
            active["source_event_ids"] = sorted(
                set(active["source_event_ids"] + [self.event_id])
            )
        else:
            self.review_reasons.append("active_set_ambiguity")
        self._set_entity_fact(
            entity,
            "active",
            True,
            "SET_ACTIVE",
            "state.active.set",
            allow_replace=True,
        )

    def set_inactive(self, entity: Dict[str, Any], rule_id: str) -> None:
        side = entity["side"]["value"] if entity["side"]["knowledge"] == "known" else None
        if side:
            active = self.state[SIDE_KEYS[side]]["active"]
            active["value"] = [
                item for item in (active["value"] or []) if item != entity["entity_id"]
            ]
        self._set_entity_fact(
            entity,
            "active",
            False,
            "SET_INACTIVE",
            rule_id,
            allow_replace=True,
        )

    def mark_fainted(self, entity: Dict[str, Any]) -> None:
        self._set_entity_fact(
            entity,
            "fainted",
            True,
            "MARK_FAINTED",
            "state.faint.mark",
            allow_replace=False,
        )
        self.set_inactive(entity, "state.faint.remove_active")
        side = entity["side"]["value"] if entity["side"]["knowledge"] == "known" else None
        if side:
            fainted = self.state[SIDE_KEYS[side]]["fainted"]
            values = list(fainted["value"] or [])
            if entity["entity_id"] not in values:
                values.append(entity["entity_id"])
            fainted["value"] = sorted(values)
            fainted["knowledge"] = "known"
            fainted["confidence"] = self.rule_confidence()
            fainted["source_event_ids"] = sorted(
                set(fainted["source_event_ids"] + [self.event_id])
            )

    def set_status(self, entity: Dict[str, Any], status: Optional[str]) -> None:
        operation = "CLEAR_STATUS" if status is None else "SET_STATUS"
        rule_id = "state.status.clear" if status is None else "state.status.set"
        self._set_entity_fact(
            entity,
            "status",
            status,
            operation,
            rule_id,
            allow_replace=status is None,
        )

    def set_volatile(
        self,
        entity: Dict[str, Any],
        effect: str,
        active: bool,
        counter: Optional[int] = None,
    ) -> None:
        statuses = entity["volatile_statuses"]
        before = deepcopy(statuses.get(effect))
        current = statuses.get(
            effect,
            {
                "effect": effect,
                "active": knowledge_value(),
                "counter": knowledge_value(),
                "last_action": knowledge_value(),
            },
        )
        current["active"] = knowledge_value(
            "known",
            active,
            self.rule_confidence(),
            [self.event_id],
            [self.timeline_id],
            self.timestamp,
        )
        if counter is not None:
            current["counter"] = knowledge_value(
                "known",
                int(counter),
                self.rule_confidence(),
                [self.event_id],
                [self.timeline_id],
                self.timestamp,
            )
        current["last_action"] = knowledge_value(
            "known",
            str(self.event["metadata"]["action"]),
            self.rule_confidence(),
            [self.event_id],
            [self.timeline_id],
            self.timestamp,
        )
        statuses[effect] = current
        self.add_operation(
            "ADD_VOLATILE" if active else "REMOVE_VOLATILE",
            entity["entity_id"],
            "volatile_statuses.{}".format(effect),
            before,
            current,
            "state.volatile.{}".format("add" if active else "remove"),
        )

    def change_stat_stage(
        self,
        entity: Dict[str, Any],
        stat: str,
        delta: int,
    ) -> None:
        stages = entity["stat_stages"]
        before = deepcopy(stages.get(stat))
        previous_observed = int((stages.get(stat) or {}).get("observed_net_change", 0))
        observed = previous_observed + int(delta)
        clamped = clamp_stat_stage(observed)
        stages[stat] = {
            "knowledge": "unknown",
            "value": None,
            "observed_net_change": observed,
            "clamped_observed_net_change": clamped,
            "confidence": self.rule_confidence(),
            "source_event_ids": sorted(
                set((stages.get(stat) or {}).get("source_event_ids", []) + [self.event_id])
            ),
            "source_timeline_ids": sorted(
                set(
                    (stages.get(stat) or {}).get("source_timeline_ids", [])
                    + [self.timeline_id]
                )
            ),
            "observed_at": self.timestamp,
        }
        self.add_operation(
            "CHANGE_STAT_STAGE",
            entity["entity_id"],
            "stat_stages.{}".format(stat),
            before,
            stages[stat],
            "state.stat_stage.change",
            evidence=["absolute_stage_unknown", "clamp=[-6,+6]"],
        )

    def set_weather(self, weather: Optional[str]) -> None:
        current = self.state["field"]["weather"]
        before = deepcopy(current)
        if (
            weather is not None
            and current["knowledge"] == "known"
            and current["value"] not in {None, weather}
        ):
            self.add_conflict(
                "weather_conflict",
                "field",
                "weather",
                current,
                weather,
                "state.weather.conflict",
            )
            current["knowledge"] = "conflicted"
            current["value"] = {"existing": before["value"], "proposed": weather}
            return
        self.state["field"]["weather"] = knowledge_value(
            "known",
            weather,
            self.rule_confidence(),
            [self.event_id],
            [self.timeline_id],
            self.timestamp,
        )
        self.add_operation(
            "SET_WEATHER" if weather is not None else "CLEAR_WEATHER",
            "field",
            "weather",
            before,
            self.state["field"]["weather"],
            "state.weather.{}".format("set" if weather is not None else "clear"),
        )

    def set_side_condition(self, side: str, effect: str, active: bool) -> None:
        conditions = self.state[SIDE_KEYS[side]]["side_conditions"]
        before = deepcopy(conditions.get(effect))
        conditions[effect] = {
            "effect": effect,
            "active": knowledge_value(
                "known",
                active,
                self.rule_confidence(),
                [self.event_id],
                [self.timeline_id],
                self.timestamp,
            ),
            "started_at": self.timestamp if active else (before or {}).get("started_at"),
            "ended_at": self.timestamp if not active else None,
        }
        self.add_operation(
            "ADD_SIDE_CONDITION" if active else "REMOVE_SIDE_CONDITION",
            side,
            "side_conditions.{}".format(effect),
            before,
            conditions[effect],
            "state.side_condition.{}".format("add" if active else "remove"),
        )

    def set_field_effect(self, effect: str, counter: Optional[int]) -> None:
        effects = self.state["field"]["field_effects"]
        before = deepcopy(effects.get(effect))
        effects[effect] = {
            "effect": effect,
            "active": knowledge_value(
                "known",
                True,
                self.rule_confidence(),
                [self.event_id],
                [self.timeline_id],
                self.timestamp,
            ),
            "counter": knowledge_value(
                "known" if counter is not None else "unknown",
                counter,
                self.rule_confidence() if counter is not None else 0.0,
                [self.event_id] if counter is not None else [],
                [self.timeline_id] if counter is not None else [],
                self.timestamp if counter is not None else None,
            ),
        }
        self.add_operation(
            "ADD_FIELD_EFFECT",
            "field",
            "field_effects.{}".format(effect),
            before,
            effects[effect],
            "state.field_effect.add",
        )

    def append_known_evidence(
        self,
        entity: Dict[str, Any],
        field: str,
        value: str,
        operation: str,
        rule_id: str,
    ) -> None:
        current = entity[field]
        before = deepcopy(current)
        values = list(current["value"] or []) if current["knowledge"] == "known" else []
        if value not in values:
            values.append(value)
        entity[field] = knowledge_value(
            "known",
            sorted(values),
            self.rule_confidence(),
            sorted(set(current.get("source_event_ids", []) + [self.event_id])),
            sorted(
                set(current.get("source_timeline_ids", []) + [self.timeline_id])
            ),
            self.timestamp,
        )
        self.add_operation(
            operation,
            entity["entity_id"],
            field,
            before,
            entity[field],
            rule_id,
        )

    def set_transformation(self, entity: Dict[str, Any], value: Dict[str, Any]) -> None:
        current = entity["transformation"]
        if (
            current["knowledge"] == "known"
            and current["value"].get("phase") == "completed"
            and value.get("phase") == "activated"
        ):
            self.add_conflict(
                "transformation_phase_conflict",
                entity["entity_id"],
                "transformation",
                current,
                value,
                "state.transformation.phase_conflict",
            )
            return
        self._set_entity_fact(
            entity,
            "transformation",
            value,
            "SET_TRANSFORMATION",
            "state.transformation.set",
            allow_replace=True,
        )

    def set_battle_fact(
        self,
        field: str,
        value: Any,
        operation: str = "SET_BATTLE_RESULT",
    ) -> None:
        current = self.state["battle"][field]
        before = deepcopy(current)
        if (
            current["knowledge"] == "known"
            and current["value"] not in {None, value}
        ):
            self.add_conflict(
                "battle_result_conflict",
                "battle",
                field,
                current,
                value,
                "state.battle_result.conflict",
            )
            return
        self.state["battle"][field] = knowledge_value(
            "known",
            value,
            self.rule_confidence(),
            [self.event_id],
            [self.timeline_id],
            self.timestamp,
        )
        self.add_operation(
            operation,
            "battle",
            field,
            before,
            self.state["battle"][field],
            "state.battle_result.set",
        )
