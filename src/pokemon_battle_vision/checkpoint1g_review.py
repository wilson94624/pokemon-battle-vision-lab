"""Checkpoint 1G 非阻塞式工程 Review／Audit Pack。"""

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from PIL import Image, ImageDraw, ImageFont

from .utils import sha256_file, write_json


BG = (18, 23, 31)
PANEL = (35, 43, 55)
WHITE = (240, 244, 250)
MUTED = (165, 178, 196)
ACCENT = (77, 208, 225)
GREEN = (97, 210, 140)


def _font(size: int):
    for path in (
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    ):
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _fit(image: Image.Image, width: int, height: int) -> Image.Image:
    copy = image.copy()
    copy.thumbnail((width, height), Image.Resampling.LANCZOS)
    return copy


def _save_table(path: Path, title: str, rows: Sequence[str], page_size: int = 24) -> List[str]:
    pages = []
    chunks = [rows[index : index + page_size] for index in range(0, len(rows), page_size)] or [[]]
    for page_index, chunk in enumerate(chunks, start=1):
        image = Image.new("RGB", (1600, 120 + 48 * max(1, len(chunk))), BG)
        draw = ImageDraw.Draw(image)
        draw.text((36, 28), title, font=_font(34), fill=ACCENT)
        draw.text((1370, 38), "page {}/{}".format(page_index, len(chunks)), font=_font(20), fill=MUTED)
        for index, row in enumerate(chunk):
            y = 96 + index * 48
            if index % 2 == 0:
                draw.rectangle((24, y - 4, 1576, y + 40), fill=PANEL)
            draw.text((42, y), row, font=_font(20), fill=WHITE)
        page = path.with_name("{}-{:03d}{}".format(path.stem, page_index, path.suffix))
        page.parent.mkdir(parents=True, exist_ok=True)
        image.save(page, quality=92)
        pages.append(page.name)
    return pages


def _build_evidence_sheet(
    output_dir: Path,
    title: str,
    items: Sequence[Mapping[str, Any]],
    relative_dir: str,
    per_page: int = 16,
) -> List[str]:
    pages = []
    for offset in range(0, len(items), per_page):
        chunk = items[offset : offset + per_page]
        image = Image.new("RGB", (1600, 1000), BG)
        draw = ImageDraw.Draw(image)
        draw.text((32, 22), title, font=_font(32), fill=ACCENT)
        for tile_index, item in enumerate(chunk):
            column, row = tile_index % 4, tile_index // 4
            x, y = 24 + column * 394, 82 + row * 226
            draw.rectangle((x, y, x + 374, y + 208), fill=PANEL)
            path = item.get("path")
            if path and (output_dir / path).is_file():
                tile = _fit(Image.open(output_dir / path).convert("RGB"), 350, 142)
                image.paste(tile, (x + 12, y + 12))
            draw.text((x + 12, y + 160), str(item.get("label", ""))[:38], font=_font(17), fill=WHITE)
            draw.text((x + 12, y + 184), str(item.get("detail", ""))[:46], font=_font(14), fill=MUTED)
        page_index = len(pages) + 1
        relative = "{}/page-{:03d}.jpg".format(relative_dir, page_index)
        target = output_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, quality=92)
        pages.append(relative)
    return pages


def _hp_track_chart(output_dir: Path, hp: Mapping[str, Any]) -> str:
    image = Image.new("RGB", (1800, 1000), BG)
    draw = ImageDraw.Draw(image)
    draw.text((40, 24), "HP tracks（exact／OCR %／visual estimate 分層）", font=_font(34), fill=ACCENT)
    rows = hp["observations"]
    start = float(hp["coverage"]["start_time"] or 0.0)
    end = float(hp["coverage"]["end_time"] or start + 1.0)
    tracks = sorted({(row["side"], row["slot"]) for row in rows})
    colors = [(86, 207, 143), (78, 167, 255), (245, 184, 71), (218, 105, 120)]
    for index, track in enumerate(tracks):
        y0 = 150 + index * 190
        draw.text((40, y0 - 25), "{} {}".format(*track), font=_font(24), fill=WHITE)
        draw.rectangle((180, y0, 1740, y0 + 120), outline=MUTED, width=2)
        segments = []
        current_segment = []
        previous_time = None
        for row in rows:
            if (row["side"], row["slot"]) != track or row.get("hp_percent") is None:
                continue
            timestamp = float(row["timestamp"])
            if previous_time is not None and timestamp - previous_time > 3.0:
                if current_segment:
                    segments.append(current_segment)
                current_segment = []
            x = 180 + int(1560 * (float(row["timestamp"]) - start) / max(0.001, end - start))
            y = y0 + 120 - int(120 * float(row["hp_percent"]) / 100.0)
            current_segment.append((x, y))
            previous_time = timestamp
        if current_segment:
            segments.append(current_segment)
        for points in segments:
            if len(points) >= 2:
                draw.line(points, fill=colors[index % len(colors)], width=4)
            for point in points:
                draw.ellipse((point[0] - 3, point[1] - 3, point[0] + 3, point[1] + 3), fill=colors[index % len(colors)])
    relative = "hp_tracks/hp-tracks.jpg"
    target = output_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, quality=94)
    return relative


