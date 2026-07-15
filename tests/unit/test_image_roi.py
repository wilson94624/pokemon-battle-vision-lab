import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from pokemon_battle_vision.errors import RoiApprovalError
from pokemon_battle_vision.image_io import (
    detect_image_format_bytes,
    read_image,
    write_image,
)
from pokemon_battle_vision.models import NormalizedRoi
from pokemon_battle_vision.roi import (
    create_roi_approval,
    draw_roi_overlay,
    normalized_to_pixel,
)
from pokemon_battle_vision.utils import sha256_file


def test_magic_byte_detection_and_suffix_mismatch(tmp_path):
    image = np.zeros((10, 20, 3), dtype=np.uint8)
    success, encoded = cv2.imencode(".png", image)
    assert success
    path = tmp_path / "actually_png.jpeg"
    path.write_bytes(encoded.tobytes())
    decoded, report = read_image(path)
    assert decoded.shape[:2] == (10, 20)
    assert report["detected_format"] == "png"
    assert report["declared_format"] == "jpeg"
    assert report["warnings"][0]["code"] == "INPUT_FORMAT_MISMATCH"


def test_write_image_verifies_encoding(tmp_path):
    path = tmp_path / "output.jpg"
    write_image(path, np.full((8, 9, 3), 127, dtype=np.uint8))
    assert detect_image_format_bytes(path.read_bytes()) == "jpeg"


def test_normalized_to_pixel_covers_fractional_edges():
    roi = NormalizedRoi("x", 0.1, 0.2, 0.3, 0.4)
    pixel = normalized_to_pixel(roi, 11, 13)
    assert pixel.x == 1
    assert pixel.y == 2
    assert pixel.x2 == 5
    assert pixel.y2 == 8


def test_normalized_to_pixel_rejects_out_of_bounds():
    with pytest.raises(ValueError, match="超出"):
        normalized_to_pixel(NormalizedRoi("bad", 0.9, 0.1, 0.2, 0.2), 100, 100)


def test_overlay_changes_only_copy():
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    roi = normalized_to_pixel(NormalizedRoi("menu", 0.5, 0.2, 0.4, 0.5), 200, 100)
    overlay = draw_roi_overlay(frame, [roi], line_thickness=2)
    assert np.count_nonzero(frame) == 0
    assert np.count_nonzero(overlay) > 0


def test_roi_approval_hashes_all_inputs(tmp_path):
    video = tmp_path / "video.mp4"
    config = tmp_path / "roi.json"
    overlay = tmp_path / "overlay.png"
    manifest_path = tmp_path / "roi_overlay_manifest.json"
    approval = tmp_path / "roi_approval.json"
    video.write_bytes(b"video")
    config.write_text("{}", encoding="utf-8")
    overlay.write_bytes(b"overlay")
    manifest = {
        "video_sha256": sha256_file(video),
        "roi_config_sha256": sha256_file(config),
        "overlays": [{"path": overlay.name, "sha256": sha256_file(overlay)}],
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    payload = create_roi_approval(video, config, manifest_path, "Tester", approval)
    assert payload["status"] == "approved"
    assert payload["overlay_count"] == 1
    assert approval.is_file()


def test_roi_approval_rejects_changed_overlay(tmp_path):
    video = tmp_path / "video.mp4"
    config = tmp_path / "roi.json"
    overlay = tmp_path / "overlay.png"
    manifest_path = tmp_path / "manifest.json"
    video.write_bytes(b"video")
    config.write_text("{}", encoding="utf-8")
    overlay.write_bytes(b"before")
    manifest_path.write_text(
        json.dumps(
            {
                "video_sha256": sha256_file(video),
                "roi_config_sha256": sha256_file(config),
                "overlays": [{"path": overlay.name, "sha256": sha256_file(overlay)}],
            }
        ),
        encoding="utf-8",
    )
    overlay.write_bytes(b"after")
    with pytest.raises(RoiApprovalError, match="overlay hash 已改變"):
        create_roi_approval(video, config, manifest_path, "Tester", tmp_path / "approval.json")

