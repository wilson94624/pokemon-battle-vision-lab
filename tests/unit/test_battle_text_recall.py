import json
import os
import stat
from pathlib import Path

import cv2
import numpy as np
import pytest

from pokemon_battle_vision.battle_text_detection import (
    DEFAULT_BATTLE_TEXT_CONFIG,
    analyze_battle_text_crop,
)
from pokemon_battle_vision.battle_text_layout import layout_hamming_distance
from pokemon_battle_vision.battle_text_round1 import build_round1_mapping
from pokemon_battle_vision.battle_text_timeline import build_battle_text_timeline
from pokemon_battle_vision.candidate_detection import DEFAULT_THRESHOLDS
from pokemon_battle_vision.checkpoint1b_models import EVENT_TYPES, FrameScanRecord
from pokemon_battle_vision.errors import InputError
from pokemon_battle_vision.output_transaction import (
    OutputTransaction,
    validate_generated_output_path,
)
from pokemon_battle_vision.scanner import SCAN_HZ, SCAN_INTERVAL_SEC
from pokemon_battle_vision.timeline import format_timestamp


def _record(index, level, layout="00" * 40):
    if isinstance(level, bool):
        level = "strong" if level else "negative"
    score = {"strong": 0.9, "weak": 0.5, "negative": 0.2}[level]
    scores = {event_type: 0.0 for event_type in EVENT_TYPES}
    scores["BATTLE_TEXT"] = score
    positive = level != "negative"
    fingerprint = {
        "layout_hash": layout,
        "row_profile": [0.2] * 10,
        "column_profile": [0.2] * 16,
        "bbox": [0.1, 0.2, 0.7, 0.8],
        "component_count": 18,
    }
    return FrameScanRecord(
        sample_index=index,
        frame_index=index * 3,
        target_time=index * 0.1,
        pts=index * 0.1,
        timestamp=format_timestamp(index * 0.1),
        roi_available=True,
        ui_state="BATTLE_TEXT" if positive else "UNKNOWN",
        visible_rois=["battle_text"] if positive else [],
        frame_hash="f" * 64,
        candidate_scores=scores,
        battle_text_evidence={
            "layout_hash": layout,
            "layout_fingerprint": fingerprint,
            "template_similarity": score,
            "template_strength": score,
            "visual_structure_strength": score,
            "text_line_strength": score,
            "strong_positive": level == "strong",
            "weak_positive": level == "weak",
            "evidence_level": level,
            "positive_reasons": ["horizontal_text_structure"] if positive else [],
            "negative_reasons": [] if positive else ["no_horizontal_text_line"],
            "local_edge_density": 0.02,
            "top_row_density": 0.15,
            "component_count": 18,
            "aligned_component_count": 14,
            "line_span_ratio": 0.5,
            "line_height_cv": 0.2,
            "text_mask_ratio": 0.02,
            "large_bright_fraction": 0.0,
            "dark_background_ratio": 0.8,
            "low_saturation_ratio_60": 0.1,
            "low_saturation_ratio_90": 0.08,
        },
    )


def _timeline(pattern, layouts=None):
    layouts = layouts or ["00" * 40] * len(pattern)
    records = [_record(index, level, layouts[index]) for index, level in enumerate(pattern)]
    return build_battle_text_timeline(
        records,
        scan_hz=SCAN_HZ,
        threshold=DEFAULT_THRESHOLDS["BATTLE_TEXT"],
    )


def test_sampling_contract_remains_fixed_10_hz():
    assert SCAN_HZ == 10.0
    assert SCAN_INTERVAL_SEC == 0.1


