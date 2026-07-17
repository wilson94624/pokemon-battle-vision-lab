"""Checkpoint 1J review records、CSV worksheet 與 Markdown review pack。"""

import csv
import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .errors import InputError
from .interpretation_review_models import (
    CONFLICT_CATEGORIES,
    ISSUE_CODES,
    REVIEW_SCHEMA_VERSION,
    REVIEW_STATUSES,
    InterpretationReviewRecord,
)


def canonical_payload_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _conflict_context(interpretation: Mapping[str, Any]) -> Mapping[str, Any]:
    derived = interpretation["conclusion"]["derived_values"]
    conflicting_fields = []
    for label, observed_key, expected_key in (
        ("effectiveness_result", "observed_result", "expected_result"),
        ("outcome", "observed_outcome", "expected_outcome"),
        ("value", "observed_value", "expected_value"),
    ):
        if observed_key in derived and expected_key in derived:
            conflicting_fields.append(
                {
                    "field": label,
                    "observed_value": derived[observed_key],
                    "knowledge_expected_value": derived[expected_key],
                }
            )
    if not conflicting_fields:
        raise InputError(
            "Conflicted interpretation 缺少可保存的 observed／expected fields：{}".format(
                interpretation["interpretation_id"]
            )
        )
    return {
        "observed_conclusion": {
            "code": interpretation["conclusion"]["code"],
            "derived_values": dict(derived),
        },
        "knowledge_derived_expectation": {
            row["field"]: row["knowledge_expected_value"]
            for row in conflicting_fields
        },
        "conflicting_fields": conflicting_fields,
        "evidence_references": {
            "battle_fact_ids": list(
                interpretation["referenced_battle_fact_ids"]
            ),
            "fact_relation_ids": list(
                interpretation["referenced_fact_relation_ids"]
            ),
            "knowledge_ids": [
                row["knowledge_id"]
                for row in interpretation["knowledge_evidence"]
            ],
        },
    }


def build_review_records(
    interpretations: Sequence[Mapping[str, Any]],
) -> List[InterpretationReviewRecord]:
    ordered = sorted(
        interpretations,
        key=lambda row: (
            row["origin_checkpoint"],
            float(row["payload"]["timestamp"]),
            row["payload"]["interpretation_id"],
        ),
    )
    records = []
    for index, item in enumerate(ordered, start=1):
        row = item["payload"]
        record_id = "interpretation-review-{:04d}".format(index)
        records.append(
            InterpretationReviewRecord(
                review_record_id=record_id,
                interpretation_id=str(row["interpretation_id"]),
                interpretation_origin=str(item["origin_checkpoint"]),
                interpretation_payload_hash=canonical_payload_hash(row),
                certainty=str(row["certainty"]),
                review_status="needs_review",
                reviewer=None,
                reviewed_at=None,
                review_reason=None,
                issue_codes=(),
                conflict_category=None,
                interpretation_version=str(item["interpretation_version"]),
                knowledge_version=str(item["knowledge_version"]),
                review_schema_version=REVIEW_SCHEMA_VERSION,
                review_card_path="review_pack/cards/{}.md".format(record_id),
                conflict_context=(
                    _conflict_context(row)
                    if row["certainty"] == "conflicted"
                    else None
                ),
            )
        )
    return records


def _empty_to_none(value: Optional[str]) -> Optional[str]:
    stripped = (value or "").strip()
    return stripped or None


