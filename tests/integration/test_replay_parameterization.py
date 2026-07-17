import shutil
from pathlib import Path

from pokemon_battle_vision.checkpoint1d import run_checkpoint_1d


ROOT = Path(__file__).resolve().parents[2]


def test_checkpoint1d_accepts_second_replay_namespace_without_source_changes(tmp_path):
    project = tmp_path / "project"
    shutil.copytree(ROOT / "src", project / "src")
    shutil.copytree(ROOT / "schemas", project / "schemas")
    shutil.copytree(ROOT / "configs", project / "configs")
    replay_root = project / "outputs/replays/official-02"
    for name in ("checkpoint-1a", "checkpoint-1b", "checkpoint-1c"):
        shutil.copytree(ROOT / "outputs" / name, replay_root / name)
    review = shutil.copy(
        ROOT / "outputs/checkpoint-1c-review/checkpoint1c_review.json",
        replay_root / "checkpoint-1c-review.json",
    )
    output = replay_root / "checkpoint-1d"
    manifest = run_checkpoint_1d(
        project_root=project,
        review_path=review,
        output_dir=output,
        checkpoint1a_dir=replay_root / "checkpoint-1a",
        checkpoint1b_dir=replay_root / "checkpoint-1b",
        checkpoint1c_dir=replay_root / "checkpoint-1c",
        roi_config_path=project / "configs/roi_2868x1320.json",
        replay_id="official-02",
    )
    assert manifest["replay_id"] == "official-02"
    assert manifest["input"]["path"].endswith("checkpoint-1c-review.json")