def test_true_white_text_rows_trigger_structural_proposal():
    crop = np.zeros((212, 1535, 3), dtype=np.uint8)
    for y in (55, 110):
        cv2.putText(
            crop,
            "BATTLE TEXT MESSAGE 123",
            (20, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.3,
            (235, 235, 235),
            3,
            cv2.LINE_AA,
        )
    evidence = analyze_battle_text_crop(crop, template_similarity=0.69)
    assert evidence.visual_structure_strength >= DEFAULT_BATTLE_TEXT_CONFIG.proposal_threshold
    assert evidence.evidence_level == "strong"
    assert "horizontal_text_structure" in evidence.positive_reasons


def test_large_single_bright_blob_is_not_text():
    crop = np.zeros((212, 1535, 3), dtype=np.uint8)
    cv2.rectangle(crop, (20, 30), (650, 180), (245, 245, 245), -1)
    evidence = analyze_battle_text_crop(crop, template_similarity=0.95)
    assert evidence.evidence_level == "negative"
    assert "large_bright_blob" in evidence.negative_reasons


def test_sparse_wide_field_or_menu_highlights_are_not_text():
    crop = np.zeros((212, 1535, 3), dtype=np.uint8)
    # 橫跨 ROI 的稀疏燈點可模仿場地燈光或選單刻度，但不具備文字密度。
    for x in np.linspace(20, 745, 10, dtype=int):
        cv2.circle(crop, (int(x), 95), 3, (235, 235, 235), -1)
    evidence = analyze_battle_text_crop(crop, template_similarity=0.95)
    assert evidence.evidence_level == "negative"
    assert "sparse_wide_highlights" in evidence.negative_reasons


def test_single_0_1_second_strong_message_is_preserved():
    events, diagnostics = _timeline(["negative", "strong", "negative", "negative"])
    assert len(events) == 1
    assert events[0].duration_sec == pytest.approx(0.2)
    assert any(row["decision"] == "open_candidate" for row in diagnostics)


def test_weak_only_field_light_cannot_hold_candidate_forever():
    events, diagnostics = _timeline(["strong"] + ["weak"] * 14 + ["negative"] * 2)
    assert len(events) == 1
    assert events[0].duration_sec <= 1.2
    assert any(row["close_reason"] == "weak_evidence_decay" for row in diagnostics)


def test_three_structurally_supported_weak_samples_can_open_once():
    events, diagnostics = _timeline(
        ["negative", "weak", "weak", "weak", "negative", "negative"]
    )
    assert len(events) == 1
    assert events[0].duration_sec <= 0.4
    assert any(
        row["open_reason"] == "temporally_confirmed_weak_positive"
        for row in diagnostics
    )


def test_one_negative_sample_is_bridged():
    events, diagnostics = _timeline(
        ["strong", "negative", "strong", "negative", "negative"]
    )
    assert len(events) == 1
    assert diagnostics[1]["decision"] == "bridged_gap"
    assert diagnostics[2]["merge_reason"] == "resumed_after_single_negative_gap"


def test_short_negative_gap_with_small_layout_change_stays_merged():
    levels = ["strong"] * 7 + ["negative"] + ["strong"] * 4
    layouts = ["00" * 40] * 8 + ["01" * 40] * 4
    events, _ = _timeline(levels, layouts)
    assert len(events) == 1


def test_short_negative_gap_with_real_layout_change_splits():
    levels = ["strong"] * 7 + ["negative"] + ["strong"] * 4
    layouts = ["00" * 40] * 8 + ["03" * 40] * 4
    events, diagnostics = _timeline(levels, layouts)
    assert len(events) == 2
    split = next(row for row in diagnostics if row["decision"] == "split_on_layout_transition")
    assert split["split_reason"] == "layout_transition_after_fade"


def test_negative_then_weak_fade_uses_sensitive_persistent_transition():
    levels = ["strong"] * 7 + ["negative", "weak"] + ["strong"] * 4
    layouts = ["00" * 40] * 9 + ["01" * 40] * 4
    events, _ = _timeline(levels, layouts)
    assert len(events) == 2


def test_multiple_negative_samples_close_candidate():
    events, diagnostics = _timeline(["strong", "negative", "negative", "strong"])
    assert len(events) == 2
    assert any(row["close_reason"] == "negative_gap_exceeded" for row in diagnostics)


def test_same_layout_after_short_obstruction_reopens_same_candidate():
    levels = ["strong"] * 7 + ["negative"] * 8 + ["strong"] * 7
    events, diagnostics = _timeline(levels)
    assert len(events) == 1
    assert any(
        row["decision"] == "bridged_same_layout_reopen" for row in diagnostics
    )


def test_different_layout_after_obstruction_remains_separate():
    levels = ["strong"] * 7 + ["negative"] * 8 + ["strong"] * 7
    layouts = ["00" * 40] * 15 + ["ff" * 40] * 7
    events, _ = _timeline(levels, layouts)
    assert len(events) == 2


def test_stable_layout_does_not_over_split():
    events, _ = _timeline(["strong"] * 12 + ["negative", "negative"])
    assert len(events) == 1


def test_persistent_major_layout_change_splits_messages():
    layouts = ["00" * 40] * 6 + ["ff" * 40] * 4 + ["ff" * 40] * 2
    events, diagnostics = _timeline(["strong"] * 12, layouts)
    assert layout_hamming_distance("00", "ff") == 1.0
    assert len(events) == 2
    assert any(row["decision"] == "split_on_layout_transition" for row in diagnostics)


def test_single_layout_flicker_does_not_split_same_message():
    layouts = ["00" * 40] * 7 + ["ff" * 40] + ["00" * 40] * 4
    events, _ = _timeline(["strong"] * len(layouts), layouts)
    assert len(events) == 1


def test_different_messages_after_short_fade_split():
    levels = ["strong"] * 7 + ["negative"] + ["strong"] * 5
    layouts = ["00" * 40] * 8 + ["ff" * 40] * 5
    events, _ = _timeline(levels, layouts)
    assert len(events) == 2


def test_persistent_moderate_layout_change_after_fade_splits():
    levels = ["strong"] * 7 + ["weak"] + ["strong"] * 4
    layouts = ["00" * 40] * 8 + ["07" * 40] * 4
    events, _ = _timeline(levels, layouts)
    assert len(events) == 2


def test_candidate_duration_is_visual_not_fixed():
    short, _ = _timeline(["strong", "negative", "negative"])
    long, _ = _timeline(["strong"] * 8 + ["negative", "negative"])
    assert short[0].duration_sec == pytest.approx(0.2)
    assert long[0].duration_sec > short[0].duration_sec


def test_each_sample_has_traceable_state_fields():
    _, diagnostics = _timeline(["negative", "strong", "weak", "negative", "negative"])
    required = {
        "timestamp",
        "frame_ordinal",
        "battle_text_score",
        "evidence_level",
        "candidate_id",
        "decision",
        "open_reason",
        "close_reason",
        "merge_reason",
        "split_reason",
        "layout_change_score",
    }
    assert all(required.issubset(row) for row in diagnostics)


def test_human_fixture_is_not_imported_or_hardcoded_in_production_detector():
    root = Path(__file__).resolve().parents[2]
    production_paths = (
        "src/pokemon_battle_vision/candidate_detection.py",
        "src/pokemon_battle_vision/battle_text_detection.py",
        "src/pokemon_battle_vision/battle_text_features.py",
        "src/pokemon_battle_vision/battle_text_layout.py",
        "src/pokemon_battle_vision/battle_text_timeline.py",
    )
    production = "\n".join(
        (root / path).read_text(encoding="utf-8") for path in production_paths
    )
    assert "battle_text_human_review_round1" not in production
    for forbidden in ("battle_text-0033", "57.988", "140.397", "151.197"):
        assert forbidden not in production


def test_round1_mapping_uses_time_overlap_not_new_candidate_ids():
    root = Path(__file__).resolve().parents[2]
    fixture = json.loads(
        (root / "references/battle_text_human_review_round1.json").read_text()
    )
    events = [
        {
            "event_id": "arbitrary-new-id",
            "type": "BATTLE_TEXT",
            "start_time": 82.3,
            "end_time": 84.1,
        }
    ]
    report = build_round1_mapping(fixture, events)
    row = next(
        row for row in report["rows"] if row["baseline_candidate_id"] == "battle_text-0015"
    )
    assert row["mapped_candidate_ids"] == ["arbitrary-new-id"]


def test_output_transaction_uses_visible_staging_and_removes_empty_conflict(tmp_path):
    project_root = tmp_path / "project"
    target = project_root / "outputs" / "checkpoint-1b"
    target.mkdir(parents=True)
    (target / "old.txt").write_text("old", encoding="utf-8")
    conflict = target.with_name(target.name + " 2")
    conflict.mkdir()
    with OutputTransaction(project_root, target) as transaction:
        assert not transaction.staging_dir.name.startswith(".")
        (transaction.staging_dir / "new.txt").write_text("new", encoding="utf-8")
        transaction.commit()
    assert not (target / "old.txt").exists()
    assert (target / "new.txt").read_text(encoding="utf-8") == "new"
    assert not conflict.exists()
    assert not list(target.parent.glob("checkpoint-1b.tmp-*"))
    assert not list(target.parent.glob("checkpoint-1b.backup-*"))


def test_output_transaction_failure_preserves_old_directory(tmp_path):
    project_root = tmp_path / "project"
    target = project_root / "outputs" / "checkpoint-1b"
    target.mkdir(parents=True)
    (target / "old.txt").write_text("old", encoding="utf-8")
    with pytest.raises(RuntimeError, match="預期失敗"):
        with OutputTransaction(project_root, target) as transaction:
            (transaction.staging_dir / "partial.txt").write_text("partial", encoding="utf-8")
            raise RuntimeError("預期失敗")
    assert (target / "old.txt").read_text(encoding="utf-8") == "old"
    assert not list(target.parent.glob("checkpoint-1b.tmp-*"))


def test_output_transaction_rejects_nonempty_conflict_and_preserves_old(tmp_path):
    project_root = tmp_path / "project"
    target = project_root / "outputs" / "checkpoint-1b"
    target.mkdir(parents=True)
    (target / "old.txt").write_text("old", encoding="utf-8")
    conflict = target.with_name(target.name + " 2")
    conflict.mkdir()
    (conflict / "keep.txt").write_text("user data", encoding="utf-8")
    with pytest.raises(InputError, match="非空白 output 衝突目錄"):
        with OutputTransaction(project_root, target) as transaction:
            (transaction.staging_dir / "new.txt").write_text("new", encoding="utf-8")
            transaction.commit()
    assert (target / "old.txt").read_text() == "old"
    assert (conflict / "keep.txt").read_text() == "user data"


@pytest.mark.skipif(
    not hasattr(os, "chflags") or not getattr(stat, "UF_HIDDEN", 0),
    reason="BSD hidden flag 只在支援 chflags 的平台驗證",
)
def test_output_transaction_recursively_clears_bsd_hidden_flag(tmp_path):
    project_root = tmp_path / "project"
    target = project_root / "outputs" / "checkpoint-1b"
    with OutputTransaction(project_root, target) as transaction:
        child = transaction.staging_dir / "child"
        child.mkdir()
        (transaction.staging_dir / ".DS_Store").write_bytes(b"finder metadata")
        hidden = child / "visible.txt"
        hidden.write_text("visible", encoding="utf-8")
        os.chflags(str(hidden), int(hidden.stat().st_flags) | stat.UF_HIDDEN)
        transaction.commit()
    assert OutputTransaction.hidden_items(target) == []
    assert not (target / ".DS_Store").exists()


@pytest.mark.parametrize("relative", [".", "outputs", "../external"])
def test_output_transaction_rejects_dangerous_paths(tmp_path, relative):
    project_root = tmp_path / "project"
    project_root.mkdir()
    with pytest.raises(InputError, match="拒絕清理|必須位於"):
        validate_generated_output_path(project_root, project_root / relative)