def apply_review_decisions(
    records: Sequence[InterpretationReviewRecord],
    decisions_path: Optional[Path],
) -> List[InterpretationReviewRecord]:
    if decisions_path is None:
        return list(records)
    if not decisions_path.is_file():
        raise InputError("Checkpoint 1J review decisions CSV 不存在：{}".format(decisions_path))
    with decisions_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    by_id = {row.review_record_id: row for row in records}
    seen = set()
    updated = []
    decisions = {}
    for row in rows:
        record_id = str(row.get("review_record_id") or "")
        if record_id in seen:
            raise InputError("Review decisions CSV 的 review_record_id 重複：{}".format(record_id))
        seen.add(record_id)
        if record_id not in by_id:
            raise InputError("Review decisions CSV 含未知 review_record_id：{}".format(record_id))
        source = by_id[record_id]
        for key, expected in (
            ("interpretation_id", source.interpretation_id),
            ("interpretation_payload_hash", source.interpretation_payload_hash),
            ("certainty", source.certainty),
        ):
            if str(row.get(key) or "") != expected:
                raise InputError("Review decisions CSV 不可修改 immutable 欄位：{}".format(key))
        status = str(row.get("review_status") or "")
        if status not in REVIEW_STATUSES:
            raise InputError("不支援的 review_status：{}".format(status))
        issue_codes = tuple(
            value.strip()
            for value in str(row.get("issue_codes") or "").split(";")
            if value.strip()
        )
        decisions[record_id] = replace(
            source,
            review_status=status,
            reviewer=_empty_to_none(row.get("reviewer")),
            reviewed_at=_empty_to_none(row.get("reviewed_at")),
            review_reason=_empty_to_none(row.get("review_reason")),
            issue_codes=issue_codes,
            conflict_category=_empty_to_none(row.get("conflict_category")),
        )
    if seen != set(by_id):
        raise InputError("Review decisions CSV 必須保留所有 review records")
    for record in records:
        updated.append(decisions[record.review_record_id])
    return updated


def validate_review_records(
    records: Sequence[InterpretationReviewRecord],
    interpretations: Sequence[Mapping[str, Any]],
) -> Dict[str, bool]:
    sources = {
        str(item["payload"]["interpretation_id"]): item for item in interpretations
    }
    ids = [record.review_record_id for record in records]
    interpretation_ids = [record.interpretation_id for record in records]
    payload_hashes_match = True
    immutable_fields_match = True
    decisions_valid = True
    conflict_policy_valid = True
    for record in records:
        item = sources.get(record.interpretation_id)
        if item is None:
            payload_hashes_match = False
            immutable_fields_match = False
            continue
        payload = item["payload"]
        payload_hashes_match &= (
            record.interpretation_payload_hash == canonical_payload_hash(payload)
        )
        immutable_fields_match &= (
            record.certainty == payload["certainty"]
            and record.interpretation_version == item["interpretation_version"]
            and record.knowledge_version == item["knowledge_version"]
        )
        decisions_valid &= (
            record.review_status in REVIEW_STATUSES
            and set(record.issue_codes) <= set(ISSUE_CODES)
        )
        if record.review_status == "needs_review":
            decisions_valid &= (
                record.reviewer is None
                and record.reviewed_at is None
                and record.review_reason is None
            )
        else:
            decisions_valid &= bool(
                record.reviewer and record.reviewed_at and record.review_reason
            )
        if record.review_status == "accepted" and record.certainty == "unresolved":
            decisions_valid &= "unresolved_outcome_correct" in record.issue_codes
        if record.certainty == "conflicted":
            conflict_policy_valid &= record.conflict_context is not None
            if record.review_status != "needs_review":
                conflict_policy_valid &= record.conflict_category in CONFLICT_CATEGORIES
        else:
            conflict_policy_valid &= (
                record.conflict_context is None and record.conflict_category is None
            )
    return {
        "review_record_ids_unique": len(ids) == len(set(ids)),
        "interpretation_ids_unique": len(interpretation_ids)
        == len(set(interpretation_ids)),
        "review_count_matches_interpretations": len(records)
        == len(interpretations),
        "every_review_references_existing_interpretation": set(interpretation_ids)
        == set(sources),
        "interpretation_payload_hashes_match": bool(payload_hashes_match),
        "immutable_interpretation_fields_match": bool(immutable_fields_match),
        "review_decisions_valid": bool(decisions_valid),
        "conflict_review_policy_valid": bool(conflict_policy_valid),
        "review_does_not_contain_editable_conclusion": all(
            "conclusion" not in record.to_dict() for record in records
        ),
    }


