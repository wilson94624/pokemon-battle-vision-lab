"""Status UI observation、active slot track 與 HP change 建構。"""

import re
from collections import defaultdict
from typing import Any, DefaultDict, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from .checkpoint1g_models import ExtractedVisualFrame, OcrObservation
from .hp_status_tracker import (
    classify_hp_change,
    normalize_bar_estimates,
    parse_exact_hp,
    parse_percentage,
    parse_status,
)
from .visual_identity import fingerprint_similarity, stable_visual_identity


CJK_NAME = re.compile(r"[\u3400-\u9fff]{2,8}")


def _identity_text(raw_text: str) -> Optional[str]:
    cleaned = re.sub(r"\d+\s*[/／%％]\s*\d*", " ", raw_text)
    for status in ("灼傷", "中毒", "劇毒", "麻痺", "睡眠", "冰凍"):
        cleaned = cleaned.replace(status, " ")
    candidates = CJK_NAME.findall(cleaned)
    return min(candidates, key=len) if candidates else None


def build_hp_observations(
    status_frames: Sequence[ExtractedVisualFrame],
    ocr_by_request: Mapping[str, OcrObservation],
    knowledge_base=None,
) -> Dict[str, Any]:
    raw_rows: List[Dict[str, Any]] = []
    for frame in sorted(
        status_frames,
        key=lambda row: (row.request.pts, str(row.request.side), str(row.request.slot)),
    ):
        request = frame.request
        ocr = ocr_by_request.get(request.request_id)
        raw_text = ocr.raw_text if ocr else ""
        identity_text = _identity_text(raw_text)
        species_candidates = (
            knowledge_base.resolve_species(identity_text, limit=5)
            if knowledge_base is not None and identity_text
            else []
        )
        resolved = species_candidates[0] if len(species_candidates) == 1 else None
        exact = parse_exact_hp(raw_text)
        percentage = parse_percentage(raw_text)
        if exact:
            current, maximum, hp_percent = exact
            value_type = "exact_numeric"
        elif percentage is not None:
            current, maximum, hp_percent = None, None, percentage
            value_type = "ocr_percentage"
        else:
            current, maximum, hp_percent = None, None, None
            value_type = "visual_bar_estimate"
        raw_rows.append(
            {
                "side": request.side,
                "slot": request.slot,
                "timestamp": request.pts,
                "frame_ordinal": request.frame_ordinal,
                "identity_text": identity_text,
                "species_id": int(resolved["canonical_species_id"]) if resolved else None,
                "canonical_species_name": resolved["traditional_chinese_name"] if resolved else None,
                "species_candidates": species_candidates,
                "visual_identity": stable_visual_identity(
                    "status-{}-{}".format(request.side, request.slot), frame.fingerprint
                ),
                "visual_fingerprint": frame.fingerprint,
                "current_hp": current,
                "max_hp": maximum,
                "hp_percent": hp_percent,
                "value_type": value_type,
                "status": parse_status(raw_text),
                "bar_measurement": frame.bar_measurement or {},
                "raw_ocr": raw_text,
                "ocr_confidence": ocr.confidence if ocr else 0.0,
                "ocr_error": ocr.error if ocr else None,
                "source_roi": request.roi_name,
                "evidence_path": frame.evidence_path,
                "rule_ids": [
                    "hp.ocr.exact_or_percentage.v1",
                    "visual.hp_bar.longest_health_color_run.v1",
                ],
            }
        )
    normalize_bar_estimates(raw_rows)
    for row in raw_rows:
        if row["hp_percent"] is None and row["visual_bar_percent"] is not None:
            row["hp_percent"] = row["visual_bar_percent"]
            row["value_type"] = "visual_bar_estimate"
        if row["hp_percent"] is None:
            row["value_type"] = "unknown"

    by_slot: DefaultDict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in raw_rows:
        by_slot[(str(row["side"]), str(row["slot"]))].append(row)
    observations: List[Dict[str, Any]] = []
    track_counter = 0
    for key in sorted(by_slot):
        prior: Optional[Dict[str, Any]] = None
        track_id = None
        for row in by_slot[key]:
            if row["value_type"] == "unknown":
                continue
            identity_changed = bool(
                prior
                and (
                    (
                        row.get("species_id")
                        and prior.get("species_id")
                        and row["species_id"] != prior["species_id"]
                    )
                    or (
                        row["identity_text"]
                        and prior["identity_text"]
                        and row["identity_text"] != prior["identity_text"]
                    )
                )
            )
            if prior and not row["identity_text"] and not prior["identity_text"]:
                identity_changed = (
                    fingerprint_similarity(
                        row["visual_fingerprint"], prior["visual_fingerprint"]
                    )
                    < 0.60
                )
            if track_id is None or identity_changed:
                track_counter += 1
                track_id = "status-track-{:04d}".format(track_counter)
            meaningful = prior is None or identity_changed
            if prior is not None:
                meaningful = meaningful or (
                    row["current_hp"] is not None
                    and row["current_hp"] != prior["current_hp"]
                )
                meaningful = meaningful or row["status"] != prior["status"]
                if row["hp_percent"] is not None and prior["hp_percent"] is not None:
                    meaningful = meaningful or abs(
                        float(row["hp_percent"]) - float(prior["hp_percent"])
                    ) >= (2.0 if row["value_type"] != "visual_bar_estimate" else 5.0)
            if meaningful:
                confidence = 0.0
                if row["value_type"] == "exact_numeric":
                    confidence = max(0.8, row["ocr_confidence"])
                elif row["value_type"] == "ocr_percentage":
                    confidence = max(0.72, row["ocr_confidence"])
                elif row["value_type"] == "visual_bar_estimate":
                    confidence = 0.55 * float(row["bar_measurement"].get("quality", 0.0))
                observation = dict(row)
                observation.update(
                    {
                        "observation_id": "hp-observation-{:05d}".format(len(observations) + 1),
                        "track_id": track_id,
                        "confidence": round(min(1.0, confidence), 6),
                        "knowledge": (
                            "observed_and_knowledge_base_resolved"
                            if row.get("species_id") is not None
                            else ("observed" if row["hp_percent"] is not None else "unknown")
                        ),
                        "review_status": (
                            "auto_accepted" if confidence >= 0.75 else "observation_only"
                        ),
                    }
                )
                observation["visual_identity"] = "visual:{}".format(track_id)
                observations.append(observation)
                prior = row
            elif prior is None:
                prior = row
    observations.sort(
        key=lambda row: (float(row["timestamp"]), str(row["side"]), str(row["slot"]), str(row["track_id"]))
    )
    for index, observation in enumerate(observations, start=1):
        observation["observation_id"] = "hp-observation-{:05d}".format(index)
    return {
        "schema_version": "0.1.0",
        "checkpoint": "1G",
        "kind": "hp_observations",
        "sampling_interval_sec": 0.5,
        "raw_sample_count": len(raw_rows),
        "observation_count": len(observations),
        "coverage": {
            "start_time": min((row["timestamp"] for row in raw_rows), default=None),
            "end_time": max((row["timestamp"] for row in raw_rows), default=None),
            "side_slots": sorted("{}:{}".format(*key) for key in by_slot),
        },
        "observations": observations,
    }


