"""Checkpoint 1G frame request planning；所有時間均轉為 verified PTS ordinal。"""

from typing import Any, Dict, List, Mapping, Sequence

from .checkpoint1g_models import VisualFrameRequest
from .models import FrameTimestampIndex


STATUS_INTERVAL_SEC = 0.5
STATUS_OCR_INTERVAL_SEC = 2.0


def _request(
    request_id: str,
    source_id: str,
    role: str,
    roi_name: str,
    ordinal: int,
    index: FrameTimestampIndex,
    **kwargs,
) -> VisualFrameRequest:
    return VisualFrameRequest(
        request_id=request_id,
        source_id=source_id,
        role=role,
        roi_name=roi_name,
        frame_ordinal=ordinal,
        pts=round(float(index.pts_sec[ordinal]), 6),
        **kwargs,
    )


def build_visual_frame_requests(
    events: Sequence[Mapping[str, Any]],
    review_records: Sequence[Mapping[str, Any]],
    timeline_groups: Sequence[Mapping[str, Any]],
    index: FrameTimestampIndex,
) -> List[VisualFrameRequest]:
    review_by_id = {str(row["candidate_id"]): row for row in review_records}
    requests: List[VisualFrameRequest] = []
    selected_events = [
        row for row in events if row["type"] in ("TEAM_PREVIEW", "SELECTED_FOUR", "MOVE_MENU")
    ]
    for event in selected_events:
        candidate_id = str(event["event_id"])
        review = review_by_id[candidate_id]
        ordinal = int(review["representative_frame"])
        if event["type"] == "TEAM_PREVIEW":
            for side in ("player", "opponent"):
                for slot_index in range(1, 7):
                    requests.append(
                        _request(
                            "team-{}-slot{}".format(side, slot_index), candidate_id,
                            "team_preview", "team_preview_{}:slot{}".format(side, slot_index),
                            ordinal, index, side=side, slot="slot{}".format(slot_index),
                            run_ocr=True, keep_evidence=True,
                        )
                    )
        elif event["type"] == "SELECTED_FOUR":
            for slot_index in range(1, 5):
                requests.append(
                    _request(
                        "selected-four-slot{}".format(slot_index), candidate_id,
                        "selected_four", "selected_four:slot{}".format(slot_index),
                        ordinal, index, side="player", slot="slot{}".format(slot_index),
                        run_ocr=False, keep_evidence=True,
                    )
                )
        else:
            menu_number = candidate_id.split("-")[-1]
            requests.append(
                _request(
                    "move-menu-{}".format(menu_number), candidate_id, "move_menu", "move_menu",
                    ordinal, index, side="player", run_ocr=True, keep_evidence=True,
                )
            )
            for side in ("player", "opponent"):
                for slot_index, slot in ((1, "left"), (2, "right")):
                    requests.append(
                        _request(
                            "move-menu-{}-{}-{}".format(menu_number, side, slot), candidate_id,
                            "menu_status", "{}_status:slot{}".format(side, slot_index), ordinal,
                            index, side=side, slot=slot, run_ocr=True, keep_evidence=False,
                        )
                    )

    if timeline_groups:
        start = float(timeline_groups[0]["start_time"])
        end = float(timeline_groups[-1]["end_time"])
        targets = []
        current = start
        while current <= end + 1e-9:
            targets.append(current)
            current += STATUS_INTERVAL_SEC
        last_ordinal = None
        sample_index = 0
        for target in targets:
            ordinal = index.nearest_ordinal(target)
            if ordinal == last_ordinal:
                continue
            last_ordinal = ordinal
            run_ocr = sample_index % int(round(STATUS_OCR_INTERVAL_SEC / STATUS_INTERVAL_SEC)) == 0
            for side in ("player", "opponent"):
                for slot_index, slot in ((1, "left"), (2, "right")):
                    requests.append(
                        _request(
                            "status-{:05d}-{}-{}".format(sample_index, side, slot),
                            "status-sample-{:05d}".format(sample_index), "status_sample",
                            "{}_status:slot{}".format(side, slot_index), ordinal, index,
                            side=side, slot=slot, run_ocr=run_ocr, keep_evidence=run_ocr,
                        )
                    )
            sample_index += 1
    ids = [row.request_id for row in requests]
    if len(ids) != len(set(ids)):
        raise ValueError("Checkpoint 1G frame request ID 重複")
    return requests