def review_summary(
    records: Sequence[InterpretationReviewRecord],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    def counts(values: Sequence[str], keys: Sequence[str]) -> Dict[str, int]:
        return {key: sum(value == key for value in values) for key in keys}

    statuses = [record.review_status for record in records]
    certainties = [record.certainty for record in records]
    origins = sorted({record.interpretation_origin for record in records})
    summary = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "checkpoint": "1J",
        "kind": "interpretation_review_summary",
        "status": (
            "complete" if "needs_review" not in statuses else "pending_human_review"
        ),
        "review_record_count": len(records),
        "review_status_counts": counts(statuses, REVIEW_STATUSES),
        "certainty_counts": counts(
            certainties, ("supported", "unresolved", "conflicted")
        ),
        "origin_counts": {
            origin: sum(record.interpretation_origin == origin for record in records)
            for origin in origins
        },
        "remaining_needs_review_ids": [
            record.review_record_id
            for record in records
            if record.review_status == "needs_review"
        ],
        "accepted_ids": [
            record.review_record_id
            for record in records
            if record.review_status == "accepted"
        ],
        "rejected_ids": [
            record.review_record_id
            for record in records
            if record.review_status == "rejected"
        ],
        "deferred_ids": [
            record.review_record_id
            for record in records
            if record.review_status == "deferred"
        ],
    }
    statistics = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "checkpoint": "1J",
        "kind": "interpretation_review_statistics",
        "total_review_record_count": len(records),
        "accepted_count": statuses.count("accepted"),
        "rejected_count": statuses.count("rejected"),
        "needs_review_count": statuses.count("needs_review"),
        "deferred_count": statuses.count("deferred"),
        "supported_interpretation_count": certainties.count("supported"),
        "unresolved_interpretation_count": certainties.count("unresolved"),
        "conflicted_interpretation_count": certainties.count("conflicted"),
        "human_review_complete": "needs_review" not in statuses,
    }
    return summary, statistics


def _json_block(value: Any) -> str:
    return "```json\n{}\n```".format(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    )


def _card_markdown(
    record: InterpretationReviewRecord,
    interpretation: Mapping[str, Any],
    facts_by_id: Mapping[str, Mapping[str, Any]],
    relations_by_id: Mapping[str, Mapping[str, Any]],
) -> str:
    lines = [
        "# {} — {}".format(record.review_record_id, record.interpretation_id),
        "",
        "- Origin：`{}`".format(record.interpretation_origin),
        "- Payload SHA-256：`{}`".format(record.interpretation_payload_hash),
        "- Type：`{}`".format(interpretation["interpretation_type"]),
        "- Rule：`{}` @ `{}`".format(
            interpretation["rule_id"], interpretation["rule_version"]
        ),
        "- Certainty：`{}`（Human review 不得改寫此欄）".format(
            interpretation["certainty"]
        ),
        "- Review status：`{}`".format(record.review_status),
        "",
        "## Referenced Battle Facts",
        "",
    ]
    for fact_id in interpretation["referenced_battle_fact_ids"]:
        fact = facts_by_id[fact_id]
        lines.extend(
            [
                "### `{}` — `{}` @ {:.6f}s".format(
                    fact_id, fact["fact_type"], float(fact["timestamp"])
                ),
                "",
                _json_block(
                    {
                        "raw_text": (fact.get("attributes") or {}).get("raw_text"),
                        "parsed_metadata": (fact.get("attributes") or {}).get(
                            "parsed_metadata"
                        ),
                        "participants": fact.get("participants", []),
                        "evidence": fact.get("evidence", []),
                    }
                ),
                "",
            ]
        )
    lines.extend(["## Referenced Fact Relations", ""])
    if interpretation["referenced_fact_relation_ids"]:
        for relation_id in interpretation["referenced_fact_relation_ids"]:
            lines.extend(
                ["### `{}`".format(relation_id), "", _json_block(relations_by_id[relation_id]), ""]
            )
    else:
        lines.extend(["無（此 interpretation 只依明確單一觀察）。", ""])
    lines.extend(
        [
            "## Required Observations",
            "",
            _json_block(interpretation["required_observations"]),
            "",
            "## Knowledge Evidence",
            "",
            _json_block(interpretation["knowledge_evidence"]),
            "",
            "## Derived Conclusion（唯讀）",
            "",
            _json_block(interpretation["conclusion"]),
            "",
            "- Unresolved reason：`{}`".format(
                interpretation["unresolved_reason"]
            ),
            "",
        ]
    )
    if record.conflict_context:
        lines.extend(
            ["## Conflict Context（必須完整保留）", "", _json_block(record.conflict_context), ""]
        )
    lines.extend(
        [
            "## Human Review",
            "",
            "請在 `../review_worksheet.csv` 填寫 `review_status`、`reviewer`、`reviewed_at`、`review_reason`、`issue_codes`；不要修改本卡或 interpretation JSON。",
            "",
        ]
    )
    return "\n".join(lines)


