"""Checkpoint 1D orchestration：從已完成的 1C review 建立 BattleEvent IR。"""

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from jsonschema import Draft202012Validator

from .battle_event_models import BattleEvent, EVENT_TYPES
from .battle_event_normalization import normalize_battle_text
from .battle_event_parser import BattleEventParser
from .config import load_json
from .errors import InputError
from .output_transaction import OutputTransaction, finalize_generated_output
from .utils import project_relative, sha256_file, write_json


PARSER_VERSION = "0.2.0"
ACCEPTANCE_POLICY = (
    "human_decision 優先；accepted 納入、duplicate/rejected 排除；"
    "未經人工覆寫的 workflow_status=auto_accepted 納入"
)
READ_ONLY_INPUTS = {
    "roi_config": "configs/roi_2868x1320.json",
    "roi_approval": "outputs/checkpoint-1a/roi_approval.json",
    "checkpoint1b_events": "outputs/checkpoint-1b/events.json",
    "checkpoint1b_frames": "outputs/checkpoint-1b/frames.jsonl",
    "checkpoint1c_manifest": "outputs/checkpoint-1c/checkpoint1c_manifest.json",
    "checkpoint1c_raw": "outputs/checkpoint-1c/ocr_raw_results.jsonl",
    "checkpoint1c_aggregates": "outputs/checkpoint-1c/ocr_aggregates.json",
    "checkpoint1c_validations": "outputs/checkpoint-1c/text_validations.json",
}


