"""可分頁、每格可反查 timestamp/ordinal/path 的 contact sheets。"""

from pathlib import Path
from typing import Any, Dict, List, Sequence

import cv2
import numpy as np

from .errors import InputError
from .image_io import read_image, write_image


def build_contact_sheets(
    items: Sequence[Dict[str, Any]],
    output_dir: Path,
    columns: int = 4,
    rows: int = 3,
    tile_width: int = 480,
) -> Dict[str, Any]:
    if columns <= 0 or rows <= 0 or tile_width <= 0:
        raise ValueError("contact sheet layout 必須為正數")
    if not items:
        raise InputError("沒有 contact frames 可建立 contact sheet")
    per_page = columns * rows
    tile_image_height = int(round(tile_width * 1320 / 2868))
    label_height = 42
    tile_height = tile_image_height + label_height
    output_dir.mkdir(parents=True, exist_ok=True)
    pages: List[Dict[str, Any]] = []
    for page_index, start in enumerate(range(0, len(items), per_page), start=1):
        page_items = items[start : start + per_page]
        canvas = np.full((rows * tile_height, columns * tile_width, 3), 20, dtype=np.uint8)
        indexed_tiles = []
        for tile_index, item in enumerate(page_items):
            row = tile_index // columns
            column = tile_index % columns
            image, _ = read_image(Path(item["absolute_path"]))
            if image.ndim == 2:
                image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
            elif image.shape[2] == 4:
                image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
            resized = cv2.resize(image, (tile_width, tile_image_height), interpolation=cv2.INTER_AREA)
            y = row * tile_height
            x = column * tile_width
            canvas[y : y + tile_image_height, x : x + tile_width] = resized
            label = "t={:.3f}s | frame={:06d}".format(item["pts_sec"], item["ordinal"])
            cv2.putText(
                canvas,
                label,
                (x + 10, y + tile_image_height + 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )
            indexed_tiles.append(
                {
                    "tile_index": tile_index,
                    "row": row,
                    "column": column,
                    "ordinal": item["ordinal"],
                    "pts_sec": item["pts_sec"],
                    "target_sec": item["target_sec"],
                    "frame_path": item["path"],
                }
            )
        page_path = output_dir / "contact_sheet_{:03d}.jpg".format(page_index)
        file_report = write_image(page_path, canvas, jpeg_quality=90)
        pages.append(
            {
                "page": page_index,
                "path": page_path.name,
                "sha256": file_report["sha256"],
                "tile_count": len(indexed_tiles),
                "tiles": indexed_tiles,
            }
        )
    return {
        "schema_version": "0.1.0",
        "layout": {
            "columns": columns,
            "rows": rows,
            "tile_width": tile_width,
            "tile_image_height": tile_image_height,
            "label_height": label_height,
        },
        "frame_count": len(items),
        "page_count": len(pages),
        "pages": pages,
    }
