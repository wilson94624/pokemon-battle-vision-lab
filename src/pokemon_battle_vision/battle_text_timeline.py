"""BATTLE_TEXT strong／weak evidence state machine、邊界與 diagnostics。"""

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from .battle_text_layout import layout_fingerprint_distance
from .checkpoint1b_models import EventCandidate, FrameScanRecord


@dataclass(frozen=True)
class BattleTextTemporalConfig:
    max_negative_gap_samples: int = 1
    max_weak_continuation_samples: int = 10
    max_weak_boundary_samples: int = 1
    max_structural_weak_boundary_samples: int = 10
    weak_boundary_structure_floor: float = 0.30
    weak_open_min_samples: int = 3
    layout_change_threshold: float = 0.38
    fade_layout_change_threshold: float = 0.20
    negative_gap_layout_change_threshold: float = 0.07
    negative_gap_with_weak_layout_change_threshold: float = 0.02
    min_same_layout_reopen_gap_samples: int = 5
    max_same_layout_reopen_gap_samples: int = 8
    same_layout_reopen_threshold: float = 0.01
    same_layout_comparison_samples: int = 6
    layout_change_persistence_samples: int = 3
    layout_grace_strong_samples: int = 5
    long_candidate_sec: float = 5.0

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload.update(
            {
                "minimum_positive_samples": 1,
                "duration_filter": None,
                "cooldown": None,
                "suppression": None,
            }
        )
        return payload


DEFAULT_BATTLE_TEXT_TEMPORAL_CONFIG = BattleTextTemporalConfig()


@dataclass
class _Segment:
    start_index: int
    end_index: int
    open_reason: str
    close_reason: str
    close_trigger_index: int
    split_from_previous: bool = False
    same_layout_reopen_gaps: Tuple[Tuple[int, int], ...] = ()
    event_id: str = ""


