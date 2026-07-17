import json
import shutil
from pathlib import Path

import pytest

from pokemon_battle_vision.checkpoint1g import run_checkpoint_1g
from pokemon_battle_vision.utils import sha256_file


PROJECT = Path(__file__).resolve().parents[2]


@pytest.mark.slow
@pytest.mark.integration
def test_real_video_full_pipeline_is_deterministic_against_formal_payloads():
    output = PROJECT / "outputs/checkpoint-1g-slow-test"
    review = PROJECT / "outputs/checkpoint-1g-review-slow-test"
    try:
        manifest = run_checkpoint_1g(
            project_root=PROJECT,
            video_path=PROJECT / "samples/videos/win-01.mp4",
            roi_config_path=PROJECT / "configs/roi_2868x1320.json",
            checkpoint1a_dir=PROJECT / "outputs/checkpoint-1a",
            checkpoint1b_dir=PROJECT / "outputs/checkpoint-1b",
            checkpoint1b_review_dir=PROJECT / "outputs/checkpoint-1b-review",
            checkpoint1c_dir=PROJECT / "outputs/checkpoint-1c",
            checkpoint1d_dir=PROJECT / "outputs/checkpoint-1d",
            checkpoint1e_dir=PROJECT / "outputs/checkpoint-1e",
            checkpoint1f_dir=PROJECT / "outputs/checkpoint-1f",
            output_dir=output,
            review_output_dir=review,
        )
        assert manifest["status"] == "complete"
        assert manifest["counts"]["move_menu_observations"] == 31
        assert manifest["counts"]["enriched_snapshots"] == 71
        assert manifest["counts"]["ocr_runtime_results"] == {"success": 1235}
        assert manifest["counts"]["knowledge_base_species"] == 1025
        formal = json.loads(
            (PROJECT / "outputs/checkpoint-1g/checkpoint1g_manifest.json").read_text(encoding="utf-8")
        )
        for filename in formal["outputs"]:
            assert sha256_file(output / filename) == sha256_file(
                PROJECT / "outputs/checkpoint-1g" / filename
            )
    finally:
        shutil.rmtree(output, ignore_errors=True)
        shutil.rmtree(review, ignore_errors=True)
