import json
import shutil
from pathlib import Path

import pytest

from pokemon_battle_vision.checkpoint1i import run_checkpoint_1i
from pokemon_battle_vision.utils import sha256_file


PROJECT = Path(__file__).resolve().parents[2]


@pytest.mark.slow
@pytest.mark.integration
def test_formal_checkpoint1i_rerun_is_byte_identical_and_preserves_direct_inputs():
    formal = PROJECT / "outputs/checkpoint-1i"
    source = PROJECT / "outputs/checkpoint-1h"
    output = PROJECT / "outputs/checkpoint-1i-slow-test"
    manifest = json.loads(
        (formal / "checkpoint1i_manifest.json").read_text(encoding="utf-8")
    )
    direct_paths = [
        PROJECT / row["path"]
        for row in (
            manifest["source"]["manifest"],
            manifest["source"]["battle_facts"],
            manifest["source"]["battle_fact_relations"],
            manifest["knowledge"]["data"],
            manifest["knowledge"]["manifest"],
        )
    ]
    before = {str(path): sha256_file(path) for path in direct_paths}
    try:
        run_checkpoint_1i(PROJECT, source, output)
        first = {
            path.relative_to(output).as_posix(): sha256_file(path)
            for path in sorted(output.rglob("*"))
            if path.is_file()
        }
        run_checkpoint_1i(PROJECT, source, output)
        second = {
            path.relative_to(output).as_posix(): sha256_file(path)
            for path in sorted(output.rglob("*"))
            if path.is_file()
        }
        assert second == first
        assert before == {str(path): sha256_file(path) for path in direct_paths}
    finally:
        shutil.rmtree(output, ignore_errors=True)
