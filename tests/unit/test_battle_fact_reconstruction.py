from pokemon_battle_vision.battle_fact_identity import FactParticipantResolver
from pokemon_battle_vision.battle_fact_reconstruction import (
    build_battle_facts,
    build_fact_relations,
    build_reconstructed_turns,
    fact_type_for_event,
)


class StubKnowledgeBase:
    def resolve_species(self, text, limit=5):
        if text != "測試獸":
            return []
        return [
            {
                "canonical_species_id": 9999,
                "canonical_identifier": "testmon",
                "traditional_chinese_name": "測試獸",
                "english_name": "Testmon",
                "default_pokemon_id": 9999,
                "regulation_availability": {"MB": False},
                "confidence": 1.0,
                "matched_normalized_alias": "測試獸",
                "source_ids": ["fixture"],
                "resolution_rule_id": "fixture.exact.v1",
            }
        ]


def _event(identifier, event_type, action, timestamp, metadata=None):
    values = {"action": action, "rule_id": "fixture.rule"}
    values.update(metadata or {})
    return {
        "id": identifier,
        "candidate_id": "battle_text-{}".format(identifier[-4:]),
        "event_type": event_type,
        "timestamp": timestamp,
        "start_time": timestamp,
        "end_time": timestamp + 0.5,
        "confidence": 0.9,
        "raw_text": "fixture",
        "normalized_text": "fixture",
        "metadata": values,
        "source": {"acceptance": "auto_accepted"},
    }


def _source():
    events = [
        _event(
            "battle-event-0001",
            "MOVE",
            "use",
            10.0,
            {"actor": "測試獸", "move": "測試招式", "side": "player"},
        ),
        _event(
            "battle-event-0002",
            "STATUS",
            "inflict",
            12.0,
            {"target": "對手獸", "status": "灼傷", "side": "opponent"},
        ),
    ]
    return {
        "knowledge_base": StubKnowledgeBase(),
        "events": {"events": events},
        "timeline": {
            "groups": [
                {
                    "timeline_id": "timeline-0001",
                    "event_ids": [row["id"] for row in events],
                }
            ]
        },
        "relations": {
            "relation_count": 1,
            "relations": [
                {
                    "relation_id": "relation-0001",
                    "relation_type": "TEMPORALLY_ADJACENT",
                    "from_event_id": "battle-event-0001",
                    "to_event_id": "battle-event-0002",
                    "group_id": None,
                    "rule_id": "fixture.temporal",
                    "evidence": ["fixture"],
                    "confidence": 0.7,
                    "review_status": "needs_review",
                }
            ],
        },
        "relation_reviews": {
            "records": [
                {"relation_id": "relation-0001", "human_decision": "rejected"}
            ]
        },
        "hp_changes": {
            "changes": [
                {
                    "change_id": "hp-change-00001",
                    "timestamp": 11.0,
                    "confidence": 0.6,
                    "source_observation_ids": ["hp-observation-00001", "hp-observation-00002"],
                    "linked_timeline_ids": ["timeline-0001"],
                    "pokemon_entity_id": None,
                    "identity_text": "測試獸",
                    "side": "player",
                    "slot": "left",
                    "change_type": "damage",
                    "before_hp": 100,
                    "after_hp": 80,
                    "delta_hp": -20,
                    "before_percent": 100.0,
                    "after_percent": 80.0,
                    "delta_percent": -20.0,
                    "cause": "unknown",
                    "rule_id": "fixture.hp",
                }
            ]
        },
        "hp_observations": {
            "observations": [
                {"observation_id": "hp-observation-00001", "timestamp": 10.5, "confidence": 0.7},
                {"observation_id": "hp-observation-00002", "timestamp": 11.0, "confidence": 0.7},
            ]
        },
        "pokemon_entities": {"entities": []},
        "move_menu_observations": {
            "observations": [
                {"candidate_id": "move_menu-0001", "start_time": 10.0, "confidence": 0.8}
            ]
        },
        "decision_cycles": {
            "cycles": [
                {
                    "cycle_id": "decision-cycle-001",
                    "cycle_index": 1,
                    "start_time": 9.0,
                    "end_time": 10.0,
                    "confidence": 0.8,
                    "phase": "opening",
                    "battle_event_ids": [],
                    "timeline_ids": [],
                    "boundary_evidence": [{"candidate_ids": []}],
                },
                {
                    "cycle_id": "decision-cycle-002",
                    "cycle_index": 2,
                    "start_time": 10.0,
                    "end_time": 13.0,
                    "confidence": 0.95,
                    "phase": "final",
                    "battle_event_ids": [row["id"] for row in events],
                    "timeline_ids": ["timeline-0001"],
                    "boundary_evidence": [{"candidate_ids": ["move_menu-0001"]}],
                },
            ]
        },
    }


def test_event_mapping_is_explicit_and_unknown_is_preserved():
    assert fact_type_for_event(_event("battle-event-0001", "MOVE", "use", 1.0)) == "MOVE_USED"
    assert fact_type_for_event(_event("battle-event-0002", "NEW_TYPE", "new", 2.0)) == "UNRESOLVED_EVENT"


def test_reconstruction_is_additive_deterministic_and_menu_is_not_a_move():
    source = _source()
    first = build_battle_facts(source)
    second = build_battle_facts(source)
    first_rows = [row.to_dict() for row in first["facts"]]
    assert first_rows == [row.to_dict() for row in second["facts"]]
    assert [row["fact_type"] for row in first_rows] == [
        "TURN_BOUNDARY",
        "MOVE_USED",
        "HP_CHANGED",
        "STATUS_APPLIED",
    ]
    assert sum(row["fact_type"] == "MOVE_USED" for row in first_rows) == 1
    assert all(row["evidence"] for row in first_rows)


def test_identity_resolution_excludes_regulation_and_keeps_provenance():
    resolver = FactParticipantResolver(StubKnowledgeBase(), [])
    participant = resolver.resolve("測試獸", "actor", "player", 0.9).to_dict()
    assert participant["canonical_species_id"] == 9999
    assert participant["resolution_source_ids"] == ["fixture"]
    assert "regulation_availability" not in str(participant)


def test_rejected_relation_is_retained_but_inactive_and_noncausal():
    source = _source()
    reconstruction = build_battle_facts(source)
    relations = build_fact_relations(source, reconstruction["source_to_fact"])
    assert len(relations) == 1
    assert relations[0]["review_resolution"] == "rejected"
    assert relations[0]["active"] is False
    assert relations[0]["causal_claim"] is False


def test_turn_candidate_is_ambiguous_and_contains_all_observed_facts_once():
    source = _source()
    reconstruction = build_battle_facts(source)
    turns = build_reconstructed_turns(
        source, reconstruction["facts"], reconstruction["source_to_fact"]
    )
    assert turns["turn_candidate_count"] == 1
    turn = turns["turn_candidates"][0]
    assert turn["official_turn_number"] is None
    assert turn["is_official_turn_number"] is False
    assert turn["reconstruction_status"] == "ambiguous"
    assert len(turn["event_fact_ids"]) == 2
    assert len(turn["hp_change_fact_ids"]) == 1
    boundary = next(
        fact for fact in reconstruction["facts"] if fact.fact_type == "TURN_BOUNDARY"
    )
    assert boundary.attributes["move_menu_candidate_ids"] == ["move_menu-0001"]
