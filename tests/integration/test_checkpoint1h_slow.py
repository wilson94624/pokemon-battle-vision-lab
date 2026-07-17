import json
import shutil
from pathlib import Path

import pytest

from pokemon_battle_vision.checkpoint1h import run_checkpoint_1h
from pokemon_battle_vision.utils import sha256_file


PROJECT = Path(__file__).resolve().parents[2]


@pytest.mark.slow
@pytest.mark.integration
def test_formal_reconstruction_is_deterministic_and_preserves_frozen_inputs():
    output = PROJECT / "outputs/checkpoint-1h-slow-test"
    formal = PROJECT / "outputs/checkpoint-1h"
    formal_manifest = json.loads(
        (formal / "checkpoint1h_manifest.json").read_text(encoding="utf-8")
    )
    frozen_before = {
        row["path"]: sha256_file(PROJECT / row["path"])
        for row in formal_manifest["source"].values()
    }
    try:
        rerun_manifest = run_checkpoint_1h(
            project_root=PROJECT,
            checkpoint1d_dir=PROJECT / "outputs/checkpoint-1d",
            checkpoint1e_dir=PROJECT / "outputs/checkpoint-1e",
            checkpoint1e_review_dir=PROJECT / "outputs/checkpoint-1e-review",
            checkpoint1g_dir=PROJECT / "outputs/checkpoint-1g",
            output_dir=output,
        )
        assert rerun_manifest == formal_manifest
        for filename in [*formal_manifest["outputs"], "checkpoint1h_manifest.json"]:
            assert sha256_file(output / filename) == sha256_file(formal / filename)
        assert frozen_before == {
            path: sha256_file(PROJECT / path) for path in frozen_before
        }
    finally:
        shutil.rmtree(output, ignore_errors=True)