def _format_timestamp(seconds: float) -> str:
    milliseconds = max(0, int(round(float(seconds) * 1000.0)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return "{:02d}:{:02d}:{:02d}.{:03d}".format(hours, minutes, secs, millis)


def _evidence_level(record: FrameScanRecord, threshold: float) -> str:
    evidence = record.battle_text_evidence
    level = str(evidence.get("evidence_level", ""))
    if level in {"strong", "weak", "negative"}:
        return level
    score = float(record.candidate_scores["BATTLE_TEXT"])
    visual = float(evidence.get("visual_structure_strength") or 0.0)
    if score >= threshold and visual >= threshold:
        return "strong"
    if score >= threshold:
        return "weak"
    return "negative"


def _fingerprint(record: FrameScanRecord) -> Dict[str, Any]:
    evidence = record.battle_text_evidence
    value = evidence.get("layout_fingerprint")
    if isinstance(value, Mapping):
        return dict(value)
    layout_hash = str(evidence.get("layout_hash", ""))
    return {
        "layout_hash": layout_hash,
        "row_profile": [],
        "column_profile": [],
        "bbox": [],
        "component_count": int(evidence.get("component_count") or 0),
    }


def _segment_records(
    records: Sequence[FrameScanRecord],
    threshold: float,
    config: BattleTextTemporalConfig,
) -> List[_Segment]:
    segments: List[_Segment] = []
    active_start: Optional[int] = None
    active_open_reason = ""
    split_from_previous = False
    last_strong = -1
    last_weak = -1
    last_structural_weak = -1
    last_supported = -1
    weak_run = 0
    negative_run = 0
    pending_weak: List[int] = []
    reference: Dict[str, Any] = {}
    reference_strength = -1.0
    layout_change_run = 0
    layout_change_start = -1
    layout_previous_strong = -1
    strong_sample_count = 0
    transition_evidence_start = -1
    transition_negative_seen = False
    transition_weak_seen = False
    weak_open_blocked = False

    def open_segment(index: int, start: int, reason: str, split: bool = False) -> None:
        nonlocal active_start, active_open_reason, split_from_previous
        nonlocal last_strong, last_weak, last_structural_weak
        nonlocal last_supported, weak_run, negative_run
        nonlocal pending_weak, reference, reference_strength
        nonlocal layout_change_run, layout_change_start, layout_previous_strong
        nonlocal strong_sample_count, transition_evidence_start, transition_negative_seen
        nonlocal transition_weak_seen
        active_start = start
        active_open_reason = reason
        split_from_previous = split
        level = _evidence_level(records[index], threshold)
        last_strong = index if level == "strong" else -1
        last_weak = index if level == "weak" else -1
        last_structural_weak = (
            index
            if level == "weak"
            and float(records[index].battle_text_evidence.get("text_line_strength") or 0.0)
            >= config.weak_boundary_structure_floor
            else -1
        )
        last_supported = index
        # 三個 weak 已提供 open confirmation；開啟後才開始計 continuation decay。
        weak_run = 0
        negative_run = 0
        pending_weak = []
        reference = _fingerprint(records[index])
        reference_strength = float(
            records[index].battle_text_evidence.get("text_line_strength") or 0.0
        )
        layout_change_run = 0
        layout_change_start = -1
        layout_previous_strong = -1
        strong_sample_count = 1 if level == "strong" else 0
        transition_evidence_start = -1
        transition_negative_seen = False
        transition_weak_seen = False

    def close_segment(end: int, reason: str, trigger: int) -> None:
        nonlocal active_start, active_open_reason, split_from_previous
        nonlocal last_strong, last_weak, last_structural_weak
        nonlocal last_supported, weak_run, negative_run
        nonlocal reference, reference_strength
        nonlocal layout_change_run, layout_change_start, layout_previous_strong
        nonlocal strong_sample_count, transition_evidence_start, transition_negative_seen
        nonlocal transition_weak_seen
        if active_start is None:
            return
        end = max(active_start, end)
        segments.append(
            _Segment(
                start_index=active_start,
                end_index=end,
                open_reason=active_open_reason,
                close_reason=reason,
                close_trigger_index=trigger,
                split_from_previous=split_from_previous,
            )
        )
        active_start = None
        active_open_reason = ""
        split_from_previous = False
        last_strong = -1
        last_weak = -1
        last_structural_weak = -1
        last_supported = -1
        weak_run = 0
        negative_run = 0
        reference = {}
        reference_strength = -1.0
        layout_change_run = 0
        layout_change_start = -1
        layout_previous_strong = -1
        strong_sample_count = 0
        transition_evidence_start = -1
        transition_negative_seen = False
        transition_weak_seen = False

    for index, record in enumerate(records):
        level = _evidence_level(record, threshold)
        if active_start is None:
            if level == "strong":
                weak_open_blocked = False
                start = pending_weak[0] if pending_weak else index
                reason = "strong_positive"
                if pending_weak:
                    reason = "weak_confirmed_by_strong"
                open_segment(index, start, reason)
            elif level == "weak":
                if weak_open_blocked:
                    continue
                if not pending_weak or pending_weak[-1] == index - 1:
                    pending_weak.append(index)
                else:
                    pending_weak = [index]
                pending_weak = pending_weak[-config.weak_open_min_samples :]
                if len(pending_weak) >= config.weak_open_min_samples:
                    open_segment(
                        index,
                        pending_weak[0],
                        "temporally_confirmed_weak_positive",
                    )
            else:
                pending_weak = []
                weak_open_blocked = False
            continue

        if level == "strong":
            current_fingerprint = _fingerprint(record)
            layout_distance = layout_fingerprint_distance(reference, current_fingerprint)
            # 淡入期間的字形遮罩會逐格長大；先建立穩定 reference，避免把動畫拆碎。
            after_grace = strong_sample_count >= config.layout_grace_strong_samples
            resumed_after_transition = transition_evidence_start >= 0
            transition_threshold = (
                config.negative_gap_with_weak_layout_change_threshold
                if transition_negative_seen and transition_weak_seen
                else (
                    config.negative_gap_layout_change_threshold
                    if transition_negative_seen
                    else config.fade_layout_change_threshold
                )
            )
            if (
                after_grace
                and resumed_after_transition
                and layout_distance >= transition_threshold
            ):
                previous_end = max(int(active_start), last_strong)
                close_segment(previous_end, "layout_transition_after_fade", index)
                open_segment(
                    index,
                    index,
                    "layout_transition_after_fade",
                    split=True,
                )
                continue
            transition_evidence_start = -1
            transition_negative_seen = False
            transition_weak_seen = False
            if after_grace and layout_distance >= config.layout_change_threshold:
                if layout_change_run == 0:
                    layout_change_start = index
                    layout_previous_strong = last_strong
                layout_change_run += 1
            else:
                layout_change_run = 0
                layout_change_start = -1
                layout_previous_strong = -1
            if layout_change_run >= config.layout_change_persistence_samples:
                transition_start = layout_change_start
                previous_end = (
                    layout_previous_strong
                    if layout_previous_strong >= int(active_start)
                    else transition_start - 1
                )
                close_segment(previous_end, "persistent_layout_transition", index)
                open_segment(
                    index,
                    transition_start,
                    "persistent_layout_transition",
                    split=True,
                )
                continue
            weak_run = 0
            negative_run = 0
            last_strong = index
            last_weak = -1
            last_structural_weak = -1
            last_supported = index
            strong_sample_count += 1
            strength = float(record.battle_text_evidence.get("text_line_strength") or 0.0)
            if (
                not after_grace
                or layout_distance < config.layout_change_threshold
            ) and strength > reference_strength:
                reference = current_fingerprint
                reference_strength = strength
            continue

        layout_change_run = 0
        layout_change_start = -1
        layout_previous_strong = -1
        if level == "weak":
            if last_strong >= int(active_start) and transition_evidence_start < 0:
                transition_evidence_start = index
            if transition_negative_seen:
                transition_weak_seen = True
            negative_run = 0
            weak_run += 1
            if weak_run <= config.max_weak_continuation_samples:
                last_weak = index
                if (
                    float(record.battle_text_evidence.get("text_line_strength") or 0.0)
                    >= config.weak_boundary_structure_floor
                ):
                    last_structural_weak = index
                last_supported = index
                continue
            weak_boundary = (
                last_weak
                if last_strong < 0
                else min(last_weak, last_strong + config.max_weak_boundary_samples)
            )
            if last_structural_weak >= 0:
                structural_boundary = (
                    last_structural_weak
                    if last_strong < 0
                    else min(
                        last_structural_weak,
                        last_strong + config.max_structural_weak_boundary_samples,
                    )
                )
                weak_boundary = max(weak_boundary, structural_boundary)
            tail_end = max(
                int(active_start),
                last_strong,
                weak_boundary,
            )
            close_segment(tail_end, "weak_evidence_decay", index)
            pending_weak = []
            weak_open_blocked = True
            continue

        if last_strong >= int(active_start) and transition_evidence_start < 0:
            transition_evidence_start = index
        transition_negative_seen = True
        negative_run += 1
        if negative_run <= config.max_negative_gap_samples:
            last_supported = index
            continue
        weak_boundary = (
            last_weak
            if last_strong < 0
            else min(last_weak, last_strong + config.max_weak_boundary_samples)
        )
        if last_structural_weak >= 0:
            structural_boundary = (
                last_structural_weak
                if last_strong < 0
                else min(
                    last_structural_weak,
                    last_strong + config.max_structural_weak_boundary_samples,
                )
            )
            weak_boundary = max(weak_boundary, structural_boundary)
        tail_end = max(
            int(active_start),
            last_strong,
            weak_boundary,
            last_supported,
        )
        close_segment(tail_end, "negative_gap_exceeded", index)
        pending_weak = []

    if active_start is not None:
        end = max(int(active_start), last_strong, last_weak, last_supported)
        close_segment(min(len(records) - 1, end), "end_of_stream", len(records) - 1)
    return _merge_same_layout_reopens(records, segments, threshold, config)


def _merge_same_layout_reopens(
    records: Sequence[FrameScanRecord],
    segments: Sequence[_Segment],
    threshold: float,
    config: BattleTextTemporalConfig,
) -> List[_Segment]:
    """把短暫遮擋後恢復的同一版面合併，避免把同一句誤切兩段。"""

    merged: List[_Segment] = []
    for current in segments:
        if not merged:
            merged.append(current)
            continue
        previous = merged[-1]
        gap_start = previous.end_index + 1
        gap_end = current.start_index - 1
        gap_samples = max(0, gap_end - gap_start + 1)
        gap_is_negative = gap_samples > 0 and all(
            _evidence_level(records[index], threshold) == "negative"
            for index in range(gap_start, gap_end + 1)
        )
        can_reopen = (
            not current.split_from_previous
            and previous.close_reason == "negative_gap_exceeded"
            and gap_is_negative
            and gap_samples >= config.min_same_layout_reopen_gap_samples
            and gap_samples <= config.max_same_layout_reopen_gap_samples
        )
        if can_reopen:
            previous_strong = [
                index
                for index in range(previous.start_index, previous.end_index + 1)
                if _evidence_level(records[index], threshold) == "strong"
            ][-config.same_layout_comparison_samples :]
            current_strong = [
                index
                for index in range(current.start_index, current.end_index + 1)
                if _evidence_level(records[index], threshold) == "strong"
            ][: config.same_layout_comparison_samples]
            minimum_distance = min(
                (
                    layout_fingerprint_distance(
                        _fingerprint(records[left]), _fingerprint(records[right])
                    )
                    for left in previous_strong
                    for right in current_strong
                ),
                default=1.0,
            )
            if minimum_distance <= config.same_layout_reopen_threshold:
                merged[-1] = _Segment(
                    start_index=previous.start_index,
                    end_index=current.end_index,
                    open_reason=previous.open_reason,
                    close_reason=current.close_reason,
                    close_trigger_index=current.close_trigger_index,
                    split_from_previous=previous.split_from_previous,
                    same_layout_reopen_gaps=(
                        previous.same_layout_reopen_gaps
                        + ((gap_start, gap_end),)
                        + current.same_layout_reopen_gaps
                    ),
                )
                continue
        merged.append(current)
    return merged


def _diagnostic_base(
    record: FrameScanRecord,
    threshold: float,
) -> Dict[str, Any]:
    evidence = dict(record.battle_text_evidence)
    level = _evidence_level(record, threshold)
    return {
        "sample_index": record.sample_index,
        "timestamp": record.timestamp,
        "pts": record.pts,
        "frame_ordinal": record.frame_index,
        "battle_text_score": round(float(record.candidate_scores["BATTLE_TEXT"]), 6),
        "threshold": float(threshold),
        "raw_positive": level != "negative",
        "strong_positive": level == "strong",
        "weak_positive": level == "weak",
        "evidence_level": level,
        "candidate_active_before": False,
        "candidate_active_after": False,
        "candidate_id": "",
        "closed_candidate_id": "",
        "decision": "negative",
        "suppression_reason": "",
        "boundary_reason": "",
        "open_reason": "",
        "close_reason": "",
        "merge_reason": "",
        "split_reason": "",
        "layout_change_score": 0.0,
        "layout_reference_distance": 0.0,
        "weak_run_length": 0,
        "negative_run_length": 0,
        "template_similarity": evidence.get("template_similarity"),
        "template_strength": evidence.get("template_strength"),
        "visual_structure_strength": evidence.get("visual_structure_strength"),
        "positive_reasons": evidence.get("positive_reasons", []),
        "negative_reasons": evidence.get("negative_reasons", []),
        "layout_fingerprint": evidence.get("layout_fingerprint", {}),
        "features": {
            key: evidence.get(key)
            for key in (
                "local_edge_density",
                "top_row_density",
                "component_count",
                "low_saturation_ratio_60",
                "low_saturation_ratio_90",
                "text_line_strength",
                "aligned_component_count",
                "line_span_ratio",
                "line_height_cv",
                "text_mask_ratio",
                "large_bright_fraction",
                "dark_background_ratio",
            )
        },
    }


def build_battle_text_timeline(
    records: Sequence[FrameScanRecord],
    scan_hz: float,
    threshold: float,
    config: BattleTextTemporalConfig = DEFAULT_BATTLE_TEXT_TEMPORAL_CONFIG,
) -> Tuple[List[EventCandidate], List[Dict[str, Any]]]:
    if scan_hz <= 0:
        raise ValueError("scan_hz 必須大於 0")
    segments = _segment_records(records, threshold, config)
    diagnostics = [_diagnostic_base(record, threshold) for record in records]
    events: List[EventCandidate] = []
    previous_segment: Optional[_Segment] = None
    for number, segment in enumerate(segments, start=1):
        segment.event_id = "battle_text-{:04d}".format(number)
        span = records[segment.start_index : segment.end_index + 1]
        scores = [float(record.candidate_scores["BATTLE_TEXT"]) for record in span]
        start = span[0]
        end = span[-1]
        events.append(
            EventCandidate(
                event_id=segment.event_id,
                type="BATTLE_TEXT",
                start_frame=start.frame_index,
                end_frame=end.frame_index,
                start_time=round(start.pts, 6),
                end_time=round(end.pts, 6),
                start_timestamp=_format_timestamp(start.pts),
                end_timestamp=_format_timestamp(end.pts),
                duration_sec=round(
                    max(1.0 / scan_hz, end.pts - start.pts + 1.0 / scan_hz), 6
                ),
                confidence=round(float(np.mean(scores)), 6),
                sample_count=len(span),
                visible_rois=["battle_text"],
            )
        )
        weak_run = 0
        negative_run = 0
        reference: Dict[str, Any] = {}
        reference_strength = -1.0
        strong_count = 0
        same_layout_gap_indices = {
            index
            for gap_start, gap_end in segment.same_layout_reopen_gaps
            for index in range(gap_start, gap_end + 1)
        }
        for index in range(segment.start_index, segment.end_index + 1):
            row = diagnostics[index]
            row["candidate_id"] = segment.event_id
            row["candidate_active_before"] = index > segment.start_index
            row["candidate_active_after"] = index < segment.end_index
            level = row["evidence_level"]
            if index == segment.start_index:
                row["decision"] = (
                    "split_on_layout_transition"
                    if segment.split_from_previous
                    else "open_candidate"
                )
                row["open_reason"] = segment.open_reason
                row["boundary_reason"] = segment.open_reason
                if segment.split_from_previous:
                    row["split_reason"] = segment.open_reason
                    if previous_segment is not None:
                        row["closed_candidate_id"] = previous_segment.event_id
            elif level == "negative":
                negative_run += 1
                weak_run = 0
                if index in same_layout_gap_indices:
                    row["decision"] = "bridged_same_layout_reopen"
                    row["merge_reason"] = "same_layout_reopen_after_obstruction"
                    row["boundary_reason"] = "same_layout_reopen_gap"
                else:
                    row["decision"] = "bridged_gap"
                    row["merge_reason"] = "single_negative_gap"
                    row["boundary_reason"] = "short_negative_gap"
            elif level == "weak":
                weak_run += 1
                negative_run = 0
                row["decision"] = "continue_weak"
                row["merge_reason"] = "bounded_weak_continuation"
            else:
                if negative_run:
                    row["merge_reason"] = "resumed_after_single_negative_gap"
                elif weak_run:
                    row["merge_reason"] = "resumed_after_weak_fade"
                row["decision"] = "continue_candidate"
                weak_run = 0
                negative_run = 0
            row["weak_run_length"] = weak_run
            row["negative_run_length"] = negative_run
            current = row.get("layout_fingerprint")
            if level == "strong" and isinstance(current, dict):
                strength = float(row["features"].get("text_line_strength") or 0.0)
                distance = 0.0
                if reference:
                    distance = layout_fingerprint_distance(reference, current)
                    row["layout_change_score"] = round(distance, 6)
                    row["layout_reference_distance"] = round(distance, 6)
                after_grace = strong_count >= config.layout_grace_strong_samples
                if (
                    not reference
                    or not after_grace
                    or (
                        distance < config.layout_change_threshold
                        and strength > reference_strength
                    )
                ):
                    reference = current
                    reference_strength = strength
                strong_count += 1
        trigger = segment.close_trigger_index
        if 0 <= trigger < len(diagnostics):
            close_row = diagnostics[trigger]
            if not segment.split_from_previous or trigger != segment.start_index:
                if not close_row["candidate_id"]:
                    close_row["candidate_id"] = segment.event_id
                close_row["closed_candidate_id"] = segment.event_id
                close_row["close_reason"] = segment.close_reason
                if close_row["decision"] == "negative":
                    close_row["decision"] = "close_candidate"
                    close_row["boundary_reason"] = segment.close_reason
        previous_segment = segment
    return events, diagnostics
