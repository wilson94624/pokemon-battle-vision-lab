from pathlib import Path

import pytest

from pokemon_battle_vision.pipeline import run_checkpoint_1a


@pytest.mark.slow
@pytest.mark.integration
def test_win01_complete_checkpoint_1a_smoke(tmp_path):
    root = Path(__file__).resolve().parents[2]
    output = tmp_path / "checkpoint-1a"
    report = run_checkpoint_1a(
        project_root=root,
        video_path=root / "samples/videos/win-01.mp4",
        known_frames_path=root / "references/win01_known_frames.json",
        match_reference_path=root / "references/win01_match_reference.json",
        screenshots_dir=root / "samples/screenshots",
        roi_config_path=root / "configs/roi_2868x1320.json",
        output_dir=output,
        interval_sec=60.0,
        ffprobe_timeout_sec=300.0,
    )
    assert report["status"] == "complete_pending_roi_approval"
    assert report["counts"]["pts_frames"] == report["counts"]["opencv_decoded_frames"]
    assert report["counts"]["anchors"] == 6
    assert report["counts"]["roi_overlays"] == 6
    assert (output / "roi_overlay_manifest.json").is_file()
    assert not (output / "roi_approval.json").exists()
    assert not (output / "analysis_samples.npz").exists()
    assert not (output.parent / "checkpoint-1b").exists()

