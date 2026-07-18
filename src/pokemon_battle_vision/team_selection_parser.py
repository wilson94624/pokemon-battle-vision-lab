"""Team Preview 與 Selected Four 的 partial、可追溯解析。"""

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .checkpoint1g_models import ExtractedVisualFrame, OcrObservation, ResolutionEdge
from .visual_identity import fingerprint_similarity, stable_visual_identity


CJK_TEXT = re.compile(r"[\u3400-\u9fff]{2,}")
SELECTION_MARKER = re.compile(r"(?<![0-9０-９])([1-4１-４])(?![0-9０-９])")
FULLWIDTH_MARKERS = str.maketrans("１２３４", "1234")


def _species_text(ocr: OcrObservation) -> Tuple[Any, float]:
    candidates = []
    for line in ocr.lines:
        text = str(line.get("text", "")).strip()
        if CJK_TEXT.search(text):
            candidates.append((text, float(line.get("confidence", ocr.confidence))))
    if not candidates:
        match = CJK_TEXT.search(ocr.raw_text)
        return (match.group(0), ocr.confidence) if match else (None, 0.0)
    # 物種名稱通常是短 CJK token；長敘述與道具文字保留於 raw evidence，不硬當 species。
    short = [row for row in candidates if 2 <= len(row[0]) <= 8]
    selected = max(short or candidates, key=lambda row: row[1])
    return selected[0], round(selected[1], 6)


def parse_team_roster(
    frames: Sequence[ExtractedVisualFrame],
    ocr_by_request: Mapping[str, OcrObservation],
    knowledge_base=None,
) -> Dict[str, Any]:
    entries: List[Dict[str, Any]] = []
    for frame in sorted(frames, key=lambda row: (str(row.request.side), str(row.request.slot))):
        request = frame.request
        ocr = ocr_by_request.get(request.request_id)
        species_text, text_confidence = _species_text(ocr) if ocr else (None, 0.0)
        species_candidates = (
            knowledge_base.resolve_species(species_text, limit=5)
            if knowledge_base is not None and species_text
            else []
        )
        resolved = (
            species_candidates[0]
            if species_candidates
            and (
                len(species_candidates) == 1
                or float(species_candidates[0]["confidence"])
                > float(species_candidates[1]["confidence"])
            )
            else None
        )
        visual_id = stable_visual_identity(
            "{}-preview-{}".format(request.side, request.slot), frame.fingerprint
        )
        confidence = (
            0.7 * text_confidence + 0.3 * float(resolved["confidence"])
            if resolved
            else (text_confidence if species_text else 0.55)
        )
        entries.append(
            {
                "side": request.side,
                "slot_index": int(str(request.slot).replace("slot", "")),
                "species_text": species_text,
                "species_id": int(resolved["canonical_species_id"]) if resolved else None,
                "canonical_species_name": resolved["traditional_chinese_name"] if resolved else None,
                "species_candidates": species_candidates,
                "visual_identity": visual_id,
                "visual_fingerprint": frame.fingerprint,
                "confidence": round(confidence, 6),
                "knowledge": (
                    "observed_and_knowledge_base_resolved"
                    if resolved
                    else ("observed" if species_text else "unknown")
                ),
                "source_candidate_id": request.source_id,
                "source_frame_ordinal": request.frame_ordinal,
                "source_pts": request.pts,
                "evidence": [
                    {
                        "kind": "roi_crop",
                        "roi": request.roi_name,
                        "path": frame.evidence_path,
                    },
                    {
                        "kind": "apple_vision_ocr",
                        "raw_text": ocr.raw_text if ocr else "",
                        "confidence": ocr.confidence if ocr else 0.0,
                        "error": ocr.error if ocr else "ocr_not_requested",
                    },
                    {
                        "kind": "pokemon_knowledge_base_resolution",
                        "rule_id": "pokemon_kb.exact_normalized_alias.v1",
                        "candidates": species_candidates,
                    },
                ],
                "review_status": "auto_accepted" if confidence >= 0.8 else "observation_only",
            }
        )
    return {
        "schema_version": "0.1.0",
        "checkpoint": "1G",
        "kind": "team_roster",
        "source_candidate_count": len({row.request.source_id for row in frames}),
        "entry_count": len(entries),
        "entries": entries,
    }


