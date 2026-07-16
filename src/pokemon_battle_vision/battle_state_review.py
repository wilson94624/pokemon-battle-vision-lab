"""Checkpoint 1F State Before → Event → Delta → State After Review Pack。"""

import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from .battle_state_models import STATE_VERSION, human_review_defaults
from .utils import sha256_file, write_json


BACKGROUND = (16, 22, 31)
PANEL = (29, 39, 54)
PANEL_ALT = (35, 47, 63)
WHITE = (242, 246, 252)
MUTED = (159, 174, 193)
GREEN = (79, 205, 145)
AMBER = (244, 185, 73)
RED = (236, 105, 115)
BLUE = (91, 160, 237)
CARDS_PER_PAGE = 12


def _font(size: int):
    candidates = (
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/STHeiti Medium.ttc"),
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    )
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> List[str]:
    result: List[str] = []
    for paragraph in str(text).splitlines() or [""]:
        current = ""
        for char in paragraph:
            trial = current + char
            if current and draw.textlength(trial, font=font) > width:
                result.append(current)
                current = char
            else:
                current = trial
        result.append(current)
    return result


def _text_block(
    draw: ImageDraw.ImageDraw,
    xy: Tuple[int, int],
    text: str,
    font,
    fill,
    width: int,
    max_lines: int,
    spacing: int = 6,
) -> int:
    x, y = xy
    lines = _wrap(draw, text, font, width)[:max_lines]
    line_height = int(getattr(font, "size", 18) * 1.25)
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height + spacing
    return y


def _known_value(field: Mapping[str, Any]) -> str:
    knowledge = field.get("knowledge", "unknown")
    if knowledge != "known":
        return knowledge
    return str(field.get("value"))


def _state_summary(snapshot: Mapping[str, Any]) -> List[str]:
    battle = snapshot["battle"]
    field = snapshot["field"]
    player = snapshot["player_side"]
    opponent = snapshot["opponent_side"]
    return [
        "confidence={:.3f}".format(snapshot["confidence"]),
        "completeness={:.3f}".format(snapshot["completeness"]),
        "weather={}".format(_known_value(field["weather"])),
        "battle.result={}".format(_known_value(battle["result"])),
        "player known={} observed active={}".format(
            len(player["known_pokemon"]), player["active"].get("value") or []
        ),
        "opponent known={} observed active={}".format(
            len(opponent["known_pokemon"]), opponent["active"].get("value") or []
        ),
        "unassigned={}".format(len(battle["unassigned_pokemon"])),
        "conflicts={} unresolved={}".format(
            len(snapshot["conflict_ids"]), len(snapshot["unresolved_update_ids"])
        ),
    ]


def _operation_lines(delta: Mapping[str, Any]) -> List[str]:
    lines = []
    for operation in delta["operations"]:
        lines.append(
            "{} {} → {}".format(
                operation["operation"], operation["entity"], operation["field"]
            )
        )
    if not lines:
        lines.extend(delta["no_op_reasons"] or ["NO OPERATION"])
    for unresolved in delta["unresolved_updates"]:
        lines.append("UNRESOLVED {}".format(unresolved["reason"]))
    for conflict_id in delta["conflict_ids"]:
        lines.append("CONFLICT {}".format(conflict_id))
    return lines


