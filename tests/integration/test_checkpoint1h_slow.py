import json
from pathlib import Path

import pytest

from pokemon_battle_vision.approved_drift import ApprovedDriftRegistry
from pokemon_battle_vision.utils import sha256_file


PROJECT = Path(__file__).resolve().parents[2]


@pytest.mark.slow
@pytest.mark.integration
def test_formal_reconstruction_payloads_are_stable_and_drift_is_exactly_approved():
    formal = PROJECT / "outputs/checkpoint-1h"
    formal_manifest = json.loads(
        (formal / "checkpoint1h_manifest.json").read_text(encoding="utf-8")
    )
    # 1H 完成後，只有 1E Human Review metadata 經人工授權更新；缺少舊
    # bytes 時不可假裝可重建 identical manifest。改以 direct payload hashes
    # 作 blocking gate，並要求每個 upstream difference 精確命中 registry。
    for filename, reference in formal_manifest["outputs"].items():
        assert sha256_file(formal / filename) == reference["sha256"]

    registry = ApprovedDriftRegistry.from_project(PROJECT)
    approved_ids = set()
    for reference in formal_manifest["source"].values():
        approved = registry.verify(
            "1H",
            reference["path"],
            reference["sha256"],
            sha256_file(PROJECT / reference["path"]),
        )
        if approved is not None:
            approved_ids.add(approved["drift_id"])
    assert approved_ids == {
        "approved-drift-0002",
        "approved-drift-0003",
        "approved-drift-0004",
        "approved-drift-0005",
        "approved-drift-0006",
        "approved-drift-0007",
    }
