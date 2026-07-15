import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from pokemon_battle_vision.config import SUPPORTED_PROFILE
from pokemon_battle_vision.errors import DependencyError, MediaProbeError
from pokemon_battle_vision.media_probe import (
    _default_runner,
    dependency_preflight,
    normalize_rotation,
    parse_frame_timestamp_payload,
    parse_metadata_payload,
)


def _completed(command, stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=stderr)


def _preflight_runner(version="8.1.2", malformed_capability=False):
    def runner(command, timeout):
        name = Path(command[0]).name
        if "-version" in command:
            return _completed(command, stdout="{} version {} Copyright\n".format(name, version))
        if malformed_capability:
            return _completed(command, stdout="{broken")
        return _completed(
            command,
            stdout=json.dumps(
                {
                    "streams": [{"codec_type": "video", "width": 2, "height": 2}],
                    "frames": [{"best_effort_timestamp_time": "0.000000"}],
                }
            ),
        )

    return runner


def test_dependency_preflight_success_and_untested_warning():
    report = dependency_preflight(
        runner=_preflight_runner(version="9.0"),
        which=lambda name: "/tools/{}".format(name),
    )
    assert report["status"] == "pass"
    assert report["versions"]["ffprobe"] == "9.0"
    assert report["warnings"][0]["code"] == "UNTESTED_FFMPEG_VERSION"


def test_dependency_preflight_missing_executable():
    with pytest.raises(DependencyError, match="找不到 ffprobe"):
        dependency_preflight(
            runner=_preflight_runner(),
            which=lambda name: "/tools/ffmpeg" if name == "ffmpeg" else None,
        )


def test_dependency_preflight_malformed_capability_json():
    with pytest.raises(DependencyError, match="損壞的 JSON"):
        dependency_preflight(
            runner=_preflight_runner(malformed_capability=True),
            which=lambda name: "/tools/{}".format(name),
        )


def test_default_runner_turns_timeout_into_media_error(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=1)

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(MediaProbeError, match="timeout"):
        _default_runner(["ffprobe"], 1)


@pytest.mark.parametrize(
    "raw,expected",
    [(0, 0), (90, 270), (-90, 90), (450, 270), ("180", 180)],
)
def test_normalize_rotation(raw, expected):
    assert normalize_rotation(raw) == expected


def test_parse_metadata_rotation_and_display_dimensions():
    payload = {
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "hevc",
                "width": 1320,
                "height": 2868,
                "time_base": "1/600",
                "r_frame_rate": "60/1",
                "avg_frame_rate": "30/1",
                "nb_frames": "3",
                "side_data_list": [{"side_data_type": "Display Matrix", "rotation": 90}],
            }
        ],
        "format": {"duration": "1.0", "size": "100", "bit_rate": "800"},
    }
    environment = {
        "versions": {"ffmpeg": "8.1.2", "ffprobe": "8.1.2"},
        "warnings": [],
    }
    metadata = parse_metadata_payload(
        payload, Path("video.mp4"), "a" * 64, SUPPORTED_PROFILE, environment
    )
    assert metadata["encoded_dimensions"] == {"width": 1320, "height": 2868}
    assert metadata["display_dimensions"] == {"width": 2868, "height": 1320}
    assert metadata["rotation"]["ffprobe_counter_clockwise_degrees"] == 90
    assert metadata["rotation"]["clockwise_degrees"] == 270
    assert metadata["expected_resolution_match"] is True


def test_parse_metadata_reports_resolution_mismatch():
    payload = {
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "time_base": "1/90000",
            }
        ],
        "format": {},
    }
    environment = {
        "versions": {"ffmpeg": "8.1.2", "ffprobe": "8.1.2"},
        "warnings": [],
    }
    metadata = parse_metadata_payload(
        payload, Path("video.mp4"), "b" * 64, SUPPORTED_PROFILE, environment
    )
    assert metadata["expected_resolution_match"] is False
    assert metadata["warnings"][-1]["code"] == "DISPLAY_RESOLUTION_MISMATCH"


def test_parse_pts_validation_vfr_and_nearest_tie():
    payload = {
        "frames": [
            {"best_effort_timestamp_time": "0.000", "pkt_duration_time": "0.020", "key_frame": 1},
            {"best_effort_timestamp_time": "0.020", "pkt_duration_time": "0.020", "key_frame": 0},
            {"best_effort_timestamp_time": "0.040", "pkt_duration_time": "0.060", "key_frame": 0},
            {"best_effort_timestamp_time": "0.100", "pkt_duration_time": "0.020", "key_frame": 0},
        ]
    }
    index = parse_frame_timestamp_payload(payload, "c" * 64, "8.1.2")
    assert index.validation["complete"] is True
    assert index.validation["strictly_monotonic"] is True
    assert index.validation["vfr_diagnostics"]["is_vfr"] is True
    assert index.nearest_ordinal(0.03) == 1
    np.testing.assert_array_equal(index.key_frame, [True, False, False, False])


def test_parse_pts_finds_missing_duplicate_and_non_monotonic():
    payload = {
        "frames": [
            {"best_effort_timestamp_time": "0.0"},
            {"best_effort_timestamp_time": "0.0"},
            {"best_effort_timestamp_time": "bad"},
            {"best_effort_timestamp_time": "-1.0"},
        ]
    }
    index = parse_frame_timestamp_payload(payload, "d" * 64, "8.1.2")
    assert index.validation["missing_count"] == 1
    assert index.validation["duplicate_count"] == 1
    assert index.validation["non_monotonic_count"] == 1
    assert index.validation["complete"] is False
    assert index.validation["strictly_monotonic"] is False
