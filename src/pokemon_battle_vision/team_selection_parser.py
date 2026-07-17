"""Team Preview 與 Selected Four 的 partial、可追溯解析。"""

import re
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .checkpoint1g_models import ExtractedVisualFrame, OcrObservation, ResolutionEdge
from .visual_identity import fingerprint_similarity, stable_visual_identity


CJK_TEXT = re.compile(r"[\u3400-\u9fff]{2,}")


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


def parse_selected_four(
    selected_frames: Sequence[ExtractedVisualFrame],
    roster: Mapping[str, Any],
) -> Tuple[Dict[str, Any], List[ResolutionEdge]]:
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
