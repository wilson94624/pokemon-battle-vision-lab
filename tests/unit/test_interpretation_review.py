import csv
import json
from dataclasses import replace
from pathlib import Path

from pokemon_battle_vision.interpretation_review import (
    apply_review_decisions,
    build_review_records,
    validate_review_records,
    write_review_pack,
)
from pokemon_battle_vision.rule_coverage import build_expanded_rule_interpretations
from pokemon_battle_vision.rule_knowledge import PokemonRuleKnowledgeBase


PROJECT = Path(__file__).resolve().parents[2]


def _combined():
    existing = json.loads(
        (PROJECT / "outputs/checkpoint-1i/rule_interpretations.json").read_text()
    )
    facts = json.loads(
        (PROJECT / "outputs/checkpoint-1h/battle_facts.json").read_text()
    )["facts"]
    relations = json.loads(
        (PROJECT / "outputs/checkpoint-1h/battle_fact_relations.json").read_text()
    )["relations"]
    kb = PokemonRuleKnowledgeBase.from_version(PROJECT, "v2")
    expanded = build_expanded_rule_interpretations(facts, relations, kb)
    combined = [
        {
            "origin_checkpoint": "1I",
            "interpretation_version": existing["interpretation_version"],
            "knowledge_version": existing["knowledge_version"],
            "payload": row,
        }
        for row in existing["interpretations"]
    ] + [
        {
            "origin_checkpoint": "1J",
            "interpretation_version": "0.1.0",
            "knowledge_version": kb.payload["knowledge_version"],
            "payload": row.to_dict(),
        }
        for row in expanded
    ]
    return combined, facts, relations


def test_review_records_cover_existing_eight_and_expanded_ten():
    combined, _, _ = _combined()
    records = build_review_records(combined)
    assert len(records) == 18
    assert sum(record.interpretation_origin == "1I" for record in records) == 8
    assert sum(record.interpretation_origin == "1J" for record in records) == 10
    assert all(record.review_status == "needs_review" for record in records)
    assert all(record.reviewer is None and record.reviewed_at is None for record in records)
    assert all(validate_review_records(records, combined).values())


def test_payload_hash_tampering_is_detected_before_acceptance():
    combined, _, _ = _combined()
    records = build_review_records(combined)
    tampered = [replace(records[0], interpretation_payload_hash="0" * 64), *records[1:]]
    validation = validate_review_records(tampered, combined)
    assert validation["interpretation_payload_hashes_match"] is False


def test_unresolved_acceptance_requires_explicit_issue_code():
    combined, _, _ = _combined()
    records = build_review_records(combined)
    unresolved = next(record for record in records if record.certainty == "unresolved")
    invalid = [
        replace(
            record,
            review_status="accepted",
            reviewer="wilson",
            reviewed_at="2026-07-18T00:00:00+08:00",
            review_reason="接受 unresolved 結論",
        )
        if record.review_record_id == unresolved.review_record_id
        else record
        for record in records
    ]
    assert validate_review_records(invalid, combined)["review_decisions_valid"] is False
    valid = [
        replace(record, issue_codes=("unresolved_outcome_correct",))
        if record.review_record_id == unresolved.review_record_id
        else record
        for record in invalid
    ]
    assert all(validate_review_records(valid, combined).values())


def test_conflicted_record_preserves_both_sides_and_exact_fields():
    combined, _, _ = _combined()
    source = dict(combined[0])
    payload = dict(source["payload"])
    payload["interpretation_id"] = "rule-interpretation-9999"
    payload["certainty"] = "conflicted"
    payload["conclusion"] = {
        "code": "OBSERVED_OUTCOME_CONFLICTS_WITH_TYPE_CHART",
        "summary": "保留衝突",
        "derived_values": {
            "observed_result": "not_very_effective",
            "expected_result": "super_effective",
        },
    }
    source["payload"] = payload
    record = build_review_records([source])[0]
    assert record.certainty == "conflicted"
    context = record.conflict_context
    assert context["conflicting_fields"] == [
        {
            "field": "effectiveness_result",
            "observed_value": "not_very_effective",
            "knowledge_expected_value": "super_effective",
        }
    ]
    assert context["observed_conclusion"]["derived_values"]["observed_result"] == "not_very_effective"
    assert context["knowledge_derived_expectation"]["effectiveness_result"] == "super_effective"


def test_csv_workflow_updates_review_without_editing_json(tmp_path):
    combined, facts, relations = _combined()
    records = build_review_records(combined)
    written = write_review_pack(tmp_path, records, combined, facts, relations)
    worksheet = next(path for path in written if path.name == "review_worksheet.csv")
    with worksheet.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows[0]["review_status"] = "deferred"
    rows[0]["reviewer"] = "wilson"
    rows[0]["reviewed_at"] = "2026-07-18T00:00:00+08:00"
    rows[0]["review_reason"] = "等待更多 evidence"
    with worksheet.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    updated = apply_review_decisions(records, worksheet)
    assert updated[0].review_status == "deferred"
    assert updated[0].review_reason == "等待更多 evidence"
    assert all(validate_review_records(updated, combined).values())
