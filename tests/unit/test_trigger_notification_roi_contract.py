import json
from pathlib import Path

from pokemon_battle_vision.config import load_roi_config
from pokemon_battle_vision.utils import sha256_file


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs/roi_2868x1320.json"
MANIFEST_PATH = ROOT / "outputs/checkpoint-1a/roi_overlay_manifest.json"
TRIGGER_ROI_IDS = {
    "player_trigger_notification",
    "opponent_trigger_notification",
}
EXPECTED_RECOGNITION_FIELDS = {
    "trigger_side",
    "pokemon",
    "ability_or_item",
    "trigger_name",
    "raw_text",
    "timestamp",
}


def _overlaps(first, second):
    return not (
        first["x"] + first["width"] <= second["x"]
        or second["x"] + second["width"] <= first["x"]
        or first["y"] + first["height"] <= second["y"]
        or second["y"] + second["height"] <= first["y"]
    )


def test_trigger_notification_rois_are_independent_and_have_validation_status():
    config, normalized_rois = load_roi_config(CONFIG_PATH)
    assert TRIGGER_ROI_IDS.issubset(normalized_rois)

    battle_text = config["rois"]["battle_text"]
    for roi_id in TRIGGER_ROI_IDS:
        roi = config["rois"][roi_id]
        assert set(roi["recognition_fields"]) == EXPECTED_RECOGNITION_FIELDS
        assert roi["must_remain_separate_from"] == ["battle_text"]
        assert not _overlaps(roi, battle_text)

    player_validation = config["rois"]["player_trigger_notification"]["validation"]
    assert player_validation["status"] == "provisional_unverified"
    assert player_validation["positive_example_verified"] is False

    opponent_validation = config["rois"]["opponent_trigger_notification"]["validation"]
    assert opponent_validation["status"] == "positive_example_verified"
    assert opponent_validation["positive_example_verified"] is True
    assert opponent_validation["frame_ordinal"] == 21515
    assert opponent_validation["pts_sec"] == 452.558333


def test_trigger_notification_overlay_manifest_preserves_provisional_gate():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["status"] == "pending_human_approval"
    assert manifest["approval"]["current_status"] == "not_approved"
    assert manifest["roi_config_sha256"] == sha256_file(CONFIG_PATH)

    validation = manifest["roi_validation"]
    assert validation["player_trigger_notification"]["status"] == "provisional_unverified"
    assert validation["player_trigger_notification"]["positive_example_verified"] is False
    assert validation["opponent_trigger_notification"]["status"] == "positive_example_verified"
    assert validation["opponent_trigger_notification"]["positive_example_verified"] is True

    trigger_overlay = next(
        row
        for row in manifest["overlays"]
        if row["id"] == "kf_trigger_notification_opponent_positive"
    )
    assert set(trigger_overlay["roi_ids"]) == TRIGGER_ROI_IDS
    assert trigger_overlay["source"]["frame_ordinal"] == 21515
    assert trigger_overlay["source"]["pts_sec"] == 452.558333
    overlay_path = MANIFEST_PATH.parent / trigger_overlay["path"]
    assert trigger_overlay["sha256"] == sha256_file(overlay_path)

    assert manifest["roi_validation_summary"] == {
        "all_rois_positive_example_verified": False,
        "positive_example_verified_count": 1,
        "provisional_unverified_count": 1,
    }
    approval_path = MANIFEST_PATH.parent / "roi_approval.json"
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    assert approval["status"] == "approved"
    assert approval["roi_config_sha256"] == sha256_file(CONFIG_PATH)
    assert approval["overlay_manifest_sha256"] == sha256_file(MANIFEST_PATH)
