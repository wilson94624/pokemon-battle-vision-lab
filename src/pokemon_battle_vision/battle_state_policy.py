"""Checkpoint 1F 集中式 projection、confidence 與 completeness 政策。"""

from typing import Dict, Set


STAT_STAGE_MIN = -6
STAT_STAGE_MAX = 6
MAX_OBSERVED_ACTIVE_PER_SIDE = 2
LOW_COMPLETENESS_THRESHOLD = 0.45

SUPPORTED_EVENT_TYPES: Set[str] = {
    "SWITCH",
    "FAINT",
    "STATUS",
    "VOLATILE_STATUS",
    "STAT_CHANGE",
    "WEATHER",
    "SIDE_CONDITION",
    "FIELD_EFFECT",
    "TRANSFORMATION",
    "ABILITY",
    "ITEM",
    "BATTLE_RESULT",
}

NO_OP_EVENT_TYPES: Set[str] = {"MOVE", "MOVE_RESULT", "DAMAGE_RESULT"}

IMPORTANT_OPERATIONS: Set[str] = {
    "SET_ACTIVE",
    "MARK_FAINTED",
    "SET_STATUS",
    "CLEAR_STATUS",
    "ADD_VOLATILE",
    "REMOVE_VOLATILE",
    "CHANGE_STAT_STAGE",
    "SET_WEATHER",
    "CLEAR_WEATHER",
    "ADD_SIDE_CONDITION",
    "REMOVE_SIDE_CONDITION",
    "SET_TRANSFORMATION",
    "SET_BATTLE_RESULT",
}

COMPLETENESS_WEIGHTS: Dict[str, float] = {
    "battle_result": 0.10,
    "player_active": 0.15,
    "opponent_active": 0.15,
    "player_roster": 0.10,
    "opponent_roster": 0.10,
    "player_side_conditions": 0.08,
    "opponent_side_conditions": 0.08,
    "weather": 0.08,
    "known_pokemon_core": 0.16,
}

RULE_CONFIDENCE_FACTORS: Dict[str, float] = {
    "explicit_metadata": 1.0,
    "unique_entity_resolution": 0.92,
    "unknown_side_entity": 0.86,
    "unresolved": 0.65,
}

STAT_ALIASES = {
    "攻擊": "attack",
    "防禦": "defense",
    "特攻": "special_attack",
    "特防": "special_defense",
    "速度": "speed",
    "命中": "accuracy",
    "閃避": "evasion",
}


def clamp_stat_stage(value: int) -> int:
    return max(STAT_STAGE_MIN, min(STAT_STAGE_MAX, int(value)))