def build_checkpoint1g_review(
    output_dir: Path,
    roster: Mapping[str, Any],
    selected: Mapping[str, Any],
    menus: Mapping[str, Any],
    hp: Mapping[str, Any],
    hp_changes: Mapping[str, Any],
    active: Mapping[str, Any],
    entities: Mapping[str, Any],
    edges: Mapping[str, Any],
    cycles: Mapping[str, Any],
    enriched: Mapping[str, Any],
) -> Dict[str, Any]:
    roster_pages = _build_evidence_sheet(
        output_dir,
        "Team Preview roster",
        [
            {
                "path": row["evidence"][0]["path"],
                "label": "{} slot {}".format(row["side"], row["slot_index"]),
                "detail": row.get("species_text") or row["visual_identity"],
            }
            for row in roster["entries"]
        ],
        "team_preview",
        per_page=12,
    )
    selected_review_rows = selected.get("row_observations")
    selected_pages = _build_evidence_sheet(
        output_dir,
        "Selected Four",
        (
            [
                {
                    "path": row["evidence_path"],
                    "label": "row {} | order {}".format(
                        row["roster_row"],
                        row["selection_order"] if row["selection_order"] is not None else "unknown",
                    ),
                    "detail": "marker {} | OCR {}".format(
                        row["marker_status"], row["marker_raw_text"] or "empty"
                    ),
                }
                for row in selected_review_rows
            ]
            if selected_review_rows is not None
            else [
                {
                    "path": row["evidence_path"],
                    "label": "order {}".format(row["selection_order"]),
                    "detail": row.get("species") or row["visual_identity"],
                }
                for row in selected["player_selected"]
            ]
        ),
        "selected_four",
        per_page=6 if selected_review_rows is not None else 4,
    )
    menu_pages = _build_evidence_sheet(
        output_dir,
        "Move Menu observations",
        [
            {
                "path": row["evidence"][0]["path"],
                "label": row["candidate_id"],
                "detail": "{} | {}".format(
                    row["selecting_slot"],
                    ",".join(move["value"] for move in row["available_moves"]) or "moves unknown",
                ),
            }
            for row in menus["observations"]
        ],
        "move_menu",
        per_page=16,
    )
    hp_chart = _hp_track_chart(output_dir, hp)
    table_specs = {
        "hp_changes": [
            "{} t={:.3f} {}:{} {}→{} ({:+.2f}%) cause={}".format(
                row["change_id"], row["timestamp"], row["side"], row["slot"],
                row["before_percent"], row["after_percent"], row["delta_percent"], row["cause"]
            )
            for row in hp_changes["changes"]
        ],
        "active_slots": [
            "{} t={:.3f} {}:{} identity={} entity={}".format(
                row["active_slot_entry_id"], row["timestamp"], row["side"], row["slot"],
                row.get("identity_text") or row.get("visual_identity"), row.get("pokemon_entity_id")
            )
            for row in active["entries"]
        ],
        "entity_resolution": [
            "{} side={} species={} team_slot={} selected={} conflicts={}".format(
                row["entity_id"], row["side"]["value"], row["species"]["value"],
                row["team_slot"], row["selected_order"], len(row["conflicts"])
            )
            for row in entities["entities"]
        ],
        "decision_cycles": [
            "{} {:.3f}–{:.3f}s menus={} timeline={} events={} official_turn=false".format(
                row["cycle_id"], row["start_time"], row["end_time"],
                len(row["decision_window_ids"]), len(row["timeline_ids"]), len(row["battle_event_ids"])
            )
            for row in cycles["cycles"]
        ],
        "enriched_snapshots": [
            "{} t={:.3f} base={} hp_slots={} active_slots={} cycle={} confidence={:.3f}".format(
                row["enriched_snapshot_id"], row["timestamp"], row["base_state_snapshot_id"],
                len(row["hp_state"]), len(row["active_slots"]), row["decision_cycle"], row["confidence"]
            )
            for row in enriched["snapshots"]
        ],
    }
    table_pages: Dict[str, List[str]] = {}
    for name, rows in table_specs.items():
        directory = output_dir / name
        pages = _save_table(directory / (name + ".jpg"), name.replace("_", " ").title(), rows)
        table_pages[name] = ["{}/{}".format(name, page) for page in pages]
    uncertainties = {
        "schema_version": "0.1.0",
        "kind": "checkpoint1g_uncertainty_conflict_index",
        "unknown_roster_entries": [row["visual_identity"] for row in roster["entries"] if row["species_text"] is None],
        "unresolved_selected_orders": [row["selection_order"] for row in selected["player_selected"] if row["roster_ref"] is None],
        "move_windows_without_moves": [row["decision_window_id"] for row in menus["observations"] if not row["available_moves"]],
        "entity_conflicts": [row["entity_id"] for row in entities["entities"] if row["conflicts"]],
        "edge_count": edges["edge_count"],
    }
    coverage = {
        "schema_version": "0.1.0",
        "kind": "checkpoint1g_coverage_summary",
        "team_preview_candidates_processed": roster["source_candidate_count"],
        "selected_four_rows": len(selected["player_selected"]),
        "move_menu_candidates_processed": menus["source_candidate_count"],
        "hp_raw_samples": hp["raw_sample_count"],
        "hp_coverage": hp["coverage"],
        "base_snapshots_mapped": enriched["base_snapshot_count"],
        "review_is_blocking": False,
    }
    write_json(output_dir / "uncertainty_conflict_index.json", uncertainties)
    write_json(output_dir / "coverage_summary.json", coverage)
    pages = {
        "team_preview": roster_pages,
        "selected_four": selected_pages,
        "move_menu": menu_pages,
        "hp_tracks": [hp_chart],
        **table_pages,
    }
    manifest = {
        "schema_version": "0.1.0",
        "checkpoint": "1G",
        "kind": "checkpoint1g_review_manifest",
        "status": "complete",
        "blocking_human_review": False,
        "page_counts": {key: len(value) for key, value in pages.items()},
        "pages": pages,
        "indexes": ["uncertainty_conflict_index.json", "coverage_summary.json"],
    }
    write_json(output_dir / "review_manifest.json", manifest)
    return manifest
