"""Checkpoint 1E Human Review cards、索引與 contact sheets。"""

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from .battle_timeline_models import (
    RelationEdge,
    TimelineGroup,
    human_review_defaults,
)
from .utils import sha256_file, write_json


BACKGROUND = (17, 22, 31)
PANEL = (31, 41, 56)
WHITE = (242, 246, 252)
MUTED = (157, 172, 191)
GREEN = (73, 199, 137)
AMBER = (242, 181, 72)
RED = (235, 103, 112)
FONT_PATHS = (
    Path("/System/Library/Fonts/STHeiti Medium.ttc"),
    Path("/System/Library/Fonts/STHeiti Light.ttc"),
)


def _font(size: int):
    for path in FONT_PATHS:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _status_color(status: str):
    return {
        "auto_accepted": GREEN,
        "needs_review": AMBER,
        "unlinked": RED,
    }.get(status, WHITE)


def _wrapped_lines(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> List[str]:
    lines: List[str] = []
    for paragraph in str(text or "").splitlines() or [""]:
        current = ""
        for character in paragraph:
            trial = current + character
            if current and draw.textlength(trial, font=font) > width:
                lines.append(current)
                current = character
            else:
                current = trial
        lines.append(current)
    return lines


def _draw_wrapped(
    draw: ImageDraw.ImageDraw,
    xy: Tuple[int, int],
    text: str,
    font,
    fill,
    width: int,
    line_height: int,
    max_lines: int,
) -> int:
    lines = _wrapped_lines(draw, text, font, width)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = (lines[-1][:-1] + "…") if lines[-1] else "…"
    x, y = xy
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height
    return y


def _event_summary(event: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "event_id": event["id"],
        "event_type": event["event_type"],
        "start_time": event["start_time"],
        "end_time": event["end_time"],
        "confidence": event["confidence"],
        "raw_text": event["raw_text"],
        "metadata": event["metadata"],
        "candidate_id": event["candidate_id"],
    }


def render_group_card(
    output_path: Path,
    group: TimelineGroup,
    events: Sequence[Mapping[str, Any]],
    relations: Sequence[RelationEdge],
) -> None:
    width = 1800
    height = 250 + max(1, len(events)) * 165 + max(1, len(relations)) * 110
    canvas = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    draw.text(
        (40, 28),
        "{}  {}".format(group.timeline_id, group.group_type),
        font=_font(38),
        fill=WHITE,
    )
    draw.text(
        (40, 80),
        "{}  confidence {:.3f}  PTS {:.3f}–{:.3f}".format(
            group.review_status, group.confidence, group.start_time, group.end_time
        ),
        font=_font(27),
        fill=_status_color(group.review_status),
    )
    draw.text(
        (40, 128),
        "primary={}  events={}  relations={}".format(
            group.primary_event_id, len(events), len(relations)
        ),
        font=_font(23),
        fill=MUTED,
    )
    _draw_wrapped(
        draw,
        (40, 170),
        "review reasons: {}".format(", ".join(group.review_reasons) or "none"),
        _font(20),
        MUTED,
        width - 80,
        28,
        2,
    )

    top = 235
    for event in events:
        draw.rectangle((24, top, width - 24, top + 145), fill=PANEL)
        draw.text(
            (44, top + 15),
            "{}  {}  {:.3f}–{:.3f}  conf {:.3f}".format(
                event["id"],
                event["event_type"],
                float(event["start_time"]),
                float(event["end_time"]),
                float(event["confidence"]),
            ),
            font=_font(23),
            fill=WHITE,
        )
        _draw_wrapped(
            draw,
            (44, top + 53),
            str(event["raw_text"]).replace("\n", " / "),
            _font(22),
            GREEN,
            700,
            28,
            2,
        )
        metadata = json.dumps(event["metadata"], ensure_ascii=False, sort_keys=True)
        _draw_wrapped(
            draw,
            (800, top + 53),
            metadata,
            _font(19),
            MUTED,
            940,
            26,
            3,
        )
        top += 165

    for relation in relations:
        draw.rectangle((24, top, width - 24, top + 92), fill=(24, 33, 46))
        draw.text(
            (44, top + 12),
            "{}  {}  {:.3f}  {}".format(
                relation.relation_id,
                relation.relation_type,
                relation.confidence,
                relation.review_status,
            ),
            font=_font(21),
            fill=_status_color(relation.review_status),
        )
        _draw_wrapped(
            draw,
            (44, top + 47),
            "{} → {}  rule={}  evidence={}".format(
                relation.from_event_id,
                relation.to_event_id,
                relation.rule_id,
                ", ".join(relation.evidence),
            ),
            _font(17),
            MUTED,
            width - 88,
            23,
            2,
        )
        top += 110
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="JPEG", quality=91, optimize=True)


