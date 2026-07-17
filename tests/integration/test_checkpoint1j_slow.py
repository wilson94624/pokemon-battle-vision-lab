import json
import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.integration
@pytest.mark.slow
def test_real_checkpoint1j_cli_smoke():
    output = ROOT / "outputs" / "checkpoint-1j-cli-test-{}".format(uuid4().hex)
    environment = dict(os.environ)
    environment["PYTHONPYCACHEPREFIX"] = "/tmp/pokemon-battle-vision-pycache"
    try:
        completed = subprocess.run(
            [
                str(ROOT / ".venv/bin/pokemon-battle-vision"),
                "checkpoint-1j",
                "--project-root",
                str(ROOT),
                "--checkpoint-1h-dir",
                str(ROOT / "outputs/checkpoint-1h"),
                "--checkpoint-1i-dir",
                str(ROOT / "outputs/checkpoint-1i"),
                "--output",
                str(output),
            ],
            cwd=str(ROOT),
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )
        assert "Review records：18" in completed.stdout
        assert "Needs review：18" in completed.stdout
        manifest = json.loads(
            (output / "checkpoint1j_manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["status"] == "complete_pending_human_review"
        assert manifest["counts"]["expanded_interpretations"] == 10
        assert manifest["review_pack"]["card_count"] == 18
    finally:
        if output.exists():
            shutil.rmtree(str(output))
        for pattern in (
            "{}.tmp-*".format(output.name),
            "{}.backup-*".format(output.name),
        ):
            for item in output.parent.glob(pattern):
                if item.is_dir():
                    shutil.rmtree(str(item))