def _parse_legacy_selected_four(
    selected_frames: Sequence[ExtractedVisualFrame],
    roster: Mapping[str, Any],
) -> Tuple[Dict[str, Any], List[ResolutionEdge]]:
    """只供 revision 3 frozen artifact 重現；不得用於新 replay。"""

    player_entries = [row for row in roster["entries"] if row["side"] == "player"]
    rows = []
    edges: List[ResolutionEdge] = []
    used_roster_refs = set()
    for index, frame in enumerate(sorted(selected_frames, key=lambda row: row.request.slot), start=1):
        scored = sorted(
            [
                (
                    fingerprint_similarity(frame.fingerprint, entry["visual_fingerprint"]),
                    entry,
                )
                for entry in player_entries
                if "roster:player:{}".format(entry["slot_index"]) not in used_roster_refs
            ],
            key=lambda row: (-row[0], row[1]["slot_index"]),
        )
        score, matched = scored[0] if scored else (0.0, None)
        reliable = matched is not None and score >= 0.68
        source_ref = "selected-four:{}".format(index)
        roster_ref = (
            "roster:player:{}".format(matched["slot_index"]) if reliable else None
        )
        rows.append(
            {
                "selection_order": index,
                "species": matched["species_text"] if reliable else None,
                "species_id": matched.get("species_id") if reliable else None,
                "roster_ref": roster_ref,
                "visual_identity": stable_visual_identity(source_ref, frame.fingerprint),
                "role": "lead_or_reserve_unknown",
                "confidence": round(score, 6),
                "knowledge": "observed" if reliable else "unknown",
                "source_candidate_id": frame.request.source_id,
                "source_frame_ordinal": frame.request.frame_ordinal,
                "source_pts": frame.request.pts,
                "evidence_path": frame.evidence_path,
                "resolution_rule_id": "selected_four.icon_fingerprint_to_player_roster.v1",
            }
        )
        if matched is not None:
            edges.append(
                ResolutionEdge(
                    edge_id="resolution-edge-{:04d}".format(len(edges) + 1),
                    source_ref=source_ref,
                    target_entity_id="roster:player:{}".format(matched["slot_index"]),
                    rule_id="selected_four.icon_fingerprint_to_player_roster.v1",
                    confidence=round(score, 6),
                    evidence=["dhash_and_hsv_histogram_similarity={:.6f}".format(score)],
                    provenance=[
                        {
                            "candidate_id": frame.request.source_id,
                            "frame_ordinal": frame.request.frame_ordinal,
                            "pts": frame.request.pts,
                        }
                    ],
                )
            )
        if reliable:
            used_roster_refs.add(roster_ref)
    payload = {
        "schema_version": "0.1.0",
        "checkpoint": "1G",
        "kind": "selected_four",
        "player_selected": rows,
        "lead_candidates": [],
        "reserve_candidates": [],
        "ordering_semantics": "observed_ui_order",
        "slot_semantics": "unknown",
    }
    return payload, edges


def _selection_marker(ocr: OcrObservation) -> Tuple[Optional[int], float]:
    texts = [
        (str(line.get("text", "")), float(line.get("confidence", ocr.confidence)))
        for line in ocr.lines
    ]
    texts.append((ocr.raw_text, ocr.confidence))
    candidates = []
    for text, confidence in texts:
        for match in SELECTION_MARKER.finditer(text):
            candidates.append((int(match.group(1).translate(FULLWIDTH_MARKERS)), confidence))
    orders = {order for order, _ in candidates}
    if len(orders) != 1:
        return None, 0.0
    order = next(iter(orders))
    return order, round(max(confidence for value, confidence in candidates if value == order), 6)