def render_relation_card(
    output_path: Path,
    relation: RelationEdge,
    source: Mapping[str, Any],
    target: Mapping[str, Any],
) -> None:
    canvas = Image.new("RGB", (1600, 700), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    draw.text(
        (40, 28),
        "{}  {}  NEEDS REVIEW".format(relation.relation_id, relation.relation_type),
        font=_font(36),
        fill=AMBER,
    )
    draw.text(
        (40, 82),
        "confidence {:.3f}  rule={}".format(relation.confidence, relation.rule_id),
        font=_font(25),
        fill=MUTED,
    )
    for x, label, event in ((40, "FROM", source), (820, "TO", target)):
        draw.rectangle((x, 140, x + 740, 500), fill=PANEL)
        draw.text(
            (x + 20, 160),
            "{}  {}  {}".format(label, event["id"], event["event_type"]),
            font=_font(26),
            fill=WHITE,
        )
        draw.text(
            (x + 20, 205),
            "PTS {:.3f}–{:.3f}".format(event["start_time"], event["end_time"]),
            font=_font(21),
            fill=MUTED,
        )
        _draw_wrapped(
            draw,
            (x + 20, 250),
            str(event["raw_text"]).replace("\n", " / "),
            _font(25),
            GREEN,
            690,
            34,
            3,
        )
        _draw_wrapped(
            draw,
            (x + 20, 375),
            json.dumps(event["metadata"], ensure_ascii=False, sort_keys=True),
            _font(18),
            MUTED,
            690,
            25,
            4,
        )
    _draw_wrapped(
        draw,
        (40, 535),
        "evidence: {}".format(", ".join(relation.evidence)),
        _font(21),
        MUTED,
        1520,
        30,
        4,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="JPEG", quality=91, optimize=True)


def _contact_tile(record: Mapping[str, Any], id_key: str) -> Image.Image:
    tile = Image.new("RGB", (430, 360), PANEL)
    draw = ImageDraw.Draw(tile)
    status = str(record.get("review_status", ""))
    draw.text((14, 14), str(record[id_key]), font=_font(23), fill=WHITE)
    draw.text(
        (14, 48),
        status or str(record.get("relation_type", "")),
        font=_font(19),
        fill=_status_color(status),
    )
    if id_key == "timeline_id":
        draw.text(
            (14, 78),
            "{}  {:.2f}–{:.2f}  conf {:.2f}".format(
                record["group_type"],
                float(record["start_time"]),
                float(record["end_time"]),
                float(record["confidence"]),
            ),
            font=_font(16),
            fill=MUTED,
        )
        draw.text(
            (14, 106),
            "primary {}".format(record["primary_event_id"]),
            font=_font(16),
            fill=MUTED,
        )
        y = 140
        for event in list(record.get("source_events", []))[:3]:
            y = _draw_wrapped(
                draw,
                (14, y),
                "{}  {}".format(
                    event["event_type"], str(event["raw_text"]).replace("\n", " / ")
                ),
                _font(18),
                GREEN,
                402,
                25,
                2,
            )
            y += 8
    else:
        draw.text(
            (14, 78),
            "{}  conf {:.2f}".format(
                record["relation_type"], float(record["confidence"])
            ),
            font=_font(18),
            fill=AMBER,
        )
        source = record["from_event"]
        target = record["to_event"]
        _draw_wrapped(
            draw,
            (14, 115),
            "FROM {} {}: {}".format(
                source["event_id"],
                source["event_type"],
                str(source["raw_text"]).replace("\n", " / "),
            ),
            _font(18),
            WHITE,
            402,
            25,
            4,
        )
        _draw_wrapped(
            draw,
            (14, 230),
            "TO {} {}: {}".format(
                target["event_id"],
                target["event_type"],
                str(target["raw_text"]).replace("\n", " / "),
            ),
            _font(18),
            GREEN,
            402,
            25,
            4,
        )
    return tile


def _build_contact_pages(
    review_staging: Path,
    category: str,
    rows: Sequence[Mapping[str, Any]],
    id_key: str,
    card_key: str,
) -> Tuple[int, List[Dict[str, Any]]]:
    page_count = int(math.ceil(len(rows) / 12.0)) if rows else 0
    index_rows: List[Dict[str, Any]] = []
    for page_number in range(page_count):
        selected = rows[page_number * 12 : (page_number + 1) * 12]
        page = Image.new("RGB", (1800, 1320), BACKGROUND)
        draw = ImageDraw.Draw(page)
        draw.text(
            (40, 22),
            "{}  page {}/{}".format(category, page_number + 1, page_count),
            font=_font(31),
            fill=WHITE,
        )
        relative_page = "contact_sheets/{}/page-{:03d}.jpg".format(
            category, page_number + 1
        )
        for tile_index, row in enumerate(selected):
            x = 20 + (tile_index % 4) * 445
            y = 80 + (tile_index // 4) * 405
            page.paste(_contact_tile(row, id_key), (x, y))
            index_rows.append(
                {
                    "category": category,
                    "record_id": row[id_key],
                    "page": relative_page,
                    "page_number": page_number + 1,
                    "tile_index": tile_index,
                    "review_card_path": row[card_key],
                }
            )
        page_path = review_staging / relative_page
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page.save(page_path, format="JPEG", quality=90, optimize=True)
    return page_count, index_rows


def build_timeline_review_pack(
    review_staging: Path,
    groups: Sequence[TimelineGroup],
    relations: Sequence[RelationEdge],
    events: Sequence[Mapping[str, Any]],
    timeline_sha256: str,
) -> Dict[str, Any]:
    event_by_id = {str(event["id"]): event for event in events}
    relation_by_id = {edge.relation_id: edge for edge in relations}
    group_records: List[Dict[str, Any]] = []
    for group in groups:
        card_path = "cards/groups/{}.jpg".format(group.timeline_id)
        group_events = [event_by_id[event_id] for event_id in group.event_ids]
        group_relations = [relation_by_id[edge_id] for edge_id in group.relation_edge_ids]
        render_group_card(
            review_staging / card_path, group, group_events, group_relations
        )
        record = group.to_dict()
        record.update(
            {
                "source_events": [_event_summary(event) for event in group_events],
                "relations": [edge.to_dict() for edge in group_relations],
                "review_card_path": card_path,
            }
        )
        record.update(human_review_defaults())
        group_records.append(record)

    relation_records: List[Dict[str, Any]] = []
    for edge in relations:
        if edge.review_status != "needs_review":
            continue
        card_path = "cards/relations/{}.jpg".format(edge.relation_id)
        source = event_by_id[edge.from_event_id]
        target = event_by_id[edge.to_event_id]
        render_relation_card(review_staging / card_path, edge, source, target)
        record = edge.to_dict()
        record.update(
            {
                "from_event": _event_summary(source),
                "to_event": _event_summary(target),
                "review_card_path": card_path,
            }
        )
        record.update(human_review_defaults())
        relation_records.append(record)

    unlinked_records = []
    for group in groups:
        if group.review_status != "unlinked":
            continue
        for event_id in group.event_ids:
            unlinked_records.append(
                {
                    "event_id": event_id,
                    "timeline_id": group.timeline_id,
                    "review_card_path": "cards/groups/{}.jpg".format(group.timeline_id),
                    "event": _event_summary(event_by_id[event_id]),
                }
            )

    group_payload = {
        "schema_version": "0.1.0",
        "checkpoint": "1E",
        "kind": "timeline_group_reviews",
        "record_count": len(group_records),
        "records": group_records,
    }
    relation_payload = {
        "schema_version": "0.1.0",
        "checkpoint": "1E",
        "kind": "needs_review_relations",
        "record_count": len(relation_records),
        "records": relation_records,
    }
    unlinked_payload = {
        "schema_version": "0.1.0",
        "checkpoint": "1E",
        "kind": "unlinked_events",
        "record_count": len(unlinked_records),
        "records": unlinked_records,
    }
    write_json(review_staging / "group_reviews.json", group_payload)
    write_json(review_staging / "needs_review_relations.json", relation_payload)
    write_json(review_staging / "unlinked_events.json", unlinked_payload)

    group_pages, group_index = _build_contact_pages(
        review_staging,
        "groups",
        group_records,
        "timeline_id",
        "review_card_path",
    )
    relation_pages, relation_index = _build_contact_pages(
        review_staging,
        "needs_review",
        relation_records,
        "relation_id",
        "review_card_path",
    )
    contact_index = {
        "schema_version": "0.1.0",
        "checkpoint": "1E",
        "kind": "timeline_contact_sheet_index",
        "page_counts": {
            "groups": group_pages,
            "needs_review": relation_pages,
        },
        "total_page_count": group_pages + relation_pages,
        "tile_count": len(group_index) + len(relation_index),
        "rows": group_index + relation_index,
    }
    write_json(review_staging / "contact_sheets/contact_sheet_index.json", contact_index)

    manifest = {
        "schema_version": "0.1.0",
        "checkpoint": "1E",
        "kind": "checkpoint1e_review_manifest",
        "source": {
            "timeline_path": "outputs/checkpoint-1e/battle_timeline.json",
            "timeline_sha256": timeline_sha256,
        },
        "group_count": len(groups),
        "group_review_count": len(group_records),
        "needs_review_relation_count": len(relation_records),
        "unlinked_event_count": len(unlinked_records),
        "outputs": {
            "group_reviews": {
                "path": "group_reviews.json",
                "sha256": sha256_file(review_staging / "group_reviews.json"),
            },
            "needs_review_relations": {
                "path": "needs_review_relations.json",
                "sha256": sha256_file(review_staging / "needs_review_relations.json"),
            },
            "unlinked_events": {
                "path": "unlinked_events.json",
                "sha256": sha256_file(review_staging / "unlinked_events.json"),
            },
            "contact_sheet_index": {
                "path": "contact_sheets/contact_sheet_index.json",
                "sha256": sha256_file(
                    review_staging / "contact_sheets/contact_sheet_index.json"
                ),
            },
        },
        "contact_sheets": {
            "group_page_count": group_pages,
            "needs_review_page_count": relation_pages,
            "total_page_count": group_pages + relation_pages,
            "tile_count": len(group_index) + len(relation_index),
        },
        "validation": {
            "all_groups_have_cards": all(
                (review_staging / record["review_card_path"]).is_file()
                for record in group_records
            ),
            "all_needs_review_relations_have_cards": all(
                (review_staging / record["review_card_path"]).is_file()
                for record in relation_records
            ),
            "all_human_fields_null": all(
                all(record[key] is None for key in human_review_defaults())
                for record in group_records + relation_records
            ),
            "contact_sheet_traceable": len(group_index) == len(group_records)
            and len(relation_index) == len(relation_records),
        },
    }
    write_json(review_staging / "review_manifest.json", manifest)
    return manifest