def write_review_pack(
    output_dir: Path,
    records: Sequence[InterpretationReviewRecord],
    interpretations: Sequence[Mapping[str, Any]],
    facts: Sequence[Mapping[str, Any]],
    relations: Sequence[Mapping[str, Any]],
) -> List[Path]:
    pack = output_dir / "review_pack"
    cards = pack / "cards"
    cards.mkdir(parents=True, exist_ok=True)
    interpretation_by_id = {
        str(item["payload"]["interpretation_id"]): item["payload"]
        for item in interpretations
    }
    facts_by_id = {str(row["fact_id"]): row for row in facts}
    relations_by_id = {str(row["fact_relation_id"]): row for row in relations}
    written = []
    for record in records:
        path = output_dir / record.review_card_path
        path.write_text(
            _card_markdown(
                record,
                interpretation_by_id[record.interpretation_id],
                facts_by_id,
                relations_by_id,
            ),
            encoding="utf-8",
        )
        written.append(path)

    worksheet = pack / "review_worksheet.csv"
    fields = (
        "review_record_id",
        "interpretation_id",
        "interpretation_payload_hash",
        "certainty",
        "review_status",
        "reviewer",
        "reviewed_at",
        "review_reason",
        "issue_codes",
        "conflict_category",
    )
    temporary = worksheet.with_name(worksheet.name + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            row = record.to_dict()
            row["issue_codes"] = ";".join(record.issue_codes)
            writer.writerow({key: row.get(key) for key in fields})
    os.replace(str(temporary), str(worksheet))
    written.append(worksheet)

    index = pack / "review_index.md"
    lines = [
        "# Checkpoint 1J Interpretation Review Pack",
        "",
        "所有 interpretation payload 都是唯讀；Human Review 只能寫入 worksheet 的 review 欄位。",
        "",
        "| Review ID | Interpretation | Origin | Certainty | Status | Card |",
        "|---|---|---|---|---|---|",
    ]
    for record in records:
        lines.append(
            "| `{}` | `{}` | `{}` | `{}` | `{}` | [{}](cards/{}) |".format(
                record.review_record_id,
                record.interpretation_id,
                record.interpretation_origin,
                record.certainty,
                record.review_status,
                record.review_record_id,
                Path(record.review_card_path).name,
            )
        )
    lines.extend(
        [
            "",
            "## 允許值",
            "",
            "- `review_status`：`accepted`、`rejected`、`needs_review`、`deferred`",
            "- `issue_codes`：以分號分隔；可用值：{}".format(
                "、".join("`{}`".format(value) for value in ISSUE_CODES)
            ),
            "- `conflict_category`：只有 conflicted interpretation 完成 review 時必填；可用值：{}".format(
                "、".join("`{}`".format(value) for value in CONFLICT_CATEGORIES)
            ),
            "",
        ]
    )
    index.write_text("\n".join(lines), encoding="utf-8")
    written.append(index)
    return written
