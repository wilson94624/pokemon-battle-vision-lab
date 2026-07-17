"""Move Menu OCR aggregation；只保存畫面可支持的選項。"""

import difflib
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .checkpoint1g_models import ExtractedVisualFrame, OcrObservation


CJK_TOKEN = re.compile(r"[\u3400-\u9fff]{2,12}")


def conservative_match(raw: str, lexicon: Iterable[str]) -> Tuple[Optional[str], float, str]:
    normalized = re.sub(r"\s+", "", raw)
    if not normalized:
        return None, 0.0, "empty"
    exact = [item for item in lexicon if item == normalized]
    if exact:
        return exact[0], 1.0, "exact"
    scored = [
        (difflib.SequenceMatcher(None, normalized, item).ratio(), item)
        for item in lexicon
    ]
    score, item = max(scored, default=(0.0, None))
    # 三字招式一字 OCR 誤差的 SequenceMatcher ratio 為 2/3；仍保存 raw text 與分數。
    if item is not None and score >= 0.66:
        return item, round(score, 6), "conservative_fuzzy"
    return None, round(score, 6), "unmatched"


def parse_visible_moves(ocr: OcrObservation, lexicon: Sequence[str]) -> List[Dict[str, Any]]:
    values: List[Dict[str, Any]] = []
    seen = set()
    source_lines = [str(line.get("text", "")) for line in ocr.lines] or [ocr.raw_text]
    for line in source_lines:
        for token in CJK_TOKEN.findall(line):
            corrected, score, method = conservative_match(token, lexicon)
            value = corrected or token
            if value in seen or (corrected is None and len(token) > 7):
                continue
            seen.add(value)
            values.append(
                {
                    "raw_text": token,
                    "value": value,
                    "correction_method": method,
                    "correction_confidence": score,
                    "ocr_confidence": ocr.confidence,
                }
            )
    return values[:4]


def selecting_slot(player_frames: Sequence[ExtractedVisualFrame]) -> Tuple[str, float]:
    rows = sorted(player_frames, key=lambda row: str(row.request.slot))
    if len(rows) != 2:
        return "unknown", 0.0
    scores = [float(row.fingerprint.get("mean_brightness", 0.0)) for row in rows]
    margin = abs(scores[0] - scores[1])
    if margin < 0.025:
        return "unknown", round(margin / 0.025, 6)
    return ("left" if scores[0] > scores[1] else "right"), round(min(1.0, margin / 0.12), 6)


def parse_move_menu_observations(
    menu_frames: Sequence[ExtractedVisualFrame],
    status_frames: Sequence[ExtractedVisualFrame],
    ocr_by_request: Mapping[str, OcrObservation],
    candidates: Sequence[Mapping[str, Any]],
    move_lexicon: Sequence[str],
) -> Dict[str, Any]:
    by_candidate: Dict[str, List[ExtractedVisualFrame]] = {}
    for frame in [*menu_frames, *status_frames]:
        by_candidate.setdefault(frame.request.source_id, []).append(frame)
    observations = []
    for index, candidate in enumerate(candidates, start=1):
        candidate_id = str(candidate["event_id"])
        current = by_candidate.get(candidate_id, [])
        menus = [row for row in current if row.request.roi_name == "move_menu"]
        players = [row for row in current if row.request.roi_name.startswith("player_status:")]
        slot, slot_confidence = selecting_slot(players)
        ocr = ocr_by_request.get(menus[0].request.request_id) if menus else None
        moves = parse_visible_moves(ocr, move_lexicon) if ocr else []
        rejection_reason = (
            "representative_menu_frame_missing"
            if not menus
            else (
                "apple_vision_runtime_error"
                if ocr and ocr.error
                else (None if moves else "no_reliable_move_text")
            )
        )
        confidence = round(
            0.45 * float(candidate.get("confidence", 0.0))
            + 0.3 * (ocr.confidence if ocr else 0.0)
            + 0.25 * slot_confidence,
            6,
        )
        observations.append(
            {
                "decision_window_id": "decision-window-{:04d}".format(index),
                "candidate_id": candidate_id,
                "start_time": float(candidate["start_time"]),
                "end_time": float(candidate["end_time"]),
                "active_side": "player",
                "selecting_slot": slot,
                "pokemon": None,
                "available_moves": moves,
                "highlighted_move": None,
                "chosen_move": None,
                "target": None,
                "confidence": confidence,
                "knowledge": "observed" if moves else "unknown",
                "rejection_reason": rejection_reason,
                "review_status": "auto_accepted" if confidence >= 0.75 else "observation_only",
                "evidence": [
                    {
                        "frame_ordinal": menus[0].request.frame_ordinal if menus else None,
                        "pts": menus[0].request.pts if menus else None,
                        "roi": "move_menu",
                        "path": menus[0].evidence_path if menus else None,
                        "raw_ocr": ocr.raw_text if ocr else "",
                        "ocr_error": ocr.error if ocr else "ocr_not_requested",
                    },
                    {
                        "rule_id": "move_menu.selecting_slot.relative_status_brightness.v1",
                        "slot_confidence": slot_confidence,
                    },
                ],
            }
        )
    return {
        "schema_version": "0.1.0",
        "checkpoint": "1G",
        "kind": "move_menu_observations",
        "source_candidate_count": len(candidates),
        "observation_count": len(observations),
        "observations": observations,
    }
