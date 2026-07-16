"""Candidate review images、candidate contact sheets 與 coverage sheets 排版。"""

import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import cv2
import numpy as np

from .checkpoint1b_models import EVENT_TYPES
from .image_io import write_image
from .models import PixelRoi
from .review_frame_extractor import roi_ids_for_event
from .review_pack_models import (
    CandidateFrameSelection,
    CandidateReviewRecord,
    CoverageSample,
    EncodedFrameEvidence,
)
from .trigger_notification_features import (
    TRIGGER_ANALYSIS_ROIS,
    TRIGGER_SIDE_ROIS,
)


BACKGROUND = (24, 27, 34)
PANEL = (38, 42, 52)
TEXT = (238, 238, 238)
MUTED = (170, 176, 188)
ACCENT = (80, 210, 255)
ROI_COLORS = (
    (80, 235, 90),
    (0, 180, 255),
    (230, 90, 230),
    (255, 210, 70),
)


def decode_jpeg(data: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("無法解碼 evidence JPEG")
    return image


def fit_image(image: np.ndarray, width: int, height: int, background=PANEL) -> np.ndarray:
    canvas = np.full((height, width, 3), background, dtype=np.uint8)
    source_height, source_width = image.shape[:2]
    scale = min(width / float(source_width), height / float(source_height))
    target_width = max(1, int(round(source_width * scale)))
    target_height = max(1, int(round(source_height * scale)))
    interpolation = cv2.INTER_AREA if scale <= 1.0 else cv2.INTER_LINEAR
    resized = cv2.resize(image, (target_width, target_height), interpolation=interpolation)
    x = (width - target_width) // 2
    y = (height - target_height) // 2
    canvas[y : y + target_height, x : x + target_width] = resized
    return canvas


def _text(
    canvas: np.ndarray,
    value: str,
    origin: Tuple[int, int],
    scale: float = 0.72,
    color=TEXT,
    thickness: int = 1,
) -> None:
    cv2.putText(
        canvas,
        value,
        origin,
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        color,
        thickness,
        cv2.LINE_AA,
    )


def _ellipsize(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: max(0, limit - 3)] + "..."


def annotate_full_frame(
    evidence: EncodedFrameEvidence,
    roi_ids: Sequence[str],
    pixel_rois: Mapping[str, PixelRoi],
    display_width: int,
    display_height: int,
) -> np.ndarray:
    image = decode_jpeg(evidence.full_frame_jpeg)
    height, width = image.shape[:2]
    for index, roi_id in enumerate(roi_ids):
        roi = pixel_rois[roi_id]
        x1 = int(round(roi.x * width / float(display_width)))
        y1 = int(round(roi.y * height / float(display_height)))
        x2 = int(round(roi.x2 * width / float(display_width)))
        y2 = int(round(roi.y2 * height / float(display_height)))
        color = ROI_COLORS[index % len(ROI_COLORS)]
        cv2.rectangle(image, (x1, y1), (max(x1 + 1, x2), max(y1 + 1, y2)), color, 3)
    return image


def render_candidate_review_image(
    event: Mapping[str, Any],
    selection: CandidateFrameSelection,
    evidence: Mapping[int, EncodedFrameEvidence],
    pixel_rois: Mapping[str, PixelRoi],
    display_width: int,
    display_height: int,
    output_path: Path,
) -> Dict[str, Any]:
    roi_ids = roi_ids_for_event(event)
    display_roi_ids = list(roi_ids)
    if str(event["type"]) == "TRIGGER_NOTIFICATION":
        visible = set(roi_ids)
        display_roi_ids.extend(
            TRIGGER_ANALYSIS_ROIS[side]
            for side, canonical_id in TRIGGER_SIDE_ROIS.items()
            if canonical_id in visible
        )
    width = 1920
    metadata_height = 178
    primary_points = [
        point
        for point in selection.evidence_points
        if any(role != "evidence_strip" for role in point.roles)
    ]
    strip_points = [
        point for point in selection.evidence_points if "evidence_strip" in point.roles
    ]
    full_row_height = 300
    roi_row_height = 205
    strip_rows = int(math.ceil(len(strip_points) / 4.0)) if strip_points else 0
    strip_height = strip_rows * 185 + (45 if strip_points else 0)
    height = (
        metadata_height
        + full_row_height
        + roi_row_height * len(display_roi_ids)
        + strip_height
        + 30
    )
    canvas = np.full((height, width, 3), BACKGROUND, dtype=np.uint8)

    candidate_id = str(event["event_id"])
    _text(canvas, "Candidate Review: {}".format(candidate_id), (30, 42), 1.0, ACCENT, 2)
    _text(
        canvas,
        "Predicted: {} | confidence={:.6f} | duration={:.3f}s".format(
            event["type"], float(event["confidence"]), float(event["duration_sec"])
        ),
        (30, 78),
        0.76,
    )
    _text(
        canvas,
        "start={:.6f}s | end={:.6f}s | representative={:.6f}s".format(
            float(event["start_time"]),
            float(event["end_time"]),
            selection.representative_pts,
        ),
        (30, 112),
        0.72,
    )
    _text(
        canvas,
        "Relevant ROIs: {}".format(", ".join(display_roi_ids)),
        (30, 146),
        0.65,
        MUTED,
    )

    phase_width = int((width - 30 - 15 * max(0, len(primary_points) - 1)) / len(primary_points))
    phase_gap = 15
    for phase_index, point in enumerate(primary_points):
        phase = "+".join(point.roles).upper()
        frame_index = point.frame_index
        pts = point.pts
        x = 15 + phase_index * (phase_width + phase_gap)
        y = metadata_height
        annotated = annotate_full_frame(
            evidence[frame_index], display_roi_ids, pixel_rois, display_width, display_height
        )
        panel = fit_image(annotated, phase_width, 230)
        canvas[y : y + 230, x : x + phase_width] = panel
        _text(
            canvas,
            _ellipsize(phase, 46),
            (x + 8, y + 246),
            0.42,
        )
        _text(
            canvas,
            "frame={} | pts={:.3f}s".format(frame_index, pts),
            (x + 8, y + 267),
            0.40,
            MUTED,
        )
        if str(event["type"]) == "BATTLE_TEXT":
            _text(
                canvas,
                "score={:.3f} structure={:.3f}".format(
                    point.score, point.text_structure_strength
                ),
                (x + 8, y + 288),
                0.39,
                MUTED,
            )
        elif str(event["type"]) == "TRIGGER_NOTIFICATION":
            _text(
                canvas,
                "side={} panel={:.3f} text={:.3f} icon={:.3f} combined={:.3f}".format(
                    point.side,
                    point.panel_score,
                    point.text_score,
                    point.icon_score,
                    point.combined_score,
                ),
                (x + 8, y + 288),
                0.35,
                MUTED,
            )

    roi_start_y = metadata_height + full_row_height
    for roi_index, roi_id in enumerate(display_roi_ids):
        y = roi_start_y + roi_index * roi_row_height
        color = ROI_COLORS[roi_index % len(ROI_COLORS)]
        _text(canvas, "ROI: {}".format(roi_id), (25, y + 30), 0.72, color, 2)
        crop_y = y + 42
        for phase_index, point in enumerate(primary_points):
            frame_index = point.frame_index
            pts = point.pts
            x = 15 + phase_index * (phase_width + phase_gap)
            crop = decode_jpeg(evidence[frame_index].roi_jpegs[roi_id])
            panel = fit_image(crop, phase_width, 140)
            canvas[crop_y : crop_y + 140, x : x + phase_width] = panel
            _text(canvas, "{:.3f}s".format(pts), (x + 8, crop_y + 132), 0.46, (255, 255, 255))

    if strip_points:
        strip_y = roi_start_y + roi_row_height * len(display_roi_ids)
        _text(canvas, "BATTLE_TEXT INTERNAL EVIDENCE STRIP", (25, strip_y + 30), 0.70, ACCENT, 2)
        tile_width = 465
        tile_height = 175
        for index, point in enumerate(strip_points):
            row = index // 4
            column = index % 4
            x = 15 + column * 475
            y = strip_y + 42 + row * 185
            crop = decode_jpeg(evidence[point.frame_index].roi_jpegs["battle_text"])
            canvas[y : y + 112, x : x + tile_width] = fit_image(crop, tile_width, 112)
            _text(
                canvas,
                "PTS={:.3f} score={:.3f} {}".format(
                    point.pts, point.score, point.evidence_level
                ),
                (x + 5, y + 134),
                0.43,
            )
            _text(
                canvas,
                _ellipsize(point.decision, 52),
                (x + 5, y + 158),
                0.42,
                MUTED,
            )

    report = write_image(output_path, canvas, jpeg_quality=91)
    return report


def render_candidate_tile(
    event: Mapping[str, Any],
    selection: CandidateFrameSelection,
    evidence: Mapping[int, EncodedFrameEvidence],
    width: int = 580,
    height: int = 300,
) -> np.ndarray:
    tile = np.full((height, width, 3), PANEL, dtype=np.uint8)
    roi_ids = roi_ids_for_event(event)
    if str(event["type"]) == "TRIGGER_NOTIFICATION":
        visible = set(roi_ids)
        roi_ids = [
            TRIGGER_ANALYSIS_ROIS[side]
            for side, canonical_id in TRIGGER_SIDE_ROIS.items()
            if canonical_id in visible
        ]
    _text(tile, _ellipsize(str(event["event_id"]), 38), (12, 26), 0.61, ACCENT, 2)
    _text(
        tile,
        "{:.3f}s -> {:.3f}s | conf={:.4f}".format(
            float(event["start_time"]), float(event["end_time"]), float(event["confidence"])
        ),
        (12, 53),
        0.50,
    )
    _text(tile, str(event["type"]), (12, 78), 0.55, TEXT, 1)

    columns = 1 if len(roi_ids) == 1 else 2
    rows = int(math.ceil(len(roi_ids) / float(columns)))
    gap = 6
    image_y = 88
    cell_width = (width - gap * (columns + 1)) // columns
    cell_height = (height - image_y - gap * (rows + 1)) // rows
    representative = evidence[selection.representative_frame]
    for index, roi_id in enumerate(roi_ids):
        row = index // columns
        column = index % columns
        x = gap + column * (cell_width + gap)
        y = image_y + gap + row * (cell_height + gap)
        crop = decode_jpeg(representative.roi_jpegs[roi_id])
        panel = fit_image(crop, cell_width, cell_height)
        tile[y : y + cell_height, x : x + cell_width] = panel
        cv2.rectangle(tile, (x, y), (x + cell_width - 1, y + cell_height - 1), ROI_COLORS[index % 4], 2)
        _text(tile, _ellipsize(roi_id, 28), (x + 5, y + 18), 0.42, (255, 255, 255), 1)
    return tile


def build_trigger_round1_regression_sheets(
    report: Mapping[str, Any],
    reference_frames: Mapping[str, Sequence[int]],
    selections: Mapping[str, CandidateFrameSelection],
    evidence: Mapping[int, EncodedFrameEvidence],
    output_dir: Path,
) -> Dict[str, Any]:
    """兩個人工正例各一列：window reference + 新 candidate peak。"""
    width = 1880
    row_height = 330
    rows = list(report["rows"])
    canvas = np.full((max(1, len(rows)) * row_height, width, 3), BACKGROUND, dtype=np.uint8)
    tiles = []
    for row_index, row in enumerate(rows):
        y = row_index * row_height
        side = str(row["side"])
        analysis_roi_id = TRIGGER_ANALYSIS_ROIS[side]
        _text(
            canvas,
            "{} | {} | old={} new={}".format(
                row["case_id"], side, row["previous_status"], row["new_status"]
            ),
            (15, y + 30),
            0.66,
            ACCENT,
            2,
        )
        frame_ids = list(reference_frames.get(str(row["case_id"]), []))
        mapped_ids = [
            candidate_id
            for candidate_id in row["mapped_candidate_ids"]
            if candidate_id in selections
        ]
        if mapped_ids:
            frame_ids.append(selections[mapped_ids[0]].representative_frame)
        frame_ids = list(dict.fromkeys(frame_ids))[:4]
        tile_width = 455
        for column, frame_index in enumerate(frame_ids):
            x = 10 + column * 465
            crop = decode_jpeg(evidence[frame_index].roi_jpegs[analysis_roi_id])
            canvas[y + 45 : y + 255, x : x + tile_width] = fit_image(
                crop, tile_width, 210
            )
            roles = "PEAK" if mapped_ids and frame_index == selections[mapped_ids[0]].representative_frame else "WINDOW"
            _text(
                canvas,
                "{} frame={} pts={:.3f}s".format(
                    roles, frame_index, evidence[frame_index].pts
                ),
                (x + 5, y + 280),
                0.45,
            )
            _text(
                canvas,
                _ellipsize(",".join(mapped_ids) or "NO_CANDIDATE", 50),
                (x + 5, y + 307),
                0.44,
                (80, 230, 100) if mapped_ids else (70, 90, 235),
                2,
            )
        tiles.append(
            {
                "row": row_index,
                "case_id": row["case_id"],
                "side": side,
                "frame_indices": frame_ids,
                "mapped_candidate_ids": mapped_ids,
            }
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "trigger_notification_round1_001.jpg"
    image_report = write_image(path, canvas, jpeg_quality=91)
    return {
        "schema_version": "0.1.0",
        "page_count": 1,
        "pages": [
            {
                "page": 1,
                "path": path.name,
                "sha256": image_report["sha256"],
                "tile_count": len(tiles),
                "tiles": tiles,
            }
        ],
    }


def build_candidate_contact_sheets(
    events: Sequence[Mapping[str, Any]],
    records: Mapping[str, CandidateReviewRecord],
    selections: Mapping[str, CandidateFrameSelection],
    evidence: Mapping[int, EncodedFrameEvidence],
    output_dir: Path,
    columns: int = 3,
    rows: int = 4,
) -> Dict[str, Any]:
    tile_width = 580
    tile_height = 300
    per_page = columns * rows
    pages_by_type: Dict[str, List[Dict[str, Any]]] = {}
    candidate_lookup = {}
    for event_type in EVENT_TYPES:
        type_events = [event for event in events if event["type"] == event_type]
        pages = []
        type_dir = output_dir / event_type
        type_dir.mkdir(parents=True, exist_ok=True)
        for page_number, start in enumerate(range(0, len(type_events), per_page), start=1):
            page_events = type_events[start : start + per_page]
            canvas = np.full((rows * tile_height, columns * tile_width, 3), BACKGROUND, dtype=np.uint8)
            tiles = []
            for tile_index, event in enumerate(page_events):
                row = tile_index // columns
                column = tile_index % columns
                x = column * tile_width
                y = row * tile_height
                candidate_id = str(event["event_id"])
                tile = render_candidate_tile(event, selections[candidate_id], evidence)
                canvas[y : y + tile_height, x : x + tile_width] = tile
                tile_row = {
                    "tile_index": tile_index,
                    "row": row,
                    "column": column,
                    "candidate_id": candidate_id,
                    "review_image_path": records[candidate_id].review_image_path,
                }
                tiles.append(tile_row)
            path = type_dir / "{}_contact_{:03d}.jpg".format(event_type, page_number)
            report = write_image(path, canvas, jpeg_quality=90)
            page = {
                "page": page_number,
                "path": path.relative_to(output_dir.parent).as_posix(),
                "sha256": report["sha256"],
                "tile_count": len(tiles),
                "candidate_ids": [tile["candidate_id"] for tile in tiles],
                "tiles": tiles,
            }
            pages.append(page)
            for tile in tiles:
                candidate_lookup[tile["candidate_id"]] = {
                    "page_path": page["path"],
                    "page": page_number,
                    "tile_index": tile["tile_index"],
                }
        pages_by_type[event_type] = pages
    return {
        "schema_version": "0.1.0",
        "layout": {
            "columns": columns,
            "rows": rows,
            "candidates_per_page": per_page,
            "tile_width": tile_width,
            "tile_height": tile_height,
        },
        "pages_by_type": pages_by_type,
        "page_counts": {event_type: len(pages) for event_type, pages in pages_by_type.items()},
        "candidate_lookup": candidate_lookup,
    }


def render_coverage_tile(
    sample: CoverageSample,
    evidence: EncodedFrameEvidence,
    width: int = 470,
    height: int = 350,
) -> np.ndarray:
    tile = np.full((height, width, 3), PANEL, dtype=np.uint8)
    full = decode_jpeg(evidence.full_frame_jpeg)
    image_height = 170
    tile[:image_height] = fit_image(full, width, image_height)
    has_candidate = bool(sample.candidate_ids)
    color = (80, 230, 100) if has_candidate else (70, 90, 235)
    cv2.rectangle(tile, (0, 0), (width - 1, image_height - 1), color, 3)
    crop = decode_jpeg(evidence.roi_jpegs["battle_text"])
    crop_y = image_height + 3
    crop_height = 105
    tile[crop_y : crop_y + crop_height] = fit_image(crop, width, crop_height)
    _text(
        tile,
        "PTS={:.3f}s | frame={}".format(sample.pts, sample.frame_index),
        (10, 305),
        0.55,
        TEXT,
    )
    if has_candidate:
        label = "{} | {}".format(",".join(sample.candidate_ids), ",".join(sample.candidate_types))
    else:
        label = "NO_CANDIDATE"
    _text(tile, _ellipsize(label, 57), (10, 335), 0.50, color, 2)
    return tile


def build_coverage_contact_sheets(
    samples: Sequence[CoverageSample],
    evidence: Mapping[int, EncodedFrameEvidence],
    output_dir: Path,
    columns: int = 4,
    rows: int = 4,
) -> Dict[str, Any]:
    tile_width = 470
    tile_height = 350
    per_page = columns * rows
    output_dir.mkdir(parents=True, exist_ok=True)
    pages = []
    for page_number, start in enumerate(range(0, len(samples), per_page), start=1):
        page_samples = samples[start : start + per_page]
        canvas = np.full((rows * tile_height, columns * tile_width, 3), BACKGROUND, dtype=np.uint8)
        tiles = []
        for tile_index, sample in enumerate(page_samples):
            row = tile_index // columns
            column = tile_index % columns
            x = column * tile_width
            y = row * tile_height
            tile = render_coverage_tile(sample, evidence[sample.frame_index])
            canvas[y : y + tile_height, x : x + tile_width] = tile
            tiles.append(
                {
                    "tile_index": tile_index,
                    "row": row,
                    "column": column,
                    **sample.to_dict(),
                }
            )
        path = output_dir / "coverage_{:03d}.jpg".format(page_number)
        report = write_image(path, canvas, jpeg_quality=90)
        pages.append(
            {
                "page": page_number,
                "path": path.name,
                "sha256": report["sha256"],
                "tile_count": len(tiles),
                "tiles": tiles,
            }
        )
    return {
        "schema_version": "0.1.0",
        "layout": {
            "columns": columns,
            "rows": rows,
            "tiles_per_page": per_page,
            "tile_width": tile_width,
            "tile_height": tile_height,
        },
        "tile_count": len(samples),
        "page_count": len(pages),
        "pages": pages,
    }


def render_dense_audit_tile(
    row: Mapping[str, Any],
    evidence: EncodedFrameEvidence,
    width: int = 470,
    height: int = 260,
) -> np.ndarray:
    tile = np.full((height, width, 3), PANEL, dtype=np.uint8)
    crop = decode_jpeg(evidence.roi_jpegs["battle_text"])
    tile[:150] = fit_image(crop, width, 150)
    candidate_id = str(row.get("candidate_id", "")) or "NO_CANDIDATE"
    color = (80, 230, 100) if candidate_id != "NO_CANDIDATE" else (70, 90, 235)
    cv2.rectangle(tile, (0, 0), (width - 1, 149), color, 3)
    _text(
        tile,
        "PTS={:.3f}s | frame={}".format(float(row["pts"]), int(row["frame_ordinal"])),
        (10, 176),
        0.52,
    )
    _text(tile, _ellipsize(candidate_id, 55), (10, 201), 0.49, color, 2)
    _text(
        tile,
        "score={:.4f} threshold={:.3f}".format(
            float(row["battle_text_score"]), float(row["threshold"])
        ),
        (10, 226),
        0.46,
    )
    _text(tile, _ellipsize(str(row["decision"]), 50), (10, 250), 0.47, TEXT, 1)
    return tile


def build_dense_recall_audit_sheets(
    rows_data: Sequence[Mapping[str, Any]],
    evidence: Mapping[int, EncodedFrameEvidence],
    output_dir: Path,
    columns: int = 4,
    rows: int = 4,
) -> Dict[str, Any]:
    tile_width = 470
    tile_height = 260
    per_page = columns * rows
    output_dir.mkdir(parents=True, exist_ok=True)
    pages = []
    for page_number, start in enumerate(range(0, len(rows_data), per_page), start=1):
        page_rows = rows_data[start : start + per_page]
        canvas = np.full(
            (rows * tile_height, columns * tile_width, 3), BACKGROUND, dtype=np.uint8
        )
        tiles = []
        for tile_index, row_data in enumerate(page_rows):
            grid_row = tile_index // columns
            column = tile_index % columns
            x = column * tile_width
            y = grid_row * tile_height
            frame_index = int(row_data["frame_ordinal"])
            canvas[y : y + tile_height, x : x + tile_width] = render_dense_audit_tile(
                row_data, evidence[frame_index]
            )
            tiles.append(
                {
                    "tile_index": tile_index,
                    "row": grid_row,
                    "column": column,
                    "pts": row_data["pts"],
                    "frame_ordinal": frame_index,
                    "candidate_id": row_data.get("candidate_id", ""),
                    "decision": row_data["decision"],
                    "regression_targets": row_data["regression_targets"],
                }
            )
        path = output_dir / "battle_text_recall_{:03d}.jpg".format(page_number)
        report = write_image(path, canvas, jpeg_quality=90)
        pages.append(
            {
                "page": page_number,
                "path": path.name,
                "sha256": report["sha256"],
                "tile_count": len(tiles),
                "tiles": tiles,
            }
        )
    return {
        "schema_version": "0.1.0",
        "layout": {
            "columns": columns,
            "rows": rows,
            "tiles_per_page": per_page,
            "tile_width": tile_width,
            "tile_height": tile_height,
        },
        "interval_sec": 0.1,
        "tile_count": len(rows_data),
        "page_count": len(pages),
        "pages": pages,
    }


def build_round1_regression_sheets(
    report: Mapping[str, Any],
    reference_frames: Mapping[str, Sequence[int]],
    selections: Mapping[str, CandidateFrameSelection],
    evidence: Mapping[int, EncodedFrameEvidence],
    output_dir: Path,
) -> Dict[str, Any]:
    """同頁顯示舊人工案例參考區段與新版 peak representatives。"""
    tile_width = 950
    tile_height = 330
    columns = 2
    rows_per_page = 2
    per_page = columns * rows_per_page
    output_dir.mkdir(parents=True, exist_ok=True)
    pages = []
    all_rows = list(report["rows"])
    for page_number, start in enumerate(range(0, len(all_rows), per_page), start=1):
        page_rows = all_rows[start : start + per_page]
        canvas = np.full(
            (rows_per_page * tile_height, columns * tile_width, 3),
            BACKGROUND,
            dtype=np.uint8,
        )
        tiles = []
        for tile_index, mapping in enumerate(page_rows):
            grid_row = tile_index // columns
            column = tile_index % columns
            origin_x = column * tile_width
            origin_y = grid_row * tile_height
            candidate_id = str(mapping["baseline_candidate_id"])
            _text(
                canvas,
                "{} | {} | {}".format(
                    candidate_id,
                    mapping["human_category"],
                    mapping["mapping_result"],
                ),
                (origin_x + 12, origin_y + 28),
                0.57,
                ACCENT,
                2,
            )
            _text(
                canvas,
                _ellipsize(
                    "new: {}".format(", ".join(mapping["mapped_candidate_ids"]) or "NONE"),
                    105,
                ),
                (origin_x + 12, origin_y + 54),
                0.46,
                MUTED,
            )
            panels: List[Tuple[str, int]] = []
            for number, frame_index in enumerate(reference_frames[candidate_id], start=1):
                panels.append(("OLD SPAN {}".format(number), int(frame_index)))
            for mapped_id in mapping["mapped_candidate_ids"]:
                if mapped_id in selections:
                    panels.append(
                        ("NEW {} PEAK".format(mapped_id), selections[mapped_id].representative_frame)
                    )
            panels = panels[:4]
            panel_width = 228
            for panel_index, (label, frame_index) in enumerate(panels):
                x = origin_x + 8 + panel_index * 235
                y = origin_y + 68
                crop = decode_jpeg(evidence[frame_index].roi_jpegs["battle_text"])
                canvas[y : y + 190, x : x + panel_width] = fit_image(
                    crop, panel_width, 190
                )
                _text(
                    canvas,
                    _ellipsize(label, 32),
                    (x + 3, y + 216),
                    0.39,
                    TEXT,
                )
                _text(
                    canvas,
                    "PTS={:.3f}".format(evidence[frame_index].pts),
                    (x + 3, y + 238),
                    0.39,
                    MUTED,
                )
            tiles.append(
                {
                    "tile_index": tile_index,
                    "row": grid_row,
                    "column": column,
                    "baseline_candidate_id": candidate_id,
                    "mapped_candidate_ids": mapping["mapped_candidate_ids"],
                }
            )
        path = output_dir / "round1_regression_{:03d}.jpg".format(page_number)
        image_report = write_image(path, canvas, jpeg_quality=91)
        pages.append(
            {
                "page": page_number,
                "path": path.name,
                "sha256": image_report["sha256"],
                "tile_count": len(tiles),
                "tiles": tiles,
            }
        )
    return {
        "schema_version": "0.1.0",
        "layout": {
            "columns": columns,
            "rows": rows_per_page,
            "tiles_per_page": per_page,
        },
        "tile_count": len(all_rows),
        "page_count": len(pages),
        "pages": pages,
    }
