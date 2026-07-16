"""由 frozen Checkpoint 1B evidence 決定多影格 OCR 輸入。"""

from collections import defaultdict
from typing import Any, DefaultDict, Dict, List, Mapping, Sequence, Tuple

from .checkpoint1c_models import OcrFrameSelection
from .errors import InputError
from .models import FrameTimestampIndex


SUPPORTED_OCR_TYPES = ("BATTLE_TEXT", "TRIGGER_NOTIFICATION")
MAX_OCR_FRAMES = 7


def _trigger_side(event: Mapping[str, Any]) -> str:
    visible = [str(value) for value in event.get("visible_rois", [])]
    if "player_trigger_notification" in visible:
        return "player"
    if "opponent_trigger_notification" in visible:
        return "opponent"
    raise InputError("TRIGGER_NOTIFICATION 缺少可辨識 side：{}".format(event["event_id"]))


def _evidence(row: Mapping[str, Any], event_type: str, side: str) -> Mapping[str, Any]:
    if event_type == "BATTLE_TEXT":
        value = row.get("battle_text_evidence", {})
    else:
        value = (
            row.get("trigger_notification_evidence", {})
            .get("sides", {})
            .get(side, {})
        )
    return value if isinstance(value, Mapping) else {}


def _quality(row: Mapping[str, Any], event_type: str, side: str) -> Tuple[float, float]:
    evidence = _evidence(row, event_type, side)
    scores = row.get("candidate_scores", {})
    if event_type == "BATTLE_TEXT":
        structure = float(
            evidence.get("text_line_strength")
            or evidence.get("visual_structure_strength")
            or 0.0
        )
        proposal = float(scores.get(event_type, evidence.get("proposal_score", 0.0)))
        return round(0.65 * proposal + 0.35 * structure, 6), round(structure, 6)
    structure = float(evidence.get("text_score", 0.0))
    combined = float(evidence.get("combined_score", scores.get(event_type, 0.0)))
    return round(0.7 * combined + 0.3 * structure, 6), round(structure, 6)


def _template_strength(row: Mapping[str, Any], event_type: str, side: str) -> float:
    if event_type != "BATTLE_TEXT":
        return 0.0
    return round(
        float(_evidence(row, event_type, side).get("template_strength", 0.0)), 6
    )


def select_ocr_frames(
    event: Mapping[str, Any],
    review_record: Mapping[str, Any],
    frame_records: Sequence[Mapping[str, Any]],
    timestamp_index: FrameTimestampIndex,
) -> List[OcrFrameSelection]:
    event_id = str(event["event_id"])
    event_type = str(event["type"])
    if event_type not in SUPPORTED_OCR_TYPES:
        raise InputError("Checkpoint 1C 不支援 OCR event type：{}".format(event_type))
    side = _trigger_side(event) if event_type == "TRIGGER_NOTIFICATION" else ""
    start = int(event["start_frame"])
    end = int(event["end_frame"])
    within = [
        row for row in frame_records if start <= int(row["frame_index"]) <= end
    ]
    if not within:
        raise InputError("{} 區間內沒有 Checkpoint 1B sampled frame".format(event_id))
    for row in within:
        ordinal = int(row["frame_index"])
        if abs(float(timestamp_index.pts_sec[ordinal]) - float(row["pts"])) > 1e-6:
            raise InputError("{} 的 ordinal／PTS mapping 不一致".format(event_id))

    review_roles: DefaultDict[int, List[str]] = defaultdict(list)
    for point in review_record.get("evidence_frames", []):
        ordinal = int(point["frame_index"])
        review_roles[ordinal].extend(str(role) for role in point.get("roles", []))
    strong = [
        row
        for row in within
        if str(_evidence(row, event_type, side).get("evidence_level", "negative"))
        == "strong"
    ]
    ranked = strong or within
    peak = max(
        ranked,
        key=lambda row: (_quality(row, event_type, side)[0], -int(row["frame_index"])),
    )
    peak_index = within.index(peak)
    reasons: DefaultDict[int, List[str]] = defaultdict(list)

    def add(row: Mapping[str, Any], reason: str) -> None:
        ordinal = int(row["frame_index"])
        if reason not in reasons[ordinal]:
            reasons[ordinal].append(reason)

    if strong:
        add(strong[0], "first_strong_positive")
        add(strong[-1], "last_strong_positive")
    add(peak, "trigger_peak_evidence" if event_type == "TRIGGER_NOTIFICATION" else "peak_score_structure")
    if peak_index > 0:
        add(within[peak_index - 1], "before_peak")
    if peak_index + 1 < len(within):
        add(within[peak_index + 1], "after_peak")
    evidence_strip = [
        row
        for row in within
        if "evidence_strip" in review_roles.get(int(row["frame_index"]), [])
    ]
    if evidence_strip:
        add(
            max(evidence_strip, key=lambda row: _quality(row, event_type, side)[0]),
            "highest_quality_evidence_strip",
        )
    if event_type == "TRIGGER_NOTIFICATION" or len(reasons) < 3:
        add(within[0], "candidate_start")
        add(within[-1], "candidate_end")

    priority = {
        "peak_score_structure": 0,
        "trigger_peak_evidence": 0,
        "first_strong_positive": 1,
        "last_strong_positive": 2,
        "before_peak": 3,
        "after_peak": 4,
        "highest_quality_evidence_strip": 5,
        "candidate_start": 6,
        "candidate_end": 7,
    }
    if len(reasons) > MAX_OCR_FRAMES:
        chosen = sorted(
            reasons,
            key=lambda ordinal: (
                min(priority.get(reason, 99) for reason in reasons[ordinal]),
                ordinal,
            ),
        )[:MAX_OCR_FRAMES]
        reasons = defaultdict(list, {ordinal: reasons[ordinal] for ordinal in chosen})

    roi_name = (
        "{}_trigger_notification_analysis_context".format(side)
        if event_type == "TRIGGER_NOTIFICATION"
        else "battle_text"
    )
    insufficient = "candidate_has_one_sampled_frame" if len(reasons) < 2 else None
    selections: List[OcrFrameSelection] = []
    by_ordinal = {int(row["frame_index"]): row for row in within}
    for ordinal in sorted(reasons):
        row = by_ordinal[ordinal]
        quality, structure = _quality(row, event_type, side)
        formal_path = "frames/{}/{}/{:06d}__raw.png".format(
            event_type, event_id, ordinal
        )
        selections.append(
            OcrFrameSelection(
                event_id=event_id,
                event_type=event_type,
                frame_ordinal=ordinal,
                pts=round(float(row["pts"]), 6),
                selection_reason=reasons[ordinal][0],
                selection_reasons=reasons[ordinal],
                roi_name=roi_name,
                image_path=formal_path,
                frame_quality=quality,
                visual_text_strength=structure,
                detector_template_strength=_template_strength(row, event_type, side),
                insufficient_frame_reason=insufficient,
            )
        )
    return selections