def _render_card(
    path: Path,
    record: Mapping[str, Any],
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    delta: Mapping[str, Any],
) -> None:
    image = Image.new("RGB", (1600, 1000), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.text((42, 28), record["timeline_id"], font=_font(38), fill=WHITE)
    draw.text(
        (360, 36),
        "{} | {} → {} | {:.3f}".format(
            delta["status"],
            delta["snapshot_before"],
            delta["snapshot_after"],
            delta["confidence"],
        ),
        font=_font(24),
        fill=GREEN if delta["review_status"] == "auto_accepted" else AMBER,
    )
    draw.text(
        (42, 82),
        "PTS {:.6f} | events {} | review {}".format(
            delta["timestamp"], ", ".join(delta["source_event_ids"]), delta["review_status"]
        ),
        font=_font(20),
        fill=MUTED,
    )

    panels = (
        (30, 130, 400, 790, "STATE BEFORE", BLUE),
        (420, 130, 790, 790, "APPLIED EVENT", AMBER),
        (810, 130, 1180, 790, "PROPOSED DELTA", GREEN),
        (1200, 130, 1570, 790, "STATE AFTER", BLUE),
    )
    for x1, y1, x2, y2, title, color in panels:
        draw.rounded_rectangle((x1, y1, x2, y2), radius=18, fill=PANEL)
        draw.text((x1 + 20, y1 + 18), title, font=_font(24), fill=color)

    y = 190
    for line in _state_summary(before):
        y = _text_block(draw, (50, y), line, _font(18), WHITE, 330, 2)
    y = 190
    for event in record["events"]:
        y = _text_block(
            draw,
            (440, y),
            "{} {}\n{}\nmetadata={}".format(
                event["event_id"], event["event_type"], event["raw_text"], event["metadata"]
            ),
            _font(17),
            WHITE,
            330,
            9,
        ) + 14
    y = 190
    for line in _operation_lines(delta):
        y = _text_block(draw, (830, y), line, _font(17), WHITE, 330, 3) + 7
        if y > 745:
            break
    y = 190
    for line in _state_summary(after):
        y = _text_block(draw, (1220, y), line, _font(18), WHITE, 330, 2)

    draw.rounded_rectangle((30, 815, 1570, 960), radius=18, fill=PANEL_ALT)
    reasons = ", ".join(delta["review_reasons"]) or "none"
    excluded = ", ".join(delta["excluded_rejected_relation_ids"]) or "none"
    _text_block(
        draw,
        (50, 835),
        "Review reasons：{}\nExcluded rejected relations：{}\nHuman review：pending（所有欄位預設 null）".format(
            reasons, excluded
        ),
        _font(20),
        WHITE,
        1490,
        4,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(path), format="PNG", compress_level=6)


def _render_contact_pages(
    root: Path,
    category: str,
    records: Sequence[Mapping[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    pages = []
    tiles = []
    page_dir = root / "contact_sheets" / category
    page_dir.mkdir(parents=True, exist_ok=True)
    for page_index in range(int(math.ceil(len(records) / CARDS_PER_PAGE))):
        page_records = records[
            page_index * CARDS_PER_PAGE : (page_index + 1) * CARDS_PER_PAGE
        ]
        image = Image.new("RGB", (1600, 1120), BACKGROUND)
        draw = ImageDraw.Draw(image)
        draw.text(
            (34, 22),
            "Checkpoint 1F {} — page {:03d}".format(category, page_index + 1),
            font=_font(31),
            fill=WHITE,
        )
        for tile_index, record in enumerate(page_records):
            row, column = divmod(tile_index, 3)
            x = 30 + column * 520
            y = 82 + row * 250
            card = Image.open(root / record["review_card_path"]).convert("RGB")
            card.thumbnail((490, 205), Image.Resampling.LANCZOS)
            image.paste(card, (x, y))
            draw.text(
                (x, y + 208),
                "{} {} C={:.2f}".format(
                    record["timeline_id"], record["delta_status"], record["confidence"]
                ),
                font=_font(17),
                fill=AMBER if record["review_status"] == "needs_review" else GREEN,
            )
            tiles.append(
                {
                    "category": category,
                    "page": page_index + 1,
                    "tile": tile_index + 1,
                    "review_id": record["review_id"],
                    "timeline_id": record["timeline_id"],
                    "review_card_path": record["review_card_path"],
                }
            )
        relative = "contact_sheets/{}/page-{:03d}.png".format(
            category, page_index + 1
        )
        image.save(str(root / relative), format="PNG", compress_level=6)
        pages.append(
            {
                "category": category,
                "page": page_index + 1,
                "path": relative,
                "tile_count": len(page_records),
            }
        )
    return pages, tiles


def build_state_review_pack(
    review_staging: Path,
    snapshots: Sequence[Mapping[str, Any]],
    deltas: Sequence[Mapping[str, Any]],
    conflicts: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    snapshots_sha256: str,
    deltas_sha256: str,
) -> Dict[str, Any]:
    event_by_id = {str(event["id"]): event for event in events}
    snapshot_by_id = {str(item["snapshot_id"]): item for item in snapshots}
    records = []
    for index, delta in enumerate(deltas, 1):
        before = snapshot_by_id[delta["snapshot_before"]]
        after = snapshot_by_id[delta["snapshot_after"]]
        event_rows = [
            {
                "event_id": event_id,
                "event_type": event_by_id[event_id]["event_type"],
                "raw_text": event_by_id[event_id]["raw_text"],
                "metadata": event_by_id[event_id]["metadata"],
            }
            for event_id in delta["source_event_ids"]
        ]
        card_path = "snapshot_review_cards/{}/{}.png".format(
            delta["review_status"], delta["timeline_id"]
        )
        record = {
            "review_id": "state-review-{:04d}".format(index),
            "timeline_id": delta["timeline_id"],
            "snapshot_before": delta["snapshot_before"],
            "snapshot_after": delta["snapshot_after"],
            "source_event_ids": delta["source_event_ids"],
            "events": event_rows,
            "delta_id": delta["delta_id"],
            "delta_status": delta["status"],
            "operation_count": len(delta["operations"]),
            "unresolved_count": len(delta["unresolved_updates"]),
            "conflict_count": len(delta["conflict_ids"]),
            "confidence": delta["confidence"],
            "before_completeness": before["completeness"],
            "after_completeness": after["completeness"],
            "review_status": delta["review_status"],
            "review_reasons": delta["review_reasons"],
            "review_card_path": card_path,
            "human_review": human_review_defaults(),
        }
        _render_card(review_staging / card_path, record, before, after, delta)
        records.append(record)

    conflicts_by_id = {item["conflict_id"]: item for item in conflicts}
    record_by_timeline = {item["timeline_id"]: item for item in records}
    conflict_index = [
        {
            **conflict,
            "review_card_path": record_by_timeline[conflict["timeline_id"]][
                "review_card_path"
            ],
        }
        for conflict in conflicts
    ]
    unresolved_index = []
    for delta in deltas:
        for unresolved in delta["unresolved_updates"]:
            unresolved_index.append(
                {
                    **unresolved,
                    "review_card_path": record_by_timeline[delta["timeline_id"]][
                        "review_card_path"
                    ],
                }
            )
    low_completeness_index = [
        record
        for record in records
        if "low_completeness_important_snapshot" in record["review_reasons"]
    ]
    accepted_unlinked_index = [
        record
        for record in records
        if "accepted_unlinked_event" in record["review_reasons"]
    ]
    needs_review_records = [
        record for record in records if record["review_status"] == "needs_review"
    ]

    review_payload = {
        "schema_version": STATE_VERSION,
        "checkpoint": "1F",
        "kind": "state_review_records",
        "record_count": len(records),
        "records": records,
    }
    conflicts_payload = {
        "schema_version": STATE_VERSION,
        "checkpoint": "1F",
        "kind": "conflicts_review_index",
        "record_count": len(conflict_index),
        "records": conflict_index,
    }
    unresolved_payload = {
        "schema_version": STATE_VERSION,
        "checkpoint": "1F",
        "kind": "unresolved_updates_review_index",
        "record_count": len(unresolved_index),
        "records": unresolved_index,
    }
    low_payload = {
        "schema_version": STATE_VERSION,
        "checkpoint": "1F",
        "kind": "low_completeness_snapshots_index",
        "record_count": len(low_completeness_index),
        "records": low_completeness_index,
    }
    unlinked_payload = {
        "schema_version": STATE_VERSION,
        "checkpoint": "1F",
        "kind": "accepted_unlinked_events_index",
        "record_count": len(accepted_unlinked_index),
        "records": accepted_unlinked_index,
    }
    write_json(review_staging / "state_review_records.json", review_payload)
    write_json(review_staging / "conflicts_index.json", conflicts_payload)
    write_json(review_staging / "unresolved_updates_index.json", unresolved_payload)
    write_json(review_staging / "low_completeness_snapshots_index.json", low_payload)
    write_json(review_staging / "accepted_unlinked_events_index.json", unlinked_payload)

    all_pages, all_tiles = _render_contact_pages(review_staging, "all", records)
    needs_pages, needs_tiles = _render_contact_pages(
        review_staging, "needs_review", needs_review_records
    )
    contact_index = {
        "schema_version": STATE_VERSION,
        "checkpoint": "1F",
        "kind": "state_contact_sheet_index",
        "pages": all_pages + needs_pages,
        "tiles": all_tiles + needs_tiles,
    }
    write_json(review_staging / "contact_sheets/contact_sheet_index.json", contact_index)

    outputs = {}
    for key, relative in (
        ("state_review_records", "state_review_records.json"),
        ("conflicts_index", "conflicts_index.json"),
        ("unresolved_updates_index", "unresolved_updates_index.json"),
        ("low_completeness_snapshots_index", "low_completeness_snapshots_index.json"),
        ("accepted_unlinked_events_index", "accepted_unlinked_events_index.json"),
        ("contact_sheet_index", "contact_sheets/contact_sheet_index.json"),
    ):
        outputs[key] = {
            "path": relative,
            "sha256": sha256_file(review_staging / relative),
        }
    validation = {
        "all_groups_have_review_records": len(records) == len(deltas),
        "all_records_have_cards": all(
            (review_staging / record["review_card_path"]).is_file()
            for record in records
        ),
        "snapshot_links_traceable": all(
            record["snapshot_before"] in snapshot_by_id
            and record["snapshot_after"] in snapshot_by_id
            for record in records
        ),
        "conflicts_traceable": all(
            item["conflict_id"] in conflicts_by_id for item in conflict_index
        ),
        "unresolved_traceable": all(
            item["timeline_id"] in record_by_timeline for item in unresolved_index
        ),
        "accepted_unlinked_cards_present": len(accepted_unlinked_index) == 2,
        "human_fields_default_null": all(
            all(value is None for value in record["human_review"].values())
            for record in records
        ),
        "contact_sheet_traceable": len(all_tiles) == len(records)
        and len(needs_tiles) == len(needs_review_records),
    }
    return {
        "schema_version": STATE_VERSION,
        "checkpoint": "1F",
        "kind": "checkpoint1f_review_manifest",
        "source": {
            "snapshots_sha256": snapshots_sha256,
            "deltas_sha256": deltas_sha256,
        },
        "review_record_count": len(records),
        "card_count": len(records),
        "contact_sheets": {
            "all_page_count": len(all_pages),
            "needs_review_page_count": len(needs_pages),
            "total_page_count": len(all_pages) + len(needs_pages),
            "cards_per_page": CARDS_PER_PAGE,
        },
        "indexes": {
            "conflict_count": len(conflict_index),
            "unresolved_update_count": len(unresolved_index),
            "low_completeness_snapshot_count": len(low_completeness_index),
            "accepted_unlinked_event_count": len(accepted_unlinked_index),
        },
        "outputs": outputs,
        "validation": validation,
    }