def _parse_marker_selected_four(
    selected_frames: Sequence[ExtractedVisualFrame],
    roster: Mapping[str, Any],
    ocr_by_request: Mapping[str, OcrObservation],
) -> Tuple[Dict[str, Any], List[ResolutionEdge]]:
    player_entries = {
        int(row["slot_index"]): row
        for row in roster["entries"]
        if row["side"] == "player"
    }
    observations = []
    for frame in sorted(selected_frames, key=lambda row: row.request.slot):
        roster_row = int(str(frame.request.slot).replace("slot", ""))
        ocr = ocr_by_request.get(frame.request.request_id)
        selection_order, marker_confidence = (
            _selection_marker(ocr) if ocr is not None else (None, 0.0)
        )
        observations.append(
            {
                "frame": frame,
                "roster_row": roster_row,
                "selection_order": selection_order,
                "marker_confidence": marker_confidence,
                "marker_raw_text": ocr.raw_text if ocr is not None else "",
            }
        )

    marker_counts: Dict[int, int] = {}
    for observation in observations:
        order = observation["selection_order"]
        if order is not None:
            marker_counts[order] = marker_counts.get(order, 0) + 1

    rows = []
    edges: List[ResolutionEdge] = []
    row_observations = []
    for observation in observations:
        frame = observation["frame"]
        roster_row = observation["roster_row"]
        order = observation["selection_order"]
        duplicate = order is not None and marker_counts[order] > 1
        marker_status = (
            "ambiguous_duplicate"
            if duplicate
            else ("observed" if order is not None else "not_observed")
        )
        row_observations.append(
            {
                "roster_row": roster_row,
                "selection_order": None if duplicate else order,
                "marker_status": marker_status,
                "marker_raw_text": observation["marker_raw_text"],
                "marker_confidence": observation["marker_confidence"],
                "source_candidate_id": frame.request.source_id,
                "source_frame_ordinal": frame.request.frame_ordinal,
                "source_pts": frame.request.pts,
                "evidence_path": frame.evidence_path,
            }
        )
        if order is None or duplicate:
            continue
        matched = player_entries.get(roster_row)
        source_ref = "selected-four:row{}".format(roster_row)
        roster_ref = "roster:player:{}".format(roster_row) if matched else None
        confidence = float(observation["marker_confidence"])
        rows.append(
            {
                "selection_order": order,
                "roster_row": roster_row,
                "species": matched.get("species_text") if matched else None,
                "species_id": matched.get("species_id") if matched else None,
                "roster_ref": roster_ref,
                "visual_identity": stable_visual_identity(source_ref, frame.fingerprint),
                "role": "lead_or_reserve_unknown",
                "confidence": round(confidence, 6),
                "knowledge": "observed" if matched else "unknown",
                "source_candidate_id": frame.request.source_id,
                "source_frame_ordinal": frame.request.frame_ordinal,
                "source_pts": frame.request.pts,
                "evidence_path": frame.evidence_path,
                "marker_raw_text": observation["marker_raw_text"],
                "resolution_rule_id": "selected_four.marker_and_roster_row_alignment.v2",
            }
        )
        if matched:
            edges.append(
                ResolutionEdge(
                    edge_id="resolution-edge-{:04d}".format(len(edges) + 1),
                    source_ref=source_ref,
                    target_entity_id=roster_ref,
                    rule_id="selected_four.marker_and_roster_row_alignment.v2",
                    confidence=round(confidence, 6),
                    evidence=[
                        "selection_marker={}".format(order),
                        "player_roster_row={}".format(roster_row),
                        "marker_ocr_confidence={:.6f}".format(confidence),
                    ],
                    provenance=[
                        {
                            "candidate_id": frame.request.source_id,
                            "frame_ordinal": frame.request.frame_ordinal,
                            "pts": frame.request.pts,
                        }
                    ],
                )
            )

    rows.sort(key=lambda row: row["selection_order"])
    observed_orders = {row["selection_order"] for row in rows}
    complete = observed_orders == {1, 2, 3, 4}
    payload = {
        "schema_version": "0.1.0",
        "checkpoint": "1G",
        "kind": "selected_four",
        "source_candidate_count": len({row.request.source_id for row in selected_frames}),
        "player_selected": rows,
        "row_observations": row_observations,
        "lead_candidates": [],
        "reserve_candidates": [],
        "ordering_semantics": "observed_ui_order" if complete else "unknown",
        "slot_semantics": "player_roster_row",
        "selection_complete": complete,
        "missing_marker_orders": sorted({1, 2, 3, 4} - observed_orders),
        "duplicate_marker_orders": sorted(
            order for order, count in marker_counts.items() if count > 1
        ),
    }
    return payload, edges


def parse_selected_four(
    selected_frames: Sequence[ExtractedVisualFrame],
    roster: Mapping[str, Any],
    ocr_by_request: Optional[Mapping[str, OcrObservation]] = None,
) -> Tuple[Dict[str, Any], List[ResolutionEdge]]:
    ocr_by_request = ocr_by_request or {}
    if not any(row.request.request_id in ocr_by_request for row in selected_frames):
        return _parse_legacy_selected_four(selected_frames, roster)
    return _parse_marker_selected_four(selected_frames, roster, ocr_by_request)