def _schema_validator(project_root: Path, name: str) -> Draft202012Validator:
    schema = load_json(project_root / "schemas" / name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _read_only_hashes(project_root: Path) -> Dict[str, Dict[str, str]]:
    hashes = {}
    for name, relative in READ_ONLY_INPUTS.items():
        path = project_root / relative
        if not path.is_file():
            raise InputError("Checkpoint 1D read-only input 不存在：{}".format(path))
        hashes[name] = {"path": relative, "sha256": sha256_file(path)}
    return hashes


def acceptance_for_record(record: Mapping[str, Any]) -> Optional[str]:
    """人工結論覆蓋自動 workflow；回傳 None 表示不進入 Parser。"""
    human_decision = record.get("human_decision")
    workflow_status = record.get("workflow_status")
    if human_decision == "accepted":
        return "human_accepted"
    if human_decision in {"duplicate", "rejected"}:
        return None
    if human_decision is not None:
        raise InputError(
            "Checkpoint 1D 不支援的 human_decision：{} ({})".format(
                human_decision, record.get("event_id")
            )
        )
    if workflow_status == "auto_accepted":
        return "auto_accepted"
    if workflow_status == "needs_review":
        raise InputError(
            "Checkpoint 1C 尚有未完成人工審查：{}".format(record.get("event_id"))
        )
    if workflow_status == "rejected":
        return None
    raise InputError(
        "Checkpoint 1D 不支援的 workflow_status：{} ({})".format(
            workflow_status, record.get("event_id")
        )
    )


def select_accepted_review_records(
    records: Sequence[Mapping[str, Any]],
) -> List[Tuple[Mapping[str, Any], str]]:
    ids = [str(row.get("event_id")) for row in records]
    if len(ids) != len(set(ids)):
        raise InputError("Checkpoint 1C review event_id 不可重複")
    accepted = []
    accepted_ids = set()
    duplicates = []
    for record in records:
        acceptance = acceptance_for_record(record)
        if acceptance is not None:
            accepted.append((record, acceptance))
            accepted_ids.add(str(record["event_id"]))
        if record.get("human_decision") == "duplicate":
            duplicates.append(record)
    for duplicate in duplicates:
        target = duplicate.get("merge_with_event_id")
        if not target or str(target) not in accepted_ids:
            raise InputError(
                "Duplicate 未指向有效 Accepted candidate：{} -> {}".format(
                    duplicate.get("event_id"), target
                )
            )
    accepted.sort(key=lambda pair: (float(pair[0]["start_time"]), str(pair[0]["event_id"])))
    return accepted


def _input_text(record: Mapping[str, Any]) -> Tuple[str, str]:
    human_text = record.get("human_text")
    if isinstance(human_text, str) and human_text.strip():
        return human_text, "human_text"
    ocr_text = str(record.get("ocr_text") or "")
    if not ocr_text.strip():
        raise InputError("Accepted candidate 沒有可解析文字：{}".format(record.get("event_id")))
    return ocr_text, "ocr_text"


def _event_confidence(
    record: Mapping[str, Any], acceptance: str, text_origin: str, rule_confidence: float
) -> float:
    text_confidence = 1.0 if text_origin == "human_text" else float(record["ocr_confidence"])
    acceptance_confidence = (
        1.0 if acceptance == "human_accepted" else float(record["validation_confidence"])
    )
    return round(min(text_confidence, acceptance_confidence, rule_confidence), 6)


def _build_events(
    selected: Sequence[Tuple[Mapping[str, Any], str]], parser: BattleEventParser
) -> List[BattleEvent]:
    events = []
    for index, (record, acceptance) in enumerate(selected, start=1):
        raw_text, text_origin = _input_text(record)
        parsed = parser.parse(raw_text, str(record["event_type"]))
        start_time = float(record["start_time"])
        end_time = float(record["end_time"])
        if end_time < start_time:
            raise InputError("Candidate 時間順序錯誤：{}".format(record["event_id"]))
        events.append(
            BattleEvent(
                id="battle-event-{:04d}".format(index),
                # 1D 不推算回合，timestamp 明確採 candidate 起點。
                timestamp=start_time,
                start_time=start_time,
                end_time=end_time,
                candidate_id=str(record["event_id"]),
                event_type=parsed.event_type,
                raw_text=raw_text,
                normalized_text=normalize_battle_text(raw_text),
                confidence=_event_confidence(
                    record, acceptance, text_origin, parsed.rule_confidence
                ),
                source={
                    "checkpoint": "1C",
                    "input_event_type": str(record["event_type"]),
                    "text_origin": text_origin,
                    "acceptance": acceptance,
                    "ocr_confidence": float(record["ocr_confidence"]),
                    "validation_confidence": float(record["validation_confidence"]),
                    "reviewed_by": record.get("reviewed_by"),
                    "reviewed_at": record.get("reviewed_at"),
                },
                metadata=parsed.metadata,
            )
        )
    return events


def run_checkpoint_1d(
    project_root: Path,
    review_path: Path,
    output_dir: Path,
    parser: Optional[BattleEventParser] = None,
) -> Dict[str, Any]:
    project_root = project_root.resolve()
    review_path = review_path.resolve()
    output_dir = output_dir.resolve()
    if not review_path.is_file():
        raise InputError("Checkpoint 1D review input 不存在：{}".format(review_path))
    read_only_before = _read_only_hashes(project_root)
    review_sha256_before = sha256_file(review_path)
    review_payload = load_json(review_path)
    _schema_validator(project_root, "checkpoint1c_review.schema.json").validate(
        review_payload
    )
    records = list(review_payload.get("records", []))
    if int(review_payload.get("record_count", -1)) != len(records):
        raise InputError("Checkpoint 1C review record_count 不一致")
    selected = select_accepted_review_records(records)
    event_models = _build_events(selected, parser or BattleEventParser())
    event_rows = [event.to_dict() for event in event_models]
    event_counts = dict(Counter(event.event_type for event in event_models))
    for event_type in EVENT_TYPES:
        event_counts.setdefault(event_type, 0)
    event_counts = {event_type: event_counts[event_type] for event_type in EVENT_TYPES}
    result = {
        "schema_version": "0.2.0",
        "checkpoint": "1D",
        "kind": "battle_event_results",
        "parser_version": PARSER_VERSION,
        "source": {
            "checkpoint": "1C",
            "review_path": project_relative(review_path, project_root),
            "review_sha256": review_sha256_before,
            "review_record_count": len(records),
            "accepted_input_count": len(selected),
            "acceptance_policy": ACCEPTANCE_POLICY,
        },
        "event_count": len(event_rows),
        "event_counts": event_counts,
        "unknown_count": event_counts["UNKNOWN_EVENT"],
        "events": event_rows,
    }
    result_validator = _schema_validator(project_root, "battle_event.schema.json")
    result_validator.validate(result)
    event_ids = [event.id for event in event_models]
    candidate_ids = [event.candidate_id for event in event_models]
    timestamps = [event.timestamp for event in event_models]
    validation = {
        "schema_valid": True,
        "event_ids_unique": len(event_ids) == len(set(event_ids)),
        "candidate_ids_unique": len(candidate_ids) == len(set(candidate_ids)),
        "timestamps_monotonic": timestamps == sorted(timestamps),
        "all_events_traceable": set(candidate_ids).issubset(
            {str(row["event_id"]) for row in records}
        ),
        "review_input_unchanged": sha256_file(review_path) == review_sha256_before,
    }
    if not all(validation.values()):
        raise InputError("Checkpoint 1D event validation 失敗：{}".format(validation))
    with OutputTransaction(project_root, output_dir) as transaction:
        result_path = transaction.staging_dir / "battle_events.json"
        write_json(result_path, result)
        manifest = {
            "schema_version": "0.2.0",
            "checkpoint": "1D",
            "kind": "checkpoint1d_manifest",
            "status": "complete",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "parser_version": PARSER_VERSION,
            "input": {
                "path": project_relative(review_path, project_root),
                "sha256": review_sha256_before,
                "record_count": len(records),
                "accepted_input_count": len(selected),
            },
            "output": {
                "path": "battle_events.json",
                "sha256": sha256_file(result_path),
            },
            "event_count": len(event_rows),
            "event_counts": event_counts,
            "unknown_count": event_counts["UNKNOWN_EVENT"],
            "validation": validation,
            "scope_guards": {
                "ocr_rerun": False,
                "detector_rerun": False,
                "roi_modified": False,
                "checkpoint1b_modified": False,
                "checkpoint1c_modified": False,
                "battle_state_performed": False,
                "replay_analysis_performed": False,
                "gui_created": False,
            },
        }
        read_only_after = _read_only_hashes(project_root)
        if read_only_after != read_only_before or sha256_file(review_path) != review_sha256_before:
            raise InputError("Checkpoint 1D 執行期間 read-only 1A／1B／1C input 發生變更")
        _schema_validator(project_root, "checkpoint1d_manifest.schema.json").validate(
            manifest
        )
        write_json(transaction.staging_dir / "checkpoint1d_manifest.json", manifest)
        transaction.commit()
    finalize_generated_output(output_dir)
    return manifest