def build_hp_changes(hp_payload: Mapping[str, Any], timeline_groups: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    by_track: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in hp_payload["observations"]:
        by_track[str(row["track_id"])].append(row)
    changes = []
    for track_id in sorted(by_track):
        rows = sorted(by_track[track_id], key=lambda row: row["timestamp"])
        for before, after in zip(rows, rows[1:]):
            if before.get("hp_percent") is None or after.get("hp_percent") is None:
                continue
            delta = round(float(after["hp_percent"]) - float(before["hp_percent"]), 3)
            if abs(delta) < 0.5:
                continue
            time_gap = float(after["timestamp"]) - float(before["timestamp"])
            if time_gap > 3.0:
                continue
            if (
                before.get("value_type") == "visual_bar_estimate"
                or after.get("value_type") == "visual_bar_estimate"
            ):
                if abs(delta) < 8.0:
                    continue
                if min(
                    float(before.get("bar_measurement", {}).get("quality", 0.0)),
                    float(after.get("bar_measurement", {}).get("quality", 0.0)),
                ) < 0.65:
                    continue
            midpoint = (float(before["timestamp"]) + float(after["timestamp"])) / 2.0
            near = [
                group
                for group in timeline_groups
                if float(group["start_time"]) - 2.0 <= midpoint <= float(group["end_time"]) + 2.0
            ]
            changes.append(
                {
                    "change_id": "hp-change-{:05d}".format(len(changes) + 1),
                    "pokemon_entity_id": None,
                    "identity_text": after.get("identity_text") or before.get("identity_text"),
                    "side": after["side"],
                    "slot": after["slot"],
                    "timestamp": after["timestamp"],
                    "before_hp": before.get("current_hp"),
                    "after_hp": after.get("current_hp"),
                    "delta_hp": (
                        after["current_hp"] - before["current_hp"]
                        if before.get("current_hp") is not None
                        and after.get("current_hp") is not None
                        and before.get("max_hp") == after.get("max_hp")
                        else None
                    ),
                    "before_percent": before["hp_percent"],
                    "after_percent": after["hp_percent"],
                    "delta_percent": delta,
                    "change_type": classify_hp_change(before, after),
                    "cause": "unknown",
                    "source_observation_ids": [before["observation_id"], after["observation_id"]],
                    "linked_timeline_ids": [str(row["timeline_id"]) for row in near],
                    "confidence": round(min(float(before["confidence"]), float(after["confidence"])), 6),
                    "rule_id": "hp.change.consecutive_stable_observations.v1",
                }
            )
    return {
        "schema_version": "0.1.0",
        "checkpoint": "1G",
        "kind": "hp_changes",
        "change_count": len(changes),
        "changes": changes,
    }


def build_active_slot_timeline(hp_payload: Mapping[str, Any]) -> Dict[str, Any]:
    entries = []
    last_by_slot: Dict[Tuple[str, str], str] = {}
    for observation in sorted(hp_payload["observations"], key=lambda row: row["timestamp"]):
        identity = observation.get("identity_text") or observation.get("visual_identity")
        key = (str(observation["side"]), str(observation["slot"]))
        if not identity or last_by_slot.get(key) == identity:
            continue
        entries.append(
            {
                "active_slot_entry_id": "active-slot-{:04d}".format(len(entries) + 1),
                "timestamp": observation["timestamp"],
                "frame_ordinal": observation["frame_ordinal"],
                "side": observation["side"],
                "slot": observation["slot"],
                "pokemon_entity_id": None,
                "identity_text": observation.get("identity_text"),
                "species_id": observation.get("species_id"),
                "visual_identity": observation.get("visual_identity"),
                "action": "set_active",
                "confidence": observation["confidence"],
                "knowledge": "observed",
                "source_observation_ids": [observation["observation_id"]],
                "rule_id": "active_slot.status_identity_change.v1",
            }
        )
        last_by_slot[key] = str(identity)
    return {
        "schema_version": "0.1.0",
        "checkpoint": "1G",
        "kind": "active_slot_timeline",
        "entry_count": len(entries),
        "entries": entries,
    }
