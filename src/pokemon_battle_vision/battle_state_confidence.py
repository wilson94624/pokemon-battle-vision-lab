"""Checkpoint 1F confidence、completeness 與 unknown fields 計算。"""

from typing import Any, Dict, Iterable, List

from .battle_state_policy import COMPLETENESS_WEIGHTS


def _knowledge_values(value: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(value, dict):
        if "knowledge" in value and "confidence" in value:
            yield value
        for child in value.values():
            yield from _knowledge_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _knowledge_values(child)


def state_confidence(state: Dict[str, Any]) -> float:
    confidences = [
        float(item["confidence"])
        for item in _knowledge_values(state)
        if item["knowledge"] == "known" and float(item["confidence"]) > 0.0
    ]
    if not confidences:
        return 0.0
    return round(sum(confidences) / len(confidences), 6)


def _known(value: Dict[str, Any]) -> float:
    return 1.0 if value.get("knowledge") == "known" else 0.0


def _side_condition_completeness(side: Dict[str, Any]) -> float:
    if not side["side_conditions"]:
        return 0.0
    return 1.0 if all(
        item.get("active", {}).get("knowledge") == "known"
        for item in side["side_conditions"].values()
    ) else 0.5


def _pokemon_core_completeness(state: Dict[str, Any]) -> float:
    pokemon = []
    pokemon.extend(state["player_side"]["known_pokemon"].values())
    pokemon.extend(state["opponent_side"]["known_pokemon"].values())
    pokemon.extend(state["battle"]["unassigned_pokemon"].values())
    if not pokemon:
        return 0.0
    per_entity = []
    for entity in pokemon:
        fields = ("side", "active", "fainted", "status")
        per_entity.append(sum(_known(entity[name]) for name in fields) / len(fields))
    return sum(per_entity) / len(per_entity)


def state_completeness(state: Dict[str, Any]) -> float:
    values = {
        "battle_result": _known(state["battle"]["result"]),
        "player_active": _known(state["player_side"]["active"]),
        "opponent_active": _known(state["opponent_side"]["active"]),
        "player_roster": _known(state["player_side"]["complete_roster"]),
        "opponent_roster": _known(state["opponent_side"]["complete_roster"]),
        "player_side_conditions": _side_condition_completeness(state["player_side"]),
        "opponent_side_conditions": _side_condition_completeness(
            state["opponent_side"]
        ),
        "weather": _known(state["field"]["weather"]),
        "known_pokemon_core": _pokemon_core_completeness(state),
    }
    result = sum(COMPLETENESS_WEIGHTS[key] * values[key] for key in values)
    return round(result, 6)


def unknown_field_paths(state: Dict[str, Any]) -> List[str]:
    paths: List[str] = []

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            if value.get("knowledge") in {"unknown", "conflicted"}:
                paths.append(path)
                return
            for key, child in value.items():
                walk(child, "{}.{}".format(path, key) if path else key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, "{}[{}]".format(path, index))

    walk(state, "")
    return sorted(set(paths))
