"""將固定 10 Hz frame candidates 聚合為可供後續 parser 使用的時間區段。"""

from typing import Dict, List, Mapping, Sequence

import numpy as np

from .candidate_detection import DEFAULT_THRESHOLDS, EVENT_ROIS
from .checkpoint1b_models import EVENT_TYPES, EventCandidate, FrameScanRecord


DEFAULT_MIN_SAMPLES: Dict[str, int] = {
    "TEAM_PREVIEW": 3,
    "SELECTED_FOUR": 3,
    "MOVE_MENU": 3,
    "BATTLE_TEXT": 2,
    "TRIGGER_NOTIFICATION": 2,
    "RESULT": 3,
}


def format_timestamp(seconds: float) -> str:
    milliseconds = max(0, int(round(float(seconds) * 1000.0)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return "{:02d}:{:02d}:{:02d}.{:03d}".format(hours, minutes, secs, millis)


def build_event_timeline(
    records: Sequence[FrameScanRecord],
    scan_hz: float = 10.0,
    thresholds: Mapping[str, float] = DEFAULT_THRESHOLDS,
    min_samples: Mapping[str, int] = DEFAULT_MIN_SAMPLES,
    max_gap_samples: int = 2,
) -> List[EventCandidate]:
    if scan_hz <= 0:
        raise ValueError("scan_hz 必須大於 0")
    if max_gap_samples < 0:
        raise ValueError("max_gap_samples 不可小於 0")
    events: List[EventCandidate] = []
    for event_type in EVENT_TYPES:
        active_indices = [
            index
            for index, record in enumerate(records)
            if float(record.candidate_scores[event_type]) >= float(thresholds[event_type])
        ]
        if not active_indices:
            continue
        groups: List[List[int]] = [[active_indices[0]]]
        for index in active_indices[1:]:
            if index - groups[-1][-1] <= max_gap_samples + 1:
                groups[-1].append(index)
            else:
                groups.append([index])
        accepted_index = 0
        for group in groups:
            start_index = group[0]
            end_index = group[-1]
            span = records[start_index : end_index + 1]
            if len(span) < int(min_samples[event_type]):
                continue
            accepted_index += 1
            start = span[0]
            end = span[-1]
            scores = [float(record.candidate_scores[event_type]) for record in span]
            duration = max(1.0 / scan_hz, end.pts - start.pts + 1.0 / scan_hz)
            visible_rois = sorted(
                {
                    roi_id
                    for record in span
                    for roi_id in record.visible_rois
                    if roi_id in EVENT_ROIS[event_type]
                }
            )
            events.append(
                EventCandidate(
                    event_id="{}-{:04d}".format(event_type.lower(), accepted_index),
                    type=event_type,
                    start_frame=start.frame_index,
                    end_frame=end.frame_index,
                    start_time=round(start.pts, 6),
                    end_time=round(end.pts, 6),
                    start_timestamp=format_timestamp(start.pts),
                    end_timestamp=format_timestamp(end.pts),
                    duration_sec=round(duration, 6),
                    confidence=round(float(np.mean(scores)), 6),
                    sample_count=len(span),
                    visible_rois=visible_rois or list(EVENT_ROIS[event_type]),
                )
            )
    events.sort(key=lambda event: (event.start_time, EVENT_TYPES.index(event.type)))
    return events
