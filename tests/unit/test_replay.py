from pathlib import Path

from pokemon_battle_vision.checkpoint1c import _frozen_inputs
from pokemon_battle_vision.cli import _parser
from pokemon_battle_vision.replay import DEFAULT_REPLAY_ID, ReplayContext


def test_legacy_replay_context_keeps_existing_output_layout(tmp_path):
    context = ReplayContext()
    assert context.replay_id == DEFAULT_REPLAY_ID
    assert context.checkpoint_dir(tmp_path, "checkpoint-1a") == tmp_path / "outputs/checkpoint-1a"


def test_second_replay_context_uses_isolated_namespace(tmp_path):
    context = ReplayContext("official-02")
    assert context.checkpoint_dir(tmp_path, "checkpoint-1a") == (
        tmp_path / "outputs/replays/official-02/checkpoint-1a"
    )


def test_checkpoint1c_frozen_inputs_are_derived_from_supplied_directories(tmp_path):
    root = tmp_path
    paths = _frozen_inputs(
        root,
        root / "outputs/replays/official-02/checkpoint-1a",
        root / "outputs/replays/official-02/checkpoint-1b",
        root / "outputs/replays/official-02/checkpoint-1b-review",
        root / "configs/official-02-roi.json",
    )
    assert paths["events"] == "outputs/replays/official-02/checkpoint-1b/events.json"
    assert paths["roi_approval"] == (
        "outputs/replays/official-02/checkpoint-1a/roi_approval.json"
    )
    assert paths["roi_config"] == "configs/official-02-roi.json"
    assert all("win01" not in value for value in paths.values())


def test_checkpoint1c_cli_accepts_replay_identifier_and_inputs():
    args = _parser().parse_args(
        [
            "checkpoint-1c",
            "--video", "second.mp4",
            "--checkpoint-1b-dir", "outputs/replays/official-02/checkpoint-1b",
            "--checkpoint-1b-review-dir", "outputs/replays/official-02/checkpoint-1b-review",
            "--output", "outputs/replays/official-02/checkpoint-1c",
            "--review-output", "outputs/replays/official-02/checkpoint-1c-review",
            "--replay-id", "official-02",
        ]
    )
    assert args.replay_id == "official-02"
    assert args.checkpoint_1b_dir == Path(
        "outputs/replays/official-02/checkpoint-1b"
    )
