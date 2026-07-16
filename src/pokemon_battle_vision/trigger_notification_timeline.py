"""TRIGGER_NOTIFICATION 的 side-aware temporal state machine。"""

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import numpy as np

from .checkpoint1b_models import EventCandidate, FrameScanRecord
from .trigger_notification_features import TRIGGER_SIDE_ROIS


@dataclass(frozen=True)
class TriggerNotificationTemporalConfig:
    weak_confirmation_samples: int = 2
    max_negative_gap_samples: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


DEFAULT_TRIGGER_TEMPORAL_CONFIG = TriggerNotificationTemporalConfig()


def _format_timestamp(seconds: float) -> str:
    milliseconds = max(0, int(round(float(seconds) * 1000.0)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return "{:02d}:{:02d}:{:02d}.{:03d}".format(hours, minutes, secs, millis)


def _side_evidence(record: FrameScanRecord, side: str) -> Dict[str, Any]:
    root = record.trigger_notification_evidence
    sides = root.get("sides", {}) if isinstance(root, dict) else {}
    value = sides.get(side, {}) if isinstance(sides, dict) else {}
    if isinstance(value, dict) and value:
        return dict(value)
    # 舊 unit fixtures 沒有 feature payload；僅以既有 visible ROI 保持 API 相容。
    canonical_roi = TRIGGER_SIDE_ROIS[side]
    if canonical_roi in record.visible_rois:
        score = float(record.candidate_scores.get("TRIGGER_NOTIFICATION", 0.0))
        return {
            "proposal_score": score,
            "combined_score": score,
            "evidence_level": "strong" if score >= 0.72 else "weak",
        }
    return {}


def _base_diagnostic(record: FrameScanRecord, side: str) -> Dict[str, Any]:
    evidence = _side_evidence(record, side)
    return {
        "frame_ordinal": record.frame_index,
        "pts": record.pts,
        "side": side,
        "canonical_roi_id": TRIGGER_SIDE_ROIS[side],
        "analysis_roi_id": str(evidence.get("analysis_roi_id", "")),
        "analysis_bbox": list(evidence.get("analysis_bbox", [])),
        "template_score": float(evidence.get("template_score", 0.0)),
        "brightness_contrast": float(evidence.get("brightness_contrast", 0.0)),
        "edge_density": float(evidence.get("edge_density", 0.0)),
        "component_count": int(evidence.get("component_count", 0)),
        "aligned_component_count": int(evidence.get("aligned_component_count", 0)),
        "line_span_ratio": float(evidence.get("line_span_ratio", 0.0)),
        "secondary_aligned_component_count": int(
            evidence.get("secondary_aligned_component_count", 0)
        ),
        "secondary_line_span_ratio": float(
            evidence.get("secondary_line_span_ratio", 0.0)
        ),
        "secondary_line_height_cv": float(
            evidence.get("secondary_line_height_cv", 0.0)
        ),
        "line_separation_ratio": float(evidence.get("line_separation_ratio", 0.0)),
        "primary_glyph_like_count": int(
            evidence.get("primary_glyph_like_count", 0)
        ),
        "secondary_glyph_like_count": int(
            evidence.get("secondary_glyph_like_count", 0)
        ),
        "panel_occupancy": float(evidence.get("panel_occupancy", 0.0)),
        "text_region_occupancy": float(evidence.get("text_region_occupancy", 0.0)),
        "icon_region_occupancy": float(evidence.get("icon_region_occupancy", 0.0)),
        "panel_score": float(evidence.get("panel_score", 0.0)),
        "text_score": float(evidence.get("text_score", 0.0)),
        "icon_score": float(evidence.get("icon_score", 0.0)),
        "combined_score": float(evidence.get("combined_score", 0.0)),
        "proposal_score": float(evidence.get("proposal_score", 0.0)),
        "evidence_level": str(evidence.get("evidence_level", "negative")),
        "temporal_state": "inactive",
        "decision": "negative",
        "candidate_id": "",
        "open_reason": "",
        "continue_reason": "",
        "close_reason": "",
    }


def _build_side_segments(
    records: Sequence[FrameScanRecord],
    side: str,
    config: TriggerNotificationTemporalConfig,
    diagnostics: List[Dict[str, Any]],
) -> List[Tuple[int, int, List[int], str, str]]:
    segments: List[Tuple[int, int, List[int], str, str]] = []
    active = False
    pending_weak: List[int] = []
    active_start = -1
    positive_indices: List[int] = []
    gap_indices: List[int] = []
    open_reason = ""

    def close(end_index: int, reason: str) -> None:
        nonlocal active, active_start, positive_indices, gap_indices, open_reason
        if active and positive_indices:
            segments.append((active_start, end_index, list(positive_indices), open_reason, reason))
        active = False
        active_start = -1
        positive_indices = []
        gap_indices = []
        open_reason = ""

    for index, record in enumerate(records):
        row = diagnostics[index]
        level = str(row["evidence_level"])
        if not active:
            if level == "strong":
                active = True
                active_start = index
                positive_indices = [index]
                pending_weak = []
                open_reason = "strong_proposal_immediate_open"
                row.update(
                    temporal_state="active",
                    decision="open_strong",
                    open_reason=open_reason,
                )
            elif level == "weak":
                pending_weak.append(index)
                row.update(temporal_state="pending_weak", decision="pending_weak_confirmation")
                if len(pending_weak) >= config.weak_confirmation_samples:
                    active = True
                    active_start = pending_weak[0]
                    positive_indices = list(pending_weak)
                    open_reason = "temporally_confirmed_weak_proposals"
                    for weak_index in pending_weak:
                        diagnostics[weak_index].update(
                            temporal_state="active",
                            decision=(
                                "open_weak_confirmed"
                                if weak_index == pending_weak[-1]
                                else "weak_confirmation_member"
                            ),
                            open_reason=open_reason,
                        )
                    pending_weak = []
            else:
                for weak_index in pending_weak:
                    diagnostics[weak_index].update(
                        temporal_state="inactive",
                        decision="discard_unconfirmed_weak",
                        close_reason="weak_proposal_not_temporally_confirmed",
                    )
                pending_weak = []
            continue

        if level in ("strong", "weak", "continuation"):
            if gap_indices:
                for gap_index in gap_indices:
                    diagnostics[gap_index].update(
                        temporal_state="active",
                        decision="bridge_confirmed",
                        continue_reason="single_negative_gap_bridged",
                    )
            gap_indices = []
            positive_indices.append(index)
            row.update(
                temporal_state="active",
                decision="continue_{}".format(level),
                continue_reason=(
                    "primary_glyph_continuation_support"
                    if level == "continuation"
                    else "continuing_{}_proposal".format(level)
                ),
            )
            continue

        gap_indices.append(index)
        if len(gap_indices) <= config.max_negative_gap_samples:
            row.update(
                temporal_state="gap_pending",
                decision="bridge_pending",
                continue_reason="single_negative_gap_pending",
            )
            continue
        last_positive = positive_indices[-1]
        row.update(
            temporal_state="closed",
            decision="close_negative_gap",
            close_reason="negative_gap_exceeded_bridge_limit",
        )
        diagnostics[gap_indices[0]].update(
            temporal_state="closed",
            decision="close_boundary_negative",
            close_reason="text_structure_disappeared",
        )
        close(last_positive, "negative_gap_exceeded_bridge_limit")
        gap_indices = []
    if active and positive_indices:
        close(positive_indices[-1], "end_of_stream")
    for weak_index in pending_weak:
        diagnostics[weak_index].update(
            temporal_state="inactive",
            decision="discard_unconfirmed_weak",
            close_reason="end_of_stream_before_weak_confirmation",
        )
    return segments


def build_trigger_notification_timeline(
    records: Sequence[FrameScanRecord],
    scan_hz: float = 10.0,
    config: TriggerNotificationTemporalConfig = DEFAULT_TRIGGER_TEMPORAL_CONFIG,
) -> Tuple[List[EventCandidate], List[Dict[str, Any]]]:
    if scan_hz <= 0:
        raise ValueError("scan_hz 必須大於 0")
    if config.weak_confirmation_samples < 1 or config.max_negative_gap_samples < 0:
        raise ValueError("trigger temporal config 無效")
    diagnostics_by_side = {
        side: [_base_diagnostic(record, side) for record in records]
        for side in TRIGGER_SIDE_ROIS
    }
    raw_segments = []
    for side in TRIGGER_SIDE_ROIS:
        segments = _build_side_segments(
            records, side, config, diagnostics_by_side[side]
        )
        raw_segments.extend((start, end, positives, side, open_reason, close_reason) for start, end, positives, open_reason, close_reason in segments)
    raw_segments.sort(key=lambda value: (records[value[0]].pts, value[3]))
    events: List[EventCandidate] = []
    for event_number, (start_index, end_index, positives, side, open_reason, close_reason) in enumerate(raw_segments, start=1):
        start = records[start_index]
        end = records[end_index]
        candidate_id = "trigger_notification-{:04d}".format(event_number)
        scores = [
            float(_side_evidence(records[index], side).get("proposal_score", 0.0))
            for index in positives
        ]
        duration = max(1.0 / scan_hz, end.pts - start.pts + 1.0 / scan_hz)
        events.append(
            EventCandidate(
                event_id=candidate_id,
                type="TRIGGER_NOTIFICATION",
                start_frame=start.frame_index,
                end_frame=end.frame_index,
                start_time=round(start.pts, 6),
                end_time=round(end.pts, 6),
                start_timestamp=_format_timestamp(start.pts),
                end_timestamp=_format_timestamp(end.pts),
                duration_sec=round(duration, 6),
                confidence=round(float(np.mean(scores)), 6),
                sample_count=end_index - start_index + 1,
                visible_rois=[TRIGGER_SIDE_ROIS[side]],
            )
        )
        for row_index in range(start_index, end_index + 1):
            diagnostics_by_side[side][row_index]["candidate_id"] = candidate_id
        diagnostics_by_side[side][start_index]["open_reason"] = open_reason
        diagnostics_by_side[side][end_index]["close_reason"] = close_reason
    diagnostics = [
        diagnostics_by_side[side][index]
        for index in range(len(records))
        for side in TRIGGER_SIDE_ROIS
    ]
    return events, diagnostics
