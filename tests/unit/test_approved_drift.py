from pathlib import Path

import pytest

from pokemon_battle_vision.approved_drift import ApprovedDriftRegistry
from pokemon_battle_vision.errors import InputError


PROJECT = Path(__file__).resolve().parents[2]


def test_exact_approved_metadata_drift_is_accepted():
    registry = ApprovedDriftRegistry.from_project(PROJECT)
    record = registry.payload["records"][0]
    matched = registry.verify(
        record["consumer_checkpoint"],
        record["source_path"],
        record["frozen_snapshot_sha256"],
        record["approved_current_sha256"],
    )
    assert matched["drift_id"] == record["drift_id"]


def test_unchanged_hash_needs_no_registry_record():
    registry = ApprovedDriftRegistry.from_project(PROJECT)
    assert registry.verify("1H", "any/path", "a" * 64, "a" * 64) is None


def test_unexpected_hash_drift_still_fails():
    registry = ApprovedDriftRegistry.from_project(PROJECT)
    record = registry.payload["records"][0]
    with pytest.raises(InputError, match="未核准 upstream drift"):
        registry.verify(
            record["consumer_checkpoint"],
            record["source_path"],
            record["frozen_snapshot_sha256"],
            "f" * 64,
        )


def test_registry_cannot_approve_unlisted_direct_payload_path():
    registry = ApprovedDriftRegistry.from_project(PROJECT)
    with pytest.raises(InputError, match="未核准"):
        registry.verify(
            "1H",
            "outputs/checkpoint-1h/battle_facts.json",
            "a" * 64,
            "b" * 64,
        )
