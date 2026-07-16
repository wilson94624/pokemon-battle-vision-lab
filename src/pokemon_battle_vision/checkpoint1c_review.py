"""Checkpoint 1C Human Review cards 與分類 contact sheets。"""

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from .checkpoint1c_models import OcrFrameSelection, PreprocessingVariant, TextValidationRecord
from .utils import sha256_file, write_json


BACKGROUND = (18, 24, 34)
PANEL = (31, 40, 54)
MUTED = (160, 174, 192)
WHITE = (242, 246, 252)
GREEN = (68, 201, 132)
AMBER = (245, 181, 72)
RED = (235, 101, 111)
FONT_PATHS = (
    Path("/System/Library/Fonts/STHeiti Medium.ttc"),
    Path("/System/Library/Fonts/STHeiti Light.ttc"),
)


def _font(size: int):
    for path in FONT_PATHS:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _fit(image: Image.Image, width: int, height: int) -> Image.Image:
    copy = image.convert("RGB")
    copy.thumbnail((width, height), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (width, height), (10, 14, 20))
    canvas.paste(copy, ((width - copy.width) // 2, (height - copy.height) // 2))
    return canvas


def _wrapped_lines(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> List[str]:
    lines: List[str] = []
    for paragraph in (text or "").splitlines() or [""]:
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
    max_lines: int = 4,
) -> int:
    lines = _wrapped_lines(draw, text, font, width)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-1] + "…" if lines[-1] else "…"
    x, y = xy
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height
    return y


def _status_color(label: str):
    return {"VALID_TEXT": GREEN, "NO_TEXT": RED, "UNCERTAIN": AMBER}.get(label, WHITE)


def render_review_card(
    output_path: Path,
    event: Mapping[str, Any],
    selections: Sequence[OcrFrameSelection],
    variants_by_frame_key: Mapping[str, Sequence[PreprocessingVariant]],
    raw_results_by_frame: Mapping[int, Sequence[Mapping[str, Any]]],
    aggregate: Mapping[str, Any],
    validation: TextValidationRecord,
    output_staging: Path,
    review_staging: Path,
    full_frame_paths: Mapping[str, str],
) -> None:
    shown = list(selections)[:5]
    width = 1900
    header_height = 285
    hero_height = 370
    evidence_height = 250 * max(1, len(shown))
    canvas = Image.new("RGB", (width, header_height + hero_height + evidence_height + 40), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    title_font = _font(36)
    body_font = _font(25)
    small_font = _font(20)
    label_color = _status_color(validation.validation_label)
    draw.text((40, 28), "{}  {}".format(validation.event_id, validation.event_type), font=title_font, fill=WHITE)
    draw.text(
        (40, 82),
        "{} / {}".format(validation.validation_label, validation.workflow_status),
        font=title_font,
        fill=label_color,
    )
    draw.text(
        (40, 138),
        "PTS {:.3f}–{:.3f}  OCR {:.3f}  consensus {:.3f}  validation {:.3f}".format(
            validation.start_time,
            validation.end_time,
            validation.ocr_confidence,
            validation.consensus_confidence,
            validation.validation_confidence,
        ),
        font=body_font,
        fill=MUTED,
    )
    draw.text(
        (40, 184),
        "reasons: {}".format(", ".join(validation.review_reasons) or "none"),
        font=small_font,
        fill=MUTED,
    )
    duplicate_summary = "none"
    if validation.duplicate_group_id:
        duplicate_summary = "group={}  possible_of={}  confidence={:.3f}".format(
            validation.duplicate_group_id,
            validation.possible_duplicate_of or "group_origin",
            validation.duplicate_confidence,
        )
    draw.text(
        (40, 222),
        "possible duplicate: {}".format(duplicate_summary),
        font=small_font,
        fill=AMBER if validation.duplicate_group_id else MUTED,
    )

    selected_ordinal = aggregate.get("selected_frame_ordinal")
    hero_selection = next(
        (row for row in selections if row.frame_ordinal == selected_ordinal), selections[0]
    )
    frame_key = "{}:{:06d}".format(hero_selection.event_id, hero_selection.frame_ordinal)
    full_image = Image.open(review_staging / full_frame_paths[frame_key])
    raw_image = Image.open(output_staging / hero_selection.image_path)
    canvas.paste(_fit(full_image, 880, 330), (40, header_height + 20))
    canvas.paste(_fit(raw_image, 880, 250), (980, header_height + 20))
    draw.text((980, header_height + 280), "aggregate OCR：", font=body_font, fill=MUTED)
    _draw_wrapped(
        draw,
        (1160, header_height + 278),
        str(aggregate.get("best_text", "")) or "（空）",
        body_font,
        WHITE,
        680,
        34,
        max_lines=2,
    )

    top = header_height + hero_height
    for selection in shown:
        draw.rectangle((20, top, width - 20, top + 230), fill=PANEL)
        frame_key = "{}:{:06d}".format(selection.event_id, selection.frame_ordinal)
        raw = Image.open(output_staging / selection.image_path)
        canvas.paste(_fit(raw, 330, 160), (40, top + 48))
        draw.text(
            (40, top + 14),
            "frame {}  PTS {:.3f}  {}".format(
                selection.frame_ordinal, selection.pts, selection.selection_reason
            ),
            font=small_font,
            fill=WHITE,
        )
        variants = list(variants_by_frame_key[frame_key])
        x = 390
        results = {str(row["variant_id"]): row for row in raw_results_by_frame[selection.frame_ordinal]}
        for variant in variants:
            image = Image.open(output_staging / variant.image_path)
            canvas.paste(_fit(image, 210, 100), (x, top + 48))
            result = results.get(variant.variant_id, {})
            draw.text((x, top + 154), variant.variant_id, font=_font(16), fill=MUTED)
            draw.text(
                (x, top + 180),
                "conf {:.2f}".format(float(result.get("ocr_confidence", 0.0))),
                font=_font(16),
                fill=WHITE,
            )
            x += 225
        text_x = 1305
        draw.text((text_x, top + 48), "各 variant OCR：", font=small_font, fill=MUTED)
        y = top + 82
        for variant in variants:
            result = results.get(variant.variant_id, {})
            value = str(result.get("normalized_text", "")) or "（空）"
            y = _draw_wrapped(
                draw,
                (text_x, y),
                "{}: {}".format(variant.variant_id, value.replace("\n", " / ")),
                _font(17),
                WHITE,
                550,
                24,
                max_lines=1,
            )
        top += 250
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, format="JPEG", quality=91, optimize=True)


def _render_contact_tile(
    record: Mapping[str, Any], card_path: Path, width: int = 430, height: int = 300
) -> Image.Image:
    tile = Image.new("RGB", (width, height), PANEL)
    draw = ImageDraw.Draw(tile)
    card = Image.open(card_path)
    tile.paste(_fit(card, width, 170), (0, 0))
    draw.text((14, 180), str(record["event_id"]), font=_font(20), fill=WHITE)
    draw.text(
        (14, 210),
        "{:.2f}–{:.2f}  OCR {:.2f}/{:.2f}".format(
            float(record["start_time"]),
            float(record["end_time"]),
            float(record["ocr_confidence"]),
            float(record["consensus_confidence"]),
        ),
        font=_font(16),
        fill=MUTED,
    )
    _draw_wrapped(
        draw,
        (14, 242),
        str(record.get("ocr_text", "")) or "（空）",
        _font(18),
        _status_color(str(record["validation_label"])),
        width - 28,
        24,
        max_lines=2,
    )
    return tile


def build_classification_contact_sheets(
    review_staging: Path,
    records: Sequence[Mapping[str, Any]],
    cards_by_event: Mapping[str, str],
) -> Dict[str, Any]:
    index_rows = []
    page_counts: Dict[str, int] = {}
    for event_type in ("BATTLE_TEXT", "TRIGGER_NOTIFICATION"):
        for label in ("VALID_TEXT", "NO_TEXT", "UNCERTAIN"):
            category = "{}_{}".format(event_type, label)
            rows = [
                row
                for row in records
                if row["event_type"] == event_type and row["validation_label"] == label
            ]
            page_count = int(math.ceil(len(rows) / 12.0)) if rows else 0
            page_counts[category] = page_count
            for page_number in range(page_count):
                page_rows = rows[page_number * 12 : (page_number + 1) * 12]
                page = Image.new("RGB", (1800, 1300), BACKGROUND)
                draw = ImageDraw.Draw(page)
                draw.text(
                    (40, 22),
                    "{}  page {}/{}".format(category, page_number + 1, page_count),
                    font=_font(30),
                    fill=WHITE,
                )
                relative_page = "contact_sheets/{}/{}_contact_{:03d}.jpg".format(
                    category, category, page_number + 1
                )
                for tile_index, row in enumerate(page_rows):
                    x = 20 + (tile_index % 4) * 445
                    y = 80 + (tile_index // 4) * 405
                    card_path = review_staging / cards_by_event[str(row["event_id"])]
                    page.paste(_render_contact_tile(row, card_path), (x, y))
                    index_rows.append(
                        {
                            "category": category,
                            "event_id": row["event_id"],
                            "page": relative_page,
                            "page_number": page_number + 1,
                            "tile_index": tile_index,
                        }
                    )
                page_path = review_staging / relative_page
                page_path.parent.mkdir(parents=True, exist_ok=True)
                page.save(page_path, format="JPEG", quality=90, optimize=True)
    index_payload = {
        "schema_version": "0.1.0",
        "kind": "checkpoint1c_contact_sheet_index",
        "page_counts": page_counts,
        "total_page_count": sum(page_counts.values()),
        "tile_count": len(index_rows),
        "rows": index_rows,
    }
    write_json(review_staging / "contact_sheets/contact_sheet_index.json", index_payload)
    return {
        "page_counts": page_counts,
        "total_page_count": sum(page_counts.values()),
        "tile_count": len(index_rows),
        "index_path": "contact_sheets/contact_sheet_index.json",
        "index_sha256": sha256_file(review_staging / "contact_sheets/contact_sheet_index.json"),
    }


def build_review_record(
    validation: TextValidationRecord,
    selections: Sequence[OcrFrameSelection],
    aggregate: Mapping[str, Any],
    card_path: str,
) -> Dict[str, Any]:
    payload = validation.to_dict()
    payload.update(
        {
            "review_card_path": card_path,
            "ocr_frame_count": len(selections),
            "ocr_frame_ordinals": [row.frame_ordinal for row in selections],
            "ocr_frame_pts": [row.pts for row in selections],
            "selected_frame_ordinal": aggregate.get("selected_frame_ordinal"),
            "selected_variant_id": aggregate.get("selected_variant_id", ""),
            "supporting_frame_ordinals": aggregate.get("supporting_frame_ordinals", []),
        }
    )
    return payload
