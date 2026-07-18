"""Read-only smoke test against the repository's current replay artifacts."""

from pathlib import Path

import pytest

from battle_vision_product.adapters import (
    FilesystemArtifactReader,
    FilesystemEngineGateway,
    FilesystemReplayCatalog,
)
from battle_vision_product.application import ReplayDiscoveryService


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _metadata_fingerprint(root: Path):
    return {
        path.relative_to(root).as_posix(): (
            path.stat().st_size,
            path.stat().st_mtime_ns,
            path.stat().st_mode,
        )
        for path in sorted(row for row in root.rglob("*") if row.is_file())
    }


@pytest.mark.integration
def test_repository_replays_are_discovered_without_artifact_changes() -> None:
    outputs = PROJECT_ROOT / "outputs"
    official = outputs / "replays" / "official-02" / "checkpoint-1a"
    if not (outputs / "checkpoint-1a").is_dir() or not official.is_dir():
        pytest.skip("repository replay artifacts 不在目前 checkout")

    before = _metadata_fingerprint(outputs)
    service = ReplayDiscoveryService(
        catalog=FilesystemReplayCatalog(PROJECT_ROOT),
        engine_gateway=FilesystemEngineGateway(FilesystemArtifactReader()),
    )

    inspections = service.discover()

    assert {row.replay_id for row in inspections} >= {"win-01", "official-02"}
    assert len({row.replay_id for row in inspections}) == len(inspections)
    assert _metadata_fingerprint(outputs) == before
