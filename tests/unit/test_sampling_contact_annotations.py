from pathlib import Path

import numpy as np
import pytest

from pokemon_battle_vision.annotations import validate_annotation_document
from pokemon_battle_vision.contact_sheet import build_contact_sheets
from pokemon_battle_vision.errors import InputError
from pokemon_battle_vision.image_io import write_image
from pokemon_battle_vision.models import FrameTimestampIndex
from pokemon_battle_vision.sampling import fixed_interval_ordinals, fixed_interval_targets


def _index(values):
    count = len(values)
    return FrameTimestampIndex(
        pts_sec=np.asarray(values, dtype=np.float64),
        duration_sec=np.full(count, np.nan),
        key_frame=np.zeros(count, dtype=np.bool_),
        validation={},
        video_sha256="a" * 64,
        ffprobe_version="8.1.2",
    )


def test_fixed_interval_schedule_uses_pts_and_earlier_tie():
    index = _index([0.0, 0.4, 0.6, 1.0, 1.4, 1.6, 2.0])
    assert fixed_interval_targets(0.0, 2.0, 1.0) == [0.0, 1.0, 2.0]
    assert fixed_interval_ordinals(index, 1.0) == [0, 3, 6]
    assert index.nearest_ordinal(0.5) == 1


def test_contact_sheet_index_can_trace_every_tile(tmp_path):
    items = []
    for ordinal in range(3):
        path = tmp_path / "frame_{}.png".format(ordinal)
        write_image(path, np.full((132, 286, 3), ordinal * 30, dtype=np.uint8))
        items.append(
            {
                "absolute_path": str(path),
                "path": path.name,
                "ordinal": ordinal,
                "pts_sec": ordinal * 1.25,
                "target_sec": float(ordinal),
            }
        )
    result = build_contact_sheets(items, tmp_path / "sheets", columns=2, rows=1, tile_width=120)
    assert result["page_count"] == 2
    traced = [tile for page in result["pages"] for tile in page["tiles"]]
    assert [tile["ordinal"] for tile in traced] == [0, 1, 2]
    assert all("frame_path" in tile and "pts_sec" in tile for tile in traced)


def test_annotation_schema_accepts_required_states_and_rejects_unknown_enum():
    root = Path(__file__).resolve().parents[2]
    schema = root / "schemas" / "annotation.schema.json"
    document = {
        "schema_version": "0.1.0",
        "video_sha256": "b" * 64,
        "segments": [
            {"start_sec": 1.0, "end_sec": 2.0, "state": "TEAM_PREVIEW"},
            {"start_sec": 2.0, "end_sec": 3.0, "state": "UNKNOWN"},
        ],
    }
    validate_annotation_document(document, schema)
    document["segments"][0]["state"] = "NOT_A_STATE"
    with pytest.raises(InputError, match="annotation schema 驗證失敗"):
        validate_annotation_document(document, schema)

