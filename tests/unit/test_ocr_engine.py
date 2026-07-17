import json
import subprocess
from pathlib import Path

import pytest

from pokemon_battle_vision.checkpoint1c_models import OcrEngineResult
from pokemon_battle_vision.errors import DependencyError
from pokemon_battle_vision.ocr_engine import AppleVisionOcrEngine


def _capability_result():
    return subprocess.CompletedProcess(
        args=["helper", "--probe"],
        returncode=0,
        stdout=json.dumps(
            {
                "available": True,
                "supported_languages": ["zh-Hant"],
                "language": "zh-Hant",
                "revision": "VNRecognizeTextRequestRevision3",
                "recognition_level": "accurate",
                "error": None,
            }
        ),
        stderr="",
    )


def test_probe_executes_the_same_recognize_runtime_path(monkeypatch, tmp_path):
    engine = AppleVisionOcrEngine(source_path=tmp_path / "unused.m")
    monkeypatch.setattr(engine, "_binary", lambda: Path("/tmp/fake-helper"))
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: _capability_result())
    seen = []

    def recognize(jobs):
        seen.extend(jobs)
        assert Path(jobs[0]["image_path"]).is_file()
        return [
            OcrEngineResult(
                job_id=jobs[0]["job_id"],
                raw_text="",
                confidence=0.0,
                lines=[],
                error=None,
            )
        ]

    monkeypatch.setattr(engine, "recognize", recognize)
    probe = engine.probe()
    assert [row["job_id"] for row in seen] == ["apple-vision-runtime-probe"]
    assert probe["runtime_path_verified"] is True


def test_probe_rejects_a_production_runtime_failure(monkeypatch, tmp_path):
    engine = AppleVisionOcrEngine(source_path=tmp_path / "unused.m")
    monkeypatch.setattr(engine, "_binary", lambda: Path("/tmp/fake-helper"))
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: _capability_result())
    monkeypatch.setattr(
        engine,
        "recognize",
        lambda jobs: [
            OcrEngineResult(
                job_id=jobs[0]["job_id"],
                raw_text="",
                confidence=0.0,
                lines=[],
                error="kCVReturnAllocationFailed (-6662)",
            )
        ],
    )
    with pytest.raises(DependencyError, match="production runtime probe"):
        engine.probe()
